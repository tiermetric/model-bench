"""Standard-library unittest suite for cron.next_fire."""

import unittest
from datetime import datetime

from cron import next_fire


class TestBasics(unittest.TestCase):
    def test_next_whole_minute_from_boundary(self):
        # `after` on an exact minute boundary: result is strictly after.
        self.assertEqual(next_fire("* * * * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 1, 0, 1))

    def test_nonzero_seconds_round_up(self):
        self.assertEqual(next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 30)),
                         datetime(2026, 1, 1, 0, 1))

    def test_nonzero_microseconds_round_up(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 5, 0, 1)),
            datetime(2026, 1, 1, 0, 6))

    def test_strictly_after_matching_instant(self):
        # `after` itself matches the expression; must return the NEXT fire.
        self.assertEqual(next_fire("0 * * * *", datetime(2026, 1, 1, 5, 0)),
                         datetime(2026, 1, 1, 6, 0))

    def test_result_has_zero_second_and_microsecond(self):
        result = next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 7, 33, 999))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)

    def test_hour_rollover(self):
        self.assertEqual(next_fire("5 * * * *", datetime(2026, 1, 1, 3, 10)),
                         datetime(2026, 1, 1, 4, 5))

    def test_day_rollover(self):
        self.assertEqual(next_fire("0 8 * * *", datetime(2026, 1, 1, 9, 0)),
                         datetime(2026, 1, 2, 8, 0))

    def test_year_rollover(self):
        self.assertEqual(next_fire("0 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2027, 1, 1, 0, 0))

    def test_whitespace_tolerated(self):
        self.assertEqual(
            next_fire("   0   12  *  *  *   ", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 12, 0))

    def test_comma_list(self):
        self.assertEqual(next_fire("10,20 * * * *", datetime(2026, 1, 1, 0, 15)),
                         datetime(2026, 1, 1, 0, 20))
        self.assertEqual(next_fire("10,20 * * * *", datetime(2026, 1, 1, 0, 20)),
                         datetime(2026, 1, 1, 1, 10))

    def test_list_mixing_names_and_numbers(self):
        # dow "1,fri" = Monday and Friday. 2026-07-22 is a Wednesday.
        self.assertEqual(
            next_fire("0 0 * * 1,fri", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 24, 0, 0))   # Friday
        self.assertEqual(
            next_fire("0 0 * * 1,fri", datetime(2026, 7, 24, 0, 0)),
            datetime(2026, 7, 27, 0, 0))   # Monday

    def test_month_names(self):
        self.assertEqual(
            next_fire("0 0 1 dec *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 12, 1, 0, 0))

    def test_list_with_range_and_step(self):
        # "0-10/2,30" -> {0,2,4,6,8,10,30}
        fires = []
        t = datetime(2026, 1, 1, 0, 59)
        for _ in range(7):
            t = next_fire("0-10/2,30 1 1 1 *", t)
            fires.append(t.minute)
        self.assertEqual(fires, [0, 2, 4, 6, 8, 10, 30])


class TestDeviation1Intersection(unittest.TestCase):
    def test_friday_the_13th(self):
        # Jan 13 2026 is a Tuesday; the first Friday-the-13th is Feb 13 2026.
        self.assertEqual(next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 2, 13, 0, 0))

    def test_first_and_monday(self):
        # Next 1st-of-month that is also a Monday after Jun 2 2026.
        self.assertEqual(next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0)),
                         datetime(2027, 2, 1, 0, 0))

    def test_union_trap_would_fire_earlier(self):
        # Standard cron (union) would fire Jan 2 2026 (a Friday); the
        # intersection must skip every non-13th Friday and every non-Friday 13th.
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertNotEqual(result, datetime(2026, 1, 2, 0, 0))
        self.assertNotEqual(result, datetime(2026, 1, 13, 0, 0))

    def test_dom_alone_when_dow_star(self):
        self.assertEqual(next_fire("0 0 13 * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 13, 0, 0))

    def test_dow_alone_when_dom_star(self):
        # First Friday after Jan 1 2026 (a Thursday) is Jan 2.
        self.assertEqual(next_fire("0 0 * * fri", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 2, 0, 0))

    def test_stepped_dom_counts_as_restricted(self):
        # dom */2 = {1,3,...,31} is restricted, so it intersects with dow.
        # After Thu Jan 1 2026: Fridays are 2,9,16,23,30; odd ones are 9, 23...
        self.assertEqual(next_fire("0 0 */2 * fri", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 9, 0, 0))

    def test_both_star_every_day(self):
        self.assertEqual(next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 2, 0, 0))


class TestDeviation2StepAnchor(unittest.TestCase):
    def test_range_step_anchored_at_start(self):
        # 5-30/10 -> 5, 15, 25 — NOT 10, 20, 30.
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2026, 6, 1, 12, 5))
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5)),
            datetime(2026, 6, 1, 12, 15))
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 15)),
            datetime(2026, 6, 1, 12, 25))
        # 25 is the last in the set; next fire is next year's Jun 1 12:05.
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 25)),
            datetime(2027, 6, 1, 12, 5))

    def test_10_50_15_yields_10_25_40(self):
        fires = []
        t = datetime(2026, 1, 1, 0, 0)
        for _ in range(3):
            t = next_fire("10-50/15 0 1 1 *", t)
            fires.append(t.minute)
        self.assertEqual(fires, [10, 25, 40])

    def test_star_step_anchors_at_domain_floor(self):
        # */15 on minute -> 0, 15, 30, 45 (floor 0).
        self.assertEqual(next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 1, 0, 15))
        self.assertEqual(next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 46)),
                         datetime(2026, 1, 1, 1, 0))

    def test_star_step_dom_anchors_at_1(self):
        # dom */15 -> {1, 16, 31}.
        self.assertEqual(next_fire("0 0 */15 * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 16, 0, 0))
        self.assertEqual(next_fire("0 0 */15 * *", datetime(2026, 1, 16, 0, 0)),
                         datetime(2026, 1, 31, 0, 0))

    def test_star_step_month_anchors_at_1(self):
        # month */5 -> {1, 6, 11}.
        self.assertEqual(next_fire("0 0 1 */5 *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 6, 1, 0, 0))
        self.assertEqual(next_fire("0 0 1 */5 *", datetime(2026, 6, 1, 0, 0)),
                         datetime(2026, 11, 1, 0, 0))

    def test_star_step_dow_anchors_at_0(self):
        # dow */3 -> {0, 3, 6} = Sun, Wed, Sat. After Wed Jul 22 2026 00:30:
        self.assertEqual(next_fire("0 0 * * */3", datetime(2026, 7, 22, 0, 30)),
                         datetime(2026, 7, 25, 0, 0))  # Saturday

    def test_step_larger_than_range(self):
        # 10-20/30 -> just {10}.
        self.assertEqual(next_fire("10-20/30 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 1, 0, 10))
        self.assertEqual(
            next_fire("10-20/30 0 1 1 *", datetime(2026, 1, 1, 0, 10)),
            datetime(2027, 1, 1, 0, 10))


class TestDeviation3StrictDomains(unittest.TestCase):
    def test_dow_7_is_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1))

    def test_dow_7_in_range_is_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 5-7", datetime(2026, 1, 1))

    def test_sunday_is_0(self):
        # Jul 26 2026 is a Sunday.
        self.assertEqual(next_fire("0 0 * * 0", datetime(2026, 7, 22, 0, 0)),
                         datetime(2026, 7, 26, 0, 0))
        self.assertEqual(next_fire("0 0 * * sun", datetime(2026, 7, 22, 0, 0)),
                         datetime(2026, 7, 26, 0, 0))


class TestRule4WrapRanges(unittest.TestCase):
    def test_minute_wrap(self):
        # 50-10 -> {50..59, 0..10}
        self.assertEqual(next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30)),
                         datetime(2026, 1, 1, 0, 50))
        self.assertEqual(next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 55)),
                         datetime(2026, 1, 1, 0, 56))
        # ...and it includes the wrapped-to low values in the next hour.
        self.assertEqual(next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 59)),
                         datetime(2026, 1, 1, 1, 0))
        self.assertEqual(next_fire("50-10 * * * *", datetime(2026, 1, 1, 1, 10)),
                         datetime(2026, 1, 1, 1, 50))

    def test_month_name_wrap(self):
        # nov-feb -> {11, 12, 1, 2}
        self.assertEqual(next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0)),
                         datetime(2026, 11, 1, 0, 0))
        self.assertEqual(
            next_fire("0 0 1 nov-feb *", datetime(2026, 12, 1, 0, 0)),
            datetime(2027, 1, 1, 0, 0))

    def test_dow_wrap(self):
        # fri-mon -> {5, 6, 0, 1}; Jul 21 2026 is a Tuesday.
        self.assertEqual(next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0)),
                         datetime(2026, 7, 24, 0, 0))
        # Saturday, Sunday, Monday all match too.
        self.assertEqual(next_fire("0 0 * * fri-mon", datetime(2026, 7, 24, 0, 0)),
                         datetime(2026, 7, 25, 0, 0))
        self.assertEqual(next_fire("0 0 * * fri-mon", datetime(2026, 7, 26, 0, 0)),
                         datetime(2026, 7, 27, 0, 0))
        # ...but Tuesday does not.
        self.assertEqual(next_fire("0 0 * * fri-mon", datetime(2026, 7, 27, 0, 0)),
                         datetime(2026, 7, 31, 0, 0))

    def test_wrap_is_not_an_error(self):
        # 30-5 is a valid wrapped minute range.
        self.assertEqual(next_fire("30-5 0 1 1 *", datetime(2026, 1, 1, 0, 6)),
                         datetime(2026, 1, 1, 0, 30))


