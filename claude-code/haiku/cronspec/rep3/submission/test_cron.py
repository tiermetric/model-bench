import unittest
from datetime import datetime
from cron import next_fire


class TestCronBasic(unittest.TestCase):
    """Test basic cron expression parsing and matching."""

    def test_every_minute(self):
        """Test */1 (every minute)."""
        result = next_fire("* * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 1))

    def test_every_15_minutes(self):
        """Test */15 on minutes."""
        result = next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 15))

    def test_single_value(self):
        """Test single values in fields."""
        result = next_fire("30 12 15 * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 15, 12, 30))

    def test_time_boundary(self):
        """Test that time strictly after `after` is returned."""
        result = next_fire("0 12 * * *", datetime(2026, 1, 1, 12, 0))
        self.assertEqual(result, datetime(2026, 1, 2, 12, 0))

    def test_seconds_and_microseconds_cleared(self):
        """Test that result has second == 0 and microsecond == 0."""
        result = next_fire("15 * * * *", datetime(2026, 1, 1, 0, 0, 45, 123456))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)
        self.assertEqual(result, datetime(2026, 1, 1, 0, 15))


class TestDeviation1(unittest.TestCase):
    """Test Deviation 1: dom AND dow is intersection."""

    def test_friday_13th(self):
        """Test 0 0 13 * fri - only Friday that is also 13th."""
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 13, 0, 0))

    def test_first_and_monday(self):
        """Test 0 0 1 * mon - only 1st that is also Monday."""
        result = next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0))
        self.assertEqual(result, datetime(2027, 2, 1, 0, 0))

    def test_dom_restricted_dow_unrestricted(self):
        """Test that only dom matters when dow is *."""
        result = next_fire("30 9 * * *", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 22, 9, 30))

    def test_dow_restricted_dom_unrestricted(self):
        """Test that only dow matters when dom is *."""
        result = next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0))
        # 2026-07-27 is a Monday
        self.assertEqual(result, datetime(2026, 7, 27, 9, 30))

    def test_both_unrestricted(self):
        """Test that every day matches when both dom and dow are *."""
        result = next_fire("12 15 * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 15, 12))


class TestDeviation2(unittest.TestCase):
    """Test Deviation 2: step anchored to range start."""

    def test_step_explicit_range(self):
        """Test 5-30/10 -> 5, 15, 25 (not 10, 20, 30)."""
        result = next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 6, 1, 12, 5))

    def test_step_explicit_range_next(self):
        """Test subsequent values in 5-30/10 range."""
        result = next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5))
        self.assertEqual(result, datetime(2026, 6, 1, 12, 15))

    def test_step_on_full_range(self):
        """Test */15 on minute (0-59): 0, 15, 30, 45."""
        result = next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 15))

    def test_step_on_hour_range(self):
        """Test */6 on hour (0-23): 0, 6, 12, 18."""
        # Start before hour 0: should find hour 0 of the same day
        result = next_fire("0 */6 * * *", datetime(2025, 12, 31, 23, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 0))
        # Start after hour 0: should find hour 6
        result = next_fire("0 */6 * * *", datetime(2026, 1, 1, 0, 1))
        self.assertEqual(result, datetime(2026, 1, 1, 6, 0))

    def test_step_on_day_of_month_range(self):
        """Test */10 on day (1-31): 1, 11, 21, 31."""
        # Start before day 1: should find day 1
        result = next_fire("0 0 */10 * *", datetime(2025, 12, 31, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 0))
        # Next should be the 11th
        result = next_fire("0 0 */10 * *", datetime(2026, 1, 1, 1, 0))
        self.assertEqual(result, datetime(2026, 1, 11, 0, 0))

    def test_step_on_month_range(self):
        """Test */3 on month (1-12): 1, 4, 7, 10."""
        # Start before month 1: should find month 1
        result = next_fire("0 0 1 */3 *", datetime(2025, 12, 31, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 0))
        # Next should be April 1
        result = next_fire("0 0 1 */3 *", datetime(2026, 1, 1, 1, 0))
        self.assertEqual(result, datetime(2026, 4, 1, 0, 0))


