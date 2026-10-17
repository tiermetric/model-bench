"""Standard-library tests for :mod:`duration`."""

import unittest

from duration import format_duration, parse_duration


class ParseDurationTests(unittest.TestCase):
    def test_worked_examples(self):
        examples = {
            "1h30m": 5_400,
            "2d": 172_800,
            "1d2h3m4s": 93_784,
            "90s": 90,
            "1h90m": 9_000,
            "01h": 3_600,
        }
        for text, expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), expected)

    def test_whole_string_whitespace_is_ignored(self):
        self.assertEqual(parse_duration(" \t1h30m\n"), 5_400)

    def test_unbounded_component_magnitudes_are_allowed(self):
        self.assertEqual(parse_duration("999999999999999999999m"), 59_999_999_999_999_999_999_940)

    def test_invalid_inputs(self):
        invalid = (
            "",
            " \t\n",
            "1x",
            "1H",
            "30m1h",
            "1h1h",
            "5",
            "h",
            "1h30",
            "-1s",
            "1.5s",
            "1 h",
            "1h 30m",
            "1h\t30m",
            "1s1m",
            "1d1d",
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

    def test_omits_zero_components_and_uses_largest_units(self):
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(3_661), "1h1m1s")
        self.assertEqual(format_duration(172_800), "2d")

    def test_negative_input_is_invalid(self):
        with self.assertRaises(ValueError):
            format_duration(-1)

    def test_round_trip_over_a_range(self):
        for seconds in range(0, 100_001):
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)


if __name__ == "__main__":
    unittest.main()
