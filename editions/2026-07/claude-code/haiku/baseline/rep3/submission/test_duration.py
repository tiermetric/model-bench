import unittest
from duration import parse_duration, format_duration


class TestParseDuration(unittest.TestCase):
    """Test parse_duration function."""

    def test_parse_single_units(self):
        """Test parsing single unit durations."""
        self.assertEqual(parse_duration("1s"), 1)
        self.assertEqual(parse_duration("1m"), 60)
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertEqual(parse_duration("1d"), 86400)

    def test_parse_large_single_units(self):
        """Test single units with large numbers."""
        self.assertEqual(parse_duration("60s"), 60)
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("100m"), 6000)
        self.assertEqual(parse_duration("2d"), 172800)

    def test_parse_multiple_units(self):
        """Test parsing multiple unit combinations."""
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2d4h"), 187200)
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)

    def test_parse_all_units(self):
        """Test parsing all four units together."""
        # 1d + 2h + 3m + 4s = 86400 + 7200 + 180 + 4 = 93784
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)

    def test_parse_leading_zeros(self):
        """Test that leading zeros are allowed."""
        self.assertEqual(parse_duration("01h"), 3600)
        self.assertEqual(parse_duration("001m"), 60)
        self.assertEqual(parse_duration("0001s"), 1)
        self.assertEqual(parse_duration("01h02m03s"), 3723)

    def test_parse_zero_components_via_leading_zeros(self):
        """Test components like 01 (which equal 1)."""
        self.assertEqual(parse_duration("01d"), 86400)
        self.assertEqual(parse_duration("010h"), 36000)

    def test_parse_unbounded_component_values(self):
        """Test that component values can exceed unit limits."""
        # 1h + 90m = 3600 + 5400 = 9000
        self.assertEqual(parse_duration("1h90m"), 9000)
        # 2d + 25h = 172800 + 90000 = 262800
        self.assertEqual(parse_duration("2d25h"), 262800)
        # 1h + 90m + 90s = 3600 + 5400 + 90 = 9090
        self.assertEqual(parse_duration("1h90m90s"), 9090)

    def test_parse_leading_trailing_whitespace(self):
        """Test that leading/trailing whitespace is stripped."""
        self.assertEqual(parse_duration("  1h30m  "), 5400)
        self.assertEqual(parse_duration("\t1h\n"), 3600)
        self.assertEqual(parse_duration("  90s  "), 90)

    def test_parse_empty_input(self):
        """Test that empty input raises ValueError."""
        with self.assertRaises(ValueError):
            parse_duration("")

    def test_parse_whitespace_only_input(self):
        """Test that whitespace-only input raises ValueError."""
        with self.assertRaises(ValueError):
            parse_duration("   ")
        with self.assertRaises(ValueError):
            parse_duration("\t\n")

    def test_parse_internal_whitespace(self):
        """Test that internal whitespace raises ValueError."""
        with self.assertRaises(ValueError):
            parse_duration("1h 30m")
        with self.assertRaises(ValueError):
            parse_duration("1h\t30m")
        with self.assertRaises(ValueError):
            parse_duration("1 h")

    def test_parse_unknown_unit(self):
        """Test that unknown units raise ValueError."""
        with self.assertRaises(ValueError):
            parse_duration("1x")
        with self.assertRaises(ValueError):
            parse_duration("1w")
        with self.assertRaises(ValueError):
            parse_duration("1ms")
        with self.assertRaises(ValueError):
            parse_duration("1h30x")

    def test_parse_uppercase_units(self):
        """Test that uppercase units raise ValueError."""
        with self.assertRaises(ValueError):
            parse_duration("1H")
        with self.assertRaises(ValueError):
            parse_duration("1D")
        with self.assertRaises(ValueError):
            parse_duration("1M")
        with self.assertRaises(ValueError):
            parse_duration("1S")
        with self.assertRaises(ValueError):
            parse_duration("1h30M")

    def test_parse_bare_number(self):
        """Test that bare numbers without units raise ValueError."""
        with self.assertRaises(ValueError):
            parse_duration("5")
        with self.assertRaises(ValueError):
            parse_duration("100")
        with self.assertRaises(ValueError):
            parse_duration("1h30")

    def test_parse_bare_unit(self):
        """Test that bare units without numbers raise ValueError."""
        with self.assertRaises(ValueError):
            parse_duration("h")
        with self.assertRaises(ValueError):
            parse_duration("m")
        with self.assertRaises(ValueError):
            parse_duration("1hm")

    def test_parse_non_integer_numbers(self):
        """Test that non-integer numbers raise ValueError."""
        with self.assertRaises(ValueError):
            parse_duration("1.5h")
        with self.assertRaises(ValueError):
            parse_duration("1.5")

    def test_parse_negative_numbers(self):
        """Test that negative numbers raise ValueError."""
        with self.assertRaises(ValueError):
            parse_duration("-1h")
        with self.assertRaises(ValueError):
            parse_duration("1h-30m")

    def test_parse_out_of_order_units(self):
        """Test that units out of order raise ValueError."""
        with self.assertRaises(ValueError):
            parse_duration("30m1h")
        with self.assertRaises(ValueError):
            parse_duration("1m1d")
        with self.assertRaises(ValueError):
            parse_duration("1s1h")
        with self.assertRaises(ValueError):
            parse_duration("1m1h1d")

    def test_parse_duplicate_units(self):
        """Test that duplicate units raise ValueError."""
        with self.assertRaises(ValueError):
            parse_duration("1h1h")
        with self.assertRaises(ValueError):
            parse_duration("1h30m30m")
        with self.assertRaises(ValueError):
            parse_duration("1d1d")
        with self.assertRaises(ValueError):
            parse_duration("5s5s")

    def test_parse_complex_duplicate_order(self):
        """Test combinations of order and duplicate violations."""
        # Duplicate before order check might matter
        with self.assertRaises(ValueError):
            parse_duration("1h1h30m")


