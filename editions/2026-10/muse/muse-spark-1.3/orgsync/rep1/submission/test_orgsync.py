"""Tests for `orgsync sync` — reconciliation, outputs, rate limits, idempotency.

Self-contained: spins up fake Directory/Ledger HTTP services on loopback
using only the standard library, so no outside network is needed. The fakes
implement the pagination scheme, the ledger POST semantics (201/200/409/422),
429 injection with `Retry-After`, and a timestamped log of every request.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from orgsync.__main__ import main as orgsync_main
from orgsync.config import Config
from orgsync.sync import is_well_formed_account, is_well_formed_item, run_sync


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    try:
        size = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        size = 0
    return handler.rfile.read(size) if size > 0 else b""


def _send_json(handler: BaseHTTPRequestHandler, status: int, obj,
               extra_headers=None) -> None:
    data = json.dumps(obj).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    for key, value in (extra_headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(data)


class _Handler(BaseHTTPRequestHandler):
    """Routes for both fakes; behavior is driven by `self.server` attrs."""

    def log_message(self, *args):  # keep test output quiet
        pass

    def do_GET(self):  # noqa: N802
        self._route("GET")

    def do_POST(self):  # noqa: N802
        self._route("POST")

    def _route(self, method: str) -> None:
        server = self.server
        body = _read_body(self)
        target = self.path
        with server.lock:
            server.log.append({
                "t": time.monotonic(), "method": method, "target": target,
                "headers": {k: v for k, v in self.headers.items()},
                "body": body, "status": None,
            })
            entry = server.log[-1]
            remaining = server.fail_plan.get((method, target), 0)
            if remaining > 0:
                server.fail_plan[(method, target)] = remaining - 1
                entry["status"] = 429
                retry_after = server.retry_after
        if entry["status"] == 429:
            _send_json(self, 429, {"error": "rate limited"},
                       {"Retry-After": retry_after})
            return
        parsed = urllib.parse.urlparse(self.path)
        if method == "GET" and parsed.path in (
                "/v1/items", "/v1/accounts", "/v1/summaries"):
            if server.kind == "directory" and parsed.path != "/v1/items":
                return self._finish(entry, 404, {"error": "not found"})
            if server.kind == "ledger" and parsed.path == "/v1/items":
                return self._finish(entry, 404, {"error": "not found"})
            query = urllib.parse.parse_qs(parsed.query)
            try:
                page = int(query.get("page", ["1"])[0])
            except (TypeError, ValueError):
                page = 1
            pages = server.pages_for(parsed.path)
            index = page - 1
            if 0 <= index < len(pages):
                batch = pages[index]
                nxt = page + 1 if page < len(pages) else None
            else:
                batch, nxt = [], None
            key = {"/v1/items": "items", "/v1/accounts": "accounts",
                   "/v1/summaries": "summaries"}[parsed.path]
            # For summaries the fake serves its live list, paginated.
            if parsed.path == "/v1/summaries":
                batch, nxt = server.summary_page(page)
            return self._finish(entry, 200, {key: batch, "next_page": nxt})
        if method == "POST" and parsed.path == "/v1/summaries" \
                and server.kind == "ledger":
            return self._post_summary(entry, body)
        return self._finish(entry, 404, {"error": "not found"})

    def _finish(self, entry, status: int, obj) -> None:
        with self.server.lock:
            entry["status"] = status
        _send_json(self, status, obj)

    def _post_summary(self, entry, body: bytes) -> None:
        server = self.server
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._finish(entry, 400, {"error": "bad json"})
        key = self.headers.get("Idempotency-Key")
        if not key:
            return self._finish(entry, 400, {"error": "missing key"})
        valid = (
            isinstance(payload, dict)
            and isinstance(payload.get("owner"), str)
            and type(payload.get("open_items")) is int
            and type(payload.get("done_items")) is int
            and type(payload.get("total_points")) is int
            and payload["open_items"] >= 0
            and payload["done_items"] >= 0
            and payload["total_points"] >= 0
        )
        if not valid:
            return self._finish(entry, 400, {"error": "bad body"})
        if payload["owner"] not in server.active_owners:
            return self._finish(entry, 422, {"error": "unknown owner"})
        canonical = json.dumps(payload, sort_keys=True)
        with server.lock:
            if key in server.idempotency_store:
                status = 200 if server.idempotency_store[key] == canonical \
                    else 409
                entry["status"] = status
                code = status
            else:
                server.idempotency_store[key] = canonical
                server.live_summaries.append(payload)
                server.posted.append({"key": key, "body": payload})
                entry["status"] = 201
                code = 201
        _send_json(self, code, {"ok": True})


class FakeService:
    """A fake Directory or Ledger service with a request log."""

    def __init__(self, kind: str, pages=None, retry_after: str = "0.2",
                 fail_plan=None, active_owners=None,
                 live_summaries=None, summary_page_size: int = 2):
        self.kind = kind
        self.pages = pages or []
        self.retry_after = retry_after
        self.fail_plan = dict(fail_plan or {})
        self.active_owners = set(active_owners or ())
        self.live_summaries = list(live_summaries or [])
        self.summary_page_size = summary_page_size
        self.idempotency_store = {}
        self.posted = []
        self.log = []
        self.lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        for attr in ("kind", "pages", "retry_after", "fail_plan",
                     "active_owners", "live_summaries", "summary_page_size",
                     "idempotency_store", "posted", "log", "lock",
                     "pages_for", "summary_page"):
            setattr(self.server, attr, getattr(self, attr))
        # The handler mutates server attrs in place; re-point our aliases.
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01},
                                       daemon=True)

    def pages_for(self, path: str):
        if self.kind == "directory" and path == "/v1/items":
            return self.pages
        if self.kind == "ledger" and path == "/v1/accounts":
            return self.pages
        return [[]]

    def summary_page(self, page: int):
        size = self.summary_page_size
        chunk = self.live_summaries[(page - 1) * size: page * size]
        nxt = page + 1 if page * size < len(self.live_summaries) else None
        return chunk, nxt

    def start(self) -> str:
        self.thread.start()
        host, port = self.server.server_address
        return "http://%s:%d" % (host, port)

    def stop(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def posts(self):
        with self.lock:
            return [e for e in self.log
                    if e["method"] == "POST" and e["status"] != 429]

    def all_posts(self):
        with self.lock:
            return [e for e in self.log if e["method"] == "POST"]


class SyncCase(unittest.TestCase):
    """Boots a fake Directory + Ledger pair per test and tears them down."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = os.path.join(self.tmp.name, "out")
        self.directory = None
        self.ledger = None

    def tearDown(self):
        for service in (self.directory, self.ledger):
            if service is not None:
                service.stop()

    def start(self, item_pages=(), account_pages=(), summaries=(),
              active_owners=(), fail_plan=None, directory_fail_plan=None,
              ledger_fail_plan=None, retry_after: str = "0.2",
              summary_page_size: int = 2):
        """Start both fakes; `fail_plan` applies to both services."""
        directory_plan, ledger_plan = dict(fail_plan or {}), dict(
            fail_plan or {})
        directory_plan.update(directory_fail_plan or {})
        ledger_plan.update(ledger_fail_plan or {})
        self.directory = FakeService("directory", pages=[list(p) for p in
                                     item_pages], retry_after=retry_after,
                                     fail_plan=directory_plan)
        self.ledger = FakeService(
            "ledger", pages=[list(p) for p in account_pages],
            retry_after=retry_after, fail_plan=ledger_plan,
            active_owners=set(active_owners),
            live_summaries=[dict(s) for s in summaries],
            summary_page_size=summary_page_size)
        directory_url = self.directory.start()
        ledger_url = self.ledger.start()
        self.config = Config(directory_url, ledger_url)
        return self.config

    def do_sync(self) -> int:
        return run_sync(self.config, self.out_dir)

    def read(self, name: str):
        with open(os.path.join(self.out_dir, name), "r",
                  encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def raw(self, name: str) -> bytes:
        with open(os.path.join(self.out_dir, name), "rb") as handle:
            return handle.read()

    def assertNoRejectedWrites(self):  # noqa: N802
        for entry in self.ledger.all_posts():
            self.assertNotIn(entry["status"], (400, 409, 422),
                             "rejected write: %r" % (entry,))


class ValidatorsTest(unittest.TestCase):
    def test_well_formed_items_pass(self):
        self.assertTrue(is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "open", "points": 3}))
        self.assertTrue(is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "done", "points": 0,
             "extra": [1, {"a": None}]}))

    def test_malformed_items_fail(self):
        bad = [
            None, 42, "nope", ["i-1"],
            {},  # missing everything
            {"owner": "kim", "status": "open", "points": 1},  # no id
            {"id": 7, "owner": "kim", "status": "open", "points": 1},
            {"id": "i", "status": "open", "points": 1},  # no owner
            {"id": "i", "owner": "kim", "points": 1},  # no status
            {"id": "i", "owner": "kim", "status": "Open", "points": 1},
            {"id": "i", "owner": "kim", "status": "archived", "points": 1},
            {"id": "i", "owner": "kim", "status": True, "points": 1},
            {"id": "i", "owner": "kim", "status": "open"},  # no points
            {"id": "i", "owner": "kim", "status": "open", "points": "3"},
            {"id": "i", "owner": "kim", "status": "open", "points": 3.0},
            {"id": "i", "owner": "kim", "status": "open", "points": True},
            {"id": "i", "owner": "kim", "status": "open", "points": -1},
        ]
        for record in bad:
            with self.subTest(record=record):
                self.assertFalse(is_well_formed_item(record))

    def test_well_formed_accounts_pass(self):
        self.assertTrue(is_well_formed_account(
            {"id": "a-1", "owner": "kim", "active": True}))
        self.assertTrue(is_well_formed_account(
            {"id": "a-1", "owner": "kim", "active": False, "note": 1}))

    def test_malformed_accounts_fail(self):
        bad = [
            None, "x", 9, [],
            {},
            {"owner": "kim", "active": True},  # no id
            {"id": "a", "active": True},  # no owner
            {"id": "a", "owner": "kim"},  # no active
            {"id": "a", "owner": "kim", "active": "yes"},
            {"id": "a", "owner": "kim", "active": 1},
            {"id": "a", "owner": "kim", "active": None},
            {"id": "a", "owner": 5, "active": True},
        ]
        for record in bad:
            with self.subTest(record=record):
                self.assertFalse(is_well_formed_account(record))


