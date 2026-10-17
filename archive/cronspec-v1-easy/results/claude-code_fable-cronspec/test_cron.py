"""Standard-library unittest suite for cron.next_fire."""

import unittest
from datetime import datetime

from cron import next_fire


class TestWorkedExamples(unittest.TestCase):
    """Every worked example from the specification."""

    def test_star_step_minutes(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )

    def test_range_step_anchored_first(self):
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2026, 6, 1, 12, 5),
        )

    def test_range_step_anchored_second(self):
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5)),
            datetime(2026, 6, 1, 12, 15),
        )

    def test_range_step_anchored_10_50_15(self):
        self.assertEqual(
            next_fire("10-50/15 0 1 3 *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 3, 1, 0, 10),
        )

    def test_friday_the_13th(self):
        self.assertEqual(
            next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 2, 13, 0, 0),
        )

    def test_friday_the_13th_next(self):
        self.assertEqual(
            next_fire("0 0 13 * fri", datetime(2026, 2, 14, 0, 0)),
            datetime(2026, 3, 13, 0, 0),
        )

    def test_first_and_monday(self):
        self.assertEqual(
            next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0)),
            datetime(2027, 2, 1, 0, 0),
        )

    def test_next_monday_morning(self):
        self.assertEqual(
            next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 27, 9, 30),
        )

    def test_weekday_range_skips_weekend(self):
        self.assertEqual(
            next_fire("0 0 * * mon-fri", datetime(2026, 7, 25, 0, 0)),
            datetime(2026, 7, 27, 0, 0),
        )

    def test_month_name_list(self):
        self.assertEqual(
            next_fire("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 7, 1, 12, 0),
        )

    def test_dow_zero_is_sunday(self):
        self.assertEqual(
            next_fire("0 0 * * 0", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 26, 0, 0),
        )

    def test_minute_list_hour_range(self):
        self.assertEqual(
            next_fire("15,45 8-10 * * *", datetime(2026, 7, 22, 9, 20)),
            datetime(2026, 7, 22, 9, 45),
        )


class TestStrictlyAfterAndBoundaries(unittest.TestCase):
    def test_exact_match_is_excluded(self):
        # `after` itself matches the expression; result must be later.
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 2, 0, 0),
        )

    def test_nonzero_seconds_round_up_to_next_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 30)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_nonzero_microseconds_round_up_to_next_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 5, 0, 1)),
            datetime(2026, 1, 1, 0, 6),
        )

    def test_result_has_zero_seconds_and_microseconds(self):
        result = next_fire("*/7 3 * * *", datetime(2026, 5, 4, 2, 59, 59, 999999))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)
        self.assertEqual(result, datetime(2026, 5, 4, 3, 0))

    def test_hour_rollover(self):
        self.assertEqual(
            next_fire("0 * * * *", datetime(2026, 1, 1, 5, 59)),
            datetime(2026, 1, 1, 6, 0),
        )

    def test_day_rollover(self):
        self.assertEqual(
            next_fire("30 8 * * *", datetime(2026, 1, 1, 9, 0)),
            datetime(2026, 1, 2, 8, 30),
        )

    def test_year_rollover(self):
        self.assertEqual(
            next_fire("0 0 1 1 *", datetime(2026, 12, 31, 23, 59)),
            datetime(2027, 1, 1, 0, 0),
        )

    def test_leap_day(self):
        self.assertEqual(
            next_fire("0 0 29 2 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )

    def test_short_month_skipped(self):
        # Day 31 does not exist in April; jumps to May 31.
        self.assertEqual(
            next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0)),
            datetime(2026, 5, 31, 0, 0),
        )


