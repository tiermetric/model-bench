import unittest

from duration import format_duration, parse_duration


class ParseDurationValidTests(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2d"), 172800)
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration("01h"), 3600)

    def test_single_units(self):
        self.assertEqual(parse_duration("1d"), 86400)
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertEqual(parse_duration("1m"), 60)
        self.assertEqual(parse_duration("1s"), 1)

    def test_zero_values(self):
        self.assertEqual(parse_duration("0s"), 0)
        self.assertEqual(parse_duration("0d0h0m0s"), 0)

    def test_leading_zeros(self):
        self.assertEqual(parse_duration("007d"), 7 * 86400)
        self.assertEqual(parse_duration("00s"), 0)

    def test_unbounded_component_magnitude(self):
        self.assertEqual(parse_duration("100h"), 360000)
        self.assertEqual(parse_duration("999999s"), 999999)

    def test_all_units_in_order(self):
        self.assertEqual(parse_duration("1d2h3m4s"), 86400 + 2 * 3600 + 3 * 60 + 4)

    def test_partial_unit_subsets_in_order(self):
        self.assertEqual(parse_duration("2d5m"), 2 * 86400 + 5 * 60)
        self.assertEqual(parse_duration("2d4s"), 2 * 86400 + 4)
        self.assertEqual(parse_duration("3h4s"), 3 * 3600 + 4)

    def test_surrounding_whitespace_ignored(self):
        self.assertEqual(parse_duration("  1h30m  "), 5400)
        self.assertEqual(parse_duration("\t2d\n"), 172800)


class ParseDurationInvalidTests(unittest.TestCase):
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

    def test_trailing_bare_number(self):
        with self.assertRaises(ValueError):
            parse_duration("1h30")

    def test_negative_number(self):
        with self.assertRaises(ValueError):
            parse_duration("-1h")

    def test_non_integer_number(self):
        with self.assertRaises(ValueError):
            parse_duration("1.5h")

    def test_internal_whitespace(self):
        with self.assertRaises(ValueError):
            parse_duration("1h 30m")

    def test_internal_whitespace_between_all_segments(self):
        with self.assertRaises(ValueError):
            parse_duration("1d 2h 3m 4s")

    def test_duplicate_smallest_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("1s1s")

    def test_out_of_order_all_units(self):
        with self.assertRaises(ValueError):
            parse_duration("1s1m1h1d")

    def test_unknown_trailing_characters(self):
        with self.assertRaises(ValueError):
            parse_duration("1h!")

    def test_stray_characters_between_segments(self):
        with self.assertRaises(ValueError):
            parse_duration("1h,2m")

    def test_unit_only_no_digits_anywhere(self):
        with self.assertRaises(ValueError):
            parse_duration("dhms")


class FormatDurationTests(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(93784), "1d2h3m4s")
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(0), "0s")

    def test_single_units(self):
        self.assertEqual(format_duration(1), "1s")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(86400), "1d")

    def test_omits_zero_components(self):
        self.assertEqual(format_duration(3660), "1h1m")
        self.assertEqual(format_duration(90000), "1d1h")
        self.assertEqual(format_duration(61), "1m1s")

    def test_uses_largest_fitting_units(self):
        # never render as smaller units when a larger one divides evenly
        self.assertEqual(format_duration(172800), "2d")
        self.assertEqual(format_duration(7200), "2h")
        self.assertEqual(format_duration(120), "2m")

    def test_all_components_present(self):
        self.assertEqual(format_duration(93784), "1d2h3m4s")

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            format_duration(-1)

    def test_large_negative_raises(self):
        with self.assertRaises(ValueError):
            format_duration(-100000)


class RoundTripTests(unittest.TestCase):
    def test_round_trip_small_range(self):
        for n in range(0, 10000):
            self.assertEqual(parse_duration(format_duration(n)), n)

    def test_round_trip_sparse_large_values(self):
        candidates = [
            10000, 86399, 86400, 86401, 172800, 200000,
            1000000, 123456789, 999999999,
        ]
        for n in candidates:
            self.assertEqual(parse_duration(format_duration(n)), n)

    def test_round_trip_step_sample(self):
        for n in range(0, 1000000, 977):
            self.assertEqual(parse_duration(format_duration(n)), n)

    def test_non_canonical_input_still_parses_to_same_value_as_canonical(self):
        self.assertEqual(parse_duration("1h90m"), parse_duration(format_duration(9000)))
        self.assertEqual(format_duration(parse_duration("1h90m")), "2h30m")


if __name__ == "__main__":
    unittest.main()
