import unittest

from duration import format_duration, parse_duration


class TestParseDuration(unittest.TestCase):
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
        self.assertEqual(parse_duration("007s"), 7)
        self.assertEqual(parse_duration("00d01h"), 3600)

    def test_unbounded_component_magnitude(self):
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration("100d"), 8640000)
        self.assertEqual(parse_duration("99999s"), 99999)

    def test_all_units_in_order(self):
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)

    def test_leading_trailing_whitespace_ignored(self):
        self.assertEqual(parse_duration("  1h30m  "), 5400)
        self.assertEqual(parse_duration("\t2d\n"), 172800)

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("")

    def test_whitespace_only_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("   ")

    def test_unknown_unit_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("1x")

    def test_uppercase_unit_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("1H")
        with self.assertRaises(ValueError):
            parse_duration("1D")
        with self.assertRaises(ValueError):
            parse_duration("1M")
        with self.assertRaises(ValueError):
            parse_duration("1S")

    def test_units_out_of_order_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("30m1h")
        with self.assertRaises(ValueError):
            parse_duration("1s1d")

    def test_duplicate_unit_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("1h1h")
        with self.assertRaises(ValueError):
            parse_duration("1d2d3h")

    def test_bare_number_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("5")

    def test_unit_with_no_number_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("h")
        with self.assertRaises(ValueError):
            parse_duration("1hm")

    def test_negative_number_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("-1h")

    def test_non_integer_number_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("1.5h")

    def test_internal_whitespace_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("1h 30m")
        with self.assertRaises(ValueError):
            parse_duration("1 h30m")

    def test_trailing_garbage_raises(self):
        with self.assertRaises(ValueError):
            parse_duration("1h!")
        with self.assertRaises(ValueError):
            parse_duration("1hx")


class TestFormatDuration(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(93784), "1d2h3m4s")
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(0), "0s")

    def test_zero_returns_0s(self):
        self.assertEqual(format_duration(0), "0s")

    def test_uses_largest_fitting_units(self):
        self.assertEqual(format_duration(86400), "1d")
        self.assertNotEqual(format_duration(86400), "24h")

    def test_single_unit_values(self):
        self.assertEqual(format_duration(1), "1s")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(86400), "1d")

    def test_omits_zero_components(self):
        self.assertEqual(format_duration(90), "1m30s")
        self.assertEqual(format_duration(3660), "1h1m")
        self.assertEqual(format_duration(86401), "1d1s")

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            format_duration(-1)

    def test_large_values(self):
        self.assertEqual(format_duration(8640000), "100d")
        self.assertEqual(format_duration(93784 + 172800), "3d2h3m4s")


class TestRoundTrip(unittest.TestCase):
    def test_round_trip_small_range(self):
        for seconds in range(0, 200000):
            self.assertEqual(parse_duration(format_duration(seconds)), seconds)

    def test_round_trip_sparse_large_values(self):
        candidates = [
            200000,
            500000,
            999999,
            1000000,
            86400 * 365,
            86400 * 1000 + 3661,
            123456789,
        ]
        for seconds in candidates:
            self.assertEqual(parse_duration(format_duration(seconds)), seconds)

    def test_round_trip_non_canonical_input_normalizes(self):
        self.assertEqual(format_duration(parse_duration("1h90m")), "2h30m")
        self.assertEqual(parse_duration(format_duration(parse_duration("1h90m"))), 9000)


if __name__ == "__main__":
    unittest.main()
