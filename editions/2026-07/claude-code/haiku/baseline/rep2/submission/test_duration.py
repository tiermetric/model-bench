import unittest
from duration import parse_duration, format_duration


class TestParseDuration(unittest.TestCase):
    """Test parse_duration function."""

    def test_parse_single_unit_seconds(self):
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("1s"), 1)
        self.assertEqual(parse_duration("60s"), 60)

    def test_parse_single_unit_minutes(self):
        self.assertEqual(parse_duration("1m"), 60)
        self.assertEqual(parse_duration("30m"), 1800)
        self.assertEqual(parse_duration("2m"), 120)

    def test_parse_single_unit_hours(self):
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("24h"), 86400)

    def test_parse_single_unit_days(self):
        self.assertEqual(parse_duration("1d"), 86400)
        self.assertEqual(parse_duration("2d"), 172800)

    def test_parse_multiple_units_two(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2d4h"), 187200)

    def test_parse_multiple_units_three(self):
        self.assertEqual(parse_duration("1d2h3m"), 93780)

    def test_parse_multiple_units_four(self):
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)

    def test_parse_large_component(self):
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration("1d25h"), 176400)
        self.assertEqual(parse_duration("1h3661s"), 7261)

    def test_parse_leading_zeros(self):
        self.assertEqual(parse_duration("01h"), 3600)
        self.assertEqual(parse_duration("001s"), 1)
        self.assertEqual(parse_duration("010m"), 600)
        self.assertEqual(parse_duration("0d1h"), 3600)

    def test_parse_zero_components(self):
        self.assertEqual(parse_duration("0s"), 0)
        self.assertEqual(parse_duration("0d"), 0)
        self.assertEqual(parse_duration("0h0m"), 0)

    def test_parse_leading_whitespace(self):
        self.assertEqual(parse_duration("  1h30m"), 5400)

    def test_parse_trailing_whitespace(self):
        self.assertEqual(parse_duration("1h30m  "), 5400)

    def test_parse_leading_and_trailing_whitespace(self):
        self.assertEqual(parse_duration("  1h30m  "), 5400)

    def test_parse_empty_string(self):
        with self.assertRaises(ValueError):
            parse_duration("")

    def test_parse_whitespace_only(self):
        with self.assertRaises(ValueError):
            parse_duration("   ")

    def test_parse_unknown_unit_lowercase(self):
        with self.assertRaises(ValueError):
            parse_duration("1x")

    def test_parse_unknown_unit_other(self):
        with self.assertRaises(ValueError):
            parse_duration("1y")

    def test_parse_uppercase_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("1H")

    def test_parse_uppercase_unit_all(self):
        with self.assertRaises(ValueError):
            parse_duration("1D2H3M4S")

    def test_parse_units_out_of_order(self):
        with self.assertRaises(ValueError):
            parse_duration("30m1h")

    def test_parse_units_out_of_order_minutes_before_seconds(self):
        with self.assertRaises(ValueError):
            parse_duration("30s1m")

    def test_parse_units_out_of_order_days_after_hours(self):
        with self.assertRaises(ValueError):
            parse_duration("1h1d")

    def test_parse_duplicate_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("1h1h")

    def test_parse_duplicate_unit_other_combinations(self):
        with self.assertRaises(ValueError):
            parse_duration("1d2d")
        with self.assertRaises(ValueError):
            parse_duration("30m45m")
        with self.assertRaises(ValueError):
            parse_duration("10s20s")

    def test_parse_bare_number(self):
        with self.assertRaises(ValueError):
            parse_duration("5")

    def test_parse_bare_number_large(self):
        with self.assertRaises(ValueError):
            parse_duration("100")

    def test_parse_unit_without_number(self):
        with self.assertRaises(ValueError):
            parse_duration("h")

    def test_parse_unit_without_number_multiple_units(self):
        with self.assertRaises(ValueError):
            parse_duration("1hm")

    def test_parse_whitespace_inside(self):
        with self.assertRaises(ValueError):
            parse_duration("1h 30m")

    def test_parse_whitespace_inside_multiple_places(self):
        with self.assertRaises(ValueError):
            parse_duration("1 h 30 m")

    def test_parse_negative_number(self):
        with self.assertRaises(ValueError):
            parse_duration("-1h")

    def test_parse_negative_number_other(self):
        with self.assertRaises(ValueError):
            parse_duration("-5s")


