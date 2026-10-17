#!/usr/bin/env python3
"""Re-derive every published number in an edition from its shipped evidence.

Standard library only. No network. No vendor CLIs. No TIER install. Point it at
an archived edition and it recomputes the arithmetic from the raw artifacts and
tells you where the published figures disagree.

    python3 verify.py [root] [--rejudge] [--require-no-skips] [--expect edition=N:sha256]

This is the reproducibility claim in PROTOCOL.md §8, made executable.
Re-EXECUTION is impossible in principle -- models are stochastic and get
retired, CLIs auto-update -- so what a reader is owed is not "run it again and
get the same thing" but "check that what we published follows from the evidence
we shipped". Specifically, per run:

  * the session log's own hash matches what was recorded  (evidence unaltered)
  * the token totals re-derive from the session log       (nobody retyped them)
  * the cost re-derives from those tokens and the rate table SNAPSHOTTED
    at scoring time                                        (arithmetic is right)
  * the judge hash in the stamp matches the judge on disk  (commit-reveal holds)

and per cell, that the headline metric is what the protocol defines it to be.

Exit code 0 = every check passed. Non-zero = at least one FAIL; the report says
which. A check that cannot run (missing artifact) is SKIP, never a silent pass.
With --require-no-skips, every SKIP row, including skipped rejudge checks,
is reported as FAIL and causes a non-zero exit.
"""
import hashlib
import json
import os
import sys
sys.dont_write_bytecode = True
import glob
import argparse
import re
import csv
import shutil
import subprocess
import tempfile
from pathlib import Path
from collections import defaultdict
from token_evidence import sum_native_jsonl, records
from score import price_native, sum_claude_jsonl, sum_codex_jsonl, price as score_price
from errata import load_errata

ANTHROPIC_W5M = 1.25
ANTHROPIC_W1H = 2.0


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── rate table (minimal YAML reader: the snapshot is a flat models: mapping) ──
def load_prices(path):
    """Parse the price table without PyYAML -- a third-party dependency would
    defeat the point of a verifier that must still run years from now.

    Rows are inline flow mappings:
        claude-opus-4-8: { input_per_m: 5.00, output_per_m: 25.00, provider: anthropic }
    Cache reads use their real multiplier from the rate table. Defaults are
    per-provider and applied
    only when the row is silent, never overriding a stated value.
    """
    default_read_mult = {"anthropic": 0.10, "openai": 0.10}
    rows, in_models = {}, False
    for raw in open(path):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        s = line.strip()
        if indent == 0:
            in_models = s.startswith("models:")
            continue
        if not in_models or "{" not in s:
            continue
        name, _, body = s.partition(":")
        name = name.strip().strip('"\'')
        body = body.strip().strip("{}").strip()
        row = {}
        for field in body.split(","):
            if ":" not in field:
                continue
            k, _, v = field.partition(":")
            v = v.strip().strip('"\'')
            try:
                row[k.strip()] = float(v)
            except ValueError:
                row[k.strip()] = v
        if "input_per_m" not in row:
            continue
        row.setdefault("cache_read_mult",
                       default_read_mult.get(row.get("provider"), 0.10))
        rows[name] = row
    return rows


def sum_claude(path):
    tot = {"input": 0, "output": 0, "cache_read": 0, "w5m": 0, "w1h": 0}
    seen = {}
    for line in open(path):
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = o.get("message")
        if not isinstance(m, dict):
            continue
        u = m.get("usage")
        if not isinstance(u, dict):
            continue
        # Claude Code emits one assistant entry per streaming chunk plus a final
        # post-stream entry, all sharing message.id; the chunks carry partial
        # counts. Largest-total wins, exactly as the scorer does.
        mid = m.get("id")
        total = (u.get("input_tokens", 0) + u.get("output_tokens", 0)
                 + u.get("cache_read_input_tokens", 0))
        if mid is not None:
            if mid in seen and seen[mid][0] >= total:
                continue
            seen[mid] = (total, u)
        else:
            seen[len(seen)] = (total, u)
    for _, u in seen.values():
        tot["input"] += u.get("input_tokens", 0)
        tot["output"] += u.get("output_tokens", 0)
        tot["cache_read"] += u.get("cache_read_input_tokens", 0)
        cc = u.get("cache_creation")
        if isinstance(cc, dict):
            tot["w5m"] += cc.get("ephemeral_5m_input_tokens", 0)
            tot["w1h"] += cc.get("ephemeral_1h_input_tokens", 0)
        else:
            tot["w5m"] += u.get("cache_creation_input_tokens", 0)
    return tot


def sum_codex(path):
    last = None
    for line in open(path):
        if '"token_count"' not in line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        info = (o.get("payload") or {}).get("info")
        if isinstance(info, dict) and isinstance(info.get("total_token_usage"), dict):
            last = info["total_token_usage"]
    if last is None:
        return None
    inp = last.get("input_tokens", 0)
    cached = last.get("cached_input_tokens", 0)
    return {"input": inp - cached, "output": last.get("output_tokens", 0),
            "cache_read": cached, "w5m": 0,
            "w1h": 0,
            "_cache_write": last.get("cache_write_input_tokens", 0),
            "_raw_input": inp, "_reasoning": last.get("reasoning_output_tokens", 0),
            "_total": last.get("total_tokens", 0)}


def price(tokens, row, fmt):
    inr = row["input_per_m"] / 1e6
    outr = row["output_per_m"] / 1e6
    mult = row.get("cache_read_mult", 0.1)
    cost = tokens["input"] * inr + tokens["cache_read"] * inr * mult
    cost += tokens["output"] * outr
    if fmt == "claude":
        cost += tokens["w5m"] * inr * ANTHROPIC_W5M
        cost += tokens["w1h"] * inr * ANTHROPIC_W1H
    else:
        cost += tokens.get("_cache_write", 0) * inr
    return cost


