"""End-to-end reconciliation tests using an in-process HTTP-client double."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from orgsync.config import Config
from orgsync.sync import run_sync


class FakeResponse:
    def __init__(self, status, payload=None, headers=None, url="http://fake"):
        self.status = status
        self._payload = payload
        self.headers = headers or {}
        self.url = url

    def json(self):
        return self._payload


class Service:
    def __init__(self, pages, ledger=False):
        self.pages = pages
        self.ledger = ledger
        self.calls = []
        self.posts = []
        self.rate_limited = set()

    def request(self, method, path, params=None, payload=None, headers=None):
        page = (params or {}).get("page")
        self.calls.append((method, path, page, payload, headers))
        # Exercise a retry on a read and on a write without making the test wait.
        marker = (method, path, page)
        if marker not in self.rate_limited and marker in {
                ("GET", "/v1/accounts", 1), ("POST", "/v1/summaries", None)}:
            self.rate_limited.add(marker)
            return FakeResponse(429, headers={"Retry-After": "0"})
        if method == "POST":
            self.posts.append(payload)
            self.pages["summaries"][1]["summaries"].append(dict(payload))
            return FakeResponse(201, {"ok": True})
        collection = {"/v1/accounts": "accounts", "/v1/items": "items",
                      "/v1/summaries": "summaries"}[path]
        return FakeResponse(200, self.pages[collection][page])


class SyncTest(unittest.TestCase):
    def test_paginates_validates_deduplicates_posts_and_is_idempotent(self):
        ledger = Service({
            "accounts": {
                1: {"accounts": [{"id": "a", "owner": "kim", "active": True},
                                  {"id": "b", "owner": "lee", "active": False},
                                  {"id": "bad", "owner": "no", "active": 1}], "next_page": 2},
                2: {"accounts": [{"id": "c", "owner": "zoe", "active": True}], "next_page": None},
            },
            "summaries": {1: {"summaries": [{"owner": "zoe", "open_items": 0,
                                                  "done_items": 0, "total_points": 0},
                                                {"owner": 3}], "next_page": None}},
        }, ledger=True)
        directory = Service({
            "items": {
                1: {"items": [{"id": "1", "owner": "kim", "status": "open", "points": 3},
                                {"id": "1", "owner": "kim", "status": "open", "points": 3},
                                {"id": "bad", "owner": "kim", "status": "open", "points": True}],
                    "next_page": 2},
                2: {"items": [{"id": "2", "owner": "kim", "status": "done", "points": 4},
                                {"id": "3", "owner": "lee", "status": "open", "points": 9}],
                    "next_page": None},
            },
        })
        clients = [directory, ledger]
        with tempfile.TemporaryDirectory() as out_dir, \
                patch("orgsync.sync.HttpClient", side_effect=lambda url: clients.pop(0)), \
                patch("orgsync.sync.time.sleep") as sleep:
            config = Config("directory", "ledger")
            self.assertEqual(run_sync(config, out_dir), 0)
            first = {}
            for name in ("errors.jsonl", "orphans.jsonl", "summary.jsonl"):
                with open(os.path.join(out_dir, name), encoding="utf-8") as handle:
                    first[name] = handle.read()
            self.assertEqual(ledger.posts, [{"owner": "kim", "open_items": 1,
                                             "done_items": 1, "total_points": 7}])
            error_records = [json.loads(line) for line in first["errors.jsonl"].splitlines()]
            self.assertEqual({entry["service"] for entry in error_records},
                             {"directory", "ledger"})
            self.assertEqual(len(error_records), 3)
            self.assertEqual([json.loads(line)["id"] for line in first["orphans.jsonl"].splitlines()], ["3"])
            self.assertTrue(sleep.called)

            # A second invocation reads again but makes no new POST and leaves
            # every output byte unchanged.
            clients[:] = [directory, ledger]
            self.assertEqual(run_sync(config, out_dir), 0)
            self.assertEqual(len(ledger.posts), 1)
            for name, content in first.items():
                with open(os.path.join(out_dir, name), encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), content)


if __name__ == "__main__":
    unittest.main()