class TestFormatDuration(unittest.TestCase):
    """Test format_duration function."""

    def test_format_zero(self):
        self.assertEqual(format_duration(0), "0s")

    def test_format_single_unit_seconds(self):
        self.assertEqual(format_duration(1), "1s")
        self.assertEqual(format_duration(30), "30s")
        self.assertEqual(format_duration(59), "59s")

    def test_format_single_unit_minutes(self):
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(120), "2m")
        self.assertEqual(format_duration(1800), "30m")

    def test_format_single_unit_hours(self):
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(7200), "2h")

    def test_format_single_unit_days(self):
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(172800), "2d")

    def test_format_multiple_units_two(self):
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(90000), "1d1h")

    def test_format_multiple_units_three(self):
        self.assertEqual(format_duration(93780), "1d2h3m")

    def test_format_multiple_units_four(self):
        self.assertEqual(format_duration(93784), "1d2h3m4s")

    def test_format_no_zero_components(self):
        self.assertEqual(format_duration(3661), "1h1m1s")
        self.assertEqual(format_duration(90060), "1d1h1m")

    def test_format_large_values(self):
        self.assertEqual(format_duration(1000000), "11d13h46m40s")

    def test_format_negative_raises(self):
        with self.assertRaises(ValueError):
            format_duration(-1)

    def test_format_negative_large_raises(self):
        with self.assertRaises(ValueError):
            format_duration(-100)

    def test_format_canonical_shortest(self):
        self.assertEqual(format_duration(86400 + 3600), "1d1h")
        self.assertEqual(format_duration(3600 + 60 + 1), "1h1m1s")
        self.assertFalse("60m" in format_duration(3600))
        self.assertFalse("24h" in format_duration(86400))


class TestRoundTrip(unittest.TestCase):
    """Test round-trip property: parse_duration(format_duration(n)) == n"""

    def test_roundtrip_zero(self):
        self.assertEqual(parse_duration(format_duration(0)), 0)

    def test_roundtrip_single_units(self):
        for seconds in [1, 60, 3600, 86400]:
            self.assertEqual(parse_duration(format_duration(seconds)), seconds)

    def test_roundtrip_mixed_values(self):
        test_values = [
            90,      # 1m30s
            5400,    # 1h30m
            93784,   # 1d2h3m4s
            90000,   # 1d1h
            1,       # 1s
            61,      # 1m1s
            3661,    # 1h1m1s
            86401,   # 1d1s
            172800,  # 2d
            1000000,
        ]
        for value in test_values:
            formatted = format_duration(value)
            parsed = parse_duration(formatted)
            self.assertEqual(
                parsed,
                value,
                f"Round-trip failed for {value}: format={formatted}, parse={parsed}",
            )

    def test_roundtrip_wide_range(self):
        for i in range(0, 100000, 1000):
            formatted = format_duration(i)
            parsed = parse_duration(formatted)
            self.assertEqual(parsed, i, f"Round-trip failed at {i}")


class TestNonCanonicalInputs(unittest.TestCase):
    """Test that parse_duration accepts non-canonical inputs."""

    def test_parse_non_canonical_hours_and_minutes(self):
        self.assertEqual(parse_duration("1h90m"), 9000)
        formatted = format_duration(9000)
        self.assertEqual(formatted, "2h30m")

    def test_parse_non_canonical_multiple(self):
        self.assertEqual(parse_duration("0h0m30s"), 30)
        self.assertEqual(parse_duration("0d1h"), 3600)

    def test_parse_non_canonical_then_format(self):
        non_canonical = "1h90m"
        parsed = parse_duration(non_canonical)
        formatted = format_duration(parsed)
        self.assertEqual(formatted, "2h30m")
        self.assertNotEqual(formatted, non_canonical)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""

    def test_parse_large_numbers(self):
        self.assertEqual(parse_duration("999999s"), 999999)
        self.assertEqual(parse_duration("999999d"), 999999 * 86400)

    def test_format_boundary_values(self):
        self.assertEqual(format_duration(59), "59s")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(3599), "59m59s")
        self.assertEqual(format_duration(3600), "1h")

    def test_parse_all_units_max_order(self):
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)
        self.assertEqual(parse_duration("999d999h999m999s"), 999 * 86400 + 999 * 3600 + 999 * 60 + 999)

    def test_format_then_parse_consistency(self):
        for val in [0, 1, 59, 60, 3599, 3600, 86399, 86400, 172800, 1000000]:
            original = val
            formatted = format_duration(original)
            parsed = parse_duration(formatted)
            self.assertEqual(parsed, original)


if __name__ == "__main__":
    unittest.main()