class TestRule5WrappedSteps(unittest.TestCase):
    def test_minute_wrapped_step(self):
        # 50-10/5 -> wrapped order 50..59,0..10, every 5th -> {50,55,0,5,10}
        fires = []
        t = datetime(2026, 1, 1, 0, 0)
        for _ in range(5):
            t = next_fire("50-10/5 0 1 1 *", t)
            fires.append(t.minute)
        self.assertEqual(fires, [5, 10, 50, 55, 0])
        self.assertEqual(next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 1, 0, 5))
        # 51 must NOT match even though 51 is inside the wrapped range.
        self.assertEqual(
            next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 50)),
            datetime(2026, 1, 1, 0, 55))

    def test_dow_wrapped_step(self):
        # fri-tue/2 -> wrapped order fri,sat,sun,mon,tue -> {fri, sun, tue}
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0))   # Tue -> Fri
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 24, 0, 0)),
            datetime(2026, 7, 26, 0, 0))   # Fri -> Sun (skips Sat)
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 26, 0, 0)),
            datetime(2026, 7, 28, 0, 0))   # Sun -> Tue (skips Mon)


class TestRule6LastDay(unittest.TestCase):
    def test_last_day_31(self):
        self.assertEqual(next_fire("0 0 L * *", datetime(2026, 1, 15, 0, 0)),
                         datetime(2026, 1, 31, 0, 0))

    def test_last_day_non_leap_february(self):
        self.assertEqual(next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0)),
                         datetime(2026, 2, 28, 0, 0))

    def test_last_day_30_day_month(self):
        self.assertEqual(next_fire("0 0 L * *", datetime(2026, 4, 1, 0, 0)),
                         datetime(2026, 4, 30, 0, 0))

    def test_last_day_leap_february(self):
        self.assertEqual(next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0)),
                         datetime(2028, 2, 29, 0, 0))

    def test_L_intersects_with_dow(self):
        # The next month whose last day is a Friday: Jul 31 2026.
        self.assertEqual(next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 7, 31, 0, 0))

    def test_L_does_not_match_other_days(self):
        # Jan 30 must not match L even at the right time.
        self.assertEqual(next_fire("0 0 L * *", datetime(2026, 1, 30, 0, 0)),
                         datetime(2026, 1, 31, 0, 0))


