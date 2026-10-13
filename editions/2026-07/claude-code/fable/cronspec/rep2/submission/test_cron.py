"""Tests for cron.next_fire — covers the grammar, all three deviations,
all four hardening rules, every worked example, and every error case."""

import unittest
from datetime import datetime

from cron import next_fire


class TestBasics(unittest.TestCase):
    def test_strictly_after_exact_minute(self):
        # `after` on a matching minute boundary must NOT be returned.
        self.assertEqual(next_fire("* * * * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 1, 0, 1))

    def test_nonzero_seconds_round_up(self):
        self.assertEqual(next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 30)),
                         datetime(2026, 1, 1, 0, 1))

    def test_nonzero_microseconds_round_up(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 0, 999999)),
            datetime(2026, 1, 1, 0, 1))

    def test_result_has_zero_seconds_and_microseconds(self):
        got = next_fire("*/5 * * * *", datetime(2026, 3, 4, 5, 6, 7, 8))
        self.assertEqual(got.second, 0)
        self.assertEqual(got.microsecond, 0)
        self.assertEqual(got, datetime(2026, 3, 4, 5, 10))

    def test_whitespace_tolerated(self):
        self.assertEqual(
            next_fire("   30    9   *  *   mon   ", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 27, 9, 30))

    def test_single_values(self):
        self.assertEqual(next_fire("7 3 * * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 1, 3, 7))

    def test_hour_rollover_to_next_day(self):
        self.assertEqual(next_fire("15 8 * * *", datetime(2026, 1, 1, 9, 0)),
                         datetime(2026, 1, 2, 8, 15))

    def test_comma_list_numbers(self):
        self.assertEqual(next_fire("1,3,5 * * * *", datetime(2026, 1, 1, 0, 3)),
                         datetime(2026, 1, 1, 0, 5))

    def test_comma_list_mixed_range_step_and_value(self):
        # 0-10/2,30 -> {0,2,4,6,8,10,30}
        self.assertEqual(next_fire("0-10/2,30 * * * *", datetime(2026, 1, 1, 0, 10)),
                         datetime(2026, 1, 1, 0, 30))
        self.assertEqual(next_fire("0-10/2,30 * * * *", datetime(2026, 1, 1, 0, 5)),
                         datetime(2026, 1, 1, 0, 6))

    def test_month_names(self):
        self.assertEqual(next_fire("0 0 1 dec *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 12, 1, 0, 0))

    def test_dow_names(self):
        self.assertEqual(next_fire("0 0 * * sat", datetime(2026, 7, 22, 0, 0)),
                         datetime(2026, 7, 25, 0, 0))

    def test_names_and_numbers_mixed_in_list(self):
        # 1,fri -> Monday or Friday
        self.assertEqual(next_fire("0 0 * * 1,fri", datetime(2026, 7, 22, 0, 0)),
                         datetime(2026, 7, 24, 0, 0))  # Wed -> Fri
        self.assertEqual(next_fire("0 0 * * 1,fri", datetime(2026, 7, 25, 0, 0)),
                         datetime(2026, 7, 27, 0, 0))  # Sat -> Mon

    def test_sunday_is_zero(self):
        # 2026-07-26 is a Sunday.
        self.assertEqual(next_fire("0 0 * * 0", datetime(2026, 7, 22, 0, 0)),
                         datetime(2026, 7, 26, 0, 0))

    def test_year_rollover(self):
        self.assertEqual(next_fire("0 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2027, 1, 1, 0, 0))


class TestDeviation1Intersection(unittest.TestCase):
    def test_friday_the_13th(self):
        self.assertEqual(next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 2, 13, 0, 0))

    def test_first_and_monday(self):
        self.assertEqual(next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0)),
                         datetime(2027, 2, 1, 0, 0))

    def test_dom_star_dow_restricted(self):
        # Only dow decides: every Monday.
        self.assertEqual(next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0)),
                         datetime(2026, 7, 27, 9, 30))

    def test_dow_star_dom_restricted(self):
        # Only dom decides: the 13th regardless of weekday.
        self.assertEqual(next_fire("0 0 13 * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 13, 0, 0))

    def test_both_star_every_day(self):
        self.assertEqual(next_fire("0 0 * * *", datetime(2026, 1, 1, 5, 0)),
                         datetime(2026, 1, 2, 0, 0))

    def test_step_on_dom_counts_as_restricted(self):
        # dom */2 (odd days) intersected with Friday: 2026-07-22 is Wed;
        # Fri 24th is even -> skip; next odd Friday is 2026-07-31.
        self.assertEqual(next_fire("0 0 */2 * fri", datetime(2026, 7, 22, 0, 0)),
                         datetime(2026, 7, 31, 0, 0))

    def test_range_on_dow_counts_as_restricted(self):
        # dom 20 intersected with dow 1-5: 2026-09-20 is a Sunday -> skip
        # to 2026-10-20 (Tuesday).
        self.assertEqual(next_fire("0 0 20 * 1-5", datetime(2026, 9, 1, 0, 0)),
                         datetime(2026, 10, 20, 0, 0))


class TestDeviation2StepAnchor(unittest.TestCase):
    def test_range_step_anchored_at_start(self):
        self.assertEqual(next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
                         datetime(2026, 6, 1, 12, 5))

    def test_range_step_second_hit(self):
        self.assertEqual(next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5)),
                         datetime(2026, 6, 1, 12, 15))

    def test_range_step_does_not_include_multiples(self):
        # 5-30/10 = {5,15,25}: from :15 the next is :25, NOT :20 or :30.
        self.assertEqual(next_fire("5-30/10 * * * *", datetime(2026, 1, 1, 0, 15)),
                         datetime(2026, 1, 1, 0, 25))
        self.assertEqual(next_fire("5-30/10 * * * *", datetime(2026, 1, 1, 0, 25)),
                         datetime(2026, 1, 1, 1, 5))

    def test_10_50_15(self):
        # 10-50/15 -> {10,25,40}
        self.assertEqual(next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 1, 0, 10))
        self.assertEqual(next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 10)),
                         datetime(2026, 1, 1, 0, 25))
        self.assertEqual(next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 40)),
                         datetime(2026, 1, 1, 1, 10))  # 50 is NOT in the set

    def test_star_step_anchored_at_domain_floor(self):
        self.assertEqual(next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 1, 0, 15))
        self.assertEqual(next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 46)),
                         datetime(2026, 1, 1, 1, 0))

    def test_star_step_month_floor_is_one(self):
        # */5 on month anchors at 1 -> {1,6,11}.
        self.assertEqual(next_fire("0 0 1 */5 *", datetime(2026, 1, 15, 0, 0)),
                         datetime(2026, 6, 1, 0, 0))
        self.assertEqual(next_fire("0 0 1 */5 *", datetime(2026, 6, 15, 0, 0)),
                         datetime(2026, 11, 1, 0, 0))