class ArithmeticTest(SyncCase):
    def test_worked_example(self):
        items = [{"id": "i-1", "owner": "kim", "status": "open", "points": 3},
                 {"id": "i-2", "owner": "kim", "status": "open", "points": 4},
                 {"id": "i-3", "owner": "kim", "status": "done", "points": 2}]
        accounts = [[{"id": "a-1", "owner": "kim", "active": True}]]
        self.start(item_pages=[items], account_pages=accounts,
                   active_owners={"kim"})
        self.assertEqual(self.do_sync(), 0)
        self.assertEqual(self.read("summary.jsonl"), [
            {"owner": "kim", "open_items": 2, "done_items": 1,
             "total_points": 9}])
        self.assertEqual(self.read("orphans.jsonl"), [])
        self.assertEqual(self.read("errors.jsonl"), [])
        posts = self.ledger.posts()
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["status"], 201)
        self.assertEqual(
            posts[0]["headers"].get("Idempotency-Key"), "sync-kim")
        self.assertEqual(json.loads(posts[0]["body"].decode("utf-8")),
                         {"owner": "kim", "open_items": 2, "done_items": 1,
                          "total_points": 9})
        self.assertNoRejectedWrites()

    def test_active_account_without_items_gets_zeros(self):
        self.start(item_pages=[[]],
                   account_pages=[[ {"id": "a-1", "owner": "solo",
                                     "active": True}]],
                   active_owners={"solo"})
        self.assertEqual(self.do_sync(), 0)
        self.assertEqual(self.read("summary.jsonl"), [
            {"owner": "solo", "open_items": 0, "done_items": 0,
             "total_points": 0}])
        self.assertNoRejectedWrites()

    def test_inactive_accounts_get_no_summary_and_items_are_orphans(self):
        items = [{"id": "i-1", "owner": "zed", "status": "open", "points": 5}]
        accounts = [[{"id": "a-1", "owner": "zed", "active": False}]]
        self.start(item_pages=[items], account_pages=accounts,
                   active_owners=set())
        self.assertEqual(self.do_sync(), 0)
        self.assertEqual(self.read("summary.jsonl"), [])
        self.assertEqual(self.read("orphans.jsonl"), items)
        self.assertEqual(len(self.ledger.posts()), 0)
        self.assertNoRejectedWrites()


