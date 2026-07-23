#!/usr/bin/env python3
"""Control-test `acceptance_orgsync.py` in BOTH directions.

SEALED (design/). PROTOCOL §4.5 requires a judge to be shown passing the
reference AND failing each wrong implementation on the assertions it was built
to trip, before the judge is used on a single model run. This script is that
demonstration, and it is re-runnable.

For each case it builds a throwaway repo from `tasks/orgsync-skeleton`,
installs one implementation as `orgsync/sync.py` (plus `test_orgsync.py` where
the case ships one), runs the judge against it, and prints the judge's
`ACCEPTANCE_RESULT` line together with the individual checks that failed.

Cases:
    reference       the sealed reference implementation      → must PASS
    w1              naive read                               → must FAIL
    w2              careful but non-idempotent               → must FAIL (A9 only)
    w3              confident finisher (own-tests PASS)      → must FAIL
    empty           an empty directory                       → must FAIL (error)
    stub            the untouched skeleton (NotImplementedError) → must FAIL
    writes-nothing  runs, exits 0, produces no output        → must FAIL

Usage:
    python3 design/orgsync/controls/control_check.py [case ...] [--keep]

Exit code 0 iff every case reached its expected verdict.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BENCH_DIR = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SKELETON = os.path.join(BENCH_DIR, "tasks", "orgsync-skeleton")
JUDGE = os.path.join(BENCH_DIR, "acceptance_orgsync.py")
REFERENCE = os.path.join(HERE, "..", "reference", "orgsync_reference.py")

#: Adapter that lets the standalone reference serve as `orgsync/sync.py`. It
#: changes no behaviour: the reference takes a config PATH, so the adapter
#: re-materializes one from the Config the CLI already loaded.
REFERENCE_ADAPTER = '''\
"""The sealed reference implementation, wired into the package CLI."""

import json
import os
import tempfile

from .config import Config
from ._reference import run_sync as _reference_run_sync

DEFAULT_OUT_DIR = "out"


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    try:
        json.dump({"directory_url": config.directory_url,
                   "ledger_url": config.ledger_url}, handle)
        handle.close()
        return _reference_run_sync(handle.name, out_dir)
    finally:
        os.unlink(handle.name)
'''

CASES = {
    "reference":      {"expect": "PASS", "impl": None},
    "w1":             {"expect": "FAIL", "impl": "w1_naive_read.py"},
    "w2":             {"expect": "FAIL", "impl": "w2_non_idempotent.py"},
    "w3":             {"expect": "FAIL", "impl": "w3_confident_finisher.py",
                       "own_tests": "w3_test_orgsync.py"},
    "empty":          {"expect": "FAIL", "impl": "EMPTY"},
    "stub":           {"expect": "FAIL", "impl": "SKELETON"},
    "writes-nothing": {"expect": "FAIL", "impl": "g_writes_nothing.py"},
}

FAIL_RE = re.compile(r"^(FAIL|ERROR): (\S+) \(.*?\)(?: \[(.*)\])?", re.M)
CHECK_RE = re.compile(r"\[(A\d+\.[a-z0-9_]+)\]")


def build_repo(case, root):
    """Lay down a repo for `case` under `root`; return its path."""
    repo = os.path.join(root, case)
    spec = CASES[case]
    if spec["impl"] == "EMPTY":
        os.makedirs(repo)
        return repo
    shutil.copytree(SKELETON, repo)
    if spec["impl"] == "SKELETON":
        return repo                     # untouched: sync raises NotImplementedError
    if case == "reference":
        shutil.copyfile(REFERENCE, os.path.join(repo, "orgsync", "_reference.py"))
        with open(os.path.join(repo, "orgsync", "sync.py"), "w",
                  encoding="utf-8") as handle:
            handle.write(REFERENCE_ADAPTER)
    else:
        shutil.copyfile(os.path.join(HERE, spec["impl"]),
                        os.path.join(repo, "orgsync", "sync.py"))
    if spec.get("own_tests"):
        shutil.copyfile(os.path.join(HERE, spec["own_tests"]),
                        os.path.join(repo, "test_orgsync.py"))
    return repo


def run_own_tests(repo):
    """Run the submission's own suite, as run.sh does. Returns PASS/FAIL/n-a."""
    if not os.path.isfile(os.path.join(repo, "test_orgsync.py")):
        return "n/a", ""
    proc = subprocess.run([sys.executable, "-m", "unittest", "test_orgsync"],
                          cwd=repo, capture_output=True, text=True)
    return ("PASS" if proc.returncode == 0 else "FAIL"), proc.stderr


def failed_checks(output):
    """The `[Ax.name]` labels of every failed check, in report order."""
    labels = []
    for line in output.splitlines():
        for label in CHECK_RE.findall(line):
            if label not in labels:
                labels.append(label)
    return labels


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", nargs="*", default=[], help="cases to run")
    parser.add_argument("--keep", action="store_true", help="keep the repos")
    args = parser.parse_args(argv)
    cases = args.cases or list(CASES)
    for case in cases:
        if case not in CASES:
            parser.error("unknown case %r (known: %s)" % (case, ", ".join(CASES)))

    root = tempfile.mkdtemp(prefix="orgsync-controls-")
    summary = []
    try:
        for case in cases:
            repo = build_repo(case, root)
            own, _ = run_own_tests(repo)
            proc = subprocess.run([sys.executable, JUDGE, repo],
                                  capture_output=True, text=True)
            output = proc.stdout + proc.stderr
            verdict = "PASS" if proc.returncode == 0 else "FAIL"
            line = ""
            for candidate in output.splitlines():
                if candidate.startswith("ACCEPTANCE_RESULT"):
                    line = candidate
            checks = failed_checks(output)
            expected = CASES[case]["expect"]
            ok = verdict == expected
            summary.append({"case": case, "verdict": verdict, "expected": expected,
                            "ok": ok, "own_tests": own, "result_line": line,
                            "failed_checks": checks})
            print("=" * 78)
            print("CASE %s — expected %s, got %s%s"
                  % (case, expected, verdict, "" if ok else "   *** MISMATCH ***"))
            print("own tests: %s" % own)
            print(output.rstrip())
            print("failed checks: %s" % (checks or "none"))
    finally:
        if args.keep:
            print("\nrepos kept at %s" % root)
        else:
            shutil.rmtree(root, ignore_errors=True)

    print("=" * 78)
    print("SUMMARY")
    for row in summary:
        print("  %-15s expected=%-4s got=%-4s own_tests=%-4s %s  %s"
              % (row["case"], row["expected"], row["verdict"], row["own_tests"],
                 "OK " if row["ok"] else "BAD", row["result_line"]))
    print(json.dumps({r["case"]: r["failed_checks"] for r in summary}, indent=2))
    return 0 if all(r["ok"] for r in summary) else 1


if __name__ == "__main__":
    sys.exit(main())