class TestFormatDuration(unittest.TestCase):
    """Test format_duration function."""

    def test_format_single_units(self):
        """Test formatting single unit values."""
        self.assertEqual(format_duration(1), "1s")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(86400), "1d")

    def test_format_large_single_units(self):
        """Test formatting large values in single units."""
        self.assertEqual(format_duration(90), "1m30s")
        self.assertEqual(format_duration(172800), "2d")
        self.assertEqual(format_duration(5400), "1h30m")

    def test_format_multiple_units(self):
        """Test formatting multiple unit combinations."""
        self.assertEqual(format_duration(93784), "1d2h3m4s")
        self.assertEqual(format_duration(187200), "2d4h")

    def test_format_zero(self):
        """Test that zero returns "0s"."""
        self.assertEqual(format_duration(0), "0s")

    def test_format_omits_zero_components(self):
        """Test that zero components are omitted."""
        # 1d + 0h + 3m + 4s
        self.assertEqual(format_duration(86400 + 0 + 180 + 4), "1d3m4s")
        # 1h + 0m + 30s
        self.assertEqual(format_duration(3600 + 30), "1h30s")
        # 2d + 0h + 0m + 0s
        self.assertEqual(format_duration(86400 * 2), "2d")
        # 0d + 1h + 0m + 0s
        self.assertEqual(format_duration(3600), "1h")

    def test_format_uses_largest_units(self):
        """Test that the largest fitting units are always used."""
        # 24h should be 1d, not "24h"
        self.assertEqual(format_duration(86400), "1d")
        # 60m should be 1h, not "60m"
        self.assertEqual(format_duration(3600), "1h")
        # 60s should be 1m, not "60s"
        self.assertEqual(format_duration(60), "1m")

    def test_format_compound_values(self):
        """Test formatting compound values that span multiple units."""
        # 1h + 30m + 45s
        self.assertEqual(format_duration(3600 + 1800 + 45), "1h30m45s")
        # 2d + 5h + 15m
        self.assertEqual(format_duration(172800 + 18000 + 900), "2d5h15m")
        # 2d + 23h + 56m
        self.assertEqual(format_duration(258960), "2d23h56m")

    def test_format_negative_raises_error(self):
        """Test that negative input raises ValueError."""
        with self.assertRaises(ValueError):
            format_duration(-1)
        with self.assertRaises(ValueError):
            format_duration(-100)

    def test_format_large_values(self):
        """Test formatting large duration values."""
        # 365 days
        self.assertEqual(format_duration(365 * 86400), "365d")
        # 100 days + 10 hours
        self.assertEqual(format_duration(100 * 86400 + 10 * 3600), "100d10h")


class TestRoundTrip(unittest.TestCase):
    """Test round-trip property: parse(format(n)) == n for all n >= 0."""

    def test_round_trip_zero(self):
        """Test round-trip for zero."""
        n = 0
        self.assertEqual(parse_duration(format_duration(n)), n)

    def test_round_trip_single_units(self):
        """Test round-trip for values in single units."""
        for value in [1, 30, 60, 90, 3600, 86400]:
            with self.subTest(value=value):
                self.assertEqual(parse_duration(format_duration(value)), value)

    def test_round_trip_multi_unit_values(self):
        """Test round-trip for multi-unit values."""
        test_values = [
            5400,      # 1h30m
            93784,     # 1d2h3m4s
            187200,    # 2d4h
            262800,    # 3d1h
            1,         # 1s
            60,        # 1m
            3600,      # 1h
            86400,     # 1d
        ]
        for value in test_values:
            with self.subTest(value=value):
                formatted = format_duration(value)
                parsed = parse_duration(formatted)
                self.assertEqual(parsed, value)

    def test_round_trip_comprehensive_range(self):
        """Test round-trip for a comprehensive range of values."""
        # Test a range of values up to 10 days
        for seconds in range(0, 864000, 1337):  # Prime-ish step to hit varied combinations
            with self.subTest(seconds=seconds):
                self.assertEqual(parse_duration(format_duration(seconds)), seconds)

    def test_round_trip_edge_cases(self):
        """Test round-trip for edge cases."""
        edge_cases = [
            0,
            1,
            59,
            60,
            61,
            3599,
            3600,
            3601,
            86399,
            86400,
            86401,
            1000000,
        ]
        for value in edge_cases:
            with self.subTest(value=value):
                self.assertEqual(parse_duration(format_duration(value)), value)


class TestWorkedExamples(unittest.TestCase):
    """Test all worked examples from the specification."""

    def test_parse_examples(self):
        """Test parse_duration worked examples."""
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2d"), 172800)
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration("01h"), 3600)

    def test_parse_error_examples(self):
        """Test parse_duration error examples."""
        with self.assertRaises(ValueError):
            parse_duration("")
        with self.assertRaises(ValueError):
            parse_duration("1x")
        with self.assertRaises(ValueError):
            parse_duration("1H")
        with self.assertRaises(ValueError):
            parse_duration("30m1h")
        with self.assertRaises(ValueError):
            parse_duration("1h1h")
        with self.assertRaises(ValueError):
            parse_duration("5")

    def test_format_examples(self):
        """Test format_duration worked examples."""
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(93784), "1d2h3m4s")
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(0), "0s")

    def test_format_error_example(self):
        """Test format_duration error example."""
        with self.assertRaises(ValueError):
            format_duration(-1)


if __name__ == '__main__':
    unittest.main()
