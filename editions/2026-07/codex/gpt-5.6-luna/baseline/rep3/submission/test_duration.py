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

    def test_whole_string_whitespace_is_ignored(self):
        self.assertEqual(parse_duration("  1h30m\t"), 5400)

    def test_all_units_and_unbounded_components(self):
        self.assertEqual(parse_duration("0002d0003h004m0005s"), 2 * 86400 + 3 * 3600 + 4 * 60 + 5)
        self.assertEqual(parse_duration("1d99h99m99s"), 86400 + 99 * 3600 + 99 * 60 + 99)

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
            "1",
            "1.5h",
            "-1h",
            "+1h",
            "1 h",
            "1h 30m",
            "1h30 m",
            "1h\n30m",
        ]
        for text in invalid:
            with self.subTest(text=repr(text)):
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

    def test_zero_components_are_omitted(self):
        self.assertEqual(format_duration(86400 + 1), "1d1s")
        self.assertEqual(format_duration(3600 + 60), "1h1m")
        self.assertEqual(format_duration(60), "1m")

    def test_negative_and_non_integer_inputs_are_invalid(self):
        for value in (-1, -86400, 1.0, True, "60"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    format_duration(value)

    def test_round_trip_across_range(self):
        values = list(range(0, 250)) + [599, 600, 3599, 3600, 3601, 86399, 86400, 86401, 172800, 987654321]
        for seconds in values:
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)


if __name__ == "__main__":
    unittest.main()
