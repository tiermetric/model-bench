"""Tests for the orgsync `sync` command and the rules it implements.

The suite is self-contained: it starts its own in-process mock services
(plain `http.server` sockets on ephemeral ports) that implement the contracts
the two real services follow — page-following GETs, the Idempotency-Key POST
semantics (201 accepted / 200 replay / 409 same key different body / 422
inactive owner), and 429s carrying `Retry-After`. Each test then drives
`run_sync` or the command line against those mocks and checks the behaviors
the spec pins down: what is counted, what lands in `out/*.jsonl`, which
requests are made, that rate limits are honored, and that a second run
issues no writes and changes nothing.
"""

from __future__ import annotations

import email.message
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse

from orgsync.config import Config
from orgsync.http_client import HttpClient, Response
from orgsync.sync import (DEFAULT_RETRY_AFTER_SECONDS, IDEMPOTENCY_PREFIX,
                          Throttle, retry_after_seconds, run_sync,
                          well_formed_account, well_formed_item)

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

PAGE_SIZE = 2  # records per page in the mock services' listings


def canonical(record: Any) -> str:
    """A stable identity for a JSON record, for order-free comparisons."""
    return json.dumps(record, sort_keys=True)


# ---------------------------------------------------------------------------
# A mock service implementing the contracts both real services follow.
# ---------------------------------------------------------------------------

