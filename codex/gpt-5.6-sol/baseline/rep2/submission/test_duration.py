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

    def test_zero_and_unbounded_components(self):
        self.assertEqual(parse_duration("0d0h0m0s"), 0)
        self.assertEqual(parse_duration("999999999999999999999s"), 999999999999999999999)
        self.assertEqual(parse_duration("25h120m3600s"), 100800)

    def test_leading_and_trailing_whitespace_is_ignored(self):
        for text in (" 1h30m", "1h30m ", "\t\n1h30m\r "):
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), 5400)

    def test_empty_and_whitespace_only_are_invalid(self):
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
        for text in ("5", "h", "1h2", "1hm", "d1h"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_non_integer_and_negative_numbers_are_invalid(self):
        for text in ("1.5h", "-1h", "+1h", "oneh", "1e2s"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_internal_whitespace_is_invalid(self):
        for text in ("1h 30m", "1 h", "1h\t30m", "1\nmin"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_out_of_order_units_are_invalid(self):
        for text in ("30m1h", "1s1m", "1h1d", "1d1s1m"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)

    def test_duplicate_units_are_invalid(self):
        for text in ("1h1h", "1d2d", "1m0m", "1s2s"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)


class FormatDurationTests(unittest.TestCase):
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

    def test_zero_components_are_omitted(self):
        self.assertEqual(format_duration(86401), "1d1s")
        self.assertEqual(format_duration(3660), "1h1m")
        self.assertEqual(format_duration(60), "1m")

    def test_largest_fitting_units_are_used(self):
        self.assertEqual(format_duration(172800), "2d")
        self.assertEqual(format_duration(9000), "2h30m")
        self.assertEqual(format_duration(3600), "1h")

    def test_negative_input_is_invalid(self):
        with self.assertRaises(ValueError):
            format_duration(-1)

    def test_round_trip_over_range(self):
        for seconds in range(0, 200001):
            self.assertEqual(parse_duration(format_duration(seconds)), seconds)

    def test_round_trip_for_large_values(self):
        for seconds in (10**9, 10**20, 10**100):
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)


if __name__ == "__main__":
    unittest.main()
