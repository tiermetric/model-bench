"""Standard-library unittest suite for cron.next_fire."""

import unittest
from datetime import datetime

from cron import next_fire


class TestBasics(unittest.TestCase):
    def test_every_minute_advances_one_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_strictly_after_exact_match(self):
        # `after` itself matches the expression but must not be returned
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 15)),
            datetime(2026, 1, 1, 0, 30),
        )

    def test_nonzero_seconds_round_to_next_whole_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 30)),
            datetime(2026, 1, 1, 0, 1),
        )
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 0, 999999)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_result_is_minute_aligned(self):
        result = next_fire("*/5 3 * * *", datetime(2026, 5, 4, 1, 2, 3, 456))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)
        self.assertEqual(result, datetime(2026, 5, 4, 3, 0))

    def test_whitespace_tolerance(self):
        self.assertEqual(
            next_fire("  */15   *  * *   *  ", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )

    def test_single_values_and_rollover(self):
        # hour/minute already past today -> tomorrow
        self.assertEqual(
            next_fire("30 9 * * *", datetime(2026, 7, 22, 10, 0)),
            datetime(2026, 7, 23, 9, 30),
        )

    def test_comma_lists(self):
        self.assertEqual(
            next_fire("0-10/2,30 * * * *", datetime(2026, 1, 1, 0, 10)),
            datetime(2026, 1, 1, 0, 30),
        )
        self.assertEqual(
            next_fire("0 0 * * mon,wed,fri", datetime(2026, 7, 22, 1, 0)),
            datetime(2026, 7, 24, 0, 0),  # Wed 1:00 -> Fri
        )

    def test_mixed_names_and_numbers_in_list(self):
        self.assertEqual(
            next_fire("0 0 * * 1,fri", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 24, 0, 0),  # Wed -> Fri(5), then Mon(1)
        )
        self.assertEqual(
            next_fire("0 0 * * 1,fri", datetime(2026, 7, 24, 0, 0)),
            datetime(2026, 7, 27, 0, 0),
        )

    def test_year_rollover(self):
        self.assertEqual(
            next_fire("0 0 1 1 *", datetime(2026, 1, 2, 0, 0)),
            datetime(2027, 1, 1, 0, 0),
        )


class TestDeviation1Intersection(unittest.TestCase):
    """dom AND dow when both restricted (standard cron would union)."""

    def test_friday_the_13th(self):
        self.assertEqual(
            next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 2, 13, 0, 0),
        )

    def test_first_and_monday(self):
        self.assertEqual(
            next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0)),
            datetime(2027, 2, 1, 0, 0),
        )

    def test_union_result_would_be_earlier(self):
        # Standard cron (union) would fire Mon 2026-01-05; intersection must
        # wait for a 13th that is a Friday.
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertNotEqual(result, datetime(2026, 1, 5, 0, 0))
        self.assertNotEqual(result, datetime(2026, 1, 13, 0, 0))

    def test_only_dom_restricted(self):
        self.assertEqual(
            next_fire("0 0 13 * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 13, 0, 0),
        )

    def test_only_dow_restricted(self):
        # 2026-07-22 is a Wednesday; next Monday is the 27th
        self.assertEqual(
            next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 27, 9, 30),
        )

    def test_both_unrestricted(self):
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 7, 22, 5, 0)),
            datetime(2026, 7, 23, 0, 0),
        )

    def test_stepped_dow_counts_as_restricted(self):
        # dow */2 = {sun,tue,thu,sat}; dom 14; intersection needs a 14th on
        # one of those weekdays: 2026-07-14 is a Tuesday.
        self.assertEqual(
            next_fire("0 0 14 * */2", datetime(2026, 7, 1, 0, 0)),
            datetime(2026, 7, 14, 0, 0),
        )
        # 2026-08-14 is a Friday (dow 5, not in {0,2,4,6}) -> skip to a
        # matching month: 2026-11-14 is a Saturday.
        self.assertEqual(
            next_fire("0 0 14 * */2", datetime(2026, 7, 14, 0, 0)),
            datetime(2026, 11, 14, 0, 0),
        )


