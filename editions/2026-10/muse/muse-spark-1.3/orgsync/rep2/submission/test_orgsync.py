"""Tests for `orgsync sync`, against in-process fake Directory/Ledger services.

Standard library only. The fakes implement just enough of the two services'
HTTP contracts (pagination, summaries already posted, idempotent POST with
`Idempotency-Key`, `429` + `Retry-After`) to exercise every behavior the
`sync` command promises.
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

from orgsync.config import Config
from orgsync.sync import (
    is_well_formed_account,
    is_well_formed_item,
    run_sync,
)


class FakeLedgerState:
    """Mutable backing store shared by one fake Directory + Ledger pair."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.items: list = []
        self.accounts: list = []
        self.summaries: list = []
        self.page_size = 2
        # path -> list of Retry-After values (None means "no header");
        # one entry is consumed per GET, then the request is served.
        self.get_429: dict = {}
        # Same, consumed per POST to /v1/summaries.
        self.post_429: list = []
        self.requests: list = []
        self.keys_seen: dict = {}


def _paginate(records, page, size):
    start = (page - 1) * size
    chunk = records[start:start + size]
    nxt = page + 1 if start + size < len(records) else None
    return chunk, nxt


def _valid_summary_shape(body) -> bool:
    if not isinstance(body, dict):
        return False
    if not isinstance(body.get("owner"), str):
        return False
    for key in ("open_items", "done_items", "total_points"):
        value = body.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        if value < 0:
            return False
    return True


class _Handler(BaseHTTPRequestHandler):
    state: FakeLedgerState = None  # set per server

    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, status, body=None, retry_after=None):
        payload = b"" if body is None else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        if retry_after is not None:
            self.send_header("Retry-After", str(retry_after))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _send_429_no_header(self):
        self.send_response(429)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _log(self, method, path, query, key, body, status):
        with self.state.lock:
            self.state.requests.append({
                "t": time.monotonic(), "method": method, "path": path,
                "query": query, "key": key, "body": body, "status": status,
            })

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        page = int(query.get("page", ["1"])[0])
        with self.state.lock:
            queued = self.state.get_429.get(parsed.path, [])
            retry = queued.pop(0) if queued else "no"
        if retry != "no":
            if retry is None:
                self._send_429_no_header()
            else:
                self._send(429, {"error": "slow down"}, retry_after=retry)
            self._log("GET", parsed.path, query, None, None, 429)
            return
        with self.state.lock:
            if parsed.path == "/v1/items":
                records = list(self.state.items)
                key = "items"
            elif parsed.path == "/v1/accounts":
                records = list(self.state.accounts)
                key = "accounts"
            elif parsed.path == "/v1/summaries":
                records = list(self.state.summaries)
                key = "summaries"
            else:
                records, key = None, None
            size = self.state.page_size
        if records is None:
            self._send(404, {"error": "unknown path"})
            self._log("GET", parsed.path, query, None, None, 404)
            return
        chunk, nxt = _paginate(records, page, size)
        self._send(200, {key: chunk, "next_page": nxt})
        self._log("GET", parsed.path, query, None, None, 200)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            body = None
        key = self.headers.get("Idempotency-Key")
        with self.state.lock:
            retry = self.state.post_429.pop(0) if self.state.post_429 else "no"
        if retry != "no":
            if retry is None:
                self._send_429_no_header()
            else:
                self._send(429, {"error": "slow down"}, retry_after=retry)
            self._log("POST", parsed.path, {}, key, body, 429)
            return
        if parsed.path != "/v1/summaries":
            self._send(404, {"error": "unknown path"})
            self._log("POST", parsed.path, {}, key, body, 404)
            return
        with self.state.lock:
            if key in self.state.keys_seen:
                if self.state.keys_seen[key] == body:
                    status = 200
                else:
                    self._send(409, {"error": "idempotency conflict"})
                    status = 409
                    self._log("POST", parsed.path, {}, key, body, status)
                    return
                self._log("POST", parsed.path, {}, key, body, status)
                self._send(status, {"ok": True})
                return
            active = {a["owner"] for a in self.state.accounts
                      if isinstance(a, dict) and isinstance(a.get("owner"), str)
                      and a.get("active") is True}
            if not _valid_summary_shape(body) or body["owner"] not in active:
                self._send(422, {"error": "invalid summary"})
                self._log("POST", parsed.path, {}, key, body, 422)
                return
            self.state.keys_seen[key] = body
            self.state.summaries.append(body)
        self._send(201, {"ok": True})
        self._log("POST", parsed.path, {}, key, body, 201)


