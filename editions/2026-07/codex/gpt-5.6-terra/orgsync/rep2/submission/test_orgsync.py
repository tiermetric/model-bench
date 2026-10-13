"""Unit tests for sync's complete reconciliation behavior."""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from orgsync.config import Config
from orgsync.http_client import Response
from orgsync.sync import run_sync


class FakeClient:
    """A small in-memory implementation of the two documented HTTP APIs."""

    def __init__(self, name, pages, state, rate_limit=False):
        self.name, self.pages, self.state = name, pages, state
        self.rate_limit, self.was_limited = rate_limit, False

    def get(self, path, params=None, headers=None):
        self.state["requests"].append((self.name, "GET", path, params))
        if self.rate_limit and not self.was_limited:
            self.was_limited = True
            return Response(429, {"Retry-After": "0"}, b"", "fake" + path)
        resource = path.rsplit("/", 1)[-1]
        page = params["page"]
        rows = self.pages[resource][page - 1]
        body = {resource: rows, "next_page": page + 1 if page < len(self.pages[resource]) else None}
        return Response(200, {}, json.dumps(body).encode(), "fake" + path)

    def post_json(self, path, payload, headers=None):
        self.state["requests"].append((self.name, "POST", path, payload, headers))
        active = {record["owner"] for page in self.pages["accounts"] for record in page
                  if isinstance(record, dict) and isinstance(record.get("id"), str)
                  and isinstance(record.get("owner"), str) and record.get("active") is True}
        if payload["owner"] not in active:
            return Response(422, {}, b"", "fake" + path)
        self.pages["summaries"] = [self.pages["summaries"][0] + [payload]]
        return Response(201, {}, b"", "fake" + path)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.directory_pages = {"items": [[
            {"id": "a", "owner": "kim", "status": "open", "points": 3},
            {"id": "a", "owner": "kim", "status": "open", "points": 3},
            {"id": "broken", "owner": "kim", "status": "open", "points": True},
        ], [
            {"id": "b", "owner": "kim", "status": "done", "points": 2},
            {"id": "c", "owner": "lost", "status": "open", "points": 5},
        ]]}
        self.ledger_pages = {"accounts": [[
            {"id": "1", "owner": "kim", "active": True},
            {"id": "2", "owner": "gone", "active": False},
            {"id": "bad", "owner": "nope", "active": 1},
        ], [{"id": "3", "owner": "empty", "active": True}]],
            "summaries": [[{"owner": "already"}]]}
        self.state = {"requests": []}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _lines(self, name):
        with open(os.path.join(self.tmp.name, name), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def _bytes(self, name):
        with open(os.path.join(self.tmp.name, name), "rb") as handle:
            return handle.read()

    def test_reconciles_malformed_orphans_rate_limits_and_second_run(self):
        directory = FakeClient("directory", self.directory_pages, self.state, rate_limit=True)
        ledger = FakeClient("ledger", self.ledger_pages, self.state)
        with patch("orgsync.sync.HttpClient", side_effect=[directory, ledger]), \
             patch("orgsync.sync.time.sleep") as sleep:
            self.assertEqual(run_sync(Config("directory", "ledger"), self.tmp.name), 0)
            sleep.assert_called_once_with(0.0)

        posts = [request[3] for request in self.state["requests"] if request[1] == "POST"]
        self.assertEqual(posts, [
            {"owner": "empty", "open_items": 0, "done_items": 0, "total_points": 0},
            {"owner": "kim", "open_items": 1, "done_items": 1, "total_points": 5},
        ])
        self.assertEqual(self._lines("orphans.jsonl"), [
            {"id": "c", "owner": "lost", "status": "open", "points": 5}])
        self.assertEqual(self._lines("errors.jsonl"), [
            {"service": "ledger", "record": {"id": "bad", "owner": "nope", "active": 1}},
            {"service": "directory", "record": {"id": "broken", "owner": "kim", "status": "open", "points": True}},
        ])
        first_output = {name: self._bytes(name)
                        for name in ("errors.jsonl", "orphans.jsonl", "summary.jsonl")}
        posts_before = len(posts)
        directory = FakeClient("directory", self.directory_pages, self.state)
        ledger = FakeClient("ledger", self.ledger_pages, self.state)
        with patch("orgsync.sync.HttpClient", side_effect=[directory, ledger]):
            self.assertEqual(run_sync(Config("directory", "ledger"), self.tmp.name), 0)
        self.assertEqual(posts_before, len([r for r in self.state["requests"] if r[1] == "POST"]))
        self.assertEqual(first_output, {name: self._bytes(name) for name in first_output})


if __name__ == "__main__":
    unittest.main()