def derive_run_cost(session, result, stamp, rows, fmt):
    """Price the same evidence for bundle checks and report re-pricing."""
    key = result.get("model_key") or stamp.get("model_flag")
    if fmt in ("opencode", "muse"):
        return price_native(sum_native_jsonl(session, fmt), rows, fmt)
    if fmt == "claude" and rows[key].get("context_threshold"):
        return score_price(sum_claude_jsonl(session), rows[key], fmt)[0]
    tokens = sum_claude(session) if fmt == "claude" else sum_codex(session)
    return price(tokens, rows[key], fmt)


METRIC_FIELDS = (
    "edition", "task", "tool", "model", "rep", "outcome", "bundle",
    "wall_seconds", "model_calls", "model_calls_source", "input_uncached",
    "cache_read", "cache_write", "output", "reasoning", "total_tokens",
    "cost_list_usd",
)


def edition_context(root, bundle_root=None, manifest=None):
    """Resolve report and bundle locations for a repository or archived edition."""
    root = Path(root).resolve()
    if (root / "editions/2026-07/MANIFEST.json").is_file():
        bundle_root = bundle_root or root
        root = root / "editions/2026-07"
    manifest = manifest if manifest is not None else json.loads((root / "MANIFEST.json").read_text())
    if len(manifest.get("tasks", {})) > 1 and manifest.get("edition") != "2026-07":
        raise ValueError("multi-task reports require edition 2026-07")
    if bundle_root is None:
        bundle_root = root.parent.parent if root.parent.name == "editions" and manifest.get("edition") == "2026-07" else root
    return root, Path(bundle_root).resolve(), manifest


def derive_run_metrics(bundle, rows, root, edition=None):
    """One derivation for report tables, chart CSV and verification.

    Wall time is the harness receipt, not inferred from transcript timestamps.
    Usage is independently parsed from the shipped log and matched to the
    recorded counters. Codex counts cumulative token_count records, not tools
    or assistant text items (one response can contain several output items).
    """
    from pathlib import Path
    bundle, root = Path(bundle).resolve(), Path(root).resolve()
    res = json.loads((bundle / "result.json").read_text())
    stamp = json.loads((bundle / "stamp.json").read_text())
    tool = res["tool"]
    fmt = "claude" if tool == "claude-code" else tool
    session = bundle / "session.jsonl"
    if rows is None:
        snapshot = bundle / "prices.snapshot.yaml"
        if sha256_file(snapshot) != stamp["prices_yaml_sha256"]:
            raise ValueError(f"{bundle}: prices snapshot sha256 mismatch")
        rows = load_prices(snapshot)
    if res.get("capture") not in (None, "OK"):
        raise ValueError("no metrics derivable for failed capture")
    try:
        tokens = (sum_native_jsonl(session, fmt) if fmt in ("opencode", "muse")
                  else sum_claude_jsonl(session) if fmt == "claude"
                  else sum_codex_jsonl(session))
    except SystemExit as exc:
        raise ValueError(f"{bundle}: {exc}") from exc
    recorded = res["tokens"]
    for field in ("input_uncached", "cache_read", "cache_write", "output", "reasoning"):
        if tokens[field] != recorded[field]:
            raise ValueError(f"recorded {field} differs from session usage")
    calls = recorded.get("api_calls")
    if calls is not None:
        if type(calls) is not int or calls <= 0 or calls != tokens["api_calls"]:
            raise ValueError("recorded api_calls differs from session usage")
        source = "tokens.api_calls"
    elif tool == "codex":
        calls, previous = 0, None
        for o in records(session):
            p = o.get("payload") or {}
            usage = (p.get("info") or {}).get("total_token_usage")
            if o.get("type") == "event_msg" and p.get("type") == "token_count" and isinstance(usage, dict):
                if usage != previous:
                    calls += 1
                previous = usage
        if not calls:
            raise ValueError("no Codex token_count records")
        source = "session.token_count records"
    else:
        raise ValueError("model call count unavailable")
    if "wall_seconds" not in res:
        raise ValueError(f"{bundle}: wall_seconds not recorded")
    wall = res["wall_seconds"]
    if type(wall) not in (int, float) or not (wall > 0 and wall < float("inf")):
        raise ValueError("wall_seconds must be finite and positive")
    return {
        "edition": res.get("edition", stamp.get("edition", edition)), "task": res["task"],
        "tool": tool, "model": res.get("model_key") or stamp["model_flag"],
        "rep": int(bundle.name.removeprefix("rep")), "outcome": res["outcome"],
        "bundle": str(bundle.relative_to(root)), "wall_seconds": wall,
        "model_calls": calls, "model_calls_source": source,
        **{k: tokens[k] for k in ("input_uncached", "cache_read", "cache_write", "output", "reasoning")},
        "total_tokens": sum(tokens[k] for k in ("input_uncached", "cache_read", "cache_write", "output")),
        "cost_list_usd": round(derive_run_cost(session, res, stamp, rows, fmt), 6),
    }


def metric_strings(row):
    """Stable CSV serialization; missing reasoning is empty, never zero."""
    return {k: (f"{row[k]:.6f}" if k == "cost_list_usd" else
                "" if row[k] is None else str(row[k])) for k in METRIC_FIELDS}


FIGURE_NAMES = ("cost-per-accepted", "model-calls", "wall-time", "tokens")


def verify_figure_markers(text, tasks, r):
    """Single-task charts are unscoped; multi-task charts must name their task."""
    for name in FIGURE_NAMES:
        scopes = tasks if len(tasks) > 1 else (None,)
        for task in scopes:
            key = f"{name}:{task}" if task else name
            r.add("PASS" if text.count(f"<!-- figure:{key} -->") == 1 else "FAIL",
                  f"report {key} figure marker")
        if len(tasks) > 1:
            r.add("PASS" if f"<!-- figure:{name} -->" not in text else "FAIL",
                  f"report {name} has no unscoped figure marker")


