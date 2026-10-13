import unittest

from duration import format_duration, parse_duration


class ParseDurationTests(unittest.TestCase):
    def test_worked_examples_and_valid_inputs(self):
        cases = {
            "1h30m": 5400,
            "2d": 172800,
            "1d2h3m4s": 93784,
            "90s": 90,
            "1h90m": 9000,
            "01h": 3600,
            "  1d2h3m4s  ": 93784,
            "000d001h0002m0003s": 3723,
            "999999999999999999999s": 999999999999999999999,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_duration(text), expected)

    def test_invalid_inputs(self):
        invalid = (
            "",
            "   \t\n",
            "1x",
            "1H",
            "30m1h",
            "1h1h",
            "5",
            "h1",
            "1h m",
            "1 h",
            "1h\t30m",
            "-1s",
            "+1s",
            "1.5s",
            "1 s",
            "1d2x",
            "1d2h1d",
            "1s1m",
        )
        for text in invalid:
            with self.subTest(text=repr(text)):
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

    def test_zero_components_are_omitted_and_units_are_largest_first(self):
        cases = {
            1: "1s",
            60: "1m",
            61: "1m1s",
            3600: "1h",
            3661: "1h1m1s",
            86401: "1d1s",
            9000: "2h30m",
        }
        for seconds, expected in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(format_duration(seconds), expected)

    def test_negative_input_is_rejected(self):
        with self.assertRaises(ValueError):
            format_duration(-1)

    def test_round_trip(self):
        values = list(range(0, 500)) + [599, 600, 3599, 3600, 86399, 86400,
                                        86401, 90000, 1234567, 10**30]
        for seconds in values:
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)


if __name__ == "__main__":
    unittest.main()
