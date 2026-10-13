import unittest
from datetime import datetime

from cron import next_fire


class WorkedExamplesTests(unittest.TestCase):
    """Every example given directly in the spec."""

    def test_deviation1_intersection_friday_13th(self):
        self.assertEqual(
            next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 2, 13, 0, 0),
        )

    def test_deviation1_intersection_first_and_monday(self):
        self.assertEqual(
            next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0)),
            datetime(2027, 2, 1, 0, 0),
        )

    def test_deviation2_step_anchored_to_range_start(self):
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2026, 6, 1, 12, 5),
        )

    def test_deviation2_step_anchored_second_candidate(self):
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5)),
            datetime(2026, 6, 1, 12, 15),
        )

    def test_star_step_floor_anchor(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )

    def test_dow_only_next_monday(self):
        self.assertEqual(
            next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 27, 9, 30),
        )

    def test_month_name_list(self):
        self.assertEqual(
            next_fire("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 7, 1, 12, 0),
        )

    def test_rule4_minute_wrap_from_before(self):
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30)),
            datetime(2026, 1, 1, 0, 50),
        )

    def test_rule4_minute_wrap_within(self):
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

    def test_rule5_step_through_minute_wrap(self):
        self.assertEqual(
            next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 5),
        )

    def test_rule5_step_through_dow_wrap(self):
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

    def test_rule6_last_day_leap_feb(self):
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )

    def test_rule6_last_day_and_friday(self):
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


class ErrorContractTests(unittest.TestCase):
    """Every error case given directly in the spec."""

    def test_empty_expression(self):
        with self.assertRaises(ValueError):
            next_fire("", datetime(2026, 1, 1))

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

    def test_dom_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("* * 0 * *", datetime(2026, 1, 1))

    def test_month_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("* * * 13 *", datetime(2026, 1, 1))

    def test_dow_seven_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1))

    def test_uppercase_name_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * SUN", datetime(2026, 1, 1))

    def test_zero_step_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("*/0 * * * *", datetime(2026, 1, 1))

    def test_step_on_single_value_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("5/10 * * * *", datetime(2026, 1, 1))

    def test_empty_list_element_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("1,,3 * * * *", datetime(2026, 1, 1))

    def test_unknown_token_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("1x * * * *", datetime(2026, 1, 1))

    def test_wrap_endpoint_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("70-10 * * * *", datetime(2026, 1, 1))

    def test_l_combined_with_list_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 L,15 * *", datetime(2026, 1, 1))

    def test_l_only_in_dom_field(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 * * L", datetime(2026, 1, 1))

    def test_lowercase_l_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 l * *", datetime(2026, 1, 1))


class WrapIsNotAnErrorTests(unittest.TestCase):
    """Rule 4 explicitly says A > B is valid, never a 'start > end' error."""

    def test_minute_wrap_is_valid(self):
        next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_month_name_wrap_is_valid(self):
        next_fire("0 0 1 nov-feb *", datetime(2026, 1, 1, 0, 0))

    def test_dow_name_wrap_is_valid(self):
        next_fire("0 0 * * fri-mon", datetime(2026, 1, 1, 0, 0))


class DeviationAdditionalTests(unittest.TestCase):
    def test_deviation1_dom_star_dow_restricted_uses_dow_only(self):
        # dom is '*' (unrestricted) -> only dow constrains the day.
        result = next_fire("0 0 * * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.hour, 0)
        self.assertEqual(result.minute, 0)
        self.assertEqual((result.weekday() + 1) % 7, 5)  # Friday

    def test_deviation1_both_star_matches_every_day(self):
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 2, 0, 0),
        )

    def test_deviation1_dow_star_dom_restricted_uses_dom_only(self):
        self.assertEqual(
            next_fire("0 0 15 * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 15, 0, 0),
        )

    def test_deviation2_star_step_matches_standard_cron(self):
        result = next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 16))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 30))

    def test_deviation2_explicit_range_step_ten_to_fifty(self):
        # 10-50/15 on minute -> 10, 25, 40
        r1 = next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(r1.minute, 10)
        r2 = next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 10))
        self.assertEqual(r2.minute, 25)
        r3 = next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 25))
        self.assertEqual(r3.minute, 40)
        r4 = next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 40))
        self.assertEqual(r4.minute, 10)
        self.assertEqual(r4.hour, 1)

    def test_deviation3_dow_zero_is_sunday_valid(self):
        result = next_fire("0 0 * * 0", datetime(2026, 1, 1, 0, 0))
        self.assertEqual((result.weekday() + 1) % 7, 0)


