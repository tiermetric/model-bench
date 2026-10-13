"""Standard-library unittest suite for cron.next_fire."""

import unittest
from datetime import datetime

from cron import next_fire


class TestBasics(unittest.TestCase):
    def test_strictly_after_exact_minute(self):
        # A candidate equal to `after` must not be returned.
        self.assertEqual(next_fire("* * * * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 1, 0, 1))

    def test_nonzero_seconds_round_up_to_next_minute(self):
        self.assertEqual(next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 30)),
                         datetime(2026, 1, 1, 0, 1))

    def test_nonzero_microseconds_round_up_to_next_minute(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 14, 59, 999999)),
            datetime(2026, 1, 1, 0, 15))

    def test_matching_minute_with_trailing_microsecond_is_skipped(self):
        self.assertEqual(next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 15, 0, 1)),
                         datetime(2026, 1, 1, 0, 30))

    def test_result_has_zero_seconds_and_microseconds(self):
        result = next_fire("*/5 3 * * *", datetime(2026, 1, 1, 2, 59, 59, 123456))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)
        self.assertEqual(result, datetime(2026, 1, 1, 3, 0))

    def test_whitespace_tolerance(self):
        self.assertEqual(next_fire("   0   12    1  6   *   ",
                                   datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 6, 1, 12, 0))

    def test_hour_rollover_and_day_rollover(self):
        self.assertEqual(next_fire("15 10 * * *", datetime(2026, 1, 1, 10, 15)),
                         datetime(2026, 1, 2, 10, 15))

    def test_year_rollover(self):
        self.assertEqual(next_fire("0 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2027, 1, 1, 0, 0))

    def test_comma_list_of_values(self):
        self.assertEqual(next_fire("1,3,5 * * * *", datetime(2026, 1, 1, 0, 3)),
                         datetime(2026, 1, 1, 0, 5))

    def test_mixed_list_range_step_and_value(self):
        # 0-10/2,30 -> {0,2,4,6,8,10,30}
        self.assertEqual(next_fire("0-10/2,30 * * * *", datetime(2026, 1, 1, 0, 10)),
                         datetime(2026, 1, 1, 0, 30))
        self.assertEqual(next_fire("0-10/2,30 * * * *", datetime(2026, 1, 1, 0, 30)),
                         datetime(2026, 1, 1, 1, 0))


class TestNames(unittest.TestCase):
    def test_month_names(self):
        self.assertEqual(next_fire("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0)),
                         datetime(2026, 7, 1, 12, 0))

    def test_dow_names(self):
        self.assertEqual(next_fire("0 9 * * mon,wed,fri", datetime(2026, 7, 21, 10, 0)),
                         datetime(2026, 7, 22, 9, 0))  # Tue 10:00 -> Wed 09:00

    def test_names_and_numbers_mixed_in_one_list(self):
        # dow "1,fri" = {Mon, Fri}; from Tue 2026-07-21 the next is Fri Jul 24.
        self.assertEqual(next_fire("0 0 * * 1,fri", datetime(2026, 7, 21, 0, 0)),
                         datetime(2026, 7, 24, 0, 0))

    def test_name_range_in_month(self):
        # mar-may = {3,4,5}
        self.assertEqual(next_fire("0 0 1 mar-may *", datetime(2026, 6, 1, 0, 0)),
                         datetime(2027, 3, 1, 0, 0))


class TestDeviation1Intersection(unittest.TestCase):
    def test_friday_the_13th(self):
        self.assertEqual(next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 2, 13, 0, 0))

    def test_first_and_monday(self):
        self.assertEqual(next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0)),
                         datetime(2027, 2, 1, 0, 0))

    def test_only_dom_restricted(self):
        self.assertEqual(next_fire("0 0 13 * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 13, 0, 0))

    def test_only_dow_restricted(self):
        self.assertEqual(next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0)),
                         datetime(2026, 7, 27, 9, 30))

    def test_both_unrestricted(self):
        self.assertEqual(next_fire("0 0 * * *", datetime(2026, 1, 1, 5, 0)),
                         datetime(2026, 1, 2, 0, 0))

    def test_stepped_star_counts_as_restricted(self):
        # dom */2 = {1,3,...,31} and dow mon intersect: need an odd day that is
        # a Monday. From Wed 2026-07-22: Mon Jul 27 is odd -> matches.
        self.assertEqual(next_fire("0 0 */2 * mon", datetime(2026, 7, 22, 0, 0)),
                         datetime(2026, 7, 27, 0, 0))
        # From Tue Jul 28: Mon Aug 3 is odd -> matches (Aug 10 not needed).
        self.assertEqual(next_fire("0 0 */2 * mon", datetime(2026, 7, 28, 0, 0)),
                         datetime(2026, 8, 3, 0, 0))


