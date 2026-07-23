#!/usr/bin/env python3
"""Independent acceptance suite for the cross-model TIER demo `cronspec` task.

This is the uniform, hidden judge applied identically to every leg (Claude Code
models and Codex models). It imports `cron.py` from a TARGET directory and tests
`next_fire(expr, after)` against a self-contained REFERENCE implementation of the
DEVIATED cron spec (see prompts/cronspec.md). The reference is embedded here so
the judge has no dependency on the code under test; the target is compared to it
across a broad deterministic case set plus the full error contract.

The spec has THREE original deviations (DOM∧DOW intersection; step anchored to
the range start; strict domain incl. dow 7 invalid) PLUS FOUR hardening rules
(wrap-around ranges; steps that count through a wrap; `L` = last day of month;
correct month-length/leap rollover). The reference has been hand-verified against
every worked example in the prompt. See control/check-cronspec-selftest.py, which
proves a spec-faithful impl PASSES, and that BOTH a vixie-faithful impl and a
"careful but incomplete" impl (original 3 deviations only) FAIL — the latter
specifically on the four hardening rules.

Usage:
    python3 acceptance_cronspec.py <target-dir>
    TARGET_DIR=<target-dir> python3 acceptance_cronspec.py

Exit code 0 iff all tests pass. Always prints a final machine-readable line
(identical shape to acceptance_test.py, so run.sh / score.py parse it the same):
    ACCEPTANCE_RESULT pass=<True|False> failures=<n> errors=<n> tests=<n>
"""

import calendar
import os
import sys
import unittest
from datetime import datetime, timedelta

