"""Standard-library tests for :mod:`duration`."""

import unittest

from duration import format_duration, parse_duration


class ParseDurationTests(unittest.TestCase):
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

    def test_each_unit_and_multi_digit_components(self):
        self.assertEqual(parse_duration("3d"), 259200)
        self.assertEqual(parse_duration("12h"), 43200)
        self.assertEqual(parse_duration("45m"), 2700)
        self.assertEqual(parse_duration("678s"), 678)
        self.assertEqual(parse_duration("999999999999999999s"), 999999999999999999)

    def test_whole_string_surrounding_whitespace_is_ignored(self):
        self.assertEqual(parse_duration("\t 1h30m \n"), 5400)

    def test_zero_components_are_valid(self):
        self.assertEqual(parse_duration("0d0h0m0s"), 0)

    def test_invalid_inputs_raise_value_error(self):
        invalid = [
            "",
            " \t\n ",
            "1x",
            "1H",
            "30m1h",
            "1h1h",
            "5",
            "h",
            "1h30",
            "-1s",
            "+1s",
            "1.5s",
            "oneh",
            "1 h",
            "1h 30m",
            "1h\t30m",
            "1s1m",
            "1d1d",
            "1m1m",
            "1s1s",
        ]
        for text in invalid:
            with self.subTest(text=repr(text)):
                with self.assertRaises(ValueError):
                    parse_duration(text)


class FormatDurationTests(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(93784), "1d2h3m4s")
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(0), "0s")

    def test_uses_largest_units_and_omits_zero_components(self):
        cases = {
            1: "1s",
            60: "1m",
            3600: "1h",
            86399: "23h59m59s",
            9000: "2h30m",
            172800: "2d",
        }
        for seconds, expected in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(format_duration(seconds), expected)

    def test_negative_input_raises_value_error(self):
        with self.assertRaises(ValueError):
            format_duration(-1)

    def test_round_trip_over_a_range(self):
        for seconds in range(0, 200_001):
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)


if __name__ == "__main__":
    unittest.main()
