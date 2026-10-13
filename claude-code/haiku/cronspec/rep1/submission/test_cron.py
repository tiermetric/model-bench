import unittest
from datetime import datetime
from cron import next_fire


class TestCronBasics(unittest.TestCase):
    """Test basic cron functionality."""

    def test_asterisk_all_fields(self):
        """Test * in all fields matches every minute."""
        result = next_fire("* * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 1))

    def test_next_minute(self):
        """Test that result is strictly after the input."""
        result = next_fire("30 9 * * *", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 22, 9, 30))

    def test_step_minutes(self):
        """Test step notation in minutes."""
        result = next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 15))


class TestDeviation1DayBothRestricted(unittest.TestCase):
    """Test Deviation 1: day-of-month AND day-of-week is an INTERSECTION."""

    def test_friday_13th(self):
        """Friday the 13th: must be BOTH 13th AND Friday."""
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 13, 0, 0))

    def test_first_and_monday(self):
        """1st AND Monday."""
        result = next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0))
        self.assertEqual(result, datetime(2027, 2, 1, 0, 0))

    def test_monday_restricted_dom_unrestricted(self):
        """Day-of-week restricted, day-of-month unrestricted."""
        result = next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 27, 9, 30))


class TestDeviation2RangeStep(unittest.TestCase):
    """Test Deviation 2: step is anchored to range START, not to 0."""

    def test_5_30_step_10(self):
        """5-30/10 should be 5, 15, 25 (not 10, 20, 30)."""
        result = next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 6, 1, 12, 5))

        result = next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5))
        self.assertEqual(result, datetime(2026, 6, 1, 12, 15))

    def test_star_step_15(self):
        """*/15 on minute starts from 0."""
        result = next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 15))


class TestDeviation3StrictDomain(unittest.TestCase):
    """Test Deviation 3: strict domain and strict rejection."""

    def test_dow_7_invalid(self):
        """dow 7 is INVALID (not Sunday like standard cron)."""
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1, 0, 0))

    def test_uppercase_month_name_invalid(self):
        """Month names must be lowercase."""
        with self.assertRaises(ValueError):
            next_fire("* * * JAN *", datetime(2026, 1, 1, 0, 0))

    def test_uppercase_dow_name_invalid(self):
        """Day-of-week names must be lowercase."""
        with self.assertRaises(ValueError):
            next_fire("* * * * SUN", datetime(2026, 1, 1, 0, 0))


class TestRule4WrapRange(unittest.TestCase):
    """Test Rule 4: ranges WRAP when start > end."""

    def test_minute_wrap_50_to_10(self):
        """minute 50-10 wraps to {50,51,...,59,0,1,...,10}."""
        result = next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 50))

        result = next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 55))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 56))

    def test_month_wrap_nov_to_feb(self):
        """month nov-feb wraps to {11,12,1,2}."""
        result = next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 11, 1, 0, 0))

    def test_dow_wrap_fri_to_mon(self):
        """dow fri-mon wraps to {5,6,0,1}."""
        result = next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))


class TestRule5StepThroughWrap(unittest.TestCase):
    """Test Rule 5: step over wrapped range counts through the wrap."""

    def test_minute_wrap_step_50_10_5(self):
        """minute 50-10/5: wrapped order is 50,...,59,0,...,10; every 5th -> {50,55,0,5,10}."""
        result = next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 5))

    def test_dow_wrap_step_fri_tue_2(self):
        """dow fri-tue/2: wrapped order fri,sat,sun,mon,tue; every 2nd -> {fri,sun,tue}."""
        result = next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))


class TestRule6LastDayOfMonth(unittest.TestCase):
    """Test Rule 6: L = last day of month."""

    def test_L_jan_31(self):
        """L in January should match 31st."""
        result = next_fire("0 0 L * *", datetime(2026, 1, 15, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 31, 0, 0))

    def test_L_feb_28_non_leap(self):
        """L in February 2026 (non-leap) should match 28th."""
        result = next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 28, 0, 0))

    def test_L_feb_29_leap(self):
        """L in February 2028 (leap) should match 29th."""
        result = next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2028, 2, 29, 0, 0))

    def test_L_and_dow_intersection(self):
        """L with dow restriction: last day that is also a Friday."""
        result = next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 31, 0, 0))


