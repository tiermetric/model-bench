import unittest
from datetime import datetime

from cron import next_fire


class WorkedExamplesTests(unittest.TestCase):
    def test_star_step_minute(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )

    def test_deviation2_range_step_first(self):
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2026, 6, 1, 12, 5),
        )

    def test_deviation2_range_step_second(self):
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

    def test_weekly_monday_930(self):
        self.assertEqual(
            next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 27, 9, 30),
        )

    def test_month_name_list(self):
        self.assertEqual(
            next_fire("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 7, 1, 12, 0),
        )

    def test_rule4_minute_wrap_a(self):
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30)),
            datetime(2026, 1, 1, 0, 50),
        )

    def test_rule4_minute_wrap_b(self):
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

    def test_rule5_minute_wrap_step(self):
        self.assertEqual(
            next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 5),
        )

    def test_rule5_dow_wrap_step(self):
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )

    def test_rule6_last_day_mid_month(self):
        self.assertEqual(
            next_fire("0 0 L * *", datetime(2026, 1, 15, 0, 0)),
            datetime(2026, 1, 31, 0, 0),
        )

    def test_rule6_last_day_feb_non_leap(self):
        self.assertEqual(
            next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0)),
            datetime(2026, 2, 28, 0, 0),
        )

    def test_rule6_last_day_feb_next_leap(self):
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


class ErrorContractTests(unittest.TestCase):
    def test_empty_expr(self):
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

    def test_step_zero_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("*/0 * * * *", datetime(2026, 1, 1))

    def test_step_on_single_value_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("5/10 * * * *", datetime(2026, 1, 1))

    def test_empty_list_element(self):
        with self.assertRaises(ValueError):
            next_fire("1,,3 * * * *", datetime(2026, 1, 1))

    def test_unknown_token(self):
        with self.assertRaises(ValueError):
            next_fire("1x * * * *", datetime(2026, 1, 1))

    def test_wrap_endpoint_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("70-10 * * * *", datetime(2026, 1, 1))

    def test_l_combined_with_list(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 L,15 * *", datetime(2026, 1, 1))

    def test_l_only_in_dom_field(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 * * L", datetime(2026, 1, 1))

    def test_lowercase_l_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 l * *", datetime(2026, 1, 1))


class WrapAroundValidityTests(unittest.TestCase):
    """A > B in a range is valid (wrap), never a 'start > end' error."""

    def test_minute_wrap_is_valid(self):
        try:
            next_fire("30-5 * * * *", datetime(2026, 1, 1))
        except ValueError:
            self.fail("30-5 should be a valid wrap-around range")

    def test_month_name_wrap_is_valid(self):
        try:
            next_fire("0 0 1 nov-feb *", datetime(2026, 1, 1))
        except ValueError:
            self.fail("nov-feb should be a valid wrap-around range")

    def test_dow_name_wrap_is_valid(self):
        try:
            next_fire("0 0 * * fri-mon", datetime(2026, 1, 1))
        except ValueError:
            self.fail("fri-mon should be a valid wrap-around range")


class WildcardBasicsTests(unittest.TestCase):
    def test_all_star_next_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_all_star_with_seconds_rounds_up(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 30, 500000)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_result_has_zero_seconds_and_micros(self):
        result = next_fire("*/5 * * * *", datetime(2026, 1, 1, 0, 0, 45, 123456))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)

    def test_star_star_both_unrestricted(self):
        # dom='*' and dow='*' => every day matches (deviation 1, both unrestricted).
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 2, 0, 0),
        )


class Deviation1IntersectionTests(unittest.TestCase):
    def test_only_dom_restricted(self):
        # dow='*' -> dom alone decides.
        self.assertEqual(
            next_fire("0 0 15 * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 15, 0, 0),
        )

    def test_only_dow_restricted(self):
        # dom='*' -> dow alone decides. 2026-01-01 is a Thursday.
        self.assertEqual(
            next_fire("0 0 * * thu", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 8, 0, 0),
        )

    def test_both_restricted_is_intersection_not_union(self):
        # 2026-01-02 is a Friday but not the 13th; next Friday-the-13th is Feb 13 2026.
        # If this were a UNION (vixie cron), Jan 2 (a Friday) would match instead.
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 13, 0, 0))
        self.assertNotEqual(result.day, 2)


class Deviation2StepAnchorTests(unittest.TestCase):
    def test_range_step_anchored_at_start(self):
        result = next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0))
        self.assertEqual(result.minute, 5)

    def test_star_step_anchored_at_domain_floor(self):
        result = next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.minute, 15)

    def test_ten_fifty_step_fifteen(self):
        # 10-50/15 on minute -> 10, 25, 40
        after = datetime(2026, 1, 1, 0, 0)
        r1 = next_fire("10-50/15 * * * *", after)
        self.assertEqual(r1.minute, 10)
        r2 = next_fire("10-50/15 * * * *", r1)
        self.assertEqual(r2.minute, 25)
        r3 = next_fire("10-50/15 * * * *", r2)
        self.assertEqual(r3.minute, 40)
        r4 = next_fire("10-50/15 * * * *", r3)
        # Wraps to the next hour's first match (10), not 55.
        self.assertEqual(r4.minute, 10)
        self.assertEqual(r4.hour, r3.hour + 1)


class Deviation3StrictDomainTests(unittest.TestCase):
    def test_dow_zero_is_sunday(self):
        # 2026-01-04 is a Sunday.
        self.assertEqual(
            next_fire("0 0 * * 0", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 4, 0, 0),
        )

    def test_dow_six_is_saturday(self):
        # 2026-01-03 is a Saturday.
        self.assertEqual(
            next_fire("0 0 * * 6", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 3, 0, 0),
        )

    def test_dow_seven_rejected(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1))


