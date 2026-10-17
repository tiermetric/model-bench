import unittest
from duration import parse_duration, format_duration


class TestParseDuration(unittest.TestCase):

    def test_parse_single_units(self):
        self.assertEqual(parse_duration("1s"), 1)
        self.assertEqual(parse_duration("1m"), 60)
        self.assertEqual(parse_duration("1h"), 3600)
        self.assertEqual(parse_duration("1d"), 86400)

    def test_parse_multiple_units(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2d"), 172800)
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)
        self.assertEqual(parse_duration("90s"), 90)

    def test_parse_unordered_components_allowed_if_ordered(self):
        self.assertEqual(parse_duration("1d2h"), 93600)
        self.assertEqual(parse_duration("1h30m10s"), 5410)

    def test_parse_unbounded_magnitudes(self):
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration("1d48h"), 259200)
        self.assertEqual(parse_duration("10h120m"), 43200)

    def test_parse_leading_zeros(self):
        self.assertEqual(parse_duration("01h"), 3600)
        self.assertEqual(parse_duration("001d"), 86400)
        self.assertEqual(parse_duration("00s"), 0)
        self.assertEqual(parse_duration("001h030m"), 3600 + 1800)

    def test_parse_large_numbers(self):
        self.assertEqual(parse_duration("9999s"), 9999)
        self.assertEqual(parse_duration("1000d"), 86400000)

    def test_parse_whitespace_handling(self):
        self.assertEqual(parse_duration("  1h30m  "), 5400)
        self.assertEqual(parse_duration("\t2d\n"), 172800)

    def test_parse_empty_or_whitespace_only(self):
        with self.assertRaises(ValueError):
            parse_duration("")
        with self.assertRaises(ValueError):
            parse_duration("   ")
        with self.assertRaises(ValueError):
            parse_duration("\t")

    def test_parse_unknown_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("1x")
        with self.assertRaises(ValueError):
            parse_duration("5q")
        with self.assertRaises(ValueError):
            parse_duration("1y")

    def test_parse_uppercase_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("1H")
        with self.assertRaises(ValueError):
            parse_duration("1D2h")
        with self.assertRaises(ValueError):
            parse_duration("1h30M")
        with self.assertRaises(ValueError):
            parse_duration("1h30m4S")

    def test_parse_bare_number_no_unit(self):
        with self.assertRaises(ValueError):
            parse_duration("5")
        with self.assertRaises(ValueError):
            parse_duration("30")
        with self.assertRaises(ValueError):
            parse_duration("1h30")

    def test_parse_unit_no_number(self):
        with self.assertRaises(ValueError):
            parse_duration("h")
        with self.assertRaises(ValueError):
            parse_duration("s")
        with self.assertRaises(ValueError):
            parse_duration("1h30m s")

    def test_parse_whitespace_inside_string(self):
        with self.assertRaises(ValueError):
            parse_duration("1h 30m")
        with self.assertRaises(ValueError):
            parse_duration("1h\t30m")
        with self.assertRaises(ValueError):
            parse_duration("1 h30m")
        with self.assertRaises(ValueError):
            parse_duration("1h 30 m")

    def test_parse_units_out_of_order(self):
        with self.assertRaises(ValueError):
            parse_duration("30m1h")
        with self.assertRaises(ValueError):
            parse_duration("1s1h")
        with self.assertRaises(ValueError):
            parse_duration("1m1d")
        with self.assertRaises(ValueError):
            parse_duration("5s2m1h3d")

    def test_parse_duplicate_units(self):
        with self.assertRaises(ValueError):
            parse_duration("1h1h")
        with self.assertRaises(ValueError):
            parse_duration("1h30m30m")
        with self.assertRaises(ValueError):
            parse_duration("1d1d")
        with self.assertRaises(ValueError):
            parse_duration("5s10s")

    def test_parse_negative_numbers(self):
        with self.assertRaises(ValueError):
            parse_duration("-1h")
        with self.assertRaises(ValueError):
            parse_duration("1h-30m")

    def test_parse_non_integer_numbers(self):
        with self.assertRaises(ValueError):
            parse_duration("1.5h")
        with self.assertRaises(ValueError):
            parse_duration("1h30.5m")
        with self.assertRaises(ValueError):
            parse_duration("0.5s")

    def test_parse_special_characters(self):
        with self.assertRaises(ValueError):
            parse_duration("1h+30m")
        with self.assertRaises(ValueError):
            parse_duration("(1h)")
        with self.assertRaises(ValueError):
            parse_duration("1h,30m")

    def test_parse_zero_components(self):
        self.assertEqual(parse_duration("0s"), 0)
        self.assertEqual(parse_duration("0h"), 0)
        self.assertEqual(parse_duration("0d"), 0)
        self.assertEqual(parse_duration("0d0h0m0s"), 0)


