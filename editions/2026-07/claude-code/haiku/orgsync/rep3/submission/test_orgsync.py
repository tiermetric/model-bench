"""Tests for the `sync` command: reconcile Directory and Ledger.

These tests exercise the core sync logic against the two services,
covering pagination, deduplication, orphan detection, error handling,
rate limiting, and idempotency.
"""

import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List
from unittest import mock

from orgsync.config import Config
from orgsync.http_client import HttpClient, Response
from orgsync.models import Account, Item, Summary
from orgsync.sync import (
    DEFAULT_OUT_DIR, ERRORS_FILE, ORPHANS_FILE, SUMMARIES_FILE,
    _compute_summaries, _fetch_accounts, _fetch_items, _fetch_summaries,
    _is_well_formed_account, _is_well_formed_item, run_sync
)


class WellFormedValidationTest(unittest.TestCase):
    """Tests for record validation functions."""

    def test_well_formed_account(self):
        record = {"id": "acct-1", "owner": "alice", "active": True}
        self.assertTrue(_is_well_formed_account(record))

    def test_account_with_extra_fields_is_well_formed(self):
        record = {"id": "acct-1", "owner": "alice", "active": True, "extra": "ignored"}
        self.assertTrue(_is_well_formed_account(record))

    def test_account_missing_field_is_malformed(self):
        for key in ("id", "owner", "active"):
            with self.subTest(missing=key):
                record = {"id": "acct-1", "owner": "alice", "active": True}
                del record[key]
                self.assertFalse(_is_well_formed_account(record))

    def test_account_wrong_type_is_malformed(self):
        self.assertFalse(_is_well_formed_account({"id": 1, "owner": "alice", "active": True}))
        self.assertFalse(_is_well_formed_account({"id": "acct-1", "owner": 1, "active": True}))
        self.assertFalse(_is_well_formed_account({"id": "acct-1", "owner": "alice", "active": "yes"}))

    def test_account_not_dict_is_malformed(self):
        self.assertFalse(_is_well_formed_account([]))
        self.assertFalse(_is_well_formed_account("not a dict"))

    def test_well_formed_item(self):
        record = {"id": "item-1", "owner": "alice", "status": "open", "points": 5}
        self.assertTrue(_is_well_formed_item(record))

    def test_item_with_extra_fields_is_well_formed(self):
        record = {"id": "item-1", "owner": "alice", "status": "open", "points": 5, "extra": "ignored"}
        self.assertTrue(_is_well_formed_item(record))

    def test_item_missing_field_is_malformed(self):
        for key in ("id", "owner", "status", "points"):
            with self.subTest(missing=key):
                record = {"id": "item-1", "owner": "alice", "status": "open", "points": 5}
                del record[key]
                self.assertFalse(_is_well_formed_item(record))

    def test_item_wrong_status_is_malformed(self):
        record = {"id": "item-1", "owner": "alice", "status": "pending", "points": 5}
        self.assertFalse(_is_well_formed_item(record))

    def test_item_negative_points_is_malformed(self):
        record = {"id": "item-1", "owner": "alice", "status": "open", "points": -1}
        self.assertFalse(_is_well_formed_item(record))

    def test_item_wrong_type_is_malformed(self):
        self.assertFalse(_is_well_formed_item({"id": 1, "owner": "alice", "status": "open", "points": 5}))
        self.assertFalse(_is_well_formed_item({"id": "item-1", "owner": 1, "status": "open", "points": 5}))
        self.assertFalse(_is_well_formed_item({"id": "item-1", "owner": "alice", "status": "open", "points": "5"}))


