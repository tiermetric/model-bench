"""Standard-library unittest suite for cron.next_fire."""

import unittest
from datetime import datetime

from cron import next_fire


ANY = datetime(2026, 1, 1, 0, 0)


class BasicMatchingTest(unittest.TestCase):
    def test_every_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_result_is_strictly_after(self):
        # An exact match on `after` itself is not returned.
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 2, 0, 0),
        )

    def test_seconds_and_microseconds_advance_to_next_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 30, 500000)),
            datetime(2026, 1, 1, 0, 1),
        )
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 1, 1, 23, 59, 59, 999999)),
            datetime(2026, 1, 2, 0, 0),
        )

    def test_result_has_zero_seconds_and_microseconds(self):
        result = next_fire("*/7 * * * *", datetime(2026, 3, 4, 5, 6, 7, 8))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)

    def test_single_values(self):
        self.assertEqual(
            next_fire("30 4 * * *", datetime(2026, 5, 1, 12, 0)),
            datetime(2026, 5, 2, 4, 30),
        )

    def test_comma_list(self):
        self.assertEqual(
            next_fire("1,3,5 * * * *", datetime(2026, 1, 1, 0, 1)),
            datetime(2026, 1, 1, 0, 3),
        )
        self.assertEqual(
            next_fire("1,3,5 * * * *", datetime(2026, 1, 1, 0, 5)),
            datetime(2026, 1, 1, 1, 1),
        )

    def test_mixed_names_and_numbers_in_list(self):
        # month list mixing a name and a number
        self.assertEqual(
            next_fire("0 0 1 jan,7 *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 7, 1, 0, 0),
        )
        # dow list mixing a number and a name: Sunday(0) or Friday
        self.assertEqual(
            next_fire("0 0 * * 0,fri", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )

    def test_dow_numbering_sunday_is_zero(self):
        # 2026-07-26 is a Sunday.
        self.assertEqual(
            next_fire("0 0 * * 0", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 26, 0, 0),
        )
        # 6 == Saturday
        self.assertEqual(
            next_fire("0 0 * * 6", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 25, 0, 0),
        )
        # names agree with the numbers
        self.assertEqual(
            next_fire("0 0 * * sun", datetime(2026, 7, 22, 0, 0)),
            next_fire("0 0 * * 0", datetime(2026, 7, 22, 0, 0)),
        )
        self.assertEqual(
            next_fire("0 0 * * sat", datetime(2026, 7, 22, 0, 0)),
            next_fire("0 0 * * 6", datetime(2026, 7, 22, 0, 0)),
        )

    def test_month_names(self):
        for index, name in enumerate(
            ["jan", "feb", "mar", "apr", "may", "jun",
             "jul", "aug", "sep", "oct", "nov", "dec"], start=1
        ):
            with self.subTest(month=name):
                self.assertEqual(
                    next_fire(f"0 0 1 {name} *", datetime(2025, 12, 31, 23, 59)),
                    next_fire(f"0 0 1 {index} *", datetime(2025, 12, 31, 23, 59)),
                )

    def test_whitespace_tolerance(self):
        expected = datetime(2026, 1, 1, 0, 15)
        self.assertEqual(
            next_fire("  */15 * * * *  ", datetime(2026, 1, 1, 0, 0)), expected
        )
        self.assertEqual(
            next_fire("*/15\t*   *  *\t*", datetime(2026, 1, 1, 0, 0)), expected
        )

    def test_plain_range(self):
        self.assertEqual(
            next_fire("0 9-17 * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 9, 0),
        )
        self.assertEqual(
            next_fire("0 9-17 * * *", datetime(2026, 1, 1, 17, 0)),
            datetime(2026, 1, 2, 9, 0),
        )


class Deviation1IntersectionTest(unittest.TestCase):
    def test_friday_the_thirteenth(self):
        self.assertEqual(
            next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 2, 13, 0, 0),
        )

    def test_first_of_month_and_monday(self):
        self.assertEqual(
            next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0)),
            datetime(2027, 2, 1, 0, 0),
        )

    def test_intersection_not_union(self):
        # Under standard cron's union rule this would fire on 2026-01-02
        # (the 2nd) or on the first Friday.  Intersection pushes it out.
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.day, 13)
        self.assertEqual(result.weekday(), 4)  # Python: Friday == 4

    def test_only_dom_restricted(self):
        self.assertEqual(
            next_fire("0 0 15 * *", datetime(2026, 7, 1, 0, 0)),
            datetime(2026, 7, 15, 0, 0),
        )

    def test_only_dow_restricted(self):
        self.assertEqual(
            next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 27, 9, 30),
        )

    def test_both_unrestricted_matches_every_day(self):
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 7, 22, 12, 0)),
            datetime(2026, 7, 23, 0, 0),
        )

    def test_step_dom_counts_as_restricted(self):
        # */2 on day-of-month is restricted, so it intersects with the dow.
        # Odd days only (1, 3, 5, …) that are also a Saturday.
        result = next_fire("0 0 */2 7 sat", datetime(2026, 7, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 11, 0, 0))
        self.assertEqual(result.weekday(), 5)  # Saturday
        self.assertEqual(result.day % 2, 1)

    def test_range_dom_counts_as_restricted(self):
        # 1-7 ∧ sun -> the first Sunday of a month.
        self.assertEqual(
            next_fire("0 0 1-7 * sun", datetime(2026, 7, 8, 0, 0)),
            datetime(2026, 8, 2, 0, 0),
        )

    def test_range_dow_counts_as_restricted(self):
        # 2026-07-25 is a Saturday, 2026-07-27 a Monday.
        self.assertEqual(
            next_fire("0 0 25-31 * mon-fri", datetime(2026, 7, 24, 0, 0)),
            datetime(2026, 7, 27, 0, 0),
        )


