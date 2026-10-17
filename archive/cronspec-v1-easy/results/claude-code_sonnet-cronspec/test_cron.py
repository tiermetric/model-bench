import unittest
from datetime import datetime

from cron import next_fire


class WorkedExamplesTest(unittest.TestCase):
    def test_step_from_domain_floor(self):
        self.assertEqual(
            next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 15),
        )

    def test_step_anchored_to_range_start_first(self):
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0)),
            datetime(2026, 6, 1, 12, 5),
        )

    def test_step_anchored_to_range_start_next(self):
        self.assertEqual(
            next_fire("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5)),
            datetime(2026, 6, 1, 12, 15),
        )

    def test_step_anchored_range_10_50_15(self):
        self.assertEqual(
            next_fire("10-50/15 0 1 3 *", datetime(2026, 3, 1, 0, 0)),
            datetime(2026, 3, 1, 0, 10),
        )

    def test_intersection_friday_the_13th(self):
        self.assertEqual(
            next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 2, 13, 0, 0),
        )

    def test_intersection_friday_the_13th_next(self):
        self.assertEqual(
            next_fire("0 0 13 * fri", datetime(2026, 2, 14, 0, 0)),
            datetime(2026, 3, 13, 0, 0),
        )

    def test_intersection_first_and_monday(self):
        self.assertEqual(
            next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0)),
            datetime(2027, 2, 1, 0, 0),
        )

    def test_next_monday(self):
        self.assertEqual(
            next_fire("30 9 * * mon", datetime(2026, 7, 22, 0, 0)),
            datetime(2026, 7, 27, 9, 30),
        )

    def test_weekday_range_skips_weekend(self):
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

    def test_minute_list_and_hour_range(self):
        self.assertEqual(
            next_fire("15,45 8-10 * * *", datetime(2026, 7, 22, 9, 20)),
            datetime(2026, 7, 22, 9, 45),
        )


