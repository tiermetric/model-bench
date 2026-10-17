#!/usr/bin/env python3
"""Independent acceptance suite (hidden judge) for the `orgsync` bench task.

This is the uniform judge applied identically to every leg. It is built to
`design/orgsync/JUDGE_SPEC.md`; the expected artifacts come from
`design/orgsync/FIXTURES.md` §4 and are reproduced by the sealed reference
implementation, which must PASS this judge.

How it judges
-------------
It does NOT import the model's code. It:

1. copies the target repo into a scratch directory (so nothing it does can
   touch the model's submission, and — because the model's tool is only ever
   *exec'd* as a subprocess, never imported — a model-authored `json.py` or
   `http/` cannot shadow a stdlib module inside this judge);
2. starts its OWN fresh fixture pair (`fixtures/orgsync/server.py`) on
   OS-assigned ports, with its own request logs — the model's dev-time fixture
   state is never judged;
3. rewrites `config.json` in the scratch copy with the live URLs;
4. runs `python3 -m orgsync sync` TWICE against that one fixture process
   (single process lifetime = deterministic 429 ordinals and a ledger that
   remembers run 1's posts);
5. runs `python3 -m unittest discover tests/existing` last, so the brownfield
   rerun cannot pollute the request log the assertions read;
6. tears the fixture down — SIGTERM to the process GROUP, then SIGKILL — in a
   `finally`, so neither a port nor a process leaks on any exit path.

Evidence: the three output files, the fixture request logs (`n`-ordered, split
into run 1 / run 2 by a snapshot taken between invocations), and the exit codes.

Every assertion carries the prompt rule it enforces. Assertions are executed
inside `subTest`, so a failing check never suppresses the checks after it —
that is what lets a control implementation fail exactly the assertions it was
designed to trip, and it keeps the assertion budget below honest.

Vacuous-pass guards (a judge that cannot fail is not a judge):
  * a missing/empty target (no `orgsync/__main__.py`) is a hard error, never a
    pass;
  * a target that runs but writes nothing fails A00/A03/A04/A05;
  * `EXPECTED_CHECKS` assertions must actually have executed (`TestZZBudget`);
  * A06 and A08 each assert their own precondition fired (a write was made at
    all; a 429 was actually served), so neither can pass vacuously.

CORRECTED RULE 5 (see prompts/orgsync.md): a rejected write is one answered
`400`, `409` or `422`. A `429` on a write is NOT a rejection — the fixture's
rate-limit schedule guarantees one lands on a POST — it is rule 6's rate limit
and must be waited out and retried. JUDGE_SPEC's A6 wording ("any 4xx") is the
older, unsatisfiable phrasing; the prompt is authoritative and is what is
implemented here.

Usage:
    python3 acceptance_orgsync.py <target-dir>
    TARGET_DIR=<target-dir> python3 acceptance_orgsync.py

Set ORGSYNC_JUDGE_KEEP=1 to keep the scratch directory for debugging.

Exit code 0 iff all assertions pass. Always prints a final machine-readable
line (same shape as acceptance_test.py / acceptance_cronspec.py, so run.sh and
score.py parse it identically):
    ACCEPTANCE_RESULT pass=<True|False> failures=<n> errors=<n> tests=<n>
"""

import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import parse_qs

BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
FIXTURE_SERVER = os.path.join(BENCH_DIR, "fixtures", "orgsync", "server.py")
SKELETON_DIR = os.path.join(BENCH_DIR, "tasks", "orgsync-skeleton")
SKELETON_EXISTING_TESTS = os.path.join(SKELETON_DIR, "tests", "existing")

# ── Timing / limits ──────────────────────────────────────────────────────────

#: Seconds allowed for one `python3 -m orgsync sync`. A correct run is a couple
#: of seconds (two 1s rate-limit waits); this only stops a hung target.
SYNC_TIMEOUT_SECONDS = 300.0
#: Seconds allowed for the `tests/existing` rerun.
TESTS_TIMEOUT_SECONDS = 180.0
#: Seconds to wait for the fixture to bind and print its ports.
FIXTURE_START_TIMEOUT_SECONDS = 30.0

