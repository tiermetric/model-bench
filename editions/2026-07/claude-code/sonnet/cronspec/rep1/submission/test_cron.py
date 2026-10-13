import unittest
from datetime import datetime

from cron import next_fire


class WorkedExamplesTests(unittest.TestCase):
    def test_step_on_star_minute(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )

    def test_deviation2_step_anchored_to_range_start(self):
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2026, 6, 1, 12, 5),
        )

    def test_deviation2_step_anchored_to_range_start_next_occurrence(self):
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5)),
            datetime(2026, 6, 1, 12, 15),
        )

    def test_deviation1_friday_the_13th(self):
        self.assertEqual(
            next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 2, 13, 0, 0),
        )

    def test_deviation1_first_and_monday(self):
        self.assertEqual(
            next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0)),
            datetime(2027, 2, 1, 0, 0),
        )

    def test_weekly_monday_schedule(self):
        self.assertEqual(
            next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 27, 9, 30),
        )

    def test_month_name_list(self):
        self.assertEqual(
            next_fire("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 7, 1, 12, 0),
        )

    def test_rule4_minute_wrap(self):
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30)),
            datetime(2026, 1, 1, 0, 50),
        )

    def test_rule4_minute_wrap_later_start(self):
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 55)),
            datetime(2026, 1, 1, 0, 56),
        )

    def test_rule4_month_wrap(self):
        self.assertEqual(
            next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 11, 1, 0, 0),
        )

    def test_rule4_dow_wrap(self):
        self.assertEqual(
            next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )

    def test_rule5_step_through_wrap_minute(self):
        self.assertEqual(
            next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 5),
        )

    def test_rule5_step_through_wrap_dow(self):
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )

    def test_rule6_last_day_mid_month(self):
        self.assertEqual(
            next_fire("0 0 L * *", datetime(2026, 1, 15, 0, 0)),
            datetime(2026, 1, 31, 0, 0),
        )

    def test_rule6_last_day_non_leap_feb(self):
        self.assertEqual(
            next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0)),
            datetime(2026, 2, 28, 0, 0),
        )

    def test_rule6_last_day_next_leap_feb(self):
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )

    def test_rule6_last_day_that_is_friday(self):
        self.assertEqual(
            next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 7, 31, 0, 0),
        )

    def test_rule7_skip_short_month(self):
        self.assertEqual(
            next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0)),
            datetime(2026, 5, 31, 0, 0),
        )

    def test_rule7_skip_february(self):
        self.assertEqual(
            next_fire("0 0 30 * *", datetime(2026, 2, 1, 0, 0)),
            datetime(2026, 3, 30, 0, 0),
        )

    def test_rule7_leap_day_only(self):
        self.assertEqual(
            next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )


class SecondsAndMicrosecondsTests(unittest.TestCase):
    def test_after_with_nonzero_seconds_rounds_up_to_next_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 30)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_after_with_nonzero_microseconds_rounds_up(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 0, 500)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_after_exactly_on_minute_boundary_is_strictly_after(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 1, 0)),
            datetime(2026, 1, 1, 0, 2),
        )

    def test_result_has_zero_seconds_and_microseconds(self):
        result = next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 45, 123))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)