class TestRule7MonthLengths(unittest.TestCase):
    def test_31st_skips_short_months(self):
        # After Apr 1: Apr has 30 days, so the next 31st is May 31.
        self.assertEqual(next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0)),
                         datetime(2026, 5, 31, 0, 0))

    def test_31st_skips_february(self):
        self.assertEqual(next_fire("0 0 31 * *", datetime(2026, 1, 31, 0, 0)),
                         datetime(2026, 3, 31, 0, 0))

    def test_30th_skips_february(self):
        self.assertEqual(next_fire("0 0 30 * *", datetime(2026, 2, 1, 0, 0)),
                         datetime(2026, 3, 30, 0, 0))

    def test_feb_29_only_on_leap_years(self):
        self.assertEqual(next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0)),
                         datetime(2028, 2, 29, 0, 0))
        self.assertEqual(next_fire("0 0 29 feb *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2028, 2, 29, 0, 0))
        # From within a leap year, before Feb 29:
        self.assertEqual(next_fire("0 0 29 feb *", datetime(2028, 1, 1, 0, 0)),
                         datetime(2028, 2, 29, 0, 0))
        # ...and strictly after that fire, the next one is 2032.
        self.assertEqual(next_fire("0 0 29 feb *", datetime(2028, 2, 29, 0, 0)),
                         datetime(2032, 2, 29, 0, 0))

    def test_no_clamping_to_month_end(self):
        # Day 31 in April must NOT clamp to Apr 30.
        result = next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0))
        self.assertNotEqual(result, datetime(2026, 4, 30, 0, 0))


