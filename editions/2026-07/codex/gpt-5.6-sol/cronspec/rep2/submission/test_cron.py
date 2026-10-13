import unittest
from datetime import datetime

from cron import next_fire


class CronTests(unittest.TestCase):
    def check(self, expr, after, expected):
        self.assertEqual(next_fire(expr, after), expected)

    def test_original_deviations_and_basics(self):
        cases = [
            ("*/15 * * * *", (2026, 1, 1, 0, 0), (2026, 1, 1, 0, 15)),
            ("5-30/10 12 1 6 *", (2026, 6, 1, 0, 0), (2026, 6, 1, 12, 5)),
            ("5-30/10 12 1 6 *", (2026, 6, 1, 12, 5), (2026, 6, 1, 12, 15)),
            ("0 0 13 * fri", (2026, 1, 1, 0, 0), (2026, 2, 13, 0, 0)),
            ("0 0 1 * mon", (2026, 6, 2, 0, 0), (2027, 2, 1, 0, 0)),
            ("30 9 * * mon", (2026, 7, 22, 0, 0), (2026, 7, 27, 9, 30)),
            ("0 12 1 jan,jul *", (2026, 3, 1, 0, 0), (2026, 7, 1, 12, 0)),
        ]
        for expr, after, expected in cases:
            with self.subTest(expr=expr, after=after):
                self.check(expr, datetime(*after), datetime(*expected))

    def test_wrap_around_ranges(self):
        cases = [
            ("50-10 * * * *", (2026, 1, 1, 0, 30), (2026, 1, 1, 0, 50)),
            ("50-10 * * * *", (2026, 1, 1, 0, 55), (2026, 1, 1, 0, 56)),
            ("0 0 1 nov-feb *", (2026, 3, 1, 0, 0), (2026, 11, 1, 0, 0)),
            ("0 0 * * fri-mon", (2026, 7, 21, 0, 0), (2026, 7, 24, 0, 0)),
        ]
        for expr, after, expected in cases:
            with self.subTest(expr=expr, after=after):
                self.check(expr, datetime(*after), datetime(*expected))

    def test_wrapped_steps(self):
        self.check("50-10/5 0 1 1 *", datetime(2026, 1, 1), datetime(2026, 1, 1, 0, 5))
        self.check("0 0 * * fri-tue/2", datetime(2026, 7, 21), datetime(2026, 7, 24))

    def test_last_day(self):
        cases = [
            ("0 0 L * *", (2026, 1, 15), (2026, 1, 31)),
            ("0 0 L * *", (2026, 2, 1), (2026, 2, 28)),
            ("0 0 L 2 *", (2027, 3, 1), (2028, 2, 29)),
            ("0 0 L * fri", (2026, 1, 1), (2026, 7, 31)),
        ]
        for expr, after, expected in cases:
            with self.subTest(expr=expr):
                self.check(expr, datetime(*after), datetime(*expected))

    def test_calendar_rollover(self):
        cases = [
            ("0 0 31 * *", (2026, 4, 1), (2026, 5, 31)),
            ("0 0 30 * *", (2026, 2, 1), (2026, 3, 30)),
            ("0 0 29 feb *", (2027, 1, 1), (2028, 2, 29)),
        ]
        for expr, after, expected in cases:
            with self.subTest(expr=expr):
                self.check(expr, datetime(*after), datetime(*expected))

    def test_strictly_after_and_fractional_minute(self):
        self.check("* * * * *", datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 1))
        result = next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 0, 1))
        self.assertEqual(result, datetime(2026, 1, 1, 0, 1))
        self.assertEqual((result.second, result.microsecond), (0, 0))

    def test_whitespace_and_mixed_names_numbers(self):
        self.check("  0   0  *  jan,2  1,fri  ", datetime(2026, 1, 1), datetime(2026, 1, 2))

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

    def test_valid_descending_ranges(self):
        self.check("30-5 * * * *", datetime(2026, 1, 1, 0, 29), datetime(2026, 1, 1, 0, 30))


if __name__ == "__main__":
    unittest.main()