#: `Retry-After` the fixture serves with every 429 (FIXTURES.md §3).
RETRY_AFTER_SECONDS = 1.0
#: Tolerance on the observed wait (FIXTURES.md §7): the next request to a
#: rate-limited service must be at least `Retry-After − 0.05s` later.
RETRY_TOLERANCE_SECONDS = 0.05

#: Statuses that mean the ledger REJECTED a write (prompt rule 5, as corrected).
#: 429 is deliberately absent: it is rule 6's rate limit, not a rejection.
REJECT_STATUSES = (400, 409, 422)
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

#: Scratch entries never copied into the judge's working copy of the target.
#: `out/` in particular MUST NOT be copied: a stale dev-time output file would
#: let a target that writes nothing look like a target that wrote the answer.
COPY_EXCLUDE = frozenset({
    ".git", "__pycache__", "out", ".fixture-logs", "fixture-logs",
    ".fixture-ports.json", ".fixture-stderr.log", "submission",
    "judge-fixture-logs", ".venv", "venv", "node_modules", ".mypy_cache",
    ".pytest_cache",
})

# ── The answer key (FIXTURES.md §4; reproduced by the sealed reference) ───────

EXPECTED_SUMMARIES = [
    {"owner": "amara", "open_items": 2, "done_items": 1, "total_points": 12},
    {"owner": "bo", "open_items": 1, "done_items": 1, "total_points": 6},
    {"owner": "dee", "open_items": 0, "done_items": 0, "total_points": 0},
    {"owner": "eli", "open_items": 1, "done_items": 1, "total_points": 3},
]

EXPECTED_ORPHANS = [
    {"id": "item-104", "owner": "chen", "status": "open", "points": 8},
    {"id": "item-106", "owner": "zara", "status": "open", "points": 4},
    {"id": "item-107", "owner": "fen", "status": "open", "points": 2},
]

EXPECTED_ERRORS = [
    {"service": "ledger",
     "record": {"id": "acct-06", "owner": "fen", "active": "yes"}},
    {"service": "directory",
     "record": {"id": "item-109", "owner": "amara", "status": "open"}},
]

#: Pages every collection must be read to (rule 1). accounts 7/3, items 12/4,
#: summaries 0/10 — the last page is the one whose `next_page` is null.
EXPECTED_PAGES = {
    ("ledger", "/v1/accounts"): {1, 2, 3},
    ("directory", "/v1/items"): {1, 2, 3},
    ("ledger", "/v1/summaries"): {1},
}

#: Number of individual checks a complete pass executes. Asserted at the end so
#: a judge that silently skipped its own assertions cannot report a pass.
EXPECTED_CHECKS = 34


# ── Small helpers ────────────────────────────────────────────────────────────