def verify_metrics(runs, r, edition_dir, rows, bundle_root=None):
    """Require every CSV value and generated report table to match evidence."""
    from pathlib import Path
    try:
        edition_dir, bundle_root, manifest = edition_context(edition_dir, bundle_root)
        path = edition_dir / manifest.get("metrics_file", "metrics.csv")
        repeats = 0
        for run in runs:
            # Edition 2's response audit established no repeated snapshots.
            # July has exactly one repeat; the shared derivation counts it once.
            if run.get("tool") != "codex":
                continue
            previous = None
            for o in records(Path(run["_bundle_dir"]) / "session.jsonl"):
                p = o.get("payload") or {}
                usage = (p.get("info") or {}).get("total_token_usage")
                if o.get("type") == "event_msg" and p.get("type") == "token_count" and isinstance(usage, dict):
                    if usage == previous:
                        repeats += 1
                        if manifest.get("edition") != "2026-07":
                            raise ValueError("Codex adjacent repeated usage snapshots")
                    previous = usage
        if manifest.get("edition") == "2026-07" and repeats != 1:
            raise ValueError(f"Codex adjacent repeated usage snapshots: expected 1 for July, found {repeats}")
        expected = [derive_run_metrics(x["_bundle_dir"], rows, bundle_root, manifest.get("edition")) for x in runs]
        expected.sort(key=lambda x: x["bundle"])
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            actual = list(reader)
            if reader.fieldnames != list(METRIC_FIELDS) or any(None in row for row in actual):
                raise ValueError("CSV columns differ from metrics schema")
        want = [metric_strings(x) for x in expected]
        if len(actual) != len(want):
            raise ValueError(f"expected {len(want)} rows, found {len(actual)}")
        for a, b in zip(actual, want):
            for field in METRIC_FIELDS:
                if a[field] != b[field]:
                    raise ValueError(f"{b['bundle']}: {field} mismatch")
        r.add("PASS", "report metrics.csv", f"every value matches across {len(want)} runs")
        from edition_report import generated_tables
        text = (Path(edition_dir) / "REPORT.md").read_text()
        verify_figure_markers(text, list(manifest.get("tasks") or dict.fromkeys(x["task"] for x in expected)), r)
        for name, table in generated_tables(expected, manifest).items():
            matches = re.findall(r"<!-- table:" + re.escape(name) + r" -->\n(.*?)\n<!-- /table:" + re.escape(name) + r" -->", text, re.S)
            ok = matches == [table]
            r.add("PASS" if ok else "FAIL", f"report {name} table matches metrics")
        if len(manifest.get("tasks", {})) > 1:
            from edition_report import worked_section, CHECK_COUNTS_NOTE_PATTERN
            sections = re.findall(r"<!-- section:how-models-worked -->.*?<!-- /section:how-models-worked -->", text, re.S)
            # The measured check-count sentence is the section's sole manual line.
            note = re.search(CHECK_COUNTS_NOTE_PATTERN, sections[0], re.M) if sections else None
            section = worked_section(expected, manifest, bundle_root, note[0] if note else "")
            r.add("PASS" if sections == [section] else "FAIL", "report how-models-worked section matches evidence")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        r.add("FAIL", "report metrics.csv", str(exc))


class Report:
    def __init__(self):
        self.rows = []

    def add(self, status, what, detail=""):
        self.rows.append((status, what, detail))

    def emit(self, require_no_skips=False):
        counts = defaultdict(int)
        for st, what, detail in self.rows:
            if require_no_skips and st == "SKIP":
                st = "FAIL"
                detail = "--require-no-skips: check would be SKIP" + (f"; {detail}" if detail else "")
            counts[st] += 1
            if st != "PASS" or what.startswith("report "):
                print(f"  [{st}] {what}" + (f" -- {detail}" if detail else ""))
        print(f"\n  PASS {counts['PASS']} · FAIL {counts['FAIL']} · SKIP {counts['SKIP']}")
        return counts["FAIL"]


def sealed_paths(root):
    """Read the public-export boundary; a missing judge is also a loud SKIP."""
    path = Path(root) / ".export-exclude"
    if not path.is_file():
        return []
    return [line.split("#", 1)[0].strip().rstrip("/")
            for line in path.read_text().splitlines()
            if line.split("#", 1)[0].strip()]


def is_sealed(name, paths, root=None):
    root = Path(root or ".").resolve()
    name = str((root / name).resolve()).casefold()
    return any(name == p or name.startswith(p + os.sep)
               for p in (str((root / p).resolve()).casefold() for p in paths))


ORGSYNC_ARCHIVED_FILES = (
    "config.json", "submission/test_orgsync.py",
    "orgsync/__init__.py", "orgsync/__main__.py", "orgsync/config.py",
    "orgsync/http_client.py", "orgsync/models.py", "orgsync/sync.py",
    "tests/existing/test_cli.py", "tests/existing/test_config.py",
    "tests/existing/test_http_client.py", "tests/existing/test_models.py",
)


