"""End-to-end tests for the orgsync reconciliation logic."""

import json
import os
import tempfile
import unittest
from unittest import mock

from orgsync.config import Config
from orgsync.http_client import Response
from orgsync.sync import run_sync


class FakeService:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []
        self.limited = set()
        self.accepted = []

    @staticmethod
    def response(status, body, headers=None):
        return Response(status, headers or {}, json.dumps(body).encode(), "fake")

    def get(self, path, params=None, headers=None):
        page = params["page"]
        self.requests.append(("GET", path, page))
        marker = (path, page)
        # Rate-limit the first request to the first endpoint on each service.
        if not self.limited:
            self.limited.add(marker)
            return self.response(429, {}, {"Retry-After": "0"})
        return self.response(200, self.routes[path][page])

    def post_json(self, path, payload, headers=None):
        self.requests.append(("POST", path, payload, headers["Idempotency-Key"]))
        active = {
            account["owner"]
            for page in self.routes["/v1/accounts"].values()
            for account in page["accounts"]
            if isinstance(account, dict)
            and isinstance(account.get("id"), str)
            and isinstance(account.get("owner"), str)
            and type(account.get("active")) is bool and account["active"]
        }
        if payload["owner"] not in active:
            return self.response(422, {})
        self.accepted.append(payload)
        return self.response(201, {})


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = FakeService({
            "/v1/items": {
                1: {"items": [
                    {"id": "i1", "owner": "kim", "status": "open", "points": 3,
                     "extra": "kept"},
                    {"id": "bad", "owner": "kim", "status": "open", "points": True},
                    {"id": "orphan", "owner": "nobody", "status": "done", "points": 5},
                ], "next_page": 2},
                2: {"items": [
                    {"id": "i1", "owner": "kim", "status": "open", "points": 3,
                     "extra": "kept"},
                    {"id": "i2", "owner": "kim", "status": "done", "points": 2},
                    {"id": "orphan", "owner": "nobody", "status": "done", "points": 5},
                    {"id": "bad", "owner": "kim", "status": "open", "points": True},
                ], "next_page": None},
            }
        })
        self.ledger = FakeService({
            "/v1/accounts": {
                1: {"accounts": [
                    {"id": "a1", "owner": "kim", "active": True},
                    {"id": "a2", "owner": "lee", "active": True},
                    {"id": "a3", "owner": "off", "active": False},
                    {"id": "broken", "owner": "ghost", "active": 1},
                ], "next_page": None}
            },
            "/v1/summaries": {
                1: {"summaries": [
                    {"owner": "already", "open_items": 0, "done_items": 0,
                     "total_points": 0},
                    {"owner": "bad-summary", "open_items": True, "done_items": 0,
                     "total_points": 0},
                ], "next_page": None}
            },
        })
        self.out = os.path.join(self.tmp.name, "out")

    def client_factory(self, url):
        return self.directory if url == "directory" else self.ledger

    def lines(self, name):
        with open(os.path.join(self.out, name), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def run_reconciliation(self):
        with mock.patch("orgsync.sync.HttpClient", side_effect=self.client_factory):
            return run_sync(Config("directory", "ledger"), self.out)

    def test_full_sync_rate_limits_deduplicates_and_is_idempotent(self):
        self.assertEqual(self.run_reconciliation(), 0)
        self.assertCountEqual(self.lines("summary.jsonl"), [
            {"owner": "kim", "open_items": 1, "done_items": 1, "total_points": 5},
            {"owner": "lee", "open_items": 0, "done_items": 0, "total_points": 0},
        ])
        self.assertEqual(self.lines("orphans.jsonl"), [
            {"id": "orphan", "owner": "nobody", "status": "done", "points": 5}
        ])
        self.assertCountEqual(self.lines("errors.jsonl"), [
            {"service": "directory",
             "record": {"id": "bad", "owner": "kim", "status": "open", "points": True}},
            {"service": "ledger",
             "record": {"id": "broken", "owner": "ghost", "active": 1}},
            {"service": "ledger",
             "record": {"owner": "bad-summary", "open_items": True,
                        "done_items": 0, "total_points": 0}},
        ])
        posts = [request for request in self.ledger.requests if request[0] == "POST"]
        self.assertEqual({request[3] for request in posts}, {"sync-kim", "sync-lee"})
        original = {}
        for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl"):
            with open(os.path.join(self.out, name), "rb") as handle:
                original[name] = handle.read()

        self.ledger.routes["/v1/summaries"][1]["summaries"].extend(
            self.ledger.accepted)
        self.assertEqual(self.run_reconciliation(), 0)
        self.assertEqual(
            len([request for request in self.ledger.requests if request[0] == "POST"]), 2)
        for name, content in original.items():
            with self.subTest(name=name):
                with open(os.path.join(self.out, name), "rb") as handle:
                    self.assertEqual(handle.read(), content)


if __name__ == "__main__":
    unittest.main()
