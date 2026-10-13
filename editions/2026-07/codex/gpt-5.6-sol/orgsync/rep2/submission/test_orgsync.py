"""End-to-end tests for the orgsync reconciliation command."""

import json
import os
import tempfile
import unittest
from email.message import Message
from unittest import mock

from orgsync.config import Config
from orgsync.http_client import Response
from orgsync.sync import run_sync


def read_jsonl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class FakeBackend:
    def __init__(self):
        self.requests = []
        self.rate_limited = set()
        self.summaries = [
            {"owner": "kim", "open_items": 8, "done_items": 0, "total_points": 8},
            {"owner": 9, "open_items": 0, "done_items": 0, "total_points": 0},
        ]


class FakeClient:
    backends = {}

    def __init__(self, base_url):
        self.base_url = base_url
        self.backend = self.backends[base_url]

    def _response(self, status, body, path, retry_after=None):
        headers = Message()
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        return Response(status, headers, json.dumps(body).encode(), self.base_url + path)

    def get(self, path, params=None):
        page = params["page"]
        backend = self.backend
        backend.requests.append(("GET", path, page))
        marker = (path, page)
        if self.base_url == "directory" and marker == ("/v1/items", 1):
            if marker not in backend.rate_limited:
                backend.rate_limited.add(marker)
                return self._response(429, {}, path, "0")
        if path == "/v1/items":
            pages = {
                1: {"items": [
                    {"id": "i1", "owner": "ann", "status": "open", "points": 3,
                     "extra": "kept"},
                    {"id": "bad", "owner": "ann", "status": "open", "points": True},
                    {"id": "orphan", "owner": "zoe", "status": "done", "points": 7},
                ], "next_page": 2},
                2: {"items": [
                    {"id": "i1", "owner": "ann", "status": "open", "points": 3,
                     "extra": "kept"},
                    {"id": "i2", "owner": "ann", "status": "done", "points": 2},
                ], "next_page": None},
            }
            return self._response(200, pages[page], path)
        if path == "/v1/accounts":
            pages = {
                1: {"accounts": [
                    {"id": "a1", "owner": "ann", "active": True},
                    {"id": "a2", "owner": "kim", "active": True},
                    {"id": "a3", "owner": "zoe", "active": False},
                ], "next_page": 2},
                2: {"accounts": [
                    {"id": "a4", "owner": "bob", "active": True},
                    {"id": "bad", "owner": "ghost", "active": 1},
                ], "next_page": None},
            }
            return self._response(200, pages[page], path)
        return self._response(
            200, {"summaries": backend.summaries, "next_page": None}, path
        )

    def post_json(self, path, payload, headers=None):
        backend = self.backend
        backend.requests.append(("POST", path, headers["Idempotency-Key"], payload))
        marker = ("post", payload["owner"])
        if payload["owner"] == "bob" and marker not in backend.rate_limited:
            backend.rate_limited.add(marker)
            return self._response(429, {}, path, "0")
        backend.summaries.append(payload)
        return self._response(201, {"accepted": True}, path)


class SyncEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.directory = FakeBackend()
        self.ledger = FakeBackend()
        FakeClient.backends = {"directory": self.directory, "ledger": self.ledger}
        patcher = mock.patch("orgsync.sync.HttpClient", FakeClient)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "out")
        self.config = Config("directory", "ledger")

    def test_reconciles_pages_malformed_duplicates_orphans_retries_and_is_idempotent(self):
        self.assertEqual(run_sync(self.config, self.out), 0)

        summaries = read_jsonl(os.path.join(self.out, "summary.jsonl"))
        self.assertCountEqual(
            summaries,
            [
                {"owner": "ann", "open_items": 1, "done_items": 1,
                 "total_points": 5},
                {"owner": "bob", "open_items": 0, "done_items": 0,
                 "total_points": 0},
            ],
        )
        self.assertEqual(
            read_jsonl(os.path.join(self.out, "orphans.jsonl")),
            [{"id": "orphan", "owner": "zoe", "status": "done", "points": 7}],
        )
        errors = read_jsonl(os.path.join(self.out, "errors.jsonl"))
        self.assertEqual(len(errors), 3)
        self.assertIn(
            {"service": "directory",
             "record": {"id": "bad", "owner": "ann", "status": "open",
                        "points": True}},
            errors,
        )

        first_files = {}
        for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl"):
            with open(os.path.join(self.out, name), "rb") as handle:
                first_files[name] = handle.read()
        posts_before = sum(request[0] == "POST" for request in self.ledger.requests)
        self.assertEqual(posts_before, 3)  # two accepted posts plus one 429 retry

        self.assertEqual(run_sync(self.config, self.out), 0)
        posts_after = sum(request[0] == "POST" for request in self.ledger.requests)
        self.assertEqual(posts_after, posts_before)
        for name, content in first_files.items():
            with open(os.path.join(self.out, name), "rb") as handle:
                self.assertEqual(handle.read(), content)


if __name__ == "__main__":
    unittest.main()
