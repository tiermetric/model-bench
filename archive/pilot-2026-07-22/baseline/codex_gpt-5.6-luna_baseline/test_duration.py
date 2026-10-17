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

    def test_units_and_whole_string_whitespace(self):
        self.assertEqual(parse_duration("  1d2h3m4s\n"), 93784)
        self.assertEqual(parse_duration("0d00h000m000s"), 0)

    def test_all_units_are_unbounded(self):
        self.assertEqual(parse_duration("1d25h90m120s"), 1 * 86400 + 25 * 3600 + 90 * 60 + 120)

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
            "1.5h",
            "-1h",
            "1 h",
            "1h 30m",
            "1h\t30m",
            "1h30",
            "1h2x",
        ]
        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_non_string_input_is_invalid(self):
        with self.assertRaises(ValueError):
            parse_duration(None)


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

    def test_zero_components_are_omitted_and_units_are_largest_first(self):
        self.assertEqual(format_duration(86400 + 1), "1d1s")
        self.assertEqual(format_duration(3600 + 60), "1h1m")
        self.assertEqual(format_duration(59), "59s")
        self.assertEqual(format_duration(9000), "2h30m")

    def test_negative_and_non_integer_inputs(self):
        for seconds in (-1, -86400, 1.0, True, None):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError):
                    format_duration(seconds)

    def test_round_trip(self):
        values = list(range(0, 500)) + [59, 60, 61, 3599, 3600, 3601, 86399, 86400, 86401]
        for seconds in values:
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)


if __name__ == "__main__":
    unittest.main()
