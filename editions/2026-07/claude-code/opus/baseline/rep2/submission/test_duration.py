"""Standard-library unittest suite for duration.py."""

import unittest

from duration import format_duration, parse_duration


class TestParseDurationWorkedExamples(unittest.TestCase):
    """The examples named explicitly in the specification."""

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

    def test_worked_error_examples(self):
        for text in ("", "1x", "1H", "30m1h", "1h1h", "5"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)


class TestParseDurationUnits(unittest.TestCase):
    def test_single_units(self):
        self.assertEqual(parse_duration("1d"), 86400)
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertEqual(parse_duration("1m"), 60)
        self.assertEqual(parse_duration("1s"), 1)

    def test_zero_components(self):
        self.assertEqual(parse_duration("0s"), 0)
        self.assertEqual(parse_duration("0d"), 0)
        self.assertEqual(parse_duration("0d0h0m0s"), 0)

    def test_all_valid_orderings_of_two_units(self):
        self.assertEqual(parse_duration("1d1h"), 90000)
        self.assertEqual(parse_duration("1d1m"), 86460)
        self.assertEqual(parse_duration("1d1s"), 86401)
        self.assertEqual(parse_duration("1h1m"), 3660)
        self.assertEqual(parse_duration("1h1s"), 3601)
        self.assertEqual(parse_duration("1m1s"), 61)

    def test_skipping_units_is_allowed(self):
        self.assertEqual(parse_duration("2d30s"), 172830)
        self.assertEqual(parse_duration("1d3m"), 86580)

    def test_all_four_units(self):
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)


class TestParseDurationMagnitudes(unittest.TestCase):
    def test_components_are_unbounded(self):
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("90m"), 5400)
        self.assertEqual(parse_duration("100h"), 360000)
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration("1d99h99m99s"), 86400 + 356400 + 5940 + 99)

    def test_very_large_values(self):
        self.assertEqual(parse_duration("1000000d"), 1000000 * 86400)

    def test_leading_zeros(self):
        self.assertEqual(parse_duration("01h"), parse_duration("1h"))
        self.assertEqual(parse_duration("000s"), 0)
        self.assertEqual(parse_duration("007m"), 420)
        self.assertEqual(parse_duration("01d02h03m04s"), 93784)


class TestParseDurationWhitespace(unittest.TestCase):
    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(parse_duration("  1h30m"), 5400)
        self.assertEqual(parse_duration("1h30m  "), 5400)
        self.assertEqual(parse_duration("\t 1h30m \n"), 5400)

    def test_empty_and_whitespace_only(self):
        for text in ("", " ", "   ", "\t", "\n", " \t\n "):
            with self.subTest(text=repr(text)):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_internal_whitespace_is_invalid(self):
        for text in ("1h 30m", "1 h", "1h\t30m", "1h\n30m", "1d 2h 3m 4s"):
            with self.subTest(text=repr(text)):
                with self.assertRaises(ValueError):
                    parse_duration(text)


class TestParseDurationErrors(unittest.TestCase):
    def test_unknown_units(self):
        for text in ("1x", "1y", "1w", "5ms", "1hh", "3sec"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_uppercase_units_are_invalid(self):
        for text in ("1D", "1H", "1M", "1S", "1h30M"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_bare_number_without_unit(self):
        for text in ("5", "0", "1h30"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_unit_without_number(self):
        for text in ("h", "s", "hm", "1hm", "h30m", "1h m"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_non_integer_numbers(self):
        for text in ("1.5h", "1,5h", ".5h", "1e3s", "0x10s"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_negative_numbers(self):
        for text in ("-1h", "-0s", "1h-30m", "+1h"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_units_out_of_order(self):
        for text in ("30m1h", "1s1m", "1h1d", "4s3m2h1d", "1m1h1s"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_duplicate_units(self):
        for text in ("1h1h", "1d1d", "1s1s", "1h30m30m", "1d2h2h3m"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_stray_separators(self):
        for text in ("1h,30m", "1h+30m", "1h:30m", "1h-", "-", "--"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)


class TestFormatDurationWorkedExamples(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(93784), "1d2h3m4s")
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(0), "0s")

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            format_duration(-1)


class TestFormatDuration(unittest.TestCase):
    def test_single_units(self):
        self.assertEqual(format_duration(1), "1s")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(86400), "1d")

    def test_zero_components_are_omitted(self):
        self.assertEqual(format_duration(86401), "1d1s")
        self.assertEqual(format_duration(86460), "1d1m")
        self.assertEqual(format_duration(3601), "1h1s")
        self.assertEqual(format_duration(172830), "2d30s")

    def test_largest_fitting_units_are_used(self):
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(7200), "2h")
        self.assertEqual(format_duration(172800), "2d")
        self.assertEqual(format_duration(9000), "2h30m")

    def test_zero_only_case(self):
        self.assertEqual(format_duration(0), "0s")

    def test_sub_minute(self):
        self.assertEqual(format_duration(59), "59s")
        self.assertEqual(format_duration(90), "1m30s")

    def test_large_value(self):
        self.assertEqual(format_duration(1000000 * 86400), "1000000d")

    def test_negative_values_raise(self):
        for seconds in (-1, -60, -86400, -1000000):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError):
                    format_duration(seconds)

    def test_output_is_always_parseable(self):
        for seconds in (0, 1, 59, 60, 3599, 3600, 86399, 86400, 93784):
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)


class TestRoundTrip(unittest.TestCase):
    """parse_duration(format_duration(n)) == n for every integer n >= 0."""

    def test_small_contiguous_range(self):
        for n in range(0, 1000):
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_boundaries(self):
        boundaries = []
        for size in (1, 60, 3600, 86400):
            for delta in (-1, 0, 1):
                candidate = size + delta
                if candidate >= 0:
                    boundaries.append(candidate)
        for n in boundaries:
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_strided_large_range(self):
        for n in range(0, 5_000_000, 7919):
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_assorted_large_values(self):
        for n in (10**9, 10**12, 86400 * 365 * 100, 93784, 604800):
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_non_canonical_input_normalizes(self):
        # The reverse round-trip is not required: parse accepts non-canonical
        # forms which format renders canonically.
        self.assertEqual(format_duration(parse_duration("1h90m")), "2h30m")
        self.assertEqual(format_duration(parse_duration("01h")), "1h")
        self.assertEqual(format_duration(parse_duration("90s")), "1m30s")
        self.assertEqual(format_duration(parse_duration("24h")), "1d")


if __name__ == "__main__":
    unittest.main()
