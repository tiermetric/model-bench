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

    def test_zero_and_unbounded_components(self):
        self.assertEqual(parse_duration("0d0h0m0s"), 0)
        self.assertEqual(parse_duration("999999999999999999999s"), 999999999999999999999)
        self.assertEqual(parse_duration("25h61m61s"), 93_721)

    def test_outer_whitespace_is_ignored(self):
        for text in (" 1h30m", "1h30m\n", "\t1h30m \r\n"):
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), 5_400)

    def test_empty_or_whitespace_only_is_invalid(self):
        for text in ("", " ", "\t\n"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_unknown_and_uppercase_units_are_invalid(self):
        for text in ("1x", "1H", "1D", "1M", "1S"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_missing_number_or_unit_is_invalid(self):
        for text in ("5", "h", "1h2", "1hs"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_non_integer_and_negative_numbers_are_invalid(self):
        for text in ("1.5h", "-1h", "+1h", "oneh"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_internal_whitespace_is_invalid(self):
        for text in ("1h 30m", "1 h", "1h\t30m", "1h\n30m"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_out_of_order_and_duplicate_units_are_invalid(self):
        for text in ("30m1h", "1s1m", "1h1d", "1h1h", "1d2h3m4s5s"):
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

    def test_uses_largest_units_and_omits_zero_components(self):
        cases = {
            60: "1m",
            3_600: "1h",
            86_400 + 60: "1d1m",
            2 * 86_400 + 3_601: "2d1h1s",
        }
        for seconds, expected in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(format_duration(seconds), expected)

    def test_negative_input_is_invalid(self):
        with self.assertRaises(ValueError):
            format_duration(-1)

    def test_round_trip_across_range(self):
        for seconds in range(0, 200_001):
            self.assertEqual(parse_duration(format_duration(seconds)), seconds)

    def test_round_trip_large_values(self):
        for seconds in (10**9, 10**30, 10**100):
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)


if __name__ == "__main__":
    unittest.main()
