import unittest
from datetime import datetime

from cron import next_fire


class CronTests(unittest.TestCase):
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
        for expression, after, expected in cases:
            with self.subTest(expression=expression, after=after):
                self.assertEqual(next_fire(expression, after), expected)

    def test_strictly_after_and_rounding(self):
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0, 1, 1)),
            datetime(2026, 1, 1, 0, 1),
        )
        self.assertEqual(
            next_fire("* * * * *", datetime(2026, 1, 1, 0, 0)),
            datetime(2026, 1, 1, 0, 1),
        )

    def test_bare_star_is_the_only_unrestricted_form(self):
        # */2 is restricted, so it intersects with Monday.
        self.assertEqual(
            next_fire("0 0 */2 * mon", datetime(2026, 1, 1)),
            datetime(2026, 1, 5),
        )

    def test_steps_anchor_at_domain_floor_and_range_start(self):
        self.assertEqual(next_fire("5-30/10 * * * *", datetime(2026, 1, 1)), datetime(2026, 1, 1, 0, 5))
        self.assertEqual(next_fire("0 0 */10 * *", datetime(2026, 1, 1)), datetime(2026, 1, 11))
        self.assertEqual(next_fire("0 0 1 */5 *", datetime(2026, 1, 2)), datetime(2026, 6, 1))

    def test_names_numbers_lists_and_whitespace(self):
        self.assertEqual(
            next_fire("  0   0  *  jan,7  1,fri  ", datetime(2026, 1, 1)),
            datetime(2026, 1, 2),
        )

    def test_invalid_expressions(self):
        expressions = [
            "", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *",
            "* * 0 * *", "* * * 13 *", "* * * * 7", "* * * * SUN",
            "* * * JAN *", "*/0 * * * *", "*/-1 * * * *", "1-5/0 * * * *",
            "30-5 * * * *", "* * * * fri-mon", "5/10 * * * *",
            "1,,3 * * * *", "1, * * * *", "1x * * * *", "* * * * mon-",
            "*//2 * * * *", "* * * abc *",
        ]
        for expression in expressions:
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    next_fire(expression, datetime(2026, 1, 1))

    def test_lowercase_month_name_is_valid(self):
        self.assertEqual(next_fire("0 0 1 jan *", datetime(2026, 2, 1)), datetime(2027, 1, 1))


if __name__ == "__main__":
    unittest.main()