def rejudge_run(d, r, judge_root):
    """Execute the stamped, revealed judge on a disposable submission copy."""
    d, judge_root = Path(d), Path(judge_root)
    label = f"{os.path.relpath(d)}: re-judge"
    try:
        res = json.loads((d / "result.json").read_text())
        stamp = json.loads((d / "stamp.json").read_text())
        name, digest = stamp.get("judge"), stamp.get("judge_sha256")
        if not name or not digest:
            raise ValueError("stamp carries no judge name/hash")
        # Resolve only inside the kit: no absolute/parent path can execute code
        # from outside the archive's declared judge boundary.
        judge = judge_root / name
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("judge path escapes the archive")
        if is_sealed(name, sealed_paths(judge_root), judge_root) or not judge.is_file():
            r.add("SKIP", label, f"judge {name} sealed or absent; NOT re-judged")
            return
        if sha256_file(judge) != digest:
            raise ValueError("judge SHA-256 differs from the bundle stamp; refusing execution")
        submission = d / "submission"
        if not submission.is_dir():
            r.add("SKIP", label, "missing archived path: submission/; NOT re-judged")
            return
        archived = [submission]
        if name == "acceptance_orgsync.py":
            # Reconstruct the original RUN_DIR layout from THIS bundle only:
            # da2561f:run.sh:176-205 lays down orgsync/, tests/existing/ and
            # config.json; :321 judges RUN_DIR, while :390-391 archives only
            # root *.py in submission/. da2561f:score.py:337 only records ACC.
            # Restore submission/ at target root plus those adjacent archives;
            # never fill a missing file from the live skeleton or another run.
            missing = [p for p in ORGSYNC_ARCHIVED_FILES if not (d / p).is_file()]
            if missing:
                r.add("SKIP", label, "missing archived path(s): " + ", ".join(missing)
                      + "; NOT re-judged")
                return
            archived.extend(d / p for p in ("orgsync", "config.json", "tests/existing"))
        for source in archived:
            for p in [source, *source.rglob("*")] if source.is_dir() else [source]:
                if p.is_symlink():
                    r.add("SKIP", label, f"archived path is a symlink: {p.relative_to(d)}; "
                          "refusing to pull files outside this bundle; NOT re-judged")
                    return
        with tempfile.TemporaryDirectory(prefix="model-bench-rejudge-") as tmp:
            target = Path(tmp) / "submission"
            shutil.copytree(submission, target)
            for source in archived[1:]:
                destination = target / source.relative_to(d)
                if source.is_dir():
                    shutil.copytree(source, destination, dirs_exist_ok=True)
                else:
                    shutil.copyfile(source, destination)
            env = {k: os.environ.get(k, default) for k, default in
                   (("PATH", os.defpath), ("LANG", "C.UTF-8"), ("LC_ALL", "C.UTF-8"))}
            env.update(TARGET_DIR=str(target), PYTHONDONTWRITEBYTECODE="1", HOME=tmp)
            judged = subprocess.run([sys.executable, str(judge.resolve())],
                                    cwd=tmp, env=env, capture_output=True,
                                    text=True, timeout=900)
        # Exit 0 alone proves nothing. Require exactly one machine verdict,
        # with an executed assertion budget, then compare the scored outcome.
        lines = re.findall(
            r"^ACCEPTANCE_RESULT pass=(True|False) failures=(\d+) errors=(\d+) tests=(\d+)$",
            judged.stdout, re.MULTILINE)
        if len(lines) != 1:
            raise ValueError("judge emitted no unique ACCEPTANCE_RESULT verdict line")
        passed, failures, errors, tests = lines[0]
        verdict = "PASS" if passed == "True" else "FAIL"
        if verdict == "PASS" and int(tests) == 0:
            raise ValueError("judge executed zero tests: " + lines[0].__repr__())
        if ((verdict == "PASS") != (judged.returncode == 0)
                or (verdict == "PASS" and (int(failures) or int(errors)))):
            raise ValueError("judge verdict line and exit/assertion counts disagree")
        recorded = res.get("outcome")
        if recorded not in ("PASS", "FAIL"):
            raise ValueError(f"recorded verdict is not PASS/FAIL: {recorded!r}")
        if verdict == recorded == "FAIL" and res.get("edge_failures") != f"{int(failures)}f+{int(errors)}e":
            raise ValueError(f"failure/error counts differ: recorded {res.get('edge_failures')!r}, "
                             f"re-judged {failures}f+{errors}e")
        r.add("PASS" if verdict == recorded else "FAIL", label,
              f"recorded {recorded}, re-judged {verdict}")
    except Exception as exc:
        r.add("FAIL", label, str(exc))