class TestDeviation3StrictDomains(unittest.TestCase):
    def test_dow_seven_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1))

    def test_dow_seven_invalid_in_range(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 5-7", datetime(2026, 1, 1))

    def test_dow_seven_invalid_in_list(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 1,7", datetime(2026, 1, 1))

    def test_uppercase_names_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * SUN", datetime(2026, 1, 1))
        with self.assertRaises(ValueError):
            next_fire("* * * JAN *", datetime(2026, 1, 1))
        with self.assertRaises(ValueError):
            next_fire("* * * Jan *", datetime(2026, 1, 1))


class TestRule4WrapRanges(unittest.TestCase):
    def test_minute_wrap(self):
        self.assertEqual(next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30)),
                         datetime(2026, 1, 1, 0, 50))

    def test_minute_wrap_inside_high_side(self):
        self.assertEqual(next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 55)),
                         datetime(2026, 1, 1, 0, 56))

    def test_minute_wrap_low_side(self):
        # After :59 the wrapped set continues at :00 of the next hour.
        self.assertEqual(next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 59)),
                         datetime(2026, 1, 1, 1, 0))
        self.assertEqual(next_fire("50-10 * * * *", datetime(2026, 1, 1, 1, 10)),
                         datetime(2026, 1, 1, 1, 50))

    def test_month_wrap(self):
        self.assertEqual(next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0)),
                         datetime(2026, 11, 1, 0, 0))

    def test_month_wrap_low_side_year_rollover(self):
        self.assertEqual(next_fire("0 0 1 nov-feb *", datetime(2026, 12, 15, 0, 0)),
                         datetime(2027, 1, 1, 0, 0))

    def test_dow_wrap(self):
        self.assertEqual(next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0)),
                         datetime(2026, 7, 24, 0, 0))

    def test_dow_wrap_includes_low_side(self):
        # fri-mon = {fri,sat,sun,mon}: Sat 7/25 -> Sun 7/26 -> Mon 7/27.
        self.assertEqual(next_fire("0 0 * * fri-mon", datetime(2026, 7, 25, 0, 0)),
                         datetime(2026, 7, 26, 0, 0))
        self.assertEqual(next_fire("0 0 * * fri-mon", datetime(2026, 7, 26, 0, 0)),
                         datetime(2026, 7, 27, 0, 0))

    def test_dow_wrap_excludes_middle(self):
        # Tue 7/28 is not in fri-mon; next is Fri 7/31.
        self.assertEqual(next_fire("0 0 * * fri-mon", datetime(2026, 7, 27, 0, 0)),
                         datetime(2026, 7, 31, 0, 0))

    def test_wrap_is_not_an_error(self):
        # 30-5, nov-feb, fri-mon are all valid.
        next_fire("30-5 * * * *", datetime(2026, 1, 1))
        next_fire("0 0 1 nov-feb *", datetime(2026, 1, 1))
        next_fire("0 0 * * fri-mon", datetime(2026, 1, 1))