class TestWorkedExamples(unittest.TestCase):
    """Every worked example from the spec, verbatim."""

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
        "",                 # empty / not 5 fields
        "* * * *",          # 4 fields
        "* * * * * *",      # 6 fields
        "60 * * * *",       # minute out of domain
        "* 24 * * *",       # hour out of domain
        "* * 0 * *",        # day-of-month out of domain
        "* * 32 * *",       # day-of-month out of domain (high)
        "* * * 13 *",       # month out of domain
        "* * * 0 *",        # month out of domain (low)
        "* * * * 7",        # Dev 3: dow 7 is invalid
        "* * * * SUN",      # uppercase name
        "* * * JAN *",      # uppercase month name
        "*/0 * * * *",      # step == 0
        "5/10 * * * *",     # step on a single value
        "jan/2 0 1 * *",    # step on a single named value
        "1,,3 * * * *",     # empty list element
        ",1 * * * *",       # leading empty list element
        "1x * * * *",       # unknown token
        "-5 * * * *",       # malformed range (missing start)
        "5- * * * *",       # malformed range (missing end)
        "1-2-3 * * * *",    # malformed range (double hyphen)
        "*/1/2 * * * *",    # double step
        "*/x * * * *",      # non-numeric step
        "70-10 * * * *",    # wrap endpoint out of domain
        "10-70 * * * *",    # range endpoint out of domain
        "0 0 L,15 * *",     # L may not be combined (list)
        "0 0 L-31 * *",     # L may not be combined (range)
        "0 0 L/2 * *",      # L may not be combined (step)
        "0 0 * * L",        # L only in the day-of-month field
        "0 0 l * *",        # lowercase l is not L
        "0 L * * *",        # L not valid in the hour field
    ]

    def test_all_bad_expressions_raise_valueerror(self):
        for expr in self.BAD:
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, self.ANY)

    def test_wrap_ranges_are_valid_not_errors(self):
        # Explicitly: A > B never raises (Rule 4).
        for expr in ("30-5 * * * *", "0 0 1 nov-feb *", "0 0 * * fri-mon"):
            with self.subTest(expr=expr):
                next_fire(expr, self.ANY)  # must not raise


if __name__ == "__main__":
    unittest.main()