class RuleAdditionalTests(unittest.TestCase):
    def test_rule4_range_A_equals_B(self):
        result = next_fire("0 5-5 * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.hour, 5)

    def test_rule5_wrap_step_hour_field(self):
        # hour 22-2/2 wraps: order 22,23,0,1,2 -> every 2nd -> {22,0,2}.
        # From just after midnight, the earliest matching hour that day is 2.
        result = next_fire("0 22-2/2 * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.hour, 2)
        self.assertEqual(result.day, 1)
        result2 = next_fire("0 22-2/2 * * *", datetime(2026, 1, 1, 22, 0))
        self.assertEqual(result2.hour, 0)
        self.assertEqual(result2.day, 2)

    def test_rule6_l_alone_field_valid(self):
        # sanity: L used alone never raises
        next_fire("0 0 L * *", datetime(2026, 1, 1, 0, 0))

    def test_rule6_leap_year_feb_29_last_day(self):
        self.assertEqual(
            next_fire("0 0 L * *", datetime(2028, 2, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )

    def test_rule7_month_boundary_forward_search(self):
        # No Feb 30/31 ever -> must roll to March for day 30, but 31 must
        # skip Feb, Apr, Jun, Sep, Nov entirely.
        result = next_fire("0 0 31 * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 31, 0, 0))
        result2 = next_fire("0 0 31 * *", datetime(2026, 1, 31, 0, 0))
        self.assertEqual(result2, datetime(2026, 3, 31, 0, 0))


class ParsingMechanicsTests(unittest.TestCase):
    def test_whitespace_trimmed_and_collapsible(self):
        r1 = next_fire("  0 0 1 1 *  ", datetime(2026, 1, 1, 0, 0))
        r2 = next_fire("0   0  1   1 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(r1, r2)

    def test_mixed_names_and_numbers_in_list(self):
        result = next_fire("0 0 1 1,jul *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result.month, 7)

    def test_comma_list_of_singles(self):
        result = next_fire("0 0 * * 1,3,5", datetime(2026, 1, 1, 0, 0))
        self.assertIn((result.weekday() + 1) % 7, {1, 3, 5})

    def test_comma_list_with_range_and_step(self):
        # 0-10/2,30 on minute -> {0,2,4,6,8,10,30}
        result = next_fire("0-10/2,30 * * * *", datetime(2026, 1, 1, 0, 5))
        self.assertEqual(result.minute, 6)
        result2 = next_fire("0-10/2,30 * * * *", datetime(2026, 1, 1, 0, 10))
        self.assertEqual(result2.minute, 30)

    def test_day_of_month_domain_boundaries_valid(self):
        next_fire("0 0 1 * *", datetime(2026, 1, 1, 0, 0))
        next_fire("0 0 31 * *", datetime(2026, 1, 1, 0, 0))

    def test_hour_domain_boundaries_valid(self):
        next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 0))
        next_fire("0 23 * * *", datetime(2026, 1, 1, 0, 0))

    def test_seconds_and_microseconds_stripped_from_after(self):
        result = next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 30, 123456))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 1))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)

    def test_after_on_exact_minute_boundary_advances(self):
        result = next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 1))

    def test_result_always_minute_boundary(self):
        result = next_fire("*/7 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)


if __name__ == "__main__":
    unittest.main()