class TestRule7MonthLengthLeap(unittest.TestCase):
    """Test Rule 7: month lengths and leap years."""

    def test_31st_skips_30_day_months(self):
        """31st skips April (30 days)."""
        result = next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 5, 31, 0, 0))

    def test_30th_skips_february(self):
        """30th skips February (28/29 days)."""
        result = next_fire("0 0 30 * *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 3, 30, 0, 0))

    def test_29_feb_leap_only(self):
        """29 Feb only matches leap years."""
        result = next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2028, 2, 29, 0, 0))


class TestErrorCases(unittest.TestCase):
    """Test error handling."""

    def test_empty_expression(self):
        """Empty expression raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("", datetime(2026, 1, 1, 0, 0))

    def test_too_few_fields(self):
        """4 fields raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * * *", datetime(2026, 1, 1, 0, 0))

    def test_too_many_fields(self):
        """6 fields raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * * * * *", datetime(2026, 1, 1, 0, 0))

    def test_minute_out_of_domain(self):
        """60 minute raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("60 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_hour_out_of_domain(self):
        """24 hour raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* 24 * * *", datetime(2026, 1, 1, 0, 0))

    def test_dom_0_out_of_domain(self):
        """0 day-of-month raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * 0 * *", datetime(2026, 1, 1, 0, 0))

    def test_dom_32_out_of_domain(self):
        """32 day-of-month raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * 32 * *", datetime(2026, 1, 1, 0, 0))

    def test_month_13_out_of_domain(self):
        """13 month raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * * 13 *", datetime(2026, 1, 1, 0, 0))

    def test_dow_7_invalid(self):
        """dow 7 raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1, 0, 0))

    def test_uppercase_name_error(self):
        """Uppercase names raise ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * * * SUN", datetime(2026, 1, 1, 0, 0))

    def test_step_zero(self):
        """*/0 raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("*/0 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_step_negative(self):
        """/−1 raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("*/-1 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_step_on_single_value(self):
        """5/10 (step on single value) raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("5/10 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_empty_list_element(self):
        """1,,3 (empty list element) raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("1,,3 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_invalid_token(self):
        """1x (unknown token) raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("1x * * * *", datetime(2026, 1, 1, 0, 0))

    def test_wrap_endpoint_out_of_domain_start(self):
        """70-10 (start out of domain) raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("70-10 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_wrap_endpoint_out_of_domain_end(self):
        """50-70 (end out of domain) raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("50-70 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_L_in_list(self):
        """L,15 (L in list) raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("0 0 L,15 * *", datetime(2026, 1, 1, 0, 0))

    def test_L_in_range(self):
        """L-15 (L in range) should raise ValueError."""
        with self.assertRaises(ValueError):
            next_fire("0 0 L-15 * *", datetime(2026, 1, 1, 0, 0))

    def test_L_in_step(self):
        """L/5 (L in step) should raise ValueError."""
        with self.assertRaises(ValueError):
            next_fire("0 0 L/5 * *", datetime(2026, 1, 1, 0, 0))

    def test_L_only_in_dom_field(self):
        """L in hour field raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("0 L * * *", datetime(2026, 1, 1, 0, 0))

    def test_L_only_in_dow_field(self):
        """L in day-of-week field raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("0 0 * * L", datetime(2026, 1, 1, 0, 0))

    def test_lowercase_l_not_L(self):
        """lowercase l is not L."""
        with self.assertRaises(ValueError):
            next_fire("0 0 l * *", datetime(2026, 1, 1, 0, 0))


class TestWorkedExamples(unittest.TestCase):
    """Test all worked examples from the spec."""

    def test_star_step_15(self):
        result = next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 15))

    def test_5_30_step_10_first(self):
        result = next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 6, 1, 12, 5))

    def test_5_30_step_10_second(self):
        result = next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5))
        self.assertEqual(result, datetime(2026, 6, 1, 12, 15))

    def test_friday_13th_worked(self):
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 13, 0, 0))

    def test_1st_monday_worked(self):
        result = next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0))
        self.assertEqual(result, datetime(2027, 2, 1, 0, 0))

    def test_monday_930_worked(self):
        result = next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 27, 9, 30))

    def test_noon_1st_jan_jul_worked(self):
        result = next_fire("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 1, 12, 0))

    def test_minute_wrap_50_10_worked(self):
        result = next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 50))

    def test_minute_wrap_50_10_second_worked(self):
        result = next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 55))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 56))

    def test_month_wrap_nov_feb_worked(self):
        result = next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 11, 1, 0, 0))

    def test_dow_wrap_fri_mon_worked(self):
        result = next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))

    def test_minute_step_wrap_50_10_5_worked(self):
        result = next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 5))

    def test_dow_step_wrap_fri_tue_2_worked(self):
        result = next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))

    def test_L_jan_31_worked(self):
        result = next_fire("0 0 L * *", datetime(2026, 1, 15, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 31, 0, 0))

    def test_L_feb_28_worked(self):
        result = next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 28, 0, 0))

    def test_L_feb_29_leap_worked(self):
        result = next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2028, 2, 29, 0, 0))

    def test_L_fri_worked(self):
        result = next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 31, 0, 0))

    def test_31st_skip_april_worked(self):
        result = next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 5, 31, 0, 0))

    def test_30th_skip_february_worked(self):
        result = next_fire("0 0 30 * *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 3, 30, 0, 0))

    def test_29_feb_leap_worked(self):
        result = next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2028, 2, 29, 0, 0))