class WildcardAndBasicFieldTests(unittest.TestCase):
    def test_all_wildcards_fires_next_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 3, 5, 10, 20)),
            datetime(2026, 3, 5, 10, 21),
        )

    def test_single_values(self):
        self.assertEqual(
            next_fire("0 6 15 * *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 3, 15, 6, 0),
        )

    def test_comma_list_minutes(self):
        self.assertEqual(
            next_fire("1,3,5 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_comma_list_minutes_next_element(self):
        self.assertEqual(
            next_fire("1,3,5 * * * *", datetime(2026, 1, 1, 0, 1)),
            datetime(2026, 1, 1, 0, 3),
        )

    def test_mixed_list_step_and_single(self):
        self.assertEqual(
            next_fire("0-10/2,30 * * * *", datetime(2026, 1, 1, 0, 9)),
            datetime(2026, 1, 1, 0, 10),
        )

    def test_mixed_list_falls_through_to_single(self):
        self.assertEqual(
            next_fire("0-10/2,30 * * * *", datetime(2026, 1, 1, 0, 10)),
            datetime(2026, 1, 1, 0, 30),
        )

    def test_mixed_names_and_numbers_in_dow_list(self):
        self.assertEqual(
            next_fire("0 0 * * 1,fri", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )

    def test_star_step_floor_is_domain_floor(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 16)),
            datetime(2026, 1, 1, 0, 30),
        )


class DeviationOneIntersectionTests(unittest.TestCase):
    def test_dom_star_means_dow_alone_decides(self):
        # dom is '*' (unrestricted) so only dow constrains the day.
        self.assertEqual(
            next_fire("0 0 * * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 2, 0, 0),
        )

    def test_dow_star_means_dom_alone_decides(self):
        # dow is '*' (unrestricted) so only dom constrains the day.
        self.assertEqual(
            next_fire("0 0 5 * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 5, 0, 0),
        )

    def test_both_star_matches_every_day(self):
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 2, 0, 0),
        )

    def test_both_restricted_requires_both_step_field_counts_as_restricted(self):
        # */5 counts as restricted, so combined with a restricted dow it's an
        # intersection: day must be in {1,6,11,16,21,26,31} AND a Monday.
        result = next_fire("0 0 */5 * mon", datetime(2026, 1, 1, 0, 0))
        self.assertIn(result.day, {1, 6, 11, 16, 21, 26, 31})
        self.assertEqual(result.isoweekday() % 7, 1)


class RangeAndStepDeviationTests(unittest.TestCase):
    def test_deviation2_explicit_example_5_30_10(self):
        # 5-30/10 -> {5, 15, 25}, not {10, 20, 30}
        self.assertEqual(
            next_fire("5-30/10 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 5),
        )
        self.assertEqual(
            next_fire("5-30/10 * * * *", datetime(2026, 1, 1, 0, 5)),
            datetime(2026, 1, 1, 0, 15),
        )
        self.assertEqual(
            next_fire("5-30/10 * * * *", datetime(2026, 1, 1, 0, 25)),
            datetime(2026, 1, 1, 1, 5),
        )

    def test_deviation2_10_50_15(self):
        # 10-50/15 -> {10, 25, 40}
        self.assertEqual(
            next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 10),
        )
        self.assertEqual(
            next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 10)),
            datetime(2026, 1, 1, 0, 25),
        )
        self.assertEqual(
            next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 25)),
            datetime(2026, 1, 1, 0, 40),
        )
        self.assertEqual(
            next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 40)),
            datetime(2026, 1, 1, 1, 10),
        )


class RuleFourWrapTests(unittest.TestCase):
    def test_minute_wrap_set_membership(self):
        # 50-10 -> {50..59, 0..10}; 11 through 49 should not match.
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 11)),
            datetime(2026, 1, 1, 0, 50),
        )

    def test_month_wrap_nov_feb_all_members(self):
        for month, expected_month in [(11, 11), (12, 12), (1, 1), (2, 2)]:
            with self.subTest(month=month):
                result = next_fire(
                    "0 0 1 nov-feb *", datetime(2026, 1 if month != 1 else 1, 1, 0, 0)
                )
                self.assertIn(result.month, (11, 12, 1, 2))

    def test_dow_wrap_fri_mon_excludes_midweek(self):
        # Tuesday -> next matching day should be Friday, not Wed/Thu.
        result = next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0))
        self.assertEqual(result.isoweekday() % 7, 5)


class RuleFiveStepThroughWrapTests(unittest.TestCase):
    def test_minute_wrap_step_values(self):
        # 50-10/5 -> {50, 55, 0, 5, 10}; use hour='0' with dom/month unrestricted
        # so the sequence cycles daily instead of jumping a year.
        expr = "50-10/5 0 * * *"
        self.assertEqual(next_fire(expr, datetime(2026, 1, 1, 0, 0)).minute, 5)
        self.assertEqual(next_fire(expr, datetime(2026, 1, 1, 0, 5)).minute, 10)
        self.assertEqual(next_fire(expr, datetime(2026, 1, 1, 0, 10)).minute, 50)
        self.assertEqual(next_fire(expr, datetime(2026, 1, 1, 0, 50)).minute, 55)
        next_day = next_fire(expr, datetime(2026, 1, 1, 0, 55))
        self.assertEqual((next_day.day, next_day.hour, next_day.minute), (2, 0, 0))

    def test_dow_wrap_step_values(self):
        # fri-tue/2 -> {fri, sun, tue} = {5, 0, 2}
        result = next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0))
        self.assertIn(result.isoweekday() % 7, (5, 0, 2))


class RuleSixLastDayTests(unittest.TestCase):
    def test_l_alone_matches_last_day_every_month(self):
        result = next_fire("0 0 L * *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 3, 31, 0, 0))

    def test_l_leap_year_february(self):
        result = next_fire("0 0 L 2 *", datetime(2024, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2024, 2, 29, 0, 0))

    def test_l_combined_with_restricted_dow(self):
        # Last day of month must also be a Friday (intersection, Deviation 1).
        result = next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.isoweekday() % 7, 5)
        last_day_of_month = result.day
        import calendar as _cal
        self.assertEqual(last_day_of_month, _cal.monthrange(result.year, result.month)[1])