def canonical(obj):
    """Order-independent string identity for a parsed JSON value."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def as_set(records):
    return frozenset(canonical(r) for r in records)


def describe(records):
    """Readable, deterministic rendering of a record collection."""
    return "[" + ", ".join(sorted(canonical(r) for r in records)) + "]"


def diff_sets(got, want):
    """`(missing, unexpected)` as sorted lists of canonical strings."""
    return sorted(want - got), sorted(got - want)


def _fatal(message, tests=0):
    """Report a judge-level error and exit non-zero, never silently pass."""
    print("JUDGE ERROR: %s" % message)
    print("ACCEPTANCE_RESULT pass=False failures=0 errors=1 tests=%d" % tests)
    sys.exit(1)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_tree(root):
    """`{relpath: sha256}` for every file under `root` (empty when absent)."""
    result = {}
    if not os.path.isdir(root):
        return result
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, name)
            try:
                result[os.path.relpath(full, root)] = sha256_file(full)
            except OSError as exc:
                result[os.path.relpath(full, root)] = "UNREADABLE:%s" % exc
    return result


def read_jsonl(path):
    """Read a JSONL output file.

    Returns `{"exists", "records", "error"}`. Blank lines are ignored (a
    trailing newline is normal); a line that is not JSON is fatal for the file
    and reported as `error`, because "one JSON line" per entry is stated.
    """
    out = {"exists": False, "records": [], "error": None}
    if not os.path.isfile(path):
        out["error"] = "file does not exist"
        return out
    out["exists"] = True
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        out["error"] = "unreadable: %s" % exc
        return out
    for lineno, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            out["records"].append(json.loads(line))
        except ValueError as exc:
            out["error"] = "line %d is not JSON (%s): %r" % (lineno, exc, line[:200])
            return out
    return out


def duplicates(records):
    """Canonical forms appearing more than once, with their counts."""
    counts = {}
    for record in records:
        key = canonical(record)
        counts[key] = counts.get(key, 0) + 1
    return {k: v for k, v in counts.items() if v > 1}


def load_request_log(path):
    """Parse one fixture request log into `n`-ordered entries."""
    entries = []
    if not os.path.isfile(path):
        return entries
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return entries
    entries.sort(key=lambda e: e.get("n", 0))
    return entries


def page_of(entry):
    """The `page` a list request asked for; 1 when absent (the fixture's rule)."""
    values = parse_qs(entry.get("query") or "").get("page")
    if not values:
        return 1
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return None


def request_signature(entry):
    """Identity of a logical request, for 'was the 429'd request retried?'."""
    return (
        entry.get("method"),
        entry.get("path"),
        page_of(entry),
        entry.get("idempotency_key"),
        canonical(entry.get("body")),
    )


def short(entry):
    return "n=%s %s %s%s -> %s" % (
        entry.get("n"), entry.get("method"), entry.get("path"),
        ("?" + entry["query"]) if entry.get("query") else "", entry.get("status"),
    )


# ── Subprocess plumbing ──────────────────────────────────────────────────────


def _child_env():
    """Environment for every child: no inherited PYTHONPATH, no .pyc litter."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _kill_group(proc, grace=5.0):
    """SIGTERM then SIGKILL the child's process GROUP; never raise."""
    if proc is None or proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        pgid = None
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            if pgid is not None:
                os.killpg(pgid, sig)
            else:
                proc.send_signal(sig)
        except OSError:
            pass
        deadline = time.time() + (grace if sig == signal.SIGTERM else 2.0)
        while time.time() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.05)
    try:
        proc.wait(timeout=1)
    except Exception:
        pass


def run_command(cmd, cwd, timeout):
    """Run `cmd`, returning `(returncode, stdout, stderr)`.

    The child gets its own process group and the group is killed on timeout, so
    a target that spawns helpers cannot leave one behind. A timeout returns
    code 124 (the conventional `timeout(1)` code) rather than raising.
    """
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=_child_env(), start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout.decode("utf-8", "replace"), stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            stdout, stderr = b"", b""
        return (124,
                stdout.decode("utf-8", "replace"),
                stderr.decode("utf-8", "replace") + "\n[judge] TIMEOUT after %ss" % timeout)
    finally:
        _kill_group(proc)


def tail(text, limit=1200):
    text = (text or "").strip()
    return text[-limit:] if len(text) > limit else text


# ── Evidence collection ──────────────────────────────────────────────────────


def _copy_target(target, dest):
    """Copy the model's repo into a scratch location, minus scratch/state dirs."""
    def ignore(dirname, names):
        return {n for n in names if n in COPY_EXCLUDE or n.endswith(".pyc")}
    shutil.copytree(target, dest, ignore=ignore, symlinks=False)


def _start_fixture(work_root, log_dir):
    """Start the fixture pair; return `(proc, urls, ports_path, stderr_path)`."""
    ports_path = os.path.join(work_root, "fixture-ports.json")
    stderr_path = os.path.join(work_root, "fixture-stderr.log")
    out_handle = open(ports_path, "w", encoding="utf-8")
    err_handle = open(stderr_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, FIXTURE_SERVER,
         "--directory-port", "0", "--ledger-port", "0", "--log-dir", log_dir],
        cwd=work_root, stdout=out_handle, stderr=err_handle,
        env=_child_env(), start_new_session=True,
    )
    out_handle.close()
    err_handle.close()

    deadline = time.time() + FIXTURE_START_TIMEOUT_SECONDS
    urls = None
    while time.time() < deadline:
        if os.path.getsize(ports_path) > 0:
            try:
                with open(ports_path, "r", encoding="utf-8") as handle:
                    payload = json.loads(handle.readline())
                if "directory_url" in payload and "ledger_url" in payload:
                    urls = payload
                    break
            except ValueError:
                pass
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    if urls is None:
        _kill_group(proc)
        detail = ""
        try:
            with open(stderr_path, "r", encoding="utf-8") as handle:
                detail = tail(handle.read(), 600)
        except OSError:
            pass
        raise RuntimeError("fixture services did not start; stderr: %s" % detail)
    return proc, urls


