"""unittest suite for duration.parse_duration and duration.format_duration."""

import unittest

from duration import format_duration, parse_duration


class TestParseDuration(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2d"), 172800)
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)
        self.assertEqual(parse_duration("90s"), 90)

    def test_single_units(self):
        self.assertEqual(parse_duration("1d"), 86400)
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertEqual(parse_duration("1m"), 60)
        self.assertEqual(parse_duration("1s"), 1)

    def test_zero_components(self):
        self.assertEqual(parse_duration("0s"), 0)
        self.assertEqual(parse_duration("0d0h0m0s"), 0)
        self.assertEqual(parse_duration("0h30m"), 1800)

    def test_unbounded_component_magnitudes(self):
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration("1000d"), 86400000)
        self.assertEqual(parse_duration("123456789s"), 123456789)

    def test_leading_zeros(self):
        self.assertEqual(parse_duration("01h"), 3600)
        self.assertEqual(parse_duration("007s"), 7)
        self.assertEqual(parse_duration("00d01h"), 3600)

    def test_outer_whitespace_ignored(self):
        self.assertEqual(parse_duration("  1h30m"), 5400)
        self.assertEqual(parse_duration("1h30m  "), 5400)
        self.assertEqual(parse_duration("\t 2d \n"), 172800)

    def test_empty_or_whitespace_only(self):
        for text in ("", " ", "   ", "\t", "\n", " \t\n "):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_unknown_unit(self):
        for text in ("1x", "1w", "1y", "5q"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_uppercase_unit(self):
        for text in ("1H", "1D", "1M", "1S", "1h30M"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_bare_number_without_unit(self):
        for text in ("5", "123", "1h30"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_unit_without_number(self):
        for text in ("h", "s", "h30m", "1hm"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_non_integer_or_negative_number(self):
        for text in ("1.5h", "-1h", "1h-30m", "+1h", "1e3s"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_internal_whitespace(self):
        for text in ("1h 30m", "1 h", "1h\t30m", "1h\n30m", "1 h30m"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_units_out_of_order(self):
        for text in ("30m1h", "1s1m", "1h2d", "1m1d", "1s1h", "2h1d3m"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_duplicate_units(self):
        for text in ("1h1h", "1d1d", "2m3m", "1s1s", "1d2h2h"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
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

    def test_largest_fitting_units(self):
        self.assertEqual(format_duration(172800), "2d")
        self.assertEqual(format_duration(7200), "2h")
        self.assertEqual(format_duration(90), "1m30s")
        self.assertEqual(format_duration(9000), "2h30m")

    def test_large_values(self):
        self.assertEqual(format_duration(1000 * 86400), "1000d")
        self.assertEqual(format_duration(1000 * 86400 + 59), "1000d59s")

    def test_negative_raises(self):
        for seconds in (-1, -60, -86400):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError):
                    format_duration(seconds)


class TestRoundTrip(unittest.TestCase):
    def test_round_trip_range(self):
        for n in range(0, 200000, 7):
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_round_trip_boundaries(self):
        boundaries = (0, 1, 59, 60, 61, 3599, 3600, 3601, 86399, 86400,
                      86401, 90061, 10**9, 10**12 + 12345)
        for n in boundaries:
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_non_canonical_parse_reformats_canonically(self):
        self.assertEqual(format_duration(parse_duration("1h90m")), "2h30m")
        self.assertEqual(format_duration(parse_duration("90s")), "1m30s")
        self.assertEqual(format_duration(parse_duration("24h")), "1d")


if __name__ == "__main__":
    unittest.main()
