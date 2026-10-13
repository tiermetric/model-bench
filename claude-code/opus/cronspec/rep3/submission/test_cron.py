"""Standard-library unittest suite for cron.next_fire."""

import unittest
from datetime import datetime, timedelta

from cron import next_fire


EPOCH = datetime(2026, 1, 1, 0, 0)


class SpecWorkedExamplesTest(unittest.TestCase):
    """Every worked example from the specification, verbatim."""

    def test_original_deviations(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2026, 6, 1, 12, 5),
        )
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5)),
            datetime(2026, 6, 1, 12, 15),
        )
        self.assertEqual(
            next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 2, 13, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0)),
            datetime(2027, 2, 1, 0, 0),
        )
        self.assertEqual(
            next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 27, 9, 30),
        )
        self.assertEqual(
            next_fire("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 7, 1, 12, 0),
        )

    def test_rule4_wrap_around_ranges(self):
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30)),
            datetime(2026, 1, 1, 0, 50),
        )
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 55)),
            datetime(2026, 1, 1, 0, 56),
        )
        self.assertEqual(
            next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 11, 1, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )

    def test_rule5_steps_through_the_wrap(self):
        self.assertEqual(
            next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 5),
        )
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )

    def test_rule6_last_day_of_month(self):
        self.assertEqual(
            next_fire("0 0 L * *", datetime(2026, 1, 15, 0, 0)),
            datetime(2026, 1, 31, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0)),
            datetime(2026, 2, 28, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 7, 31, 0, 0),
        )

    def test_rule7_month_length_and_leap_rollover(self):
        self.assertEqual(
            next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0)),
            datetime(2026, 5, 31, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 30 * *", datetime(2026, 2, 1, 0, 0)),
            datetime(2026, 3, 30, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )


class BasicSemanticsTest(unittest.TestCase):
    def test_result_is_strictly_after_and_minute_aligned(self):
        result = next_fire("* * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 1))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)

    def test_exact_match_is_not_returned(self):
        # 00:15 already matches, so the answer must be the following one.
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 15)),
            datetime(2026, 1, 1, 0, 30),
        )

    def test_seconds_and_microseconds_round_up_to_next_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 30)),
            datetime(2026, 1, 1, 0, 1),
        )
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 0, 1)),
            datetime(2026, 1, 1, 0, 1),
        )
        # 00:14:59.999999 -> the next whole minute is 00:15, which matches.
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 14, 59, 999999)),
            datetime(2026, 1, 1, 0, 15),
        )

    def test_rollover_across_hour_day_month_and_year(self):
        self.assertEqual(
            next_fire("0 * * * *", datetime(2026, 1, 1, 0, 30)),
            datetime(2026, 1, 1, 1, 0),
        )
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 1)),
            datetime(2026, 1, 2, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 1 * *", datetime(2026, 1, 2, 0, 0)),
            datetime(2026, 2, 1, 0, 0),
        )
        self.assertEqual(
            next_fire("59 23 31 12 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2026, 12, 31, 23, 59),
        )
        self.assertEqual(
            next_fire("0 0 1 1 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2027, 1, 1, 0, 0),
        )

    def test_whitespace_is_tolerated(self):
        expected = datetime(2026, 1, 1, 0, 15)
        self.assertEqual(next_fire("  */15 * * * *  ", EPOCH), expected)
        self.assertEqual(next_fire("*/15   *  *   *    *", EPOCH), expected)
        self.assertEqual(next_fire("\t*/15 * * * *\n", EPOCH), expected)

    def test_single_values_and_lists(self):
        self.assertEqual(
            next_fire("7 3 * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 3, 7),
        )
        self.assertEqual(
            next_fire("1,3,5 0 1 1 *", datetime(2026, 1, 1, 0, 1)),
            datetime(2026, 1, 1, 0, 3),
        )
        self.assertEqual(
            next_fire("0-10/2,30 0 1 1 *", datetime(2026, 1, 1, 0, 10)),
            datetime(2026, 1, 1, 0, 30),
        )

    def test_names_and_mixed_name_number_lists(self):
        self.assertEqual(
            next_fire("0 0 * * mon,wed,fri", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 22, 0, 0),  # Tue -> Wed
        )
        self.assertEqual(
            next_fire("0 0 1 jan,jul *", datetime(2026, 8, 1, 0, 0)),
            datetime(2027, 1, 1, 0, 0),
        )
        # "1,fri" mixes a number and a name in the day-of-week field.
        self.assertEqual(
            next_fire("0 0 * * 1,fri", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),  # Tue -> Fri
        )

    def test_all_month_names_resolve(self):
        names = [
            "jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "oct", "nov", "dec",
        ]
        for index, name in enumerate(names, start=1):
            with self.subTest(month=name):
                self.assertEqual(
                    next_fire("0 0 1 %s *" % name, datetime(2026, 1, 1, 0, 1)),
                    next_fire("0 0 1 %d *" % index, datetime(2026, 1, 1, 0, 1)),
                )

    def test_all_dow_names_resolve(self):
        names = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"]
        for index, name in enumerate(names):
            with self.subTest(dow=name):
                fired = next_fire("0 0 * * %s" % name, EPOCH)
                self.assertEqual(fired.isoweekday() % 7, index)
                self.assertEqual(
                    fired, next_fire("0 0 * * %d" % index, EPOCH)
                )

    def test_domain_boundaries_are_accepted(self):
        self.assertEqual(
            next_fire("59 23 * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 23, 59),
        )
        self.assertEqual(
            next_fire("0 0 1 1 0", datetime(2026, 1, 1, 0, 0)),
            # 1 January that is also a Sunday: 2028-01-01 is a Saturday,
            # so assert only the invariants the domain guarantees.
            next_fire("0 0 1 1 sun", datetime(2026, 1, 1, 0, 0)),
        )
        self.assertEqual(
            next_fire("0 0 31 12 6", datetime(2026, 1, 1, 0, 0)),
            datetime(2033, 12, 31, 0, 0),
        )


class Deviation1IntersectionTest(unittest.TestCase):
    """day-of-month AND day-of-week intersect when both are restricted."""

    def test_friday_the_thirteenth(self):
        self.assertEqual(
            next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 2, 13, 0, 0),
        )
        # The next one after February 2026 is March 2027 (not March 2026).
        self.assertEqual(
            next_fire("0 0 13 * fri", datetime(2026, 2, 13, 0, 0)),
            datetime(2026, 3, 13, 0, 0),
        )

    def test_first_of_month_that_is_a_monday(self):
        result = next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0))
        self.assertEqual(result, datetime(2027, 2, 1, 0, 0))
        self.assertEqual(result.day, 1)
        self.assertEqual(result.isoweekday(), 1)

    def test_union_would_have_matched_sooner(self):
        # Standard (union) cron would fire on 2026-07-23 (a Thursday) or on
        # the 13th; the intersection must skip both.
        result = next_fire("0 0 13 * thu", datetime(2026, 7, 1, 0, 0))
        self.assertEqual(result.day, 13)
        self.assertEqual(result.isoweekday(), 4)
        self.assertEqual(result, datetime(2026, 8, 13, 0, 0))

    def test_star_dom_lets_dow_decide(self):
        self.assertEqual(
            next_fire("0 0 * * mon", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 27, 0, 0),
        )

    def test_star_dow_lets_dom_decide(self):
        self.assertEqual(
            next_fire("0 0 15 * *", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 8, 15, 0, 0),
        )

    def test_both_star_matches_every_day(self):
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 7, 22, 0, 1)),
            datetime(2026, 7, 23, 0, 0),
        )

    def test_step_dom_counts_as_restricted(self):
        # */2 on day-of-month is restricted, so it must intersect with dow.
        result = next_fire("0 0 */2 * sun", datetime(2026, 7, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 5, 0, 0))
        self.assertEqual(result.isoweekday() % 7, 0)
        self.assertEqual(result.day % 2, 1)  # */2 from 1 -> odd days

    def test_range_dow_counts_as_restricted(self):
        result = next_fire("0 0 1-7 * 1-5", datetime(2026, 8, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 8, 3, 0, 0))  # Mon 3 Aug

    def test_l_counts_as_restricted_dom(self):
        result = next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 31, 0, 0))
        self.assertEqual(result.isoweekday(), 5)


