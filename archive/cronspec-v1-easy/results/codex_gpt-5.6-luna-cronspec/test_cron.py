import unittest
from datetime import datetime

from cron import next_fire


class TestCronExamples(unittest.TestCase):
    def test_worked_examples(self):
        cases = [
            ("*/15 * * * *", datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 15)),
            ("5-30/10 12 1 6 *", datetime(2026, 6, 1), datetime(2026, 6, 1, 12, 5)),
            ("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5), datetime(2026, 6, 1, 12, 15)),
            ("10-50/15 0 1 3 *", datetime(2026, 3, 1), datetime(2026, 3, 1, 0, 10)),
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
            with self.subTest(expr=expr):
                self.assertEqual(next_fire(expr, after), expected)

    def test_strict_after_with_seconds(self):
        self.assertEqual(next_fire("0 * * * *", datetime(2026, 1, 1, 0, 0, 1)), datetime(2026, 1, 1, 1))

    def test_intersection_and_restricted_steps(self):
        self.assertEqual(next_fire("0 0 */2 * *", datetime(2026, 1, 1)), datetime(2026, 1, 3))
        self.assertEqual(next_fire("0 0 * * */2", datetime(2026, 1, 2)), datetime(2026, 1, 3))
        self.assertEqual(next_fire("0 0 1 * mon", datetime(2026, 6, 2)), datetime(2027, 2, 1))


class TestCronErrors(unittest.TestCase):
    def assert_invalid(self, expr):
        with self.assertRaises(ValueError):
            next_fire(expr, datetime(2026, 1, 1))

    def test_error_cases(self):
        for expr in (
            "", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *",
            "* * 0 * *", "* * * 13 *", "* * * * 7", "* * * * SUN",
            "* * * JAN *", "*/0 * * * *", "30-5 * * * *", "5/10 * * * *",
            "1,,3 * * * *", "1, * * * *", "1x * * * *", "fri-mon * * * *",
        ):
            with self.subTest(expr=expr):
                self.assert_invalid(expr)

    def test_lowercase_month_name_is_valid(self):
        self.assertEqual(next_fire("0 0 * jan *", datetime(2025, 1, 1)), datetime(2025, 1, 2))


if __name__ == "__main__":
    unittest.main()
