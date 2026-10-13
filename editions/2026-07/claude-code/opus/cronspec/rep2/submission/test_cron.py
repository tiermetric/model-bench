"""Standard-library unittest suite for cron.next_fire.

Covers the grammar, the three deviations (intersection day matching,
range-anchored steps, strict domains), the four hardening rules (wrapping
ranges, steps through the wrap, ``L``, month-length/leap handling), every
worked example from the spec, and every error case.
"""

import calendar
import unittest
from datetime import datetime, timedelta

from cron import next_fire


def fires(expr, start, count):
    """The next ``count`` fire times of ``expr`` after ``start``."""
    out = []
    current = start
    for _ in range(count):
        current = next_fire(expr, current)
        out.append(current)
    return out


class TestBasics(unittest.TestCase):
    def test_every_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_strictly_after(self):
        # An exact match at `after` is not returned; the next one is.
        self.assertEqual(
            next_fire("0 12 * * *", datetime(2026, 3, 4, 12, 0)),
            datetime(2026, 3, 5, 12, 0),
        )

    def test_seconds_and_microseconds_round_up_to_next_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 30, 500000)),
            datetime(2026, 1, 1, 0, 1),
        )
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 0, 1)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_result_has_zero_seconds_and_microseconds(self):
        got = next_fire("*/7 * * * *", datetime(2026, 5, 5, 3, 21, 44, 999999))
        self.assertEqual(got.second, 0)
        self.assertEqual(got.microsecond, 0)

    def test_surrounding_and_inner_whitespace_is_ignored(self):
        expected = datetime(2026, 1, 1, 0, 15)
        for expr in ("*/15 * * * *", "  */15 * * * *  ", "*/15   *  * *    *",
                     "\t*/15 * * * *\n"):
            self.assertEqual(next_fire(expr, datetime(2026, 1, 1, 0, 0)), expected, expr)

    def test_single_values(self):
        self.assertEqual(
            next_fire("30 4 * * *", datetime(2026, 9, 9, 5, 0)),
            datetime(2026, 9, 10, 4, 30),
        )

    def test_comma_list(self):
        self.assertEqual(
            [dt.minute for dt in fires("1,3,5 0 1 1 *", datetime(2026, 1, 1, 0, 0), 3)],
            [1, 3, 5],
        )

    def test_mixed_names_and_numbers_in_a_list(self):
        # month list mixing a name and a number
        self.assertEqual(
            next_fire("0 0 1 jan,7 *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 7, 1, 0, 0),
        )
        # dow list mixing a number and a name: 1 (Mon) or fri
        got = fires("0 0 * * 1,fri", datetime(2026, 7, 21, 0, 0), 3)
        self.assertEqual(
            got,
            [datetime(2026, 7, 24), datetime(2026, 7, 27), datetime(2026, 7, 31)],
        )

    def test_month_names(self):
        for i, name in enumerate(
            ["jan", "feb", "mar", "apr", "may", "jun",
             "jul", "aug", "sep", "oct", "nov", "dec"], start=1
        ):
            by_name = next_fire("0 0 1 %s *" % name, datetime(2026, 1, 1, 0, 0))
            by_num = next_fire("0 0 1 %d *" % i, datetime(2026, 1, 1, 0, 0))
            self.assertEqual(by_name, by_num, name)
            self.assertEqual(by_name.month, i)

    def test_dow_names_sunday_is_zero(self):
        for i, name in enumerate(
            ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
        ):
            by_name = next_fire("0 0 * * %s" % name, datetime(2026, 7, 21, 0, 0))
            by_num = next_fire("0 0 * * %d" % i, datetime(2026, 7, 21, 0, 0))
            self.assertEqual(by_name, by_num, name)
            # Python: Monday == 0; cron here: Sunday == 0
            self.assertEqual((by_name.weekday() + 1) % 7, i, name)

    def test_star_in_every_field(self):
        self.assertEqual(
            fires("0 0 * * *", datetime(2026, 12, 30, 0, 0), 3),
            [datetime(2026, 12, 31), datetime(2027, 1, 1), datetime(2027, 1, 2)],
        )

    def test_rollover_across_hour_day_month_year(self):
        self.assertEqual(
            next_fire("0 * * * *", datetime(2026, 1, 1, 0, 30)),
            datetime(2026, 1, 1, 1, 0),
        )
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 1, 31, 12, 0)),
            datetime(2026, 2, 1, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 1 * *", datetime(2026, 12, 15, 0, 0)),
            datetime(2027, 1, 1, 0, 0),
        )