class Deviation2StepAnchorTest(unittest.TestCase):
    """Steps are anchored at the range start, not at zero."""

    def _minutes_in_hour(self, expr, hour_start):
        """Collect every minute the expression fires within one hour."""
        fired = []
        cursor = hour_start - timedelta(minutes=1)
        end = hour_start.replace(minute=59)
        while True:
            cursor = next_fire(expr, cursor)
            if cursor > end:
                break
            fired.append(cursor.minute)
        return fired

    def test_5_30_by_10_is_5_15_25(self):
        self.assertEqual(
            self._minutes_in_hour("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12)),
            [5, 15, 25],
        )

    def test_10_50_by_15_is_10_25_40(self):
        self.assertEqual(
            self._minutes_in_hour("10-50/15 12 1 6 *", datetime(2026, 6, 1, 12)),
            [10, 25, 40],
        )

    def test_star_step_anchors_at_domain_floor(self):
        self.assertEqual(
            self._minutes_in_hour("*/15 12 1 6 *", datetime(2026, 6, 1, 12)),
            [0, 15, 30, 45],
        )

    def test_step_anchor_on_hour_field(self):
        # 5-23/6 -> 5, 11, 17, 23 (not 6, 12, 18).
        hours = []
        cursor = datetime(2026, 6, 1, 0, 0)
        for _ in range(4):
            cursor = next_fire("0 5-23/6 1 6 *", cursor)
            hours.append(cursor.hour)
        self.assertEqual(hours, [5, 11, 17, 23])

    def test_step_anchor_on_day_of_month(self):
        days = []
        cursor = datetime(2026, 6, 1, 0, 0)
        for _ in range(4):
            cursor = next_fire("0 0 4-30/7 6 *", cursor)
            days.append(cursor.day)
        self.assertEqual(days, [4, 11, 18, 25])

    def test_star_step_on_day_of_month_anchors_at_one(self):
        days = []
        cursor = datetime(2026, 6, 1, 0, 0) - timedelta(minutes=1)
        for _ in range(4):
            cursor = next_fire("0 0 */5 6 *", cursor)
            days.append(cursor.day)
        self.assertEqual(days, [1, 6, 11, 16])

    def test_star_step_on_month_anchors_at_one(self):
        months = []
        cursor = datetime(2025, 12, 31, 23, 59)
        for _ in range(4):
            cursor = next_fire("0 0 1 */4 *", cursor)
            months.append((cursor.year, cursor.month))
        self.assertEqual(
            months, [(2026, 1), (2026, 5), (2026, 9), (2027, 1)]
        )

    def test_step_larger_than_range_yields_only_the_start(self):
        self.assertEqual(
            next_fire("5-30/100 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2026, 6, 1, 12, 5),
        )
        self.assertEqual(
            next_fire("5-30/100 12 1 6 *", datetime(2026, 6, 1, 12, 5)),
            datetime(2027, 6, 1, 12, 5),
        )

    def test_step_of_one_keeps_every_value(self):
        self.assertEqual(
            next_fire("5-30/1 12 1 6 *", datetime(2026, 6, 1, 12, 5)),
            datetime(2026, 6, 1, 12, 6),
        )


class Deviation3StrictDomainTest(unittest.TestCase):
    def test_dow_seven_is_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", EPOCH)

    def test_dow_seven_invalid_inside_list_and_range(self):
        for expr in ("* * * * 0,7", "* * * * 5-7", "* * * * 7-1"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)

    def test_uppercase_names_rejected(self):
        for expr in ("* * * * SUN", "* * * * Sun", "0 0 1 JAN *", "0 0 1 Jan *"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)

    def test_each_field_domain_upper_and_lower_bounds(self):
        bad = [
            "60 * * * *",
            "-1 * * * *",
            "* 24 * * *",
            "* * 0 * *",
            "* * 32 * *",
            "* * * 0 *",
            "* * * 13 *",
            "* * * * 7",
        ]
        for expr in bad:
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)

        good = [
            "0 * * * *",
            "59 * * * *",
            "* 0 * * *",
            "* 23 * * *",
            "* * 1 * *",
            "* * 31 * *",
            "* * * 1 *",
            "* * * 12 *",
            "* * * * 0",
            "* * * * 6",
        ]
        for expr in good:
            with self.subTest(expr=expr):
                self.assertIsInstance(next_fire(expr, EPOCH), datetime)


class Rule4WrapRangeTest(unittest.TestCase):
    def test_minute_wrap_set_membership(self):
        expected = list(range(50, 60)) + list(range(0, 11))
        seen = []
        cursor = datetime(2026, 1, 1, 0, 49)
        for _ in range(21):
            cursor = next_fire("50-10 * * * *", cursor)
            seen.append(cursor.minute)
        self.assertEqual(sorted(seen), sorted(expected))

    def test_month_name_wrap(self):
        self.assertEqual(
            next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 11, 1, 0, 0),
        )
        months = []
        cursor = datetime(2026, 10, 1, 0, 0)
        for _ in range(4):
            cursor = next_fire("0 0 1 nov-feb *", cursor)
            months.append((cursor.year, cursor.month))
        self.assertEqual(
            months, [(2026, 11), (2026, 12), (2027, 1), (2027, 2)]
        )

    def test_dow_wrap(self):
        self.assertEqual(
            next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )
        days = []
        cursor = datetime(2026, 7, 23, 0, 0)
        for _ in range(4):
            cursor = next_fire("0 0 * * fri-mon", cursor)
            days.append(cursor.day)
        self.assertEqual(days, [24, 25, 26, 27])  # Fri Sat Sun Mon

    def test_numeric_dow_wrap_matches_named(self):
        self.assertEqual(
            next_fire("0 0 * * 5-1", datetime(2026, 7, 21, 0, 0)),
            next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0)),
        )

    def test_wrap_range_valid_not_an_error(self):
        for expr in ("30-5 * * * *", "0 0 1 nov-feb *", "0 0 * * fri-mon"):
            with self.subTest(expr=expr):
                self.assertIsInstance(next_fire(expr, EPOCH), datetime)

    def test_hour_wrap(self):
        hours = []
        cursor = datetime(2026, 1, 1, 21, 59)
        for _ in range(5):
            cursor = next_fire("0 22-1 * * *", cursor)
            hours.append(cursor.hour)
        self.assertEqual(hours, [22, 23, 0, 1, 22])

    def test_day_of_month_wrap(self):
        days = []
        cursor = datetime(2026, 1, 28, 0, 0)
        for _ in range(5):
            cursor = next_fire("0 0 30-2 * *", cursor)
            days.append((cursor.month, cursor.day))
        # Jan: 30, 31; Feb (28 days) has no 30/31 -> 1, 2; then Mar 1, 2.
        self.assertEqual(days, [(1, 30), (1, 31), (2, 1), (2, 2), (3, 1)])

    def test_wrap_endpoints_must_be_in_domain(self):
        for expr in ("70-10 * * * *", "50-70 * * * *", "* * 31-0 * *"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)


