"""Standard-library unittest suite for cron.next_fire."""

import unittest
from datetime import datetime

from cron import next_fire


class WorkedExamplesTest(unittest.TestCase):
    """The exact worked examples from the spec."""

    def test_step_minute_domain_floor(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )

    def test_range_step_anchor_first(self):
        # Deviation 2: 5-30/10 -> 5, 15, 25 (start 5, not 10).
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2026, 6, 1, 12, 5),
        )

    def test_range_step_anchor_next(self):
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5)),
            datetime(2026, 6, 1, 12, 15),
        )

    def test_range_step_ten_twentyfive_forty(self):
        # 10-50/15 -> 10, 25, 40.
        self.assertEqual(
            next_fire("10-50/15 0 1 3 *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 3, 1, 0, 10),
        )

    def test_friday_the_thirteenth(self):
        # Deviation 1: intersection, not union.
        self.assertEqual(
            next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 2, 13, 0, 0),
        )

    def test_friday_the_thirteenth_next(self):
        self.assertEqual(
            next_fire("0 0 13 * fri", datetime(2026, 2, 14, 0, 0)),
            datetime(2026, 3, 13, 0, 0),
        )

    def test_first_and_monday(self):
        # Deviation 1: 1st AND Monday.
        self.assertEqual(
            next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0)),
            datetime(2027, 2, 1, 0, 0),
        )

    def test_next_monday_0930(self):
        self.assertEqual(
            next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 27, 9, 30),
        )

    def test_weekday_range_skips_sunday(self):
        self.assertEqual(
            next_fire("0 0 * * mon-fri", datetime(2026, 7, 25, 0, 0)),
            datetime(2026, 7, 27, 0, 0),
        )

    def test_month_list_with_names(self):
        self.assertEqual(
            next_fire("0 12 1 jan,jul *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 7, 1, 12, 0),
        )

    def test_dow_zero_is_sunday(self):
        self.assertEqual(
            next_fire("0 0 * * 0", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 26, 0, 0),
        )

    def test_lists_and_range(self):
        self.assertEqual(
            next_fire("15,45 8-10 * * *", datetime(2026, 7, 22, 9, 20)),
            datetime(2026, 7, 22, 9, 45),
        )


class Deviation1IntersectionTest(unittest.TestCase):
    """day-of-month AND day-of-week is an intersection when both restricted."""

    def test_both_restricted_is_intersection(self):
        # Would be Jan 2 (first Friday) under union cron; here Feb 13.
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 2, 13, 0, 0))
        self.assertEqual(result.day, 13)
        self.assertEqual((result.weekday() + 1) % 7, 5)  # Friday

    def test_only_dom_restricted(self):
        # dow is '*', so dom alone decides: every 13th.
        self.assertEqual(
            next_fire("0 0 13 * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 13, 0, 0),
        )

    def test_only_dow_restricted(self):
        # dom is '*', so dow alone decides: next Friday.
        self.assertEqual(
            next_fire("0 0 * * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 2, 0, 0),
        )

    def test_both_star_every_day(self):
        self.assertEqual(
            next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 30)),
            datetime(2026, 1, 2, 0, 0),
        )

    def test_step_dom_counts_as_restricted(self):
        # dom '*/2' is restricted, dow 'mon' is restricted -> intersection.
        # Odd days that are also Monday.
        result = next_fire("0 0 */2 * mon", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.day % 2, 1)
        self.assertEqual((result.weekday() + 1) % 7, 1)


class Deviation2StepAnchorTest(unittest.TestCase):
    """Steps anchor to the range start, not to 0."""

    def test_range_step_values(self):
        # 5-30/10 fires at minutes 5, 15, 25 within the hour.
        fires = []
        cur = datetime(2026, 6, 1, 12, 0)
        for _ in range(3):
            cur = next_fire("5-30/10 12 1 6 *", cur)
            fires.append(cur.minute)
        self.assertEqual(fires, [5, 15, 25])

    def test_star_step_uses_domain_floor(self):
        fires = []
        cur = datetime(2026, 1, 1, 0, 0)
        # collect the first four fires of */15 within the hour
        for _ in range(4):
            cur = next_fire("*/15 * * * *", cur)
            fires.append(cur.minute)
        self.assertEqual(fires, [15, 30, 45, 0])

    def test_ten_fifty_fifteen(self):
        fires = []
        cur = datetime(2026, 3, 1, 0, 0)
        for _ in range(3):
            cur = next_fire("10-50/15 0 1 3 *", cur)
            fires.append(cur.minute)
        self.assertEqual(fires, [10, 25, 40])


class Deviation3StrictRejectionTest(unittest.TestCase):
    """Strict domains and strict token rejection."""

    def _bad(self, expr):
        with self.assertRaises(ValueError):
            next_fire(expr, datetime(2026, 1, 1, 0, 0))

    def test_dow_seven_invalid(self):
        self._bad("* * * * 7")

    def test_uppercase_name(self):
        self._bad("* * * * SUN")
        self._bad("* * * JAN *")

    def test_lowercase_month_name_valid(self):
        # Sanity: lowercase 'jan' is accepted.
        self.assertEqual(
            next_fire("0 0 1 jan *", datetime(2026, 6, 1, 0, 0)),
            datetime(2027, 1, 1, 0, 0),
        )


