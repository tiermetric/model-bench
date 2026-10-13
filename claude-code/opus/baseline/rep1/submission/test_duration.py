"""Standard-library unittest suite for duration.py."""

import unittest

from duration import format_duration, parse_duration


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
        self.assertEqual(parse_duration("0s"), 0)
        self.assertEqual(parse_duration("0d0h0m0s"), 0)
        self.assertEqual(parse_duration("0d1h"), 3600)

    def test_every_ordered_subset_of_units(self):
        # Any subset of d/h/m/s in descending order is valid.
        units = (("d", 86400), ("h", 3600), ("m", 60), ("s", 1))
        for mask in range(1, 16):
            chosen = [u for i, u in enumerate(units) if mask & (1 << (3 - i))]
            text = "".join(f"2{letter}" for letter, _ in chosen)
            expected = sum(2 * size for _, size in chosen)
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), expected)

    def test_components_are_unbounded(self):
        self.assertEqual(parse_duration("90m"), 5400)
        self.assertEqual(parse_duration("100h"), 360000)
        self.assertEqual(parse_duration("1h120m120s"), 3600 + 7200 + 120)
        self.assertEqual(parse_duration("999999999999s"), 999999999999)

    def test_leading_zeros_allowed(self):
        self.assertEqual(parse_duration("01h"), parse_duration("1h"))
        self.assertEqual(parse_duration("0001d000030m"), 86400 + 1800)
        self.assertEqual(parse_duration("00s"), 0)

    def test_surrounding_whitespace_ignored(self):
        self.assertEqual(parse_duration("  1h30m"), 5400)
        self.assertEqual(parse_duration("1h30m  "), 5400)
        self.assertEqual(parse_duration("\t\n 1h30m \r\n"), 5400)


class ParseErrors(unittest.TestCase):
    def assertParseRaises(self, text):
        with self.subTest(text=text):
            with self.assertRaises(ValueError):
                parse_duration(text)

    def test_worked_error_examples(self):
        for text in ("", "1x", "1H", "30m1h", "1h1h", "5"):
            self.assertParseRaises(text)

    def test_empty_or_whitespace_only(self):
        for text in ("", " ", "   ", "\t", "\n", " \t\r\n "):
            self.assertParseRaises(text)

    def test_unknown_unit(self):
        for text in ("1x", "1y", "1w", "1ms", "2d3q", "1h30z"):
            self.assertParseRaises(text)

    def test_uppercase_unit_rejected(self):
        for text in ("1D", "1H", "1M", "1S", "1h30M", "1D2H3M4S"):
            self.assertParseRaises(text)

    def test_bare_number_without_unit(self):
        for text in ("5", "0", "123", "1h30"):
            self.assertParseRaises(text)

    def test_unit_without_number(self):
        for text in ("h", "s", "dhms", "1hm", "h30m", "1h m"):
            self.assertParseRaises(text)

    def test_non_integer_number(self):
        for text in ("1.5h", "1,5h", ".5h", "1e3s", "1/2h"):
            self.assertParseRaises(text)

    def test_negative_number(self):
        for text in ("-1h", "-5s", "1h-30m", "+1h"):
            self.assertParseRaises(text)

    def test_internal_whitespace(self):
        for text in ("1h 30m", "1 h", "1h\t30m", "1h\n30m", " 1h 30m "):
            self.assertParseRaises(text)

    def test_units_out_of_order(self):
        for text in ("30m1h", "1s1m", "4s3m2h1d", "1m2d", "1h1d", "1s1h"):
            self.assertParseRaises(text)

    def test_duplicate_units(self):
        for text in ("1h1h", "1d2d", "1m1m", "0s0s", "1d1h1h", "1h30m30m"):
            self.assertParseRaises(text)

    def test_stray_separators(self):
        for text in ("1h,30m", "1h:30m", "1h-", "-", "h1", "1h30m5"):
            self.assertParseRaises(text)


class FormatDuration(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(93784), "1d2h3m4s")
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(0), "0s")

    def test_zero_is_the_only_zero_component(self):
        self.assertEqual(format_duration(0), "0s")
        # 1d0h0m1s collapses to the two non-zero components.
        self.assertEqual(format_duration(86401), "1d1s")
        self.assertEqual(format_duration(90061), "1d1h1m1s")

    def test_single_units(self):
        self.assertEqual(format_duration(1), "1s")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(86400), "1d")

    def test_largest_fitting_units_used(self):
        self.assertEqual(format_duration(59), "59s")
        self.assertEqual(format_duration(3599), "59m59s")
        self.assertEqual(format_duration(86399), "23h59m59s")
        self.assertEqual(format_duration(172800), "2d")
        self.assertEqual(format_duration(9000), "2h30m")

    def test_no_whitespace_or_zero_padding(self):
        text = format_duration(93784)
        self.assertNotIn(" ", text)
        self.assertEqual(text, text.strip())
        self.assertEqual(format_duration(5), "5s")

    def test_negative_raises(self):
        for seconds in (-1, -60, -86400, -999999):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError):
                    format_duration(seconds)

    def test_large_value(self):
        self.assertEqual(format_duration(1000000), "11d13h46m40s")


class RoundTrip(unittest.TestCase):
    def test_round_trip_small_range(self):
        for n in range(0, 1000):
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_round_trip_sampled_range(self):
        for n in range(0, 500000, 997):
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_round_trip_boundaries(self):
        boundaries = [
            0, 1, 59, 60, 61, 3599, 3600, 3601, 86399, 86400, 86401,
            90061, 93784, 172800, 1000000, 31536000, 10**12,
        ]
        for n in boundaries:
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_format_is_canonical(self):
        # Non-canonical input parses fine but formats to the canonical form.
        self.assertEqual(format_duration(parse_duration("1h90m")), "2h30m")
        self.assertEqual(format_duration(parse_duration("120s")), "2m")
        self.assertEqual(format_duration(parse_duration("24h")), "1d")
        self.assertEqual(format_duration(parse_duration("01h")), "1h")


class PublicApi(unittest.TestCase):
    def test_only_two_public_functions(self):
        import duration

        public = {
            name for name in vars(duration)
            if not name.startswith("_") and callable(vars(duration)[name])
        }
        self.assertEqual(public, {"parse_duration", "format_duration"})


if __name__ == "__main__":
    unittest.main()