class TestEdgeCases(unittest.TestCase):
    """Test various edge cases."""

    def test_result_has_zero_seconds_microseconds(self):
        """Result should have second=0, microsecond=0."""
        result = next_fire("30 9 * * *", datetime(2026, 7, 22, 15, 30, 45, 123456))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)

    def test_strictly_after_on_boundary(self):
        """Even if input is exact minute boundary, result is next match."""
        result = next_fire("30 9 * * *", datetime(2026, 7, 22, 9, 30, 0, 0))
        self.assertNotEqual(result, datetime(2026, 7, 22, 9, 30, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 23, 9, 30, 0, 0))

    def test_comma_list(self):
        """Comma-separated list of values."""
        result = next_fire("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 1, 12, 0))

    def test_dow_range_non_wrap(self):
        """Day-of-week range without wrap."""
        result = next_fire("0 0 * * mon-wed", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 27, 0, 0))

    def test_multiple_month_list(self):
        """Multiple months in list."""
        result = next_fire("0 0 1 1,6,12 *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 6, 1, 0, 0))

    def test_whitespace_handling(self):
        """Leading/trailing whitespace in expression."""
        result = next_fire("  * * * * *  ", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 1))

    def test_multiple_spaces_between_fields(self):
        """Multiple spaces between fields."""
        result = next_fire("*  *  *  *  *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 1))

    def test_hour_range(self):
        """Hour range."""
        result = next_fire("0 8-17 * * *", datetime(2026, 7, 22, 7, 59))
        self.assertEqual(result, datetime(2026, 7, 22, 8, 0))

    def test_dom_range(self):
        """Day-of-month range."""
        result = next_fire("0 0 10-20 * *", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 8, 10, 0, 0))

    def test_specific_minute_hour_dom(self):
        """All specific values."""
        result = next_fire("30 9 15 * *", datetime(2026, 7, 8, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 15, 9, 30))

    def test_crossing_year_boundary(self):
        """Crossing from one year to next."""
        result = next_fire("0 0 1 1 *", datetime(2026, 12, 31, 23, 59))
        self.assertEqual(result, datetime(2027, 1, 1, 0, 0))

    def test_february_leap_year_transition(self):
        """Transition across leap year boundaries."""
        result = next_fire("0 0 29 2 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2028, 2, 29, 0, 0))

    def test_dom_wrap_near_eom(self):
        """Day-of-month near end of month, wrapping."""
        result = next_fire("0 0 25-5 * *", datetime(2026, 7, 6, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 25, 0, 0))


class TestMonthNames(unittest.TestCase):
    """Test month name handling."""

    def test_january(self):
        result = next_fire("0 0 1 jan *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2027, 1, 1, 0, 0))

    def test_december(self):
        result = next_fire("0 0 1 dec *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 12, 1, 0, 0))

    def test_mix_numbers_and_names(self):
        result = next_fire("0 0 1 1,feb,mar *", datetime(2026, 4, 1, 0, 0))
        self.assertEqual(result, datetime(2027, 1, 1, 0, 0))


class TestDayOfWeekNames(unittest.TestCase):
    """Test day-of-week name handling."""

    def test_sunday(self):
        result = next_fire("0 0 * * sun", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 26, 0, 0))

    def test_monday(self):
        result = next_fire("0 0 * * mon", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 27, 0, 0))

    def test_saturday(self):
        result = next_fire("0 0 * * sat", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 25, 0, 0))

    def test_mix_numbers_and_names_dow(self):
        result = next_fire("0 0 * * 0,mon,5", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))


if __name__ == '__main__':
    unittest.main()
