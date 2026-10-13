"""Behavioral tests for the reconciliation command (standard library only)."""

import json
import os
import tempfile
import unittest
from unittest import mock

from orgsync.config import Config
from orgsync.http_client import Response
from orgsync.sync import run_sync


def response(status, body, url, headers=None):
    return Response(status, headers or {}, json.dumps(body).encode("utf-8"), url)


class Service:
    def __init__(self, pages, rate_limits=None):
        self.pages = pages
        self.rate_limits = dict(rate_limits or {})
        self.requests = []
        self.summaries = list(pages.get("/v1/summaries", {}).get(1, {}).get(
            "summaries", []))

    def get(self, path, params=None, headers=None):
        page = params["page"]
        self.requests.append(("GET", path, page))
        key = ("GET", path, page)
        if self.rate_limits.get(key, 0):
            self.rate_limits[key] -= 1
            return response(429, {}, path, {"Retry-After": "0"})
        body = self.pages[path][page]
        if path == "/v1/summaries":
            body = {"summaries": list(self.summaries), "next_page": None}
        return response(200, body, path)

    def post_json(self, path, payload, headers=None):
        self.requests.append(("POST", path, dict(payload), dict(headers or {})))
        key = ("POST", payload["owner"])
        if self.rate_limits.get(key, 0):
            self.rate_limits[key] -= 1
            return response(429, {}, path, {"Retry-After": "0"})
        self.summaries.append(dict(payload))
        return response(201, {"accepted": True}, path)


class FakeClientFactory:
    def __init__(self, directory, ledger):
        self.services = {"directory": directory, "ledger": ledger}

    def __call__(self, url):
        return self.services[url]


def read_jsonl(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


class SyncBehaviorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "out")

    def test_reconciles_pages_deduplicates_and_is_idempotent(self):
        duplicate = {"id": "i1", "owner": "kim", "status": "open",
                     "points": 3, "extra": "kept"}
        directory = Service({
            "/v1/items": {
                1: {"items": [duplicate, {"id": "bad", "owner": "kim",
                                          "status": "open", "points": True}],
                    "next_page": 2},
                2: {"items": [duplicate,
                              {"id": "i2", "owner": "kim", "status": "done",
                               "points": 2},
                              {"id": "i3", "owner": "inactive",
                               "status": "open", "points": 8},
                              {"id": "i4", "owner": "unknown",
                               "status": "done", "points": 1}],
                    "next_page": None},
            }
        }, {("GET", "/v1/items", 1): 1})
        bad_account = {"id": "broken", "owner": "ghost", "active": 1}
        bad_summary = {"owner": "broken", "open_items": "one",
                       "done_items": 0, "total_points": 0}
        ledger = Service({
            "/v1/accounts": {
                1: {"accounts": [{"id": "a1", "owner": "kim", "active": True},
                                  {"id": "a2", "owner": "lee", "active": True}],
                    "next_page": 2},
                2: {"accounts": [{"id": "a3", "owner": "inactive",
                                  "active": False}, bad_account],
                    "next_page": None},
            },
            "/v1/summaries": {
                1: {"summaries": [bad_summary], "next_page": None},
            },
        }, {("POST", "kim"): 1})
        factory = FakeClientFactory(directory, ledger)

        with mock.patch("orgsync.sync.HttpClient", factory), \
                mock.patch("orgsync.sync.time.sleep") as sleep:
            self.assertEqual(run_sync(Config("directory", "ledger"), self.out), 0)
            first = {
                name: read_bytes(os.path.join(self.out, name))
                for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")
            }
            posts_after_first = [r for r in ledger.requests if r[0] == "POST"]
            self.assertEqual(run_sync(Config("directory", "ledger"), self.out), 0)
            second = {
                name: read_bytes(os.path.join(self.out, name))
                for name in first
            }

        self.assertEqual(first, second)
        self.assertEqual(len(posts_after_first), 3)  # kim retry plus lee
        self.assertEqual(len([r for r in ledger.requests if r[0] == "POST"]), 3)
        self.assertEqual(sleep.call_count, 2)  # one Directory and one Ledger 429
        self.assertCountEqual(read_jsonl(os.path.join(self.out, "summary.jsonl")), [
            {"owner": "kim", "open_items": 1, "done_items": 1,
             "total_points": 5},
            {"owner": "lee", "open_items": 0, "done_items": 0,
             "total_points": 0},
        ])
        self.assertCountEqual(read_jsonl(os.path.join(self.out, "orphans.jsonl")), [
            {"id": "i3", "owner": "inactive", "status": "open", "points": 8},
            {"id": "i4", "owner": "unknown", "status": "done", "points": 1},
        ])
        self.assertCountEqual(read_jsonl(os.path.join(self.out, "errors.jsonl")), [
            {"service": "directory",
             "record": {"id": "bad", "owner": "kim", "status": "open",
                        "points": True}},
            {"service": "ledger", "record": bad_account},
            {"service": "ledger", "record": bad_summary},
        ])

    def test_existing_summary_prevents_post(self):
        directory = Service({
            "/v1/items": {1: {"items": [], "next_page": None}},
        })
        ledger = Service({
            "/v1/accounts": {1: {
                "accounts": [{"id": "a", "owner": "kim", "active": True}],
                "next_page": None}},
            "/v1/summaries": {1: {
                "summaries": [{"owner": "kim", "open_items": 9,
                               "done_items": 9, "total_points": 9}],
                "next_page": None}},
        })
        with mock.patch("orgsync.sync.HttpClient",
                        FakeClientFactory(directory, ledger)):
            self.assertEqual(run_sync(Config("directory", "ledger"), self.out), 0)
        self.assertFalse(any(request[0] == "POST" for request in ledger.requests))
        self.assertEqual(read_jsonl(os.path.join(self.out, "summary.jsonl")), [])


if __name__ == "__main__":
    unittest.main()