class PaginationAndDuplicatesTest(SyncCase):
    def test_reads_every_page_and_counts_duplicates_once(self):
        dup = {"id": "i-1", "owner": "kim", "status": "open", "points": 3}
        pages = [[dup, {"id": "i-2", "owner": "kim", "status": "done",
                       "points": 2}],
                 [dict(dup), {"id": "i-3", "owner": "kim", "status": "open",
                              "points": 4}],
                 []]
        accounts = [[{"id": "a-1", "owner": "kim", "active": True}],
                    [{"id": "a-9", "owner": "kim", "active": True}]]
        self.start(item_pages=pages, account_pages=accounts,
                   active_owners={"kim"})
        self.assertEqual(self.do_sync(), 0)
        self.assertEqual(self.read("summary.jsonl"), [
            {"owner": "kim", "open_items": 2, "done_items": 1,
             "total_points": 9}])
        # Every page was fetched.
        targets = [e["target"] for e in self.directory.log
                   if e["method"] == "GET"]
        self.assertIn("/v1/items?page=1", targets)
        self.assertIn("/v1/items?page=2", targets)
        self.assertIn("/v1/items?page=3", targets)
        targets = [e["target"] for e in self.ledger.log
                   if e["method"] == "GET"]
        self.assertIn("/v1/accounts?page=1", targets)
        self.assertIn("/v1/accounts?page=2", targets)
        self.assertNoRejectedWrites()

    def test_duplicate_orphan_written_once(self):
        item = {"id": "i-1", "owner": "ghost", "status": "open", "points": 1}
        self.start(item_pages=[[item], [dict(item)]],
                   account_pages=[[ {"id": "a-1", "owner": "kim",
                                     "active": True}]],
                   active_owners={"kim"})
        self.assertEqual(self.do_sync(), 0)
        self.assertEqual(self.read("orphans.jsonl"), [item])
        self.assertEqual(self.read("summary.jsonl"), [
            {"owner": "kim", "open_items": 0, "done_items": 0,
             "total_points": 0}])