class Deviation2StepAnchorTest(unittest.TestCase):
    def test_range_step_anchored_at_range_start(self):
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2026, 6, 1, 12, 5),
        )
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5)),
            datetime(2026, 6, 1, 12, 15),
        )
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 15)),
            datetime(2026, 6, 1, 12, 25),
        )
        # 35 is not in the set (5, 15, 25) -> next is next year's June 1.
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 25)),
            datetime(2027, 6, 1, 12, 5),
        )

    def test_star_step_anchored_at_domain_floor(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 45)),
            datetime(2026, 1, 1, 1, 0),
        )

    def test_minute_10_50_step_15(self):
        # {10, 25, 40}
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

    def test_step_larger_than_range_yields_only_start(self):
        self.assertEqual(
            next_fire("7-9/50 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 7),
        )
        self.assertEqual(
            next_fire("7-9/50 * * * *", datetime(2026, 1, 1, 0, 7)),
            datetime(2026, 1, 1, 1, 7),
        )

    def test_hour_range_step(self):
        # 1-23/6 -> {1, 7, 13, 19}
        self.assertEqual(
            next_fire("0 1-23/6 * * *", datetime(2026, 1, 1, 1, 0)),
            datetime(2026, 1, 1, 7, 0),
        )
        self.assertEqual(
            next_fire("0 1-23/6 * * *", datetime(2026, 1, 1, 19, 0)),
            datetime(2026, 1, 2, 1, 0),
        )

    def test_star_step_domain_floor_for_month_is_one(self):
        # */5 on month -> {1, 6, 11}
        self.assertEqual(
            next_fire("0 0 1 */5 *", datetime(2026, 1, 2, 0, 0)),
            datetime(2026, 6, 1, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 1 */5 *", datetime(2026, 6, 2, 0, 0)),
            datetime(2026, 11, 1, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 1 */5 *", datetime(2026, 11, 2, 0, 0)),
            datetime(2027, 1, 1, 0, 0),
        )

    def test_star_step_domain_floor_for_dom_is_one(self):
        # */10 on day-of-month -> {1, 11, 21, 31}
        self.assertEqual(
            next_fire("0 0 */10 1 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 11, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 */10 1 *", datetime(2026, 1, 21, 0, 0)),
            datetime(2026, 1, 31, 0, 0),
        )

    def test_step_in_a_list(self):
        # 0-10/2,30 -> {0, 2, 4, 6, 8, 10, 30}
        self.assertEqual(
            next_fire("0-10/2,30 * * * *", datetime(2026, 1, 1, 0, 10)),
            datetime(2026, 1, 1, 0, 30),
        )
        self.assertEqual(
            next_fire("0-10/2,30 * * * *", datetime(2026, 1, 1, 0, 30)),
            datetime(2026, 1, 1, 1, 0),
        )


class Rule4WrapRangeTest(unittest.TestCase):
    def test_minute_wrap(self):
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30)),
            datetime(2026, 1, 1, 0, 50),
        )
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 55)),
            datetime(2026, 1, 1, 0, 56),
        )
        # crossing the hour boundary
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 59)),
            datetime(2026, 1, 1, 1, 0),
        )
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 1, 10)),
            datetime(2026, 1, 1, 1, 50),
        )

    def test_month_wrap_nov_feb(self):
        self.assertEqual(
            next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 11, 1, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 1 nov-feb *", datetime(2026, 12, 1, 0, 0)),
            datetime(2027, 1, 1, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 1 nov-feb *", datetime(2027, 2, 1, 0, 0)),
            datetime(2027, 11, 1, 0, 0),
        )

    def test_dow_wrap_fri_mon(self):
        # 2026-07-21 is a Tuesday; the wrapped set is {fri, sat, sun, mon}.
        self.assertEqual(
            next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )
        for start, expected in [
            (datetime(2026, 7, 24, 0, 0), datetime(2026, 7, 25, 0, 0)),
            (datetime(2026, 7, 25, 0, 0), datetime(2026, 7, 26, 0, 0)),
            (datetime(2026, 7, 26, 0, 0), datetime(2026, 7, 27, 0, 0)),
            (datetime(2026, 7, 27, 0, 0), datetime(2026, 7, 31, 0, 0)),
        ]:
            with self.subTest(start=start):
                self.assertEqual(next_fire("0 0 * * fri-mon", start), expected)

    def test_hour_wrap(self):
        # 22-2 -> {22, 23, 0, 1, 2}
        self.assertEqual(
            next_fire("0 22-2 * * *", datetime(2026, 1, 1, 3, 0)),
            datetime(2026, 1, 1, 22, 0),
        )
        self.assertEqual(
            next_fire("0 22-2 * * *", datetime(2026, 1, 1, 23, 0)),
            datetime(2026, 1, 2, 0, 0),
        )
        self.assertEqual(
            next_fire("0 22-2 * * *", datetime(2026, 1, 2, 2, 0)),
            datetime(2026, 1, 2, 22, 0),
        )

    def test_dom_wrap(self):
        # 30-2 -> {30, 31, 1, 2}
        self.assertEqual(
            next_fire("0 0 30-2 1 *", datetime(2026, 1, 2, 0, 0)),
            datetime(2026, 1, 30, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 30-2 1 *", datetime(2026, 1, 30, 0, 0)),
            datetime(2026, 1, 31, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 30-2 1 *", datetime(2026, 1, 31, 0, 0)),
            datetime(2027, 1, 1, 0, 0),
        )

    def test_wrap_range_is_not_an_error(self):
        for expr in ["30-5 * * * *", "0 0 1 nov-feb *", "0 0 * * fri-mon"]:
            with self.subTest(expr=expr):
                self.assertIsInstance(next_fire(expr, ANY), datetime)

    def test_degenerate_range_single_value(self):
        self.assertEqual(
            next_fire("5-5 * * * *", datetime(2026, 1, 1, 0, 5)),
            datetime(2026, 1, 1, 1, 5),
        )


class Rule5WrapStepTest(unittest.TestCase):
    def test_minute_wrap_step(self):
        # 50-10/5 -> {50, 55, 0, 5, 10}
        self.assertEqual(
            next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 5),
        )
        self.assertEqual(
            next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 5)),
            datetime(2026, 1, 1, 0, 10),
        )
        self.assertEqual(
            next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 10)),
            datetime(2026, 1, 1, 0, 50),
        )
        self.assertEqual(
            next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 50)),
            datetime(2026, 1, 1, 0, 55),
        )
        # 55 is the last of the day for this hour-0 schedule.
        self.assertEqual(
            next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 55)),
            datetime(2027, 1, 1, 0, 0),
        )
        # 51-54, 56-59 are NOT in the set.
        self.assertEqual(
            next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 51)),
            datetime(2026, 1, 1, 0, 55),
        )

    def test_dow_wrap_step(self):
        # fri-tue/2 -> wrapped order fri,sat,sun,mon,tue -> {fri, sun, tue}
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )
        # Fri 7/24 -> Sun 7/26 -> Tue 7/28 -> Fri 7/31
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 24, 0, 0)),
            datetime(2026, 7, 26, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 26, 0, 0)),
            datetime(2026, 7, 28, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 28, 0, 0)),
            datetime(2026, 7, 31, 0, 0),
        )

    def test_month_wrap_step(self):
        # nov-mar/2 -> wrapped order nov,dec,jan,feb,mar -> {11, 1, 3}
        self.assertEqual(
            next_fire("0 0 1 nov-mar/2 *", datetime(2026, 4, 1, 0, 0)),
            datetime(2026, 11, 1, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 1 nov-mar/2 *", datetime(2026, 11, 1, 0, 0)),
            datetime(2027, 1, 1, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 1 nov-mar/2 *", datetime(2027, 1, 1, 0, 0)),
            datetime(2027, 3, 1, 0, 0),
        )

    def test_hour_wrap_step(self):
        # 23-4/2 -> wrapped order 23,0,1,2,3,4 -> {23, 1, 3}
        self.assertEqual(
            next_fire("0 23-4/2 * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 1, 0),
        )
        self.assertEqual(
            next_fire("0 23-4/2 * * *", datetime(2026, 1, 1, 1, 0)),
            datetime(2026, 1, 1, 3, 0),
        )
        self.assertEqual(
            next_fire("0 23-4/2 * * *", datetime(2026, 1, 1, 3, 0)),
            datetime(2026, 1, 1, 23, 0),
        )


class Rule6LastDayTest(unittest.TestCase):
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

    def test_last_day_that_is_a_friday(self):
        self.assertEqual(
            next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 7, 31, 0, 0),
        )

    def test_last_day_every_month_sequence(self):
        expected = [
            datetime(2026, 1, 31, 0, 0),
            datetime(2026, 2, 28, 0, 0),
            datetime(2026, 3, 31, 0, 0),
            datetime(2026, 4, 30, 0, 0),
            datetime(2026, 5, 31, 0, 0),
            datetime(2026, 6, 30, 0, 0),
        ]
        current = datetime(2026, 1, 1, 0, 0)
        for want in expected:
            current = next_fire("0 0 L * *", current)
            self.assertEqual(current, want)

    def test_last_day_of_leap_february_2028_is_29(self):
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2028, 2, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )

    def test_century_leap_rules(self):
        # 2100 is not a leap year; 2000 was.
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2100, 1, 1, 0, 0)),
            datetime(2100, 2, 28, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2000, 1, 1, 0, 0)),
            datetime(2000, 2, 29, 0, 0),
        )


