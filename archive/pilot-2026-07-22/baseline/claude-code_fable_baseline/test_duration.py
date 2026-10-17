"""Standard-library unittest suite for duration.py."""

import unittest

from duration import format_duration, parse_duration


class TestParseDuration(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2d"), 172800)
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration("01h"), 3600)

    def test_single_units(self):
        self.assertEqual(parse_duration("1d"), 86400)
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertEqual(parse_duration("1m"), 60)
        self.assertEqual(parse_duration("1s"), 1)

    def test_zero_components_allowed(self):
        self.assertEqual(parse_duration("0s"), 0)
        self.assertEqual(parse_duration("0d"), 0)
        self.assertEqual(parse_duration("0h0s"), 0)
        self.assertEqual(parse_duration("1h0m"), 3600)

    def test_all_four_units(self):
        self.assertEqual(parse_duration("2d4h"), 187200)
        self.assertEqual(parse_duration("1d1h1m1s"), 90061)

    def test_unbounded_component_magnitudes(self):
        self.assertEqual(parse_duration("90m"), 5400)
        self.assertEqual(parse_duration("25h"), 90000)
        self.assertEqual(parse_duration("1000000d"), 1000000 * 86400)
        self.assertEqual(parse_duration("1d25h"), 86400 + 90000)

    def test_leading_zeros(self):
        self.assertEqual(parse_duration("007s"), 7)
        self.assertEqual(parse_duration("00h05m"), 300)
        self.assertEqual(parse_duration("000s"), 0)

    def test_outer_whitespace_ignored(self):
        self.assertEqual(parse_duration("  1h30m  "), 5400)
        self.assertEqual(parse_duration("\t2d\n"), 172800)

    def test_empty_and_whitespace_only_raise(self):
        for text in ("", " ", "   ", "\t", "\n", " \t\n "):
            with self.assertRaises(ValueError, msg=repr(text)):
                parse_duration(text)

    def test_unknown_unit_raises(self):
        for text in ("1x", "5w", "3y", "10q"):
            with self.assertRaises(ValueError, msg=repr(text)):
                parse_duration(text)

    def test_uppercase_unit_raises(self):
        for text in ("1H", "1D", "1M", "1S", "1h30M"):
            with self.assertRaises(ValueError, msg=repr(text)):
                parse_duration(text)

    def test_bare_number_raises(self):
        for text in ("5", "90", "1h30"):
            with self.assertRaises(ValueError, msg=repr(text)):
                parse_duration(text)

    def test_unit_without_number_raises(self):
        for text in ("h", "s", "h30m", "1hm", "d2h"):
            with self.assertRaises(ValueError, msg=repr(text)):
                parse_duration(text)

    def test_non_integer_or_negative_number_raises(self):
        for text in ("1.5h", "-1h", "+1h", "1h-30m", "1e2s"):
            with self.assertRaises(ValueError, msg=repr(text)):
                parse_duration(text)

    def test_internal_whitespace_raises(self):
        for text in ("1h 30m", "1 h", " 1h 30m ", "1h\t30m", "1h\n30m"):
            with self.assertRaises(ValueError, msg=repr(text)):
                parse_duration(text)

    def test_units_out_of_order_raise(self):
        for text in ("30m1h", "1s1m", "1h2d", "1s1d", "1m2h3d"):
            with self.assertRaises(ValueError, msg=repr(text)):
                parse_duration(text)

    def test_duplicate_unit_raises(self):
        for text in ("1h1h", "1d1d", "2m3m", "1d1h1h", "1s0s"):
            with self.assertRaises(ValueError, msg=repr(text)):
                parse_duration(text)


class TestFormatDuration(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(93784), "1d2h3m4s")
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(0), "0s")

    def test_single_units(self):
        self.assertEqual(format_duration(1), "1s")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(3600), "1h")

    def test_zero_components_omitted(self):
        self.assertEqual(format_duration(3601), "1h1s")
        self.assertEqual(format_duration(86460), "1d1m")
        self.assertEqual(format_duration(86401), "1d1s")
        self.assertEqual(format_duration(61), "1m1s")

    def test_largest_fitting_units(self):
        self.assertEqual(format_duration(172800), "2d")
        self.assertEqual(format_duration(90000), "1d1h")
        self.assertEqual(format_duration(7200), "2h")
        self.assertEqual(format_duration(59), "59s")
        self.assertEqual(format_duration(3599), "59m59s")

    def test_large_values(self):
        self.assertEqual(format_duration(1000000 * 86400), "1000000d")

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            format_duration(-1)
        with self.assertRaises(ValueError):
            format_duration(-86400)


class TestRoundTrip(unittest.TestCase):
    def test_round_trip_dense_range(self):
        for n in range(0, 200000):
            self.assertEqual(parse_duration(format_duration(n)), n)

    def test_round_trip_boundaries_and_large(self):
        values = [
            0, 1, 59, 60, 61, 3599, 3600, 3601, 5400,
            86399, 86400, 86401, 90000, 93784, 172800,
            10**6, 10**9, 10**12, 86400 * 10**6 + 3661,
        ]
        for n in values:
            self.assertEqual(parse_duration(format_duration(n)), n)

    def test_non_canonical_parse_formats_canonically(self):
        self.assertEqual(format_duration(parse_duration("1h90m")), "2h30m")
        self.assertEqual(format_duration(parse_duration("24h")), "1d")
        self.assertEqual(format_duration(parse_duration("90s")), "1m30s")


if __name__ == "__main__":
    unittest.main()