def seed_state() -> FakeLedgerState:
    """The shared fixture: active/inactive owners, dupes, orphans, junk."""
    state = FakeLedgerState()
    state.accounts = [
        {"id": "a1", "owner": "amy", "active": True},
        {"id": "a2", "owner": "bob", "active": True},
        {"id": "a3", "owner": "cid", "active": False},
        {"id": "a4", "owner": "zed", "active": True},
        {"id": "a1", "owner": "amy", "active": True},  # repeat delivery
        {"id": "a5", "owner": "hal", "active": "yes"},  # malformed
        42,  # malformed
    ]
    state.items = [
        {"id": "i1", "owner": "amy", "status": "open", "points": 3},
        {"id": "i2", "owner": "amy", "status": "open", "points": 4},
        {"id": "i3", "owner": "amy", "status": "done", "points": 2},
        {"id": "i1", "owner": "amy", "status": "open", "points": 3},  # dupe
        {"id": "i4", "owner": "bob", "status": "done", "points": 5},
        {"id": "i5", "owner": "cid", "status": "open", "points": 9},  # orphan
        {"id": "i6", "owner": "ghost", "status": "open", "points": 1},  # orphan
        {"id": "i6", "owner": "ghost", "status": "open", "points": 1},  # dupe
        {"id": "i7", "owner": "hal", "status": "open", "points": 2},  # orphan
        {"id": "i8", "owner": "amy", "status": "open"},  # malformed
        {"id": "i9", "owner": "amy", "status": "open", "points": "3"},  # bad
        {"id": "i10", "owner": "amy", "status": "open", "points": True},  # bad
        {"id": "i11", "owner": "amy", "status": "open", "points": -1},  # bad
        {"id": "i12", "owner": "amy", "status": "weird", "points": 1},  # bad
        "oops",  # malformed
    ]
    return state


EXPECTED_SUMMARIES = {
    "amy": {"owner": "amy", "open_items": 2, "done_items": 1, "total_points": 9},
    "bob": {"owner": "bob", "open_items": 0, "done_items": 1, "total_points": 5},
    "zed": {"owner": "zed", "open_items": 0, "done_items": 0, "total_points": 0},
}

EXPECTED_ORPHANS = [
    {"id": "i5", "owner": "cid", "status": "open", "points": 9},
    {"id": "i6", "owner": "ghost", "status": "open", "points": 1},
    {"id": "i7", "owner": "hal", "status": "open", "points": 2},
]

