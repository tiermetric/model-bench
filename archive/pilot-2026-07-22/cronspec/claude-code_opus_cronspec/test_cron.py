"""Standard-library unittest suite for cron.next_fire."""

import calendar
import unittest
from datetime import datetime, timedelta

from cron import next_fire


ANY_TIME = datetime(2026, 1, 1, 0, 0)


class WorkedExamplesTest(unittest.TestCase):
    """Every example listed in the specification, verbatim."""

    def test_original_deviations(self):
        cases = [
            ("*/15 * * * *", datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 15)),
            ("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0), datetime(2026, 6, 1, 12, 5)),
            ("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5), datetime(2026, 6, 1, 12, 15)),
            ("0 0 13 * fri", datetime(2026, 1, 1, 0, 0), datetime(2026, 2, 13, 0, 0)),
            ("0 0 1 * mon", datetime(2026, 6, 2, 0, 0), datetime(2027, 2, 1, 0, 0)),
            ("30 9 * * mon", datetime(2026, 7, 22, 0, 0), datetime(2026, 7, 27, 9, 30)),
            ("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0), datetime(2026, 7, 1, 12, 0)),
        ]
        for expr, after, expected in cases:
            with self.subTest(expr=expr, after=after):
                self.assertEqual(next_fire(expr, after), expected)

    def test_rule4_wrap_around_ranges(self):
        cases = [
            ("50-10 * * * *", datetime(2026, 1, 1, 0, 30), datetime(2026, 1, 1, 0, 50)),
            ("50-10 * * * *", datetime(2026, 1, 1, 0, 55), datetime(2026, 1, 1, 0, 56)),
            ("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0), datetime(2026, 11, 1, 0, 0)),
            ("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0), datetime(2026, 7, 24, 0, 0)),
        ]
        for expr, after, expected in cases:
            with self.subTest(expr=expr, after=after):
                self.assertEqual(next_fire(expr, after), expected)

    def test_rule5_steps_through_the_wrap(self):
        cases = [
            ("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 5)),
            ("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0), datetime(2026, 7, 24, 0, 0)),
        ]
        for expr, after, expected in cases:
            with self.subTest(expr=expr, after=after):
                self.assertEqual(next_fire(expr, after), expected)

    def test_rule6_last_day_of_month(self):
        cases = [
            ("0 0 L * *", datetime(2026, 1, 15, 0, 0), datetime(2026, 1, 31, 0, 0)),
            ("0 0 L * *", datetime(2026, 2, 1, 0, 0), datetime(2026, 2, 28, 0, 0)),
            ("0 0 L 2 *", datetime(2027, 3, 1, 0, 0), datetime(2028, 2, 29, 0, 0)),
            ("0 0 L * fri", datetime(2026, 1, 1, 0, 0), datetime(2026, 7, 31, 0, 0)),
        ]
        for expr, after, expected in cases:
            with self.subTest(expr=expr, after=after):
                self.assertEqual(next_fire(expr, after), expected)

    def test_rule7_month_length_and_leap_rollover(self):
        cases = [
            ("0 0 31 * *", datetime(2026, 4, 1, 0, 0), datetime(2026, 5, 31, 0, 0)),
            ("0 0 30 * *", datetime(2026, 2, 1, 0, 0), datetime(2026, 3, 30, 0, 0)),
            ("0 0 29 feb *", datetime(2027, 1, 1, 0, 0), datetime(2028, 2, 29, 0, 0)),
        ]
        for expr, after, expected in cases:
            with self.subTest(expr=expr, after=after):
                self.assertEqual(next_fire(expr, after), expected)


class StrictlyAfterTest(unittest.TestCase):
    """The result is always strictly after `after`, on a minute boundary."""

    def test_exact_match_is_not_returned(self):
        # after is itself a matching instant: the next one must be returned.
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_seconds_round_up_to_next_whole_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 30)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_microseconds_round_up_to_next_whole_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 0, 1)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_sub_minute_time_does_not_skip_the_current_minute_slot(self):
        # 00:14:59.999999 -> next whole minute is 00:15, which matches.
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 14, 59, 999999)),
            datetime(2026, 1, 1, 0, 15),
        )

    def test_result_has_zero_seconds_and_microseconds(self):
        result = next_fire("*/7 */3 * * *", datetime(2026, 5, 4, 3, 2, 1, 999))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)

    def test_rollover_across_hour_day_month_and_year(self):
        self.assertEqual(
            next_fire("0 * * * *", datetime(2026, 1, 1, 10, 30)),
            datetime(2026, 1, 1, 11, 0),
        )
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 1, 1, 10, 30)),
            datetime(2026, 1, 2, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 1 * *", datetime(2026, 1, 5, 0, 0)),
            datetime(2026, 2, 1, 0, 0),
        )
        self.assertEqual(
            next_fire("59 23 31 12 *", datetime(2026, 12, 31, 23, 58)),
            datetime(2026, 12, 31, 23, 59),
        )
        self.assertEqual(
            next_fire("59 23 31 12 *", datetime(2026, 12, 31, 23, 59)),
            datetime(2027, 12, 31, 23, 59),
        )