def verify_run(d, rep, r, root=None):
    """Re-derive one run from its own bundle."""
    label = os.path.relpath(d)
    result_p = os.path.join(d, "result.json")
    res = json.load(open(result_p))
    stamp_p = os.path.join(d, "stamp.json")
    stamp = json.load(open(stamp_p)) if os.path.exists(stamp_p) else {}

    # (1) evidence integrity: the session log is what was recorded
    sess = os.path.join(d, "session.jsonl")
    sha_p = sess + ".sha256"
    if os.path.exists(sess) and os.path.exists(sha_p):
        want = open(sha_p).read().strip()
        got = sha256_file(sess)
        r.add("PASS" if want == got else "FAIL", f"{label}: session log integrity",
              "" if want == got else f"recorded {want[:12]}, file hashes {got[:12]}")
    elif res.get("capture") not in (None, "OK"):
        r.add("SKIP", f"{label}: no session log", f"capture={res.get('capture')}")
        return
    else:
        r.add("SKIP", f"{label}: session log or hash absent")
        return

    # (2) commit-reveal: the judge that graded this run is the judge on disk
    jsha = stamp.get("judge_sha256")
    jname = stamp.get("judge")
    if jsha and jname:
        jp = os.path.join(os.path.dirname(os.path.abspath(__file__)), jname)
        if os.path.exists(jp):
            got = sha256_file(jp)
            r.add("PASS" if got == jsha else "FAIL", f"{label}: judge hash",
                  "" if got == jsha else f"stamp {jsha[:12]}, disk {got[:12]}")
        else:
            r.add("SKIP", f"{label}: judge {jname} not present (sealed?)")
    else:
        r.add("FAIL", f"{label}: stamp carries no judge_sha256",
              "commit-reveal unverifiable for this run")

    if res.get("capture") not in (None, "OK"):
        r.add("PASS" if res.get("cost_list_usd") is None else "FAIL",
              f"{label}: capture failure recorded", f"capture={res.get('capture')}; no cost derivable")
        return

    # (3) the cost re-derives from the log and the SNAPSHOTTED rate table
    prices_p = os.path.join(d, "prices.snapshot.yaml")
    if not os.path.exists(prices_p):
        r.add("SKIP", f"{label}: no rate-table snapshot")
        return
    # The report table is separate: bundle arithmetic uses this run's own pin,
    # so v11 original runs and v13 late admissions can coexist in one edition.
    got = sha256_file(prices_p)
    r.add("PASS" if got == stamp.get("prices_yaml_sha256") else "FAIL",
          f"{label}: stamped run price pin",
          "" if got == stamp.get("prices_yaml_sha256") else "snapshot sha256 mismatch")
    from pathlib import Path
    verification_root = Path(root).resolve() if root is not None else Path(d).resolve().parents[3]
    ancestors = []
    for parent in Path(d).resolve().parents:
        if parent != verification_root and verification_root not in parent.parents:
            break
        ancestors.append(parent)
        if parent == verification_root:
            break
    manifest_path = next((p / "MANIFEST.json" for p in ancestors
                          if (p / "MANIFEST.json").is_file()), None)
    manifest = json.loads(manifest_path.read_text()) if manifest_path is not None else {}
    if "roster" in manifest:
        key = res.get("model_key") or stamp.get("model_flag")
        expected = next((entry for entry in manifest.get("late_admissions", [])
                         if (entry["tool"], entry["model"]) == (res.get("tool"), key)), manifest)
        ok = (key in manifest["roster"].get(res.get("tool"), [])
              and bool(expected.get("prices_yaml_sha256"))
              and stamp.get("prices_yaml_sha256") == expected.get("prices_yaml_sha256"))
        r.add("PASS" if ok else "FAIL", f"{label}: expected run price pin")
    if "prices_yaml_version" in stamp:
        with open(prices_p, encoding="utf-8") as fh:
            version = re.search(r"^version:\s*(\d+)\s*$", fh.read(), re.MULTILINE)
        ok = version is not None and int(version.group(1)) == stamp["prices_yaml_version"]
        r.add("PASS" if ok else "FAIL", f"{label}: stamped run price version")
    fmt = "claude" if res.get("tool") == "claude-code" else res.get("tool", "codex")
    if fmt in ("opencode", "muse"):
        toks = sum_native_jsonl(sess, fmt)
        r.add("PASS" if toks == res.get("tokens") else "FAIL",
              f"{label}: native token counts re-derive")
    else:
        toks = sum_claude(sess) if fmt == "claude" else sum_codex(sess)
    if toks is None:
        r.add("SKIP", f"{label}: no usage events in session log")
        return
    rows = load_prices(prices_p)
    key = res.get("model_key") or stamp.get("model_flag")
    if key not in rows:
        r.add("FAIL", f"{label}: model {key} absent from snapshotted rate table")
        return
    want = res.get("cost_list_usd")
    if want is None:
        r.add("SKIP", f"{label}: no published cost to check")
        return
    got = derive_run_cost(sess, res, stamp, rows, fmt)
    # Tolerance covers float formatting only, not real disagreement.
    ok = abs(got - want) <= max(1e-6, abs(want) * 1e-4)
    r.add("PASS" if ok else "FAIL", f"{label}: cost re-derives",
          "" if ok else f"published ${want:.6f}, re-derived ${got:.6f}")


def verify_cells(runs, r):
    """The headline metric must be what PROTOCOL 4.1 says it is.

    EVERY add() here compares something. An earlier version emitted PASS on every
    path with no comparison at all -- 21 of 210 checks that could not fail, which
    is a rubber stamp wearing a checkmark. The invariants below are real:

      * a zero-pass cell must have SPENT money and still produce NO figure. That
        is the NO SCORE rule (PROTOCOL 4.1): cost without an accepted outcome is
        an absence of the thing measured, not inefficiency. A cell that spent
        nothing is a run that never happened and is a different fault.
      * E[$/accepted] = (cost over ALL runs) / (accepted runs) must be >= the
        naive mean over only the passing runs, because the numerator includes the
        cost of failures. Equality holds exactly when nothing failed. A cell where
        the naive figure is HIGHER means the headline is understating cost, which
        is the survivorship bug this metric exists to prevent -- and it is now a
        FAIL rather than a note.
    """
    cells = defaultdict(list)
    for res in runs:
        cells[(res.get("tool"), res.get("label"), res.get("task"))].append(res)
    for (tool, model, task), rs in sorted(cells.items()):
        costs = [x.get("cost_list_usd") for x in rs if x.get("cost_list_usd") is not None]
        npass = sum(1 for x in rs if x.get("outcome") == "PASS")
        name = f"{tool}/{model}/{task}"
        total = sum(costs)
        if len(costs) != len(rs):
            r.add("FAIL", f"{name}: unknown run cost — NO SCORE",
                  "every run's spend is required; never drop an unpriced run")
            continue
        if not npass:
            # Asserted, not announced: real spend, and no figure derivable.
            if total > 0:
                r.add("PASS", f"{name}: NO SCORE with ${total:.4f} spent -> no figure derivable (correct)")
            else:
                r.add("FAIL", f"{name}: zero passes AND zero cost",
                      "a cell that spent nothing did not run; NO SCORE requires real spend")
            continue
        expected = total / npass
        if not (expected > 0 and expected == expected and expected != float("inf")):
            r.add("FAIL", f"{name}: E[$/accepted] is not a finite positive number", f"got {expected!r}")
            continue
        pc = [x["cost_list_usd"] for x in rs
              if x.get("outcome") == "PASS" and x.get("cost_list_usd") is not None]
        naive = (sum(pc) / len(pc)) if pc else None
        if naive is None:
            r.add("FAIL", f"{name}: {npass} passes but no per-run cost on any of them",
                  "cannot cross-check the headline against the naive mean")
            continue
        if expected + 1e-12 < naive:
            r.add("FAIL", f"{name}: headline UNDERSTATES cost",
                  f"E[$/accepted]=${expected:.4f} < mean-over-passing ${naive:.4f} "
                  f"-- the survivorship bug PROTOCOL 4.1 forbids")
            continue
        if npass == len(rs):
            r.add("PASS", f"{name}: E[$/accepted]=${expected:.4f} == mean-over-passing "
                          f"(no failures, so they must agree)")
        else:
            r.add("PASS", f"{name}: E[$/accepted]=${expected:.4f} >= mean-over-passing "
                          f"${naive:.4f} over {npass}/{len(rs)} (failure cost retained)")