class TestDeviation1Intersection(unittest.TestCase):
    def test_friday_the_thirteenth(self):
        self.assertEqual(
            next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 2, 13, 0, 0),
        )

    def test_first_of_month_that_is_a_monday(self):
        self.assertEqual(
            next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0)),
            datetime(2027, 2, 1, 0, 0),
        )

    def test_intersection_not_union(self):
        # Under vixie's union rule the next fire would be 2026-01-02 (a Friday)
        # or 2026-01-13 (the 13th). Intersection gives neither.
        got = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertNotEqual(got, datetime(2026, 1, 2, 0, 0))
        self.assertNotEqual(got, datetime(2026, 1, 13, 0, 0))
        self.assertEqual(got.day, 13)
        self.assertEqual(got.weekday(), 4)  # Friday

    def test_dom_alone_decides_when_dow_is_star(self):
        self.assertEqual(
            fires("0 0 13 * *", datetime(2026, 1, 1, 0, 0), 2),
            [datetime(2026, 1, 13), datetime(2026, 2, 13)],
        )

    def test_dow_alone_decides_when_dom_is_star(self):
        self.assertEqual(
            next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 27, 9, 30),
        )

    def test_both_star_matches_every_day(self):
        self.assertEqual(
            fires("0 0 * * *", datetime(2026, 7, 21, 0, 0), 3),
            [datetime(2026, 7, 22), datetime(2026, 7, 23), datetime(2026, 7, 24)],
        )

    def test_step_dom_counts_as_restricted(self):
        # dom */2 -> odd days; dow mon. Intersection: odd-numbered Mondays.
        for dt in fires("0 0 */2 * mon", datetime(2026, 7, 1, 0, 0), 4):
            self.assertEqual(dt.day % 2, 1, dt)
            self.assertEqual(dt.weekday(), 0, dt)

    def test_range_dow_counts_as_restricted(self):
        # dom 1-7 (first week) AND dow 1-5 (weekdays) -> first weekday-ish days
        for dt in fires("0 0 1-7 * 1-5", datetime(2026, 7, 1, 0, 0), 8):
            self.assertTrue(1 <= dt.day <= 7, dt)
            self.assertTrue(dt.weekday() <= 4, dt)