class RuleSevenMonthLengthTests(unittest.TestCase):
    def test_day_31_only_fires_on_31_day_months(self):
        result = next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0))
        self.assertEqual(result.day, 31)
        self.assertIn(result.month, (1, 3, 5, 7, 8, 10, 12))

    def test_feb_29_only_on_leap_years(self):
        result = next_fire("0 0 29 feb *", datetime(2025, 3, 1, 0, 0))
        self.assertEqual(result.month, 2)
        self.assertEqual(result.day, 29)
        self.assertTrue(calendar_isleap(result.year))


def calendar_isleap(year):
    import calendar as _cal
    return _cal.isleap(year)


class ErrorCaseTests(unittest.TestCase):
    def test_empty_expression(self):
        with self.assertRaises(ValueError):
            next_fire("", datetime(2026, 1, 1))

    def test_whitespace_only_expression(self):
        with self.assertRaises(ValueError):
            next_fire("   ", datetime(2026, 1, 1))

    def test_four_fields(self):
        with self.assertRaises(ValueError):
            next_fire("* * * *", datetime(2026, 1, 1))

    def test_six_fields(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * * *", datetime(2026, 1, 1))

    def test_minute_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("60 * * * *", datetime(2026, 1, 1))

    def test_hour_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("* 24 * * *", datetime(2026, 1, 1))

    def test_day_of_month_out_of_domain_zero(self):
        with self.assertRaises(ValueError):
            next_fire("* * 0 * *", datetime(2026, 1, 1))

    def test_day_of_month_out_of_domain_32(self):
        with self.assertRaises(ValueError):
            next_fire("* * 32 * *", datetime(2026, 1, 1))

    def test_month_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("* * * 13 *", datetime(2026, 1, 1))

    def test_month_out_of_domain_zero(self):
        with self.assertRaises(ValueError):
            next_fire("* * * 0 *", datetime(2026, 1, 1))

    def test_dow_seven_invalid_deviation3(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1))

    def test_uppercase_dow_name_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * SUN", datetime(2026, 1, 1))

    def test_uppercase_month_name_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * JAN *", datetime(2026, 1, 1))

    def test_step_zero_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("*/0 * * * *", datetime(2026, 1, 1))

    def test_step_negative_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("*/-5 * * * *", datetime(2026, 1, 1))

    def test_step_on_single_value_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("5/10 * * * *", datetime(2026, 1, 1))

    def test_empty_list_element(self):
        with self.assertRaises(ValueError):
            next_fire("1,,3 * * * *", datetime(2026, 1, 1))

    def test_leading_comma_empty_element(self):
        with self.assertRaises(ValueError):
            next_fire(",1 * * * *", datetime(2026, 1, 1))

    def test_trailing_comma_empty_element(self):
        with self.assertRaises(ValueError):
            next_fire("1, * * * *", datetime(2026, 1, 1))

    def test_unknown_token(self):
        with self.assertRaises(ValueError):
            next_fire("1x * * * *", datetime(2026, 1, 1))

    def test_wrap_endpoint_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("70-10 * * * *", datetime(2026, 1, 1))

    def test_l_combined_in_list_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 L,15 * *", datetime(2026, 1, 1))

    def test_l_in_range_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 L-15 * *", datetime(2026, 1, 1))

    def test_l_with_step_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 L/2 * *", datetime(2026, 1, 1))

    def test_l_only_in_day_of_month_field_not_dow(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 * * L", datetime(2026, 1, 1))

    def test_l_only_in_day_of_month_field_not_month(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 * L *", datetime(2026, 1, 1))

    def test_lowercase_l_is_not_l(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 l * *", datetime(2026, 1, 1))

    def test_name_in_minute_field_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("jan * * * *", datetime(2026, 1, 1))

    def test_name_in_hour_field_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* mon * * *", datetime(2026, 1, 1))

    def test_name_in_day_of_month_field_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * jan * *", datetime(2026, 1, 1))

    def test_multiple_slashes_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("*/5/2 * * * *", datetime(2026, 1, 1))


class ValidWrapNotAnErrorTests(unittest.TestCase):
    def test_minute_wrap_30_5_is_valid(self):
        result = next_fire("30-5 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertIsInstance(result, datetime)

    def test_month_wrap_nov_feb_is_valid(self):
        result = next_fire("0 0 1 nov-feb *", datetime(2026, 1, 1, 0, 0))
        self.assertIsInstance(result, datetime)

    def test_dow_wrap_fri_mon_is_valid(self):
        result = next_fire("0 0 * * fri-mon", datetime(2026, 1, 1, 0, 0))
        self.assertIsInstance(result, datetime)


class WhitespaceHandlingTests(unittest.TestCase):
    def test_leading_and_trailing_whitespace_ignored(self):
        self.assertEqual(
            next_fire("  */15 * * * *  ", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )

    def test_multiple_spaces_between_fields(self):
        self.assertEqual(
            next_fire("*/15   *  *   *  *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )


if __name__ == "__main__":
    unittest.main()