class TestDeviation2StepAnchor(unittest.TestCase):
    def test_worked_examples(self):
        self.assertEqual(next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 1, 0, 15))
        self.assertEqual(next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
                         datetime(2026, 6, 1, 12, 5))
        self.assertEqual(next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5)),
                         datetime(2026, 6, 1, 12, 15))

    def test_range_step_anchored_at_start_not_multiples(self):
        # 10-50/15 -> {10, 25, 40}, never 15/30/45.
        base = datetime(2026, 1, 1, 0, 0)
        self.assertEqual(next_fire("10-50/15 * * * *", base),
                         datetime(2026, 1, 1, 0, 10))
        self.assertEqual(next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 10)),
                         datetime(2026, 1, 1, 0, 25))
        self.assertEqual(next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 25)),
                         datetime(2026, 1, 1, 0, 40))
        self.assertEqual(next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 40)),
                         datetime(2026, 1, 1, 1, 10))

    def test_5_30_10_full_set(self):
        # 5-30/10 -> {5,15,25}: after 25 the next is 5 of the following hour.
        self.assertEqual(next_fire("5-30/10 * * * *", datetime(2026, 1, 1, 0, 25)),
                         datetime(2026, 1, 1, 1, 5))

    def test_star_step_anchored_at_domain_floor(self):
        # dom */5 anchors at 1 -> {1,6,11,16,21,26,31}
        self.assertEqual(next_fire("0 0 */5 * *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 6, 0, 0))
        # month */5 anchors at 1 -> {1,6,11} = jan,jun,nov
        self.assertEqual(next_fire("0 0 1 */5 *", datetime(2026, 2, 1, 0, 0)),
                         datetime(2026, 6, 1, 0, 0))


class TestDeviation3StrictDomains(unittest.TestCase):
    def test_dow_7_is_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1))

    def test_dow_7_invalid_even_inside_range(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 5-7", datetime(2026, 1, 1))

    def test_sunday_is_0(self):
        # 2026-07-26 is a Sunday.
        self.assertEqual(next_fire("0 0 * * 0", datetime(2026, 7, 21, 0, 0)),
                         datetime(2026, 7, 26, 0, 0))

    def test_saturday_is_6(self):
        self.assertEqual(next_fire("0 0 * * 6", datetime(2026, 7, 21, 0, 0)),
                         datetime(2026, 7, 25, 0, 0))


class TestRule4WrapRanges(unittest.TestCase):
    def test_minute_wrap(self):
        self.assertEqual(next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30)),
                         datetime(2026, 1, 1, 0, 50))
        self.assertEqual(next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 55)),
                         datetime(2026, 1, 1, 0, 56))

    def test_minute_wrap_crosses_hour(self):
        # {50..59, 0..10}: after xx:59 comes (xx+1):00.
        self.assertEqual(next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 59)),
                         datetime(2026, 1, 1, 1, 0))
        # After xx:10 the set is exhausted until xx:50.
        self.assertEqual(next_fire("50-10 * * * *", datetime(2026, 1, 1, 1, 10)),
                         datetime(2026, 1, 1, 1, 50))

    def test_month_wrap_with_names(self):
        self.assertEqual(next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0)),
                         datetime(2026, 11, 1, 0, 0))
        # Inside the wrapped part: from December, January matches.
        self.assertEqual(next_fire("0 0 1 nov-feb *", datetime(2026, 12, 1, 0, 0)),
                         datetime(2027, 1, 1, 0, 0))

    def test_dow_wrap(self):
        # fri-mon = {fri,sat,sun,mon}; Tue 2026-07-21 -> Fri Jul 24.
        self.assertEqual(next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0)),
                         datetime(2026, 7, 24, 0, 0))

    def test_wrap_is_not_an_error(self):
        # A > B never raises (Rule 4 supersedes the usual start>end error).
        next_fire("30-5 * * * *", datetime(2026, 1, 1))
        next_fire("0 0 1 nov-feb *", datetime(2026, 1, 1))
        next_fire("0 0 * * fri-mon", datetime(2026, 1, 1))


class TestRule5SteppedWrapRanges(unittest.TestCase):
    def test_minute_wrap_step(self):
        # 50-10/5 -> wrapped order 50..59,0..10, every 5th -> {50,55,0,5,10}
        self.assertEqual(next_fire("50-10/5 0 1 1 *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 1, 1, 0, 5))
        self.assertEqual(next_fire("50-10/5 * * * *", datetime(2026, 1, 1, 0, 10)),
                         datetime(2026, 1, 1, 0, 50))
        self.assertEqual(next_fire("50-10/5 * * * *", datetime(2026, 1, 1, 0, 55)),
                         datetime(2026, 1, 1, 1, 0))
        # 60 (i.e. 0 of next hour) is NOT in the set by multiples-of-5 logic;
        # verify 51..54 are excluded too.
        self.assertEqual(next_fire("50-10/5 * * * *", datetime(2026, 1, 1, 0, 50)),
                         datetime(2026, 1, 1, 0, 55))

    def test_dow_wrap_step(self):
        # fri-tue/2 -> wrapped order fri,sat,sun,mon,tue, every 2nd ->
        # {fri,sun,tue} = {5,0,2}. Tue 2026-07-21 00:00 -> Fri Jul 24.
        self.assertEqual(next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0)),
                         datetime(2026, 7, 24, 0, 0))
        # Sat is excluded: from Fri Jul 24 the next is Sun Jul 26.
        self.assertEqual(next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 24, 0, 0)),
                         datetime(2026, 7, 26, 0, 0))


