"""Tests for orgsync.models — containers, equality, and dict shape."""

import dataclasses
import unittest

from orgsync.models import Account, Item, Summary


class AccountTest(unittest.TestCase):
    def test_to_dict_round_trips_the_fields(self):
        self.assertEqual(Account("a-1", "kim", True).to_dict(),
                         {"id": "a-1", "owner": "kim", "active": True})

    def test_value_equality(self):
        self.assertEqual(Account("a-1", "kim", True), Account("a-1", "kim", True))
        self.assertNotEqual(Account("a-1", "kim", True), Account("a-1", "kim", False))

    def test_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            Account("a-1", "kim", True).owner = "someone-else"


class ItemTest(unittest.TestCase):
    def test_to_dict_round_trips_the_fields(self):
        self.assertEqual(Item("i-1", "kim", "open", 3).to_dict(),
                         {"id": "i-1", "owner": "kim", "status": "open", "points": 3})

    def test_construction_does_not_validate(self):
        # Models are containers: nothing here decides what a service may send.
        item = Item(id="i-1", owner="kim", status="sideways", points=-1)
        self.assertEqual((item.status, item.points), ("sideways", -1))


class SummaryTest(unittest.TestCase):
    def test_counters_default_to_zero(self):
        self.assertEqual(Summary(owner="kim").to_dict(),
                         {"owner": "kim", "open_items": 0, "done_items": 0,
                          "total_points": 0})

    def test_to_dict_uses_the_api_field_order_and_values(self):
        summary = Summary(owner="kim", open_items=2, done_items=1, total_points=9)
        self.assertEqual(list(summary.to_dict()),
                         ["owner", "open_items", "done_items", "total_points"])
        self.assertEqual(summary.to_dict(),
                         {"owner": "kim", "open_items": 2, "done_items": 1,
                          "total_points": 9})


if __name__ == "__main__":
    unittest.main()