class Deviation1IntersectionTest(unittest.TestCase):
    """day-of-month AND day-of-week intersect when both are restricted."""

    def test_friday_the_thirteenth_sequence(self):
        expr = "0 0 13 * fri"
        first = next_fire(expr, datetime(2026, 1, 1, 0, 0))
        self.assertEqual(first, datetime(2026, 2, 13, 0, 0))
        second = next_fire(expr, first)
        self.assertEqual(second, datetime(2026, 3, 13, 0, 0))
        third = next_fire(expr, second)
        self.assertEqual(third, datetime(2026, 11, 13, 0, 0))
        for fire in (first, second, third):
            self.assertEqual(fire.day, 13)
            self.assertEqual(fire.weekday(), 4)  # Python: Friday == 4

    def test_first_of_month_that_is_a_monday(self):
        fire = next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0))
        self.assertEqual(fire, datetime(2027, 2, 1, 0, 0))
        self.assertEqual(fire.day, 1)
        self.assertEqual(fire.weekday(), 0)  # Monday

    def test_union_semantics_would_have_matched_sooner(self):
        # Standard cron takes the union here and would fire on 2026-01-02
        # (a Friday) or 2026-01-13 (the 13th). We must do neither.
        fire = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertGreater(fire, datetime(2026, 1, 31, 23, 59))

    def test_only_day_of_month_restricted(self):
        # dow is bare '*': day-of-month alone decides.
        self.assertEqual(
            next_fire("0 0 13 * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 13, 0, 0),
        )

    def test_only_day_of_week_restricted(self):
        # dom is bare '*': day-of-week alone decides. 2026-01-01 is a Thursday.
        self.assertEqual(
            next_fire("0 0 * * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 2, 0, 0),
        )

    def test_both_unrestricted_matches_every_day(self):
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 1)),
            datetime(2026, 1, 2, 0, 0),
        )

    def test_step_day_of_month_counts_as_restricted(self):
        # '*/2' on dom is restricted, so it intersects with the dow field.
        # Odd days only (1,3,5,...,31) that are also Sundays.
        fire = next_fire("0 0 */2 * sun", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(fire.weekday(), 6)  # Sunday
        self.assertEqual(fire.day % 2, 1)
        self.assertEqual(fire, datetime(2026, 1, 11, 0, 0))

    def test_range_day_of_week_counts_as_restricted(self):
        # dom 1-5 intersected with dow mon-fri: the first weekday in 1..5.
        # 2026-08-01 is a Saturday, so 2026-08-03 (Mon) is the match.
        self.assertEqual(
            next_fire("0 0 1-5 8 mon-fri", datetime(2026, 7, 1, 0, 0)),
            datetime(2026, 8, 3, 0, 0),
        )

    def test_L_counts_as_restricted_day_of_month(self):
        # Rule 6 + Deviation 1: last day AND Friday.
        fire = next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(fire, datetime(2026, 7, 31, 0, 0))
        self.assertEqual(fire.weekday(), 4)


class Deviation2StepAnchorTest(unittest.TestCase):
    """Steps are anchored at the range start, not at zero."""

    def _minutes_in_hour(self, expr, day_start):
        """Collect every minute the expression fires within one hour."""
        fires = []
        cursor = day_start - timedelta(minutes=1)
        end = day_start + timedelta(hours=1)
        while True:
            cursor = next_fire(expr, cursor)
            if cursor >= end:
                break
            fires.append(cursor.minute)
        return fires

    def test_explicit_range_step_starts_at_range_start(self):
        self.assertEqual(
            self._minutes_in_hour("5-30/10 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
            [5, 15, 25],
        )

    def test_another_explicit_range_step(self):
        self.assertEqual(
            self._minutes_in_hour("10-50/15 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
            [10, 25, 40],
        )

    def test_star_step_anchors_at_domain_floor(self):
        self.assertEqual(
            self._minutes_in_hour("*/15 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
            [0, 15, 30, 45],
        )

    def test_hour_star_step_anchors_at_zero(self):
        hours = []
        cursor = datetime(2026, 1, 1, 0, 0) - timedelta(minutes=1)
        while True:
            cursor = next_fire("0 */5 1 1 *", cursor)
            if cursor >= datetime(2026, 1, 2, 0, 0):
                break
            hours.append(cursor.hour)
        self.assertEqual(hours, [0, 5, 10, 15, 20])

    def test_day_of_month_star_step_anchors_at_one(self):
        # Domain floor for day-of-month is 1, so */5 -> 1, 6, 11, 16, 21, 26, 31.
        days = []
        cursor = datetime(2026, 1, 1, 0, 0) - timedelta(minutes=1)
        while True:
            cursor = next_fire("0 0 */5 1 *", cursor)
            if cursor >= datetime(2026, 2, 1, 0, 0):
                break
            days.append(cursor.day)
        self.assertEqual(days, [1, 6, 11, 16, 21, 26, 31])

    def test_month_star_step_anchors_at_one(self):
        months = []
        cursor = datetime(2026, 1, 1, 0, 0) - timedelta(minutes=1)
        while True:
            cursor = next_fire("0 0 1 */4 *", cursor)
            if cursor >= datetime(2027, 1, 1, 0, 0):
                break
            months.append(cursor.month)
        self.assertEqual(months, [1, 5, 9])

    def test_day_of_week_star_step_anchors_at_zero(self):
        # dow domain floor is 0 (Sunday): */3 -> {0, 3, 6} = Sun, Wed, Sat.
        dows = set()
        cursor = datetime(2026, 3, 1, 0, 0) - timedelta(minutes=1)
        for _ in range(3):
            cursor = next_fire("0 0 * * */3", cursor)
            dows.add(cursor.isoweekday() % 7)
        self.assertEqual(dows, {0, 3, 6})

    def test_step_larger_than_range_yields_only_the_start(self):
        self.assertEqual(
            self._minutes_in_hour("5-30/100 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
            [5],
        )

    def test_step_of_one_is_the_whole_range(self):
        self.assertEqual(
            self._minutes_in_hour("3-7/1 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
            [3, 4, 5, 6, 7],
        )

    def test_named_range_step_in_month_field(self):
        # jan-dec/3 -> jan, apr, jul, oct (anchored at jan, not "multiples of 3").
        months = []
        cursor = datetime(2026, 1, 1, 0, 0) - timedelta(minutes=1)
        while True:
            cursor = next_fire("0 0 1 jan-dec/3 *", cursor)
            if cursor >= datetime(2027, 1, 1, 0, 0):
                break
            months.append(cursor.month)
        self.assertEqual(months, [1, 4, 7, 10])


class Deviation3StrictDomainTest(unittest.TestCase):
    """Domains are strict; nothing outside them is accepted."""

    def test_dow_seven_is_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", ANY_TIME)

    def test_dow_zero_and_six_are_valid(self):
        self.assertEqual(
            next_fire("0 0 * * 0", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 4, 0, 0),  # first Sunday of 2026
        )
        self.assertEqual(
            next_fire("0 0 * * 6", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 3, 0, 0),  # first Saturday of 2026
        )

    def test_domain_boundaries_are_inclusive(self):
        self.assertEqual(
            next_fire("59 23 31 12 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 12, 31, 23, 59),
        )
        self.assertEqual(
            next_fire("0 0 1 1 *", datetime(2026, 1, 1, 0, 1)),
            datetime(2027, 1, 1, 0, 0),
        )

    def test_out_of_domain_inside_a_list_is_rejected(self):
        with self.assertRaises(ValueError):
            next_fire("1,60 * * * *", ANY_TIME)

    def test_out_of_domain_inside_a_range_is_rejected(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 5-7", ANY_TIME)


class Rule4WrapRangeTest(unittest.TestCase):
    """A-B with A > B wraps through the domain maximum."""

    def test_minute_wrap_set(self):
        fires = []
        cursor = datetime(2026, 1, 1, 0, 0) - timedelta(minutes=1)
        while True:
            cursor = next_fire("50-10 0 1 1 *", cursor)
            if cursor >= datetime(2026, 1, 1, 1, 0):
                break
            fires.append(cursor.minute)
        self.assertEqual(fires, list(range(0, 11)) + list(range(50, 60)))

    def test_month_name_wrap(self):
        months = set()
        cursor = datetime(2026, 3, 1, 0, 0)
        for _ in range(4):
            cursor = next_fire("0 0 1 nov-feb *", cursor)
            months.add(cursor.month)
        self.assertEqual(months, {11, 12, 1, 2})

    def test_day_of_week_name_wrap(self):
        dows = set()
        cursor = datetime(2026, 7, 21, 0, 0)
        for _ in range(4):
            cursor = next_fire("0 0 * * fri-mon", cursor)
            dows.add(cursor.isoweekday() % 7)
        self.assertEqual(dows, {5, 6, 0, 1})

    def test_hour_wrap(self):
        hours = []
        cursor = datetime(2026, 1, 1, 0, 0) - timedelta(minutes=1)
        while True:
            cursor = next_fire("0 22-2 1 1 *", cursor)
            if cursor >= datetime(2026, 1, 2, 0, 0):
                break
            hours.append(cursor.hour)
        self.assertEqual(hours, [0, 1, 2, 22, 23])

    def test_day_of_month_wrap(self):
        days = []
        cursor = datetime(2026, 1, 1, 0, 0) - timedelta(minutes=1)
        while True:
            cursor = next_fire("0 0 29-2 1 *", cursor)
            if cursor >= datetime(2026, 2, 1, 0, 0):
                break
            days.append(cursor.day)
        self.assertEqual(days, [1, 2, 29, 30, 31])

    def test_wrap_is_not_an_error(self):
        for expr in ("30-5 * * * *", "0 0 1 nov-feb *", "0 0 * * fri-mon"):
            with self.subTest(expr=expr):
                self.assertIsInstance(next_fire(expr, ANY_TIME), datetime)

    def test_single_value_range_matches_one_value(self):
        fires = []
        cursor = datetime(2026, 1, 1, 0, 0) - timedelta(minutes=1)
        while True:
            cursor = next_fire("7-7 0 1 1 *", cursor)
            if cursor >= datetime(2026, 1, 1, 1, 0):
                break
            fires.append(cursor.minute)
        self.assertEqual(fires, [7])


class Rule5WrapStepTest(unittest.TestCase):
    """Steps over a wrapped range count through the wrap."""

    def test_minute_wrap_step(self):
        fires = []
        cursor = datetime(2026, 1, 1, 0, 0) - timedelta(minutes=1)
        while True:
            cursor = next_fire("50-10/5 0 1 1 *", cursor)
            if cursor >= datetime(2026, 1, 1, 1, 0):
                break
            fires.append(cursor.minute)
        self.assertEqual(fires, [0, 5, 10, 50, 55])

    def test_day_of_week_wrap_step(self):
        dows = set()
        cursor = datetime(2026, 7, 21, 0, 0)
        for _ in range(3):
            cursor = next_fire("0 0 * * fri-tue/2", cursor)
            dows.add(cursor.isoweekday() % 7)
        self.assertEqual(dows, {5, 0, 2})

    def test_fri_tue_step_two_skips_saturday_and_monday(self):
        # From Tuesday 2026-07-21 the next match is Friday 2026-07-24
        # (Sat/Mon are excluded by the stride).
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 24, 0, 0)),
            datetime(2026, 7, 26, 0, 0),  # Sunday
        )
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 26, 0, 0)),
            datetime(2026, 7, 28, 0, 0),  # Tuesday
        )

    def test_month_wrap_step(self):
        # nov-feb -> [11, 12, 1, 2]; /2 -> {11, 1}.
        months = set()
        cursor = datetime(2026, 3, 1, 0, 0)
        for _ in range(4):
            cursor = next_fire("0 0 1 nov-feb/2 *", cursor)
            months.add(cursor.month)
        self.assertEqual(months, {11, 1})

    def test_hour_wrap_step(self):
        # 22-2 -> [22, 23, 0, 1, 2]; /2 -> {22, 0, 2}.
        hours = []
        cursor = datetime(2026, 1, 1, 0, 0) - timedelta(minutes=1)
        while True:
            cursor = next_fire("0 22-2/2 1 1 *", cursor)
            if cursor >= datetime(2026, 1, 2, 0, 0):
                break
            hours.append(cursor.hour)
        self.assertEqual(hours, [0, 2, 22])


class Rule6LastDayTest(unittest.TestCase):
    """L is the last calendar day of the month under consideration."""

    def test_last_day_of_each_month_in_a_year(self):
        cursor = datetime(2026, 1, 1, 0, 0) - timedelta(minutes=1)
        seen = []
        while True:
            cursor = next_fire("0 0 L * *", cursor)
            if cursor >= datetime(2027, 1, 1, 0, 0):
                break
            seen.append((cursor.month, cursor.day))
        expected = [(m, calendar.monthrange(2026, m)[1]) for m in range(1, 13)]
        self.assertEqual(seen, expected)

    def test_non_leap_february(self):
        self.assertEqual(
            next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0)),
            datetime(2026, 2, 28, 0, 0),
        )

    def test_leap_february(self):
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )

    def test_century_non_leap_year(self):
        # 2100 is not a leap year despite being divisible by 4.
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2100, 2, 1, 0, 0)),
            datetime(2100, 2, 28, 0, 0),
        )

    def test_year_2000_is_a_leap_year(self):
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2000, 2, 1, 0, 0)),
            datetime(2000, 2, 29, 0, 0),
        )

    def test_L_intersected_with_day_of_week(self):
        fire = next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(fire, datetime(2026, 7, 31, 0, 0))
        nxt = next_fire("0 0 L * fri", fire)
        self.assertEqual(nxt.weekday(), 4)
        self.assertEqual(
            nxt.day, calendar.monthrange(nxt.year, nxt.month)[1]
        )

    def test_L_with_time_fields(self):
        self.assertEqual(
            next_fire("30 23 L * *", datetime(2026, 1, 31, 0, 0)),
            datetime(2026, 1, 31, 23, 30),
        )

    def test_L_only_in_day_of_month_field(self):
        for expr in ("L * * * *", "* L * * *", "0 0 * L *", "0 0 * * L"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, ANY_TIME)

    def test_L_may_not_be_combined(self):
        for expr in ("0 0 L,15 * *", "0 0 15,L * *", "0 0 L-5 * *", "0 0 L/2 * *"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, ANY_TIME)

    def test_lowercase_l_is_rejected(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 l * *", ANY_TIME)


class Rule7MonthLengthTest(unittest.TestCase):
    """Impossible days are skipped, never clamped."""

    def test_thirty_first_skips_short_months(self):
        cursor = datetime(2026, 1, 1, 0, 0) - timedelta(minutes=1)
        months = []
        while True:
            cursor = next_fire("0 0 31 * *", cursor)
            if cursor >= datetime(2027, 1, 1, 0, 0):
                break
            self.assertEqual(cursor.day, 31)
            months.append(cursor.month)
        self.assertEqual(months, [1, 3, 5, 7, 8, 10, 12])

    def test_thirtieth_skips_february(self):
        self.assertEqual(
            next_fire("0 0 30 * *", datetime(2026, 2, 1, 0, 0)),
            datetime(2026, 3, 30, 0, 0),
        )

    def test_february_29_only_in_leap_years(self):
        self.assertEqual(
            next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 29 feb *", datetime(2028, 2, 29, 0, 0)),
            datetime(2032, 2, 29, 0, 0),
        )

    def test_february_29_skips_century_non_leap_year(self):
        self.assertEqual(
            next_fire("0 0 29 feb *", datetime(2096, 3, 1, 0, 0)),
            datetime(2104, 2, 29, 0, 0),
        )

    def test_february_30_never_fires(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 30 feb *", ANY_TIME)

    def test_april_31_never_fires(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 31 4 *", ANY_TIME)


class ParsingTest(unittest.TestCase):
    """Grammar details: whitespace, names, lists, mixed tokens."""

    def test_surrounding_whitespace_is_ignored(self):
        self.assertEqual(
            next_fire("   0 0 1 1 *   ", datetime(2026, 1, 1, 0, 1)),
            datetime(2027, 1, 1, 0, 0),
        )

    def test_multiple_spaces_between_fields(self):
        self.assertEqual(
            next_fire("0    0\t1  1 *", datetime(2026, 1, 1, 0, 1)),
            datetime(2027, 1, 1, 0, 0),
        )

    def test_all_month_names(self):
        names = ["jan", "feb", "mar", "apr", "may", "jun",
                 "jul", "aug", "sep", "oct", "nov", "dec"]
        for index, name in enumerate(names, start=1):
            with self.subTest(name=name):
                fire = next_fire("0 0 1 %s *" % name, datetime(2026, 1, 1, 0, 1))
                self.assertEqual(fire.month, index)

    def test_all_day_of_week_names(self):
        names = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
        for index, name in enumerate(names):
            with self.subTest(name=name):
                fire = next_fire("0 0 * * %s" % name, datetime(2026, 1, 1, 0, 0))
                self.assertEqual(fire.isoweekday() % 7, index)

    def test_numeric_and_named_month_are_equivalent(self):
        after = datetime(2026, 3, 1, 0, 0)
        self.assertEqual(
            next_fire("0 12 1 jan,jul *", after),
            next_fire("0 12 1 1,7 *", after),
        )

    def test_names_and_numbers_mixed_in_one_list(self):
        # dom '*' so dow decides: 1 (Monday) or fri.
        dows = set()
        cursor = datetime(2026, 1, 1, 0, 0)
        for _ in range(4):
            cursor = next_fire("0 0 * * 1,fri", cursor)
            dows.add(cursor.isoweekday() % 7)
        self.assertEqual(dows, {1, 5})

    def test_list_of_ranges_and_steps(self):
        fires = []
        cursor = datetime(2026, 1, 1, 0, 0) - timedelta(minutes=1)
        while True:
            cursor = next_fire("0-10/2,30 0 1 1 *", cursor)
            if cursor >= datetime(2026, 1, 1, 1, 0):
                break
            fires.append(cursor.minute)
        self.assertEqual(fires, [0, 2, 4, 6, 8, 10, 30])

    def test_duplicate_values_in_a_list_are_harmless(self):
        fires = []
        cursor = datetime(2026, 1, 1, 0, 0) - timedelta(minutes=1)
        while True:
            cursor = next_fire("5,5,1-5 0 1 1 *", cursor)
            if cursor >= datetime(2026, 1, 1, 1, 0):
                break
            fires.append(cursor.minute)
        self.assertEqual(fires, [1, 2, 3, 4, 5])

    def test_leading_zeros_are_accepted(self):
        self.assertEqual(
            next_fire("05 09 01 01 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 9, 5),
        )

    def test_every_minute_expression(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 6, 15, 13, 44)),
            datetime(2026, 6, 15, 13, 45),
        )


class ErrorTest(unittest.TestCase):
    """Every documented error case raises ValueError."""

    def assertRejected(self, expr):
        with self.subTest(expr=expr):
            with self.assertRaises(ValueError):
                next_fire(expr, ANY_TIME)

    def test_field_count_errors(self):
        for expr in ("", "   ", "* * * *", "* * * * * *", "*", "* *", "* * *"):
            self.assertRejected(expr)

    def test_minute_out_of_domain(self):
        self.assertRejected("60 * * * *")

    def test_hour_out_of_domain(self):
        self.assertRejected("* 24 * * *")

    def test_day_of_month_out_of_domain(self):
        self.assertRejected("* * 0 * *")
        self.assertRejected("* * 32 * *")

    def test_month_out_of_domain(self):
        self.assertRejected("* * * 13 *")
        self.assertRejected("* * * 0 *")

    def test_day_of_week_seven_is_invalid(self):
        self.assertRejected("* * * * 7")

    def test_uppercase_names_rejected(self):
        for expr in ("* * * * SUN", "* * * * Sun", "* * * JAN *", "* * * Jan *"):
            self.assertRejected(expr)

    def test_zero_step(self):
        self.assertRejected("*/0 * * * *")
        self.assertRejected("1-5/0 * * * *")

    def test_step_on_a_single_value(self):
        self.assertRejected("5/10 * * * *")
        self.assertRejected("* * * jan/2 *")

    def test_empty_list_element(self):
        for expr in ("1,,3 * * * *", "1, * * * *", ",1 * * * *", "1,3, * * * *"):
            self.assertRejected(expr)

    def test_unknown_token(self):
        for expr in ("1x * * * *", "abc * * * *", "* * * xyz *", "* * * * funday"):
            self.assertRejected(expr)

    def test_wrap_endpoints_must_be_in_domain(self):
        self.assertRejected("70-10 * * * *")
        self.assertRejected("50-70 * * * *")
        self.assertRejected("* * * * 7-2")

    def test_L_misuse(self):
        self.assertRejected("0 0 L,15 * *")
        self.assertRejected("0 0 * * L")
        self.assertRejected("0 0 l * *")

    def test_malformed_range(self):
        for expr in ("-5 * * * *", "5- * * * *", "1-2-3 * * * *", "- * * * *"):
            self.assertRejected(expr)

    def test_malformed_step(self):
        for expr in ("*/ * * * *", "/5 * * * *", "*/2/3 * * * *", "*/x * * * *"):
            self.assertRejected(expr)

    def test_negative_values(self):
        self.assertRejected("-1 * * * *")
        self.assertRejected("* * * * -1")

    def test_non_string_expression(self):
        with self.assertRaises(ValueError):
            next_fire(None, ANY_TIME)

    def test_valid_wrap_ranges_do_not_raise(self):
        for expr in ("30-5 * * * *", "0 0 1 nov-feb *", "0 0 * * fri-mon"):
            with self.subTest(expr=expr):
                self.assertIsInstance(next_fire(expr, ANY_TIME), datetime)


class ConsistencyTest(unittest.TestCase):
    """Cross-checks over a broad sweep of generated instants."""

    def test_iterating_is_monotonic_and_matches_the_spec_fields(self):
        expr = "5,20 3,15 * * mon-fri"
        cursor = datetime(2026, 1, 1, 0, 0)
        previous = cursor
        for _ in range(40):
            cursor = next_fire(expr, cursor)
            self.assertGreater(cursor, previous)
            self.assertIn(cursor.minute, (5, 20))
            self.assertIn(cursor.hour, (3, 15))
            self.assertIn(cursor.isoweekday() % 7, {1, 2, 3, 4, 5})
            previous = cursor

    def test_minute_by_minute_scan_agrees_with_next_fire(self):
        # Brute-force reference over a two-day window.
        expr = "*/20 1-5/2 * * *"
        start = datetime(2026, 3, 14, 0, 0)
        end = start + timedelta(days=2)
        brute = []
        cursor = start
        while cursor < end:
            cursor += timedelta(minutes=1)
            if cursor.minute in {0, 20, 40} and cursor.hour in {1, 3, 5}:
                brute.append(cursor)

        walked = []
        cursor = start
        while True:
            cursor = next_fire(expr, cursor)
            if cursor >= end:
                break
            walked.append(cursor)
        self.assertEqual(walked, brute)

    def test_no_result_is_ever_equal_to_the_input(self):
        expr = "* * * * *"
        moment = datetime(2026, 9, 9, 9, 9)
        self.assertNotEqual(next_fire(expr, moment), moment)


if __name__ == "__main__":
    unittest.main()