class MalformedRecordsTest(SyncCase):
    def test_malformed_items_skipped_and_reported_once(self):
        good = {"id": "i-good", "owner": "kim", "status": "open", "points": 3}
        bad_missing = {"id": "i-bad", "owner": "kim", "status": "open"}
        bad_type = {"id": "i-typ", "owner": "kim", "status": "open",
                    "points": "9"}
        self.start(
            item_pages=[[good, bad_missing, bad_type,
                         dict(bad_missing), "stray", None]],
            account_pages=[[ {"id": "a-1", "owner": "kim", "active": True}]],
            active_owners={"kim"})
        self.assertEqual(self.do_sync(), 0)
        self.assertEqual(self.read("summary.jsonl"), [
            {"owner": "kim", "open_items": 1, "done_items": 0,
             "total_points": 3}])
        self.assertEqual(self.read("orphans.jsonl"), [])
        errors = self.read("errors.jsonl")
        self.assertEqual(len(errors), 4)
        for entry in errors:
            self.assertEqual(entry["service"], "directory")
        records = [e["record"] for e in errors]
        self.assertIn(bad_missing, records)
        self.assertIn(bad_type, records)
        self.assertIn("stray", records)
        self.assertIn(None, records)
        self.assertNoRejectedWrites()

    def test_malformed_account_confirms_nothing_and_orphans_owner(self):
        items = [{"id": "i-1", "owner": "fen", "status": "open", "points": 2},
                 {"id": "i-2", "owner": "kim", "status": "done", "points": 1}]
        accounts = [[{"id": "a-1", "owner": "fen", "active": "yes"},
                     {"id": "a-2", "owner": "kim", "active": True}]]
        self.start(item_pages=[items], account_pages=accounts,
                   active_owners={"kim"})
        self.assertEqual(self.do_sync(), 0)
        self.assertEqual(self.read("summary.jsonl"), [
            {"owner": "kim", "open_items": 0, "done_items": 1,
             "total_points": 1}])
        self.assertEqual(self.read("orphans.jsonl"), [items[0]])
        errors = self.read("errors.jsonl")
        self.assertEqual(errors, [
            {"service": "ledger",
             "record": {"id": "a-1", "owner": "fen", "active": "yes"}}])
        owners = {p["body"] and json.loads(p["body"].decode("utf-8"))["owner"]
                  for p in self.ledger.posts()}
        self.assertEqual(owners, {"kim"})
        self.assertNoRejectedWrites()


