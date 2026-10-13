"""Tests for the `sync` command: unit and end-to-end, standard library only.

The end-to-end tests run `sync` against two local fixture services spun up on
ephemeral ports by this module — paginated JSON, planted malformed records,
duplicate deliveries, `429` rate limiting with `Retry-After`, and a ledger
with the real idempotency semantics (201 accepted / 200 replay / 409 conflict
/ 422 inactive owner). No test touches the services named in `config.json`.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlsplit

from orgsync.__main__ import main as cli_main
from orgsync.config import load_config
from orgsync.sync import (account_is_well_formed, item_is_well_formed,
                          run_sync)

# ---------------------------------------------------------------------------
# Shared dataset: every wrinkle the sync rules mention, at least once.
# ---------------------------------------------------------------------------

def item(item_id: str, owner: str, status: str, points: int,
         **extra: Any) -> Dict[str, Any]:
    record = {"id": item_id, "owner": owner, "status": status, "points": points}
    record.update(extra)
    return record


#: amara 2 open (101, 110) + 1 done (102) = 12 points; bo 1/1 = 6;
#: eli 1/1 = 3 (with an extra field, ignored); dee has no items; chen and gus
#: inactive; zara unknown; fen's account is malformed. item-103 is delivered
#: twice (counts once); item-109 twice malformed (one error line); the
#: orphans 104/106/107 are also duplicated (each orphan written once).
ITEM_PAGES: List[List[Dict[str, Any]]] = [
    [item("item-101", "amara", "open", 3),
     item("item-102", "amara", "done", 2),
     item("item-103", "bo", "open", 5),
     item("item-104", "chen", "open", 8)],
    [item("item-105", "eli", "done", 1),
     item("item-106", "zara", "open", 4, priority="low"),
     item("item-107", "fen", "open", 2),
     item("item-108", "bo", "done", 1)],
    [item("item-103", "bo", "open", 5),
     {"id": "item-109", "owner": "amara", "status": "open"},
     {"id": "item-109", "owner": "amara", "status": "open"},
     item("item-110", "amara", "open", 7),
     item("item-111", "eli", "open", 2, priority="high")],
]

ACCOUNT_PAGES: List[List[Dict[str, Any]]] = [
    [{"id": "acct-01", "owner": "amara", "active": True},
     {"id": "acct-02", "owner": "bo", "active": True},
     {"id": "acct-03", "owner": "chen", "active": False}],
    [{"id": "acct-04", "owner": "dee", "active": True},
     {"id": "acct-05", "owner": "eli", "active": True},
     {"id": "acct-06", "owner": "fen", "active": "yes"},
     {"id": 7, "owner": "ghost", "active": True}],
    [{"id": "acct-08", "owner": "gus", "active": False}],
]

ACTIVE_OWNERS: Set[str] = {"amara", "bo", "dee", "eli"}

EXPECTED_SUMMARIES: List[Dict[str, Any]] = [
    {"owner": "amara", "open_items": 2, "done_items": 1, "total_points": 12},
    {"owner": "bo", "open_items": 1, "done_items": 1, "total_points": 6},
    {"owner": "dee", "open_items": 0, "done_items": 0, "total_points": 0},
    {"owner": "eli", "open_items": 1, "done_items": 1, "total_points": 3},
]

EXPECTED_ORPHANS: List[Dict[str, Any]] = [
    {"id": "item-104", "owner": "chen", "status": "open", "points": 8},
    {"id": "item-106", "owner": "zara", "status": "open", "points": 4,
     "priority": "low"},
    {"id": "item-107", "owner": "fen", "status": "open", "points": 2},
]

EXPECTED_ERRORS: List[Dict[str, Any]] = [
    {"service": "directory", "record":
        {"id": "item-109", "owner": "amara", "status": "open"}},
    {"service": "ledger", "record": {"id": "acct-06", "owner": "fen",
                                     "active": "yes"}},
    {"service": "ledger", "record": {"id": 7, "owner": "ghost",
                                     "active": True}},
]

REJECTED_STATUSES = (400, 409, 422)


# ---------------------------------------------------------------------------
# Fixture services.
# ---------------------------------------------------------------------------

def _well_formed_account(record: Any) -> bool:
    return (isinstance(record, dict)
            and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and isinstance(record.get("active"), bool))


class FixtureHandler(BaseHTTPRequestHandler):
    """Bridges HTTP to the FixtureService attached to the server instance."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass  # keep test output clean

    def do_GET(self) -> None:
        self.server.fixture.serve(self, "GET")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length > 0 else b""
        self.server.fixture.serve(self, "POST", body)