class Rule5WrapStepTest(unittest.TestCase):
    def test_minute_50_10_step_5(self):
        seen = []
        cursor = datetime(2026, 1, 1, 0, 49)
        for _ in range(5):
            cursor = next_fire("50-10/5 * * * *", cursor)
            seen.append(cursor.minute)
        self.assertEqual(sorted(set(seen)), [0, 5, 10, 50, 55])

    def test_worked_example_50_10_step_5(self):
        self.assertEqual(
            next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 5),
        )

    def test_dow_fri_tue_step_2(self):
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )
        days = []
        cursor = datetime(2026, 7, 23, 0, 0)
        for _ in range(3):
            cursor = next_fire("0 0 * * fri-tue/2", cursor)
            days.append(cursor.isoweekday() % 7)
        self.assertEqual(sorted(days), [0, 2, 5])  # sun, tue, fri

    def test_month_wrap_step(self):
        # nov-mar wrapped order: nov, dec, jan, feb, mar; every 2nd -> nov, jan, mar
        months = []
        cursor = datetime(2026, 10, 1, 0, 0)
        for _ in range(3):
            cursor = next_fire("0 0 1 nov-mar/2 *", cursor)
            months.append((cursor.year, cursor.month))
        self.assertEqual(months, [(2026, 11), (2027, 1), (2027, 3)])