class TestWhitespaceHandling(unittest.TestCase):
    def test_leading_trailing_and_multiple_spaces(self):
        self.assertEqual(
            next_fire("  */15   *  * *   *  ", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )

    def test_tab_separated_fields(self):
        self.assertEqual(
            next_fire("0\t12\t*\t*\t*", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 12, 0),
        )


class TestDeviation1Intersection(unittest.TestCase):
    """dom and dow combine with AND, never the vixie-cron union."""

    def test_not_union_every_13th(self):
        # Union semantics would fire Fri Jan 2, 2026; intersection must not.
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertNotEqual(result, datetime(2026, 1, 2, 0, 0))
        self.assertNotEqual(result, datetime(2026, 1, 13, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 13, 0, 0))

    def test_result_satisfies_both_constraints(self):
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.day, 13)
        self.assertEqual(result.weekday(), 4)  # Python: Friday == 4

    def test_dom_star_dow_decides(self):
        # Only dow restricted: every Friday.
        self.assertEqual(
            next_fire("0 0 * * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 2, 0, 0),
        )

    def test_dow_star_dom_decides(self):
        # Only dom restricted: every 13th.
        self.assertEqual(
            next_fire("0 0 13 * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 13, 0, 0),
        )

    def test_both_star_every_day(self):
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 1, 1, 12, 0)),
            datetime(2026, 1, 2, 0, 0),
        )

    def test_stepped_dow_counts_as_restricted(self):
        # dow */2 = {0,2,4,6}; dom 14. Jan 14, 2026 is a Wednesday
        # (dow 3, no match); Feb 14 is a Saturday (dow 6, match).
        self.assertEqual(
            next_fire("0 0 14 * */2", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 2, 14, 0, 0),
        )

    def test_ranged_dom_counts_as_restricted(self):
        # dom 1-7 AND dow mon: the first Monday of a month.
        result = next_fire("0 0 1-7 * mon", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 8, 3, 0, 0))