class TestRule5WrapSteps(unittest.TestCase):
    def test_minute_wrap_step(self):
        # 50-10/5 -> {50,55,0,5,10}
        self.assertEqual(next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 1, 0, 5))
        self.assertEqual(next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 5)),
                         datetime(2026, 1, 1, 0, 10))
        self.assertEqual(next_fire("50-10/5 * * * *", datetime(2026, 1, 1, 0, 10)),
                         datetime(2026, 1, 1, 0, 50))
        self.assertEqual(next_fire("50-10/5 * * * *", datetime(2026, 1, 1, 0, 50)),
                         datetime(2026, 1, 1, 0, 55))
        self.assertEqual(next_fire("50-10/5 * * * *", datetime(2026, 1, 1, 0, 55)),
                         datetime(2026, 1, 1, 1, 0))

    def test_minute_wrap_step_excludes_unstrided(self):
        # 51..54, 56..59, 1..4, 6..9 are NOT in {50,55,0,5,10}.
        self.assertEqual(next_fire("50-10/5 * * * *", datetime(2026, 1, 1, 0, 51)),
                         datetime(2026, 1, 1, 0, 55))

    def test_dow_wrap_step(self):
        # fri-tue/2 -> {fri,sun,tue} = {5,0,2}
        self.assertEqual(next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0)),
                         datetime(2026, 7, 24, 0, 0))  # Tue -> Fri
        self.assertEqual(next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 24, 0, 0)),
                         datetime(2026, 7, 26, 0, 0))  # Fri -> Sun (skips Sat)
        self.assertEqual(next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 26, 0, 0)),
                         datetime(2026, 7, 28, 0, 0))  # Sun -> Tue (skips Mon)


class TestRule6LastDay(unittest.TestCase):
    def test_last_day_january(self):
        self.assertEqual(next_fire("0 0 L * *", datetime(2026, 1, 15, 0, 0)),
                         datetime(2026, 1, 31, 0, 0))

    def test_last_day_february_non_leap(self):
        self.assertEqual(next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0)),
                         datetime(2026, 2, 28, 0, 0))

    def test_last_day_february_leap(self):
        self.assertEqual(next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0)),
                         datetime(2028, 2, 29, 0, 0))

    def test_last_day_30_day_month(self):
        self.assertEqual(next_fire("0 0 L * *", datetime(2026, 4, 1, 0, 0)),
                         datetime(2026, 4, 30, 0, 0))

    def test_L_intersects_with_dow(self):
        # Last day of the month that is also a Friday: 2026-07-31.
        self.assertEqual(next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 7, 31, 0, 0))

    def test_L_rollover_within_month(self):
        # Already past midnight of the last day -> next month's last day.
        self.assertEqual(next_fire("0 0 L * *", datetime(2026, 1, 31, 0, 0)),
                         datetime(2026, 2, 28, 0, 0))