class TestDeviation3(unittest.TestCase):
    """Test Deviation 3: dow 7 is INVALID."""

    def test_dow_7_invalid(self):
        """Test that dow 7 raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1, 0, 0))

    def test_minute_out_of_domain(self):
        """Test minute 60 is out of domain."""
        with self.assertRaises(ValueError):
            next_fire("60 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_hour_out_of_domain(self):
        """Test hour 24 is out of domain."""
        with self.assertRaises(ValueError):
            next_fire("* 24 * * *", datetime(2026, 1, 1, 0, 0))

    def test_dom_0_out_of_domain(self):
        """Test day-of-month 0 is out of domain."""
        with self.assertRaises(ValueError):
            next_fire("* * 0 * *", datetime(2026, 1, 1, 0, 0))

    def test_month_13_out_of_domain(self):
        """Test month 13 is out of domain."""
        with self.assertRaises(ValueError):
            next_fire("* * * 13 *", datetime(2026, 1, 1, 0, 0))

    def test_dow_0_valid(self):
        """Test that dow 0 (Sunday) is valid."""
        # Should not raise
        result = next_fire("0 0 * * 0", datetime(2026, 1, 1, 0, 0))
        # 2026-01-04 is a Sunday
        self.assertEqual(result, datetime(2026, 1, 4, 0, 0))

    def test_dow_6_valid(self):
        """Test that dow 6 (Saturday) is valid."""
        # Should not raise
        result = next_fire("0 0 * * 6", datetime(2026, 1, 1, 0, 0))
        # 2026-01-03 is a Saturday
        self.assertEqual(result, datetime(2026, 1, 3, 0, 0))


class TestRule4WrapRanges(unittest.TestCase):
    """Test Rule 4: ranges wrap when start > end."""

    def test_minute_wrap(self):
        """Test 50-10 on minute: {50..59, 0..10}."""
        result = next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 50))

    def test_minute_wrap_next_hour(self):
        """Test 50-10 wrap into next hour."""
        result = next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 55))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 56))

    def test_month_wrap(self):
        """Test nov-feb on month: {11, 12, 1, 2}."""
        result = next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 11, 1, 0, 0))

    def test_dow_wrap(self):
        """Test fri-mon on dow: {5, 6, 0, 1}."""
        result = next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0))
        # 2026-07-24 is a Friday
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))

    def test_minute_wrap_identity(self):
        """Test that 0-59 is same as * on minute."""
        result1 = next_fire("* * * * *", datetime(2026, 1, 1, 0, 0))
        result2 = next_fire("0-59 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result1, result2)

    def test_dom_wrap_invalid_endpoint(self):
        """Test that wrap endpoints must still be in-domain."""
        with self.assertRaises(ValueError):
            next_fire("* * 70-10 * *", datetime(2026, 1, 1, 0, 0))

    def test_month_wrap_list(self):
        """Test list with wrapped ranges."""
        result = next_fire("0 0 1 1,nov-dec *", datetime(2026, 6, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 11, 1, 0, 0))


class TestRule5StepsThroughWrap(unittest.TestCase):
    """Test Rule 5: steps through wrapped ranges."""

    def test_minute_step_wrap(self):
        """Test 50-10/5 on minute: {50, 55, 0, 5, 10}."""
        result = next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 5))

    def test_minute_step_wrap_sequence(self):
        """Test 50-10/5 sequence."""
        dt = datetime(2026, 1, 1, 0, 0)
        result = next_fire("50-10/5 0 1 1 *", dt)
        self.assertEqual(result, datetime(2026, 1, 1, 0, 5))

        result = next_fire("50-10/5 0 1 1 *", result)
        self.assertEqual(result, datetime(2026, 1, 1, 0, 10))

        result = next_fire("50-10/5 0 1 1 *", result)
        self.assertEqual(result, datetime(2026, 1, 1, 0, 50))

    def test_dow_step_wrap(self):
        """Test fri-tue/2 on dow: {fri, sun, tue} = {5, 0, 2}."""
        result = next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0))
        # 2026-07-24 is a Friday
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))

    def test_dow_step_wrap_sun(self):
        """Test stepping through wrap hits Sunday."""
        result = next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 24, 0, 1))
        # Next is Sunday 2026-07-26
        self.assertEqual(result, datetime(2026, 7, 26, 0, 0))

    def test_dow_step_wrap_tue(self):
        """Test stepping through wrap hits Tuesday."""
        result = next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 26, 0, 1))
        # Next is Tuesday 2026-07-28
        self.assertEqual(result, datetime(2026, 7, 28, 0, 0))

    def test_hour_step_wrap(self):
        """Test 20-5/2 on hour: wrapped order is 20..23,0..5; every 2nd -> {20,22,0,2,4}."""
        # Wrapped order: 20, 21, 22, 23, 0, 1, 2, 3, 4, 5
        # Every 2nd starting at 20: 20, 22, 0, 2, 4
        # At 00:00 which is after, hour 0 IS in the set but we're looking for strictly after
        # so we need to check: we're at hour 0 min 0, next match is hour 2 (the next value in wrapped order stepping by 2)
        result = next_fire("0 20-5/2 * * *", datetime(2026, 1, 1, 0, 0))
        # hour 0 is in {20,22,0,2,4}, but strictly after 00:00:00 means next is hour 2
        self.assertEqual(result, datetime(2026, 1, 1, 2, 0))
        result = next_fire("0 20-5/2 * * *", datetime(2026, 1, 1, 2, 1))
        self.assertEqual(result, datetime(2026, 1, 1, 4, 0))
        result = next_fire("0 20-5/2 * * *", datetime(2026, 1, 1, 4, 1))
        self.assertEqual(result, datetime(2026, 1, 1, 20, 0))


class TestRule6LastDayOfMonth(unittest.TestCase):
    """Test Rule 6: L = last day of month."""

    def test_last_day_january(self):
        """Test L in January (31)."""
        result = next_fire("0 0 L * *", datetime(2026, 1, 15, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 31, 0, 0))

    def test_last_day_february_non_leap(self):
        """Test L in February non-leap year (28)."""
        result = next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 28, 0, 0))

    def test_last_day_february_leap(self):
        """Test L in February leap year (29)."""
        result = next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2028, 2, 29, 0, 0))

    def test_last_day_april(self):
        """Test L in April (30)."""
        result = next_fire("0 0 L * *", datetime(2026, 4, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 4, 30, 0, 0))

    def test_last_day_with_dow_constraint(self):
        """Test L with day-of-week constraint (intersection)."""
        result = next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 31, 0, 0))

    def test_L_uppercase_required(self):
        """Test that lowercase l is not valid."""
        with self.assertRaises(ValueError):
            next_fire("0 0 l * *", datetime(2026, 1, 1, 0, 0))

    def test_L_cannot_be_in_list(self):
        """Test that L cannot be combined in a list."""
        with self.assertRaises(ValueError):
            next_fire("0 0 L,15 * *", datetime(2026, 1, 1, 0, 0))

    def test_L_in_dow_field_invalid(self):
        """Test that L is only valid in day-of-month field."""
        with self.assertRaises(ValueError):
            next_fire("0 0 * * L", datetime(2026, 1, 1, 0, 0))

    def test_L_alone_in_dom(self):
        """Test that L must be the only value in dom field."""
        # L is valid by itself
        result = next_fire("0 0 L * *", datetime(2026, 1, 15, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 31, 0, 0))


class TestRule7MonthLengths(unittest.TestCase):
    """Test Rule 7: month lengths and leap years."""

    def test_31st_skips_30_day_month(self):
        """Test 0 0 31 * * skips April (30 days)."""
        result = next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 5, 31, 0, 0))

    def test_31st_skips_february(self):
        """Test 0 0 31 * * skips February."""
        result = next_fire("0 0 31 * *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 3, 31, 0, 0))

    def test_30th_skips_february(self):
        """Test 0 0 30 * * skips February."""
        result = next_fire("0 0 30 * *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 3, 30, 0, 0))

    def test_29th_february_leap_year_only(self):
        """Test 0 0 29 feb * only fires in leap years."""
        result = next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2028, 2, 29, 0, 0))

    def test_29th_february_non_leap_skips(self):
        """Test 0 0 29 2 * skips non-leap February."""
        result = next_fire("0 0 29 2 *", datetime(2026, 1, 1, 0, 0))
        # 2026 is not a leap year, so next is 2028-02-29
        self.assertEqual(result, datetime(2028, 2, 29, 0, 0))

    def test_31st_only_31_day_months(self):
        """Test 0 0 31 * * only fires on 31-day months."""
        # Start from 2026-01-01, should find 2026-01-31
        result = next_fire("0 0 31 * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 31, 0, 0))


class TestExpressionParsing(unittest.TestCase):
    """Test expression parsing and error handling."""

    def test_empty_expression(self):
        """Test empty expression raises error."""
        with self.assertRaises(ValueError):
            next_fire("", datetime(2026, 1, 1, 0, 0))

    def test_too_few_fields(self):
        """Test too few fields."""
        with self.assertRaises(ValueError):
            next_fire("* * * *", datetime(2026, 1, 1, 0, 0))

    def test_too_many_fields(self):
        """Test too many fields."""
        with self.assertRaises(ValueError):
            next_fire("* * * * * *", datetime(2026, 1, 1, 0, 0))

    def test_leading_trailing_whitespace(self):
        """Test leading/trailing whitespace is ignored."""
        result1 = next_fire("0 0 1 1 *", datetime(2025, 12, 31, 0, 0))
        result2 = next_fire("  0 0 1 1 *  ", datetime(2025, 12, 31, 0, 0))
        self.assertEqual(result1, result2)

    def test_multiple_spaces_between_fields(self):
        """Test multiple spaces between fields."""
        result = next_fire("0  0  1  1  *", datetime(2025, 12, 31, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 0))

    def test_uppercase_name_invalid(self):
        """Test uppercase names are invalid."""
        with self.assertRaises(ValueError):
            next_fire("* * * * SUN", datetime(2026, 1, 1, 0, 0))

    def test_month_name_lowercase(self):
        """Test lowercase month names work."""
        result = next_fire("0 0 1 jan *", datetime(2025, 12, 31, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 0))

    def test_dow_name_lowercase(self):
        """Test lowercase dow names work."""
        result = next_fire("0 0 * * mon", datetime(2026, 1, 1, 0, 0))
        # 2026-01-05 is a Monday
        self.assertEqual(result, datetime(2026, 1, 5, 0, 0))

    def test_mixed_names_and_numbers(self):
        """Test mixing names and numbers in lists."""
        result = next_fire("0 0 1 jan,jul *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 1, 0, 0))

    def test_step_without_range_or_wildcard(self):
        """Test step on single value is invalid."""
        with self.assertRaises(ValueError):
            next_fire("5/10 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_step_zero_invalid(self):
        """Test step 0 is invalid."""
        with self.assertRaises(ValueError):
            next_fire("*/0 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_step_negative_invalid(self):
        """Test negative step is invalid."""
        with self.assertRaises(ValueError):
            next_fire("*/-5 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_empty_list_element(self):
        """Test empty element in list is invalid."""
        with self.assertRaises(ValueError):
            next_fire("1,,3 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_unknown_token(self):
        """Test unknown token."""
        with self.assertRaises(ValueError):
            next_fire("1x * * * *", datetime(2026, 1, 1, 0, 0))


class TestMonthNames(unittest.TestCase):
    """Test all month names."""

    def test_jan(self):
        result = next_fire("0 0 1 jan *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2027, 1, 1, 0, 0))

    def test_feb(self):
        result = next_fire("0 0 1 feb *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 1, 0, 0))

    def test_mar(self):
        result = next_fire("0 0 1 mar *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 3, 1, 0, 0))

    def test_apr(self):
        result = next_fire("0 0 1 apr *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 4, 1, 0, 0))

    def test_may(self):
        result = next_fire("0 0 1 may *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 5, 1, 0, 0))

    def test_jun(self):
        result = next_fire("0 0 1 jun *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 6, 1, 0, 0))

    def test_jul(self):
        result = next_fire("0 0 1 jul *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 1, 0, 0))

    def test_aug(self):
        result = next_fire("0 0 1 aug *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 8, 1, 0, 0))

    def test_sep(self):
        result = next_fire("0 0 1 sep *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 9, 1, 0, 0))

    def test_oct(self):
        result = next_fire("0 0 1 oct *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 10, 1, 0, 0))

    def test_nov(self):
        result = next_fire("0 0 1 nov *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 11, 1, 0, 0))

    def test_dec(self):
        result = next_fire("0 0 1 dec *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 12, 1, 0, 0))


class TestDowNames(unittest.TestCase):
    """Test all day-of-week names."""

    def test_sun(self):
        result = next_fire("0 0 * * sun", datetime(2026, 1, 1, 0, 0))
        # 2026-01-04 is Sunday
        self.assertEqual(result, datetime(2026, 1, 4, 0, 0))

    def test_mon(self):
        result = next_fire("0 0 * * mon", datetime(2026, 1, 1, 0, 0))
        # 2026-01-05 is Monday
        self.assertEqual(result, datetime(2026, 1, 5, 0, 0))

    def test_tue(self):
        result = next_fire("0 0 * * tue", datetime(2026, 1, 1, 0, 0))
        # 2026-01-06 is Tuesday
        self.assertEqual(result, datetime(2026, 1, 6, 0, 0))

    def test_wed(self):
        result = next_fire("0 0 * * wed", datetime(2026, 1, 1, 0, 0))
        # 2026-01-07 is Wednesday
        self.assertEqual(result, datetime(2026, 1, 7, 0, 0))

    def test_thu(self):
        result = next_fire("0 0 * * thu", datetime(2026, 1, 1, 0, 0))
        # 2026-01-08 is Thursday
        self.assertEqual(result, datetime(2026, 1, 8, 0, 0))

    def test_fri(self):
        result = next_fire("0 0 * * fri", datetime(2026, 1, 1, 0, 0))
        # 2026-01-02 is Friday
        self.assertEqual(result, datetime(2026, 1, 2, 0, 0))

    def test_sat(self):
        result = next_fire("0 0 * * sat", datetime(2026, 1, 1, 0, 0))
        # 2026-01-03 is Saturday
        self.assertEqual(result, datetime(2026, 1, 3, 0, 0))


class TestWorkedExamples(unittest.TestCase):
    """Test all worked examples from the spec."""

    def test_example_step_minute(self):
        result = next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 15))

    def test_example_step_range(self):
        result = next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 6, 1, 12, 5))

    def test_example_step_range_next(self):
        result = next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5))
        self.assertEqual(result, datetime(2026, 6, 1, 12, 15))

    def test_example_friday_13th(self):
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 13, 0, 0))

    def test_example_1st_and_monday(self):
        result = next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0))
        self.assertEqual(result, datetime(2027, 2, 1, 0, 0))

    def test_example_monday_9_30(self):
        result = next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 27, 9, 30))

    def test_example_jan_or_jul(self):
        result = next_fire("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 1, 12, 0))

    def test_example_minute_wrap(self):
        result = next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 50))

    def test_example_minute_wrap_next(self):
        result = next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 55))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 56))

    def test_example_month_wrap(self):
        result = next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 11, 1, 0, 0))

    def test_example_dow_wrap(self):
        result = next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))

    def test_example_step_wrap_minute(self):
        result = next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 5))

    def test_example_step_wrap_dow(self):
        result = next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))

    def test_example_last_day_jan(self):
        result = next_fire("0 0 L * *", datetime(2026, 1, 15, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 31, 0, 0))

    def test_example_last_day_feb_non_leap(self):
        result = next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 28, 0, 0))

    def test_example_last_day_feb_leap(self):
        result = next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2028, 2, 29, 0, 0))

    def test_example_last_day_friday(self):
        result = next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 31, 0, 0))

    def test_example_31st_skip_april(self):
        result = next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 5, 31, 0, 0))

    def test_example_30th_skip_february(self):
        result = next_fire("0 0 30 * *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 3, 30, 0, 0))

    def test_example_29th_february_leap_only(self):
        result = next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2028, 2, 29, 0, 0))


if __name__ == "__main__":
    unittest.main()