class TestDeviation2StepAnchoring(unittest.TestCase):
    """A-B/s starts at A with stride s, not at multiples of s."""

    def _fires_in_hour(self, expr, start):
        """Collect all fire minutes within the hour following `start`."""
        fires = []
        current = start
        while True:
            current = next_fire(expr, current)
            if current >= start.replace(hour=start.hour + 1, minute=0):
                break
            fires.append(current.minute)
        return fires

    def test_5_30_10_yields_5_15_25(self):
        self.assertEqual(
            self._fires_in_hour("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 0)),
            [5, 15, 25],
        )

    def test_10_50_15_yields_10_25_40(self):
        self.assertEqual(
            self._fires_in_hour("10-50/15 0 1 3 *", datetime(2026, 3, 1, 0, 0)),
            [10, 25, 40],
        )

    def test_star_step_anchored_at_domain_floor(self):
        self.assertEqual(
            self._fires_in_hour("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
            [15, 30, 45],  # :00 excluded (strictly after), then 15,30,45
        )

    def test_star_step_includes_domain_floor(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 46)),
            datetime(2026, 1, 1, 1, 0),
        )

    def test_step_on_month_domain_floor_is_1(self):
        # Month */5 anchors at 1: months {1, 6, 11}.
        self.assertEqual(
            next_fire("0 0 1 */5 *", datetime(2026, 1, 15, 0, 0)),
            datetime(2026, 6, 1, 0, 0),
        )

    def test_step_on_dom_domain_floor_is_1(self):
        # Day-of-month */10 anchors at 1: days {1, 11, 21, 31}.
        self.assertEqual(
            next_fire("0 0 */10 1 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 11, 0, 0),
        )

    def test_name_range_with_step(self):
        # mon-fri/2 anchors at mon: {mon, wed, fri}. 2026-07-22 is a
        # Wednesday, so from Tuesday the next fire is Wednesday.
        self.assertEqual(
            next_fire("0 0 * * mon-fri/2", datetime(2026, 7, 21, 12, 0)),
            datetime(2026, 7, 22, 0, 0),
        )
        # ...and Thursday is skipped in favor of Friday.
        self.assertEqual(
            next_fire("0 0 * * mon-fri/2", datetime(2026, 7, 22, 12, 0)),
            datetime(2026, 7, 24, 0, 0),
        )

    def test_step_larger_than_range_keeps_only_start(self):
        # 20-40/60 -> {20} only.
        self.assertEqual(
            next_fire("20-40/60 * * * *", datetime(2026, 1, 1, 0, 25)),
            datetime(2026, 1, 1, 1, 20),
        )


class TestListsRangesAndNames(unittest.TestCase):
    def test_plain_list(self):
        self.assertEqual(
            next_fire("1,3,5 * * * *", datetime(2026, 1, 1, 0, 3)),
            datetime(2026, 1, 1, 0, 5),
        )

    def test_dow_name_list(self):
        self.assertEqual(
            next_fire("0 0 * * mon,wed,fri", datetime(2026, 7, 22, 12, 0)),
            datetime(2026, 7, 24, 0, 0),
        )

    def test_mixed_numbers_and_names_in_list(self):
        # 1 = Monday, fri = 5. From Wed 2026-07-22, next is Fri.
        self.assertEqual(
            next_fire("0 0 * * 1,fri", datetime(2026, 7, 22, 12, 0)),
            datetime(2026, 7, 24, 0, 0),
        )

    def test_list_mixing_stepped_range_and_value(self):
        # 0-10/2,30 -> {0,2,4,6,8,10,30}
        self.assertEqual(
            next_fire("0-10/2,30 * * * *", datetime(2026, 1, 1, 0, 10)),
            datetime(2026, 1, 1, 0, 30),
        )
        self.assertEqual(
            next_fire("0-10/2,30 * * * *", datetime(2026, 1, 1, 0, 30)),
            datetime(2026, 1, 1, 1, 0),
        )

    def test_month_name_range(self):
        self.assertEqual(
            next_fire("0 0 1 mar-may *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 3, 1, 0, 0),
        )

    def test_all_twelve_month_names(self):
        names = "jan feb mar apr may jun jul aug sep oct nov dec".split()
        for number, name in enumerate(names, start=1):
            with self.subTest(month=name):
                after = datetime(2025, 12, 31, 0, 0)
                self.assertEqual(
                    next_fire("0 0 1 %s *" % name, after),
                    datetime(2026, number, 1, 0, 0),
                )

    def test_all_seven_dow_names(self):
        # Week of 2026-07-19 (Sunday) through 2026-07-25 (Saturday).
        names = "sun mon tue wed thu fri sat".split()
        for offset, name in enumerate(names):
            with self.subTest(dow=name):
                after = datetime(2026, 7, 18, 12, 0)  # Saturday
                self.assertEqual(
                    next_fire("0 0 * * %s" % name, after),
                    datetime(2026, 7, 19 + offset, 0, 0),
                )

    def test_numeric_dow_matches_named_dow(self):
        after = datetime(2026, 7, 22, 0, 0)
        for number, name in enumerate("sun mon tue wed thu fri sat".split()):
            with self.subTest(dow=number):
                self.assertEqual(
                    next_fire("0 0 * * %d" % number, after),
                    next_fire("0 0 * * %s" % name, after),
                )

    def test_single_value_range_endpoints_equal(self):
        # A-B with A == B is a valid (degenerate) range.
        self.assertEqual(
            next_fire("30-30 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 30),
        )

    def test_domain_extremes_accepted(self):
        self.assertEqual(
            next_fire("59 23 31 12 6", datetime(2026, 1, 1, 0, 0)),
            # 2026-10-31 is a Saturday but not December; 2026-12-31 is a
            # Thursday, so the first Saturday Dec 31 is in 2033.
            datetime(2033, 12, 31, 23, 59),
        )

    def test_minute_zero_and_hour_zero(self):
        self.assertEqual(
            next_fire("0 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2027, 1, 1, 0, 0),
        )


class TestErrors(unittest.TestCase):
    AFTER = datetime(2026, 1, 1, 0, 0)

    def assert_invalid(self, expr):
        with self.assertRaises(ValueError, msg="expr %r should be invalid" % expr):
            next_fire(expr, self.AFTER)

    # -- field count ----------------------------------------------------
    def test_empty_expression(self):
        self.assert_invalid("")

    def test_whitespace_only_expression(self):
        self.assert_invalid("   ")

    def test_four_fields(self):
        self.assert_invalid("* * * *")

    def test_six_fields(self):
        self.assert_invalid("* * * * * *")

    # -- domain violations ----------------------------------------------
    def test_minute_out_of_domain(self):
        self.assert_invalid("60 * * * *")

    def test_hour_out_of_domain(self):
        self.assert_invalid("* 24 * * *")

    def test_dom_zero(self):
        self.assert_invalid("* * 0 * *")

    def test_dom_32(self):
        self.assert_invalid("* * 32 * *")

    def test_month_zero(self):
        self.assert_invalid("* * * 0 *")

    def test_month_13(self):
        self.assert_invalid("* * * 13 *")

    def test_dow_7_invalid(self):
        # Deviation 3: no vixie-style 7-as-Sunday alias.
        self.assert_invalid("* * * * 7")

    def test_negative_value(self):
        self.assert_invalid("* * * * -1")

    def test_range_endpoint_out_of_domain(self):
        self.assert_invalid("0-60 * * * *")

    # -- names -----------------------------------------------------------
    def test_uppercase_dow_name(self):
        self.assert_invalid("* * * * SUN")

    def test_uppercase_month_name(self):
        self.assert_invalid("* * * JAN *")

    def test_mixed_case_name(self):
        self.assert_invalid("* * * Jan *")

    def test_lowercase_month_name_is_valid(self):
        # Sanity check on the flip side: 'jan' in the month field is fine.
        self.assertEqual(
            next_fire("* * * jan *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_month_name_in_dow_field(self):
        self.assert_invalid("* * * * jan")

    def test_dow_name_in_month_field(self):
        self.assert_invalid("* * * mon *")

    def test_name_in_minute_field(self):
        self.assert_invalid("mon * * * *")

    # -- steps -----------------------------------------------------------
    def test_step_zero(self):
        self.assert_invalid("*/0 * * * *")

    def test_step_negative(self):
        self.assert_invalid("*/-1 * * * *")

    def test_range_step_zero(self):
        self.assert_invalid("1-5/0 * * * *")

    def test_step_on_single_value(self):
        self.assert_invalid("5/10 * * * *")

    def test_step_on_single_name(self):
        self.assert_invalid("* * * * mon/2")

    def test_step_not_integer(self):
        self.assert_invalid("*/x * * * *")

    def test_empty_step(self):
        self.assert_invalid("*/ * * * *")

    # -- ranges ----------------------------------------------------------
    def test_reversed_numeric_range(self):
        self.assert_invalid("30-5 * * * *")

    def test_reversed_name_range(self):
        self.assert_invalid("* * * * fri-mon")

    def test_dangling_range(self):
        self.assert_invalid("* * * * mon-")

    def test_range_missing_start(self):
        self.assert_invalid("-5 * * * *")

    def test_double_range(self):
        self.assert_invalid("1-2-3 * * * *")

    # -- lists -----------------------------------------------------------
    def test_empty_list_element_middle(self):
        self.assert_invalid("1,,3 * * * *")

    def test_trailing_comma(self):
        self.assert_invalid("1, * * * *")

    def test_leading_comma(self):
        self.assert_invalid(",1 * * * *")

    # -- unknown tokens ---------------------------------------------------
    def test_trailing_garbage_on_number(self):
        self.assert_invalid("1x * * * *")

    def test_unknown_word(self):
        self.assert_invalid("abc * * * *")

    def test_plus_prefixed_number(self):
        self.assert_invalid("+5 * * * *")

    def test_float_value(self):
        self.assert_invalid("1.5 * * * *")


if __name__ == "__main__":
    unittest.main()