def collect_evidence(target):
    """Run the whole judged procedure; return the evidence dict."""
    evidence = {
        "target": target,
        "runs": [],                      # per invocation: rc/stdout/stderr/files
        "log": {"directory": [], "ledger": []},        # all entries, n-ordered
        "run1_len": {"directory": 0, "ledger": 0},     # split point
        "tests_existing": None,
        "existing_hashes": hash_tree(os.path.join(target, "tests", "existing")),
        "skeleton_hashes": hash_tree(SKELETON_EXISTING_TESTS),
    }

    work_root = tempfile.mkdtemp(prefix="orgsync-judge-")
    repo = os.path.join(work_root, "repo")
    log_dir = os.path.join(work_root, "fixture-logs")
    os.makedirs(log_dir, exist_ok=True)
    fixture = None
    try:
        _copy_target(target, repo)
        fixture, urls = _start_fixture(work_root, log_dir)

        # The judge owns config.json for the judged runs (FIXTURES.md §2): the
        # model's copy points at a fixture that is gone.
        with open(os.path.join(repo, "config.json"), "w", encoding="utf-8") as handle:
            json.dump({"directory_url": urls["directory_url"],
                       "ledger_url": urls["ledger_url"]}, handle, indent=2)

        for index in (1, 2):
            rc, out, err = run_command(
                [sys.executable, "-m", "orgsync", "sync"], repo, SYNC_TIMEOUT_SECONDS)
            record = {"rc": rc, "stdout": out, "stderr": err, "files": {}}
            for name in ("summary", "orphans", "errors"):
                record["files"][name] = read_jsonl(
                    os.path.join(repo, "out", "%s.jsonl" % name))
            record["out_dir_exists"] = os.path.isdir(os.path.join(repo, "out"))
            evidence["runs"].append(record)
            if index == 1:
                for service in ("directory", "ledger"):
                    evidence["run1_len"][service] = len(
                        load_request_log(os.path.join(log_dir, "%s.jsonl" % service)))

        # Brownfield rerun LAST, so it cannot add requests to the judged log.
        rc, out, err = run_command(
            [sys.executable, "-m", "unittest", "discover", "tests/existing"],
            repo, TESTS_TIMEOUT_SECONDS)
        evidence["tests_existing"] = {"rc": rc, "stdout": out, "stderr": err}

        for service in ("directory", "ledger"):
            evidence["log"][service] = load_request_log(
                os.path.join(log_dir, "%s.jsonl" % service))
    finally:
        _kill_group(fixture)
        # Archive the judge's own request logs into the run bundle as evidence.
        try:
            dest = os.path.join(target, "judge-fixture-logs")
            if os.path.isdir(log_dir):
                if os.path.isdir(dest):
                    shutil.rmtree(dest, ignore_errors=True)
                shutil.copytree(log_dir, dest)
        except OSError:
            pass
        if os.environ.get("ORGSYNC_JUDGE_KEEP"):
            print("[judge] scratch kept at %s" % work_root)
        else:
            shutil.rmtree(work_root, ignore_errors=True)
    return evidence


# ── Derived views over the evidence ──────────────────────────────────────────


def entries(ev, service, run=None):
    """Log entries for `service`, optionally restricted to run 1 or run 2."""
    all_entries = ev["log"][service]
    split = ev["run1_len"][service]
    if run == 1:
        return all_entries[:split]
    if run == 2:
        return all_entries[split:]
    return all_entries


def all_entries(ev, run=None):
    return entries(ev, "directory", run) + entries(ev, "ledger", run)


