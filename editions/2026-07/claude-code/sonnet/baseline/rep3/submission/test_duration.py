import unittest

from duration import parse_duration, format_duration


class ParseDurationWorkedExamples(unittest.TestCase):
    def test_1h30m(self):
        self.assertEqual(parse_duration("1h30m"), 5400)

    def test_2d(self):
        self.assertEqual(parse_duration("2d"), 172800)

    def test_1d2h3m4s(self):
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)

    def test_90s(self):
        self.assertEqual(parse_duration("90s"), 90)

    def test_1h90m_unbounded_component(self):
        self.assertEqual(parse_duration("1h90m"), 9000)

    def test_leading_zeros(self):
        self.assertEqual(parse_duration("01h"), 3600)


class ParseDurationValidComponents(unittest.TestCase):
    def test_single_units(self):
        self.assertEqual(parse_duration("1d"), 86400)
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertEqual(parse_duration("1m"), 60)
        self.assertEqual(parse_duration("1s"), 1)

    def test_zero_valued_components(self):
        self.assertEqual(parse_duration("0s"), 0)
        self.assertEqual(parse_duration("0d0h0m0s"), 0)

    def test_all_four_units_in_order(self):
        self.assertEqual(parse_duration("1d1h1m1s"), 86400 + 3600 + 60 + 1)

    def test_partial_subsets_in_order(self):
        self.assertEqual(parse_duration("2d3m"), 172800 + 180)
        self.assertEqual(parse_duration("2h5s"), 7205)

    def test_large_numbers_no_upper_bound(self):
        self.assertEqual(parse_duration("1000000s"), 1000000)

    def test_leading_and_trailing_whitespace_ignored(self):
        self.assertEqual(parse_duration("  1h30m  "), 5400)
        self.assertEqual(parse_duration("\t2d\n"), 172800)


class ParseDurationErrors(unittest.TestCase):
    def test_empty_string(self):
        with self.assertRaises(ValueError):
            parse_duration("")

    def test_whitespace_only_string(self):
        with self.assertRaises(ValueError):
            parse_duration("   ")

    def test_unknown_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("1x")

    def test_uppercase_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("1H")

    def test_units_out_of_order(self):
        with self.assertRaises(ValueError):
            parse_duration("30m1h")

    def test_duplicate_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("1h1h")

    def test_bare_number_no_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("5")

    def test_unit_with_no_number(self):
        with self.assertRaises(ValueError):
            parse_duration("h")

    def test_non_integer_number(self):
        with self.assertRaises(ValueError):
            parse_duration("1.5h")

    def test_negative_number(self):
        with self.assertRaises(ValueError):
            parse_duration("-1h")

    def test_internal_whitespace(self):
        with self.assertRaises(ValueError):
            parse_duration("1h 30m")

    def test_duplicate_unit_non_adjacent(self):
        with self.assertRaises(ValueError):
            parse_duration("1d1h1d")

    def test_trailing_garbage(self):
        with self.assertRaises(ValueError):
            parse_duration("1h!")

    def test_unit_appearing_before_larger_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("1s1d")


class FormatDurationWorkedExamples(unittest.TestCase):
    def test_5400(self):
        self.assertEqual(format_duration(5400), "1h30m")

    def test_93784(self):
        self.assertEqual(format_duration(93784), "1d2h3m4s")

    def test_86400_uses_days_not_hours(self):
        self.assertEqual(format_duration(86400), "1d")

    def test_zero(self):
        self.assertEqual(format_duration(0), "0s")

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            format_duration(-1)


class FormatDurationBehavior(unittest.TestCase):
    def test_omits_zero_components(self):
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(1), "1s")

    def test_prefers_largest_units(self):
        self.assertEqual(format_duration(172800), "2d")
        self.assertEqual(format_duration(90), "1m30s")

    def test_component_values_can_exceed_typical_bounds(self):
        # 90 minutes worth of seconds still collapses upward to hours+minutes
        self.assertEqual(format_duration(5400), "1h30m")


class RoundTripProperty(unittest.TestCase):
    def test_round_trip_small_range(self):
        for n in range(0, 200000):
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_round_trip_sparse_large_values(self):
        candidates = [
            0, 1, 59, 60, 61, 3599, 3600, 3601,
            86399, 86400, 86401,
            172800, 90000, 93784, 9000,
            10 ** 6, 10 ** 7, 2 ** 31, 2 ** 32,
            123456789,
        ]
        for n in candidates:
            with self.subTest(n=n):
                self.assertEqual(parse_duration(format_duration(n)), n)

    def test_format_output_is_parseable_and_canonical_order(self):
        formatted = format_duration(93784)
        # canonical ordering must itself be valid input to parse_duration
        self.assertEqual(parse_duration(formatted), 93784)


if __name__ == "__main__":
    unittest.main()
