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

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(parse_duration(" \t1h30m\n"), 5400)

    def test_zero_components_and_unbounded_components(self):
        self.assertEqual(parse_duration("0d0h0m0s"), 0)
        self.assertEqual(parse_duration("1000000d"), 86_400_000_000)
        self.assertEqual(parse_duration("999h999m999s"), 3_657_339)

    def test_valid_unit_subsets_remain_in_order(self):
        cases = {"1d2m": 86520, "1d2s": 86402, "1h2s": 3602, "1m": 60}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), expected)

    def test_rejects_all_specified_invalid_inputs(self):
        invalid = [
            "", "   ", "1x", "1H", "30m1h", "1h1h", "5",
            "h", "-1h", "1.5h", "1 h", "1h 30m", "1h\t30m",
            "1s1m", "1d2h3m4s5s", "+1h", "1hm", "m1s",
        ]
        for text in invalid:
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

    def test_uses_largest_units_and_omits_zero_components(self):
        cases = {
            60: "1m", 3600: "1h", 90000: "1d1h",
            86461: "1d1m1s", 172800: "2d",
        }
        for seconds, expected in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(format_duration(seconds), expected)

    def test_negative_input_is_rejected(self):
        with self.assertRaises(ValueError):
            format_duration(-1)

    def test_round_trip_across_range(self):
        values = list(range(0, 200_001, 37))
        values += [10**6, 10**12, 10**30]
        for seconds in values:
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)


if __name__ == "__main__":
    unittest.main()
