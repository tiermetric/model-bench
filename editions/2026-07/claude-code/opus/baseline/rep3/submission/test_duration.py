"""Standard-library unittest suite for duration.py."""

import unittest

from duration import format_duration, parse_duration


class TestParseDurationWorkedExamples(unittest.TestCase):
    """The worked examples from the specification."""

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

    def test_leading_zeros_allowed(self):
        self.assertEqual(parse_duration("01h"), parse_duration("1h"))
        self.assertEqual(parse_duration("007s"), 7)
        self.assertEqual(parse_duration("0001d0002h"), 86400 + 7200)

    def test_components_are_unbounded(self):
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration("100h"), 360000)
        self.assertEqual(parse_duration("999m"), 59940)
        self.assertEqual(parse_duration("1d25h61m61s"), 86400 + 90000 + 3660 + 61)

    def test_large_values(self):
        self.assertEqual(parse_duration("1000000d"), 1000000 * 86400)

    def test_all_valid_subsets_in_order(self):
        self.assertEqual(parse_duration("1d1s"), 86401)
        self.assertEqual(parse_duration("1d1m"), 86460)
        self.assertEqual(parse_duration("1h1s"), 3601)
        self.assertEqual(parse_duration("2d4h"), 2 * 86400 + 4 * 3600)
        self.assertEqual(parse_duration("1d2h3m"), 86400 + 7200 + 180)
        self.assertEqual(parse_duration("2h3m4s"), 7200 + 180 + 4)


class TestParseDurationWhitespace(unittest.TestCase):
    def test_surrounding_whitespace_ignored(self):
        for text in (" 1h30m", "1h30m ", "  1h30m  ", "\t1h30m\n", "\n 1h30m \r\n"):
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), 5400)

    def test_internal_whitespace_invalid(self):
        for text in ("1h 30m", "1 h", "1h30 m", "1h\t30m", "1h\n30m", "1 h 30 m"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_whitespace_only_invalid(self):
        for text in (" ", "   ", "\t", "\n", " \t\n "):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)


class TestParseDurationErrors(unittest.TestCase):
    def test_empty(self):
        with self.assertRaises(ValueError):
            parse_duration("")

    def test_unknown_unit(self):
        for text in ("1x", "1y", "1w", "5ms", "1h30x", "1d2h3m4z"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_uppercase_unit(self):
        for text in ("1H", "1D", "1M", "1S", "1h30M", "1D2H"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_bare_number_no_unit(self):
        for text in ("5", "0", "123", "1h30"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_unit_with_no_number(self):
        for text in ("h", "s", "dhms", "1hm", "hm", "1h30ms"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_non_integer_number(self):
        for text in ("1.5h", "1,5h", ".5h", "1e3s", "0x10s", "1/2h"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_negative_number(self):
        for text in ("-1h", "-5s", "1h-30m", "+1h"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_units_out_of_order(self):
        for text in ("30m1h", "1s1m", "1h1d", "4s3m2h1d", "1m1h1d"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_duplicate_unit(self):
        for text in ("1h1h", "1d1d", "1s1s", "1d2h2h", "1h30m30m", "0s0s"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_stray_separators(self):
        for text in ("1h,30m", "1h:30m", "1h+30m", "1h-", "--", "h1"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)


class TestFormatDurationWorkedExamples(unittest.TestCase):
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

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            format_duration(-1)


class TestFormatDuration(unittest.TestCase):
    def test_zero_is_the_only_zero_component(self):
        self.assertEqual(format_duration(0), "0s")

    def test_single_units(self):
        self.assertEqual(format_duration(1), "1s")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(86400), "1d")

    def test_largest_fitting_units_used(self):
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(172800), "2d")

    def test_zero_components_omitted(self):
        self.assertEqual(format_duration(86401), "1d1s")
        self.assertEqual(format_duration(86460), "1d1m")
        self.assertEqual(format_duration(90000), "1d1h")
        self.assertEqual(format_duration(3601), "1h1s")
        self.assertEqual(format_duration(90061), "1d1h1m1s")

    def test_non_canonical_input_is_normalised(self):
        self.assertEqual(format_duration(parse_duration("1h90m")), "2h30m")
        self.assertEqual(format_duration(parse_duration("90s")), "1m30s")
        self.assertEqual(format_duration(parse_duration("24h")), "1d")

    def test_large_values(self):
        self.assertEqual(format_duration(1000000 * 86400), "1000000d")
        self.assertEqual(format_duration(86399), "23h59m59s")

    def test_negative_values_raise(self):
        for seconds in (-1, -60, -86400, -1000000):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError):
                    format_duration(seconds)

    def test_output_is_parseable(self):
        for seconds in (0, 1, 59, 60, 3599, 3600, 86399, 86400, 93784, 5400):
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)


class TestRoundTrip(unittest.TestCase):
    """parse_duration(format_duration(n)) == n for every n >= 0."""

    def test_small_values_exhaustive(self):
        for n in range(0, 1000):
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_around_unit_boundaries(self):
        boundaries = (60, 3600, 86400, 86400 * 7, 86400 * 365)
        for boundary in boundaries:
            for delta in (-2, -1, 0, 1, 2):
                n = boundary + delta
                with self.subTest(n=n):
                    self.assertEqual(parse_duration(format_duration(n)), n)

    def test_spread_of_larger_values(self):
        for n in range(0, 3_000_000, 7919):
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_format_output_is_canonical(self):
        """Formatting a parsed canonical string reproduces it exactly."""
        for n in range(0, 200_000, 997):
            text = format_duration(n)
            with self.subTest(n=n):
                self.assertEqual(format_duration(parse_duration(text)), text)


if __name__ == "__main__":
    unittest.main()
