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
        self.assertEqual(parse_duration("3m4s"), 184)

    def test_zero_components_allowed(self):
        self.assertEqual(parse_duration("0s"), 0)
        self.assertEqual(parse_duration("0d0h0m0s"), 0)
        self.assertEqual(parse_duration("0d5m"), 300)

    def test_unbounded_component_magnitudes(self):
        self.assertEqual(parse_duration("90m"), 5400)
        self.assertEqual(parse_duration("100d"), 8640000)
        self.assertEqual(parse_duration("1000000s"), 1000000)

    def test_leading_zeros(self):
        self.assertEqual(parse_duration("007s"), 7)
        self.assertEqual(parse_duration("01h02m"), 3720)

    def test_outer_whitespace_ignored(self):
        self.assertEqual(parse_duration("  1h30m  "), 5400)
        self.assertEqual(parse_duration("\t2d\n"), 172800)

    def test_empty_or_whitespace_only_raises(self):
        for text in ["", "   ", "\t", "\n"]:
            with self.subTest(text=text):
                self.assertRaises(ValueError, parse_duration, text)

    def test_unknown_unit_raises(self):
        for text in ["1x", "1w", "1h2q"]:
            with self.subTest(text=text):
                self.assertRaises(ValueError, parse_duration, text)

    def test_uppercase_unit_raises(self):
        for text in ["1H", "1D", "1M", "1S", "1h30M"]:
            with self.subTest(text=text):
                self.assertRaises(ValueError, parse_duration, text)

    def test_bare_number_raises(self):
        for text in ["5", "123", "1h30"]:
            with self.subTest(text=text):
                self.assertRaises(ValueError, parse_duration, text)

    def test_unit_without_number_raises(self):
        for text in ["h", "s", "d2h", "1hm"]:
            with self.subTest(text=text):
                self.assertRaises(ValueError, parse_duration, text)

    def test_non_integer_number_raises(self):
        for text in ["1.5h", "1,5h", "1e2s"]:
            with self.subTest(text=text):
                self.assertRaises(ValueError, parse_duration, text)

    def test_negative_number_raises(self):
        for text in ["-1h", "1h-30m", "+1h"]:
            with self.subTest(text=text):
                self.assertRaises(ValueError, parse_duration, text)

    def test_inner_whitespace_raises(self):
        for text in ["1h 30m", "1 h", " 1h 30m ", "1h\t30m"]:
            with self.subTest(text=text):
                self.assertRaises(ValueError, parse_duration, text)

    def test_units_out_of_order_raise(self):
        for text in ["30m1h", "1s1m", "1h2d", "4s3m2h1d"]:
            with self.subTest(text=text):
                self.assertRaises(ValueError, parse_duration, text)

    def test_duplicate_units_raise(self):
        for text in ["1h1h", "2d2d", "1m0m", "1d1h1d"]:
            with self.subTest(text=text):
                self.assertRaises(ValueError, parse_duration, text)

    def test_non_ascii_digits_raise(self):
        self.assertRaises(ValueError, parse_duration, "١h")  # Arabic-Indic one


class TestFormatDuration(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(93784), "1d2h3m4s")
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(0), "0s")

    def test_negative_raises(self):
        self.assertRaises(ValueError, format_duration, -1)
        self.assertRaises(ValueError, format_duration, -86400)

    def test_largest_units_used(self):
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(86400 + 3600), "1d1h")
        self.assertEqual(format_duration(90 * 60), "1h30m")

    def test_zero_components_omitted(self):
        self.assertEqual(format_duration(86401), "1d1s")
        self.assertEqual(format_duration(3601), "1h1s")
        self.assertEqual(format_duration(86400 + 60), "1d1m")

    def test_small_values(self):
        self.assertEqual(format_duration(1), "1s")
        self.assertEqual(format_duration(59), "59s")
        self.assertEqual(format_duration(61), "1m1s")

    def test_large_values(self):
        self.assertEqual(format_duration(365 * 86400), "365d")
        self.assertEqual(format_duration(10**9), "11574d1h46m40s")


class TestRoundTrip(unittest.TestCase):
    def test_round_trip_dense_range(self):
        for n in range(0, 400):
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_round_trip_boundaries(self):
        boundaries = [59, 60, 61, 3599, 3600, 3601, 86399, 86400, 86401]
        for n in boundaries:
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_round_trip_sparse_large(self):
        for n in range(0, 10**7, 99991):
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_parse_of_non_canonical_reformats_canonically(self):
        self.assertEqual(format_duration(parse_duration("1h90m")), "2h30m")
        self.assertEqual(format_duration(parse_duration("24h")), "1d")
        self.assertEqual(format_duration(parse_duration("90s")), "1m30s")


if __name__ == "__main__":
    unittest.main()