class TestDeviation2StepAnchor(unittest.TestCase):
    def test_step_anchored_at_range_start(self):
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2026, 6, 1, 12, 5),
        )
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5)),
            datetime(2026, 6, 1, 12, 15),
        )

    def test_full_matching_set_for_5_30_by_10(self):
        got = fires("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0), 3)
        self.assertEqual([dt.minute for dt in got], [5, 15, 25])
        # 30 itself is NOT a match (5 + 10k never lands on 30)
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 25)),
            datetime(2027, 6, 1, 12, 5),
        )

    def test_not_multiples_of_step(self):
        # Standard "every multiple of 10 within [5,30]" would give 10, 20, 30.
        got = [dt.minute for dt in fires("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0), 3)]
        self.assertNotIn(10, got)
        self.assertNotIn(20, got)
        self.assertNotIn(30, got)

    def test_10_50_by_15(self):
        got = fires("10-50/15 0 1 1 *", datetime(2026, 1, 1, 0, 0), 3)
        self.assertEqual([dt.minute for dt in got], [10, 25, 40])

    def test_star_step_uses_domain_floor(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )
        got = fires("*/15 0 1 1 *", datetime(2026, 1, 1, 0, 0), 4)
        self.assertEqual([dt.minute for dt in got], [15, 30, 45, 0])

    def test_star_step_hour_floor_is_zero(self):
        got = fires("0 */5 1 1 *", datetime(2026, 1, 1, 0, 0), 5)
        self.assertEqual([dt.hour for dt in got], [5, 10, 15, 20, 0])

    def test_star_step_day_of_month_floor_is_one(self):
        got = fires("0 0 */10 1 *", datetime(2026, 1, 1, 0, 0), 4)
        self.assertEqual([dt.day for dt in got], [11, 21, 31, 1])

    def test_star_step_month_floor_is_one(self):
        got = fires("0 0 1 */4 *", datetime(2026, 1, 1, 0, 0), 3)
        self.assertEqual([dt.month for dt in got], [5, 9, 1])

    def test_star_step_dow_floor_is_zero(self):
        # */3 on dow -> {0, 3, 6} = sun, wed, sat
        for dt in fires("0 0 * * */3", datetime(2026, 7, 21, 0, 0), 6):
            self.assertIn((dt.weekday() + 1) % 7, {0, 3, 6}, dt)

    def test_step_of_one_is_the_whole_range(self):
        got = fires("10-13/1 0 1 1 *", datetime(2026, 1, 1, 0, 0), 4)
        self.assertEqual([dt.minute for dt in got], [10, 11, 12, 13])

    def test_step_larger_than_range_yields_only_the_start(self):
        got = fires("10-13/99 0 1 1 *", datetime(2026, 1, 1, 0, 0), 2)
        self.assertEqual([dt.minute for dt in got], [10, 10])
        self.assertEqual([dt.year for dt in got], [2026, 2027])

    def test_step_inside_a_list(self):
        got = fires("0-10/2,30 0 1 1 *", datetime(2026, 1, 1, 0, 0), 7)
        # 00:00 itself is skipped (strictly after), so it reappears next year.
        self.assertEqual([dt.minute for dt in got], [2, 4, 6, 8, 10, 30, 0])
        self.assertEqual(got[-1], datetime(2027, 1, 1, 0, 0))


class TestDeviation3StrictDomains(unittest.TestCase):
    def test_dow_seven_is_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1))

    def test_dow_seven_invalid_anywhere_it_appears(self):
        for expr in ("* * * * 0,7", "* * * * 5-7", "* * * * 7-1", "* * * * 7/2"):
            with self.assertRaises(ValueError, msg=expr):
                next_fire(expr, datetime(2026, 1, 1))

    def test_dow_six_is_valid(self):
        got = next_fire("0 0 * * 6", datetime(2026, 7, 21, 0, 0))
        self.assertEqual(got.weekday(), 5)  # Saturday

    def test_uppercase_names_rejected(self):
        for expr in ("* * * * SUN", "* * * * Sun", "* * * JAN *", "* * * Jan *",
                     "* * * * FRI"):
            with self.assertRaises(ValueError, msg=expr):
                next_fire(expr, datetime(2026, 1, 1))

    def test_domain_edges_accepted(self):
        for expr in ("0 0 1 1 0", "59 23 31 12 6"):
            next_fire(expr, datetime(2026, 1, 1))

    def test_out_of_domain_rejected(self):
        for expr in ("60 * * * *", "* 24 * * *", "* * 0 * *", "* * 32 * *",
                     "* * * 0 *", "* * * 13 *", "* * * * 7"):
            with self.assertRaises(ValueError, msg=expr):
                next_fire(expr, datetime(2026, 1, 1))


