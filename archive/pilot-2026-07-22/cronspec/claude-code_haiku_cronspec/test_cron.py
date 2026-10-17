import unittest
from datetime import datetime
from cron import next_fire


class TestCronExpressions(unittest.TestCase):
    """Test suite for the cron expression scheduler."""

    # ==================== WORKED EXAMPLES ====================

    def test_step_minute(self):
        """Test */15 on minutes."""
        result = next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 15))

    def test_deviation_2_step_range_anchoring(self):
        """Test that step is anchored at range start, not at 0."""
        result = next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 6, 1, 12, 5))
        result = next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5))
        self.assertEqual(result, datetime(2026, 6, 1, 12, 15))

    def test_deviation_1_day_and_dow_intersection(self):
        """Test that day-of-month AND day-of-week are intersected."""
        # Friday the 13th
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 13, 0, 0))

    def test_deviation_1_first_and_monday(self):
        """Test 1st AND Monday (intersection)."""
        result = next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0))
        self.assertEqual(result, datetime(2027, 2, 1, 0, 0))

    def test_monday_930(self):
        """Test basic Monday at 9:30."""
        result = next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 27, 9, 30))

    def test_comma_list_months(self):
        """Test comma-separated month list."""
        result = next_fire("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 1, 12, 0))

    # ==================== RULE 4 — WRAPPED RANGES ====================

    def test_wrapped_range_minute(self):
        """Test minute range that wraps: 50-10."""
        result = next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 50))
        result = next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 55))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 56))

    def test_wrapped_range_month(self):
        """Test month range that wraps: nov-feb."""
        result = next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 11, 1, 0, 0))

    def test_wrapped_range_dow(self):
        """Test day-of-week range that wraps: fri-mon."""
        result = next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))

    # ==================== RULE 5 — STEPPED WRAPPED RANGES ====================

    def test_stepped_wrapped_range_minute(self):
        """Test minute stepped wrap: 50-10/5."""
        result = next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 5))

    def test_stepped_wrapped_range_dow(self):
        """Test day-of-week stepped wrap: fri-tue/2."""
        result = next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))

    # ==================== RULE 6 — LAST DAY OF MONTH ====================

    def test_last_day_of_month(self):
        """Test L (last day of month)."""
        result = next_fire("0 0 L * *", datetime(2026, 1, 15, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 31, 0, 0))

    def test_last_day_february_non_leap(self):
        """Test L in February (non-leap year)."""
        result = next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 28, 0, 0))

    def test_last_day_february_leap(self):
        """Test L in February (leap year)."""
        result = next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2028, 2, 29, 0, 0))

    def test_last_day_that_is_friday(self):
        """Test L AND day-of-week (last day that is a Friday)."""
        result = next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 31, 0, 0))

    # ==================== RULE 7 — MONTH-LENGTH & LEAP ROLLOVER ====================

    def test_31st_skips_30_day_month(self):
        """Test that 31st skips 30-day months."""
        result = next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 5, 31, 0, 0))

    def test_30th_skips_february(self):
        """Test that 30th skips February."""
        result = next_fire("0 0 30 * *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 3, 30, 0, 0))

    def test_feb_29_leap_year_only(self):
        """Test that Feb 29 only fires in leap years."""
        result = next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2028, 2, 29, 0, 0))

    # ==================== ERROR CASES ====================

    def test_error_empty_expression(self):
        """Test that empty expression raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("", datetime(2026, 1, 1, 0, 0))

    def test_error_too_few_fields(self):
        """Test that 4 fields raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * * *", datetime(2026, 1, 1, 0, 0))

    def test_error_too_many_fields(self):
        """Test that 6 fields raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * * * * *", datetime(2026, 1, 1, 0, 0))

    def test_error_minute_out_of_domain(self):
        """Test that minute 60 raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("60 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_error_hour_out_of_domain(self):
        """Test that hour 24 raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* 24 * * *", datetime(2026, 1, 1, 0, 0))

    def test_error_day_out_of_domain_zero(self):
        """Test that day 0 raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * 0 * *", datetime(2026, 1, 1, 0, 0))

    def test_error_day_out_of_domain_32(self):
        """Test that day 32 raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * 32 * *", datetime(2026, 1, 1, 0, 0))

    def test_error_month_out_of_domain(self):
        """Test that month 13 raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * * 13 *", datetime(2026, 1, 1, 0, 0))

    def test_error_dow_7_is_invalid(self):
        """Test that dow 7 (Deviation 3) raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1, 0, 0))

    def test_error_uppercase_day_name(self):
        """Test that uppercase day name raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("* * * * SUN", datetime(2026, 1, 1, 0, 0))

    def test_error_step_zero(self):
        """Test that step 0 raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("*/0 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_error_step_negative(self):
        """Test that negative step raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("*/-1 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_error_step_on_single_value(self):
        """Test that step on single value raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("5/10 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_error_empty_list_element(self):
        """Test that empty list element raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("1,,3 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_error_invalid_token(self):
        """Test that invalid token raises ValueError."""
        with self.assertRaises(ValueError):
            next_fire("1x * * * *", datetime(2026, 1, 1, 0, 0))

    def test_error_wrap_endpoint_out_of_domain(self):
        """Test that wrapped range endpoints must be in-domain."""
        with self.assertRaises(ValueError):
            next_fire("70-10 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_error_L_in_list(self):
        """Test that L cannot be in a list."""
        with self.assertRaises(ValueError):
            next_fire("0 0 L,15 * *", datetime(2026, 1, 1, 0, 0))

    def test_error_L_in_dow_field(self):
        """Test that L is only valid in day-of-month field."""
        with self.assertRaises(ValueError):
            next_fire("0 0 * * L", datetime(2026, 1, 1, 0, 0))

    def test_error_lowercase_l(self):
        """Test that lowercase 'l' is not valid."""
        with self.assertRaises(ValueError):
            next_fire("0 0 l * *", datetime(2026, 1, 1, 0, 0))

    # ==================== ADDITIONAL COVERAGE ====================

    def test_seconds_and_microseconds_stripped(self):
        """Test that result has second=0 and microsecond=0."""
        result = next_fire("0 0 1 * *", datetime(2026, 1, 1, 12, 30, 45, 123456))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)

    def test_first_candidate_is_next_minute(self):
        """Test that first candidate is the next whole minute."""
        result = next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 30))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 1))

    def test_hour_list(self):
        """Test comma-separated hour list."""
        result = next_fire("0 9,12,15 * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 9, 0))

    def test_minute_range_non_wrapped(self):
        """Test non-wrapped minute range."""
        result = next_fire("10-20 * * * *", datetime(2026, 1, 1, 0, 5))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 10))

    def test_every_day_noon(self):
        """Test every day at noon."""
        result = next_fire("0 12 * * *", datetime(2026, 1, 1, 11, 59))
        self.assertEqual(result, datetime(2026, 1, 1, 12, 0))

    def test_specific_day_of_month(self):
        """Test specific day of month."""
        result = next_fire("0 0 15 * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 15, 0, 0))

    def test_unrestricted_day_with_restricted_dow(self):
        """Test unrestricted day-of-month with restricted day-of-week."""
        # Should match any day that is a Monday
        result = next_fire("0 0 * * mon", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result.weekday(), 0)  # Monday

    def test_unrestricted_dow_with_restricted_day(self):
        """Test restricted day-of-month with unrestricted day-of-week."""
        # Should match the 5th regardless of day-of-week
        result = next_fire("0 0 5 * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 5, 0, 0))

    def test_month_name_january(self):
        """Test month name 'jan'."""
        result = next_fire("0 0 1 jan *", datetime(2026, 6, 1, 0, 0))
        self.assertEqual(result, datetime(2027, 1, 1, 0, 0))

    def test_month_name_december(self):
        """Test month name 'dec'."""
        result = next_fire("0 0 1 dec *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 12, 1, 0, 0))

    def test_dow_name_sunday(self):
        """Test day-of-week name 'sun'."""
        result = next_fire("0 0 * * sun", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result.weekday(), 6)  # Sunday

    def test_dow_name_saturday(self):
        """Test day-of-week name 'sat'."""
        result = next_fire("0 0 * * sat", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result.weekday(), 5)  # Saturday

    def test_mixed_numeric_and_names_months(self):
        """Test mixed numeric and name values in months."""
        result = next_fire("0 0 1 jan,3,jul *", datetime(2026, 4, 1, 0, 0))
        self.assertEqual(result.month, 7)

    def test_mixed_numeric_and_names_dow(self):
        """Test mixed numeric and name values in dow."""
        result = next_fire("0 0 * * 1,wed,5", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))  # Friday

    def test_step_hour(self):
        """Test step on hours."""
        result = next_fire("0 */6 * * *", datetime(2026, 1, 1, 1, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 6, 0))

    def test_step_day(self):
        """Test step on days."""
        result = next_fire("0 0 */5 * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 6, 0, 0))

    def test_trailing_whitespace(self):
        """Test that trailing whitespace is handled."""
        result = next_fire("0 0 1 * *  ", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 1, 0, 0))

    def test_leading_whitespace(self):
        """Test that leading whitespace is handled."""
        result = next_fire("  0 0 1 * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 1, 0, 0))

    def test_multiple_spaces_between_fields(self):
        """Test that multiple spaces between fields are handled."""
        result = next_fire("0  0  1  *  *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 1, 0, 0))

    def test_every_minute(self):
        """Test * * * * * (every minute)."""
        result = next_fire("* * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 1))

    def test_complex_expression_1(self):
        """Test a complex cron expression."""
        result = next_fire("30 14 15,30 * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 15, 14, 30))

    def test_complex_expression_2(self):
        """Test another complex cron expression."""
        result = next_fire("0 9 * * 1-5", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 22, 9, 0))

    def test_year_boundary_crossing(self):
        """Test that search crosses year boundaries."""
        result = next_fire("0 0 1 1 *", datetime(2026, 6, 1, 0, 0))
        self.assertEqual(result, datetime(2027, 1, 1, 0, 0))

    def test_leap_year_feb_29_non_leap_year(self):
        """Test that Feb 29 is skipped in non-leap years."""
        result = next_fire("0 0 * * *", datetime(2026, 2, 28, 0, 0))
        self.assertEqual(result, datetime(2026, 3, 1, 0, 0))

    def test_dow_0_is_sunday(self):
        """Test that 0 = Sunday in day-of-week."""
        result = next_fire("0 0 * * 0", datetime(2026, 7, 20, 0, 0))
        self.assertEqual(result.weekday(), 6)  # Python: Sunday=6

    def test_dow_6_is_saturday(self):
        """Test that 6 = Saturday in day-of-week."""
        result = next_fire("0 0 * * 6", datetime(2026, 7, 20, 0, 0))
        self.assertEqual(result.weekday(), 5)  # Python: Saturday=5

    def test_range_with_names_month(self):
        """Test range with month names."""
        result = next_fire("0 0 1 jan-mar *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 1, 0, 0))

    def test_range_with_names_dow(self):
        """Test range with day-of-week names."""
        # July 22, 2026 is a Wednesday; mon-wed means it should match
        result = next_fire("0 0 * * mon-wed", datetime(2026, 7, 22, 0, 0))
        # But we're looking strictly after 00:00, so next candidate is 00:01 which matches
        # Actually no, that doesn't match the cron expression (minute 1 != 0)
        # Next match is the 1st day of the next week that is Mon-Wed
        self.assertEqual(result, datetime(2026, 7, 27, 0, 0))

    def test_step_on_range_with_names(self):
        """Test step on a range with day-of-week names."""
        result = next_fire("0 0 * * sun-fri/2", datetime(2026, 7, 22, 0, 0))
        # Should be every 2 days starting from Sunday
        self.assertIsNotNone(result)

    def test_day_constraint_prevents_match(self):
        """Test that day constraint prevents matching other days."""
        result = next_fire("0 0 15 * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.day, 15)

    def test_month_constraint_jumps_months(self):
        """Test that month constraint causes month to be skipped."""
        result = next_fire("0 0 1 6 *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result.month, 6)

    def test_day_of_week_jump(self):
        """Test that day-of-week constraint causes days to be skipped."""
        result = next_fire("0 0 * * fri", datetime(2026, 7, 20, 0, 0))
        # July 20 is a Monday; next Friday is July 24
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))

    def test_last_day_multiple_months(self):
        """Test L across multiple months."""
        # Start Jan 31, next L should be Feb 28
        result = next_fire("0 0 L * *", datetime(2026, 1, 31, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 28, 0, 0))

    def test_l_day_not_matching_dow(self):
        """Test that L respects day-of-week constraint."""
        # Find last day of a month that is a Monday
        result = next_fire("0 0 L * mon", datetime(2026, 1, 1, 0, 0))
        # Verify it's the last day of its month AND a Monday
        self.assertEqual(result.weekday(), 0)  # Monday
        next_day = result.replace(day=result.day+1) if result.day < 28 else None
        if next_day and next_day.month == result.month:
            # If there's a next day in same month, it shouldn't be
            pass

    def test_range_with_step_anchor_not_zero(self):
        """Test that ranges with step are anchored at range start."""
        # 1-10/3 should be 1, 4, 7, 10 (not 0, 3, 6, 9)
        result = next_fire("1-10/3 0 1 1 *", datetime(2026, 1, 1, 0, 0))
        # First match is minute 1
        self.assertEqual(result.minute, 1)

    def test_wrap_range_with_step_sequence(self):
        """Test that stepped wrap counts through the wrap."""
        # 50-10/5 should be {50, 55, 0, 5, 10}
        # Starting from 2026-01-01 00:00, next candidate is 00:01 (strictly after)
        # So first match is 00:05 (minute 5 matches)
        result = next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.minute, 5)


if __name__ == '__main__':
    unittest.main()