class Rule6LastDayTest(unittest.TestCase):
    def test_last_day_each_month_of_2026(self):
        expected = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        cursor = datetime(2025, 12, 31, 23, 59)
        for month, last in enumerate(expected, start=1):
            cursor = next_fire("0 0 L * *", cursor)
            with self.subTest(month=month):
                self.assertEqual(cursor, datetime(2026, month, last, 0, 0))

    def test_leap_february(self):
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 2, 28, 0, 0),
        )
        # 2100 is not a leap year.
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2100, 1, 1, 0, 0)),
            datetime(2100, 2, 28, 0, 0),
        )
        # 2000 was a leap year.
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2000, 1, 1, 0, 0)),
            datetime(2000, 2, 29, 0, 0),
        )

    def test_l_intersects_with_dow(self):
        result = next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 31, 0, 0))

    def test_l_with_time_fields(self):
        self.assertEqual(
            next_fire("30 23 L * *", datetime(2026, 1, 31, 0, 0)),
            datetime(2026, 1, 31, 23, 30),
        )

    def test_l_must_be_the_whole_dom_field(self):
        for expr in (
            "0 0 L,15 * *",
            "0 0 15,L * *",
            "0 0 L-5 * *",
            "0 0 1-L * *",
            "0 0 L/2 * *",
            "0 0 LL * *",
        ):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)

    def test_l_only_in_day_of_month_field(self):
        for expr in (
            "L * * * *",
            "0 L * * *",
            "0 0 * L *",
            "0 0 * * L",
        ):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)

    def test_lowercase_l_is_not_L(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 l * *", EPOCH)


class Rule7MonthLengthTest(unittest.TestCase):
    def test_31st_skips_short_months(self):
        self.assertEqual(
            next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0)),
            datetime(2026, 5, 31, 0, 0),
        )
        months = []
        cursor = datetime(2026, 1, 1, 0, 0)
        for _ in range(7):
            cursor = next_fire("0 0 31 * *", cursor)
            months.append(cursor.month)
        self.assertEqual(months, [1, 3, 5, 7, 8, 10, 12])

    def test_30th_skips_february(self):
        self.assertEqual(
            next_fire("0 0 30 * *", datetime(2026, 2, 1, 0, 0)),
            datetime(2026, 3, 30, 0, 0),
        )

    def test_no_clamping_onto_last_day(self):
        # A naive clamp would return Feb 28; the correct answer is Mar 31.
        self.assertEqual(
            next_fire("0 0 31 * *", datetime(2026, 2, 1, 0, 0)),
            datetime(2026, 3, 31, 0, 0),
        )

    def test_feb_29_only_in_leap_years(self):
        self.assertEqual(
            next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 29 feb *", datetime(2028, 2, 29, 0, 0)),
            datetime(2032, 2, 29, 0, 0),
        )
        # Century rule: after 2096 comes 2104, skipping non-leap 2100.
        self.assertEqual(
            next_fire("0 0 29 feb *", datetime(2096, 3, 1, 0, 0)),
            datetime(2104, 2, 29, 0, 0),
        )

    def test_feb_30_never_fires(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 30 feb *", EPOCH)


class ErrorContractTest(unittest.TestCase):
    """Every error case listed in the specification."""

    SPEC_ERRORS = [
        "",
        "* * * *",
        "* * * * * *",
        "60 * * * *",
        "* 24 * * *",
        "* * 0 * *",
        "* * * 13 *",
        "* * * * 7",
        "* * * * SUN",
        "*/0 * * * *",
        "5/10 * * * *",
        "1,,3 * * * *",
        "1x * * * *",
        "70-10 * * * *",
        "0 0 L,15 * *",
        "0 0 * * L",
        "0 0 l * *",
    ]

    def test_spec_error_cases(self):
        for expr in self.SPEC_ERRORS:
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)

    def test_field_count(self):
        for expr in ("", "   ", "*", "* *", "* * *", "* * * *", "* * * * * *",
                     "* * * * * * *"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)

    def test_step_must_be_positive(self):
        for expr in ("*/0 * * * *", "0-10/0 * * * *", "*/-1 * * * *",
                     "*/ * * * *", "*/x * * * *"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)

    def test_step_only_on_star_or_range(self):
        for expr in ("5/10 * * * *", "* 3/2 * * *", "0 0 * jan/2 *",
                     "0 0 * * mon/2"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)

    def test_double_step_rejected(self):
        with self.assertRaises(ValueError):
            next_fire("0-10/2/3 * * * *", EPOCH)

    def test_empty_list_elements(self):
        for expr in ("1,,3 * * * *", ",1 * * * *", "1, * * * *", ", * * * *"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)

    def test_unknown_tokens(self):
        for expr in ("1x * * * *", "abc * * * *", "* * * xyz *",
                     "* * * * funday", "1.5 * * * *", "+1 * * * *"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)

    def test_malformed_ranges(self):
        for expr in ("1-2-3 * * * *", "1- * * * *", "-5 * * * *",
                     "- * * * *", "*-5 * * * *"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)

    def test_names_only_in_month_and_dow(self):
        for expr in ("jan * * * *", "* mon * * *", "* * mon * *"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)

    def test_month_names_not_valid_in_dow_and_vice_versa(self):
        for expr in ("0 0 * * jan", "0 0 * mon *"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, EPOCH)

    def test_non_string_expression(self):
        with self.assertRaises(ValueError):
            next_fire(None, EPOCH)


class PublicApiTest(unittest.TestCase):
    def test_module_exports_only_next_fire(self):
        import cron

        self.assertEqual(cron.__all__, ["next_fire"])
        public = [
            name
            for name in dir(cron)
            if not name.startswith("_") and callable(getattr(cron, name))
        ]
        self.assertEqual(
            [name for name in public if name not in ("datetime", "timedelta")],
            ["next_fire"],
        )

    def test_repeated_calls_are_deterministic(self):
        first = next_fire("0 0 13 * fri", EPOCH)
        second = next_fire("0 0 13 * fri", EPOCH)
        self.assertEqual(first, second)

    def test_chaining_produces_a_strictly_increasing_sequence(self):
        cursor = EPOCH
        previous = None
        for _ in range(50):
            cursor = next_fire("*/7 */3 * * *", cursor)
            if previous is not None:
                self.assertGreater(cursor, previous)
            previous = cursor


if __name__ == "__main__":
    unittest.main()