class TestRule4WrappingRanges(unittest.TestCase):
    def test_minute_wrap(self):
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30)),
            datetime(2026, 1, 1, 0, 50),
        )
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 55)),
            datetime(2026, 1, 1, 0, 56),
        )

    def test_minute_wrap_full_set(self):
        got = [dt.minute for dt in fires("50-10 0 1 1 *", datetime(2026, 1, 1, 0, 0), 21)]
        self.assertEqual(got, list(range(1, 11)) + list(range(50, 60)) + [0])

    def test_month_wrap(self):
        self.assertEqual(
            next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 11, 1, 0, 0),
        )
        got = fires("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0), 4)
        self.assertEqual([dt.month for dt in got], [11, 12, 1, 2])
        self.assertEqual([dt.year for dt in got], [2026, 2026, 2027, 2027])

    def test_dow_wrap(self):
        self.assertEqual(
            next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )
        got = fires("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0), 4)
        self.assertEqual(
            got,
            [datetime(2026, 7, 24), datetime(2026, 7, 25),
             datetime(2026, 7, 26), datetime(2026, 7, 27)],
        )

    def test_numeric_dow_wrap(self):
        # 5-1 == fri-mon
        self.assertEqual(
            fires("0 0 * * 5-1", datetime(2026, 7, 21, 0, 0), 4),
            fires("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0), 4),
        )

    def test_hour_wrap(self):
        got = [dt.hour for dt in fires("0 22-2 1 1 *", datetime(2026, 1, 1, 0, 30), 4)]
        self.assertEqual(got, [1, 2, 22, 23])

    def test_day_of_month_wrap(self):
        # 30-2 on dom -> {30, 31, 1, 2} (days that exist in the month)
        got = fires("0 0 30-2 1 *", datetime(2026, 1, 1, 0, 0), 4)
        self.assertEqual([dt.day for dt in got], [2, 30, 31, 1])

    def test_wrap_is_valid_not_an_error(self):
        for expr in ("30-5 * * * *", "0 0 * nov-feb *", "0 0 * * fri-mon",
                     "0 20-3 * * *", "0 0 25-5 * *"):
            next_fire(expr, datetime(2026, 1, 1))  # must not raise

    def test_equal_endpoints_is_a_single_value(self):
        got = fires("5-5 0 1 1 *", datetime(2026, 1, 1, 0, 0), 2)
        self.assertEqual([dt.minute for dt in got], [5, 5])
        self.assertEqual([dt.year for dt in got], [2026, 2027])

    def test_wrap_endpoints_must_be_in_domain(self):
        for expr in ("70-10 * * * *", "50-70 * * * *", "* 30-2 * * *",
                     "* * * 13-2 *", "* * * * 6-7"):
            with self.assertRaises(ValueError, msg=expr):
                next_fire(expr, datetime(2026, 1, 1))


class TestRule5StepThroughWrap(unittest.TestCase):
    def test_minute_wrap_step(self):
        self.assertEqual(
            next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 5),
        )

    def test_minute_wrap_step_full_set(self):
        got = [dt.minute for dt in fires("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0), 5)]
        self.assertEqual(got, [5, 10, 50, 55, 0])  # {50,55,0,5,10} in clock order

    def test_dow_wrap_step(self):
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )

    def test_dow_wrap_step_set_is_fri_sun_tue(self):
        got = fires("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0), 6)
        for dt in got:
            self.assertIn((dt.weekday() + 1) % 7, {5, 0, 2}, dt)
        # sat (6) and mon (1) are in the range but skipped by the stride
        self.assertNotIn(6, [(dt.weekday() + 1) % 7 for dt in got])
        self.assertNotIn(1, [(dt.weekday() + 1) % 7 for dt in got])

    def test_month_wrap_step(self):
        # nov-feb order: 11,12,1,2 -> every 2nd -> {11, 1}
        got = fires("0 0 1 nov-feb/2 *", datetime(2026, 3, 1, 0, 0), 4)
        self.assertEqual([dt.month for dt in got], [11, 1, 11, 1])
        self.assertEqual([dt.year for dt in got], [2026, 2027, 2027, 2028])

    def test_hour_wrap_step(self):
        # 22-2 order: 22,23,0,1,2 -> every 3rd -> {22, 1}
        got = [dt.hour for dt in fires("0 22-2/3 1 1 *", datetime(2026, 1, 1, 0, 0), 2)]
        self.assertEqual(got, [1, 22])


class TestRule6LastDay(unittest.TestCase):
    def test_last_day_of_january(self):
        self.assertEqual(
            next_fire("0 0 L * *", datetime(2026, 1, 15, 0, 0)),
            datetime(2026, 1, 31, 0, 0),
        )

    def test_last_day_of_non_leap_february(self):
        self.assertEqual(
            next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0)),
            datetime(2026, 2, 28, 0, 0),
        )

    def test_last_day_of_leap_february(self):
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )

    def test_last_day_every_month_for_a_year(self):
        got = fires("0 0 L * *", datetime(2026, 1, 1, 0, 0), 12)
        expected = [
            datetime(2026, m, calendar.monthrange(2026, m)[1]) for m in range(1, 13)
        ]
        self.assertEqual(got, expected)
        self.assertEqual([dt.day for dt in got],
                         [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])

    def test_last_day_intersected_with_dow(self):
        self.assertEqual(
            next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 7, 31, 0, 0),
        )

    def test_last_day_with_dow_always_lands_on_that_dow(self):
        for dt in fires("0 0 L * fri", datetime(2026, 1, 1, 0, 0), 5):
            self.assertEqual(dt.weekday(), 4, dt)
            self.assertEqual(dt.day, calendar.monthrange(dt.year, dt.month)[1], dt)

    def test_last_day_respects_hour_and_minute_fields(self):
        self.assertEqual(
            next_fire("45 23 L 12 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 12, 31, 23, 45),
        )

    def test_L_may_not_be_combined(self):
        for expr in ("0 0 L,15 * *", "0 0 15,L * *", "0 0 L-5 * *",
                     "0 0 1-L * *", "0 0 L/2 * *", "0 0 *,L * *"):
            with self.assertRaises(ValueError, msg=expr):
                next_fire(expr, datetime(2026, 1, 1))

    def test_L_only_in_day_of_month_field(self):
        for expr in ("L * * * *", "0 L * * *", "0 0 * L *", "0 0 * * L"):
            with self.assertRaises(ValueError, msg=expr):
                next_fire(expr, datetime(2026, 1, 1))

    def test_lowercase_l_is_not_L(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 l * *", datetime(2026, 1, 1))


class TestRule7MonthLengthsAndLeapYears(unittest.TestCase):
    def test_day_31_skips_short_months(self):
        self.assertEqual(
            next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0)),
            datetime(2026, 5, 31, 0, 0),
        )

    def test_day_31_only_in_31_day_months(self):
        got = fires("0 0 31 * *", datetime(2026, 1, 1, 0, 0), 7)
        self.assertEqual(
            [(dt.year, dt.month) for dt in got],
            [(2026, 1), (2026, 3), (2026, 5), (2026, 7),
             (2026, 8), (2026, 10), (2026, 12)],
        )

    def test_day_30_skips_february(self):
        self.assertEqual(
            next_fire("0 0 30 * *", datetime(2026, 2, 1, 0, 0)),
            datetime(2026, 3, 30, 0, 0),
        )

    def test_feb_29_only_in_leap_years(self):
        self.assertEqual(
            next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )

    def test_feb_29_sequence_is_every_four_years(self):
        got = fires("0 0 29 feb *", datetime(2020, 3, 1, 0, 0), 3)
        self.assertEqual(got, [datetime(2024, 2, 29), datetime(2028, 2, 29),
                               datetime(2032, 2, 29)])

    def test_feb_29_skips_the_century_non_leap_year(self):
        # 2100 is not a leap year (divisible by 100, not by 400).
        self.assertEqual(
            next_fire("0 0 29 feb *", datetime(2096, 3, 1, 0, 0)),
            datetime(2104, 2, 29, 0, 0),
        )

    def test_no_clamping_onto_the_last_day(self):
        got = next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0))
        self.assertNotEqual(got, datetime(2026, 4, 30, 0, 0))
        self.assertEqual(got.day, 31)

    def test_day_31_in_february_search_crosses_years(self):
        self.assertEqual(
            next_fire("0 0 31 1 *", datetime(2026, 2, 1, 0, 0)),
            datetime(2027, 1, 31, 0, 0),
        )