def accepted_posts(ev, run=None):
    """Ledger POSTs the fixture accepted (201) — i.e. the summaries it holds."""
    return [e for e in entries(ev, "ledger", run)
            if e.get("method") == "POST" and e.get("path") == "/v1/summaries"
            and e.get("status") == 201]


def summary_posts(ev, run=None):
    return [e for e in entries(ev, "ledger", run)
            if e.get("method") == "POST" and e.get("path") == "/v1/summaries"]


def writes(ev, run=None):
    return [e for e in all_entries(ev, run) if e.get("method") in WRITE_METHODS]


# ── Target resolution + preflight (vacuous-pass guards) ──────────────────────


def _resolve_target():
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        target = sys.argv.pop(1)
    else:
        target = os.environ.get("TARGET_DIR", "")
    if not target:
        print("usage: acceptance_orgsync.py <target-dir>  (or TARGET_DIR env)")
        print("ACCEPTANCE_RESULT pass=False failures=0 errors=1 tests=0")
        sys.exit(2)
    target = os.path.abspath(target)
    if not os.path.isdir(target):
        _fatal("target directory does not exist: %s" % target)
    if not os.path.isfile(FIXTURE_SERVER):
        _fatal("fixture server not found at %s" % FIXTURE_SERVER)
    if not os.path.isdir(SKELETON_EXISTING_TESTS):
        _fatal("skeleton tests/existing not found at %s (cannot verify the "
               "brownfield invariant)" % SKELETON_EXISTING_TESTS)
    # Vacuous-pass guard: an empty directory, or one with no runnable package,
    # is an ERROR, never a pass. `python3 -m orgsync` needs orgsync/__main__.py.
    main_module = os.path.join(target, "orgsync", "__main__.py")
    if not os.path.isfile(main_module):
        _fatal("no runnable orgsync package in %s (missing orgsync/__main__.py) "
               "— `python3 -m orgsync sync` cannot exist" % target)
    if os.path.getsize(main_module) == 0:
        _fatal("orgsync/__main__.py in %s is empty" % target)
    return target


_TARGET = _resolve_target()
try:
    EV = collect_evidence(_TARGET)
except Exception as exc:  # judge-side failure: report, never pass
    _fatal("evidence collection failed: %r" % (exc,))

#: Count of individual checks executed; guarded by TestZZBudget.
CHECKS = 0


class JudgeCase(unittest.TestCase):
    """Base class: every check is counted and runs inside its own subTest.

    subTest is deliberate — a failing check must not suppress the ones after
    it, or a control implementation would fail one assertion and hide the rest,
    and the assertion budget would under-count on every failing run.
    """

    def check(self, label, condition, message):
        global CHECKS
        CHECKS += 1
        with self.subTest(check=label):
            self.assertTrue(condition, "[%s] %s" % (label, message))

    def check_eq(self, label, got, want, message):
        global CHECKS
        CHECKS += 1
        with self.subTest(check=label):
            self.assertEqual(got, want, "[%s] %s" % (label, message))


class TestA00TargetRuns(JudgeCase):
    """A00 — the tool ran. Prompt: "Exit code `0` on success." / "Create `out/`
    if it does not exist." A stub that raises NotImplementedError dies here."""

    def test_run1_succeeded(self):
        run = EV["runs"][0]
        self.check(
            "A00.exit0", run["rc"] == 0,
            "prompt rule: 'Exit code 0 on success' — first `python3 -m orgsync "
            "sync` exited %s. stderr tail:\n%s" % (run["rc"], tail(run["stderr"])))
        self.check(
            "A00.outdir", run["out_dir_exists"],
            "prompt rule: 'Create out/ if it does not exist' — no out/ directory "
            "after the first run (a target that writes nothing is not a pass)")


