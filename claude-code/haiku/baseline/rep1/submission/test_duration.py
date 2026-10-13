import unittest
from duration import parse_duration, format_duration


class TestParseDuration(unittest.TestCase):

    def test_parse_worked_examples(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2d"), 172800)
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration("01h"), 3600)

    def test_parse_single_units(self):
        self.assertEqual(parse_duration("1d"), 86400)
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertEqual(parse_duration("1m"), 60)
        self.assertEqual(parse_duration("1s"), 1)

    def test_parse_zero_values(self):
        self.assertEqual(parse_duration("0d"), 0)
        self.assertEqual(parse_duration("0s"), 0)
        self.assertEqual(parse_duration("0d0h0m0s"), 0)

    def test_parse_multiple_units(self):
        self.assertEqual(parse_duration("1d2h"), 86400 + 7200)
        self.assertEqual(parse_duration("2h30m"), 7200 + 1800)
        self.assertEqual(parse_duration("1d1h1m1s"), 86400 + 3600 + 60 + 1)

    def test_parse_leading_zeros(self):
        self.assertEqual(parse_duration("01d"), 86400)
        self.assertEqual(parse_duration("001h"), 3600)
        self.assertEqual(parse_duration("0001s"), 1)
        self.assertEqual(parse_duration("01d02h03m04s"), 93784)

    def test_parse_large_numbers(self):
        self.assertEqual(parse_duration("100d"), 100 * 86400)
        self.assertEqual(parse_duration("1000h"), 1000 * 3600)
        self.assertEqual(parse_duration("10000m"), 10000 * 60)
        self.assertEqual(parse_duration("1000000s"), 1000000)

    def test_parse_whitespace_handling(self):
        self.assertEqual(parse_duration("  1h30m  "), 5400)
        self.assertEqual(parse_duration("\t2d\t"), 172800)
        self.assertEqual(parse_duration("\n90s\n"), 90)

    def test_parse_empty_string_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("")

    def test_parse_whitespace_only_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("   ")

    def test_parse_internal_whitespace_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("1h 30m")
        with self.assertRaises(ValueError):
            parse_duration("1h\t30m")
        with self.assertRaises(ValueError):
            parse_duration("1h\n30m")

    def test_parse_unknown_unit_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("1x")
        with self.assertRaises(ValueError):
            parse_duration("1w")
        with self.assertRaises(ValueError):
            parse_duration("1y")

    def test_parse_uppercase_unit_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("1H")
        with self.assertRaises(ValueError):
            parse_duration("1D")
        with self.assertRaises(ValueError):
            parse_duration("1M")
        with self.assertRaises(ValueError):
            parse_duration("1S")

    def test_parse_bare_number_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("5")
        with self.assertRaises(ValueError):
            parse_duration("100")

    def test_parse_bare_unit_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("h")
        with self.assertRaises(ValueError):
            parse_duration("m")

    def test_parse_out_of_order_units_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("30m1h")
        with self.assertRaises(ValueError):
            parse_duration("1s1h")
        with self.assertRaises(ValueError):
            parse_duration("1m1d")
        with self.assertRaises(ValueError):
            parse_duration("1h1d")

    def test_parse_duplicate_units_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("1h1h")
        with self.assertRaises(ValueError):
            parse_duration("1d1d")
        with self.assertRaises(ValueError):
            parse_duration("1h30m15m")
        with self.assertRaises(ValueError):
            parse_duration("1d2h3m1s1s")

    def test_parse_negative_values_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("-1h")
        with self.assertRaises(ValueError):
            parse_duration("-10s")

    def test_parse_non_integer_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("1.5h")
        with self.assertRaises(ValueError):
            parse_duration("1.0s")
        with self.assertRaises(ValueError):
            parse_duration("1e2h")