class MissingOnlyTest(SyncCase):
    def test_posts_only_what_is_missing(self):
        items = [{"id": "i-1", "owner": "kim", "status": "open", "points": 1},
                 {"id": "i-2", "owner": "pat", "status": "done", "points": 2}]
        accounts = [[{"id": "a-1", "owner": "kim", "active": True},
                     {"id": "a-2", "owner": "pat", "active": True}]]
        have = [{"owner": "kim", "open_items": 1, "done_items": 0,
                 "total_points": 1}]
        self.start(item_pages=[items], account_pages=accounts, summaries=have,
                   active_owners={"kim", "pat"})
        self.assertEqual(self.do_sync(), 0)
        # Only pat's summary was posted; kim's pre-existing one is untouched.
        self.assertEqual(self.read("summary.jsonl"), [
            {"owner": "pat", "open_items": 0, "done_items": 1,
             "total_points": 2}])
        posts = self.ledger.posts()
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["headers"].get("Idempotency-Key"),
                         "sync-pat")
        self.assertEqual(json.loads(posts[0]["body"].decode("utf-8")),
                         {"owner": "pat", "open_items": 0, "done_items": 1,
                          "total_points": 2})
        self.assertNoRejectedWrites()

    def test_nothing_missing_posts_nothing_but_writes_empty_file(self):
        have = [{"owner": "kim", "open_items": 0, "done_items": 0,
                 "total_points": 0}]
        self.start(item_pages=[[]],
                   account_pages=[[ {"id": "a-1", "owner": "kim",
                                     "active": True}]],
                   summaries=have, active_owners={"kim"})
        self.assertEqual(self.do_sync(), 0)
        self.assertEqual(self.read("summary.jsonl"), [])
        self.assertEqual(self.ledger.posts(), [])
        self.assertTrue(os.path.exists(os.path.join(self.out_dir,
                                                    "summary.jsonl")))