class FixtureService:
    """One stand-in service: paginated records, 429s, idempotent POSTs.

    `rate_limit_every` answers every Nth request (any method) with 429 and a
    Retry-After of `retry_after` seconds; 0 disables limiting. Every request
    is logged with its arrival time and the status it was answered with.
    """

    def __init__(self, kind: str, pages: List[List[Dict[str, Any]]],
                 active_owners: Set[str] = frozenset(),
                 rate_limit_every: int = 0, retry_after: float = 0.0,
                 summary_page_size: int = 2) -> None:
        self.kind = kind
        self.pages = pages
        self.active_owners = set(active_owners)
        self.rate_limit_every = rate_limit_every
        self.retry_after = retry_after
        self.summary_page_size = max(summary_page_size, 1)
        self.summaries: Dict[str, Dict[str, Any]] = {}  # key -> posted body
        self.requests: List[Dict[str, Any]] = []
        self.rate_limited = 0
        self.rejected_writes = 0
        self.accepted_writes = 0
        self.replayed_writes = 0
        self._lock = threading.Lock()
        self._seen = 0

    # -- request plumbing ---------------------------------------------------

    def serve(self, handler: FixtureHandler, method: str, body: bytes = b"") -> None:
        with self._lock:
            self._seen += 1
            serial = self._seen
        entry: Dict[str, Any] = {"t": time.monotonic(), "method": method,
                                 "path": handler.path,
                                 "key": handler.headers.get("Idempotency-Key"),
                                 "body": body, "status": None}
        with self._lock:
            self.requests.append(entry)
        if self.rate_limit_every and serial % self.rate_limit_every == 0:
            with self._lock:
                self.rate_limited += 1
            self._respond(handler, entry, 429, {"error": "rate_limited"},
                          {"Retry-After": "%g" % self.retry_after})
            return
        if method == "GET":
            self._handle_get(handler, entry)
        else:
            self._handle_post(handler, entry, body)

    def _respond(self, handler: FixtureHandler, entry: Dict[str, Any],
                 status: int, payload: Any,
                 extra_headers: Optional[Dict[str, str]] = None) -> None:
        entry["status"] = status
        data = json.dumps(payload).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        for name, value in (extra_headers or {}).items():
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.write(data)

    # -- GET: pagination ------------------------------------------------------

    def _handle_get(self, handler: FixtureHandler, entry: Dict[str, Any]) -> None:
        split = urlsplit(handler.path)
        query = parse_qs(split.query)
        try:
            page = int(query.get("page", ["1"])[0])
        except (TypeError, ValueError):
            page = 1
        if self.kind == "ledger" and split.path == "/v1/summaries":
            stored = list(self.summaries.values())
            start = (page - 1) * self.summary_page_size
            chunk = stored[start:start + self.summary_page_size]
            next_page = page + 1 if start + self.summary_page_size < len(stored) else None
            self._respond(handler, entry, 200,
                          {"summaries": chunk, "next_page": next_page})
            return
        collection = self._collection(split.path)
        if collection is None:
            self._respond(handler, entry, 404, {"error": "not_found"})
            return
        list_key, pages = collection
        index = page - 1
        records = pages[index] if 0 <= index < len(pages) else []
        next_page = page + 1 if index + 1 < len(pages) else None
        self._respond(handler, entry, 200, {list_key: records,
                                            "next_page": next_page})

    def _collection(self, path: str) -> Optional[Tuple[str, List[List[Any]]]]:
        if path == "/v1/items":
            return "items", self.pages
        if path == "/v1/accounts":
            return "accounts", self.pages
        return None

    # -- POST: idempotent summaries ------------------------------------------

    def _handle_post(self, handler: FixtureHandler, entry: Dict[str, Any],
                     body_bytes: bytes) -> None:
        split = urlsplit(handler.path)
        if self.kind != "ledger" or split.path != "/v1/summaries":
            self._respond(handler, entry, 404, {"error": "not_found"})
            return
        try:
            body = json.loads(body_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._note_rejection()
            self._respond(handler, entry, 400, {"error": "bad_json"})
            return
        if (not isinstance(body, dict)
                or not isinstance(body.get("owner"), str)
                or body.get("owner") not in self.active_owners):
            self._note_rejection()
            self._respond(handler, entry, 422, {"error": "owner_not_active"})
            return
        key = handler.headers.get("Idempotency-Key")
        with self._lock:
            stored = self.summaries.get(key)
            if stored is None:
                self.summaries[key] = body
                self.accepted_writes += 1
                status = 201
            elif stored == body:
                self.replayed_writes += 1
                status = 200
            else:
                self.rejected_writes += 1
                status = 409
        self._respond(handler, entry, status, {"stored": status})

    def _note_rejection(self) -> None:
        with self._lock:
            self.rejected_writes += 1

    # -- inspection helpers ---------------------------------------------------

    def posts(self) -> List[Dict[str, Any]]:
        return [r for r in self.requests if r["method"] == "POST"]

    def accepted_post_bodies(self) -> List[Dict[str, Any]]:
        return [json.loads(r["body"].decode("utf-8")) for r in self.posts()
                if r["status"] in (200, 201)]

    def rejected_write_count(self) -> int:
        """POSTs the ledger answered 400/409/422 — a passing run has none."""
        return sum(1 for r in self.posts()
                   if r["status"] in REJECTED_STATUSES)

    def rate_limit_violations(self) -> List[float]:
        """Gaps after a 429 that were shorter than the Retry-After asked for."""
        gaps = []
        for earlier, later in zip(self.requests, self.requests[1:]):
            if earlier["status"] == 429:
                gap = later["t"] - earlier["t"]
                if gap < self.retry_after - 0.02:
                    gaps.append(gap)
        return gaps


class FixtureServer:
    """A FixtureService on an ephemeral port, served from a daemon thread."""

    def __init__(self, fixture: FixtureService) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        self.httpd.fixture = fixture  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Test base: both services from the shared dataset, out/ deep in a tmpdir.
# ---------------------------------------------------------------------------

class SyncHarness(unittest.TestCase):

    rate_limit_every = 0
    retry_after = 0.0

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_path = tmp.name
        self.out_dir = os.path.join(self.tmp_path, "made", "up", "out")
        self.directory = FixtureService(
            "directory", self.directory_pages(),
            rate_limit_every=self.rate_limit_every,
            retry_after=self.retry_after)
        self.ledger = FixtureService(
            "ledger", self.ledger_pages(), active_owners=self.active_owners(),
            rate_limit_every=self.rate_limit_every,
            retry_after=self.retry_after)
        directory_server = FixtureServer(self.directory)
        ledger_server = FixtureServer(self.ledger)
        self.addCleanup(directory_server.stop)
        self.addCleanup(ledger_server.stop)
        self.config_path = os.path.join(self.tmp_path, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump({"directory_url": directory_server.url,
                       "ledger_url": ledger_server.url}, handle)

    # Dataset hooks a test may override.

    def directory_pages(self) -> List[List[Dict[str, Any]]]:
        return ITEM_PAGES

    def ledger_pages(self) -> List[List[Dict[str, Any]]]:
        return ACCOUNT_PAGES

    def active_owners(self) -> Set[str]:
        return set(ACTIVE_OWNERS)

    # Convenience.

    def sync(self) -> int:
        return run_sync(load_config(self.config_path), self.out_dir)

    def read(self, name: str) -> List[Any]:
        path = os.path.join(self.out_dir, name)
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def read_bytes(self, name: str) -> Optional[bytes]:
        path = os.path.join(self.out_dir, name)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as handle:
            return handle.read()

    def assert_same_records(self, expected: List[Any], actual: List[Any],
                            msg: str = "") -> None:
        key = lambda entry: json.dumps(entry, sort_keys=True)  # noqa: E731
        self.assertEqual(sorted(map(key, expected)), sorted(map(key, actual)), msg)


# ---------------------------------------------------------------------------
# End-to-end tests.
# ---------------------------------------------------------------------------

class FullRunTest(SyncHarness):
    def test_posts_exactly_the_required_summaries_and_writes_the_files(self):
        self.assertEqual(self.sync(), 0)

        # The ledger got exactly one POST per active owner, right bodies.
        self.assertEqual(self.ledger.accepted_post_bodies(), EXPECTED_SUMMARIES)
        self.assertEqual(self.ledger.accepted_writes, 4)
        self.assertEqual(self.ledger.rejected_write_count(), 0)
        keys = {r["key"] for r in self.ledger.posts() if r["status"] == 201}
        self.assertEqual(keys, {"sync-" + s["owner"] for s in EXPECTED_SUMMARIES})

        # Every page of both services was read.
        self.assertTrue(any("page=3" in r["path"] for r in self.directory.requests))
        self.assertTrue(any("page=3" in r["path"] for r in self.ledger.requests))

        # Orphans: items without an active owner, each once, as received.
        self.assert_same_records(EXPECTED_ORPHANS, self.read("orphans.jsonl"))

        # Malformed records from both services, each once.
        self.assert_same_records(EXPECTED_ERRORS, self.read("errors.jsonl"))

        # summary.jsonl records exactly the posted bodies.
        self.assertEqual(self.read("summary.jsonl"), EXPECTED_SUMMARIES)

    def test_second_run_makes_no_writes_and_leaves_files_unchanged(self):
        self.assertEqual(self.sync(), 0)
        before = {name: self.read_bytes(name) for name in
                  ("errors.jsonl", "orphans.jsonl", "summary.jsonl")}
        posts_before = len(self.ledger.posts())

        self.assertEqual(self.sync(), 0)

        self.assertEqual(len(self.ledger.posts()), posts_before)
        self.assertEqual(self.ledger.accepted_writes, 4)
        after = {name: self.read_bytes(name) for name in before}
        self.assertEqual(before, after)

    def test_existing_summaries_are_not_posted_again(self):
        for summary in EXPECTED_SUMMARIES[:2]:  # amara and bo already there
            self.ledger.summaries["sync-" + summary["owner"]] = dict(summary)
        posts_before = len(self.ledger.posts())

        self.assertEqual(self.sync(), 0)

        new_posts = self.ledger.posts()[posts_before:]
        posted_owners = {json.loads(r["body"])["owner"] for r in new_posts
                         if r["status"] in (200, 201)}
        self.assertEqual(posted_owners, {"dee", "eli"})
        self.assertEqual(self.ledger.rejected_write_count(), 0)
        self.assertEqual(self.read("summary.jsonl"),
                         [s for s in EXPECTED_SUMMARIES if s["owner"] in ("dee", "eli")])

    def test_all_summaries_preexisting_makes_a_writeless_run(self):
        for summary in EXPECTED_SUMMARIES:
            self.ledger.summaries["sync-" + summary["owner"]] = dict(summary)
        posts_before = len(self.ledger.posts())

        self.assertEqual(self.sync(), 0)

        self.assertEqual(len(self.ledger.posts()), posts_before)
        self.assertEqual(self.read("summary.jsonl"), [])
        self.assert_same_records(EXPECTED_ERRORS, self.read("errors.jsonl"))
        self.assert_same_records(EXPECTED_ORPHANS, self.read("orphans.jsonl"))

    def test_cli_entry_point_runs_the_same_reconciliation(self):
        code = cli_main(["sync", "--config", self.config_path,
                         "--out-dir", self.out_dir])
        self.assertEqual(code, 0)
        self.assertEqual(self.ledger.accepted_post_bodies(), EXPECTED_SUMMARIES)
        self.assertEqual(self.ledger.rejected_write_count(), 0)
        self.assertEqual(self.read("summary.jsonl"), EXPECTED_SUMMARIES)


class RateLimitTest(SyncHarness):
    rate_limit_every = 3
    retry_after = 0.25

    def test_429s_are_retried_after_retry_after_and_the_run_completes(self):
        self.assertEqual(self.sync(), 0)

        self.assertGreater(self.directory.rate_limited, 0)
        self.assertGreater(self.ledger.rate_limited, 0)
        self.assertEqual(self.directory.rate_limit_violations(), [])
        self.assertEqual(self.ledger.rate_limit_violations(), [])
        self.assertEqual(self.ledger.accepted_post_bodies(), EXPECTED_SUMMARIES)
        self.assertEqual(self.ledger.rejected_write_count(), 0)
        self.assertEqual(self.read("summary.jsonl"), EXPECTED_SUMMARIES)
        self.assert_same_records(EXPECTED_ERRORS, self.read("errors.jsonl"))
        self.assert_same_records(EXPECTED_ORPHANS, self.read("orphans.jsonl"))


class MalformedDatasetTest(SyncHarness):
    def test_records_that_are_only_junk_never_reach_a_summary(self):
        # An owner known only through malformed accounts, and items that are
        # all malformed: nothing is counted, everything is reported.
        self.directory.pages = [
            [{"id": "x1", "owner": "mystery", "status": "open", "points": 1},
             {"id": "x2", "owner": "mystery", "status": "done", "points": True},
             {"id": "x3", "owner": "mystery", "status": "done", "points": -2},
             {"id": "x4", "owner": "mystery", "status": "open", "points": 1.5},
             {"id": "x5", "owner": "mystery", "status": "OPEN", "points": 1},
             {"id": "x6", "owner": "mystery", "status": "done", "points": "2"},
             "junk", 42, None],
        ]
        self.ledger.pages = [
            [{"owner": "mystery", "id": "a1", "active": True},  # field order is free
             {"id": "a2", "owner": "mystery", "active": 1},
             {"id": "a3", "owner": "mystery"},  # active missing
             {"id": "a4", "owner": "mystery", "active": True, "note": "extra"}],
        ]
        # Only a1 and a4 are well formed, so mystery IS active and x1 counts.
        self.ledger.active_owners = {"mystery"}
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.ledger.accepted_post_bodies(),
                         [{"owner": "mystery", "open_items": 1,
                           "done_items": 0, "total_points": 1}])
        error_records = [e["record"] for e in self.read("errors.jsonl")]
        self.assertEqual(len(error_records), 10)
        self.assertEqual(self.read("orphans.jsonl"), [])


class EdgeCaseRunTest(SyncHarness):
    def test_empty_services_produce_empty_files_and_no_posts(self):
        self.directory.pages = [[]]
        self.ledger.pages = [[]]
        self.ledger.active_owners = set()
        self.assertEqual(self.sync(), 0)
        self.assertEqual(self.ledger.posts(), [])
        for name in ("errors.jsonl", "orphans.jsonl", "summary.jsonl"):
            self.assertEqual(self.read(name), [])
            self.assertIsNotNone(self.read_bytes(name))

    def test_every_page_of_summaries_is_read_before_posting(self):
        # Pre-seed enough summaries to span several fixture pages (2 per page),
        # all for owners that are not active now: they still count as present.
        for index in range(5):
            owner = "retired-%d" % index
            self.ledger.summaries["sync-" + owner] = {
                "owner": owner, "open_items": 0, "done_items": 0,
                "total_points": 0}
        self.assertEqual(self.sync(), 0)
        summary_paths = [r["path"] for r in self.ledger.requests
                         if r["method"] == "GET" and "/v1/summaries" in r["path"]]
        self.assertGreaterEqual(len(summary_paths), 3)
        self.assertEqual(self.ledger.rejected_write_count(), 0)


# ---------------------------------------------------------------------------
# Unit tests for the well-formedness rules (no HTTP).
# ---------------------------------------------------------------------------

class WellFormedItemTest(unittest.TestCase):
    def test_accepts_the_documented_shape_plus_extra_fields(self):
        self.assertTrue(item_is_well_formed(
            {"id": "i", "owner": "o", "status": "open", "points": 0}))
        self.assertTrue(item_is_well_formed(
            {"id": "i", "owner": "o", "status": "done", "points": 3,
             "extra": {"nested": [1, 2]}}))

    def test_rejects_wrong_or_missing_fields(self):
        bad_records = [
            None, "item", 7, [], {},
            {"id": 1, "owner": "o", "status": "open", "points": 1},
            {"id": "i", "owner": 2, "status": "open", "points": 1},
            {"id": "i", "owner": "o", "points": 1},
            {"id": "i", "owner": "o", "status": "open"},
            {"id": "i", "owner": "o", "status": "open", "points": "1"},
            {"id": "i", "owner": "o", "status": "open", "points": 1.0},
            {"id": "i", "owner": "o", "status": "open", "points": True},
            {"id": "i", "owner": "o", "status": "open", "points": -1},
            {"id": "i", "owner": "o", "status": "Open", "points": 1},
            {"id": "i", "owner": "o", "status": "closed", "points": 1},
            {"id": "i", "owner": "o", "status": None, "points": 1},
        ]
        for record in bad_records:
            with self.subTest(record=record):
                self.assertFalse(item_is_well_formed(record))


class WellFormedAccountTest(unittest.TestCase):
    def test_accepts_the_documented_shape_plus_extra_fields(self):
        self.assertTrue(account_is_well_formed(
            {"id": "a", "owner": "o", "active": False}))
        self.assertTrue(account_is_well_formed(
            {"id": "a", "owner": "o", "active": True, "tier": "gold"}))

    def test_rejects_wrong_or_missing_fields(self):
        bad_records = [
            None, "account", [], {},
            {"id": "a", "owner": "o"},
            {"id": "a", "owner": "o", "active": 1},
            {"id": "a", "owner": "o", "active": 0},
            {"id": "a", "owner": "o", "active": "yes"},
            {"id": "a", "owner": "o", "active": None},
            {"id": 1, "owner": "o", "active": True},
            {"id": "a", "owner": 2, "active": True},
        ]
        for record in bad_records:
            with self.subTest(record=record):
                self.assertFalse(account_is_well_formed(record))


if __name__ == "__main__":
    unittest.main()