class TestFormatDuration(unittest.TestCase):

    def test_format_single_units(self):
        self.assertEqual(format_duration(1), "1s")
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(86400), "1d")

    def test_format_multiple_units(self):
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(93784), "1d2h3m4s")
        self.assertEqual(format_duration(172800), "2d")

    def test_format_largest_units_first(self):
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(90000), "1d1h")
        self.assertEqual(format_duration(93784), "1d2h3m4s")

    def test_format_omits_zero_components(self):
        self.assertEqual(format_duration(3661), "1h1m1s")
        self.assertEqual(format_duration(3600), "1h")
        self.assertEqual(format_duration(90), "1m30s")
        self.assertEqual(format_duration(7200), "2h")

    def test_format_zero(self):
        self.assertEqual(format_duration(0), "0s")

    def test_format_negative_raises(self):
        with self.assertRaises(ValueError):
            format_duration(-1)
        with self.assertRaises(ValueError):
            format_duration(-3600)

    def test_format_large_values(self):
        self.assertEqual(format_duration(1000000), "11d13h46m40s")
        self.assertEqual(format_duration(86400000), "1000d")

    def test_format_canonical_shortest(self):
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(5430), "1h30m30s")
        self.assertEqual(format_duration(86464), "1d1m4s")

    def test_format_composite_edge_cases(self):
        self.assertEqual(format_duration(60), "1m")
        self.assertEqual(format_duration(61), "1m1s")
        self.assertEqual(format_duration(120), "2m")
        self.assertEqual(format_duration(3660), "1h1m")


class TestRoundTrip(unittest.TestCase):

    def test_round_trip_basic_values(self):
        for seconds in [0, 1, 60, 3600, 86400]:
            formatted = format_duration(seconds)
            parsed = parse_duration(formatted)
            self.assertEqual(parsed, seconds,
                           f"Round-trip failed for {seconds}: {formatted} -> {parsed}")

    def test_round_trip_composite_values(self):
        test_values = [
            5400,
            93784,
            172800,
            90,
            9000,
            3661,
            1000000,
            86464,
            123456,
        ]
        for seconds in test_values:
            formatted = format_duration(seconds)
            parsed = parse_duration(formatted)
            self.assertEqual(parsed, seconds,
                           f"Round-trip failed for {seconds}: {formatted} -> {parsed}")

    def test_round_trip_all_single_units(self):
        for unit_seconds, unit_name in [(86400, 'd'), (3600, 'h'), (60, 'm'), (1, 's')]:
            for multiplier in range(1, 11):
                seconds = unit_seconds * multiplier
                formatted = format_duration(seconds)
                parsed = parse_duration(formatted)
                self.assertEqual(parsed, seconds,
                               f"Round-trip failed for {seconds}s ({multiplier}{unit_name})")

    def test_round_trip_random_combinations(self):
        combinations = [
            (1, 0, 0, 0),
            (0, 1, 0, 0),
            (0, 0, 1, 0),
            (0, 0, 0, 1),
            (1, 1, 1, 1),
            (2, 5, 10, 30),
            (7, 12, 45, 59),
            (0, 23, 59, 59),
        ]
        for days, hours, minutes, seconds in combinations:
            total = days * 86400 + hours * 3600 + minutes * 60 + seconds
            formatted = format_duration(total)
            parsed = parse_duration(formatted)
            self.assertEqual(parsed, total,
                           f"Round-trip failed for {days}d{hours}h{minutes}m{seconds}s")

    def test_round_trip_range(self):
        for seconds in range(0, 604800, 3661):
            formatted = format_duration(seconds)
            parsed = parse_duration(formatted)
            self.assertEqual(parsed, seconds,
                           f"Round-trip failed for {seconds}")


class TestWorkedExamples(unittest.TestCase):

    def test_parse_examples(self):
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("2d"), 172800)
        self.assertEqual(parse_duration("1d2h3m4s"), 93784)
        self.assertEqual(parse_duration("90s"), 90)
        self.assertEqual(parse_duration("1h90m"), 9000)
        self.assertEqual(parse_duration("01h"), 3600)

    def test_parse_error_examples(self):
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
        self.assertEqual(format_duration(5400), "1h30m")
        self.assertEqual(format_duration(93784), "1d2h3m4s")
        self.assertEqual(format_duration(86400), "1d")
        self.assertEqual(format_duration(0), "0s")

    def test_format_error_examples(self):
        with self.assertRaises(ValueError):
            format_duration(-1)


if __name__ == '__main__':
    unittest.main()
