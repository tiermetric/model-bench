"""Black-box-ish tests for the reconciliation rules using in-memory services."""

import json
import os
import tempfile
import unittest
from unittest import mock

from orgsync.config import Config
from orgsync.http_client import Response
from orgsync.sync import run_sync


class FakeService:
    def __init__(self, kind):
        self.kind = kind
        self.calls = []
        self.rate_limited = False
        self.summaries = []

    def request(self, method, path, params=None, payload=None, headers=None):
        self.calls.append((method, path, params, payload, dict(headers or {})))
        if self.kind == "directory" and path == "/v1/items" and not self.rate_limited:
            self.rate_limited = True
            return Response(429, {"Retry-After": "0"}, b"", "directory/items")
        page = (params or {}).get("page")
        if method == "GET" and self.kind == "directory":
            pages = {
                1: {"items": [
                    {"id": "i1", "owner": "kim", "status": "open", "points": 3,
                     "extra": "kept"},
                    {"id": "orphan", "owner": "nobody", "status": "done", "points": 2},
                    {"id": "bad", "owner": "kim", "status": "open", "points": -1},
                ], "next_page": 2},
                2: {"items": [
                    {"id": "i1", "owner": "kim", "status": "open", "points": 3,
                     "extra": "kept"},
                    {"id": "i2", "owner": "kim", "status": "done", "points": 4},
                    ["malformed"],
                ], "next_page": None},
            }
            return self._ok(pages[page], path)
        if method == "GET" and self.kind == "ledger":
            if path == "/v1/accounts":
                pages = {
                    1: {"accounts": [
                        {"id": "a1", "owner": "kim", "active": True},
                        {"id": "a2", "owner": "lee", "active": False},
                    ], "next_page": 2},
                    2: {"accounts": [
                        {"id": "a3", "owner": "sam", "active": True},
                        {"id": "bad", "owner": "bad", "active": "yes"},
                    ], "next_page": None},
                }
                return self._ok(pages[page], path)
            if path == "/v1/summaries":
                return self._ok({"summaries": list(self.summaries), "next_page": None}, path)
        if method == "POST" and path == "/v1/summaries":
            self.summaries.append(payload)
            return self._ok({}, path, status=201)
        raise AssertionError((method, path, params, payload))

    @staticmethod
    def _ok(body, path, status=200):
        return Response(status, {}, json.dumps(body).encode(), path)


class SyncTest(unittest.TestCase):
    def test_reconciles_counts_errors_orphans_rate_limits_and_is_idempotent(self):
        directory = FakeService("directory")
        ledger = FakeService("ledger")

        def client_factory(url):
            return directory if "directory" in url else ledger

        with tempfile.TemporaryDirectory() as out_dir, mock.patch(
                "orgsync.sync.HttpClient", side_effect=client_factory):
            config = Config("directory", "ledger")
            self.assertEqual(run_sync(config, out_dir), 0)
            paths = [os.path.join(out_dir, name)
                     for name in ("errors.jsonl", "orphans.jsonl", "summary.jsonl")]
            def read(path):
                with open(path, encoding="utf-8") as handle:
                    return handle.read()

            first = {path: read(path) for path in paths}

            self.assertEqual(run_sync(config, out_dir), 0)
            self.assertEqual(first, {path: read(path) for path in paths})

        errors = [json.loads(line) for line in first[paths[0]].splitlines()]
        self.assertEqual(len(errors), 3)
        self.assertEqual({entry["service"] for entry in errors}, {"directory", "ledger"})
        self.assertEqual(json.loads(first[paths[1]]),
                         {"id": "orphan", "owner": "nobody", "status": "done", "points": 2})
        summaries = [json.loads(line) for line in first[paths[2]].splitlines()]
        self.assertEqual({entry["owner"] for entry in summaries}, {"kim", "sam"})
        self.assertIn({"owner": "kim", "open_items": 1, "done_items": 1,
                       "total_points": 7}, summaries)
        self.assertIn({"owner": "sam", "open_items": 0, "done_items": 0,
                       "total_points": 0}, summaries)
        self.assertEqual(sum(call[0] == "POST" for call in ledger.calls), 2)
        self.assertGreaterEqual(len(directory.calls), 3)  # initial 429 plus both pages


if __name__ == "__main__":
    unittest.main()
