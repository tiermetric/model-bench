import unittest
from datetime import datetime, timedelta
from cron import next_fire


class TestCronNextFire(unittest.TestCase):
    """Test suite for cron scheduler"""

    # ====== Worked Examples ======
    def test_every_15_minutes(self):
        """Test */15 * * * * - every 15 minutes"""
        result = next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 15))

    def test_dev2_explicit_range_step(self):
        """Test Deviation 2: 5-30/10 gives 5, 15, 25 not 10, 20, 30"""
        result = next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 6, 1, 12, 5))

    def test_dev2_explicit_range_step_next(self):
        """Test Deviation 2: next value after 5-30/10 at minute 5"""
        result = next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5))
        self.assertEqual(result, datetime(2026, 6, 1, 12, 15))

    def test_dev2_range_step_10_50_15(self):
        """Test Deviation 2: 10-50/15 gives 10, 25, 40"""
        result = next_fire("10-50/15 0 1 3 *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 3, 1, 0, 10))

    def test_dev1_friday_13th(self):
        """Test Deviation 1: 0 0 13 * fri fires only on Friday the 13th"""
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 13, 0, 0))

    def test_dev1_friday_13th_after(self):
        """Test Deviation 1: next Friday the 13th after Feb 13, 2026"""
        result = next_fire("0 0 13 * fri", datetime(2026, 2, 14, 0, 0))
        self.assertEqual(result, datetime(2026, 3, 13, 0, 0))

    def test_dev1_first_and_monday(self):
        """Test Deviation 1: 0 0 1 * mon fires only on 1st AND Monday"""
        result = next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0))
        self.assertEqual(result, datetime(2027, 2, 1, 0, 0))

    def test_next_monday_930(self):
        """Test finding next Monday at 09:30"""
        result = next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 27, 9, 30))

    def test_weekdays_after_weekend(self):
        """Test 0 0 * * mon-fri skips weekend"""
        result = next_fire("0 0 * * mon-fri", datetime(2026, 7, 25, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 27, 0, 0))

    def test_months_with_names_and_numbers(self):
        """Test 0 12 1 jan,jul * - month list with names"""
        result = next_fire("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 1, 12, 0))

    def test_dow_0_is_sunday(self):
        """Test 0 0 * * 0 - day of week 0 is Sunday"""
        result = next_fire("0 0 * * 0", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 26, 0, 0))

    def test_lists_and_ranges(self):
        """Test 15,45 8-10 * * * - minute list and hour range"""
        result = next_fire("15,45 8-10 * * *", datetime(2026, 7, 22, 9, 20))
        self.assertEqual(result, datetime(2026, 7, 22, 9, 45))

    # ====== Rounding Behavior ======
    def test_after_with_seconds_rounds_up(self):
        """Test that after with nonzero seconds rounds up to next minute"""
        result = next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 0, 30))
        self.assertEqual(result, datetime(2026, 1, 2, 0, 0))

    def test_after_with_microseconds_rounds_up(self):
        """Test that after with nonzero microseconds rounds up"""
        result = next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 0, 0, 1))
        self.assertEqual(result, datetime(2026, 1, 2, 0, 0))

    def test_result_always_zero_seconds(self):
        """Test that result always has second==0 and microsecond==0"""
        result = next_fire("*/5 * * * *", datetime(2026, 1, 1, 0, 0, 15, 999999))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)

    # ====== Deviation 1: Intersection Logic ======
    def test_both_dom_dow_restricted_intersection(self):
        """Test that both dom and dow restricted requires intersection"""
        # 1st AND Monday in 2026: Feb 2, 2027
        result = next_fire("0 0 1 * mon", datetime(2026, 1, 1, 0, 0))
        # Verify it lands on both 1st and Monday
        self.assertEqual(result.day, 1)
        self.assertEqual(result.weekday(), 0)  # Monday in Python weekday

    def test_only_dom_restricted(self):
        """Test that only day-of-month matters when dow is *"""
        result = next_fire("0 0 13 * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.day, 13)
        self.assertEqual(result.month, 1)

    def test_only_dow_restricted(self):
        """Test that only day-of-week matters when dom is *"""
        result = next_fire("0 0 * * fri", datetime(2026, 1, 1, 0, 0))
        # Should land on a Friday
        self.assertEqual(result.weekday(), 4)  # Friday in Python

    def test_both_unrestricted(self):
        """Test that both * means every day"""
        result = next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 0, 30))
        # Next midnight is Jan 2
        self.assertEqual(result, datetime(2026, 1, 2, 0, 0))

    # ====== Deviation 2: Step Anchoring ======
    def test_step_star_anchored_to_domain_floor_minute(self):
        """Test */15 on minute: 0, 15, 30, 45"""
        # Start at 23:59 on Dec 31, 2025 to get into Jan 1 at minute 0
        result = next_fire("*/15 0 1 1 *", datetime(2025, 12, 31, 23, 59))
        self.assertEqual(result.minute, 0)
        result = next_fire("*/15 0 1 1 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.minute, 15)

    def test_step_star_anchored_to_domain_floor_hour(self):
        """Test */6 on hour: 0, 6, 12, 18"""
        # Start before the day to get first match
        result = next_fire("0 */6 1 1 *", datetime(2025, 12, 31, 23, 0))
        self.assertEqual(result.hour, 0)
        result = next_fire("0 */6 1 1 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.hour, 6)

    def test_step_star_anchored_to_domain_floor_dom(self):
        """Test */10 on day-of-month: 1, 11, 21, 31"""
        result = next_fire("0 0 */10 1 *", datetime(2025, 12, 31, 0, 0))
        self.assertEqual(result.day, 1)
        result = next_fire("0 0 */10 1 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.day, 11)

    def test_step_star_anchored_to_domain_floor_month(self):
        """Test */4 on month: 1, 5, 9"""
        result = next_fire("0 0 1 */4 *", datetime(2025, 12, 31, 0, 0))
        self.assertEqual(result.month, 1)
        result = next_fire("0 0 1 */4 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.month, 5)

    def test_step_star_anchored_to_domain_floor_dow(self):
        """Test */3 on day-of-week: 0, 3, 6"""
        result = next_fire("0 0 * * */3", datetime(2026, 1, 3, 0, 0))
        # Should land on Sunday (0) - Jan 4, 2026 is a Sunday
        self.assertEqual(result.weekday(), 6)

    def test_step_range_anchored_to_range_start(self):
        """Test 5-30/10: 5, 15, 25 not 10, 20, 30"""
        # Test that 5-30/10 matches 5, 15, 25
        result = next_fire("5-30/10 0 1 1 *", datetime(2025, 12, 31, 23, 59))
        self.assertEqual(result.minute, 5)
        result = next_fire("5-30/10 0 1 1 *", datetime(2026, 1, 1, 0, 5))
        self.assertEqual(result.minute, 15)

    # ====== Deviation 3: Strict Domain & Rejection ======
    def test_invalid_minute_60(self):
        """Test that minute 60 is invalid"""
        with self.assertRaises(ValueError):
            next_fire("60 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_invalid_minute_negative(self):
        """Test that negative minute is invalid"""
        with self.assertRaises(ValueError):
            next_fire("-1 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_invalid_hour_24(self):
        """Test that hour 24 is invalid"""
        with self.assertRaises(ValueError):
            next_fire("* 24 * * *", datetime(2026, 1, 1, 0, 0))

    def test_invalid_dom_0(self):
        """Test that day-of-month 0 is invalid"""
        with self.assertRaises(ValueError):
            next_fire("* * 0 * *", datetime(2026, 1, 1, 0, 0))

    def test_invalid_dom_32(self):
        """Test that day-of-month 32 is invalid"""
        with self.assertRaises(ValueError):
            next_fire("* * 32 * *", datetime(2026, 1, 1, 0, 0))

    def test_invalid_month_0(self):
        """Test that month 0 is invalid"""
        with self.assertRaises(ValueError):
            next_fire("* * * 0 *", datetime(2026, 1, 1, 0, 0))

    def test_invalid_month_13(self):
        """Test that month 13 is invalid"""
        with self.assertRaises(ValueError):
            next_fire("* * * 13 *", datetime(2026, 1, 1, 0, 0))

    def test_invalid_dow_7(self):
        """Test Deviation 3: day-of-week 7 is INVALID (not alias for 0)"""
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1, 0, 0))

    def test_invalid_dow_8(self):
        """Test that day-of-week 8 is invalid"""
        with self.assertRaises(ValueError):
            next_fire("* * * * 8", datetime(2026, 1, 1, 0, 0))

    def test_uppercase_month_name(self):
        """Test that uppercase month names are rejected"""
        with self.assertRaises(ValueError):
            next_fire("* * * JAN *", datetime(2026, 1, 1, 0, 0))

    def test_uppercase_dow_name(self):
        """Test that uppercase day-of-week names are rejected"""
        with self.assertRaises(ValueError):
            next_fire("* * * * SUN", datetime(2026, 1, 1, 0, 0))

    def test_mixed_case_names_rejected(self):
        """Test that mixed-case names are rejected"""
        with self.assertRaises(ValueError):
            next_fire("* * * Jan *", datetime(2026, 1, 1, 0, 0))

    def test_invalid_step_zero(self):
        """Test that step 0 is invalid"""
        with self.assertRaises(ValueError):
            next_fire("*/0 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_invalid_step_negative(self):
        """Test that negative step is invalid"""
        with self.assertRaises(ValueError):
            next_fire("*/-1 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_invalid_range_start_greater_than_end(self):
        """Test that range start > end is invalid"""
        with self.assertRaises(ValueError):
            next_fire("30-5 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_invalid_range_start_greater_than_end_names(self):
        """Test that range start > end is invalid for names"""
        with self.assertRaises(ValueError):
            next_fire("* * * fri-mon *", datetime(2026, 1, 1, 0, 0))

    def test_step_on_single_value(self):
        """Test that step on single value is invalid"""
        with self.assertRaises(ValueError):
            next_fire("5/10 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_step_on_single_value_named(self):
        """Test that step on single named value is invalid"""
        with self.assertRaises(ValueError):
            next_fire("* * * mon/2 *", datetime(2026, 1, 1, 0, 0))

    def test_empty_field(self):
        """Test that multiple spaces create separate fields"""
        # Multiple spaces between fields splits into more than 5 fields
        # Actually, split() with no argument collapses multiple spaces
        # So "0  * * * *" becomes ["0", "*", "*", "*", "*"] - still valid
        # Let's test something that actually fails: a field with nothing
        with self.assertRaises(ValueError):
            next_fire("0 * * * ", datetime(2026, 1, 1, 0, 0))

    def test_empty_list_element_start(self):
        """Test that empty list element at start is invalid"""
        with self.assertRaises(ValueError):
            next_fire(",1,3 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_empty_list_element_middle(self):
        """Test that empty list element in middle is invalid"""
        with self.assertRaises(ValueError):
            next_fire("1,,3 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_empty_list_element_end(self):
        """Test that empty list element at end is invalid"""
        with self.assertRaises(ValueError):
            next_fire("1,3, * * * *", datetime(2026, 1, 1, 0, 0))

    def test_unknown_token(self):
        """Test that unknown tokens are rejected"""
        with self.assertRaises(ValueError):
            next_fire("1x * * * *", datetime(2026, 1, 1, 0, 0))

    def test_malformed_range(self):
        """Test that malformed ranges are rejected"""
        with self.assertRaises(ValueError):
            next_fire("1-2-3 * * * *", datetime(2026, 1, 1, 0, 0))

    # ====== Field Count ======
    def test_empty_expression(self):
        """Test that empty expression is invalid"""
        with self.assertRaises(ValueError):
            next_fire("", datetime(2026, 1, 1, 0, 0))

    def test_too_few_fields(self):
        """Test that 4 fields is invalid"""
        with self.assertRaises(ValueError):
            next_fire("* * * *", datetime(2026, 1, 1, 0, 0))

    def test_too_many_fields(self):
        """Test that 6 fields is invalid"""
        with self.assertRaises(ValueError):
            next_fire("* * * * * *", datetime(2026, 1, 1, 0, 0))

    # ====== Whitespace Handling ======
    def test_leading_trailing_whitespace(self):
        """Test that leading/trailing whitespace is ignored"""
        result = next_fire("  */15 * * * *  ", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 15))

    def test_multiple_spaces_between_fields(self):
        """Test that multiple spaces between fields work"""
        result = next_fire("*/15  *   *    *     *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 15))

    # ====== Name Parsing ======
    def test_month_name_jan(self):
        """Test month name 'jan'"""
        result = next_fire("0 0 1 jan *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.month, 1)

    def test_month_name_dec(self):
        """Test month name 'dec'"""
        result = next_fire("0 0 1 dec *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.month, 12)

    def test_dow_name_sun(self):
        """Test day-of-week name 'sun'"""
        result = next_fire("0 0 * * sun", datetime(2026, 1, 1, 0, 0))
        # Should land on Sunday
        self.assertEqual(result.weekday(), 6)

    def test_dow_name_sat(self):
        """Test day-of-week name 'sat'"""
        result = next_fire("0 0 * * sat", datetime(2026, 1, 1, 0, 0))
        # Should land on Saturday
        self.assertEqual(result.weekday(), 5)

    def test_month_names_in_list(self):
        """Test multiple month names in list"""
        result = next_fire("0 0 1 jan,apr,jul,oct *", datetime(2025, 12, 31, 0, 0))
        self.assertEqual(result.month, 1)
        result = next_fire("0 0 1 jan,apr,jul,oct *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.month, 4)

    def test_dow_names_in_range(self):
        """Test day-of-week names in range"""
        result = next_fire("0 0 * * mon-fri", datetime(2026, 1, 3, 0, 0))  # Saturday
        # Should skip to Monday
        self.assertEqual(result.weekday(), 0)

    def test_mixed_numbers_and_names_month(self):
        """Test mixed numbers and names in month field"""
        result = next_fire("0 0 1 1,jul *", datetime(2025, 12, 31, 0, 0))
        self.assertEqual(result.month, 1)

    def test_mixed_numbers_and_names_dow(self):
        """Test mixed numbers and names in day-of-week field"""
        result = next_fire("0 0 * * 0,3,fri", datetime(2026, 1, 3, 0, 0))
        # First hit should be Sunday (Jan 4, 2026)
        self.assertEqual(result.weekday(), 6)

    # ====== Specific Scenarios ======
    def test_every_day_midnight(self):
        """Test 0 0 * * * - every day at midnight"""
        result = next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 2, 0, 0))

    def test_business_hours_9am(self):
        """Test 0 9 * * mon-fri - weekday at 9am"""
        result = next_fire("0 9 * * mon-fri", datetime(2026, 7, 22, 0, 0))  # Wednesday
        self.assertEqual(result, datetime(2026, 7, 22, 9, 0))

    def test_twice_daily(self):
        """Test 0 9,17 * * mon-fri - business start and end"""
        result = next_fire("0 9,17 * * mon-fri", datetime(2026, 7, 22, 9, 0))
        self.assertEqual(result, datetime(2026, 7, 22, 17, 0))

    def test_every_6_hours(self):
        """Test */6 on hour - every 6 hours"""
        result = next_fire("0 */6 * * *", datetime(2025, 12, 31, 23, 0))
        self.assertEqual(result.hour, 0)
        result = next_fire("0 */6 * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.hour, 6)

    def test_month_end_exists(self):
        """Test that valid days are handled correctly"""
        result = next_fire("0 0 31 * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.day, 31)

    def test_february_valid_day(self):
        """Test that valid days in February work"""
        result = next_fire("0 0 28 2 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 28, 0, 0))

    def test_december_transitions_year(self):
        """Test that December transitions to next year"""
        result = next_fire("0 0 1 12 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.month, 12)
        self.assertEqual(result.year, 2026)

    def test_range_with_step_fully_specified(self):
        """Test range with step"""
        result = next_fire("0,30 9-17 * * *", datetime(2026, 1, 1, 8, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 9, 0))
        result = next_fire("0,30 9-17 * * *", datetime(2026, 1, 1, 9, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 9, 30))

    def test_list_of_ranges(self):
        """Test comma-separated ranges"""
        result = next_fire("0 0-4,20-23 * * *", datetime(2026, 1, 1, 5, 0))
        self.assertEqual(result.hour, 20)

    # ====== Edge Cases ====
    def test_leap_year_feb_29(self):
        """Test that leap years are handled (if matching day 29)"""
        # 2024 is a leap year
        result = next_fire("0 0 29 2 *", datetime(2024, 1, 1, 0, 0))
        self.assertEqual(result.day, 29)
        self.assertEqual(result.month, 2)

    def test_exact_match_next_minute(self):
        """Test match at exact next minute boundary"""
        result = next_fire("0 * * * *", datetime(2026, 1, 1, 0, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 1, 0))

    def test_same_minute_different_hour(self):
        """Test transitioning to next hour"""
        result = next_fire("30 * * * *", datetime(2026, 1, 1, 0, 30, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 1, 30))


if __name__ == '__main__':
    unittest.main()