class ComputeSummariesTest(unittest.TestCase):
    """Tests for summary computation logic."""

    def test_empty_items_and_owners(self):
        result = _compute_summaries([], set())
        self.assertEqual(result, {})

    def test_active_owner_with_no_items(self):
        result = _compute_summaries([], {"alice"})
        self.assertEqual(result, {"alice": Summary(owner="alice", open_items=0, done_items=0, total_points=0)})

    def test_multiple_active_owners_with_no_items(self):
        result = _compute_summaries([], {"alice", "bob", "charlie"})
        self.assertEqual(len(result), 3)
        for owner in ("alice", "bob", "charlie"):
            self.assertEqual(result[owner], Summary(owner=owner, open_items=0, done_items=0, total_points=0))

    def test_items_for_active_owner(self):
        items = [
            Item(id="item-1", owner="alice", status="open", points=3),
            Item(id="item-2", owner="alice", status="open", points=4),
            Item(id="item-3", owner="alice", status="done", points=2),
        ]
        result = _compute_summaries(items, {"alice"})
        expected = Summary(owner="alice", open_items=2, done_items=1, total_points=9)
        self.assertEqual(result["alice"], expected)

    def test_duplicate_items_counted_once(self):
        items = [
            Item(id="item-1", owner="alice", status="open", points=3),
            Item(id="item-1", owner="alice", status="open", points=3),  # duplicate
        ]
        result = _compute_summaries(items, {"alice"})
        expected = Summary(owner="alice", open_items=1, done_items=0, total_points=3)
        self.assertEqual(result["alice"], expected)

    def test_items_for_inactive_owner_ignored(self):
        items = [
            Item(id="item-1", owner="alice", status="open", points=3),
            Item(id="item-2", owner="bob", status="open", points=5),  # bob is inactive
        ]
        result = _compute_summaries(items, {"alice"})
        self.assertEqual(len(result), 1)
        self.assertIn("alice", result)
        self.assertNotIn("bob", result)

    def test_mixed_statuses_and_owners(self):
        items = [
            Item(id="item-1", owner="alice", status="open", points=3),
            Item(id="item-2", owner="alice", status="done", points=2),
            Item(id="item-3", owner="bob", status="open", points=4),
            Item(id="item-4", owner="bob", status="done", points=1),
        ]
        result = _compute_summaries(items, {"alice", "bob"})
        self.assertEqual(result["alice"], Summary(owner="alice", open_items=1, done_items=1, total_points=5))
        self.assertEqual(result["bob"], Summary(owner="bob", open_items=1, done_items=1, total_points=5))