class Fixture:
    """State and behavior of one mock service.

    `pages` maps GET paths to flat record lists, which the service serves in
    chunks of `page_size`; the summaries listing is served dynamically from
    the summaries POSTed so far. `rate_every` answers every Nth request to
    this service with a 429 carrying `Retry-After: rate_after`; `rate_max`
    bounds how many 429s are served (0 means never stop). A 429 never changes
    service state. POST /v1/summaries enforces the contract: only an active
    owner is accepted (422 otherwise), a fresh key+body is 201, the same key
    with the same body is a 200 replay, the same key with a different body is
    409. Every request is logged with the monotonic time it arrived and
    finished, so tests can check the client honored the pause.
    """

    def __init__(self, name: str, pages: Optional[Dict[str, List[Any]]] = None,
                 active_owners: Optional[Set[str]] = None,
                 pre_summaries: Optional[List[Dict[str, Any]]] = None,
                 rate_every: int = 0, rate_after: float = 1.0,
                 rate_max: int = 0) -> None:
        self.name = name
        self.pages = pages or {}
        self.active_owners = active_owners or set()
        self.summaries: Dict[str, str] = {}  # idempotency key -> canonical body
        for body in pre_summaries or []:
            self.summaries["sync-" + str(body.get("owner"))] = canonical(body)
        self.rate_every = rate_every
        self.rate_after = rate_after
        self.rate_max = rate_max  # 0: never stop serving 429s; else serve this many
        self.rate_served = 0
        self.requests: List[Dict[str, Any]] = []
        self.rejected: List[Tuple[int, Optional[str], Any]] = []
        self._served = 0
        self._lock = threading.Lock()

    def handle(self, method: str, path: str, query: Dict[str, List[str]],
               headers: Any, raw_body: bytes) -> Tuple[int, Optional[Dict[str, Any]]]:
        with self._lock:
            started = time.monotonic()
            self._served += 1
            due = self.rate_every > 0 and self._served % self.rate_every == 0
            budget_left = self.rate_max <= 0 or self.rate_served < self.rate_max
            limited = due and budget_left
            if limited:
                self.rate_served += 1
            if limited:
                status, payload = 429, None
            else:
                status, payload = self._dispatch(method, path, query, headers, raw_body)
            self.requests.append({
                "method": method, "path": path, "query": dict(query),
                "status": status, "key": headers.get("Idempotency-Key"),
                "body": self._parse_body(raw_body) if raw_body else None,
                "t_in": started, "t_out": time.monotonic(),
            })
            if method == "POST" and status not in (200, 201, 429):
                self.rejected.append((status, headers.get("Idempotency-Key"),
                                      self._parse_body(raw_body)))
            return status, payload

    @staticmethod
    def _parse_body(raw_body: bytes) -> Any:
        try:
            return json.loads(raw_body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return raw_body.decode("utf-8", "replace")

    def _dispatch(self, method, path, query, headers, raw_body):
        if method == "GET":
            return self._get(path, query)
        if method == "POST" and path == "/v1/summaries":
            return self._post_summary(headers, raw_body)
        return 404, {"error": "not found"}

    def _get(self, path: str, query: Dict[str, List[str]]):
        key = {"/v1/accounts": "accounts", "/v1/items": "items",
               "/v1/summaries": "summaries"}.get(path)
        if key is None:
            return 404, {"error": "not found"}
        if path == "/v1/summaries":
            records = [json.loads(body) for body in self.summaries.values()]
        else:
            records = self.pages.get(path)
            if records is None:
                return 404, {"error": "not found"}
        try:
            page = int(query.get("page", ["1"])[0])
        except ValueError:
            return 404, {"error": "bad page"}
        if page < 1:
            return 404, {"error": "bad page"}
        chunks = [records[i:i + PAGE_SIZE]
                  for i in range(0, len(records), PAGE_SIZE)] or [[]]
        index = page - 1
        if index >= len(chunks):
            return 200, {key: [], "next_page": None}
        following = page + 1 if index + 1 < len(chunks) else None
        return 200, {key: chunks[index], "next_page": following}

    def _post_summary(self, headers: Any, raw_body: bytes):
        key = headers.get("Idempotency-Key")
        body = self._parse_body(raw_body)
        owner = body.get("owner") if isinstance(body, dict) else None
        if not isinstance(owner, str) or owner not in self.active_owners:
            return 422, {"error": "no active account for owner"}
        if not isinstance(body, dict) or set(body) != {
                "owner", "open_items", "done_items", "total_points"}:
            return 422, {"error": "bad body"}
        identity = canonical(body)
        if key in self.summaries:
            if self.summaries[key] == identity:
                return 200, body  # replay
            return 409, {"error": "same key, different body"}
        self.summaries[key] = identity
        return 201, body


class MockService:
    """A `Fixture` behind a real HTTP socket on an ephemeral port."""

    def __init__(self, fixture: Fixture) -> None:
        self.fixture = fixture
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                          self._make_handler(fixture))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @staticmethod
    def _make_handler(fixture: Fixture):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output clean
                pass

            def _run(self, method: str) -> None:
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                status, payload = fixture.handle(method, parsed.path, query,
                                                 self.headers, raw)
                data = b"" if payload is None else json.dumps(payload).encode("utf-8")
                self.send_response(status)
                if status == 429:
                    self.send_header("Retry-After", str(fixture.rate_after))
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self._run("GET")

            def do_POST(self) -> None:
                self._run("POST")

        return Handler

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    @property
    def url(self) -> str:
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def requests(self) -> List[Dict[str, Any]]:
        return list(self.fixture.requests)


# ---------------------------------------------------------------------------
# Shared scenario: two pages of accounts (some malformed), four pages of
# items (some malformed, some repeated, some orphaned), one pre-existing
# (stale) summary the run must leave alone.
# ---------------------------------------------------------------------------

def account_records() -> List[Any]:
    return [
        {"id": "acct-01", "owner": "amara", "active": True},
        {"id": "acct-02", "owner": "bo", "active": True},
        {"id": "acct-03", "owner": "chen", "active": False},
        {"id": "acct-04", "owner": "gus", "active": True, "tier": "gold"},
        {"id": "acct-05", "owner": "dee", "active": True},   # no items at all
        {"owner": "no-id", "active": True},                  # malformed: no id
        {"id": "acct-06", "owner": "fen", "active": "yes"},  # malformed: not boolean
        {"id": 7, "owner": "num", "active": True},           # malformed: id not string
        {"id": "acct-08", "owner": "ivy", "active": 1},      # malformed: not boolean
    ]


