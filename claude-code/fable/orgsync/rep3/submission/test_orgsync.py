"""Tests for the `sync` reconciliation: well-formedness, the pure decision
step, rate-limit handling, and an end-to-end pass against the two live
services named in `config.json`.

Standard library only. The live tests post nothing beyond what `sync` itself
posts, and `sync` is idempotent, so this suite can run before or after the
tool and repeatedly.
"""

import json
import os
import tempfile
import unittest

from orgsync.config import load_config
from orgsync.http_client import HttpClient
from orgsync.sync import (
    ServiceReader,
    is_well_formed_account,
    is_well_formed_item,
    is_well_formed_summary,
    reconcile,
    run_sync,
)


class ItemWellFormednessTest(unittest.TestCase):
    GOOD = {"id": "i-1", "owner": "kim", "status": "open", "points": 3}

    def test_accepts_a_well_formed_item(self):
        self.assertTrue(is_well_formed_item(self.GOOD))

    def test_extra_fields_are_ignored(self):
        self.assertTrue(is_well_formed_item(dict(self.GOOD, note="x", v=2)))

    def test_zero_points_are_in_range(self):
        self.assertTrue(is_well_formed_item(dict(self.GOOD, points=0)))

    def test_rejects_missing_fields(self):
        for field in ("id", "owner", "status", "points"):
            record = dict(self.GOOD)
            del record[field]
            with self.subTest(missing=field):
                self.assertFalse(is_well_formed_item(record))

    def test_rejects_wrong_types_and_values(self):
        bad = [dict(self.GOOD, points="3"),      # int must be a JSON integer
               dict(self.GOOD, points=3.0),      # a float is not an integer
               dict(self.GOOD, points=True),     # nor is a boolean
               dict(self.GOOD, points=-1),       # out of range
               dict(self.GOOD, status="closed"),
               dict(self.GOOD, status=None),
               dict(self.GOOD, id=7),
               dict(self.GOOD, owner=None),
               "not an object", None, 42, ["i-1"]]
        for record in bad:
            with self.subTest(record=record):
                self.assertFalse(is_well_formed_item(record))


class AccountWellFormednessTest(unittest.TestCase):
    GOOD = {"id": "a-1", "owner": "kim", "active": True}

    def test_accepts_both_boolean_values_and_extra_fields(self):
        self.assertTrue(is_well_formed_account(self.GOOD))
        self.assertTrue(is_well_formed_account(dict(self.GOOD, active=False)))
        self.assertTrue(is_well_formed_account(dict(self.GOOD, tier="gold")))

    def test_rejects_non_boolean_active_and_missing_fields(self):
        bad = [dict(self.GOOD, active="yes"), dict(self.GOOD, active=1),
               dict(self.GOOD, active=None),
               {"id": "a-1", "owner": "kim"}, {"id": "a-1", "active": True},
               {"owner": "kim", "active": True}, "kim", None]
        for record in bad:
            with self.subTest(record=record):
                self.assertFalse(is_well_formed_account(record))


