import unittest

from duration import format_duration, parse_duration


class ParseDurationWorkedExamplesTest(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2d"), 172800)
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration("01h"), 3600)


class ParseDurationValidInputTest(unittest.TestCase):
    def test_single_units(self):
        self.assertEqual(parse_duration("1d"), 86400)
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertEqual(parse_duration("1m"), 60)
        self.assertEqual(parse_duration("1s"), 1)

    def test_zero_values(self):
        self.assertEqual(parse_duration("0s"), 0)
        self.assertEqual(parse_duration("0d0h0m0s"), 0)

    def test_leading_zeros(self):
        self.assertEqual(parse_duration("007s"), 7)
        self.assertEqual(parse_duration("00d01h"), 3600)

    def test_unbounded_component_magnitude(self):
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration("100d"), 8640000)
        self.assertEqual(parse_duration("999h"), 999 * 3600)

    def test_all_units_in_order(self):
        self.assertEqual(parse_duration("1d2h3m4s"), 86400 + 2 * 3600 + 3 * 60 + 4)

    def test_subset_of_units_in_order(self):
        self.assertEqual(parse_duration("2d4h"), 2 * 86400 + 4 * 3600)
        self.assertEqual(parse_duration("3m4s"), 3 * 60 + 4)
        self.assertEqual(parse_duration("1d4s"), 86400 + 4)

    def test_leading_and_trailing_whitespace_is_ignored(self):
        self.assertEqual(parse_duration("  1h30m  "), 5400)
        self.assertEqual(parse_duration("\t2d\n"), 172800)


class ParseDurationErrorTest(unittest.TestCase):
    def test_empty_string(self):
        with self.assertRaises(ValueError):
            parse_duration("")

    def test_whitespace_only_string(self):
        with self.assertRaises(ValueError):
            parse_duration("   ")
        with self.assertRaises(ValueError):
            parse_duration("\t\n")

    def test_unknown_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("1x")

    def test_uppercase_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("1H")
        with self.assertRaises(ValueError):
            parse_duration("1D2H")

    def test_bare_number_no_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("5")
        with self.assertRaises(ValueError):
            parse_duration("1h5")

    def test_unit_with_no_number(self):
        with self.assertRaises(ValueError):
            parse_duration("h")
        with self.assertRaises(ValueError):
            parse_duration("1hm")

    def test_negative_number(self):
        with self.assertRaises(ValueError):
            parse_duration("-1h")

    def test_non_integer_number(self):
        with self.assertRaises(ValueError):
            parse_duration("1.5h")

    def test_internal_whitespace(self):
        with self.assertRaises(ValueError):
            parse_duration("1h 30m")
        with self.assertRaises(ValueError):
            parse_duration("1h\t30m")

    def test_units_out_of_order(self):
        with self.assertRaises(ValueError):
            parse_duration("30m1h")
        with self.assertRaises(ValueError):
            parse_duration("1s1d")

    def test_duplicate_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("1h1h")
        with self.assertRaises(ValueError):
            parse_duration("1d2d3h")


class FormatDurationWorkedExamplesTest(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(93784), "1d2h3m4s")
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(0), "0s")


class FormatDurationTest(unittest.TestCase):
    def test_zero_seconds(self):
        self.assertEqual(format_duration(0), "0s")

    def test_single_units(self):
        self.assertEqual(format_duration(1), "1s")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(86400), "1d")

    def test_largest_units_used_never_24h(self):
        self.assertEqual(format_duration(86400), "1d")
        self.assertNotEqual(format_duration(86400), "24h")
        self.assertEqual(format_duration(172800), "2d")

    def test_zero_components_omitted(self):
        self.assertEqual(format_duration(86404), "1d4s")
        self.assertEqual(format_duration(3660), "1h1m")
        self.assertEqual(format_duration(90), "1m30s")

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            format_duration(-1)


class RoundTripTest(unittest.TestCase):
    def test_round_trip_small_range(self):
        for n in range(0, 5000):
            self.assertEqual(parse_duration(format_duration(n)), n)

    def test_round_trip_larger_values(self):
        candidates = [
            86400,
            172800,
            93784,
            5400,
            90,
            9000,
            1,
            59,
            60,
            61,
            3599,
            3600,
            3601,
            86399,
            86401,
            10 ** 6,
            10 ** 7,
            123456789,
        ]
        for n in candidates:
            self.assertEqual(parse_duration(format_duration(n)), n)

    def test_round_trip_non_canonical_inputs_still_parse(self):
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(format_duration(parse_duration("1h90m")), "2h30m")


if __name__ == "__main__":
    unittest.main()
