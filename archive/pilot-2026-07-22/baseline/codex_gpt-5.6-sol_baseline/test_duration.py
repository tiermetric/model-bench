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

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(parse_duration(" \t1h30m\n"), 5_400)

    def test_zero_and_unbounded_components(self):
        self.assertEqual(parse_duration("0d0h0m0s"), 0)
        self.assertEqual(parse_duration("999999999999999999999s"),
                         999_999_999_999_999_999_999)

    def test_invalid_inputs(self):
        invalid = [
            "", "   ", "1x", "1H", "30m1h", "1h1h", "5",
            "h", "-1h", "1.5h", "1 h", "1h 30m", "1h\t30m",
            "1h30", "1h+2m", "1hh", "1m2m", "1s1m", "+1h",
        ]
        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)


class FormatDurationTests(unittest.TestCase):
    def test_worked_examples(self):
        cases = {
            5_400: "1h30m",
            93_784: "1d2h3m4s",
            86_400: "1d",
            0: "0s",
        }
        for seconds, expected in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(format_duration(seconds), expected)

    def test_omits_zero_components_and_uses_largest_units(self):
        self.assertEqual(format_duration(172_801), "2d1s")
        self.assertEqual(format_duration(3_660), "1h1m")
        self.assertEqual(format_duration(60), "1m")

    def test_negative_input_raises_value_error(self):
        with self.assertRaises(ValueError):
            format_duration(-1)

    def test_round_trip_across_range(self):
        values = list(range(10_001)) + [
            86_399, 86_400, 86_401, 10**6, 10**12, 10**30,
        ]
        for seconds in values:
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)


if __name__ == "__main__":
    unittest.main()
