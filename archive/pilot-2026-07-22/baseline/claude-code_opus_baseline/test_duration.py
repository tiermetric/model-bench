"""Standard-library unittest suite for the duration utility."""

import unittest

from duration import parse_duration, format_duration


class ParseWorkedExamples(unittest.TestCase):
    def test_worked_examples(self):
        cases = {
            "1h30m": 5400,
            "2d": 172800,
            "1d2h3m4s": 93784,
            "90s": 90,
            "1h90m": 9000,
            "01h": 3600,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), expected)


class ParseUnits(unittest.TestCase):
    def test_single_units(self):
        self.assertEqual(parse_duration("1d"), 86400)
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertEqual(parse_duration("1m"), 60)
        self.assertEqual(parse_duration("1s"), 1)

    def test_zero_components(self):
        self.assertEqual(parse_duration("0d"), 0)
        self.assertEqual(parse_duration("0s"), 0)
        self.assertEqual(parse_duration("0h0m0s"), 0)

    def test_leading_zeros(self):
        self.assertEqual(parse_duration("01h"), 3600)
        self.assertEqual(parse_duration("007s"), 7)
        self.assertEqual(parse_duration("00m"), 0)

    def test_unbounded_components(self):
        # Components have no upper bound; non-canonical inputs are valid.
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration("100h"), 360000)
        self.assertEqual(parse_duration("999999s"), 999999)

    def test_all_units_together(self):
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)

    def test_surrounding_whitespace_ignored(self):
        self.assertEqual(parse_duration("  1h30m  "), 5400)
        self.assertEqual(parse_duration("\t2d\n"), 172800)


class ParseErrors(unittest.TestCase):
    def test_empty(self):
        with self.assertRaises(ValueError):
            parse_duration("")

    def test_whitespace_only(self):
        with self.assertRaises(ValueError):
            parse_duration("   ")

    def test_unknown_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("1x")

    def test_uppercase_unit(self):
        for text in ("1H", "1D", "1M", "1S"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_bare_number(self):
        with self.assertRaises(ValueError):
            parse_duration("5")

    def test_unit_without_number(self):
        with self.assertRaises(ValueError):
            parse_duration("h")
        with self.assertRaises(ValueError):
            parse_duration("1hm")

    def test_out_of_order(self):
        with self.assertRaises(ValueError):
            parse_duration("30m1h")
        with self.assertRaises(ValueError):
            parse_duration("1s1d")
        with self.assertRaises(ValueError):
            parse_duration("1m1h")

    def test_duplicate_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("1h1h")
        with self.assertRaises(ValueError):
            parse_duration("1d2d")

    def test_internal_whitespace(self):
        for text in ("1h 30m", "1 h", "1h30 m", "1h\t30m"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_negative_number(self):
        with self.assertRaises(ValueError):
            parse_duration("-1h")

    def test_non_integer(self):
        with self.assertRaises(ValueError):
            parse_duration("1.5h")

    def test_plus_sign(self):
        with self.assertRaises(ValueError):
            parse_duration("+1h")


class FormatWorkedExamples(unittest.TestCase):
    def test_worked_examples(self):
        cases = {
            5400: "1h30m",
            93784: "1d2h3m4s",
            86400: "1d",
            0: "0s",
        }
        for seconds, expected in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(format_duration(seconds), expected)


class FormatBehaviour(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(format_duration(0), "0s")

    def test_largest_units_used(self):
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(1), "1s")

    def test_zero_components_omitted(self):
        self.assertEqual(format_duration(90), "1m30s")
        self.assertEqual(format_duration(86461), "1d1m1s")  # no hours
        self.assertEqual(format_duration(90061), "1d1h1m1s")

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            format_duration(-1)


class RoundTrip(unittest.TestCase):
    def test_roundtrip_range(self):
        # parse(format(n)) == n for every n >= 0 across a broad range.
        for n in range(0, 200000, 7):
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_roundtrip_boundaries(self):
        for n in (0, 1, 59, 60, 61, 3599, 3600, 3601, 86399, 86400, 86401,
                  93784, 172800, 1000000):
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)


if __name__ == "__main__":
    unittest.main()
