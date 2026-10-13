import unittest
from datetime import datetime

from cron import next_fire


class WorkedExamplesTests(unittest.TestCase):
    def test_step_from_domain_floor(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )

    def test_deviation2_step_anchored_at_range_start(self):
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2026, 6, 1, 12, 5),
        )

    def test_deviation2_step_anchored_at_range_start_next(self):
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

    def test_named_dow_single(self):
        self.assertEqual(
            next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 27, 9, 30),
        )

    def test_named_month_list(self):
        self.assertEqual(
            next_fire("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 7, 1, 12, 0),
        )

    def test_rule4_minute_wrap(self):
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30)),
            datetime(2026, 1, 1, 0, 50),
        )

    def test_rule4_minute_wrap_next(self):
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

    def test_rule5_minute_step_through_wrap(self):
        self.assertEqual(
            next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 5),
        )

    def test_rule5_dow_step_through_wrap(self):
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )

    def test_rule6_last_day_basic(self):
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

    def test_rule7_skip_short_months(self):
        self.assertEqual(
            next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0)),
            datetime(2026, 5, 31, 0, 0),
        )

    def test_rule7_skip_february(self):
        self.assertEqual(
            next_fire("0 0 30 * *", datetime(2026, 2, 1, 0, 0)),
            datetime(2026, 3, 30, 0, 0),
        )

    def test_rule7_leap_only(self):
        self.assertEqual(
            next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )


class DeviationTests(unittest.TestCase):
    def test_deviation1_both_unrestricted_matches_every_day(self):
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 2, 0, 0),
        )

    def test_deviation1_only_dom_restricted(self):
        # dow is '*' (unrestricted) -> dom alone decides.
        self.assertEqual(
            next_fire("0 0 15 * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 15, 0, 0),
        )

    def test_deviation1_only_dow_restricted(self):
        # dom is '*' (unrestricted) -> dow alone decides.
        result = next_fire("0 0 * * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.weekday(), 4)  # Python Monday=0 -> Friday=4

    def test_deviation1_intersection_excludes_non_matching_days(self):
        # Only days that are BOTH the 13th AND a Friday should match;
        # verify the returned date really is Friday the 13th.
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.day, 13)
        self.assertEqual(result.weekday(), 4)

    def test_deviation2_step_not_from_zero(self):
        # 5-30/10 must yield {5, 15, 25}, never touching 10, 20, 30.
        r1 = next_fire("5-30/10 12 * 6 *", datetime(2026, 6, 1, 12, 0))
        self.assertEqual(r1, datetime(2026, 6, 1, 12, 5))
        r2 = next_fire("5-30/10 12 * 6 *", r1)
        self.assertEqual(r2, datetime(2026, 6, 1, 12, 15))
        r3 = next_fire("5-30/10 12 * 6 *", r2)
        self.assertEqual(r3, datetime(2026, 6, 1, 12, 25))
        r4 = next_fire("5-30/10 12 * 6 *", r3)
        self.assertEqual(r4, datetime(2026, 6, 2, 12, 5))
        for r in (r1, r2, r3, r4):
            self.assertNotIn(r.minute, (10, 20, 30))

    def test_deviation2_explicit_range_step_values(self):
        # 10-50/15 on minute -> {10, 25, 40}
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
        # next match is the following hour's 10 (55 is not in {10,25,40})
        self.assertEqual(
            next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 40)),
            datetime(2026, 1, 1, 1, 10),
        )

    def test_deviation2_star_step_uses_domain_floor(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 44)),
            datetime(2026, 1, 1, 0, 45),
        )

    def test_deviation3_dow_seven_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1, 0, 0))

    def test_deviation3_dow_zero_is_sunday(self):
        result = next_fire("0 0 * * 0", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.weekday(), 6)  # Sunday