class TestRule6LastDay(unittest.TestCase):
    def test_last_day_of_january(self):
        self.assertEqual(next_fire("0 0 L * *", datetime(2026, 1, 15, 0, 0)),
                         datetime(2026, 1, 31, 0, 0))

    def test_last_day_of_non_leap_february(self):
        self.assertEqual(next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0)),
                         datetime(2026, 2, 28, 0, 0))

    def test_last_day_of_leap_february(self):
        self.assertEqual(next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0)),
                         datetime(2028, 2, 29, 0, 0))

    def test_last_day_of_30_day_month(self):
        self.assertEqual(next_fire("0 0 L * *", datetime(2026, 4, 1, 0, 0)),
                         datetime(2026, 4, 30, 0, 0))

    def test_L_intersects_with_dow(self):
        # Last day of month that is a Friday: first in 2026 is Jul 31.
        self.assertEqual(next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0)),
                         datetime(2026, 7, 31, 0, 0))

    def test_L_same_day_later_time(self):
        self.assertEqual(next_fire("30 18 L * *", datetime(2026, 1, 31, 12, 0)),
                         datetime(2026, 1, 31, 18, 30))


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
        self.assertEqual(next_fire("0 0 29 feb *", datetime(2026, 1, 1, 0, 0)),
                         datetime(2028, 2, 29, 0, 0))

    def test_no_clamping_to_month_end(self):
        # Day 31 in June must NOT clamp to June 30.
        self.assertEqual(next_fire("0 0 31 * *", datetime(2026, 6, 1, 0, 0)),
                         datetime(2026, 7, 31, 0, 0))


class TestErrors(unittest.TestCase):
    def assertInvalid(self, expr):
        with self.assertRaises(ValueError, msg=f"expected ValueError for {expr!r}"):
            next_fire(expr, datetime(2026, 1, 1))

    def test_wrong_field_counts(self):
        self.assertInvalid("")
        self.assertInvalid("   ")
        self.assertInvalid("* * * *")
        self.assertInvalid("* * * * * *")

    def test_out_of_domain_values(self):
        self.assertInvalid("60 * * * *")
        self.assertInvalid("* 24 * * *")
        self.assertInvalid("* * 0 * *")
        self.assertInvalid("* * 32 * *")
        self.assertInvalid("* * * 0 *")
        self.assertInvalid("* * * 13 *")
        self.assertInvalid("* * * * 7")

    def test_uppercase_names_rejected(self):
        self.assertInvalid("* * * * SUN")
        self.assertInvalid("* * * JAN *")
        self.assertInvalid("* * * * Mon")

    def test_bad_steps(self):
        self.assertInvalid("*/0 * * * *")
        self.assertInvalid("5/10 * * * *")   # step on a single value
        self.assertInvalid("*/x * * * *")
        self.assertInvalid("*/-2 * * * *")
        self.assertInvalid("*/2/3 * * * *")

    def test_bad_lists_and_ranges(self):
        self.assertInvalid("1,,3 * * * *")
        self.assertInvalid(",1 * * * *")
        self.assertInvalid("1, * * * *")
        self.assertInvalid("1-2-3 * * * *")
        self.assertInvalid("-5 * * * *")
        self.assertInvalid("5- * * * *")

    def test_unknown_tokens(self):
        self.assertInvalid("1x * * * *")
        self.assertInvalid("* * * * fri day")  # 6 fields / garbage
        self.assertInvalid("1.5 * * * *")

    def test_wrap_endpoints_must_be_in_domain(self):
        self.assertInvalid("70-10 * * * *")
        self.assertInvalid("50-70 * * * *")
        self.assertInvalid("* * * * fri-7")

    def test_L_restrictions(self):
        self.assertInvalid("0 0 L,15 * *")   # L in a list
        self.assertInvalid("0 0 1-L * *")    # L in a range
        self.assertInvalid("0 0 L/2 * *")    # L with a step
        self.assertInvalid("0 0 * * L")      # L outside day-of-month
        self.assertInvalid("0 0 * L *")      # L in month field
        self.assertInvalid("L * * * *")      # L in minute field
        self.assertInvalid("0 0 l * *")      # lowercase l


class TestWorkedExamplesVerbatim(unittest.TestCase):
    """Every worked example from the spec, exactly as written."""

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

    def test_all(self):
        for expr, after, expected in self.CASES:
            with self.subTest(expr=expr, after=after):
                self.assertEqual(next_fire(expr, after), expected)


if __name__ == "__main__":
    unittest.main()