class TestDeviation2StepAnchoredAtRangeStart(unittest.TestCase):
    def test_anchor_is_range_start_not_multiple(self):
        # 5-30/10 -> {5,15,25}, not {10,20,30}
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2026, 6, 1, 12, 5),
        )
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5)),
            datetime(2026, 6, 1, 12, 15),
        )
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 25)),
            datetime(2027, 6, 1, 12, 5),
        )

    def test_multiple_of_step_inside_range_does_not_match(self):
        # 10 is inside 5-30 and a multiple of 10, but not on the 5+10k grid
        self.assertEqual(
            next_fire("5-30/10 * * * *", datetime(2026, 1, 1, 0, 6)),
            datetime(2026, 1, 1, 0, 15),
        )

    def test_star_step_anchored_at_domain_floor(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )
        # month domain floor is 1: */5 on month = {1, 6, 11}
        self.assertEqual(
            next_fire("0 0 1 */5 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 6, 1, 0, 0),
        )

    def test_explicit_range_step(self):
        # 10-50/15 -> {10, 25, 40}
        self.assertEqual(
            next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 26)),
            datetime(2026, 1, 1, 0, 40),
        )
        self.assertEqual(
            next_fire("10-50/15 * * * *", datetime(2026, 1, 1, 0, 41)),
            datetime(2026, 1, 1, 1, 10),
        )

    def test_step_larger_than_range_matches_only_start(self):
        self.assertEqual(
            next_fire("5-10/20 * * * *", datetime(2026, 1, 1, 0, 6)),
            datetime(2026, 1, 1, 1, 5),
        )


class TestDeviation3StrictDomains(unittest.TestCase):
    def test_dow_seven_is_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1))

    def test_dow_seven_in_range_is_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 5-7", datetime(2026, 1, 1))

    def test_dow_zero_is_sunday(self):
        # 2026-07-26 is a Sunday
        self.assertEqual(
            next_fire("0 0 * * 0", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 26, 0, 0),
        )

    def test_domain_boundaries_accepted(self):
        self.assertEqual(
            next_fire("59 23 31 12 6", datetime(2026, 1, 1, 0, 0)),
            datetime(2033, 12, 31, 23, 59),  # first Dec 31 falling on Saturday
        )
        self.assertEqual(
            next_fire("0 0 1 1 0", datetime(2026, 1, 1, 0, 0)),
            datetime(2034, 1, 1, 0, 0),  # first Jan 1 falling on Sunday
        )


class TestRule4WrapRanges(unittest.TestCase):
    def test_minute_wrap(self):
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 30)),
            datetime(2026, 1, 1, 0, 50),
        )
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 55)),
            datetime(2026, 1, 1, 0, 56),
        )
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 59)),
            datetime(2026, 1, 1, 1, 0),
        )
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 1, 10)),
            datetime(2026, 1, 1, 1, 50),
        )

    def test_wrap_excludes_middle(self):
        # 11..49 are outside 50-10
        self.assertEqual(
            next_fire("50-10 * * * *", datetime(2026, 1, 1, 0, 11)),
            datetime(2026, 1, 1, 0, 50),
        )

    def test_month_wrap_with_names(self):
        self.assertEqual(
            next_fire("0 0 1 nov-feb *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 11, 1, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 1 nov-feb *", datetime(2026, 12, 15, 0, 0)),
            datetime(2027, 1, 1, 0, 0),
        )

    def test_dow_wrap(self):
        # fri-mon = {fri,sat,sun,mon}; 2026-07-21 is a Tuesday
        self.assertEqual(
            next_fire("0 0 * * fri-mon", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )
        # from Friday the 24th, Saturday matches too
        self.assertEqual(
            next_fire("0 0 * * fri-mon", datetime(2026, 7, 24, 0, 0)),
            datetime(2026, 7, 25, 0, 0),
        )

    def test_wrap_is_not_an_error(self):
        for expr in ("30-5 * * * *", "0 0 1 nov-feb *", "0 0 * * fri-mon"):
            next_fire(expr, datetime(2026, 1, 1))  # must not raise


class TestRule5StepThroughWrap(unittest.TestCase):
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
            next_fire("50-10/5 * * * *", datetime(2026, 1, 1, 0, 10)),
            datetime(2026, 1, 1, 0, 50),
        )
        # 60 % 5 == 0 grid values NOT in the stepped wrap: e.g. 15..45
        self.assertEqual(
            next_fire("50-10/5 * * * *", datetime(2026, 1, 1, 0, 14)),
            datetime(2026, 1, 1, 0, 50),
        )

    def test_wrap_step_counts_through_boundary(self):
        # 55-5/3 wrapped order 55,56,...,59,0,...,5 -> every 3rd: {55, 58, 1, 4}
        self.assertEqual(
            next_fire("55-5/3 * * * *", datetime(2026, 1, 1, 0, 58)),
            datetime(2026, 1, 1, 1, 1),
        )
        self.assertEqual(
            next_fire("55-5/3 * * * *", datetime(2026, 1, 1, 1, 1)),
            datetime(2026, 1, 1, 1, 4),
        )
        # 0 is NOT in {55,58,1,4} even though it's on the "multiples" grid
        self.assertEqual(
            next_fire("55-5/3 * * * *", datetime(2026, 1, 1, 0, 59)),
            datetime(2026, 1, 1, 1, 1),
        )

    def test_dow_wrap_step(self):
        # fri-tue/2 -> {fri, sun, tue}; 2026-07-21 Tue -> next is Fri 24th
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 21, 0, 0)),
            datetime(2026, 7, 24, 0, 0),
        )
        # Saturday the 25th is skipped (not on the stride), Sunday matches
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 24, 0, 0)),
            datetime(2026, 7, 26, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 * * fri-tue/2", datetime(2026, 7, 26, 0, 0)),
            datetime(2026, 7, 28, 0, 0),  # Tuesday
        )