class FieldCountErrorTest(unittest.TestCase):
    def _bad(self, expr):
        with self.assertRaises(ValueError):
            next_fire(expr, datetime(2026, 1, 1, 0, 0))

    def test_empty(self):
        self._bad("")

    def test_four_fields(self):
        self._bad("* * * *")

    def test_six_fields(self):
        self._bad("* * * * * *")

    def test_whitespace_only(self):
        self._bad("     ")


class DomainErrorTest(unittest.TestCase):
    def _bad(self, expr):
        with self.assertRaises(ValueError):
            next_fire(expr, datetime(2026, 1, 1, 0, 0))

    def test_minute_out_of_domain(self):
        self._bad("60 * * * *")

    def test_hour_out_of_domain(self):
        self._bad("* 24 * * *")

    def test_dom_zero(self):
        self._bad("* * 0 * *")

    def test_dom_32(self):
        self._bad("* * 32 * *")

    def test_month_13(self):
        self._bad("* * * 13 *")

    def test_month_zero(self):
        self._bad("* * * 0 *")

    def test_dow_out_of_domain(self):
        self._bad("* * * * 7")


class StepAndRangeErrorTest(unittest.TestCase):
    def _bad(self, expr):
        with self.assertRaises(ValueError):
            next_fire(expr, datetime(2026, 1, 1, 0, 0))

    def test_step_zero(self):
        self._bad("*/0 * * * *")

    def test_step_negative(self):
        self._bad("*/-1 * * * *")

    def test_range_step_zero(self):
        self._bad("1-5/0 * * * *")

    def test_range_start_gt_end(self):
        self._bad("30-5 * * * *")

    def test_named_range_start_gt_end(self):
        self._bad("* * * * fri-mon")

    def test_step_on_single_value(self):
        self._bad("5/10 * * * *")


class TokenErrorTest(unittest.TestCase):
    def _bad(self, expr):
        with self.assertRaises(ValueError):
            next_fire(expr, datetime(2026, 1, 1, 0, 0))

    def test_empty_list_element_middle(self):
        self._bad("1,,3 * * * *")

    def test_trailing_comma(self):
        self._bad("1, * * * *")

    def test_leading_comma(self):
        self._bad(",1 * * * *")

    def test_unknown_token(self):
        self._bad("1x * * * *")

    def test_dangling_dash(self):
        self._bad("mon- * * * *")

    def test_alpha_token_in_numeric_field(self):
        self._bad("abc * * * *")


class ParsingLenienceTest(unittest.TestCase):
    """Whitespace handling and mixed names/numbers."""

    def test_leading_trailing_whitespace(self):
        self.assertEqual(
            next_fire("  0 0 * * 0  ", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 26, 0, 0),
        )

    def test_multiple_spaces_between_fields(self):
        self.assertEqual(
            next_fire("0   0    *  *   0", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 26, 0, 0),
        )

    def test_tabs_as_separators(self):
        self.assertEqual(
            next_fire("0\t0\t*\t*\t0", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 26, 0, 0),
        )

    def test_mixed_names_and_numbers_in_list(self):
        # dow list mixing a number and a name: 1 (Mon) and fri (5).
        result = next_fire("0 0 * * 1,fri", datetime(2026, 7, 22, 0, 0))
        # 2026-07-22 is Wednesday; next matching is Friday 07-24.
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))


class BoundaryBehaviorTest(unittest.TestCase):
    """Strictly-after and next-whole-minute semantics."""

    def test_strictly_after_on_boundary(self):
        # after exactly matches; must return the NEXT match, not itself.
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 12, 0, 0)),
            datetime(2026, 1, 1, 12, 1),
        )

    def test_nonzero_seconds_rounds_to_next_minute(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 12, 0, 30)),
            datetime(2026, 1, 1, 12, 1),
        )

    def test_nonzero_microseconds_rounds_up(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 12, 0, 0, 1)),
            datetime(2026, 1, 1, 12, 1),
        )

    def test_result_has_zero_seconds(self):
        result = next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 7, 42))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)
        self.assertEqual(result, datetime(2026, 1, 1, 0, 15))

    def test_rollover_across_year(self):
        self.assertEqual(
            next_fire("0 0 1 1 *", datetime(2026, 12, 31, 23, 59)),
            datetime(2027, 1, 1, 0, 0),
        )


class LeapYearTest(unittest.TestCase):
    def test_feb_29_leap_year(self):
        # 2028 is a leap year.
        self.assertEqual(
            next_fire("0 0 29 2 *", datetime(2026, 1, 1, 0, 0)),
            datetime(2028, 2, 29, 0, 0),
        )


if __name__ == "__main__":
    unittest.main()
