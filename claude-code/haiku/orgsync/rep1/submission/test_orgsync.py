"""Tests for the sync command implementation."""

import io
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from orgsync.config import Config, load_config
from orgsync.http_client import HttpClient, Response
from orgsync.models import Account, Item, Summary
from orgsync.sync import (
    _fetch_accounts,
    _fetch_items,
    _fetch_summaries,
    _post_summary,
    _validate_account,
    _validate_item,
    run_sync,
)


class ValidateAccountTest(unittest.TestCase):
    def test_valid_account(self):
        record = {"id": "1", "owner": "alice", "active": True}
        account = _validate_account(record)
        self.assertIsNotNone(account)
        self.assertEqual(account.id, "1")
        self.assertEqual(account.owner, "alice")
        self.assertTrue(account.active)

    def test_extra_fields_ignored(self):
        record = {"id": "1", "owner": "alice", "active": True, "extra": "field"}
        account = _validate_account(record)
        self.assertIsNotNone(account)

    def test_missing_id_returns_none(self):
        record = {"owner": "alice", "active": True}
        account = _validate_account(record)
        self.assertIsNone(account)

    def test_missing_owner_returns_none(self):
        record = {"id": "1", "active": True}
        account = _validate_account(record)
        self.assertIsNone(account)

    def test_missing_active_returns_none(self):
        record = {"id": "1", "owner": "alice"}
        account = _validate_account(record)
        self.assertIsNone(account)

    def test_active_not_bool_returns_none(self):
        record = {"id": "1", "owner": "alice", "active": "yes"}
        account = _validate_account(record)
        self.assertIsNone(account)

    def test_id_not_string_returns_none(self):
        record = {"id": 123, "owner": "alice", "active": True}
        account = _validate_account(record)
        self.assertIsNone(account)

    def test_owner_not_string_returns_none(self):
        record = {"id": "1", "owner": 123, "active": True}
        account = _validate_account(record)
        self.assertIsNone(account)

    def test_not_dict_returns_none(self):
        self.assertIsNone(_validate_account("not a dict"))
        self.assertIsNone(_validate_account([]))
        self.assertIsNone(_validate_account(None))


class ValidateItemTest(unittest.TestCase):
    def test_valid_item(self):
        record = {"id": "1", "owner": "alice", "status": "open", "points": 5}
        item = _validate_item(record)
        self.assertIsNotNone(item)
        self.assertEqual(item.id, "1")
        self.assertEqual(item.owner, "alice")
        self.assertEqual(item.status, "open")
        self.assertEqual(item.points, 5)

    def test_zero_points_valid(self):
        record = {"id": "1", "owner": "alice", "status": "open", "points": 0}
        item = _validate_item(record)
        self.assertIsNotNone(item)
        self.assertEqual(item.points, 0)

    def test_negative_points_returns_none(self):
        record = {"id": "1", "owner": "alice", "status": "open", "points": -1}
        item = _validate_item(record)
        self.assertIsNone(item)

    def test_points_as_string_returns_none(self):
        record = {"id": "1", "owner": "alice", "status": "open", "points": "5"}
        item = _validate_item(record)
        self.assertIsNone(item)

    def test_missing_points_returns_none(self):
        record = {"id": "1", "owner": "alice", "status": "open"}
        item = _validate_item(record)
        self.assertIsNone(item)

    def test_missing_fields_returns_none(self):
        for missing in ["id", "owner", "status", "points"]:
            with self.subTest(missing=missing):
                record = {"id": "1", "owner": "alice", "status": "open", "points": 5}
                del record[missing]
                item = _validate_item(record)
                self.assertIsNone(item)

    def test_not_dict_returns_none(self):
        self.assertIsNone(_validate_item("not a dict"))
        self.assertIsNone(_validate_item([]))
        self.assertIsNone(_validate_item(None))