# Weights are the PROTOCOL 7a DECLARATION. Kept here so the report differ can
# recompute the TIER column rather than trusting the number printed in the table.
TASK_WEIGHT = {"baseline": 1.0, "cronspec": 3.0, "orgsync": 5.0}


def report_priced_runs(runs, r, edition_dir):
    """Return report-only copies; bundle snapshots/results remain evidence.

    A declared report table is a repo-relative path pinned by full SHA and
    version. Never fall back to snapshot dollars if that declaration is broken.
    Costs retain the scorer's six-decimal per-run precision.
    """
    manifest_path = os.path.join(edition_dir, "MANIFEST.json")
    name = "report rate table"
    try:
        manifest = json.load(open(manifest_path)) if os.path.exists(manifest_path) else {}
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be a JSON object")
        if "report_rate_table" not in manifest:
            print("Report diff uses bundle snapshots (no override declared)")
            if manifest.get("metrics_file") or (manifest.get("edition") == "2026-07"
                                                and (Path(edition_dir) / "REPORT.md").is_file()):
                verify_metrics(runs, r, edition_dir, None)
            return runs
        table = manifest["report_rate_table"]
        repo = os.path.dirname(os.path.realpath(__file__))
        path = os.path.realpath(os.path.join(repo, table["path"]))
        if os.path.isabs(table["path"]) or os.path.commonpath([repo, path]) != repo:
            raise ValueError("report rate table must be a path inside the repo")
        got = sha256_file(path)
        if got != table["sha256"]:
            raise ValueError(f"sha256 mismatch: recorded {table['sha256']}, file hashes {got}")
        with open(path, encoding="utf-8") as fh:
            version = re.search(r"^version:\s*(\d+)\s*$", fh.read(), re.MULTILINE)
        if version is None or int(version.group(1)) != table["version"]:
            raise ValueError("report rate table version mismatch")
        rows = load_prices(path)
        r.add("PASS", name, f"{table['path']} v{table['version']} sha256 {got}")
        print(f"report diff uses {table['path']} v{table['version']} sha256 {got}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        r.add("FAIL", name, str(exc))
        return None
    if manifest.get("metrics_file"):
        verify_metrics(runs, r, edition_dir, rows)
    priced = []
    for res in runs:
        try:
            sess = os.path.join(res["_bundle_dir"], "session.jsonl")
            fmt = "claude" if res.get("tool") == "claude-code" else res.get("tool", "codex")
            if res.get("capture") not in (None, "OK"):
                raise ValueError(f"capture={res['capture']}; no report cost derivable")
            stamp_path = os.path.join(res["_bundle_dir"], "stamp.json")
            stamp = json.load(open(stamp_path)) if os.path.exists(stamp_path) else {}
            cost = derive_run_cost(sess, res, stamp, rows, fmt)
            priced.append({**res, "cost_list_usd": round(cost, 6)})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            r.add("FAIL", f"report pricing {res.get('_bundle_dir')}", str(exc))
    return priced if len(priced) == len(runs) else None


def verify_report(runs, r, edition_dir):
    """Diff the edition directory's PUBLISHED report against its bundles.

    This closes the gap that let three documents disagree about Edition 1's scope
    with nothing failing: verify.py globbed run bundles and never opened a
    published document, so every table value, total and run count could be edited
    arbitrarily and it would still exit 0. The report claimed the tool
    "re-derives every number in this report". It did not.
    """
    evidence_runs = runs
    runs = report_priced_runs(runs, r, edition_dir)
    if runs is None:
        return
    uses_report_table = runs is not evidence_runs
    path = os.path.join(edition_dir, "REPORT.md")
    name = f"report {path}"
    if not os.path.exists(path):
        r.add("SKIP", f"{name}: no REPORT.md to check")
        return
    text = open(path, encoding="utf-8").read()

    # Totals first: run count, spend, and the accepted/not split.
    n_runs = len(runs)
    total = sum(x["cost_list_usd"] for x in runs if x.get("cost_list_usd") is not None)
    n_pass = sum(1 for x in runs if x.get("outcome") == "PASS")
    n_fail = n_runs - n_pass
    plain = text.replace("**", "")
    stated_runs = re.findall(r"(?:^|·)\s*(\d+)\s+runs\b", plain, re.MULTILINE)
    splits = re.findall(r"\b(\d+)\s+accepted\s*(?:/|·)\s*(\d+)\s+not\b(?:\s+accepted)?", plain)
    stated_totals = re.findall(r"\$(\d+(?:\.\d+)?)(?=\s+(?:total\b|at list rates\b))", plain)
    stated_totals += re.findall(r"\|\s*total spend\s*\|\s*\$(\d+(?:\.\d+)?)", plain, re.IGNORECASE)
    # Repriced reports must state the new TOTAL, not merely contain that dollar
    # amount in a cell. Preserve the legacy differ for undeclared editions.
    total_ok = (bool(stated_totals) and all(f"{total:.4f}" == n for n in stated_totals)
                if uses_report_table else f"${total:.4f}" in text)
    for label, needle, ok in (
        ("run count", f"{n_runs} runs", bool(stated_runs) and all(int(n) == n_runs for n in stated_runs)),
        ("total spend", f"${total:.4f}", total_ok),
        ("accepted count", f"{n_pass} accepted", bool(splits) and all(int(a) == n_pass for a, _ in splits)),
        ("not-accepted count", f"{n_fail} not", bool(splits) and all(int(b) == n_fail for _, b in splits)),
    ):
        if ok:
            r.add("PASS", f"{name}: states the {label} the bundles show ({needle})")
        else:
            r.add("FAIL", f"{name}: does not state the {label} the bundles show",
                  f"expected to find {needle!r} in {path}")

    # Then every per-cell E[$/accepted] and TIER the report prints.
    cells = defaultdict(list)
    for res in runs:
        cells[(res.get("model_key") or res.get("label"), res.get("task"))].append(res)
    # Results tables belong to the task in their heading. Edition 1 calls the
    # baseline task `duration`; a single-task report may omit the heading.
    tasks = {task for _, task in cells}
    task = next(iter(tasks)) if len(tasks) == 1 else None
    table_rows = defaultdict(list)
    columns = None
    for line in text.splitlines():
        if line.startswith("#"):
            columns = None
            heading = re.match(r"###\s+.*?`([^`]+)`", line)
            task = heading.group(1) if heading else None
            if task == "duration":
                task = "baseline"
        if not line.strip().startswith("|"):
            columns = None
            continue
        parts = [p.strip().strip("*`") for p in line.strip().strip("|").split("|")]
        if parts[0].lower() == "model":
            columns = None
            tier_col = next((i for i, p in enumerate(parts) if p.startswith("TIER")), None)
            e_col = next((i for i, p in enumerate(parts) if p.startswith("E[$/accepted]")), None)
            if tier_col is not None and e_col is not None:
                columns = (tier_col, e_col)
            continue
        if columns and task and len(parts) > max(columns) and not set(parts[0]) <= set("-:"):
            table_rows[(parts[0], task)].append((parts[columns[0]], parts[columns[1]]))
    checked = 0
    for (model, task), rs in sorted(cells.items()):
        if task not in TASK_WEIGHT:
            continue
        npass = sum(1 for x in rs if x.get("outcome") == "PASS")
        costs = [x["cost_list_usd"] for x in rs if x.get("cost_list_usd") is not None]
        if not npass or len(costs) != len(rs):
            continue  # NO SCORE cells print no figure to diff
        e = sum(costs) / npass
        tier = (TASK_WEIGHT[task] * npass) / (sum(costs) / 1000.0)
        rows = table_rows.get((model, task), [])
        if len(rows) != 1:
            r.add("FAIL", f"{name}: expected one results row for {model}/{task}",
                  f"found {len(rows)}")
            continue
        tier_cell, e_cell = rows[0]
        if f"${e:.4f}" != e_cell:
            r.add("FAIL", f"{name}: is missing E[$/accepted] for {model}/{task}",
                  f"bundles give ${e:.4f}")
            continue
        if f"{tier:,.0f}" != tier_cell:
            r.add("FAIL", f"{name}: is missing the TIER score for {model}/{task}",
                  f"bundles give {tier:,.0f} (weight {TASK_WEIGHT[task]})")
            continue
        checked += 1
    if checked:
        r.add("PASS", f"{name}: E[$/accepted] and TIER figures match the bundles in {checked} cells")
    else:
        r.add("FAIL", f"{name}: diffed ZERO report cells",
              "the table shape changed and this check has gone vacuous")


def verify_manifest(paths, r, root):
    """Edition 1's declaration must match every archived stamp, not one sample."""
    editions = {json.loads(Path(p).read_text()).get("edition") or
                json.loads(Path(p).with_name("stamp.json").read_text()).get("edition") or "2026-07"
                for p in paths}
    if editions != {"2026-07"}:
        return
    path = Path(root) / "editions/2026-07/MANIFEST.json"
    if not path.is_file():
        status = "FAIL" if path.with_name("REPORT.md").is_file() else "SKIP"
        r.add(status, "Edition 1 manifest absent")
        return
    try:
        manifest = json.loads(path.read_text())
        panel = defaultdict(set)
        for p in paths:
            p = Path(p)
            res = json.loads(p.read_text())
            stamp = json.loads(p.with_name("stamp.json").read_text())
            task = manifest["tasks"][res["task"]]
            expected = (task["prompt_sha256"], task["judge_sha256"],
                        manifest["prices_yaml_sha256"])
            recorded = tuple(stamp.get(k) for k in
                             ("prompt_file_sha256", "judge_sha256", "prices_yaml_sha256"))
            r.add("PASS" if expected == recorded else "FAIL",
                  f"{os.path.relpath(p.parent)}: manifest hashes equal bundle stamp",
                  "" if expected == recorded else f"manifest {expected}, stamp {recorded}")
            panel[(res["tool"], res["model_key"], res["task"])].add(res["rep"])
            if res.get("prices_yaml_version") != manifest["prices_yaml_version"]:
                raise ValueError(f"{p}: prices version differs from manifest")
        declared = {(tool, model, task): set(range(1, spec["reps"] + 1))
                    for tool, models in manifest["roster"].items() for model in models
                    for task, spec in manifest["tasks"].items()}
        ok = (manifest["edition"] == "2026-07" and dict(panel) == declared
              and len(paths) == sum(len(reps) for reps in declared.values())
              and manifest["scored_models"] == sum(map(len, manifest["roster"].values())))
        r.add("PASS" if ok else "FAIL", "Edition 1 manifest roster/tasks/reps equal archived panel")
    except Exception as exc:
        r.add("FAIL", "Edition 1 manifest unreadable or inconsistent", str(exc))


def errata_pin(value):
    match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9._-]*)=(0|[1-9][0-9]*):([0-9a-f]{64})', value)
    if not match:
        raise argparse.ArgumentTypeError('invalid errata pin; expected edition=N:sha256')
    return match[1], (int(match[2]), match[3])


