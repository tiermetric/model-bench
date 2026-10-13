"""Tests for the `sync` command: behavior end to end, against local mocks.

The fixtures here are small scriptable HTTP services on 127.0.0.1 built
from the standard library, plus one no-socket test using an injected
opener. They cover the contract of `run_sync`: pagination, the ledger
deciding who counts, orphans, malformed records, posting only what the
ledger is missing, never sending a rejected write, honoring 429s, and
idempotence across runs.
"""

import email.message
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from orgsync.config import Config
from orgsync.sync import ServiceClient, run_sync

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


def read_jsonl(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class FixtureHandler(BaseHTTPRequestHandler):
    """Thin HTTP shim; every decision lives on the server object."""

    server_version = "orgsync-test/1.0"

    def log_message(self, format, *args):
        pass

    def _send(self, status, payload, headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.serve_get(self)

    def do_POST(self):
        self.server.serve_post(self)


class MockService(ThreadingHTTPServer):
    """One scriptable service: pages of records, logged requests, limits.

    `collections` maps a path like "/v1/items" to the list of records the
    service serves, `page_size` of them per page. `rate_limit` maps a path
    to a number: the first n GETs of that path are answered 429 with
    `Retry-After: retry_after`. The ledger side adds POST /v1/summaries
    with the idempotency semantics of the real service.
    """

    daemon_threads = True

    def __init__(self, collections, page_size=4, rate_limit=None,
                 retry_after="0"):
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.collections = collections
        self.page_size = page_size
        self.rate_limit = dict(rate_limit or {})
        self.retry_after = retry_after
        self.requests = []
        self.post_results = []
        self.summaries = {}
        self._lock = threading.Lock()
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        self._thread = thread

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server_address[1]

    def close(self):
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=5)

    def _pages(self, records):
        pages = [records[i:i + self.page_size]
                 for i in range(0, len(records), self.page_size)] or [[]]
        return pages

    def _serve_collection(self, handler, path, key, records):
        query = parse_qs(urlparse(handler.path).query)
        page = int(query.get("page", ["1"])[0])
        with self._lock:
            self.requests.append({"method": "GET", "path": path,
                                  "page": page, "key": None, "body": None})
            budget = self.rate_limit.get(path, 0)
            if budget > 0:
                self.rate_limit[path] = budget - 1
                limited = True
            else:
                limited = False
        if limited:
            handler._send(429, {"error": "slow down"},
                          {"Retry-After": self.retry_after})
            return
        pages = self._pages(records)
        if not 1 <= page <= len(pages):
            handler._send(404, {"error": "no such page"})
            return
        handler._send(200, {key: pages[page - 1],
                            "next_page": page + 1 if page < len(pages) else None})

    def _active_owners(self):
        owners = set()
        for record in self.collections.get("/v1/accounts", []):
            if (isinstance(record, dict)
                    and isinstance(record.get("id"), str)
                    and isinstance(record.get("owner"), str)
                    and isinstance(record.get("active"), bool)
                    and record["active"]):
                owners.add(record["owner"])
        return owners

    def serve_get(self, handler):
        path = urlparse(handler.path).path
        if path == "/v1/items":
            self._serve_collection(handler, path, "items",
                                   self.collections[path])
        elif path == "/v1/accounts":
            self._serve_collection(handler, path, "accounts",
                                   self.collections[path])
        elif path == "/v1/summaries":
            self._serve_collection(handler, path, "summaries",
                                   self.summary_records())
        else:
            handler._send(404, {"error": "not found"})

    def serve_post(self, handler):
        length = int(handler.headers.get("Content-Length", "0"))
        body = json.loads(handler.rfile.read(length) or b"null")
        key = handler.headers.get("Idempotency-Key")
        path = urlparse(handler.path).path
        with self._lock:
            self.requests.append({"method": "POST", "path": path,
                                  "page": None, "key": key, "body": body})
            budget = self.rate_limit.get(path, 0)
            if budget > 0:
                self.rate_limit[path] = budget - 1
                status = None
            elif path != "/v1/summaries" or not isinstance(body, dict):
                status = 400
            elif body.get("owner") not in self._active_owners():
                status = 422
            elif key in self.summaries:
                status = 200 if self.summaries[key] == body else 409
            else:
                self.summaries[key] = body
                status = 201
            if status is not None:
                self.post_results.append(status)
        if status is None:
            handler._send(429, {"error": "slow down"},
                          {"Retry-After": self.retry_after})
        else:
            handler._send(status, {"status": status})

    def summary_records(self):
        return list(self.summaries.values())


def make_services(items, accounts, summaries=None, **kwargs):
    directory = MockService({"/v1/items": items}, **kwargs)
    ledger = MockService({"/v1/accounts": accounts}, **kwargs)
    for body in (summaries or []):
        ledger.summaries["seed-%s" % body["owner"]] = body
    return directory, ledger


BASE_ITEMS = [
    {"id": "item-101", "owner": "amara", "status": "open", "points": 3},
    {"id": "item-102", "owner": "amara", "status": "done", "points": 2},
    {"id": "item-103", "owner": "bo", "status": "open", "points": 5},
    {"id": "item-104", "owner": "chen", "status": "open", "points": 8},
    {"id": "item-105", "owner": "eli", "status": "done", "points": 1,
     "note": "extra fields are fine"},
    {"id": "item-106", "owner": "zara", "status": "open", "points": 4},
    {"id": "item-107", "owner": "fen", "status": "open", "points": 2},
    {"id": "item-108", "owner": "bo", "status": "done", "points": 1},
    {"id": "item-103", "owner": "bo", "status": "open", "points": 5},
    {"id": "item-109", "owner": "amara", "status": "open"},
    {"id": "item-110", "owner": "amara", "status": "open", "points": "7"},
    {"id": "item-111", "owner": "eli", "status": "open", "points": 2},
    {"id": "item-112", "owner": "bo", "status": "open", "points": -1},
    {"id": "item-113", "owner": "amara", "status": "done", "points": True},
    {"id": "item-114", "owner": 42, "status": "open", "points": 6},
    {"id": "item-115", "owner": "bo", "status": "sideways", "points": 6},
    {"id": 116, "owner": "bo", "status": "open", "points": 6},
]

BASE_ACCOUNTS = [
    {"id": "acct-01", "owner": "amara", "active": True},
    {"id": "acct-02", "owner": "bo", "active": True},
    {"id": "acct-03", "owner": "chen", "active": False},
    {"id": "acct-04", "owner": "dee", "active": True},
    {"id": "acct-05", "owner": "eli", "active": True},
    {"id": "acct-06", "owner": "fen", "active": "yes"},
    {"id": "acct-07", "owner": "gus", "active": False},
]

EXPECTED_SUMMARIES = {
    "amara": {"owner": "amara", "open_items": 1, "done_items": 1,
              "total_points": 5},
    "bo": {"owner": "bo", "open_items": 1, "done_items": 1,
           "total_points": 6},
    "dee": {"owner": "dee", "open_items": 0, "done_items": 0,
            "total_points": 0},
    "eli": {"owner": "eli", "open_items": 1, "done_items": 1,
            "total_points": 3},
}

EXPECTED_ORPHANS = [
    {"id": "item-104", "owner": "chen", "status": "open", "points": 8},
    {"id": "item-106", "owner": "zara", "status": "open", "points": 4},
    {"id": "item-107", "owner": "fen", "status": "open", "points": 2},
]


class SyncTestCase(unittest.TestCase):
    """One shared scenario; each test adds its own question."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "out")
        directory, ledger = make_services(BASE_ITEMS, BASE_ACCOUNTS)
        self.directory, self.ledger = directory, ledger
        self.addCleanup(directory.close)
        self.addCleanup(ledger.close)
        self.config = Config(directory_url=directory.url, ledger_url=ledger.url)

    def path(self, name):
        return os.path.join(self.out, name)

    def test_returns_zero_and_writes_the_three_files(self):
        self.assertEqual(run_sync(self.config, self.out), 0)
        for name in ("orphans.jsonl", "errors.jsonl", "summary.jsonl"):
            self.assertTrue(os.path.isfile(self.path(name)), name)

    def test_reads_every_page_of_every_collection(self):
        self.directory.page_size = self.ledger.page_size = 3
        run_sync(self.config, self.out)
        pages = {(r["path"], r["page"]) for r in self.directory.requests}
        self.assertEqual(pages, {("/v1/items", 1), ("/v1/items", 2),
                                 ("/v1/items", 3), ("/v1/items", 4),
                                 ("/v1/items", 5), ("/v1/items", 6)})
        pages = {(r["path"], r["page"]) for r in self.ledger.requests
                 if r["method"] == "GET"}
        self.assertTrue({("/v1/accounts", 1), ("/v1/accounts", 2),
                         ("/v1/accounts", 3), ("/v1/summaries", 1)} <= pages)

    def test_summaries_follow_the_ledger_and_the_arithmetic(self):
        run_sync(self.config, self.out)
        lines = read_jsonl(self.path("summary.jsonl"))
        self.assertEqual({entry["owner"]: entry for entry in lines},
                         EXPECTED_SUMMARIES)

    def test_orphans_are_written_once_as_received(self):
        run_sync(self.config, self.out)
        self.assertEqual(read_jsonl(self.path("orphans.jsonl")), EXPECTED_ORPHANS)

    def test_malformed_records_are_reported_once_and_never_counted(self):
        run_sync(self.config, self.out)
        entries = read_jsonl(self.path("errors.jsonl"))
        self.assertEqual(len(entries), len({json.dumps(e, sort_keys=True)
                                            for e in entries}))
        by_service = {}
        for entry in entries:
            self.assertEqual(sorted(entry), ["record", "service"])
            by_service.setdefault(entry["service"], []).append(entry["record"])
        self.assertEqual(sorted(by_service), ["directory", "ledger"])
        self.assertEqual(by_service["ledger"], [{"id": "acct-06",
                                                 "owner": "fen",
                                                 "active": "yes"}])
        self.assertEqual(sorted(str(item["id"]) for item in
                                by_service["directory"]),
                         ["116", "item-109", "item-110", "item-112",
                          "item-113", "item-114", "item-115"])
        owners = {entry["owner"] for entry in
                  read_jsonl(self.path("summary.jsonl"))}
        self.assertNotIn("fen", owners)

    def test_posts_only_missing_summaries_with_idempotency_keys(self):
        run_sync(self.config, self.out)
        posts = [r for r in self.ledger.requests if r["method"] == "POST"]
        self.assertEqual([(r["key"], r["body"]) for r in posts],
                         [(("sync-%s" % o), EXPECTED_SUMMARIES[o])
                          for o in sorted(EXPECTED_SUMMARIES)])
        self.assertEqual(sorted(self.ledger.summaries.values(),
                                key=lambda b: b["owner"]),
                         [EXPECTED_SUMMARIES[o]
                          for o in sorted(EXPECTED_SUMMARIES)])

    def test_a_passing_run_sends_no_rejected_write(self):
        run_sync(self.config, self.out)
        self.assertEqual(self.ledger.post_results, [201] * 4)

    def test_existing_summaries_are_not_posted_again(self):
        self.ledger.summaries["seed-amara"] = EXPECTED_SUMMARIES["amara"]
        run_sync(self.config, self.out)
        posts = [r for r in self.ledger.requests if r["method"] == "POST"]
        self.assertEqual([r["key"] for r in posts],
                         ["sync-bo", "sync-dee", "sync-eli"])
        self.assertEqual({e["owner"]: e for e in
                          read_jsonl(self.path("summary.jsonl"))},
                         EXPECTED_SUMMARIES)

    def test_second_run_is_a_no_write_no_change_pass(self):
        run_sync(self.config, self.out)
        posts_after_first = [r for r in self.ledger.requests
                             if r["method"] == "POST"]
        before = {name: open(self.path(name), "rb").read()
                  for name in ("orphans.jsonl", "errors.jsonl",
                               "summary.jsonl")}
        run_sync(self.config, self.out)
        posts_after_second = [r for r in self.ledger.requests
                              if r["method"] == "POST"]
        self.assertEqual(posts_after_second, posts_after_first)
        for name, content in before.items():
            self.assertEqual(open(self.path(name), "rb").read(), content)

    def test_out_dir_is_created_even_when_everything_is_empty(self):
        empty_directory = MockService({"/v1/items": []})
        self.addCleanup(empty_directory.close)
        empty_ledger = MockService({"/v1/accounts": []})
        self.addCleanup(empty_ledger.close)
        config = Config(directory_url=empty_directory.url,
                        ledger_url=empty_ledger.url)
        self.assertEqual(run_sync(config, self.out), 0)
        self.assertEqual(read_jsonl(self.path("summary.jsonl")), [])
        self.assertEqual(read_jsonl(self.path("orphans.jsonl")), [])
        self.assertEqual(read_jsonl(self.path("errors.jsonl")), [])

    def test_rate_limited_requests_are_retried_not_treated_as_data(self):
        self.directory.rate_limit = {"/v1/items": 2}
        self.directory.retry_after = "0"
        run_sync(self.config, self.out)
        item_gets = [r for r in self.directory.requests
                     if r["path"] == "/v1/items"]
        self.assertEqual(len(item_gets), 5 + 2)
        self.assertEqual({e["owner"]: e for e in
                          read_jsonl(self.path("summary.jsonl"))},
                         EXPECTED_SUMMARIES)

    def test_rate_limited_post_is_retried_after_retry_after(self):
        self.ledger.rate_limit = {"/v1/summaries": 3}
        self.ledger.retry_after = "0"
        run_sync(self.config, self.out)
        self.assertEqual(self.ledger.post_results, [201] * 4)
        self.assertEqual(len(self.ledger.summaries), 4)


class RateLimitWaitTest(unittest.TestCase):
    """No sockets: the client must sit out Retry-After before retrying."""

    class FakeHTTPResponse:
        def __init__(self, status, headers=None):
            self.status = status
            self.headers = email.message.Message()
            for key, value in (headers or {}).items():
                self.headers[key] = value

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    class FakeOpener:
        def __init__(self, *outcomes):
            self.outcomes, self.requests = list(outcomes), []

        def open(self, request, timeout=None):
            self.requests.append(request)
            return self.outcomes.pop(0)

    def test_waits_at_least_retry_after_then_retries_same_request(self):
        opener = self.FakeOpener(
            self.FakeHTTPResponse(429, {"Retry-After": "2"}),
            self.FakeHTTPResponse(200),
        )
        sleeps = []
        client = ServiceClient("http://example.test", opener=opener,
                               sleep=sleeps.append)
        response = client.request("GET", "/v1/items", params={"page": 1})
        self.assertEqual(response.status, 200)
        self.assertEqual(len(opener.requests), 2)
        self.assertEqual(opener.requests[0].full_url,
                         opener.requests[1].full_url)
        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], 2.0)

    def test_unparseable_retry_after_falls_back_to_one_second(self):
        opener = self.FakeOpener(
            self.FakeHTTPResponse(429, {"Retry-After": "soon"}),
            self.FakeHTTPResponse(200),
        )
        sleeps = []
        client = ServiceClient("http://example.test", opener=opener,
                               sleep=sleeps.append)
        self.assertEqual(client.request("GET", "/v1/items").status, 200)
        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], 1.0)


class CliTest(unittest.TestCase):
    """`python3 -m orgsync sync` against the mocks, through the real CLI."""

    def test_sync_command_exits_zero_and_writes_outputs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = os.path.join(tmp.name, "out")
        directory, ledger = make_services(BASE_ITEMS, BASE_ACCOUNTS)
        self.addCleanup(directory.close)
        self.addCleanup(ledger.close)
        config_path = os.path.join(tmp.name, "config.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump({"directory_url": directory.url,
                       "ledger_url": ledger.url}, handle)
        result = subprocess.run(
            [sys.executable, "-m", "orgsync", "sync",
             "--config", config_path, "--out-dir", out],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = read_jsonl(os.path.join(out, "summary.jsonl"))
        self.assertEqual({e["owner"]: e for e in summary}, EXPECTED_SUMMARIES)


if __name__ == "__main__":
    unittest.main()