EXPECTED_ERRORS = [
    ("directory", {"id": "i8", "owner": "amy", "status": "open"}),
    ("directory", {"id": "i9", "owner": "amy", "status": "open", "points": "3"}),
    ("directory", {"id": "i10", "owner": "amy", "status": "open", "points": True}),
    ("directory", {"id": "i11", "owner": "amy", "status": "open", "points": -1}),
    ("directory", {"id": "i12", "owner": "amy", "status": "weird", "points": 1}),
    ("directory", "oops"),
    ("ledger", {"id": "a5", "owner": "hal", "active": "yes"}),
    ("ledger", 42),
]


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.state = seed_state()
        self.servers = []
        self.base_urls = []
        for _ in range(2):
            handler = type("_H", (_Handler,), {"state": self.state})
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            server.daemon_threads = True
            port = server.server_address[1]
            self.base_urls.append("http://127.0.0.1:%d" % port)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.servers.append(server)
        self.addCleanup(self.shutdown_servers)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = os.path.join(self.tmp.name, "out")
        self.config = Config(directory_url=self.base_urls[0],
                             ledger_url=self.base_urls[1])

    def shutdown_servers(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()

    def sync(self):
        return run_sync(self.config, self.out_dir)

    def read_jsonl(self, name):
        with open(os.path.join(self.out_dir, name), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def posts(self):
        with self.state.lock:
            return [r for r in self.state.requests if r["method"] == "POST"]

    # --- core aggregation and posting ---

    def test_exit_zero_and_creates_out_dir(self):
        self.assertEqual(self.sync(), 0)
        for name in ("orphans.jsonl", "errors.jsonl", "summary.jsonl"):
            self.assertTrue(os.path.isfile(os.path.join(self.out_dir, name)))

    def test_summary_file_holds_every_required_summary(self):
        self.sync()
        got = {s["owner"]: s for s in self.read_jsonl("summary.jsonl")}
        self.assertEqual(got, EXPECTED_SUMMARIES)

    def test_posts_only_missing_summaries_with_idempotency_keys(self):
        self.state.summaries.append(dict(EXPECTED_SUMMARIES["bob"]))
        self.sync()
        posts = self.posts()
        self.assertEqual(len(posts), 2)
        by_owner = {p["body"]["owner"]: p for p in posts}
        self.assertEqual(set(by_owner), {"amy", "zed"})
        for owner, post in by_owner.items():
            self.assertEqual(post["key"], "sync-" + owner)
            self.assertEqual(post["body"], EXPECTED_SUMMARIES[owner])
            self.assertEqual(post["status"], 201)
            self.assertEqual(post["path"], "/v1/summaries")

    def test_duplicate_deliveries_counted_once(self):
        self.sync()
        got = {s["owner"]: s for s in self.read_jsonl("summary.jsonl")}
        self.assertEqual(got["amy"]["open_items"], 2)
        self.assertEqual(got["amy"]["total_points"], 9)

    def test_active_account_without_items_gets_zeros(self):
        self.sync()
        got = {s["owner"]: s for s in self.read_jsonl("summary.jsonl")}
        self.assertEqual(got["zed"],
                         {"owner": "zed", "open_items": 0, "done_items": 0,
                          "total_points": 0})

    def test_inactive_owner_gets_no_summary_and_no_post(self):
        self.sync()
        owners = [s["owner"] for s in self.read_jsonl("summary.jsonl")]
        self.assertNotIn("cid", owners)
        self.assertNotIn("cid", [p["body"]["owner"] for p in self.posts()])

    def test_stale_existing_summary_is_left_alone(self):
        self.state.summaries.append(
            {"owner": "amy", "open_items": 99, "done_items": 99,
             "total_points": 99})
        self.sync()
        self.assertNotIn(
            "amy", [p["body"]["owner"] for p in self.posts()])
        got = {s["owner"]: s for s in self.read_jsonl("summary.jsonl")}
        self.assertEqual(got["amy"], EXPECTED_SUMMARIES["amy"])

    def test_pagination_reads_every_page(self):
        counts = {"/v1/items": len(self.state.items),
                  "/v1/accounts": len(self.state.accounts),
                  "/v1/summaries": len(self.state.summaries)}
        self.sync()
        with self.state.lock:
            got = [(r["path"], r["query"].get("page", [None])[0])
                   for r in self.state.requests if r["method"] == "GET"]
        for path, count in counts.items():
            pages = sorted(int(p) for (pa, p) in got if pa == path)
            expect = list(range(1, (count + self.state.page_size - 1)
                                 // self.state.page_size + 1)) or [1]
            self.assertEqual(pages, expect, path)

    # --- orphans and errors ---

    def test_orphans_exactly_once(self):
        self.sync()
        self.assertEqual(
            sorted(json.dumps(o, sort_keys=True)
                   for o in self.read_jsonl("orphans.jsonl")),
            sorted(json.dumps(o, sort_keys=True) for o in EXPECTED_ORPHANS))

    def test_errors_exact_and_counts_unaffected(self):
        self.sync()
        got = sorted((e["service"], json.dumps(e["record"], sort_keys=True))
                     for e in self.read_jsonl("errors.jsonl"))
        want = sorted((s, json.dumps(r, sort_keys=True))
                      for (s, r) in EXPECTED_ERRORS)
        self.assertEqual(got, want)
        # None of the junk leaked into any summary.
        for summary in self.read_jsonl("summary.jsonl"):
            self.assertEqual(summary, EXPECTED_SUMMARIES[summary["owner"]])

    def test_malformed_account_owner_is_not_active(self):
        self.sync()
        owners = [s["owner"] for s in self.read_jsonl("summary.jsonl")]
        self.assertNotIn("hal", owners)
        orphans = self.read_jsonl("orphans.jsonl")
        self.assertIn({"id": "i7", "owner": "hal", "status": "open",
                       "points": 2}, orphans)

    # --- rate limits and idempotency ---

    def test_get_429_is_retried_after_retry_after(self):
        self.state.get_429["/v1/items"] = [1]
        before = time.monotonic()
        self.assertEqual(self.sync(), 0)
        with self.state.lock:
            hits = [r["t"] - before for r in self.state.requests
                    if r["method"] == "GET" and r["path"] == "/v1/items"
                    and r["query"].get("page") == ["1"]]
        self.assertEqual(len(hits), 2)
        self.assertGreaterEqual(hits[1] - hits[0], 1.0)
        got = {s["owner"]: s for s in self.read_jsonl("summary.jsonl")}
        self.assertEqual(got, EXPECTED_SUMMARIES)

    def test_post_429_is_retried(self):
        self.state.post_429.append(0)
        self.assertEqual(self.sync(), 0)
        posts = [p for p in self.posts() if p["status"] != 429]
        self.assertEqual(len(posts), 3)
        with self.state.lock:
            attempts = [r for r in self.state.requests if r["method"] == "POST"]
        self.assertTrue(any(r["status"] == 429 for r in attempts))
        self.assertFalse(any(r["status"] not in (200, 201, 429)
                             for r in attempts))

    def test_429_without_header_is_retried(self):
        self.state.get_429["/v1/accounts"] = [None]
        self.assertEqual(self.sync(), 0)
        got = {s["owner"]: s for s in self.read_jsonl("summary.jsonl")}
        self.assertEqual(got, EXPECTED_SUMMARIES)

    def test_second_run_issues_no_writes_and_same_files(self):
        self.assertEqual(self.sync(), 0)
        first = {}
        for name in ("orphans.jsonl", "errors.jsonl", "summary.jsonl"):
            with open(os.path.join(self.out_dir, name), "rb") as handle:
                first[name] = handle.read()
        with self.state.lock:
            mark = len(self.state.requests)
        self.assertEqual(run_sync(self.config, self.out_dir), 0)
        with self.state.lock:
            extra = self.state.requests[mark:]
        self.assertEqual(
            [r for r in extra if r["method"] != "GET"], [])
        for name, content in first.items():
            with open(os.path.join(self.out_dir, name), "rb") as handle:
                self.assertEqual(handle.read(), content, name)

    def test_no_rejected_write(self):
        self.sync()
        run_sync(self.config, self.out_dir)
        with self.state.lock:
            bad = [r for r in self.state.requests
                   if r["method"] == "POST" and r["status"] in (400, 409, 422)]
        self.assertEqual(bad, [])


class ValidationTest(unittest.TestCase):
    def test_item_boundaries(self):
        good = {"id": "i", "owner": "o", "status": "open", "points": 0}
        self.assertTrue(is_well_formed_item(good))
        self.assertTrue(is_well_formed_item(
            dict(good, status="done", extra="ignored")))
        for bad in (
            None, "x", 3, [],
            {"owner": "o", "status": "open", "points": 1},
            {"id": 1, "owner": "o", "status": "open", "points": 1},
            {"id": "i", "owner": "o", "status": "OPEN", "points": 1},
            {"id": "i", "owner": "o", "status": "open", "points": -1},
            {"id": "i", "owner": "o", "status": "open", "points": "1"},
            {"id": "i", "owner": "o", "status": "open", "points": 1.0},
            {"id": "i", "owner": "o", "status": "open", "points": True},
            {"id": "i", "owner": "o", "status": "open", "points": None},
        ):
            with self.subTest(bad=bad):
                self.assertFalse(is_well_formed_item(bad))

    def test_account_boundaries(self):
        good = {"id": "a", "owner": "o", "active": False}
        self.assertTrue(is_well_formed_account(good))
        self.assertTrue(is_well_formed_account(
            dict(good, active=True, extra=1)))
        for bad in (
            None, [],
            {"id": "a", "owner": "o"},
            {"id": "a", "owner": "o", "active": "yes"},
            {"id": "a", "owner": "o", "active": 1},
            {"id": "a", "owner": 7, "active": True},
        ):
            with self.subTest(bad=bad):
                self.assertFalse(is_well_formed_account(bad))


if __name__ == "__main__":
    unittest.main()
