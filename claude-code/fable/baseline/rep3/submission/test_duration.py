"""Unit tests for duration.parse_duration and duration.format_duration."""

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

    def test_all_units_combined(self):
        self.assertEqual(parse_duration("2d4h"), 2 * 86400 + 4 * 3600)
        self.assertEqual(parse_duration("1d1s"), 86401)
        self.assertEqual(parse_duration("3m20s"), 200)

    def test_zero_components_allowed(self):
        self.assertEqual(parse_duration("0s"), 0)
        self.assertEqual(parse_duration("0d0h0m0s"), 0)
        self.assertEqual(parse_duration("0d5s"), 5)

    def test_unbounded_component_magnitudes(self):
        self.assertEqual(parse_duration("90m"), 5400)
        self.assertEqual(parse_duration("25h"), 90000)
        self.assertEqual(parse_duration("1000000d"), 1000000 * 86400)
        self.assertEqual(parse_duration("2d100h"), 2 * 86400 + 100 * 3600)

    def test_leading_zeros(self):
        self.assertEqual(parse_duration("007s"), 7)
        self.assertEqual(parse_duration("01h02m"), 3720)
        self.assertEqual(parse_duration("00s"), 0)

    def test_outer_whitespace_ignored(self):
        self.assertEqual(parse_duration("  1h30m"), 5400)
        self.assertEqual(parse_duration("1h30m   "), 5400)
        self.assertEqual(parse_duration("\t2d\n"), 172800)

    def test_empty_or_whitespace_only_raises(self):
        for text in ["", " ", "   ", "\t", "\n", " \t\n "]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_unknown_unit_raises(self):
        for text in ["1x", "5w", "3y", "1h2q"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_uppercase_unit_raises(self):
        for text in ["1H", "2D", "3M", "4S", "1h30M"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_bare_number_raises(self):
        for text in ["5", "90", "1h30"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_unit_without_number_raises(self):
        for text in ["h", "s", "d2h", "1hm", "h30m"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_non_integer_number_raises(self):
        for text in ["1.5h", "1,5h", "1e2s", "½h"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_signed_number_raises(self):
        for text in ["-1h", "+1h", "1h-30m", "1h+30m"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_interior_whitespace_raises(self):
        for text in ["1h 30m", "1 h", " 1h 30m ", "1h\t30m", "1h\n30m"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_units_out_of_order_raises(self):
        for text in ["30m1h", "1s1m", "1h2d", "4s3m2h1d", "1m1d"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_duplicate_unit_raises(self):
        for text in ["1h1h", "1d1d", "1h30m2m", "2s2s", "1d2h3m4s5s"]:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)


class TestFormatDuration(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(93784), "1d2h3m4s")
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(0), "0s")

    def test_negative_raises(self):
        for seconds in [-1, -60, -86400]:
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError):
                    format_duration(seconds)

    def test_single_units(self):
        self.assertEqual(format_duration(1), "1s")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(86400), "1d")

    def test_largest_units_used(self):
        self.assertEqual(format_duration(172800), "2d")
        self.assertEqual(format_duration(7200), "2h")
        self.assertEqual(format_duration(90000), "1d1h")
        self.assertEqual(format_duration(9000), "2h30m")

    def test_zero_components_omitted(self):
        self.assertEqual(format_duration(86401), "1d1s")
        self.assertEqual(format_duration(3601), "1h1s")
        self.assertEqual(format_duration(86460), "1d1m")
        self.assertEqual(format_duration(61), "1m1s")

    def test_large_values(self):
        self.assertEqual(format_duration(10**9), "11574d1h46m40s")
        self.assertEqual(format_duration(1000000 * 86400), "1000000d")


class TestRoundTrip(unittest.TestCase):
    def test_round_trip_small_range(self):
        for n in range(0, 100000, 7):
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_round_trip_boundary_values(self):
        boundaries = [0, 1, 59, 60, 61, 3599, 3600, 3601, 86399, 86400, 86401]
        for n in boundaries:
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_round_trip_large_values(self):
        for n in [10**6, 10**9, 10**12, 123456789, 86400 * 365 * 100 + 12345]:
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_non_canonical_parse_reformats_canonically(self):
        self.assertEqual(format_duration(parse_duration("1h90m")), "2h30m")
        self.assertEqual(format_duration(parse_duration("90s")), "1m30s")
        self.assertEqual(format_duration(parse_duration("24h")), "1d")


if __name__ == "__main__":
    unittest.main()
