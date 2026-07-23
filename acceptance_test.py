#!/usr/bin/env python3
"""Independent acceptance suite for the cross-model TIER demo (model-bench).

This is the uniform, hidden judge applied identically to every leg (Claude
Code models and Codex models). It imports `duration.py` from a TARGET
directory and tests ONLY behaviors literally stated in model-bench/prompt.md:
worked examples, enumerated error cases, rule-stated behaviors (unbounded
components, leading zeros, uppercase rejection, whitespace rules, descending
order / uniqueness, largest-unit formatting), and the round-trip property.

Usage:
    python3 acceptance_test.py <target-dir>
    TARGET_DIR=<target-dir> python3 acceptance_test.py

Exit code 0 iff all tests pass. Always prints a final machine-readable line:
    ACCEPTANCE_RESULT pass=<True|False> failures=<n> errors=<n> tests=<n>
"""

import os
import sys
import unittest


def _resolve_target() -> str:
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        target = sys.argv.pop(1)
    else:
        target = os.environ.get("TARGET_DIR", "")
    if not target:
        print("usage: acceptance_test.py <target-dir>  (or TARGET_DIR env)")
        print("ACCEPTANCE_RESULT pass=False failures=0 errors=1 tests=0")
        sys.exit(2)
    target = os.path.abspath(target)
    if not os.path.isfile(os.path.join(target, "duration.py")):
        print(f"no duration.py in {target}")
        print("ACCEPTANCE_RESULT pass=False failures=0 errors=1 tests=0")
        sys.exit(1)
    return target


_TARGET = _resolve_target()
sys.path.insert(0, _TARGET)

try:
    import duration as _duration_mod
    from duration import format_duration, parse_duration
except Exception as exc:  # import failure = acceptance failure, not a crash
    print(f"import of duration from {_TARGET} failed: {exc!r}")
    print("ACCEPTANCE_RESULT pass=False failures=0 errors=1 tests=0")
    sys.exit(1)

# Verify-the-thing: assert we imported the TARGET's duration.py, not a stray
# module from elsewhere on sys.path.
_loaded_from = os.path.dirname(os.path.abspath(_duration_mod.__file__))
if _loaded_from != _TARGET:
    print(f"loaded duration from {_loaded_from}, expected {_TARGET}")
    print("ACCEPTANCE_RESULT pass=False failures=0 errors=1 tests=0")
    sys.exit(1)


class TestParseWorkedExamples(unittest.TestCase):
    """Every positive worked example literally stated in prompt.md."""

    def test_worked_examples(self):
        cases = {
            "1h30m": 5400,
            "2d": 172800,
            "1d2h3m4s": 93784,
            "90s": 90,
            "1h90m": 9000,   # unbounded component, stated VALID
            "01h": 3600,     # leading zeros allowed, stated
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), want)


class TestParseWorkedErrors(unittest.TestCase):
    """Every error worked example literally stated in prompt.md."""

    def test_worked_error_examples(self):
        for text in ["", "1x", "1H", "30m1h", "1h1h", "5"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)


class TestParseRuleStatedErrors(unittest.TestCase):
    """Error rules enumerated in the prompt beyond the worked examples."""

    def test_whitespace_only(self):
        with self.assertRaises(ValueError):
            parse_duration("   ")

    def test_whitespace_inside(self):
        for text in ["1h 30m", "1 h", "1h\t30m"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_unit_with_no_number(self):
        for text in ["h", "1hm", "d"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_negative_number(self):
        with self.assertRaises(ValueError):
            parse_duration("-5s")

    def test_non_integer_number(self):
        with self.assertRaises(ValueError):
            parse_duration("1.5h")

    def test_uppercase_units_rejected(self):
        for text in ["1D", "2M", "3S", "1h30M"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_units_out_of_order(self):
        for text in ["1s1m", "1m1d", "4s1d"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_duplicate_unit(self):
        for text in ["2d2d", "1m1m", "5s5s"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)


class TestParseRuleStatedBehavior(unittest.TestCase):
    """Positive behaviors stated as rules (not worked examples)."""

    def test_outer_whitespace_ignored(self):
        # "Leading/trailing whitespace on the whole string is ignored"
        self.assertEqual(parse_duration(" 1h30m "), 5400)
        self.assertEqual(parse_duration("\t2d\n"), 172800)

    def test_descending_order_allows_skipped_units(self):
        # "strictly descending order" — adjacency is not required
        self.assertEqual(parse_duration("1d4s"), 86404)
        self.assertEqual(parse_duration("2d30m"), 174600)
        self.assertEqual(parse_duration("1h5s"), 3605)

    def test_unbounded_components(self):
        # "NO upper bound per component"
        self.assertEqual(parse_duration("999999s"), 999999)
        self.assertEqual(parse_duration("48h"), 172800)
        self.assertEqual(parse_duration("100000m"), 6000000)

    def test_leading_zeros(self):
        self.assertEqual(parse_duration("007s"), 7)
        self.assertEqual(parse_duration("0001d"), 86400)

    def test_zero_components(self):
        # "Numbers are non-negative integers" — zero is a non-negative integer
        self.assertEqual(parse_duration("0s"), 0)
        self.assertEqual(parse_duration("0h"), 0)


class TestFormatWorkedExamples(unittest.TestCase):
    def test_worked_examples(self):
        cases = {
            5400: "1h30m",
            93784: "1d2h3m4s",
            86400: "1d",       # "never 24h"
            0: "0s",
            9000: "2h30m",     # stated: parse accepts 1h90m, format renders 2h30m
        }
        for seconds, want in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(format_duration(seconds), want)

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            format_duration(-1)


class TestFormatRuleStatedBehavior(unittest.TestCase):
    def test_largest_units_and_zero_omission(self):
        # "Always use the largest fitting units", "zero value omitted"
        cases = {
            1: "1s",
            59: "59s",
            60: "1m",
            90: "1m30s",
            3600: "1h",
            3601: "1h1s",     # zero-minute component omitted
            86460: "1d1m",    # zero-hour component omitted
            90061: "1d1h1m1s",
        }
        for seconds, want in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(format_duration(seconds), want)


class TestRoundTrip(unittest.TestCase):
    """parse_duration(format_duration(n)) == n for every integer n >= 0."""

    def test_round_trip_many_values(self):
        values = list(range(0, 200000, 977))
        values += [0, 1, 59, 60, 61, 3599, 3600, 3601,
                   86399, 86400, 86401, 90061, 172800, 10**9]
        for n in values:
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules["__main__"])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    print(
        f"ACCEPTANCE_RESULT pass={result.wasSuccessful()} "
        f"failures={len(result.failures)} errors={len(result.errors)} "
        f"tests={result.testsRun}"
    )
    sys.exit(0 if result.wasSuccessful() else 1)