class HardeningRuleTests(unittest.TestCase):
    def test_rule4_range_wrap_minute_full_set(self):
        # 50-10 => {50..59, 0..10}; verify a value outside that (e.g. 30)
        # is skipped.
        result = next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 11))
        self.assertEqual(result.minute, 50)

    def test_rule5_wrapped_step_full_set(self):
        # 50-10/5 on minute, fixed to hour 0 / Jan 1st (any year), so the
        # set {50, 55, 0, 5, 10} fires in chronological (ascending minute)
        # order within the day, wrapping to next year's Jan 1st at 00:00.
        expr = "50-10/5 0 1 1 *"
        r1 = next_fire(expr, datetime(2026, 1, 1, 0, 0))
        self.assertEqual(r1, datetime(2026, 1, 1, 0, 5))
        r2 = next_fire(expr, r1)
        self.assertEqual(r2, datetime(2026, 1, 1, 0, 10))
        r3 = next_fire(expr, r2)
        self.assertEqual(r3, datetime(2026, 1, 1, 0, 50))
        r4 = next_fire(expr, r3)
        self.assertEqual(r4, datetime(2026, 1, 1, 0, 55))
        r5 = next_fire(expr, r4)
        self.assertEqual(r5, datetime(2027, 1, 1, 0, 0))

    def test_rule6_l_matches_last_day_including_leap(self):
        r1 = next_fire("0 0 L * *", datetime(2028, 1, 1, 0, 0))
        self.assertEqual(r1, datetime(2028, 1, 31, 0, 0))
        r2 = next_fire("0 0 L * *", r1)
        self.assertEqual(r2, datetime(2028, 2, 29, 0, 0))

    def test_rule6_l_with_dow_intersection(self):
        result = next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.weekday(), 4)

    def test_rule7_no_clamping_31st(self):
        # April has 30 days; 31st should never fire in April.
        result = next_fire("0 0 31 * *", datetime(2026, 3, 31, 0, 0))
        self.assertNotEqual(result.month, 4)

    def test_rule7_feb29_only_on_leap_years(self):
        result = next_fire("0 0 29 feb *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual((result.month, result.day), (2, 29))
        self.assertTrue(
            result.year % 4 == 0
            and (result.year % 100 != 0 or result.year % 400 == 0)
        )


class MiscBehaviorTests(unittest.TestCase):
    def test_seconds_and_microseconds_stripped_and_strictly_after(self):
        result = next_fire(
            "* * * * *", datetime(2026, 1, 1, 0, 0, 30, 500000)
        )
        self.assertEqual(result, datetime(2026, 1, 1, 0, 1))

    def test_result_has_zero_seconds_and_microseconds(self):
        result = next_fire("*/5 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)

    def test_strictly_after_same_minute_not_returned(self):
        # Exactly on a matching minute boundary -> must return the NEXT one.
        result = next_fire("0 * * * *", datetime(2026, 1, 1, 5, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 6, 0))

    def test_whitespace_tolerant_parsing(self):
        result = next_fire(
            "  */15   *  *   *  * ", datetime(2026, 1, 1, 0, 0)
        )
        self.assertEqual(result, datetime(2026, 1, 1, 0, 15))

    def test_mixed_names_and_numbers_in_list(self):
        result = next_fire("0 0 1 1,jul *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 1, 0, 0))

    def test_comma_list_with_range_and_step(self):
        # 0-10/2,30 -> {0,2,4,6,8,10,30}
        result = next_fire("0-10/2,30 * * * *", datetime(2026, 1, 1, 0, 10))
        self.assertEqual(result.minute, 30)


class ErrorCaseTests(unittest.TestCase):
    def test_empty_expression(self):
        with self.assertRaises(ValueError):
            next_fire("", datetime(2026, 1, 1, 0, 0))

    def test_four_fields(self):
        with self.assertRaises(ValueError):
            next_fire("* * * *", datetime(2026, 1, 1, 0, 0))

    def test_six_fields(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * * *", datetime(2026, 1, 1, 0, 0))

    def test_minute_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("60 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_hour_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("* 24 * * *", datetime(2026, 1, 1, 0, 0))

    def test_dom_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("* * 0 * *", datetime(2026, 1, 1, 0, 0))

    def test_month_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("* * * 13 *", datetime(2026, 1, 1, 0, 0))

    def test_dow_seven_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1, 0, 0))

    def test_uppercase_dow_name_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * SUN", datetime(2026, 1, 1, 0, 0))

    def test_step_zero_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("*/0 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_step_negative_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("*/-5 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_step_on_single_value_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("5/10 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_empty_list_element(self):
        with self.assertRaises(ValueError):
            next_fire("1,,3 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_unknown_token(self):
        with self.assertRaises(ValueError):
            next_fire("1x * * * *", datetime(2026, 1, 1, 0, 0))

    def test_wrap_endpoint_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("70-10 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_l_cannot_be_combined_in_list(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 L,15 * *", datetime(2026, 1, 1, 0, 0))

    def test_l_only_valid_in_dom_field(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 * * L", datetime(2026, 1, 1, 0, 0))

    def test_lowercase_l_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 l * *", datetime(2026, 1, 1, 0, 0))

    def test_l_in_range_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 1-L * *", datetime(2026, 1, 1, 0, 0))

    def test_l_with_step_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 L/2 * *", datetime(2026, 1, 1, 0, 0))


if __name__ == "__main__":
    unittest.main()
