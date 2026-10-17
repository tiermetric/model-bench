import unittest
from datetime import datetime

from cron import next_fire


class NextFireExamplesTests(unittest.TestCase):
    def test_worked_examples(self):
        cases = [
            ("*/15 * * * *", datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 15)),
            ("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0), datetime(2026, 6, 1, 12, 5)),
            ("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5), datetime(2026, 6, 1, 12, 15)),
            ("10-50/15 0 1 3 *", datetime(2026, 3, 1, 0, 0), datetime(2026, 3, 1, 0, 10)),
            ("0 0 13 * fri", datetime(2026, 1, 1), datetime(2026, 2, 13)),
            ("0 0 13 * fri", datetime(2026, 2, 14), datetime(2026, 3, 13)),
            ("0 0 1 * mon", datetime(2026, 6, 2), datetime(2027, 2, 1)),
            ("30 9 * * mon", datetime(2026, 7, 22), datetime(2026, 7, 27, 9, 30)),
            ("0 0 * * mon-fri", datetime(2026, 7, 25), datetime(2026, 7, 27)),
            ("0 12 1 jan,jul *", datetime(2026, 3, 1), datetime(2026, 7, 1, 12)),
            ("0 0 * * 0", datetime(2026, 7, 22), datetime(2026, 7, 26)),
            ("15,45 8-10 * * *", datetime(2026, 7, 22, 9, 20), datetime(2026, 7, 22, 9, 45)),
        ]
        for expr, after, expected in cases:
            with self.subTest(expr=expr, after=after):
                self.assertEqual(next_fire(expr, after), expected)

    def test_after_with_seconds_starts_at_following_minute(self):
        self.assertEqual(next_fire("* * * * *", datetime(2026, 1, 1, 8, 0, 1, 9)),
                         datetime(2026, 1, 1, 8, 1))

    def test_result_is_on_minute_boundary(self):
        result = next_fire("* * * * *", datetime(2026, 1, 1, 8, 0))
        self.assertEqual((result.second, result.microsecond), (0, 0))


class SyntaxAndDeviationTests(unittest.TestCase):
    def test_explicit_range_step_is_anchored_at_range_start(self):
        self.assertEqual(next_fire("5-30/10 * * * *", datetime(2026, 1, 1)),
                         datetime(2026, 1, 1, 0, 5))

    def test_wildcard_step_is_anchored_at_domain_floor(self):
        self.assertEqual(next_fire("*/15 * * * *", datetime(2026, 1, 1, 0, 46)),
                         datetime(2026, 1, 1, 1, 0))

    def test_day_fields_intersect_when_both_restricted(self):
        self.assertEqual(next_fire("0 0 13 * fri", datetime(2026, 1, 2)),
                         datetime(2026, 2, 13))

    def test_only_the_restricted_day_field_decides(self):
        self.assertEqual(next_fire("0 0 13 * *", datetime(2026, 2, 14)),
                         datetime(2026, 3, 13))
        self.assertEqual(next_fire("0 0 * * mon", datetime(2026, 7, 21)),
                         datetime(2026, 7, 27))

    def test_non_bare_wildcard_counts_as_restricted(self):
        # */2 is restricted, so this requires both an odd day and Friday.
        self.assertEqual(next_fire("0 0 */2 * fri", datetime(2026, 1, 1)),
                         datetime(2026, 1, 9))

    def test_names_and_numbers_can_be_mixed_in_a_list(self):
        self.assertEqual(next_fire("0 0 * 1,jul *", datetime(2026, 2, 1)),
                         datetime(2026, 7, 1))
        self.assertEqual(next_fire("0 0 * * 1,fri", datetime(2026, 7, 21)),
                         datetime(2026, 7, 24))


class InvalidExpressionTests(unittest.TestCase):
    def test_specified_error_cases(self):
        expressions = [
            "", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *",
            "* * 0 * *", "* * * 13 *", "* * * * 7", "* * * * SUN",
            "*/0 * * * *", "30-5 * * * *", "5/10 * * * *",
            "1,,3 * * * *", "1x * * * *",
        ]
        for expr in expressions:
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, datetime(2026, 1, 1))

    def test_uppercase_month_and_unknown_tokens_are_rejected(self):
        for expr in ("* * * JAN *", "* * * * mon-", "* * * * abc", "* * * * 1,"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, datetime(2026, 1, 1))

    def test_lowercase_month_name_is_valid(self):
        self.assertEqual(next_fire("0 0 * jan *", datetime(2026, 2, 1)),
                         datetime(2027, 1, 1))

    def test_negative_and_malformed_steps_are_rejected(self):
        for expr in ("*/-1 * * * *", "1-5/0 * * * *", "1-5/x * * * *", "*/1/2 * * * *"):
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