def item_records() -> List[Any]:
    return [
        {"id": "i-1", "owner": "amara", "status": "open", "points": 3},
        {"id": "i-2", "owner": "amara", "status": "done", "points": 2},
        {"id": "i-3", "owner": "bo", "status": "open", "points": 5},
        {"id": "i-4", "owner": "chen", "status": "open", "points": 8},  # orphan: inactive
        {"id": "i-3", "owner": "bo", "status": "open", "points": 5},    # delivered again
        {"id": "i-5", "owner": "gus", "status": "done", "points": 4,
         "tags": ["urgent"]},                              # extra field is fine
        {"id": "i-6", "owner": "zara", "status": "open", "points": 4,
         "note": "no account at all"},                     # orphan
        {"id": "i-7", "owner": "fen", "status": "open", "points": 2},  # orphan
        {"id": "i-6", "owner": "zara", "status": "open", "points": 4,
         "note": "no account at all"},                     # orphan, delivered again
        {"id": "i-8", "owner": "amara", "status": "open"},       # malformed: no points
        {"id": "i-9", "owner": "bo", "status": "open", "points": -1},  # malformed
        {"id": "i-10", "owner": "bo", "status": "done", "points": True},  # malformed
        {"id": "i-11", "owner": "bo", "status": "sideways", "points": 1},  # malformed
        {"id": "i-11", "owner": "bo", "status": "sideways", "points": 1},  # again
    ]


#: Summaries the first run must post, keyed by idempotency key.
EXPECTED_POSTS = {
    "sync-amara": {"owner": "amara", "open_items": 1, "done_items": 1,
                   "total_points": 5},
    "sync-bo": {"owner": "bo", "open_items": 1, "done_items": 0,
                "total_points": 5},
    "sync-dee": {"owner": "dee", "open_items": 0, "done_items": 0,
                 "total_points": 0},
}
STALE_GUS_SUMMARY = {"owner": "gus", "open_items": 9, "done_items": 9,
                     "total_points": 99}

EXPECTED_ORPHANS = [
    {"id": "i-4", "owner": "chen", "status": "open", "points": 8},
    {"id": "i-6", "owner": "zara", "status": "open", "points": 4,
     "note": "no account at all"},
    {"id": "i-7", "owner": "fen", "status": "open", "points": 2},
]

EXPECTED_ERRORS = [
    {"service": "ledger", "record": {"owner": "no-id", "active": True}},
    {"service": "ledger", "record": {"id": "acct-06", "owner": "fen",
                                     "active": "yes"}},
    {"service": "ledger", "record": {"id": 7, "owner": "num", "active": True}},
    {"service": "ledger", "record": {"id": "acct-08", "owner": "ivy",
                                     "active": 1}},
    {"service": "directory", "record": {"id": "i-8", "owner": "amara",
                                        "status": "open"}},
    {"service": "directory", "record": {"id": "i-9", "owner": "bo",
                                        "status": "open", "points": -1}},
    {"service": "directory", "record": {"id": "i-10", "owner": "bo",
                                        "status": "done", "points": True}},
    {"service": "directory", "record": {"id": "i-11", "owner": "bo",
                                        "status": "sideways", "points": 1}},
]