class TestRule6LastDay(unittest.TestCase):
    def test_last_day_31(self):
        self.assertEqual(
            next_fire("0 0 L * *", datetime(2026, 1, 15, 0, 0)),
            datetime(2026, 1, 31, 0, 0),
        )

    def test_last_day_feb_non_leap(self):
        self.assertEqual(
            next_fire("0 0 L * *", datetime(2026, 2, 1, 0, 0)),
            datetime(2026, 2, 28, 0, 0),
        )

    def test_last_day_feb_leap(self):
        self.assertEqual(
            next_fire("0 0 L 2 *", datetime(2027, 3, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )

    def test_last_day_30_day_month(self):
        self.assertEqual(
            next_fire("0 0 L * *", datetime(2026, 4, 1, 0, 0)),
            datetime(2026, 4, 30, 0, 0),
        )

    def test_L_intersects_with_dow(self):
        self.assertEqual(
            next_fire("0 0 L * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 7, 31, 0, 0),
        )

    def test_L_with_unrestricted_dow_fires_every_month(self):
        self.assertEqual(
            next_fire("0 0 L * *", datetime(2026, 1, 31, 0, 0)),
            datetime(2026, 2, 28, 0, 0),
        )


class TestRule7MonthLengths(unittest.TestCase):
    def test_31_skips_short_months(self):
        self.assertEqual(
            next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0)),
            datetime(2026, 5, 31, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 31 * *", datetime(2026, 1, 31, 0, 0)),
            datetime(2026, 3, 31, 0, 0),  # February skipped entirely
        )

    def test_30_skips_february(self):
        self.assertEqual(
            next_fire("0 0 30 * *", datetime(2026, 2, 1, 0, 0)),
            datetime(2026, 3, 30, 0, 0),
        )

    def test_feb_29_only_on_leap_years(self):
        self.assertEqual(
            next_fire("0 0 29 feb *", datetime(2027, 1, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )
        self.assertEqual(
            next_fire("0 0 29 feb *", datetime(2026, 1, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )

    def test_no_clamping_to_last_day(self):
        # dom 31 in April must not clamp to April 30
        self.assertNotEqual(
            next_fire("0 0 31 * *", datetime(2026, 4, 1, 0, 0)),
            datetime(2026, 4, 30, 0, 0),
        )


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
    BAD_EXPRESSIONS = [
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
        "* * * * 7",       # dow 7 invalid (Deviation 3)
        "* * * * SUN",     # uppercase name
        "* * * JAN *",     # uppercase month name
        "*/0 * * * *",     # step <= 0
        "1-5/0 * * * *",   # step <= 0 on a range
        "*/-2 * * * *",    # negative step
        "*/x * * * *",     # non-numeric step
        "5/10 * * * *",    # step on a single value
        "mon/2 * * * *",   # step on a single name (also name in wrong field)
        "1,,3 * * * *",    # empty list element
        ",1 * * * *",      # leading empty element
        "1, * * * *",      # trailing empty element
        "1x * * * *",      # unknown token
        "70-10 * * * *",   # wrap endpoint out of domain
        "50-70 * * * *",   # range end out of domain
        "1-2-3 * * * *",   # malformed range
        "*/5/2 * * * *",   # double step
        "0 0 L,15 * *",    # L in a list
        "0 0 L-5 * *",     # L in a range
        "0 0 L/2 * *",     # L with a step
        "0 0 * * L",       # L outside day-of-month
        "0 L * * *",       # L in hour field
        "0 0 l * *",       # lowercase l
        "mon * * * *",     # dow name in minute field
        "* * * fri *",     # dow name in month field
        "* * * * jan",     # month name in dow field
    ]

    def test_all_bad_expressions_raise_value_error(self):
        for expr in self.BAD_EXPRESSIONS:
            with self.subTest(expr=repr(expr)):
                with self.assertRaises(ValueError):
                    next_fire(expr, datetime(2026, 1, 1))

    def test_wrap_ranges_do_not_raise(self):
        # Explicitly: A > B is valid (Rule 4), never the classic range error
        for expr in ("30-5 * * * *", "0 0 1 nov-feb *", "0 0 * * fri-mon"):
            with self.subTest(expr=expr):
                next_fire(expr, datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