class TestRule7MonthLengths(unittest.TestCase):
    def test_31st_skips_short_months(self):
        self.assertEqual(next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0)),
                         datetime(2026, 5, 31, 0, 0))

    def test_30th_skips_february(self):
        self.assertEqual(next_fire("0 0 30 * *", datetime(2026, 2, 1, 0, 0)),
                         datetime(2026, 3, 30, 0, 0))

    def test_feb_29_waits_for_leap_year(self):
        self.assertEqual(next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0)),
                         datetime(2028, 2, 29, 0, 0))

    def test_feb_29_within_leap_year(self):
        self.assertEqual(next_fire("0 0 29 feb *", datetime(2028, 1, 1, 0, 0)),
                         datetime(2028, 2, 29, 0, 0))

    def test_31st_only_fires_in_long_months(self):
        self.assertEqual(next_fire("0 0 31 * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 31, 0, 0))
        self.assertEqual(next_fire("0 0 31 * *", datetime(2026, 1, 31, 0, 0)),
                         datetime(2026, 3, 31, 0, 0))  # skips February


class TestWorkedExamples(unittest.TestCase):
    """The full example table from the spec, verbatim."""

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

    def test_all_worked_examples(self):
        for expr, after, expected in self.CASES:
            with self.subTest(expr=expr, after=after):
                self.assertEqual(next_fire(expr, after), expected)


class TestErrors(unittest.TestCase):
    ANY = datetime(2026, 1, 1)

    BAD = [
        "",                # empty
        "   ",             # whitespace only
        "* * * *",         # 4 fields
        "* * * * * *",     # 6 fields
        "60 * * * *",      # minute out of domain
        "* 24 * * *",      # hour out of domain
        "* * 0 * *",       # day-of-month out of domain
        "* * 32 * *",      # day-of-month out of domain (high)
        "* * * 13 *",      # month out of domain
        "* * * 0 *",       # month out of domain (low)
        "* * * * 7",       # Dev 3: dow 7 invalid
        "* * * * SUN",     # uppercase name
        "* * * FRI-MON *", # uppercase names in range, wrong field too
        "*/0 * * * *",     # step 0
        "*/-5 * * * *",    # negative step
        "*/x * * * *",     # non-numeric step
        "5/10 * * * *",    # step on a single value
        "1,,3 * * * *",    # empty list element
        ",1 * * * *",      # leading empty list element
        "1, * * * *",      # trailing empty list element
        "1x * * * *",      # unknown token
        "70-10 * * * *",   # wrap endpoint out of domain
        "50-70 * * * *",   # range end out of domain
        "1-2-3 * * * *",   # malformed range
        "5- * * * *",      # missing range end
        "-5 * * * *",      # missing range start
        "0 0 L,15 * *",    # L in a list
        "0 0 15,L * *",    # L in a list (other position)
        "0 0 L-31 * *",    # L in a range
        "0 0 L/2 * *",     # L with a step
        "0 0 * * L",       # L outside day-of-month
        "L 0 * * *",       # L in minute field
        "0 0 l * *",       # lowercase l
        "* * * * mon-SUN", # uppercase name inside a dow range
    ]

    def test_all_bad_expressions_raise_valueerror(self):
        for expr in self.BAD:
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, self.ANY)

    def test_wrap_ranges_do_not_raise(self):
        # Explicit spec note: A > B is never itself an error.
        for expr in ("30-5 * * * *", "0 0 1 nov-feb *", "0 0 * * fri-mon"):
            with self.subTest(expr=expr):
                next_fire(expr, self.ANY)

    def test_impossible_date_raises(self):
        # Feb 31 never exists; the bounded search must give up cleanly.
        with self.assertRaises(ValueError):
            next_fire("0 0 31 feb *", self.ANY)


if __name__ == "__main__":
    unittest.main()