class TestWorkedExamples(unittest.TestCase):
    """Every example from the spec, verbatim."""

    CASES = [
        ("*/15 * * * *", datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 15)),
        ("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0), datetime(2026, 6, 1, 12, 5)),
        ("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5), datetime(2026, 6, 1, 12, 15)),
        ("0 0 13 * fri", datetime(2026, 1, 1, 0, 0), datetime(2026, 2, 13, 0, 0)),
        ("0 0 1 * mon", datetime(2026, 6, 2, 0, 0), datetime(2027, 2, 1, 0, 0)),
        ("30 9 * * mon", datetime(2026, 7, 22, 0, 0), datetime(2026, 7, 27, 9, 30)),
        ("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0), datetime(2026, 7, 1, 12, 0)),
        ("50-10 * * * *", datetime(2026, 1, 1, 0, 30), datetime(2026, 1, 1, 0, 50)),
        ("50-10 * * * *", datetime(2026, 1, 1, 0, 55), datetime(2026, 1, 1, 0, 56)),
        ("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0), datetime(2026, 11, 1, 0, 0)),
        ("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0), datetime(2026, 7, 24, 0, 0)),
        ("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 5)),
        ("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0), datetime(2026, 7, 24, 0, 0)),
        ("0 0 L * *", datetime(2026, 1, 15, 0, 0), datetime(2026, 1, 31, 0, 0)),
        ("0 0 L * *", datetime(2026, 2, 1, 0, 0), datetime(2026, 2, 28, 0, 0)),
        ("0 0 L 2 *", datetime(2027, 3, 1, 0, 0), datetime(2028, 2, 29, 0, 0)),
        ("0 0 L * fri", datetime(2026, 1, 1, 0, 0), datetime(2026, 7, 31, 0, 0)),
        ("0 0 31 * *", datetime(2026, 4, 1, 0, 0), datetime(2026, 5, 31, 0, 0)),
        ("0 0 30 * *", datetime(2026, 2, 1, 0, 0), datetime(2026, 3, 30, 0, 0)),
        ("0 0 29 feb *", datetime(2027, 1, 1, 0, 0), datetime(2028, 2, 29, 0, 0)),
    ]

    def test_worked_examples(self):
        for expr, after, expected in self.CASES:
            with self.subTest(expr=expr, after=after):
                self.assertEqual(next_fire(expr, after), expected)


class TestErrors(unittest.TestCase):
    SPEC_ERRORS = [
        "",                 # empty
        "* * * *",          # 4 fields
        "* * * * * *",      # 6 fields
        "60 * * * *",       # minute out of domain
        "* 24 * * *",       # hour out of domain
        "* * 0 * *",        # day-of-month out of domain
        "* * * 13 *",       # month out of domain
        "* * * * 7",        # dow 7 is invalid
        "* * * * SUN",      # uppercase name
        "*/0 * * * *",      # step <= 0
        "5/10 * * * *",     # step on a single value
        "1,,3 * * * *",     # empty list element
        "1x * * * *",       # unknown token
        "70-10 * * * *",    # wrap endpoint out of domain
        "0 0 L,15 * *",     # L may not be combined
        "0 0 * * L",        # L only in day-of-month
        "0 0 l * *",        # lowercase l
    ]

    def test_spec_error_cases(self):
        for expr in self.SPEC_ERRORS:
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, datetime(2026, 1, 1, 0, 0))

    def test_wrong_field_counts(self):
        for expr in ("", "   ", "*", "* *", "* * *", "* * * *",
                     "* * * * * *", "* * * * * * *"):
            with self.assertRaises(ValueError, msg=repr(expr)):
                next_fire(expr, datetime(2026, 1, 1))

    def test_bad_steps(self):
        for expr in ("*/0 * * * *", "1-5/0 * * * *", "*/-1 * * * *",
                     "*/x * * * *", "*/ * * * *", "/5 * * * *",
                     "*/2/3 * * * *", "1-5/2/3 * * * *"):
            with self.assertRaises(ValueError, msg=expr):
                next_fire(expr, datetime(2026, 1, 1))

    def test_step_on_single_value(self):
        for expr in ("5/10 * * * *", "* 3/2 * * *", "* * * jan/2 *",
                     "* * * * mon/2"):
            with self.assertRaises(ValueError, msg=expr):
                next_fire(expr, datetime(2026, 1, 1))

    def test_malformed_lists(self):
        for expr in ("1,,3 * * * *", ",1 * * * *", "1, * * * *", "1,2, * * * *",
                     ", * * * *"):
            with self.assertRaises(ValueError, msg=expr):
                next_fire(expr, datetime(2026, 1, 1))

    def test_malformed_ranges(self):
        for expr in ("1-2-3 * * * *", "-5 * * * *", "5- * * * *", "- * * * *"):
            with self.assertRaises(ValueError, msg=expr):
                next_fire(expr, datetime(2026, 1, 1))

    def test_unknown_tokens(self):
        for expr in ("1x * * * *", "abc * * * *", "* * * xyz *", "* * * * funday",
                     "1.5 * * * *", "+5 * * * *", "* * * * mo"):
            with self.assertRaises(ValueError, msg=expr):
                next_fire(expr, datetime(2026, 1, 1))

    def test_names_not_allowed_in_numeric_fields(self):
        for expr in ("jan * * * *", "* mon * * *", "* * mon * *"):
            with self.assertRaises(ValueError, msg=expr):
                next_fire(expr, datetime(2026, 1, 1))

    def test_non_string_expression(self):
        with self.assertRaises(ValueError):
            next_fire(None, datetime(2026, 1, 1))

    def test_valid_expressions_do_not_raise(self):
        # These look like errors to a standard parser but are valid here.
        for expr in ("30-5 * * * *", "0 0 * nov-feb *", "0 0 * * fri-mon",
                     "50-10/5 * * * *", "0 0 L * *", "0 0 * * fri-tue/2"):
            next_fire(expr, datetime(2026, 1, 1))


class TestConsistency(unittest.TestCase):
    """Cross-checks: enumerate minute by minute and compare with next_fire."""

    def _brute_force(self, expr, start, end):
        """Every matching minute in (start, end], found by scanning."""
        out = []
        current = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
        while current <= end:
            if next_fire(expr, current - timedelta(minutes=1)) == current:
                out.append(current)
            current += timedelta(minutes=1)
        return out

    def _walk(self, expr, start, end):
        """Every matching minute in (start, end], found by chaining next_fire."""
        out = []
        current = start
        while True:
            current = next_fire(expr, current)
            if current > end:
                return out
            out.append(current)

    def test_walk_matches_brute_force(self):
        start = datetime(2026, 3, 30, 22, 0)
        end = datetime(2026, 4, 2, 2, 0)
        for expr in ("*/17 * * * *", "50-10/5 */6 * * *", "0 0 * * fri-mon",
                     "0,30 1-4/2 * * *", "0 0 L * *", "*/13 22-2 30-2 * *"):
            with self.subTest(expr=expr):
                self.assertEqual(
                    self._walk(expr, start, end), self._brute_force(expr, start, end)
                )

    def test_results_are_strictly_increasing(self):
        got = fires("*/7 */3 * * *", datetime(2026, 1, 1, 0, 0), 50)
        for earlier, later in zip(got, got[1:]):
            self.assertLess(earlier, later)

    def test_idempotent_for_same_input(self):
        expr = "0 0 13 * fri"
        after = datetime(2026, 1, 1, 0, 0)
        self.assertEqual(next_fire(expr, after), next_fire(expr, after))


if __name__ == "__main__":
    unittest.main()
