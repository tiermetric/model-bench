"""Standard-library tests for duration parsing and formatting."""

import unittest

from duration import format_duration, parse_duration


class ParseDurationTests(unittest.TestCase):
    def test_worked_examples(self):
        cases = {
            "1h30m": 5_400,
            "2d": 172_800,
            "1d2h3m4s": 93_784,
            "90s": 90,
            "1h90m": 9_000,
            "01h": 3_600,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), expected)

    def test_all_units_and_outer_whitespace(self):
        self.assertEqual(parse_duration("3d"), 259_200)
        self.assertEqual(parse_duration("4h"), 14_400)
        self.assertEqual(parse_duration("5m"), 300)
        self.assertEqual(parse_duration("6s"), 6)
        self.assertEqual(parse_duration(" \t1h30m\n"), 5_400)

    def test_zero_components_and_unbounded_magnitudes_are_valid(self):
        self.assertEqual(parse_duration("0d0h0m0s"), 0)
        self.assertEqual(parse_duration("999999999999999999999s"), 999999999999999999999)

    def test_invalid_inputs(self):
        invalid = (
            "", " \t\n", "1x", "1H", "30m1h", "1h1h", "5",
            "h", "1h30", "-1s", "+1s", "1.5s", "oneh",
            "1 h", "1h 30m", "1h\t30m", "1s1m", "1d1m1h",
            "1m1m", "d1", "1",
        )
        for text in invalid:
            with self.subTest(text=repr(text)):
                with self.assertRaises(ValueError):
                    parse_duration(text)


class FormatDurationTests(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(format_duration(5_400), "1h30m")
        self.assertEqual(format_duration(93_784), "1d2h3m4s")
        self.assertEqual(format_duration(86_400), "1d")
        self.assertEqual(format_duration(0), "0s")

    def test_canonical_omits_zero_components_and_uses_largest_units(self):
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(3_600), "1h")
        self.assertEqual(format_duration(86_461), "1d1m1s")
        self.assertEqual(format_duration(9_000), "2h30m")

    def test_negative_and_non_integer_inputs_raise_value_error(self):
        for seconds in (-1, -86_400, 1.5, "1", True):
            with self.subTest(seconds=repr(seconds)):
                with self.assertRaises(ValueError):
                    format_duration(seconds)


class RoundTripTests(unittest.TestCase):
    def test_format_then_parse_across_a_range(self):
        for seconds in range(0, 200_001):
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)


if __name__ == "__main__":
    unittest.main()
