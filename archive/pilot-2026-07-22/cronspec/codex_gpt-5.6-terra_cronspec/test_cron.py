import unittest
from datetime import datetime

from cron import next_fire


class NextFireExamplesTests(unittest.TestCase):
    def check(self, expression, after, expected):
        self.assertEqual(next_fire(expression, after), expected, expression)

    def test_original_deviations_and_names(self):
        self.check("*/15 * * * *", datetime(2026, 1, 1, 0, 0), datetime(2026, 1, 1, 0, 15))
        self.check("5-30/10 12 1 6 *", datetime(2026, 6, 1, 0, 0), datetime(2026, 6, 1, 12, 5))
        self.check("5-30/10 12 1 6 *", datetime(2026, 6, 1, 12, 5), datetime(2026, 6, 1, 12, 15))
        self.check("0 0 13 * fri", datetime(2026, 1, 1), datetime(2026, 2, 13))
        self.check("0 0 1 * mon", datetime(2026, 6, 2), datetime(2027, 2, 1))
        self.check("30 9 * * mon", datetime(2026, 7, 22), datetime(2026, 7, 27, 9, 30))
        self.check("0 12 1 jan,jul *", datetime(2026, 3, 1), datetime(2026, 7, 1, 12))

    def test_wrap_ranges(self):
        self.check("50-10 * * * *", datetime(2026, 1, 1, 0, 30), datetime(2026, 1, 1, 0, 50))
        self.check("50-10 * * * *", datetime(2026, 1, 1, 0, 55), datetime(2026, 1, 1, 0, 56))
        self.check("0 0 1 nov-feb *", datetime(2026, 3, 1), datetime(2026, 11, 1))
        self.check("0 0 * * fri-mon", datetime(2026, 7, 21), datetime(2026, 7, 24))

    def test_wrapped_steps(self):
        self.check("50-10/5 0 1 1 *", datetime(2026, 1, 1), datetime(2026, 1, 1, 0, 5))
        self.check("0 0 * * fri-tue/2", datetime(2026, 7, 21), datetime(2026, 7, 24))

    def test_last_day(self):
        self.check("0 0 L * *", datetime(2026, 1, 15), datetime(2026, 1, 31))
        self.check("0 0 L * *", datetime(2026, 2, 1), datetime(2026, 2, 28))
        self.check("0 0 L 2 *", datetime(2027, 3, 1), datetime(2028, 2, 29))
        self.check("0 0 L * fri", datetime(2026, 1, 1), datetime(2026, 7, 31))

    def test_real_calendar_days(self):
        self.check("0 0 31 * *", datetime(2026, 4, 1), datetime(2026, 5, 31))
        self.check("0 0 30 * *", datetime(2026, 2, 1), datetime(2026, 3, 30))
        self.check("0 0 29 feb *", datetime(2027, 1, 1), datetime(2028, 2, 29))


class NextFireBehaviorTests(unittest.TestCase):
    def test_after_is_strictly_after_and_on_a_minute(self):
        actual = next_fire("* * * * *", datetime(2026, 1, 1, 4, 5, 59, 123))
        self.assertEqual(actual, datetime(2026, 1, 1, 4, 6))
        self.assertEqual((actual.second, actual.microsecond), (0, 0))

    def test_only_bare_star_is_unrestricted(self):
        # Jan 9, 2026 is Friday and an odd day; the step is anchored at 1.
        self.assertEqual(next_fire("0 0 */2 * fri", datetime(2026, 1, 1)),
                         datetime(2026, 1, 9))

    def test_numeric_and_named_values_can_be_mixed(self):
        self.assertEqual(next_fire("0 0 * 1,mar mon,5", datetime(2026, 2, 1)),
                         datetime(2026, 3, 2))


class ValidationTests(unittest.TestCase):
    def test_invalid_expressions(self):
        invalid = [
            "", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *",
            "* * 0 * *", "* * * 13 *", "* * * * 7", "* * * * SUN",
            "*/0 * * * *", "5/10 * * * *", "1,,3 * * * *", "1x * * * *",
            "70-10 * * * *", "0 0 L,15 * *", "0 0 * * L", "0 0 l * *",
        ]
        for expression in invalid:
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    next_fire(expression, datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