class OutputFileFormattingTest(unittest.TestCase):
    """Tests for output file format and deduplication."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)

    def test_orphans_file_format(self):
        items = [
            {"id": "item-1", "owner": "alice", "status": "open", "points": 3},
            {"id": "item-2", "owner": "bob", "status": "open", "points": 5},
        ]
        self._write_and_verify_file(
            os.path.join(self.tmpdir, ORPHANS_FILE),
            items
        )

    def test_errors_file_format(self):
        errors = [
            {"service": "directory", "record": {"id": "item-1", "owner": "alice", "status": "open"}},
            {"service": "ledger", "record": {"id": "acct-1", "owner": "bob", "active": "yes"}},
        ]
        self._write_and_verify_file(
            os.path.join(self.tmpdir, ERRORS_FILE),
            errors
        )

    def test_summaries_file_format(self):
        summaries = [
            {"owner": "alice", "open_items": 2, "done_items": 1, "total_points": 9},
            {"owner": "bob", "open_items": 1, "done_items": 1, "total_points": 5},
        ]
        self._write_and_verify_file(
            os.path.join(self.tmpdir, SUMMARIES_FILE),
            summaries
        )

    def _write_and_verify_file(self, path: str, records: List[Dict[str, Any]]) -> None:
        """Write records as JSONL and verify format."""
        from orgsync.sync import _write_jsonl_file
        _write_jsonl_file(path, records)

        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        self.assertEqual(len(lines), len(records))
        for line, expected_record in zip(lines, records):
            parsed = json.loads(line.strip())
            self.assertEqual(parsed, expected_record)


class SyncIntegrationTest(unittest.TestCase):
    """Tests for the full sync workflow against real services."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)

        config_path = os.path.join(self.tmpdir, "config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump({
                "directory_url": "http://127.0.0.1:56587",
                "ledger_url": "http://127.0.0.1:56588",
            }, f)
        self.config_path = config_path
        self.config = Config(
            directory_url="http://127.0.0.1:56587",
            ledger_url="http://127.0.0.1:56588",
        )
        self.out_dir = os.path.join(self.tmpdir, "out")

    def test_sync_completes_and_creates_output_dir(self):
        """Test that sync completes successfully and creates output directory."""
        self.assertFalse(os.path.exists(self.out_dir))
        exit_code = run_sync(self.config, self.out_dir)
        self.assertEqual(exit_code, 0)
        self.assertTrue(os.path.exists(self.out_dir))

    def test_sync_creates_all_output_files(self):
        """Test that all three output files are created."""
        run_sync(self.config, self.out_dir)
        for filename in (ORPHANS_FILE, ERRORS_FILE, SUMMARIES_FILE):
            path = os.path.join(self.out_dir, filename)
            self.assertTrue(os.path.exists(path), f"{filename} not created")

    def test_output_files_contain_valid_jsonl(self):
        """Test that output files contain valid JSON lines."""
        run_sync(self.config, self.out_dir)
        for filename in (ORPHANS_FILE, ERRORS_FILE, SUMMARIES_FILE):
            path = os.path.join(self.out_dir, filename)
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        json.loads(line)

    def test_sync_is_idempotent(self):
        """Test that running sync twice produces identical output files."""
        run_sync(self.config, self.out_dir)

        file_contents_1 = {}
        for filename in (ORPHANS_FILE, ERRORS_FILE, SUMMARIES_FILE):
            path = os.path.join(self.out_dir, filename)
            with open(path, "rb") as f:
                file_contents_1[filename] = f.read()

        run_sync(self.config, self.out_dir)

        file_contents_2 = {}
        for filename in (ORPHANS_FILE, ERRORS_FILE, SUMMARIES_FILE):
            path = os.path.join(self.out_dir, filename)
            with open(path, "rb") as f:
                file_contents_2[filename] = f.read()

        self.assertEqual(file_contents_1, file_contents_2, "Files changed on second sync")

    def test_orphans_contain_only_items_with_inactive_owners(self):
        """Test that orphans.jsonl contains items whose owners are not active accounts."""
        run_sync(self.config, self.out_dir)

        orphans_path = os.path.join(self.out_dir, ORPHANS_FILE)
        with open(orphans_path, "r", encoding="utf-8") as f:
            orphans = [json.loads(line) for line in f if line.strip()]

        if orphans:
            dir_client = HttpClient(self.config.directory_url)
            _, items_with_errors = _fetch_items(dir_client)

            ledger_client = HttpClient(self.config.ledger_url)
            accounts, _ = _fetch_accounts(ledger_client)
            active_owners = {acc.owner for acc in accounts if acc.active}

            for orphan in orphans:
                self.assertNotIn(orphan.get("owner"), active_owners,
                                f"Orphan with active owner: {orphan}")

    def test_errors_contain_malformed_records(self):
        """Test that errors.jsonl contains properly formatted error entries."""
        run_sync(self.config, self.out_dir)

        errors_path = os.path.join(self.out_dir, ERRORS_FILE)
        with open(errors_path, "r", encoding="utf-8") as f:
            errors = [json.loads(line) for line in f if line.strip()]

        for error in errors:
            self.assertIn("service", error)
            self.assertIn("record", error)
            self.assertIn(error["service"], ("directory", "ledger"))
            self.assertIsInstance(error["record"], dict)

    def test_summaries_contain_only_active_owners(self):
        """Test that posted summaries are only for active owners."""
        run_sync(self.config, self.out_dir)

        summaries_path = os.path.join(self.out_dir, SUMMARIES_FILE)
        with open(summaries_path, "r", encoding="utf-8") as f:
            posted_summaries = [json.loads(line) for line in f if line.strip()]

        ledger_client = HttpClient(self.config.ledger_url)
        accounts, _ = _fetch_accounts(ledger_client)
        active_owners = {acc.owner for acc in accounts if acc.active}

        for summary in posted_summaries:
            self.assertIn(summary["owner"], active_owners,
                         f"Posted summary for inactive owner: {summary}")

    def test_no_duplicate_entries_in_output_files(self):
        """Test that each entry appears exactly once in output files."""
        run_sync(self.config, self.out_dir)

        for filename in (ORPHANS_FILE, ERRORS_FILE, SUMMARIES_FILE):
            path = os.path.join(self.out_dir, filename)
            with open(path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]

            seen = set()
            for line in lines:
                self.assertNotIn(line, seen, f"Duplicate entry in {filename}: {line}")
                seen.add(line)


class MockHttpResponseTest(unittest.TestCase):
    """Tests for handling various HTTP responses."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir)

    def test_rate_limit_handling(self):
        """Test that 429 responses trigger retry-after behavior."""
        def mock_get(*args, **kwargs):
            if not hasattr(mock_get, 'call_count'):
                mock_get.call_count = 0
            mock_get.call_count += 1

            if mock_get.call_count == 1:
                return Response(429, {"Retry-After": "0"}, b'', "test")
            return Response(200, {}, json.dumps({"items": [], "next_page": None}).encode("utf-8"), "test")

        config = Config(directory_url="http://test", ledger_url="http://test")
        dir_client = HttpClient(config.directory_url)
        dir_client.get = mock_get

        from orgsync.sync import _fetch_paginated
        records, _ = _fetch_paginated(dir_client, "/v1/items")
        self.assertEqual(len(records), 0)


if __name__ == "__main__":
    unittest.main()