class ServicesUnderTest:
    """A directory and a ledger mock, plus a config.json pointing at them."""

    def __init__(self, tmp_dir: str, rate_every: int = 0, rate_after: float = 0.5):
        self.directory = MockService(Fixture(
            "directory", pages={"/v1/items": item_records()},
            rate_every=rate_every, rate_after=rate_after))
        self.ledger = MockService(Fixture(
            "ledger", pages={"/v1/accounts": account_records()},
            active_owners={"amara", "bo", "dee", "gus"},
            pre_summaries=[STALE_GUS_SUMMARY],
            rate_every=rate_every, rate_after=rate_after))
        self.config_path = os.path.join(tmp_dir, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump({"directory_url": self.directory.url,
                       "ledger_url": self.ledger.url}, handle)

    def start(self) -> None:
        self.directory.start()
        self.ledger.start()

    def stop(self) -> None:
        self.directory.stop()
        self.ledger.stop()

    def config(self) -> Config:
        return Config(self.directory.url, self.ledger.url)

    def posted_requests(self) -> List[Dict[str, Any]]:
        return [r for r in self.ledger.requests()
                if r["method"] == "POST" and r["status"] in (200, 201)]

    def rejected_requests(self) -> List[Any]:
        return self.ledger.fixture.rejected


def read_jsonl(path: str) -> List[Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def page_count(records: List[Any]) -> int:
    return max(1, math.ceil(len(records) / PAGE_SIZE))


# ---------------------------------------------------------------------------
# End-to-end: the files, the requests, the pauses, and the second run.
# ---------------------------------------------------------------------------

class SyncEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = os.path.join(self.tmp.name, "out")
        self.services = ServicesUnderTest(self.tmp.name, rate_every=3,
                                          rate_after=0.5)
        self.services.start()
        self.addCleanup(self.services.stop)

    def path(self, name: str) -> str:
        return os.path.join(self.out_dir, name)

    def assert_pauses_honored(self, requests, retry_after: float) -> None:
        for first, second in zip(requests, requests[1:]):
            if first["status"] == 429:
                gap = second["t_in"] - first["t_out"]
                self.assertGreaterEqual(
                    gap, retry_after - 0.001,
                    "a request went out %.3fs after a 429 asking for %.3fs"
                    % (gap, retry_after))

    def test_first_run_posts_exactly_what_is_missing_and_writes_the_files(self):
        self.assertEqual(run_sync(self.services.config(), self.out_dir), 0)

        self.assertEqual(self.services.rejected_requests(), [],
                         "no POST may be rejected")

        posted = self.services.posted_requests()
        self.assertEqual({r["key"] for r in posted}, set(EXPECTED_POSTS))
        for r in posted:
            self.assertEqual(r["body"], EXPECTED_POSTS[r["key"]])
            self.assertTrue(r["key"].startswith(IDEMPOTENCY_PREFIX))

        held = {body["owner"]: body for body in
                (json.loads(raw) for raw in
                 self.services.ledger.fixture.summaries.values())}
        for owner, body in [(key[len(IDEMPOTENCY_PREFIX):], body)
                            for key, body in EXPECTED_POSTS.items()]:
            self.assertEqual(held[owner], body)
        self.assertEqual(held["gus"], STALE_GUS_SUMMARY,
                         "an existing summary is left untouched")

        self.assertEqual(
            {canonical(r) for r in read_jsonl(self.path("orphans.jsonl"))},
            {canonical(r) for r in EXPECTED_ORPHANS})
        self.assertEqual(
            {canonical(r) for r in read_jsonl(self.path("errors.jsonl"))},
            {canonical(r) for r in EXPECTED_ERRORS})
        self.assertEqual(
            {canonical(r) for r in read_jsonl(self.path("summary.jsonl"))},
            {canonical(body) for body in EXPECTED_POSTS.values()})

    def test_duplicate_deliveries_are_counted_and_recorded_once(self):
        run_sync(self.services.config(), self.out_dir)
        orphans = read_jsonl(self.path("orphans.jsonl"))
        self.assertEqual(len([r for r in orphans if r["id"] == "i-6"]), 1,
                         "an orphan delivered twice is written once")
        errors = read_jsonl(self.path("errors.jsonl"))
        self.assertEqual(len([r for r in errors
                              if r["record"].get("id") == "i-11"]), 1,
                         "a malformed record delivered twice is written once")
        summaries = read_jsonl(self.path("summary.jsonl"))
        self.assertIn({"owner": "bo", "open_items": 1, "done_items": 0,
                       "total_points": 5}, summaries,
                      "i-3 delivered twice counts once")

    def test_second_run_issues_no_writes_and_changes_nothing(self):
        self.assertEqual(run_sync(self.services.config(), self.out_dir), 0)
        ledger_requests_before = self.services.ledger.requests()
        files_before = {}
        for name in ("orphans.jsonl", "errors.jsonl", "summary.jsonl"):
            with open(self.path(name), "rb") as handle:
                files_before[name] = handle.read()

        self.services.directory.fixture.rate_every = 0
        self.services.ledger.fixture.rate_every = 0
        self.assertEqual(run_sync(self.services.config(), self.out_dir), 0)

        new_requests = self.services.ledger.requests()[len(ledger_requests_before):]
        self.assertEqual([r for r in new_requests if r["method"] == "POST"],
                         [], "the second run must issue no write requests")
        for name, content in files_before.items():
            with open(self.path(name), "rb") as handle:
                self.assertEqual(handle.read(), content,
                                 "%s must have the same content" % name)
        directory_gets = [r["path"] for r in self.services.directory.requests()
                          if r["method"] == "GET"]
        self.assertIn("/v1/items", directory_gets, "everything is read again")

    def test_reads_every_page_and_stops_after_null(self):
        run_sync(self.services.config(), self.out_dir)
        expected = {
            (self.services.directory, "/v1/items"):
                set(range(1, page_count(item_records()) + 1)),
            (self.services.ledger, "/v1/accounts"):
                set(range(1, page_count(account_records()) + 1)),
            # Summaries are read before anything is posted: one empty page.
            (self.services.ledger, "/v1/summaries"): {1},
        }
        for (service, path), pages in expected.items():
            seen = {int(r["query"].get("page", ["0"])[0])
                    for r in service.requests()
                    if r["method"] == "GET" and r["path"] == path}
            self.assertEqual(seen, pages, "%s on %s" % (service.fixture.name, path))

    def test_429_is_answered_with_a_pause_and_a_retry(self):
        run_sync(self.services.config(), self.out_dir)
        for service in (self.services.directory, self.services.ledger):
            requests = service.requests()
            self.assertTrue(any(r["status"] == 429 for r in requests),
                            "%s should have hit its rate limit in this scenario"
                            % service.fixture.name)
            self.assert_pauses_honored(requests, 0.5)
            for index, r in enumerate(requests):
                if r["status"] != 429:
                    continue
                retried = [x for x in requests[index + 1:]
                           if x["method"] == r["method"] and x["path"] == r["path"]
                           and x["query"] == r["query"]]
                self.assertTrue(retried, "a 429 never abandons the work")
                self.assertIn(retried[0]["status"], (200, 201))

    def test_a_paused_ledger_does_not_pause_the_directory(self):
        # The ledger 429s its very first request and asks for a long pause;
        # the directory is never rate limited. Once the ledger finishes its
        # accounts pages, the directory must start immediately — a pause on
        # one service is not inherited by the other.
        other_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(other_tmp.cleanup)
        services = ServicesUnderTest(other_tmp.name)
        services.ledger.fixture.rate_every = 1
        services.ledger.fixture.rate_after = 1.5
        services.ledger.fixture.rate_max = 1
        services.start()
        self.addCleanup(services.stop)

        self.assertEqual(run_sync(services.config(),
                                  os.path.join(other_tmp.name, "out")), 0)
        self.assertEqual({r["key"] for r in services.posted_requests()},
                         set(EXPECTED_POSTS))

        ledger_requests = services.ledger.requests()
        self.assertEqual(ledger_requests[0]["status"], 429)
        self.assertEqual(ledger_requests[1]["path"], "/v1/accounts",
                         "the paused request is retried")
        silence = ledger_requests[1]["t_in"] - ledger_requests[0]["t_out"]
        self.assertGreaterEqual(silence, 1.499,
                                "no request within the Retry-After window")

        accounts_done = max(r["t_out"] for r in ledger_requests
                            if r["path"] == "/v1/accounts")
        first_item_request = min(r["t_in"] for r in services.directory.requests()
                                 if r["path"] == "/v1/items")
        self.assertLess(
            first_item_request - accounts_done, 0.5,
            "the directory starts promptly: it inherited no ledger silence")


# ---------------------------------------------------------------------------
# Posting policy: only owners with no summary in the ledger yet.
# ---------------------------------------------------------------------------

class PostingPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = os.path.join(self.tmp.name, "out")
        self.services = ServicesUnderTest(self.tmp.name)
        self.services.start()
        self.addCleanup(self.services.stop)

    def test_owners_with_a_summary_are_never_posted_even_if_the_values_differ(self):
        self.assertEqual(run_sync(self.services.config(), self.out_dir), 0)
        self.assertEqual({r["key"] for r in self.services.posted_requests()},
                         set(EXPECTED_POSTS),
                         "gus already has a summary; his stale values stand")
        self.assertEqual(self.services.rejected_requests(), [])

    def test_nothing_is_posted_when_the_ledger_is_already_complete(self):
        self.assertEqual(run_sync(self.services.config(), self.out_dir), 0)
        posts_after_first = len(self.services.posted_requests())
        self.assertGreater(posts_after_first, 0)
        self.assertEqual(run_sync(self.services.config(), self.out_dir), 0)
        self.assertEqual(len(self.services.posted_requests()), posts_after_first)


class ReplaySemanticsTest(unittest.TestCase):
    """The 200-replay path: a retry of an accepted POST is not a second post."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_replay_is_accepted_and_the_owner_is_then_skipped(self):
        fixture = Fixture("ledger", active_owners={"kim"})
        service = MockService(fixture)
        service.start()
        self.addCleanup(service.stop)

        client = HttpClient(service.url)
        body = {"owner": "kim", "open_items": 1, "done_items": 0,
                "total_points": 2}
        self.assertEqual(client.post_json("/v1/summaries", body,
                                          {"Idempotency-Key": "sync-kim"}).status, 201)
        self.assertEqual(client.post_json("/v1/summaries", body,
                                          {"Idempotency-Key": "sync-kim"}).status, 200)

        fixture.pages["/v1/accounts"] = [{"id": "a", "owner": "kim",
                                          "active": True}]
        fixture.pages["/v1/items"] = [{"id": "w", "owner": "kim",
                                       "status": "open", "points": 2}]
        out_dir = os.path.join(self.tmp.name, "out")
        self.assertEqual(run_sync(Config(service.url, service.url), out_dir), 0)

        posts = [r for r in fixture.requests if r["method"] == "POST"]
        self.assertEqual(len(posts), 2, "only the two priming POSTs: no more")
        self.assertEqual(read_jsonl(os.path.join(out_dir, "summary.jsonl")),
                         [], "nothing was posted by this run")
        self.assertEqual(read_jsonl(os.path.join(out_dir, "orphans.jsonl")), [])

    def test_same_key_with_a_different_body_is_a_conflict(self):
        fixture = Fixture("ledger", active_owners={"kim"})
        service = MockService(fixture)
        service.start()
        self.addCleanup(service.stop)
        client = HttpClient(service.url)
        first = client.post_json("/v1/summaries",
                                 {"owner": "kim", "open_items": 1,
                                  "done_items": 0, "total_points": 2},
                                 {"Idempotency-Key": "sync-kim"})
        conflict = client.post_json("/v1/summaries",
                                    {"owner": "kim", "open_items": 5,
                                     "done_items": 0, "total_points": 9},
                                    {"Idempotency-Key": "sync-kim"})
        self.assertEqual(first.status, 201)
        self.assertEqual(conflict.status, 409)

    def test_unknown_owner_is_rejected(self):
        fixture = Fixture("ledger", active_owners=set())
        service = MockService(fixture)
        service.start()
        self.addCleanup(service.stop)
        client = HttpClient(service.url)
        response = client.post_json("/v1/summaries",
                                    {"owner": "ghost", "open_items": 0,
                                     "done_items": 0, "total_points": 0},
                                    {"Idempotency-Key": "sync-ghost"})
        self.assertEqual(response.status, 422)


# ---------------------------------------------------------------------------
# Who counts: malformed records and orphans.
# ---------------------------------------------------------------------------

class MalformedAccountIsNotAnAccountTest(unittest.TestCase):
    def test_items_of_a_malformed_or_inactive_owner_are_orphans(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        out_dir = os.path.join(self.tmp.name, "out")
        services = ServicesUnderTest(self.tmp.name)
        services.start()
        self.addCleanup(services.stop)
        self.assertEqual(run_sync(services.config(), out_dir), 0)
        orphans = read_jsonl(os.path.join(out_dir, "orphans.jsonl"))
        owners = {body["owner"] for body in
                  read_jsonl(os.path.join(out_dir, "summary.jsonl"))}
        self.assertIn({"id": "i-7", "owner": "fen", "status": "open",
                       "points": 2}, orphans, "fen's only account is malformed")
        self.assertIn({"id": "i-4", "owner": "chen", "status": "open",
                       "points": 8}, orphans, "chen's account is inactive")
        self.assertNotIn("fen", owners)
        self.assertNotIn("chen", owners)
        self.assertNotIn("ivy", owners)
        self.assertNotIn("zara", owners)


# ---------------------------------------------------------------------------
# Well-formedness, decided nowhere else.
# ---------------------------------------------------------------------------

class WellFormedItemTest(unittest.TestCase):
    def test_a_well_formed_item_parses_with_extra_fields_ignored(self):
        record = {"id": "i", "owner": "o", "status": "open", "points": 0}
        self.assertEqual(well_formed_item(record).to_dict(), record)
        record.update({"status": "done", "points": 12, "extra": [1, 2]})
        parsed = well_formed_item(record)
        self.assertEqual((parsed.status, parsed.points), ("done", 12))

    def test_malformed_items(self):
        for record in (
            {},
            {"id": "i", "owner": "o", "status": "open"},             # no points
            {"id": "i", "owner": "o", "points": 1},                  # no status
            {"id": "i", "status": "open", "points": 1},              # no owner
            {"owner": "o", "status": "open", "points": 1},           # no id
            {"id": 1, "owner": "o", "status": "open", "points": 1},  # id not a string
            {"id": "i", "owner": 2, "status": "open", "points": 1},  # owner not a string
            {"id": "i", "owner": "o", "status": "sideways", "points": 1},
            {"id": "i", "owner": "o", "status": "OPEN", "points": 1},
            {"id": "i", "owner": "o", "status": None, "points": 1},
            {"id": "i", "owner": "o", "status": "open", "points": "3"},
            {"id": "i", "owner": "o", "status": "open", "points": 3.0},
            {"id": "i", "owner": "o", "status": "open", "points": -1},
            {"id": "i", "owner": "o", "status": "open", "points": True},
            {"id": "i", "owner": "o", "status": "open", "points": False},
            {"id": "i", "owner": "o", "status": "open", "points": None},
            ["not", "an", "object"],
            "a bare string",
            17,
            None,
        ):
            with self.subTest(record=record):
                self.assertIsNone(well_formed_item(record))


class WellFormedAccountTest(unittest.TestCase):
    def test_a_well_formed_account_parses_with_extra_fields_ignored(self):
        record = {"id": "a", "owner": "o", "active": False}
        self.assertEqual(well_formed_account(record).to_dict(), record)
        record["extra"] = 1
        parsed = well_formed_account(record)
        self.assertIs(parsed.active, False)

    def test_malformed_accounts(self):
        for record in (
            {},
            {"id": "a", "owner": "o"},                        # no active
            {"id": "a", "active": True},                      # no owner
            {"owner": "o", "active": True},                   # no id
            {"id": "a", "owner": "o", "active": "yes"},
            {"id": "a", "owner": "o", "active": "true"},
            {"id": "a", "owner": "o", "active": 1},
            {"id": "a", "owner": "o", "active": 0},
            {"id": "a", "owner": "o", "active": None},
            {"id": None, "owner": "o", "active": True},
            {"id": "a", "owner": ["o"], "active": True},
            "an account is an object",
            5,
        ):
            with self.subTest(record=record):
                self.assertIsNone(well_formed_account(record))


# ---------------------------------------------------------------------------
# The throttle and the Retry-After header.
# ---------------------------------------------------------------------------

class ThrottleTest(unittest.TestCase):
    def test_wait_sleeps_only_for_the_remaining_silence(self):
        now = [100.0]
        sleeps: List[float] = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        throttle = Throttle(clock=lambda: now[0], sleep=fake_sleep)
        throttle.wait()
        self.assertEqual(sleeps, [], "an unpaused service is not delayed")

        throttle.defer(5.0)
        throttle.defer(3.0)  # expires before the current hold: no effect
        throttle.wait()
        self.assertEqual(sleeps, [5.0])

        throttle.defer(2.0)  # the hold expired; this one starts from now
        throttle.wait()
        self.assertEqual(sleeps, [5.0, 2.0])

        throttle.wait()  # nothing pending any more
        self.assertEqual(sleeps, [5.0, 2.0])

    def test_the_longest_defer_wins(self):
        now = [0.0]
        sleeps: List[float] = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        throttle = Throttle(clock=lambda: now[0], sleep=fake_sleep)
        throttle.defer(2.0)
        throttle.defer(7.0)
        throttle.wait()
        self.assertEqual(sleeps, [7.0])


class RetryAfterHeaderTest(unittest.TestCase):
    def make_response(self, headers):
        message = email.message.Message()
        for key, value in (headers or {}).items():
            message[key] = value
        return Response(429, message, b"", "u")

    def test_integer_seconds(self):
        self.assertEqual(
            retry_after_seconds(self.make_response({"Retry-After": "2"})), 2.0)

    def test_header_lookup_is_case_insensitive_and_whitespace_tolerated(self):
        self.assertEqual(
            retry_after_seconds(self.make_response({"retry-after": " 3 "})), 3.0)

    def test_missing_header_falls_back_to_a_default(self):
        self.assertEqual(retry_after_seconds(self.make_response({})),
                         DEFAULT_RETRY_AFTER_SECONDS)

    def test_unparseable_header_falls_back_to_a_default(self):
        self.assertEqual(
            retry_after_seconds(self.make_response({"Retry-After": "soon"})),
            DEFAULT_RETRY_AFTER_SECONDS)

    def test_zero_seconds_is_a_zero_pause(self):
        self.assertEqual(
            retry_after_seconds(self.make_response({"Retry-After": "0"})), 0.0)


# ---------------------------------------------------------------------------
# The command line end to end, in a subprocess like a real invocation.
# ---------------------------------------------------------------------------

class CommandLineTest(unittest.TestCase):
    def test_sync_command_runs_and_writes_its_files(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        services = ServicesUnderTest(tmp.name)
        services.start()
        self.addCleanup(services.stop)
        out_dir = os.path.join(tmp.name, "fresh", "out")  # out/ is created

        result = subprocess.run(
            [sys.executable, "-m", "orgsync", "sync",
             "--config", services.config_path, "--out-dir", out_dir],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)

        self.assertEqual(result.returncode, 0, result.stderr)
        for name in ("orphans.jsonl", "errors.jsonl", "summary.jsonl"):
            self.assertTrue(os.path.exists(os.path.join(out_dir, name)), name)
        summaries = read_jsonl(os.path.join(out_dir, "summary.jsonl"))
        self.assertEqual({body["owner"] for body in summaries},
                         {"amara", "bo", "dee"})
        self.assertEqual(services.rejected_requests(), [])

        # And the command is idempotent too.
        second = subprocess.run(
            [sys.executable, "-m", "orgsync", "sync",
             "--config", services.config_path, "--out-dir", out_dir],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(len(services.posted_requests()), 3,
                         "the second invocation must post nothing")


if __name__ == "__main__":
    unittest.main()