class TestA01ExistingTests(JudgeCase):
    """A1 — prompt ground rule: "files under `tests/existing/` must not be
    edited, and `python3 -m unittest discover tests/existing` must still pass"."""

    def test_existing_suite(self):
        got, want = EV["existing_hashes"], EV["skeleton_hashes"]
        self.check(
            "A1.present", bool(got),
            "prompt ground rule: tests/existing/ must not be edited — the "
            "directory is missing or empty in the submission")
        missing = sorted(set(want) - set(got))
        changed = sorted(f for f in set(want) & set(got) if want[f] != got[f])
        self.check(
            "A1.unmodified", not missing and not changed,
            "prompt ground rule: files under tests/existing/ must not be edited "
            "— missing=%s modified=%s" % (missing, changed))
        extra = sorted(set(got) - set(want))
        self.check(
            "A1.no_additions", not extra,
            "prompt ground rule: tests/existing/ must not be edited — files "
            "added to it: %s" % extra)
        result = EV["tests_existing"]
        self.check(
            "A1.suite_passes", result["rc"] == 0,
            "prompt ground rule: `python3 -m unittest discover tests/existing` "
            "must still pass — exited %s:\n%s"
            % (result["rc"], tail(result["stderr"] or result["stdout"])))


class TestA02AcceptedPosts(JudgeCase):
    """A2 — prompt rule 2 ("The ledger decides who counts", zero-item and
    inactive clauses) + rule 5 ("Post what the ledger is missing, and only
    that"). Judged from the ledger's own record of what it accepted."""

    def test_accepted_post_bodies(self):
        posts = accepted_posts(EV, run=1)
        bodies = [p.get("body") for p in posts]
        got, want = as_set(bodies), as_set(EXPECTED_SUMMARIES)
        missing, unexpected = diff_sets(got, want)
        self.check(
            "A2.set", got == want,
            "prompt rules 2+5: exactly one summary per active well-formed "
            "account (zero-item accounts included, inactive and malformed "
            "accounts excluded), and post only what the ledger is missing — "
            "missing=%s unexpected=%s" % (missing, unexpected))
        self.check_eq(
            "A2.count", len(bodies), len(EXPECTED_SUMMARIES),
            "prompt rule 2: exactly ONE summary per active account — the ledger "
            "accepted %d posts: %s" % (len(bodies), describe(bodies)))


class TestA03SummaryFile(JudgeCase):
    """A3 — prompt rule 5: "Record each summary you post as one JSON line
    (exactly the POST body) in `out/summary.jsonl`"."""

    def test_summary_file(self):
        data = EV["runs"][0]["files"]["summary"]
        self.check(
            "A3.readable", data["exists"] and data["error"] is None,
            "prompt rule 5: out/summary.jsonl is one JSON line per summary — %s"
            % data["error"])
        got = as_set(data["records"])
        missing, unexpected = diff_sets(got, as_set(EXPECTED_SUMMARIES))
        self.check(
            "A3.set", got == as_set(EXPECTED_SUMMARIES),
            "prompt rule 5 (+ rule 2): out/summary.jsonl must hold exactly the "
            "required summaries — missing=%s unexpected=%s" % (missing, unexpected))
        dupes = duplicates(data["records"])
        self.check(
            "A3.no_duplicates", not dupes,
            "prompt: 'each entry appears exactly once' — duplicated lines in "
            "out/summary.jsonl: %s" % sorted(dupes))
        posted = as_set(p.get("body") for p in accepted_posts(EV, run=1))
        self.check(
            "A3.matches_posts", got == posted,
            "prompt rule 5: each recorded line is exactly the POST body — file=%s "
            "accepted posts=%s" % (sorted(got), sorted(posted)))


class TestA04Orphans(JudgeCase):
    """A4 — prompt rule 3 ("Items without an active owner are orphans… exactly
    once") plus rule 4's "a malformed account is not an account"."""

    def test_orphans_file(self):
        data = EV["runs"][0]["files"]["orphans"]
        self.check(
            "A4.readable", data["exists"] and data["error"] is None,
            "prompt rule 3: out/orphans.jsonl is one JSON line per orphan — %s"
            % data["error"])
        got = as_set(data["records"])
        missing, unexpected = diff_sets(got, as_set(EXPECTED_ORPHANS))
        self.check(
            "A4.set", got == as_set(EXPECTED_ORPHANS),
            "prompt rule 3 (+ rule 4: a malformed account is not an account, so "
            "its owner is not thereby active): out/orphans.jsonl must hold "
            "exactly the items with no active owner, as received — missing=%s "
            "unexpected=%s" % (missing, unexpected))
        dupes = duplicates(data["records"])
        self.check(
            "A4.no_duplicates", not dupes,
            "prompt rule 3: write each such item EXACTLY ONCE — duplicated "
            "lines: %s" % sorted(dupes))