class ErrorCasesTest(unittest.TestCase):
    def test_empty_expression(self):
        with self.assertRaises(ValueError):
            next_fire("", datetime(2026, 1, 1))

    def test_four_fields(self):
        with self.assertRaises(ValueError):
            next_fire("* * * *", datetime(2026, 1, 1))

    def test_six_fields(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * * *", datetime(2026, 1, 1))

    def test_minute_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("60 * * * *", datetime(2026, 1, 1))

    def test_hour_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("* 24 * * *", datetime(2026, 1, 1))

    def test_dom_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("* * 0 * *", datetime(2026, 1, 1))

    def test_month_out_of_domain(self):
        with self.assertRaises(ValueError):
            next_fire("* * * 13 *", datetime(2026, 1, 1))

    def test_dow_seven_is_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * 7", datetime(2026, 1, 1))

    def test_uppercase_name_rejected(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * SUN", datetime(2026, 1, 1))

    def test_lowercase_month_name_is_valid_not_an_error(self):
        # NOTE: this is deliberately NOT an error case -- lowercase 'jan' in
        # the month field is valid; only uppercase names are rejected.
        result = next_fire("* * * jan *", datetime(2026, 3, 1, 0, 0))
        self.assertEqual(result, datetime(2027, 1, 1, 0, 0))

    def test_step_zero_rejected(self):
        with self.assertRaises(ValueError):
            next_fire("*/0 * * * *", datetime(2026, 1, 1))

    def test_step_negative_rejected(self):
        with self.assertRaises(ValueError):
            next_fire("*/-1 * * * *", datetime(2026, 1, 1))

    def test_range_start_greater_than_end(self):
        with self.assertRaises(ValueError):
            next_fire("30-5 * * * *", datetime(2026, 1, 1))

    def test_step_on_single_value_rejected(self):
        with self.assertRaises(ValueError):
            next_fire("5/10 * * * *", datetime(2026, 1, 1))

    def test_empty_list_element_middle(self):
        with self.assertRaises(ValueError):
            next_fire("1,,3 * * * *", datetime(2026, 1, 1))

    def test_empty_list_element_trailing(self):
        with self.assertRaises(ValueError):
            next_fire("1, * * * *", datetime(2026, 1, 1))

    def test_unknown_token(self):
        with self.assertRaises(ValueError):
            next_fire("1x * * * *", datetime(2026, 1, 1))

    def test_unknown_alpha_token(self):
        with self.assertRaises(ValueError):
            next_fire("* * * abc *", datetime(2026, 1, 1))

    def test_malformed_range_dangling_dash(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * mon-", datetime(2026, 1, 1))

    def test_range_with_invalid_name(self):
        with self.assertRaises(ValueError):
            next_fire("* * * * fri-mon", datetime(2026, 1, 1))

    def test_step_zero_on_range_rejected(self):
        with self.assertRaises(ValueError):
            next_fire("1-5/0 * * * *", datetime(2026, 1, 1))


class Deviation1IntersectionTest(unittest.TestCase):
    def test_both_wildcard_matches_every_day(self):
        result = next_fire("0 0 * * *", datetime(2026, 7, 22, 12, 0))
        self.assertEqual(result, datetime(2026, 7, 23, 0, 0))

    def test_dom_only_restricted(self):
        # day-of-week is '*' -> only day-of-month decides.
        result = next_fire("0 0 15 * *", datetime(2026, 7, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 15, 0, 0))

    def test_dow_only_restricted(self):
        # day-of-month is '*' -> only day-of-week decides.
        result = next_fire("0 0 * * fri", datetime(2026, 7, 22, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 24, 0, 0))

    def test_both_restricted_is_intersection_not_union(self):
        # 13th AND Friday, not 13th OR Friday.
        result = next_fire("0 0 13 * fri", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.day, 13)
        self.assertEqual((result.weekday() + 1) % 7, 5)  # cron dow 5 = Friday

    def test_intersection_never_matching_within_step_is_still_dom_and_dow(self):
        # 1st AND Monday - verify the returned date really is both.
        result = next_fire("0 0 1 * mon", datetime(2026, 6, 2, 0, 0))
        self.assertEqual(result.day, 1)
        self.assertEqual((result.weekday() + 1) % 7, 1)  # cron dow 1 = Monday


class Deviation2StepAnchorTest(unittest.TestCase):
    def test_range_step_anchored_at_range_start(self):
        # 5-30/10 -> 5, 15, 25 (NOT 10, 20, 30)
        result = next_fire("5-30/10 0 * 1 *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.minute, 5)

        result2 = next_fire("5-30/10 0 * 1 *", datetime(2026, 1, 1, 0, 5))
        self.assertEqual(result2.minute, 15)

        result3 = next_fire("5-30/10 0 * 1 *", datetime(2026, 1, 1, 0, 15))
        self.assertEqual(result3.minute, 25)

        result4 = next_fire("5-30/10 0 * 1 *", datetime(2026, 1, 1, 0, 25))
        self.assertEqual(result4.hour, 0)
        self.assertEqual(result4.day, 2)
        self.assertEqual(result4.minute, 5)

    def test_star_step_anchored_at_domain_floor(self):
        result = next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result.minute, 15)

    def test_10_50_15_yields_10_25_40(self):
        vals = []
        after = datetime(2026, 3, 1, 0, 0)
        for _ in range(3):
            r = next_fire("10-50/15 0 1 3 *", after)
            vals.append(r.minute)
            after = r
        self.assertEqual(vals, [10, 25, 40])


class Deviation3StrictDomainTest(unittest.TestCase):
    def test_dow_boundaries_valid(self):
        # 0 and 6 are valid dow values.
        r0 = next_fire("0 0 * * 0", datetime(2026, 7, 22, 0, 0))
        self.assertEqual((r0.weekday() + 1) % 7, 0)
        r6 = next_fire("0 0 * * 6", datetime(2026, 7, 22, 0, 0))
        self.assertEqual((r6.weekday() + 1) % 7, 6)

    def test_dow_seven_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 * * 7", datetime(2026, 7, 22, 0, 0))

    def test_minute_boundaries_valid(self):
        next_fire("0 * * * *", datetime(2026, 1, 1, 0, 0))
        next_fire("59 * * * *", datetime(2026, 1, 1, 0, 0))

    def test_hour_boundaries_valid(self):
        next_fire("0 0 * * *", datetime(2026, 1, 1, 0, 0))
        next_fire("0 23 * * *", datetime(2026, 1, 1, 0, 0))

    def test_dom_boundaries_valid(self):
        next_fire("0 0 1 * *", datetime(2026, 1, 1, 0, 0))
        next_fire("0 0 31 * *", datetime(2026, 1, 1, 0, 0))

    def test_month_boundaries_valid(self):
        next_fire("0 0 1 1 *", datetime(2026, 1, 1, 0, 0))
        next_fire("0 0 1 12 *", datetime(2026, 1, 1, 0, 0))

    def test_dom_zero_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 0 * *", datetime(2026, 1, 1))

    def test_dom_32_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 32 * *", datetime(2026, 1, 1))

    def test_month_zero_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 * 0 *", datetime(2026, 1, 1))

    def test_month_13_invalid(self):
        with self.assertRaises(ValueError):
            next_fire("0 0 * 13 *", datetime(2026, 1, 1))


class MiscGrammarTest(unittest.TestCase):
    def test_whitespace_is_collapsed_and_trimmed(self):
        result = next_fire("  0    0   1   1   *  ", datetime(2026, 1, 1, 0, 0))
        self.assertEqual(result, datetime(2027, 1, 1, 0, 0))

    def test_after_with_nonzero_seconds_and_microseconds(self):
        result = next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 30, 123456))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 1))

    def test_result_always_on_minute_boundary(self):
        result = next_fire("*/5 * * * *", datetime(2026, 1, 1, 0, 0, 59, 999999))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)

    def test_mixed_names_and_numbers_in_dow_list(self):
        vals = next_fire("0 0 * * 1,fri", datetime(2026, 7, 22, 0, 0))
        # cron dow: mon=1, fri=5 -- next matching day after 2026-07-22 (Wed)
        # is Friday 2026-07-24.
        self.assertEqual(vals, datetime(2026, 7, 24, 0, 0))

    def test_mixed_names_and_numbers_in_month_list(self):
        result = next_fire("0 0 1 1,jul *", datetime(2026, 2, 1, 0, 0))
        self.assertEqual(result, datetime(2026, 7, 1, 0, 0))

    def test_comma_list_of_single_values(self):
        result = next_fire("0,30 * * * *", datetime(2026, 1, 1, 0, 10))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 30))

    def test_plain_range_without_step(self):
        result = next_fire("0 8-10 * * *", datetime(2026, 1, 1, 7, 0))
        self.assertEqual(result.hour, 8)

    def test_strictly_after_not_equal(self):
        # An exact match at `after` itself must not be returned.
        result = next_fire("0 0 * * *", datetime(2026, 1, 2, 0, 0))
        self.assertEqual(result, datetime(2026, 1, 3, 0, 0))


if __name__ == "__main__":
    unittest.main()