class Rule4WrapTests(unittest.TestCase):
    def test_minute_wrap_full_set(self):
        # 50-10 => {50..59, 0..10}; minute 11 through 49 must NOT match, so the
        # next fire from anywhere in that gap lands on :50 of the same hour.
        for minute in (11, 25, 49):
            after = datetime(2026, 1, 1, 0, minute)
            result = next_fire("50-10 * * * *", after)
            self.assertEqual(result, datetime(2026, 1, 1, 0, 50))

        # From inside {50..59}, the next match after :52 is :53 (still same hour).
        result = next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 52))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 53))

        # From :10 (the last value before the gap), next match jumps past the
        # whole gap to :50 of the same hour.
        result = next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 10))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 50))

    def test_month_wrap_excludes_middle_months(self):
        # nov-feb = {11,12,1,2}; starting in August should jump to November.
        self.assertEqual(
            next_fire("0 0 1 nov-feb *", datetime(2026, 8, 1, 0, 0)),
            datetime(2026, 11, 1, 0, 0),
        )

    def test_dow_wrap_excludes_middle_days(self):
        # fri-mon = {fri,sat,sun,mon}; tue/wed/thu excluded.
        # 2026-07-21 is a Tuesday -> next match is Friday 2026-07-24.
        self.assertEqual(
            next_fire("0 0 * * fri-mon", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )


class Rule5WrapStepTests(unittest.TestCase):
    def test_minute_wrap_step_excludes_non_stride(self):
        # 50-10/5 => {50,55,0,5,10}; minute 51 is not in the set.
        result = next_fire("50-10/5 * * * *", datetime(2026, 1, 1, 0, 51))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 55))

    def test_dow_wrap_step_set(self):
        # fri-tue/2 => {fri, sun, tue} = {5, 0, 2}; sat/mon excluded.
        after = datetime(2026, 7, 24, 0, 0)  # Friday
        r1 = next_fire("0 0 * * fri-tue/2", after)
        self.assertEqual(r1, datetime(2026, 7, 26, 0, 0))  # Sunday
        r2 = next_fire("0 0 * * fri-tue/2", r1)
        self.assertEqual(r2, datetime(2026, 7, 28, 0, 0))  # Tuesday
        r3 = next_fire("0 0 * * fri-tue/2", r2)
        self.assertEqual(r3, datetime(2026, 7, 31, 0, 0))  # Friday


class Rule6LastDayTests(unittest.TestCase):
    def test_l_alone_matches_last_day_every_month(self):
        result = next_fire("0 0 L * *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 3, 31, 0, 0))

    def test_l_with_restricted_dow_intersection(self):
        # Only fires when the last day of the month IS a Friday.
        result = next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.day, calendar_last_day(result.year, result.month))
        self.assertEqual((result.weekday() + 1) % 7, 5)

    def test_l_leap_year_february(self):
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2024, 1, 1, 0, 0)),
            datetime(2024, 2, 29, 0, 0),
        )


def calendar_last_day(year, month):
    import calendar as _cal
    return _cal.monthrange(year, month)[1]


class Rule7MonthLengthTests(unittest.TestCase):
    def test_day_31_only_fires_in_31_day_months(self):
        result = next_fire("0 0 31 * *", datetime(2026, 1, 31, 0, 0))
        # Feb, Apr, Jun, Sep, Nov (30 or 28 days) must all be skipped.
        self.assertEqual(result, datetime(2026, 3, 31, 0, 0))

    def test_february_29_only_leap_years(self):
        r1 = next_fire("0 0 29 feb *", datetime(2024, 3, 1, 0, 0))
        self.assertEqual(r1, datetime(2028, 2, 29, 0, 0))


class FieldGrammarTests(unittest.TestCase):
    def test_comma_list_numbers(self):
        after = datetime(2026, 1, 1, 0, 0)
        result = next_fire("0,30 * * * *", after)
        self.assertEqual(result.minute, 30)
        self.assertEqual(result.hour, 0)

    def test_comma_list_names(self):
        # mon,wed,fri; 2026-01-01 is Thursday -> next is Friday 2026-01-02.
        self.assertEqual(
            next_fire("0 0 * * mon,wed,fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 2, 0, 0),
        )

    def test_mixed_list_step(self):
        # 0-10/2,30 => {0,2,4,6,8,10,30}
        after = datetime(2026, 1, 1, 0, 0)
        result = next_fire("0-10/2,30 * * * *", after)
        self.assertEqual(result.minute, 2)

    def test_mixed_names_and_numbers(self):
        result = next_fire("0 0 1 jan,7 *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 1, 0, 0))

    def test_whitespace_tolerance(self):
        self.assertEqual(
            next_fire("  0   0  1  jan,jul   *  ", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 7, 1, 0, 0),
        )

    def test_single_value_field(self):
        self.assertEqual(
            next_fire("15 8 * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 8, 15),
        )


class AfterBoundaryTests(unittest.TestCase):
    def test_strictly_after_exact_minute_boundary(self):
        # If `after` is exactly on a firing minute, that minute is excluded.
        result = next_fire("0 0 * * *", datetime(2026, 1, 2, 0, 0, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 3, 0, 0))

    def test_seconds_round_up_to_next_minute(self):
        result = next_fire("* * * * *", datetime(2026, 1, 1, 12, 59, 1))
        self.assertEqual(result, datetime(2026, 1, 1, 13, 0))


if __name__ == "__main__":
    unittest.main()