def verify_errata(edition_dir, report, expected=()):
    root = Path(edition_dir).resolve()
    try:
        # Announce the retained tip even if validation subsequently fails.
        manifests = [(0, root / 'MANIFEST.json')]
        for path in root.glob('MANIFEST.v*.json'):
            match = re.fullmatch(r'MANIFEST\.v([1-9][0-9]*)\.json', path.name)
            if match:
                manifests.append((int(match[1]), path))
        revision, newest = max(manifests, key=lambda pair: pair[0])
        newest_hash = sha256_file(newest)
        print(f'{root.name}: errata revision {revision}, newest manifest sha256 {newest_hash}')
        files, entries = load_errata(root)
        if files:
            report.add('PASS', 'report errata integrity',
                       f'{len(files) // 2} revisions, {len(entries)} entries; hashes and schema match')
        for pin in expected:
            matches = pin == (revision, newest_hash)
            report.add('PASS' if matches else 'FAIL', f'report errata pin ({root.name})',
                       f'expected {pin[0]}:{pin[1]}, retained {revision}:{newest_hash}')
    except (OSError, ValueError) as exc:
        report.add('FAIL', 'report errata integrity', str(exc))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", nargs="?", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--rejudge", action="store_true",
                    help="execute revealed judges on temporary submission copies")
    ap.add_argument("--require-no-skips", action="store_true",
                    help="fail on every SKIP row, including skipped rejudge checks")
    ap.add_argument('--expect', type=errata_pin, action='append', default=[],
                    help='pin edition=N:sha256 to its published errata tip (repeatable)')
    args = ap.parse_args()
    root = args.root
    # July's edition URL carries a mirror; its metrics retain legacy paths.
    july = Path(root).resolve()
    july_redirect = july.name == '2026-07' and july.parent.name == 'editions'
    if july_redirect:
        root = str(july.parent.parent)
    r = Report()
    if july_redirect:
        errata_dirs = [july]
    elif (Path(root) / 'editions').is_dir():
        errata_dirs = sorted(path for path in (Path(root) / 'editions').iterdir() if path.is_dir())
    else:
        edition = Path(root).resolve()
        # Generic run-bundle roots are also supported; they are not editions.
        errata_dirs = ([edition] if edition.parent.name == 'editions'
                       or (edition / 'MANIFEST.json').exists()
                       or any(edition.glob('MANIFEST.v*.json'))
                       or any(edition.glob('ERRATA.v*.json')) else [])
    checked_editions = set()
    for edition in errata_dirs:
        checked_editions.add(edition.name)
        verify_errata(edition, r, [pin for name, pin in args.expect if name == edition.name])
    for name, pin in args.expect:
        if name not in checked_editions:
            r.add('FAIL', f'report errata pin ({name})', 'requested edition was not checked')
    paths = sorted(glob.glob(os.path.join(root, "*/*/*/rep*/result.json")))
    if not paths:
        print(f"no run bundles under {root}", file=sys.stderr)
        r.emit(require_no_skips=args.require_no_skips)
        return 2
    print(f"verifying {len(paths)} run bundles under {root}\n")
    if july_redirect:
        originals = {p.relative_to(root).as_posix(): p
                     for result in paths for p in Path(result).parent.rglob('*') if p.is_file()}
        copies = {p.relative_to(july).as_posix(): p
                  for bundle in july.glob('*/*/*/rep*') for p in bundle.rglob('*') if p.is_file()}
        differences = sorted(set(originals) ^ set(copies))
        differences += [name for name in sorted(originals.keys() & copies.keys())
                        if originals[name].read_bytes() != copies[name].read_bytes()]
        r.add('FAIL' if differences else 'PASS', 'July bundle mirror byte-identical to root',
              ', '.join(differences))
    runs = []
    for p in paths:
        d = os.path.dirname(p)
        try:
            runs.append({**json.load(open(p)), "_bundle_dir": d})
            verify_run(d, os.path.basename(d), r, root)
        except Exception as exc:                      # noqa: BLE001
            r.add("FAIL", f"{os.path.relpath(d)}: unreadable bundle", str(exc))
    verify_cells(runs, r)
    # Repository and archive roots retain the Edition 1 report location.
    repo_root = os.path.dirname(os.path.abspath(__file__))
    edition_one_root = (os.path.realpath(root) == os.path.realpath(repo_root)
                        or os.path.isfile(os.path.join(root, "editions", "2026-07", "MANIFEST.json")))
    edition_dir = (os.path.join(root, "editions", "2026-07")
                   if edition_one_root else root)
    verify_report(runs, r, edition_dir)
    if edition_one_root:
        verify_manifest(paths, r, root)
    if args.rejudge:
        judged = Report()
        for p in paths:
            rejudge_run(os.path.dirname(p), judged, os.path.dirname(os.path.abspath(__file__)))
        counts = {status: sum(row[0] == status for row in judged.rows)
                  for status in ("PASS", "FAIL", "SKIP")}
        if args.require_no_skips:
            counts["FAIL"] += counts["SKIP"]
            counts["SKIP"] = 0
        print(f"RE-JUDGE {len(paths)} bundles · PASS {counts['PASS']} · "
              f"FAIL {counts['FAIL']} · SKIP {counts['SKIP']}")
        r.rows.extend(judged.rows)
    evidence_fails = sum(row[0] == "FAIL" for row in r.rows)
    converted_skips = sum(row[0] == "SKIP" for row in r.rows) if args.require_no_skips else 0
    fails = r.emit(require_no_skips=args.require_no_skips)
    if evidence_fails:
        print(f"\n{evidence_fails} CHECK(S) FAILED — published figures do not follow from the evidence.")
    if converted_skips:
        print(f"\n{converted_skips} CHECK(S) FAILED — --require-no-skips requires every check to execute.")
    if not fails:
        if any(row[0] == "SKIP" for row in r.rows):
            print("\nVERIFIED — all executed checks passed; SKIPs remain unverified.")
        else:
            print("\nVERIFIED — every published number re-derives from the shipped evidence.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