class SummaryWellFormednessTest(unittest.TestCase):
    GOOD = {"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9}

    def test_accepts_the_posted_shape(self):
        self.assertTrue(is_well_formed_summary(self.GOOD))

    def test_rejects_wrong_counter_types(self):
        for record in (dict(self.GOOD, open_items="2"),
                       dict(self.GOOD, done_items=True),
                       dict(self.GOOD, total_points=-1),
                       dict(self.GOOD, owner=3), {"owner": "kim"}, None):
            with self.subTest(record=record):
                self.assertFalse(is_well_formed_summary(record))


class ReconcileTest(unittest.TestCase):
    """The pure decision step, fed synthetic record streams."""

    def test_worked_example_arithmetic(self):
        items = [{"id": "i-1", "owner": "kim", "status": "open", "points": 3},
                 {"id": "i-2", "owner": "kim", "status": "open", "points": 4},
                 {"id": "i-3", "owner": "kim", "status": "done", "points": 2}]
        accounts = [{"id": "a-1", "owner": "kim", "active": True}]
        to_post, orphans, errors = reconcile(items, accounts, [])
        self.assertEqual(to_post, [{"owner": "kim", "open_items": 2,
                                    "done_items": 1, "total_points": 9}])
        self.assertEqual((orphans, errors), ([], []))

    def test_duplicate_delivery_of_an_item_counts_once(self):
        item = {"id": "i-1", "owner": "kim", "status": "open", "points": 5}
        accounts = [{"id": "a-1", "owner": "kim", "active": True}]
        to_post, _, _ = reconcile([item, dict(item), dict(item)], accounts, [])
        self.assertEqual(to_post, [{"owner": "kim", "open_items": 1,
                                    "done_items": 0, "total_points": 5}])

    def test_active_account_with_no_items_gets_all_zeroes(self):
        accounts = [{"id": "a-1", "owner": "kim", "active": True}]
        to_post, _, _ = reconcile([], accounts, [])
        self.assertEqual(to_post, [{"owner": "kim", "open_items": 0,
                                    "done_items": 0, "total_points": 0}])

    def test_inactive_and_unknown_owners_make_orphans_not_summaries(self):
        items = [{"id": "i-1", "owner": "ina", "status": "open", "points": 1},
                 {"id": "i-2", "owner": "ghost", "status": "done", "points": 2}]
        accounts = [{"id": "a-1", "owner": "ina", "active": False}]
        to_post, orphans, errors = reconcile(items, accounts, [])
        self.assertEqual(to_post, [])
        self.assertEqual(sorted(o["id"] for o in orphans), ["i-1", "i-2"])
        self.assertEqual(errors, [])

    def test_orphaned_items_are_deduplicated(self):
        item = {"id": "i-1", "owner": "ghost", "status": "open", "points": 1}
        _, orphans, _ = reconcile([item, dict(item)], [], [])
        self.assertEqual(orphans, [item])

    def test_a_malformed_account_does_not_activate_its_owner(self):
        items = [{"id": "i-1", "owner": "fen", "status": "open", "points": 2}]
        accounts = [{"id": "a-1", "owner": "fen", "active": "yes"}]
        to_post, orphans, errors = reconcile(items, accounts, [])
        self.assertEqual(to_post, [])
        self.assertEqual(orphans, items)
        self.assertEqual(errors, [{"service": "ledger", "record": accounts[0]}])

    def test_malformed_records_are_tagged_skipped_and_deduplicated(self):
        bad_item = {"id": "i-9", "owner": "kim", "status": "open"}
        bad_account = {"id": "a-9", "owner": "lee", "active": 1}
        accounts = [{"id": "a-1", "owner": "kim", "active": True}, bad_account]
        to_post, orphans, errors = reconcile(
            [bad_item, dict(bad_item)], accounts, [])
        self.assertEqual(to_post, [{"owner": "kim", "open_items": 0,
                                    "done_items": 0, "total_points": 0}])
        self.assertEqual(orphans, [])
        self.assertCountEqual(errors, [
            {"service": "directory", "record": bad_item},
            {"service": "ledger", "record": bad_account}])

    def test_owners_already_summarized_are_not_posted_again(self):
        accounts = [{"id": "a-1", "owner": "kim", "active": True},
                    {"id": "a-2", "owner": "lee", "active": True}]
        summaries = [{"owner": "kim", "open_items": 0, "done_items": 0,
                      "total_points": 0}]
        to_post, _, _ = reconcile([], accounts, summaries)
        self.assertEqual([s["owner"] for s in to_post], ["lee"])

    def test_a_malformed_summary_is_an_error_and_does_not_suppress_a_post(self):
        accounts = [{"id": "a-1", "owner": "kim", "active": True}]
        bad_summary = {"owner": "kim", "open_items": "0", "done_items": 0,
                       "total_points": 0}
        to_post, _, errors = reconcile([], accounts, [bad_summary])
        self.assertEqual([s["owner"] for s in to_post], ["kim"])
        self.assertIn({"service": "ledger", "record": bad_summary}, errors)


class _FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self.headers = headers or {}
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeOpener:
    """Stands in for urllib's opener: serves a scripted list of responses and
    records every request URL."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def open(self, request, timeout=None):
        self.urls.append(request.full_url)
        return self.responses.pop(0)


class ServiceReaderTest(unittest.TestCase):
    def _reader(self, responses):
        naps = []
        opener = _FakeOpener(responses)
        client = HttpClient("http://svc.test", opener=opener)
        return ServiceReader(client, sleep=naps.append), opener, naps

    def test_follows_next_page_until_null(self):
        reader, opener, _ = self._reader([
            _FakeResponse(200, {"items": [1, 2], "next_page": 2}),
            _FakeResponse(200, {"items": [3], "next_page": None}),
        ])
        self.assertEqual(reader.fetch_all("/v1/items", "items"), [1, 2, 3])
        self.assertEqual(opener.urls, ["http://svc.test/v1/items?page=1",
                                       "http://svc.test/v1/items?page=2"])

    def test_429_waits_retry_after_then_retries_the_same_request(self):
        reader, opener, naps = self._reader([
            _FakeResponse(429, {}, headers={"Retry-After": "3"}),
            _FakeResponse(200, {"items": ["a"], "next_page": None}),
        ])
        self.assertEqual(reader.fetch_all("/v1/items", "items"), ["a"])
        self.assertEqual(naps, [3.0])
        self.assertEqual(opener.urls, ["http://svc.test/v1/items?page=1"] * 2)


class LiveSyncTest(unittest.TestCase):
    """End-to-end against the services in `config.json`. `sync` is idempotent,
    so these tests can run in any order and any number of times."""

    @classmethod
    def setUpClass(cls):
        cls.config = load_config(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"))
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out_dir = cls.tmp.name
        cls.exit_code = run_sync(cls.config, cls.out_dir)
        cls.outputs = {name: cls._read_jsonl(name)
                       for name in ("orphans.jsonl", "errors.jsonl", "summary.jsonl")}
        ledger = ServiceReader(HttpClient(cls.config.ledger_url))
        directory = ServiceReader(HttpClient(cls.config.directory_url))
        cls.items = directory.fetch_all("/v1/items", "items")
        cls.accounts = ledger.fetch_all("/v1/accounts", "accounts")
        cls.summaries = ledger.fetch_all("/v1/summaries", "summaries")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def _read_jsonl(cls, name):
        with open(os.path.join(cls.out_dir, name), "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def _active_owners(self):
        return {a["owner"] for a in self.accounts
                if is_well_formed_account(a) and a["active"]}

    def test_sync_exits_zero_and_writes_all_three_files(self):
        self.assertEqual(self.exit_code, 0)
        self.assertEqual(sorted(self.outputs),
                         ["errors.jsonl", "orphans.jsonl", "summary.jsonl"])

    def test_every_active_owner_has_exactly_one_summary_in_the_ledger(self):
        posted = [s["owner"] for s in self.summaries if is_well_formed_summary(s)]
        for owner in self._active_owners():
            self.assertEqual(posted.count(owner), 1,
                             "owner %r should have exactly one summary" % owner)

    def test_ledger_summaries_match_the_directory_arithmetic(self):
        by_owner = {s["owner"]: s for s in self.summaries
                    if is_well_formed_summary(s)}
        active = self._active_owners()
        seen = {}
        for item in self.items:
            if is_well_formed_item(item):
                seen.setdefault(item["id"], item)
        for owner in active:
            mine = [i for i in seen.values() if i["owner"] == owner]
            expected = {
                "owner": owner,
                "open_items": sum(1 for i in mine if i["status"] == "open"),
                "done_items": sum(1 for i in mine if i["status"] == "done"),
                "total_points": sum(i["points"] for i in mine),
            }
            got = dict(by_owner[owner])
            self.assertEqual({k: got.get(k) for k in expected}, expected)

    def test_orphans_are_exactly_the_items_without_an_active_owner(self):
        active = self._active_owners()
        expected = {}
        for item in self.items:
            if is_well_formed_item(item) and item["owner"] not in active:
                expected.setdefault(item["id"], item)
        got = self.outputs["orphans.jsonl"]
        self.assertEqual(len(got), len({o["id"] for o in got}),
                         "each orphan appears exactly once")
        self.assertEqual({o["id"]: o for o in got}, expected)

    def test_errors_hold_each_malformed_record_once_with_its_service(self):
        expected = []
        for record in self.items:
            if not is_well_formed_item(record):
                entry = {"service": "directory", "record": record}
                if entry not in expected:
                    expected.append(entry)
        for record, check in ((r, is_well_formed_account) for r in self.accounts):
            if not check(record):
                entry = {"service": "ledger", "record": record}
                if entry not in expected:
                    expected.append(entry)
        got = self.outputs["errors.jsonl"]
        for entry in expected:
            self.assertIn(entry, got)
        for entry in got:
            self.assertIn(entry["service"], ("directory", "ledger"))
            self.assertEqual(got.count(entry), 1)

    def test_summary_file_lines_exist_in_the_ledger(self):
        ledger_bodies = [
            {k: s[k] for k in ("owner", "open_items", "done_items", "total_points")}
            for s in self.summaries if is_well_formed_summary(s)]
        for line in self.outputs["summary.jsonl"]:
            self.assertIn(line, ledger_bodies)

    def test_second_run_changes_nothing(self):
        self.assertEqual(run_sync(self.config, self.out_dir), 0)
        for name, before in self.outputs.items():
            self.assertEqual(self._read_jsonl(name), before,
                             "%s changed on re-run" % name)
        ledger = ServiceReader(HttpClient(self.config.ledger_url))
        self.assertEqual(ledger.fetch_all("/v1/summaries", "summaries"),
                         self.summaries)


if __name__ == "__main__":
    unittest.main()
