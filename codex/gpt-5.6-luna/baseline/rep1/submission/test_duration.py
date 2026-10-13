import unittest

from duration import format_duration, parse_duration


class ParseDurationTests(unittest.TestCase):
    def test_worked_examples(self):
        examples = {
            "1h30m": 5400,
            "2d": 172800,
            "1d2h3m4s": 93784,
            "90s": 90,
            "1h90m": 9000,
            "01h": 3600,
        }
        for text, expected in examples.items():
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), expected)

    def test_all_units_and_whole_input_whitespace(self):
        self.assertEqual(parse_duration(" 1d2h3m4s "), 93784)
        self.assertEqual(parse_duration("000d001h002m003s"), 3723)
        self.assertEqual(parse_duration("1d90h90m90s"), 86400 + 90 * 3600 + 90 * 60 + 90)

    def test_invalid_inputs(self):
        invalid = [
            "",
            "   ",
            "1x",
            "1H",
            "30m1h",
            "1h1h",
            "5",
            "h1",
            "1h m",
            "1 h",
            "-1s",
            "1.5h",
            "1h+2m",
            "d",
            "1d2d",
            "1m1s1d",
        ]
        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)


class FormatDurationTests(unittest.TestCase):
    def test_worked_examples(self):
        examples = {
            5400: "1h30m",
            93784: "1d2h3m4s",
            86400: "1d",
            0: "0s",
        }
        for seconds, expected in examples.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(format_duration(seconds), expected)

    def test_omits_zero_components_and_uses_largest_units(self):
        examples = {
            1: "1s",
            60: "1m",
            3600: "1h",
            86401: "1d1s",
            9000: "2h30m",
            172800 + 3600 + 60: "2d1h1m",
        }
        for seconds, expected in examples.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(format_duration(seconds), expected)

    def test_negative_and_non_integer_inputs(self):
        for value in (-1, -86400, 1.0, "1"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    format_duration(value)

    def test_round_trip_across_range(self):
        values = list(range(0, 301)) + [3599, 3600, 3601, 86399, 86400, 86401]
        for seconds in values:
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)


if __name__ == "__main__":
    unittest.main()
