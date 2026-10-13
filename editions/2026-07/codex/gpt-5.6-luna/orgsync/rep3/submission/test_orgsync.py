import json
import os
import tempfile
import unittest
from unittest import mock

from orgsync.config import Config
from orgsync.http_client import Response
from orgsync import sync


class FakeResponse(Response):
    def __init__(self, status, body, headers=None):
        super().__init__(status, {}, json.dumps(body).encode("utf-8"), "fake")
        self.headers = headers or {}


class FakeService:
    def __init__(self, pages):
        self.pages = pages
        self.requests = []

    def get(self, path, params=None):
        self.requests.append(("GET", path, params))
        page = params["page"]
        return FakeResponse(200, self.pages[path][page - 1])

    def post_json(self, path, body, headers=None):
        self.requests.append(("POST", path, body, headers))
        self.pages["/v1/summaries"][0]["summaries"].append(dict(body))
        return FakeResponse(201, {"ok": True})


class SyncTest(unittest.TestCase):
    def test_reconciles_valid_data_and_keeps_second_run_unchanged(self):
        directory = FakeService({
            "/v1/items": [
                {"items": [
                    {"id": "i1", "owner": "kim", "status": "open", "points": 3,
                     "extra": "kept"},
                    {"id": "i1", "owner": "kim", "status": "open", "points": 3},
                    {"id": "i2", "owner": "kim", "status": "done", "points": 2},
                    {"id": "orphan", "owner": "nobody", "status": "open", "points": 8},
                    {"id": "bad", "owner": "kim", "status": "open", "points": -1},
                ], "next_page": 2},
                {"items": [{"id": "i3", "owner": "kim", "status": "open", "points": 4},
                            {"id": "broken", "owner": "kim"}], "next_page": None},
            ]
        })
        ledger = FakeService({
            "/v1/accounts": [
                {"accounts": [
                    {"id": "a1", "owner": "kim", "active": True},
                    {"id": "a2", "owner": "inactive", "active": False},
                    {"id": "bad-account", "owner": "ghost", "active": "yes"},
                ], "next_page": 2},
                {"accounts": [{"id": "a3", "owner": "lee", "active": True}],
                 "next_page": None},
            ],
            "/v1/summaries": [{"summaries": [], "next_page": None}],
        })
        services = {"directory": directory, "ledger": ledger}

        def make_client(url):
            return services["directory" if "directory" in url else "ledger"]

        with tempfile.TemporaryDirectory() as out_dir, mock.patch.object(
                sync, "HttpClient", side_effect=make_client):
            config = Config("http://directory", "http://ledger")
            self.assertEqual(sync.run_sync(config, out_dir), 0)
            def read_outputs():
                result = {}
                for name in ("errors.jsonl", "orphans.jsonl", "summary.jsonl"):
                    with open(os.path.join(out_dir, name), encoding="utf-8") as handle:
                        result[name] = handle.read()
                return result

            first = read_outputs()

            # A second pass sees the summaries posted by the first pass.
            self.assertEqual(sync.run_sync(config, out_dir), 0)
            second = read_outputs()
            self.assertEqual(first, second)

        summaries = [json.loads(line) for line in first["summary.jsonl"].splitlines()]
        self.assertEqual(sorted(summaries, key=lambda x: x["owner"]), [
            {"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9},
            {"owner": "lee", "open_items": 0, "done_items": 0, "total_points": 0},
        ])
        orphans = [json.loads(line) for line in first["orphans.jsonl"].splitlines()]
        self.assertEqual(orphans[0]["id"], "orphan")
        errors = [json.loads(line) for line in first["errors.jsonl"].splitlines()]
        self.assertEqual({entry["service"] for entry in errors}, {"directory", "ledger"})
        self.assertEqual(len(ledger.requests), 8)  # account/summary reads plus two first-pass posts
        self.assertEqual(sum(request[0] == "POST" for request in ledger.requests), 2)

    def test_retries_after_429(self):
        class Client:
            def __init__(self):
                self.calls = 0

            def get(self, path, params=None):
                self.calls += 1
                if self.calls == 1:
                    return FakeResponse(429, {}, {"Retry-After": "0"})
                return FakeResponse(200, {"items": [], "next_page": None})

        client = Client()
        limited = sync._RateLimitedClient(client)
        response = limited.get("/v1/items", {"page": 1})
        self.assertEqual(response.status, 200)
        self.assertEqual(client.calls, 2)


if __name__ == "__main__":
    unittest.main()