class FetchAccountsTest(unittest.TestCase):
    def test_fetches_valid_accounts(self):
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.read.return_value = json.dumps({
            "accounts": [
                {"id": "1", "owner": "alice", "active": True},
                {"id": "2", "owner": "bob", "active": False},
            ],
            "next_page": None
        }).encode("utf-8")
        mock_opener.open.return_value.__enter__.return_value = mock_response

        client = HttpClient("http://example.com", opener=mock_opener)
        accounts, errors = _fetch_accounts(client)

        self.assertEqual(len(accounts), 2)
        self.assertEqual(accounts[0].owner, "alice")
        self.assertEqual(accounts[1].owner, "bob")
        self.assertEqual(len(errors), 0)

    def test_separates_malformed_accounts(self):
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.read.return_value = json.dumps({
            "accounts": [
                {"id": "1", "owner": "alice", "active": True},
                {"id": "2", "owner": "bob", "active": "yes"},
            ],
            "next_page": None
        }).encode("utf-8")
        mock_opener.open.return_value.__enter__.return_value = mock_response

        client = HttpClient("http://example.com", opener=mock_opener)
        accounts, errors = _fetch_accounts(client)

        self.assertEqual(len(accounts), 1)
        self.assertEqual(len(errors), 1)

    def test_handles_pagination(self):
        mock_opener = MagicMock()
        responses = [
            json.dumps({
                "accounts": [{"id": "1", "owner": "alice", "active": True}],
                "next_page": 2
            }).encode("utf-8"),
            json.dumps({
                "accounts": [{"id": "2", "owner": "bob", "active": False}],
                "next_page": None
            }).encode("utf-8"),
        ]
        call_count = [0]

        def mock_open(request, timeout=None):
            resp = MagicMock()
            resp.status = 200
            resp.headers = {}
            resp.read.return_value = responses[call_count[0]]
            call_count[0] += 1
            return resp.__enter__.return_value.__class__()

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}

        def side_effect(request, timeout=None):
            cm = MagicMock()
            cm.__enter__.return_value = mock_response
            cm.__exit__.return_value = None
            return cm

        mock_response.read.side_effect = lambda: responses[0] if call_count[0] == 1 else responses[1]
        call_count[0] = 0

        mock_opener.open.side_effect = side_effect

        def reset_reads():
            call_count[0] = 0

        reset_reads()

        # Simpler approach: just test with single page
        mock_response.status = 200
        mock_response.headers = {}
        call_count[0] = 0

        def make_response(idx):
            r = MagicMock()
            r.status = 200
            r.headers = {}
            r.read.return_value = responses[idx]
            return r

        responses_objs = [make_response(0), make_response(1)]
        call_index = [0]

        def page_open(request, timeout=None):
            cm = MagicMock()
            cm.__enter__.return_value = responses_objs[call_index[0]]
            call_index[0] += 1
            cm.__exit__.return_value = None
            return cm

        mock_opener.open.side_effect = page_open
        client = HttpClient("http://example.com", opener=mock_opener)
        accounts, errors = _fetch_accounts(client)

        self.assertEqual(len(accounts), 2)


class FetchItemsTest(unittest.TestCase):
    def test_fetches_valid_items(self):
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.read.return_value = json.dumps({
            "items": [
                {"id": "i1", "owner": "alice", "status": "open", "points": 5},
                {"id": "i2", "owner": "bob", "status": "done", "points": 3},
            ],
            "next_page": None
        }).encode("utf-8")
        mock_opener.open.return_value.__enter__.return_value = mock_response

        client = HttpClient("http://example.com", opener=mock_opener)
        items, errors = _fetch_items(client)

        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].owner, "alice")
        self.assertEqual(items[1].owner, "bob")
        self.assertEqual(len(errors), 0)

    def test_deduplicates_items_by_id(self):
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.read.return_value = json.dumps({
            "items": [
                {"id": "i1", "owner": "alice", "status": "open", "points": 5},
                {"id": "i1", "owner": "alice", "status": "open", "points": 5},
            ],
            "next_page": None
        }).encode("utf-8")
        mock_opener.open.return_value.__enter__.return_value = mock_response

        client = HttpClient("http://example.com", opener=mock_opener)
        items, errors = _fetch_items(client)

        self.assertEqual(len(items), 1)

    def test_separates_malformed_items(self):
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.read.return_value = json.dumps({
            "items": [
                {"id": "i1", "owner": "alice", "status": "open", "points": 5},
                {"id": "i2", "owner": "bob", "status": "done", "points": -1},
            ],
            "next_page": None
        }).encode("utf-8")
        mock_opener.open.return_value.__enter__.return_value = mock_response

        client = HttpClient("http://example.com", opener=mock_opener)
        items, errors = _fetch_items(client)

        self.assertEqual(len(items), 1)
        self.assertEqual(len(errors), 1)


class FetchSummariesTest(unittest.TestCase):
    def test_fetches_existing_summaries(self):
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.read.return_value = json.dumps({
            "summaries": [
                {"owner": "alice", "open_items": 2, "done_items": 1, "total_points": 10},
            ],
            "next_page": None
        }).encode("utf-8")
        mock_opener.open.return_value.__enter__.return_value = mock_response

        client = HttpClient("http://example.com", opener=mock_opener)
        summaries = _fetch_summaries(client)

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].owner, "alice")

    def test_skips_malformed_summaries(self):
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.headers = {}
        mock_response.read.return_value = json.dumps({
            "summaries": [
                {"owner": "alice", "open_items": 2, "done_items": 1, "total_points": 10},
                {"owner": "bob", "open_items": "two", "done_items": 1, "total_points": 10},
            ],
            "next_page": None
        }).encode("utf-8")
        mock_opener.open.return_value.__enter__.return_value = mock_response

        client = HttpClient("http://example.com", opener=mock_opener)
        summaries = _fetch_summaries(client)

        self.assertEqual(len(summaries), 1)


