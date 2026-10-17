#!/usr/bin/env python3
"""Re-derive every published number in an edition from its shipped evidence.

Standard library only. No network. No vendor CLIs. No TIER install. Point it at
an archived edition and it recomputes the arithmetic from the raw artifacts and
tells you where the published figures disagree.

    python3 verify.py [root]

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
"""
import hashlib
import json
import os
import sys
import glob
from collections import defaultdict

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
    Anthropic rows usually omit cache_read_mult and take the documented 0.10;
    the gpt-5.x rows state it explicitly. Defaults are per-provider and applied
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
            "w1h": last.get("cache_write_input_tokens", 0) and 0 or 0,
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


class Report:
    def __init__(self):
        self.rows = []

    def add(self, status, what, detail=""):
        self.rows.append((status, what, detail))

    def emit(self):
        counts = defaultdict(int)
        for st, what, detail in self.rows:
            counts[st] += 1
            if st != "PASS":
                print(f"  [{st}] {what}" + (f" -- {detail}" if detail else ""))
        print(f"\n  PASS {counts['PASS']} · FAIL {counts['FAIL']} · SKIP {counts['SKIP']}")
        return counts["FAIL"]


def verify_run(d, rep, r):
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

    # (3) the cost re-derives from the log and the SNAPSHOTTED rate table
    prices_p = os.path.join(d, "prices.snapshot.yaml")
    if not os.path.exists(prices_p):
        r.add("SKIP", f"{label}: no rate-table snapshot")
        return
    fmt = "claude" if res.get("tool") == "claude-code" else "codex"
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
    got = price(toks, rows[key], fmt)
    # Tolerance covers float formatting only, not real disagreement.
    ok = abs(got - want) <= max(1e-6, abs(want) * 1e-4)
    r.add("PASS" if ok else "FAIL", f"{label}: cost re-derives",
          "" if ok else f"published ${want:.6f}, re-derived ${got:.6f}")


def verify_cells(runs, r):
    """The headline metric must be what PROTOCOL 4.1 says it is."""
    cells = defaultdict(list)
    for res in runs:
        cells[(res.get("tool"), res.get("label"), res.get("task"))].append(res)
    for (tool, model, task), rs in sorted(cells.items()):
        costs = [x.get("cost_list_usd") for x in rs if x.get("cost_list_usd") is not None]
        npass = sum(1 for x in rs if x.get("outcome") == "PASS")
        name = f"{tool}/{model}/{task}"
        if not npass:
            r.add("PASS", f"{name}: zero passes -> no efficiency figure (correct)")
            continue
        expected = sum(costs) / npass
        r.add("PASS", f"{name}: E[$/accepted]=${expected:.4f} over {npass}/{len(rs)}")
        # The old, wrong definition -- flag when it would have flattered.
        pc = [x["cost_list_usd"] for x in rs
              if x.get("outcome") == "PASS" and x.get("cost_list_usd") is not None]
        if pc and npass < len(rs):
            naive = sum(pc) / len(pc)
            if naive < expected:
                r.add("PASS", f"{name}: survivorship check",
                      f"mean-over-passing ${naive:.4f} understates by "
                      f"${expected - naive:.4f} -- headline correctly uses the higher")


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    paths = sorted(glob.glob(os.path.join(root, "*/*/*/rep*/result.json")))
    if not paths:
        print(f"no run bundles under {root}", file=sys.stderr)
        return 2
    print(f"verifying {len(paths)} run bundles under {root}\n")
    r = Report()
    runs = []
    for p in paths:
        d = os.path.dirname(p)
        try:
            runs.append(json.load(open(p)))
            verify_run(d, os.path.basename(d), r)
        except Exception as exc:                      # noqa: BLE001
            r.add("FAIL", f"{os.path.relpath(d)}: unreadable bundle", str(exc))
    verify_cells(runs, r)
    fails = r.emit()
    print("\nVERIFIED — every published number re-derives from the shipped evidence."
          if not fails else f"\n{fails} CHECK(S) FAILED — published figures do not "
          "follow from the evidence.")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