# ─────────────────────────────────────────────────────────────────────────────
# Embedded REFERENCE implementation of the deviated spec (the oracle).
# Kept byte-for-byte in sync with control/cronspec_reference.py; the self-test
# proves they agree by running this judge against that file.
# ─────────────────────────────────────────────────────────────────────────────
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}
_DOW = {d: i for i, d in enumerate(
    ["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}


def _ref_last_day(dt):
    return calendar.monthrange(dt.year, dt.month)[1]


def _ref_value(tok, lo, hi, names):
    if tok in names:
        v = names[tok]
    elif tok.isdigit():
        v = int(tok)
    else:
        raise ValueError(f"unknown token: {tok!r}")
    if not (lo <= v <= hi):
        raise ValueError(f"value {tok!r} out of domain [{lo},{hi}]")
    return v


def _ref_emit(start, end, step, lo, hi, out):
    # Wrapped ordered sequence from start to end inclusive (wraps hi->lo when
    # start > end). Step anchored at index 0 (range start), counting through wrap.
    if start <= end:
        seq = list(range(start, end + 1))
    else:
        seq = list(range(start, hi + 1)) + list(range(lo, end + 1))
    for i in range(0, len(seq), step):
        out.add(seq[i])


def _ref_element(elem, lo, hi, names, out):
    if elem == "":
        raise ValueError("empty field element")
    had_step = "/" in elem
    step = 1
    base = elem
    if had_step:
        parts = elem.split("/")
        if len(parts) != 2 or parts[0] == "":
            raise ValueError(f"unknown token: {elem!r}")
        base, stepstr = parts
        if not stepstr.isdigit():
            raise ValueError(f"invalid step (not a positive integer): {elem!r}")
        step = int(stepstr)
        if step <= 0:
            raise ValueError(f"step <= 0: {elem!r}")
    if base == "*":
        _ref_emit(lo, hi, step, lo, hi, out)
    elif "-" in base:
        rparts = base.split("-")
        if len(rparts) != 2 or rparts[0] == "" or rparts[1] == "":
            raise ValueError(f"unknown token: {base!r}")
        start = _ref_value(rparts[0], lo, hi, names)
        end = _ref_value(rparts[1], lo, hi, names)
        _ref_emit(start, end, step, lo, hi, out)   # start > end wraps
    else:
        if had_step:
            raise ValueError(f"step requires '*' or a range: {elem!r}")
        out.add(_ref_value(base, lo, hi, names))


def _ref_field(field, lo, hi, names):
    if field == "":
        raise ValueError("empty field")
    if field == "*":
        return False, set(range(lo, hi + 1))
    out = set()
    for elem in field.split(","):
        _ref_element(elem, lo, hi, names, out)
    if not out:
        raise ValueError("empty field")
    return True, out


def _ref_cron_dow(dt):
    return (dt.weekday() + 1) % 7


def _ref_next_fire(expr, after):
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"expected exactly 5 fields, got {len(fields)}")
    _, minute_set = _ref_field(fields[0], 0, 59, {})
    _, hour_set = _ref_field(fields[1], 0, 23, {})
    dom_f = fields[2]
    if dom_f == "L":
        dom_r, dom_kind, dom_set = True, "L", None
    else:
        dom_r, dom_set = _ref_field(dom_f, 1, 31, {})
        dom_kind = "set"
    _, month_set = _ref_field(fields[3], 1, 12, _MONTHS)
    dow_r, dow_set = _ref_field(fields[4], 0, 6, _DOW)  # Deviation 3: 0-6 only.

    def dom_matches(dt):
        if dom_kind == "L":
            return dt.day == _ref_last_day(dt)
        return dt.day in dom_set

    def day_matches(dt):
        dom_ok = dom_matches(dt)
        dow_ok = _ref_cron_dow(dt) in dow_set
        if dom_r and dow_r:
            return dom_ok and dow_ok      # Deviation 1: intersection
        if dom_r:
            return dom_ok
        if dow_r:
            return dow_ok
        return True

    minutes = sorted(minute_set)
    hours = sorted(hour_set)
    t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=366 * 8)
    cur = t
    while cur <= limit:
        if cur.month in month_set and day_matches(cur):
            for hh in hours:
                for mm in minutes:
                    cand = cur.replace(hour=hh, minute=mm)
                    if cand >= cur:
                        return cand
        cur = (cur + timedelta(days=1)).replace(hour=0, minute=0)
    raise ValueError("no matching time within 8-year horizon")


# ─────────────────────────────────────────────────────────────────────────────
# Target resolution + import (mirrors acceptance_test.py).
# ─────────────────────────────────────────────────────────────────────────────
def _resolve_target():
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        target = sys.argv.pop(1)
    else:
        target = os.environ.get("TARGET_DIR", "")
    if not target:
        print("usage: acceptance_cronspec.py <target-dir>  (or TARGET_DIR env)")
        print("ACCEPTANCE_RESULT pass=False failures=0 errors=1 tests=0")
        sys.exit(2)
    target = os.path.abspath(target)
    if not os.path.isfile(os.path.join(target, "cron.py")):
        print(f"no cron.py in {target}")
        print("ACCEPTANCE_RESULT pass=False failures=0 errors=1 tests=0")
        sys.exit(1)
    return target


_TARGET = _resolve_target()
sys.path.insert(0, _TARGET)

try:
    import cron as _cron_mod
    from cron import next_fire
except Exception as exc:  # import failure = acceptance failure, not a crash
    print(f"import of cron from {_TARGET} failed: {exc!r}")
    print("ACCEPTANCE_RESULT pass=False failures=0 errors=1 tests=0")
    sys.exit(1)

# Verify-the-thing: assert we imported the TARGET's cron.py, not a stray module.
_loaded_from = os.path.dirname(os.path.abspath(_cron_mod.__file__))
if _loaded_from != _TARGET:
    print(f"loaded cron from {_loaded_from}, expected {_TARGET}")
    print("ACCEPTANCE_RESULT pass=False failures=0 errors=1 tests=0")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic case generation from the reference oracle.
# ─────────────────────────────────────────────────────────────────────────────
_ANCHORS = [
    datetime(2026, 1, 1, 0, 0),
    datetime(2026, 1, 1, 0, 0, 30),        # nonzero seconds -> next minute
    datetime(2026, 3, 1, 12, 30),
    datetime(2026, 6, 15, 8, 45),
    datetime(2026, 7, 22, 9, 20),
    datetime(2026, 11, 30, 23, 59),        # year rollover
    datetime(2027, 2, 28, 6, 5),
    datetime(2027, 4, 30, 0, 0),           # month-length boundary
]

# Every expr here is satisfiable within the horizon and exercises a feature.
# The set is weighted toward the four hardening rules (wrap / wrap-step / L /
# rollover) so an impl that misses them fails MANY assertions, not just a few.
_EXPRS = [
    # ── basics ──────────────────────────────────────────────────────────────
    "* * * * *", "0 * * * *", "*/15 * * * *", "0 0 * * *", "30 9 * * *",
    "15,45 8-10 * * *", "0 0 1 * *", "0 0 * 1,7 *",
    # ── month/dow names ─────────────────────────────────────────────────────
    "0 12 1 jan,jul *", "0 0 1 mar *", "30 9 * * mon", "0 0 * * mon-fri",
    "0 0 * * sat,sun", "0 0 * * 0", "0 0 * * 6",
    # ── Deviation 2: forward step anchored to range start ───────────────────
    "5-30/10 12 * * *", "10-50/15 0 * * *", "3-59/7 * * * *", "0-30/10 1 * * *",
    "*/20 */6 * * *", "7-47/8 * * * *",
    # ── Deviation 1: intersection (dom AND dow both restricted) ─────────────
    "0 0 13 * fri", "0 0 1 * mon", "0 0 15 * wed", "0 0 1-7 * mon",
    # ── Rule 4: wrap-around ranges ──────────────────────────────────────────
    "50-10 * * * *", "45-15 * * * *", "0 22-3 * * *", "0 0 * nov-feb *",
    "0 0 * dec-jan *", "0 0 * * fri-mon", "0 0 * * sat-sun", "0 0 * * 6-1",
    "30 20-4 * * *",
    # ── Rule 5: steps counting through a wrap ───────────────────────────────
    "50-10/5 * * * *", "55-25/10 * * * *", "0 22-6/2 * * *",
    "0 0 * nov-mar/2 *", "0 0 * * fri-tue/2", "0 0 * * 5-2/2",
    # ── Rule 6: L = last day of month (+ intersection with dow) ──────────────
    "0 0 L * *", "30 8 L * *", "0 0 L 2 *", "0 0 L 1,3,5,7,8,10,12 *",
    "0 0 L * fri", "0 0 L * mon", "0 0 L 2 sat",
    # ── Rule 7: month-length / leap rollover ────────────────────────────────
    "0 0 31 * *", "0 0 30 * *", "0 0 29 * *", "0 0 29 feb *", "0 0 31 1,3,5 *",
    "0 0 30 2,4,6 *", "0 0 31 * sun",
    # ── combined ranges + lists + steps ─────────────────────────────────────
    "0,30 9-17 * * mon-fri", "*/30 0 1 */3 *", "0 0 10-20/5 6 *",
    "5 4 * jan-mar *",
]


def _gen_positive_cases():
    """(expr, after, expected) triples produced by the reference oracle."""
    cases = []
    for expr in _EXPRS:
        for after in _ANCHORS:
            try:
                expected = _ref_next_fire(expr, after)
            except ValueError:
                continue  # skip any anchor/expr the oracle can't satisfy
            cases.append((expr, after, expected))
    return cases


_POSITIVE_CASES = _gen_positive_cases()

# Worked examples from the prompt, hard-coded (independent of the generator so a
# generator bug can't silently drop them).
_WORKED = [
    # original deviations
    ("*/15 * * * *",     datetime(2026, 1, 1, 0, 0),   datetime(2026, 1, 1, 0, 15)),
    ("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0),   datetime(2026, 6, 1, 12, 5)),
    ("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5),  datetime(2026, 6, 1, 12, 15)),
    ("0 0 13 * fri",     datetime(2026, 1, 1, 0, 0),   datetime(2026, 2, 13, 0, 0)),
    ("0 0 1 * mon",      datetime(2026, 6, 2, 0, 0),   datetime(2027, 2, 1, 0, 0)),
    ("30 9 * * mon",     datetime(2026, 7, 22, 0, 0),  datetime(2026, 7, 27, 9, 30)),
    ("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0),   datetime(2026, 7, 1, 12, 0)),
    ("0 0 * * 0",        datetime(2026, 7, 22, 0, 0),  datetime(2026, 7, 26, 0, 0)),
    # Rule 4: wrap-around ranges
    ("50-10 * * * *",    datetime(2026, 1, 1, 0, 30),  datetime(2026, 1, 1, 0, 50)),
    ("50-10 * * * *",    datetime(2026, 1, 1, 0, 55),  datetime(2026, 1, 1, 0, 56)),
    ("0 0 1 nov-feb *",  datetime(2026, 3, 1, 0, 0),   datetime(2026, 11, 1, 0, 0)),
    ("0 0 * * fri-mon",  datetime(2026, 7, 21, 0, 0),  datetime(2026, 7, 24, 0, 0)),
    # Rule 5: steps through a wrap
    ("50-10/5 0 1 1 *",  datetime(2026, 1, 1, 0, 0),   datetime(2026, 1, 1, 0, 5)),
    ("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0), datetime(2026, 7, 24, 0, 0)),  # {fri,sun,tue}: Tue21->Fri24
    # Rule 6: L
    ("0 0 L * *",        datetime(2026, 1, 15, 0, 0),  datetime(2026, 1, 31, 0, 0)),
    ("0 0 L * *",        datetime(2026, 2, 1, 0, 0),   datetime(2026, 2, 28, 0, 0)),
    ("0 0 L 2 *",        datetime(2027, 3, 1, 0, 0),   datetime(2028, 2, 29, 0, 0)),
    ("0 0 L * fri",      datetime(2026, 1, 1, 0, 0),   datetime(2026, 7, 31, 0, 0)),
    # Rule 7: rollover
    ("0 0 31 * *",       datetime(2026, 4, 1, 0, 0),   datetime(2026, 5, 31, 0, 0)),
    ("0 0 29 feb *",     datetime(2027, 1, 1, 0, 0),   datetime(2028, 2, 29, 0, 0)),
    ("0 0 30 * *",       datetime(2026, 2, 1, 0, 0),   datetime(2026, 3, 30, 0, 0)),
]

# Full error contract: each must raise ValueError.
# NOTE: `30-5`, `fri-mon`, `10-1` are NO LONGER errors (they are wrap-around
# ranges now) and are deliberately absent.
_ERRORS = [
    "", "   ", "\t\n",
    "* * * *", "* * * * * *", "* * *",
    "60 * * * *", "* 24 * * *", "* * 0 * *", "* * 32 * *",
    "* * * 0 *", "* * * 13 *",
    "* * * * 7", "* * * * -1", "* * * * 8",
    "* * * * SUN", "* * * JAN *", "* * * * Mon", "* * * * FRI",
    "*/0 * * * *", "*/-1 * * * *", "1-5/0 * * * *", "*/x * * * *",
    "5/10 * * * *", "1a * * * *", "abc * * * *", "1x * * * *",
    "1,,3 * * * *", "1, * * * *", ",1 * * * *",
    "* * * * mon-", "* * * * -fri", "1- * * * *", "-5 * * * *",
    "* * * * 0-", "*/ * * * *", "1// * * * *",
    # L is DOM-only and standalone-only:
    "0 0 * * L", "0 L * * *", "L * * * *", "0 0 * L *",
    "0 0 L,15 * *", "0 0 L-5 * *", "0 0 l * *",
    # domain still enforced inside wrap endpoints:
    "70-10 * * * *", "* * * * fri-xyz",
]


class TestWorkedExamples(unittest.TestCase):
    """Every worked example literally stated in prompts/cronspec.md."""

    def test_worked(self):
        for expr, after, want in _WORKED:
            with self.subTest(expr=expr, after=after):
                self.assertEqual(next_fire(expr, after), want)


class TestOracleAgreement(unittest.TestCase):
    """Target must agree with the reference oracle across the generated set."""

    def test_generated(self):
        self.assertGreaterEqual(len(_POSITIVE_CASES), 50,
                                "case generator produced too few cases")
        for expr, after, want in _POSITIVE_CASES:
            with self.subTest(expr=expr, after=after):
                self.assertEqual(next_fire(expr, after), want)


class TestSequences(unittest.TestCase):
    """Chained next_fire must reproduce the oracle's ordered fire sequence."""

    def test_sequences(self):
        seq_exprs = [
            "*/15 * * * *", "5-30/10 12 * * *", "0 0 13 * fri",
            "0 0 * * mon-fri", "50-10/5 * * * *", "0 0 L * *",
            "0 0 31 * *", "0 0 * * fri-tue/2",
        ]
        for expr in seq_exprs:
            after = datetime(2026, 1, 1, 0, 0)
            for _ in range(8):
                want = _ref_next_fire(expr, after)
                with self.subTest(expr=expr, after=after):
                    got = next_fire(expr, after)
                    self.assertEqual(got, want)
                after = want


class TestStrictlyAfter(unittest.TestCase):
    """Result is strictly after `after`, minute-aligned, matches the oracle."""

    def test_strictly_after(self):
        for expr in ["* * * * *", "*/15 * * * *", "0 0 * * *", "50-10 * * * *"]:
            for after in _ANCHORS:
                with self.subTest(expr=expr, after=after):
                    got = next_fire(expr, after)
                    self.assertGreater(got, after)
                    self.assertEqual((got.second, got.microsecond), (0, 0))
                    self.assertEqual(got, _ref_next_fire(expr, after))


class TestErrorContract(unittest.TestCase):
    """Every invalid expression must raise ValueError (not another exception)."""

    def test_errors(self):
        after = datetime(2026, 1, 1, 0, 0)
        for expr in _ERRORS:
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, after)


class TestDeviationsBite(unittest.TestCase):
    """Direct assertions that each ORIGINAL deviation is implemented."""

    def test_dev1_intersection_not_union(self):
        got = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(got, datetime(2026, 2, 13, 0, 0))
        self.assertEqual(got.day, 13)
        self.assertEqual((got.weekday() + 1) % 7, _DOW["fri"])

    def test_dev2_step_anchored_to_range_start(self):
        got = next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0))
        self.assertEqual(got.minute, 5)
        seq = []
        after = datetime(2026, 6, 1, 0, 0)
        for _ in range(3):
            after = next_fire("5-30/10 12 1 6 *", after)
            seq.append(after.minute)
        self.assertEqual(seq, [5, 15, 25])

    def test_dev3_dow_seven_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 * * 7", datetime(2026, 1, 1, 0, 0))


class TestHardeningRulesBite(unittest.TestCase):
    """Direct assertions that each of the FOUR hardening rules is implemented."""

    def test_rule4_wrap_minute_range(self):
        # 50-10 wraps -> {50..59, 0..10}; strictly after 00:30 -> 00:50.
        self.assertEqual(next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30)),
                         datetime(2026, 1, 1, 0, 50))
        # after 00:59 wraps into the low half -> 01:00 (minute 0 matches).
        self.assertEqual(next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 59)),
                         datetime(2026, 1, 1, 1, 0))

    def test_rule4_wrap_month_range(self):
        # nov-feb -> {nov,dec,jan,feb}; after Mar 1 the next Nov 1.
        self.assertEqual(next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0)),
                         datetime(2026, 11, 1, 0, 0))

    def test_rule5_step_through_wrap(self):
        # 50-10/5 -> {50,55,0,5,10}; after 00:00 -> 00:05, next -> 00:10, next -> 00:50.
        seq = []
        after = datetime(2026, 1, 1, 0, 0)
        for _ in range(3):
            after = next_fire("50-10/5 0 1 1 *", after)
            seq.append(after.minute)
        self.assertEqual(seq, [5, 10, 50])
        # dow fri-tue/2 -> {fri,sun,tue}; from Tue 2026-07-21 -> Fri 2026-07-24.
        self.assertEqual(next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0)),
                         datetime(2026, 7, 24, 0, 0))

    def test_rule6_last_day_of_month(self):
        self.assertEqual(next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0)),
                         datetime(2026, 2, 28, 0, 0))   # 2026 Feb non-leap -> 28
        self.assertEqual(next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0)),
                         datetime(2028, 2, 29, 0, 0))   # leap Feb -> 29
        # L intersects with dow: last day that is a Friday.
        got = next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual((got.weekday() + 1) % 7, _DOW["fri"])
        self.assertEqual(got.day, calendar.monthrange(got.year, got.month)[1])

    def test_rule7_month_length_rollover(self):
        # 31 must SKIP 30-day months and February (no clamping).
        self.assertEqual(next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0)),
                         datetime(2026, 5, 31, 0, 0))
        got = next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0))
        self.assertEqual(got.day, 31)
        # 29 feb only fires on a leap-year Feb 29.
        self.assertEqual(next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0)),
                         datetime(2028, 2, 29, 0, 0))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules["__main__"])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    print(
        f"ACCEPTANCE_RESULT pass={result.wasSuccessful()} "
        f"failures={len(result.failures)} errors={len(result.errors)} "
        f"tests={result.testsRun}"
    )
    sys.exit(0 if result.wasSuccessful() else 1)