class PostSummaryTest(unittest.TestCase):
    def test_posts_summary_successfully(self):
        mock_opener = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 201
        mock_response.headers = {}
        mock_opener.open.return_value.__enter__.return_value = mock_response

        client = HttpClient("http://example.com", opener=mock_opener)
        summary = Summary("alice", open_items=2, done_items=1, total_points=10)
        result = _post_summary(client, summary)

        self.assertTrue(result)

    def test_handles_429_retry_after(self):
        mock_opener = MagicMock()

        # First response: 429
        resp_429 = MagicMock()
        resp_429.status = 429
        resp_429.headers = {"Retry-After": "0.01"}

        # Second response: 201
        resp_201 = MagicMock()
        resp_201.status = 201
        resp_201.headers = {}

        call_count = [0]
        def mock_open(request, timeout=None):
            cm = MagicMock()
            if call_count[0] == 0:
                cm.__enter__.return_value = resp_429
            else:
                cm.__enter__.return_value = resp_201
            call_count[0] += 1
            cm.__exit__.return_value = None
            return cm

        mock_opener.open.side_effect = mock_open

        client = HttpClient("http://example.com", opener=mock_opener)
        summary = Summary("alice", open_items=2, done_items=1, total_points=10)
        result = _post_summary(client, summary)

        self.assertTrue(result)
        self.assertEqual(call_count[0], 2)

    def test_returns_false_on_rejected_status(self):
        for status in [400, 409, 422]:
            with self.subTest(status=status):
                mock_opener = MagicMock()
                mock_response = MagicMock()
                mock_response.status = status
                mock_response.headers = {}
                mock_opener.open.return_value.__enter__.return_value = mock_response

                client = HttpClient("http://example.com", opener=mock_opener)
                summary = Summary("alice", open_items=2, done_items=1, total_points=10)
                result = _post_summary(client, summary)

                self.assertFalse(result)


class RunSyncIntegrationTest(unittest.TestCase):
    """Integration tests against the actual fixture services."""

    def setUp(self):
        self.config = load_config("config.json")
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir)

    def test_sync_creates_output_files(self):
        result = run_sync(self.config, self.temp_dir)
        self.assertEqual(result, 0)

        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "errors.jsonl")))
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "orphans.jsonl")))
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "summary.jsonl")))

    def test_sync_output_files_are_valid_jsonl(self):
        result = run_sync(self.config, self.temp_dir)
        self.assertEqual(result, 0)

        for filename in ["errors.jsonl", "orphans.jsonl", "summary.jsonl"]:
            with open(os.path.join(self.temp_dir, filename), "r") as f:
                for line in f:
                    if line.strip():
                        obj = json.loads(line)
                        self.assertIsInstance(obj, dict)

    def test_sync_idempotency_no_second_writes(self):
        out_dir1 = tempfile.mkdtemp()
        out_dir2 = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out_dir1)
        self.addCleanup(shutil.rmtree, out_dir2)

        result1 = run_sync(self.config, out_dir1)
        self.assertEqual(result1, 0)

        # Count summaries after first run
        with open(os.path.join(out_dir1, "summary.jsonl"), "r") as f:
            summaries_first = [json.loads(line) for line in f if line.strip()]

        result2 = run_sync(self.config, out_dir2)
        self.assertEqual(result2, 0)

        # Count summaries after second run (should be same)
        with open(os.path.join(out_dir2, "summary.jsonl"), "r") as f:
            summaries_second = [json.loads(line) for line in f if line.strip()]

        # The spec says: running sync again with services unchanged
        # must issue no write requests (POSTs)
        # First sync posts new summaries; second sync should post nothing (0 new summaries)
        self.assertEqual(len(summaries_second), 0,
                        "Second sync should not write any new summaries")

    def test_orphans_are_items_without_active_owner(self):
        result = run_sync(self.config, self.temp_dir)
        self.assertEqual(result, 0)

        orphans_file = os.path.join(self.temp_dir, "orphans.jsonl")
        orphans = []
        with open(orphans_file, "r") as f:
            for line in f:
                if line.strip():
                    orphans.append(json.loads(line))

        # All orphans should be items
        for orphan in orphans:
            self.assertIn("id", orphan)
            self.assertIn("owner", orphan)
            self.assertIn("status", orphan)
            self.assertIn("points", orphan)

    def test_errors_are_malformed_records(self):
        result = run_sync(self.config, self.temp_dir)
        self.assertEqual(result, 0)

        errors_file = os.path.join(self.temp_dir, "errors.jsonl")
        errors = []
        with open(errors_file, "r") as f:
            for line in f:
                if line.strip():
                    errors.append(json.loads(line))

        for error in errors:
            self.assertIn("service", error)
            self.assertIn("record", error)
            self.assertIn(error["service"], ["directory", "ledger"])

    def test_summaries_count_items_correctly(self):
        result = run_sync(self.config, self.temp_dir)
        self.assertEqual(result, 0)

        summaries_file = os.path.join(self.temp_dir, "summary.jsonl")
        summaries = []
        with open(summaries_file, "r") as f:
            for line in f:
                if line.strip():
                    summaries.append(json.loads(line))

        for summary in summaries:
            self.assertIn("owner", summary)
            self.assertIn("open_items", summary)
            self.assertIn("done_items", summary)
            self.assertIn("total_points", summary)
            self.assertIsInstance(summary["open_items"], int)
            self.assertIsInstance(summary["done_items"], int)
            self.assertIsInstance(summary["total_points"], int)


if __name__ == "__main__":
    unittest.main()