class TestA05Errors(JudgeCase):
    """A5 — prompt rule 4: "Malformed records never stop the run and never enter
    any count… skipped and written, exactly once"."""

    def test_errors_file(self):
        data = EV["runs"][0]["files"]["errors"]
        self.check(
            "A5.readable", data["exists"] and data["error"] is None,
            "prompt rule 4: out/errors.jsonl is one JSON line per malformed "
            "record — %s" % data["error"])
        got = as_set(data["records"])
        missing, unexpected = diff_sets(got, as_set(EXPECTED_ERRORS))
        self.check(
            "A5.set", got == as_set(EXPECTED_ERRORS),
            "prompt rule 4: every malformed record, from either service, written "
            "as {\"service\": …, \"record\": …} — missing=%s unexpected=%s"
            % (missing, unexpected))
        dupes = duplicates(data["records"])
        self.check(
            "A5.no_duplicates", not dupes,
            "prompt rule 4: written EXACTLY ONCE — duplicated lines: %s"
            % sorted(dupes))


class TestA06NoRejectedWrite(JudgeCase):
    """A6 — prompt rule 5 as corrected: "never send a POST that the ledger
    *rejects* — that is, one it answers `400`, `409` or `422`. A `429` is not a
    rejection". Both invocations."""

    def test_no_rejected_write(self):
        made = writes(EV)
        self.check(
            "A6.writes_happened", len(made) > 0,
            "prompt rule 5: the run must POST the summaries the ledger is "
            "missing — no write request was made at all, so 'no rejected write' "
            "would pass vacuously")
        rejected = [e for e in made if e.get("status") in REJECT_STATUSES]
        self.check(
            "A6.none_rejected", not rejected,
            "prompt rule 5: a passing run makes NO rejected write (400/409/422; "
            "429 is the rate limit of rule 6, not a rejection) — rejected: %s"
            % [short(e) for e in rejected])


class TestA07Pagination(JudgeCase):
    """A7 — prompt rule 1: "Retrieve every page of accounts, items, and existing
    summaries by following `next_page` until it is `null`"."""

    def test_every_page_read(self):
        for (service, path), pages in sorted(EXPECTED_PAGES.items()):
            fetched = {page_of(e) for e in entries(EV, service, run=1)
                       if e.get("path") == path and e.get("method") == "GET"
                       and e.get("status") == 200}
            self.check(
                "A7.%s" % path.strip("/").replace("/", "_"),
                pages <= fetched,
                "prompt rule 1: every page of %s must be retrieved by following "
                "next_page until null — expected pages %s, successfully read %s"
                % (path, sorted(pages), sorted(p for p in fetched if p is not None)))


class TestA08RateLimits(JudgeCase):
    """A8 — prompt rule 6: "After a `429` from a service, send that service no
    further request until at least `Retry-After` seconds have elapsed, then
    retry. A `429` never counts as data and never abandons the work"."""

    def test_rate_limit_honored(self):
        served = 0
        too_soon = []
        not_retried = []
        for service in ("directory", "ledger"):
            log = entries(EV, service)
            for index, entry in enumerate(log):
                if entry.get("status") != 429:
                    continue
                served += 1
                if index + 1 < len(log):
                    gap = log[index + 1].get("ts", 0) - entry.get("ts", 0)
                    if gap < RETRY_AFTER_SECONDS - RETRY_TOLERANCE_SECONDS:
                        too_soon.append("%s %s: next request after %.3fs"
                                        % (service, short(entry), gap))
                signature = request_signature(entry)
                if not any(request_signature(later) == signature
                           for later in log[index + 1:]):
                    not_retried.append("%s %s" % (service, short(entry)))
        self.check(
            "A8.429_served", served > 0,
            "the fixture's rate-limit schedule serves 429s on request ordinals "
            "2 and 8 — none was observed, so rule 6 cannot have been exercised "
            "(vacuous assertion guard)")
        self.check(
            "A8.waited", not too_soon,
            "prompt rule 6: after a 429, send that service no further request "
            "until at least Retry-After (%.0fs, tolerance %.2fs) has elapsed — "
            "violations: %s" % (RETRY_AFTER_SECONDS, RETRY_TOLERANCE_SECONDS,
                                too_soon))
        self.check(
            "A8.retried", not not_retried,
            "prompt rule 6: then retry — a 429 never counts as data and never "
            "abandons the work; never retried: %s" % not_retried)