class Rule7MonthLengthTest(unittest.TestCase):
    def test_day_31_skips_short_months(self):
        self.assertEqual(
            next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0)),
            datetime(2026, 5, 31, 0, 0),
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
        self.assertEqual(
            next_fire("0 0 29 feb *", datetime(2028, 3, 1, 0, 0)),
            datetime(2032, 2, 29, 0, 0),
        )

    def test_no_clamping_onto_last_day(self):
        # A 31st schedule must never land on the 30th of a 30-day month.
        current = datetime(2026, 1, 1, 0, 0)
        for _ in range(12):
            current = next_fire("0 0 31 * *", current)
            self.assertEqual(current.day, 31)

    def test_search_crosses_year_boundary(self):
        self.assertEqual(
            next_fire("0 12 1 jan,jul *", datetime(2026, 8, 1, 0, 0)),
            datetime(2027, 1, 1, 12, 0),
        )


class WorkedExamplesTest(unittest.TestCase):
    CASES = [
        # original deviations
        ("*/15 * * * *", datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 15)),
        ("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0), datetime(2026, 6, 1, 12, 5)),
        ("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5), datetime(2026, 6, 1, 12, 15)),
        ("0 0 13 * fri", datetime(2026, 1, 1, 0, 0), datetime(2026, 2, 13, 0, 0)),
        ("0 0 1 * mon", datetime(2026, 6, 2, 0, 0), datetime(2027, 2, 1, 0, 0)),
        ("30 9 * * mon", datetime(2026, 7, 22, 0, 0), datetime(2026, 7, 27, 9, 30)),
        ("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0), datetime(2026, 7, 1, 12, 0)),
        # Rule 4 — wrap-around ranges
        ("50-10 * * * *", datetime(2026, 1, 1, 0, 30), datetime(2026, 1, 1, 0, 50)),
        ("50-10 * * * *", datetime(2026, 1, 1, 0, 55), datetime(2026, 1, 1, 0, 56)),
        ("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0), datetime(2026, 11, 1, 0, 0)),
        ("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0), datetime(2026, 7, 24, 0, 0)),
        # Rule 5 — steps through the wrap
        ("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 5)),
        ("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0), datetime(2026, 7, 24, 0, 0)),
        # Rule 6 — L
        ("0 0 L * *", datetime(2026, 1, 15, 0, 0), datetime(2026, 1, 31, 0, 0)),
        ("0 0 L * *", datetime(2026, 2, 1, 0, 0), datetime(2026, 2, 28, 0, 0)),
        ("0 0 L 2 *", datetime(2027, 3, 1, 0, 0), datetime(2028, 2, 29, 0, 0)),
        ("0 0 L * fri", datetime(2026, 1, 1, 0, 0), datetime(2026, 7, 31, 0, 0)),
        # Rule 7 — month-length / leap rollover
        ("0 0 31 * *", datetime(2026, 4, 1, 0, 0), datetime(2026, 5, 31, 0, 0)),
        ("0 0 30 * *", datetime(2026, 2, 1, 0, 0), datetime(2026, 3, 30, 0, 0)),
        ("0 0 29 feb *", datetime(2027, 1, 1, 0, 0), datetime(2028, 2, 29, 0, 0)),
    ]

    def test_all_worked_examples(self):
        for expr, after, expected in self.CASES:
            with self.subTest(expr=expr, after=after):
                self.assertEqual(next_fire(expr, after), expected)


class ErrorCaseTest(unittest.TestCase):
    SPEC_ERRORS = [
        ("", "empty expression"),
        ("* * * *", "4 fields"),
        ("* * * * * *", "6 fields"),
        ("60 * * * *", "minute out of domain"),
        ("* 24 * * *", "hour out of domain"),
        ("* * 0 * *", "day-of-month out of domain"),
        ("* * * 13 *", "month out of domain"),
        ("* * * * 7", "dow 7 is invalid"),
        ("* * * * SUN", "uppercase name"),
        ("*/0 * * * *", "step <= 0"),
        ("5/10 * * * *", "step on a single value"),
        ("1,,3 * * * *", "empty list element"),
        ("1x * * * *", "unknown token"),
        ("70-10 * * * *", "wrap endpoint out of domain"),
        ("0 0 L,15 * *", "L may not be combined"),
        ("0 0 * * L", "L only in day-of-month"),
        ("0 0 l * *", "lowercase l is not L"),
    ]

    def test_spec_error_cases(self):
        for expr, why in self.SPEC_ERRORS:
            with self.subTest(expr=expr, why=why):
                with self.assertRaises(ValueError):
                    next_fire(expr, ANY)

    MORE_ERRORS = [
        "   ",                  # whitespace only
        "* * *",                # 3 fields
        "-1 * * * *",           # empty range start
        "* * * * -1",           # empty range start in dow
        "5- * * * *",           # empty range end
        "1-2-3 * * * *",        # malformed range
        "*/1/2 * * * *",        # double step
        "*/ * * * *",           # missing step value
        "1-5/ * * * *",         # missing step value on a range
        "*/-2 * * * *",         # negative step
        "1-5/0 * * * *",        # zero step on a range
        "*/2.5 * * * *",        # non-integer step
        "1.5 * * * *",          # non-integer value
        "* * * * mon-7",        # dow 7 inside a range
        "* * * * 7-1",          # dow 7 as a wrap start
        "* * * jan-13 *",       # month 13 inside a range
        "* * 32 * *",           # dom 32
        "* * * 0 *",            # month 0
        "* * * * sunday",       # unknown name
        "* * * JAN *",          # uppercase month name
        "* * * * Mon",          # mixed-case name
        "60-70 * * * *",        # both endpoints out of domain
        "0 0 L-3 * *",          # L in a range
        "0 0 L/2 * *",          # L stepped
        "0 0 15,L * *",         # L in a list (trailing)
        "0 0 * L *",            # L in the month field
        "* * * * *,",           # trailing empty list element
        ",1 * * * *",           # leading empty list element
        "* mon * * *",          # names not allowed in the hour field
        "jan * * * *",          # names not allowed in the minute field
        "* * fri * *",          # names not allowed in the day-of-month field
        "* * * * ",             # 4 fields after stripping
    ]

    def test_additional_error_cases(self):
        for expr in self.MORE_ERRORS:
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, ANY)

    def test_dow_seven_rejected_but_six_and_zero_accepted(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", ANY)
        self.assertIsInstance(next_fire("* * * * 0", ANY), datetime)
        self.assertIsInstance(next_fire("* * * * 6", ANY), datetime)

    def test_domain_boundaries_accepted(self):
        for expr in [
            "0 0 1 1 0",
            "59 23 31 12 6",
            "0-59 0-23 1-31 1-12 0-6",
        ]:
            with self.subTest(expr=expr):
                self.assertIsInstance(next_fire(expr, ANY), datetime)

    def test_wrap_ranges_are_valid_not_errors(self):
        for expr in [
            "30-5 * * * *",
            "0 0 1 nov-feb *",
            "0 0 * * fri-mon",
            "0 0 * * sat-sun",
        ]:
            with self.subTest(expr=expr):
                self.assertIsInstance(next_fire(expr, ANY), datetime)

    def test_error_type_is_valueerror_not_subclass_of_something_else(self):
        with self.assertRaises(ValueError) as ctx:
            next_fire("* * * * 7", ANY)
        self.assertIsInstance(ctx.exception, ValueError)


class ConsistencyTest(unittest.TestCase):
    """Cross-checks that walk the schedule forward step by step."""

    def test_every_minute_walk(self):
        current = datetime(2026, 2, 28, 23, 55)
        for _ in range(10):
            nxt = next_fire("* * * * *", current)
            self.assertEqual((nxt - current).total_seconds(), 60)
            current = nxt
        self.assertEqual(current, datetime(2026, 3, 1, 0, 5))

    def test_results_are_always_strictly_after(self):
        exprs = [
            "*/15 * * * *", "50-10/5 * * * *", "0 0 L * *",
            "0 0 13 * fri", "0 0 * * fri-tue/2", "0 0 31 * *",
        ]
        start = datetime(2026, 7, 22, 13, 41, 33, 7)
        for expr in exprs:
            with self.subTest(expr=expr):
                current = start
                for _ in range(5):
                    nxt = next_fire(expr, current)
                    self.assertGreater(nxt, current)
                    self.assertEqual(nxt.second, 0)
                    self.assertEqual(nxt.microsecond, 0)
                    current = nxt

    def test_wrapped_range_set_membership(self):
        # Walk a full hour of "50-10" and confirm the exact minute set.
        seen = set()
        current = datetime(2026, 1, 1, 0, 0)
        end = datetime(2026, 1, 1, 2, 0)
        while True:
            current = next_fire("50-10 * * * *", current)
            if current >= end:
                break
            seen.add(current.minute)
        self.assertEqual(seen, set(range(50, 60)) | set(range(0, 11)))

    def test_wrapped_step_set_membership(self):
        seen = set()
        current = datetime(2026, 1, 1, 0, 0)
        end = datetime(2026, 1, 1, 3, 0)
        while True:
            current = next_fire("50-10/5 * * * *", current)
            if current >= end:
                break
            seen.add(current.minute)
        self.assertEqual(seen, {50, 55, 0, 5, 10})

    def test_dow_step_set_membership(self):
        seen = set()
        current = datetime(2026, 7, 1, 0, 0)
        end = datetime(2026, 9, 1, 0, 0)
        while True:
            current = next_fire("0 0 * * fri-tue/2", current)
            if current >= end:
                break
            seen.add((current.weekday() + 1) % 7)  # cron numbering
        self.assertEqual(seen, {5, 0, 2})  # fri, sun, tue


if __name__ == "__main__":
    unittest.main()