class TestFormatDuration(unittest.TestCase):

    def test_format_worked_examples(self):
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(93784), "1d2h3m4s")
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(0), "0s")

    def test_format_single_units(self):
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(1), "1s")

    def test_format_zero_special_case(self):
        self.assertEqual(format_duration(0), "0s")

    def test_format_multiple_units(self):
        self.assertEqual(format_duration(86400 + 3600), "1d1h")
        self.assertEqual(format_duration(86400 + 3600 + 60), "1d1h1m")
        self.assertEqual(format_duration(86400 + 3600 + 60 + 1), "1d1h1m1s")

    def test_format_omits_zero_components(self):
        self.assertEqual(format_duration(3660), "1h1m")
        self.assertEqual(format_duration(86400 + 60), "1d1m")
        self.assertEqual(format_duration(86400 + 1), "1d1s")
        self.assertEqual(format_duration(3601), "1h1s")

    def test_format_large_values(self):
        self.assertEqual(format_duration(100 * 86400), "100d")
        self.assertEqual(format_duration(1000 * 3600), "41d16h")
        self.assertEqual(format_duration(10000 * 60), "6d22h40m")
        self.assertEqual(format_duration(1000000), "11d13h46m40s")

    def test_format_negative_raises(self):
        with self.assertRaises(ValueError):
            format_duration(-1)
        with self.assertRaises(ValueError):
            format_duration(-100)

    def test_format_complex_values(self):
        self.assertEqual(format_duration(172800), "2d")
        self.assertEqual(format_duration(7200), "2h")
        self.assertEqual(format_duration(120), "2m")
        self.assertEqual(format_duration(2), "2s")

    def test_format_mixed_components(self):
        self.assertEqual(format_duration(7260), "2h1m")
        self.assertEqual(format_duration(90061), "1d1h1m1s")


class TestRoundTrip(unittest.TestCase):

    def test_round_trip_zero(self):
        self.assertEqual(parse_duration(format_duration(0)), 0)

    def test_round_trip_single_units(self):
        for seconds in [86400, 3600, 60, 1]:
            formatted = format_duration(seconds)
            parsed = parse_duration(formatted)
            self.assertEqual(parsed, seconds,
                           f"Round trip failed for {seconds}: {formatted} -> {parsed}")

    def test_round_trip_multiple_units(self):
        for seconds in [93784, 5400, 172800, 86400 + 3600, 3600 + 60 + 1]:
            formatted = format_duration(seconds)
            parsed = parse_duration(formatted)
            self.assertEqual(parsed, seconds,
                           f"Round trip failed for {seconds}: {formatted} -> {parsed}")

    def test_round_trip_range(self):
        test_values = [0, 1, 60, 3600, 86400, 90000, 93784, 1000000]
        for seconds in test_values:
            formatted = format_duration(seconds)
            parsed = parse_duration(formatted)
            self.assertEqual(parsed, seconds,
                           f"Round trip failed for {seconds}")

    def test_non_canonical_parses_to_canonical(self):
        # Non-canonical inputs like "1h90m" parse to the same value as their canonical form
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration(format_duration(9000)), 9000)
        # 2d25h = 48h + 25h = 73h = 262800s, which formats as "3d1h"
        self.assertEqual(parse_duration("2d25h"), 262800)
        self.assertEqual(parse_duration(format_duration(262800)), 262800)


class TestEdgeCases(unittest.TestCase):

    def test_parse_leading_zero_large_numbers(self):
        self.assertEqual(parse_duration("0000001h"), 3600)

    def test_format_preserves_value(self):
        for i in range(0, 100000, 1234):
            formatted = format_duration(i)
            parsed = parse_duration(formatted)
            self.assertEqual(parsed, i)

    def test_parse_all_units(self):
        result = parse_duration("1d2h3m4s")
        expected = 86400 + 7200 + 180 + 4
        self.assertEqual(result, expected)

    def test_parse_maximum_intermediate_values(self):
        self.assertEqual(parse_duration("59d23h59m59s"),
                        59*86400 + 23*3600 + 59*60 + 59)


if __name__ == '__main__':
    unittest.main()
