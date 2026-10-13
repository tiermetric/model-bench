import unittest
from datetime import datetime

from cron import next_fire


class NextFireTests(unittest.TestCase):
    def assert_next(self, expr, after, expected):
        self.assertEqual(next_fire(expr, after), expected)

    def test_worked_examples_original_deviations(self):
        cases = [
            ("*/15 * * * *", datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 15)),
            ("5-30/10 12 1 6 *", datetime(2026, 6, 1), datetime(2026, 6, 1, 12, 5)),
            ("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5), datetime(2026, 6, 1, 12, 15)),
            ("0 0 13 * fri", datetime(2026, 1, 1), datetime(2026, 2, 13)),
            ("0 0 1 * mon", datetime(2026, 6, 2), datetime(2027, 2, 1)),
            ("30 9 * * mon", datetime(2026, 7, 22), datetime(2026, 7, 27, 9, 30)),
            ("0 12 1 jan,jul *", datetime(2026, 3, 1), datetime(2026, 7, 1, 12)),
        ]
        for case in cases:
            with self.subTest(expr=case[0]):
                self.assert_next(*case)

    def test_wrap_ranges_and_wrapped_steps(self):
        cases = [
            ("50-10 * * * *", datetime(2026, 1, 1, 0, 30), datetime(2026, 1, 1, 0, 50)),
            ("50-10 * * * *", datetime(2026, 1, 1, 0, 55), datetime(2026, 1, 1, 0, 56)),
            ("0 0 1 nov-feb *", datetime(2026, 3, 1), datetime(2026, 11, 1)),
            ("0 0 * * fri-mon", datetime(2026, 7, 21), datetime(2026, 7, 24)),
            ("50-10/5 0 1 1 *", datetime(2026, 1, 1), datetime(2026, 1, 1, 0, 5)),
            ("0 0 * * fri-tue/2", datetime(2026, 7, 21), datetime(2026, 7, 24)),
        ]
        for case in cases:
            with self.subTest(expr=case[0]):
                self.assert_next(*case)

    def test_last_day_and_calendar_rollover(self):
        cases = [
            ("0 0 L * *", datetime(2026, 1, 15), datetime(2026, 1, 31)),
            ("0 0 L * *", datetime(2026, 2, 1), datetime(2026, 2, 28)),
            ("0 0 L 2 *", datetime(2027, 3, 1), datetime(2028, 2, 29)),
            ("0 0 L * fri", datetime(2026, 1, 1), datetime(2026, 7, 31)),
            ("0 0 31 * *", datetime(2026, 4, 1), datetime(2026, 5, 31)),
            ("0 0 30 * *", datetime(2026, 2, 1), datetime(2026, 3, 30)),
            ("0 0 29 feb *", datetime(2027, 1, 1), datetime(2028, 2, 29)),
        ]
        for case in cases:
            with self.subTest(expr=case[0]):
                self.assert_next(*case)

    def test_strict_next_minute_and_whitespace(self):
        result = next_fire("  0 0 * * *  ", datetime(2026, 1, 1, 23, 59, 59, 1))
        self.assertEqual(result, datetime(2026, 1, 2))
        self.assertEqual(result.second, 0)
        self.assertEqual(result.microsecond, 0)

    def test_intersection_when_both_day_fields_are_restricted(self):
        self.assert_next("0 0 1 * mon", datetime(2026, 1, 1), datetime(2026, 6, 1))
        self.assert_next("0 0 * * mon", datetime(2026, 6, 2), datetime(2026, 6, 8))

    def test_mixed_names_numbers_and_range_step_anchor(self):
        self.assert_next("10-50/15 * * * *", datetime(2026, 1, 1), datetime(2026, 1, 1, 0, 10))
        self.assert_next("0 0 * 1,jul *", datetime(2026, 1, 31), datetime(2026, 7, 1))
        self.assert_next("0 0 * * 1,fri", datetime(2026, 1, 1), datetime(2026, 1, 2))

    def test_errors(self):
        expressions = [
            "", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *",
            "* * 0 * *", "* * * 13 *", "* * * * 7", "* * * * SUN",
            "*/0 * * * *", "5/10 * * * *", "1,,3 * * * *", "1x * * * *",
            "70-10 * * * *", "0 0 L,15 * *", "0 0 * * L", "0 0 l * *",
        ]
        for expr in expressions:
            with self.subTest(expr=expr):
                with self.assertRaises(ValueError):
                    next_fire(expr, datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