class TestA09Idempotent(JudgeCase):
    """A9 — prompt rule 7: "Running `sync` again with the services unchanged must
    issue no write requests and must leave the three output files with the same
    content"."""

    def test_second_run(self):
        run2 = EV["runs"][1]
        self.check(
            "A9.exit0", run2["rc"] == 0,
            "prompt: 'Exit code 0 on success' — the second `python3 -m orgsync "
            "sync` exited %s. stderr tail:\n%s" % (run2["rc"], tail(run2["stderr"])))
        second_writes = writes(EV, run=2)
        self.check(
            "A9.no_writes", not second_writes,
            "prompt rule 7: running sync again must issue NO write requests — "
            "run 2 made: %s" % [short(e) for e in second_writes])
        for name in ("summary", "orphans", "errors"):
            before = EV["runs"][0]["files"][name]
            after = run2["files"][name]
            same = (after["exists"] and after["error"] is None
                    and as_set(after["records"]) == as_set(before["records"])
                    and len(after["records"]) == len(before["records"]))
            self.check(
                "A9.file_%s" % name, same,
                "prompt rule 7: a second run must leave the three output files "
                "with the same content — out/%s.jsonl after run 1: %s; after run "
                "2: %s%s" % (name, describe(before["records"]),
                             describe(after["records"]),
                             "" if after["error"] is None else
                             " (error: %s)" % after["error"]))


class TestA10IdempotencyKeys(JudgeCase):
    """A10 — prompt rule 5: "POST it with header `Idempotency-Key: sync-<owner>`"."""

    def test_keys(self):
        posts = summary_posts(EV)
        wrong = []
        for entry in posts:
            body = entry.get("body")
            owner = body.get("owner") if isinstance(body, dict) else None
            expected = "sync-%s" % owner
            if entry.get("idempotency_key") != expected:
                wrong.append("%s key=%r body_owner=%r"
                             % (short(entry), entry.get("idempotency_key"), owner))
        self.check(
            "A10.format", not wrong,
            "prompt rule 5: POST with header Idempotency-Key: sync-<owner> — "
            "wrong or missing on: %s" % wrong)
        accepted = accepted_posts(EV)
        keys = [e.get("idempotency_key") for e in accepted]
        self.check(
            "A10.one_per_summary", len(keys) == len(set(keys)),
            "prompt rule 5: one Idempotency-Key per summary — accepted posts "
            "reused a key: %s" % keys)
        expected_keys = {"sync-%s" % s["owner"] for s in EXPECTED_SUMMARIES}
        self.check(
            "A10.covers_summaries", set(keys) == expected_keys,
            "prompt rules 2+5: the accepted summaries carry exactly the keys "
            "sync-<owner> for the active well-formed accounts — got %s, expected "
            "%s" % (sorted(set(keys)), sorted(expected_keys)))


class TestZZBudget(unittest.TestCase):
    """Vacuous-pass guard: the judge must have executed its own assertions.

    Runs last (alphabetical class ordering). If a test method died before its
    checks ran, the budget is short and the run FAILS rather than reporting a
    pass off a handful of checks.
    """

    def test_assertion_budget(self):
        self.assertGreaterEqual(
            CHECKS, EXPECTED_CHECKS,
            "judge executed only %d of %d checks — a partial run is a FAIL, "
            "never a pass" % (CHECKS, EXPECTED_CHECKS))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules["__main__"])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("[judge] checks executed: %d (budget %d)" % (CHECKS, EXPECTED_CHECKS))
    print(
        f"ACCEPTANCE_RESULT pass={result.wasSuccessful()} "
        f"failures={len(result.failures)} errors={len(result.errors)} "
        f"tests={result.testsRun}"
    )
    sys.exit(0 if result.wasSuccessful() else 1)