class RateLimitTest(SyncCase):
    def test_get_429s_are_retried_after_the_delay(self):
        items = [{"id": "i-1", "owner": "kim", "status": "open", "points": 3}]
        accounts = [[{"id": "a-1", "owner": "kim", "active": True}]]
        self.start(item_pages=[items], account_pages=accounts,
                   active_owners={"kim"}, retry_after="0.3",
                   directory_fail_plan={("GET", "/v1/items?page=1"): 2})
        self.assertEqual(self.do_sync(), 0)
        self.assertEqual(self.read("summary.jsonl"), [
            {"owner": "kim", "open_items": 1, "done_items": 0,
             "total_points": 3}])
        gets = [e for e in self.directory.log if e["method"] == "GET"]
        self.assertEqual([g["status"] for g in gets], [429, 429, 200])
        for first, second in zip(gets, gets[1:]):
            if first["status"] == 429:
                self.assertGreaterEqual(second["t"] - first["t"], 0.3 - 0.15)
        self.assertNoRejectedWrites()

    def test_post_429_is_retried_with_the_same_body_and_key(self):
        self.start(item_pages=[[]],
                   account_pages=[[ {"id": "a-1", "owner": "kim",
                                     "active": True}]],
                   active_owners={"kim"}, retry_after="0.3",
                   ledger_fail_plan={("POST", "/v1/summaries"): 1})
        self.assertEqual(self.do_sync(), 0)
        posts = self.ledger.all_posts()
        self.assertEqual([p["status"] for p in posts], [429, 201])
        self.assertEqual(posts[0]["body"], posts[1]["body"])
        self.assertEqual(posts[0]["headers"].get("Idempotency-Key"),
                         "sync-kim")
        self.assertEqual(self.read("summary.jsonl"), [
            {"owner": "kim", "open_items": 0, "done_items": 0,
             "total_points": 0}])
        self.assertNoRejectedWrites()

    def test_ledger_cooldown_does_not_block_the_directory(self):
        # A 429 from the ledger delays only ledger requests: the directory
        # listing is still read in full and the run succeeds.
        items = [{"id": "i-1", "owner": "kim", "status": "done", "points": 2}]
        accounts = [[{"id": "a-1", "owner": "kim", "active": True}]]
        self.start(item_pages=[items], account_pages=accounts,
                   active_owners={"kim"}, retry_after="0.2",
                   ledger_fail_plan={("GET", "/v1/accounts?page=1"): 1})
        self.assertEqual(self.do_sync(), 0)
        self.assertEqual(self.read("summary.jsonl"), [
            {"owner": "kim", "open_items": 0, "done_items": 1,
             "total_points": 2}])
        self.assertNoRejectedWrites()


class IdempotencyTest(SyncCase):
    def test_second_run_writes_nothing_and_keeps_every_file(self):
        items = [[{"id": "i-1", "owner": "kim", "status": "open", "points": 3},
                  {"id": "i-2", "owner": "old", "status": "done",
                   "points": 1}],
                 [{"id": "i-1", "owner": "kim", "status": "open", "points": 3},
                  {"id": "i-3", "owner": "kim", "status": "done"}]]
        accounts = [[{"id": "a-1", "owner": "kim", "active": True},
                     {"id": "a-2", "owner": "stall", "active": "maybe"}]]
        self.start(item_pages=items, account_pages=accounts,
                   active_owners={"kim"})
        self.assertEqual(self.do_sync(), 0)
        first = {name: self.raw(name) for name in
                 ("orphans.jsonl", "errors.jsonl", "summary.jsonl")}
        self.assertEqual(json.loads(first["summary.jsonl"].decode()), {
            "owner": "kim", "open_items": 1, "done_items": 0,
            "total_points": 3})
        posts_after_first = len(self.ledger.posts())
        self.assertEqual(posts_after_first, 1)
        self.assertEqual(self.do_sync(), 0)
        for name in ("orphans.jsonl", "errors.jsonl", "summary.jsonl"):
            self.assertEqual(self.raw(name), first[name],
                             "%s changed on the second run" % name)
        self.assertEqual(len(self.ledger.posts()), posts_after_first,
                         "second run issued write requests")
        self.assertNoRejectedWrites()


class CliWiringTest(SyncCase):
    def test_sync_through_main_creates_out_dir_and_exits_zero(self):
        self.start(item_pages=[[ {"id": "i-1", "owner": "kim",
                                  "status": "open", "points": 1}]],
                   account_pages=[[ {"id": "a-1", "owner": "kim",
                                     "active": True}]],
                   active_owners={"kim"})
        config_path = os.path.join(self.tmp.name, "config.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump({"directory_url": self.config.directory_url,
                       "ledger_url": self.config.ledger_url}, handle)
        nested = os.path.join(self.tmp.name, "deep", "out")
        code = orgsync_main(["sync", "--config", config_path,
                             "--out-dir", nested])
        self.assertEqual(code, 0)
        with open(os.path.join(nested, "summary.jsonl"),
                  encoding="utf-8") as handle:
            self.assertEqual(
                [json.loads(line) for line in handle if line.strip()],
                [{"owner": "kim", "open_items": 1, "done_items": 0,
                  "total_points": 1}])


if __name__ == "__main__":
    unittest.main()
