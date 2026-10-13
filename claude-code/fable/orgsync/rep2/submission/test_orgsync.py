"""End-to-end tests for `orgsync sync` against in-process fixture services.

Each test starts a private Directory and Ledger (real HTTP servers on
127.0.0.1) seeded with pages of records, runs the sync, and asserts on the
output files and on the requests the services logged. Nothing here touches
`config.json` or the environment's services, so the suite is deterministic
and can run anywhere.
"""

import http.server
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse

from orgsync.__main__ import main as orgsync_main
from orgsync.config import Config
from orgsync.sync import run_sync

REJECTION_STATUSES = (400, 409, 422)


class _ServiceState:
    """Mutable state behind one fixture service."""

    def __init__(self, kind, pages=None, summaries=None, throttle=None):
        self.kind = kind  # "directory" or "ledger"
        self.pages = pages or {}  # path -> list of pages, each a list of records
        self.summaries = list(summaries or [])
        self.idempotency = {}  # Idempotency-Key -> body accepted under it
        self.throttle = dict(throttle or {})  # key -> [hits_left, retry_after]
        self.requests = []  # dicts: ts, method, path, page, status

    def log(self, method, path, page, status):
        self.requests.append({"ts": time.monotonic(), "method": method,
                              "path": path, "page": page, "status": status})

    def active_owners(self):
        owners = set()
        for page in self.pages.get("/v1/accounts", []):
            for record in page:
                if (isinstance(record, dict)
                        and isinstance(record.get("id"), str)
                        and isinstance(record.get("owner"), str)
                        and isinstance(record.get("active"), bool)
                        and record["active"]):
                    owners.add(record["owner"])
        return owners


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep test output clean
        pass

    def _reply(self, status, payload=None, headers=()):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _take_throttle(self, key):
        entry = self.server.state.throttle.get(key)
        if entry and entry[0] > 0:
            entry[0] -= 1
            return entry[1]
        return None

    def do_GET(self):
        state = self.server.state
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        page = int(query.get("page", ["1"])[0])

        retry_after = self._take_throttle(("GET", parsed.path, page))
        if retry_after is not None:
            state.log("GET", parsed.path, page, 429)
            self._reply(429, None, [("Retry-After", str(retry_after))])
            return

        name = parsed.path.rsplit("/", 1)[-1]
        if state.kind == "ledger" and parsed.path == "/v1/summaries":
            records = state.summaries
            pages = [records[i:i + 3] for i in range(0, len(records), 3)]
        else:
            pages = state.pages.get(parsed.path)
        if pages is None:
            state.log("GET", parsed.path, page, 404)
            self._reply(404, {"error": "no such collection"})
            return

        index = page - 1
        chunk = pages[index] if 0 <= index < len(pages) else []
        next_page = page + 1 if index + 1 < len(pages) else None
        state.log("GET", parsed.path, page, 200)
        self._reply(200, {name: chunk, "next_page": next_page})

    def do_POST(self):
        state = self.server.state
        parsed = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)

        if state.kind != "ledger" or parsed.path != "/v1/summaries":
            state.log("POST", parsed.path, None, 404)
            self._reply(404, {"error": "no such endpoint"})
            return

        retry_after = self._take_throttle(("POST", parsed.path))
        if retry_after is not None:
            state.log("POST", parsed.path, None, 429)
            self._reply(429, None, [("Retry-After", str(retry_after))])
            return

        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = None
        key = self.headers.get("Idempotency-Key")

        if (not isinstance(body, dict)
                or body.get("owner") not in state.active_owners()):
            status = 422
        elif key in state.idempotency:
            status = 200 if state.idempotency[key] == body else 409
        else:
            state.idempotency[key] = body
            state.summaries.append(body)
            status = 201
        state.log("POST", parsed.path, None, status)
        self._reply(status, {"ok": status in (200, 201)})


def _start_service(state):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    server.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# --- Seed data used by most tests -----------------------------------------

def item(id, owner, status, points, **extra):
    record = {"id": id, "owner": owner, "status": status, "points": points}
    record.update(extra)
    return record


WELL_FORMED_DUP = item("i-3", "bo", "open", 5)
MALFORMED_ITEMS = [
    {"id": "i-9", "owner": "amara", "status": "open"},          # missing points
    item("i-12", "amara", "open", True),                        # bool is not int
    item("i-13", "amara", "open", -1),                          # negative points
    item("i-14", "amara", "OPEN", 1),                           # bad status
    item(7, "amara", "open", 1),                                # id not a string
    "not-even-an-object",
]

ITEM_PAGES = [
    [item("i-1", "amara", "open", 3), item("i-2", "amara", "done", 2),
     WELL_FORMED_DUP, item("i-4", "chen", "open", 8)],
    [item("i-5", "eli", "done", 1), item("i-6", "zara", "open", 4),
     item("i-7", "fen", "open", 2), item("i-8", "bo", "done", 1)],
    # i-3 delivered again; i-9 (malformed) delivered twice across pages.
    [WELL_FORMED_DUP, MALFORMED_ITEMS[0],
     item("i-10", "amara", "open", 7, note="extra fields are fine"),
     item("i-11", "eli", "open", 2)] + MALFORMED_ITEMS,
]

MALFORMED_ACCOUNTS = [
    {"id": "a-6", "owner": "fen", "active": "yes"},             # string, not bool
    {"id": "a-8", "owner": "hal"},                              # missing active
    41,
]

ACCOUNT_PAGES = [
    [{"id": "a-1", "owner": "amara", "active": True},
     {"id": "a-2", "owner": "bo", "active": True},
     {"id": "a-3", "owner": "chen", "active": False}],
    [{"id": "a-4", "owner": "dee", "active": True, "tier": "gold"},
     {"id": "a-5", "owner": "eli", "active": True}] + MALFORMED_ACCOUNTS,
    [{"id": "a-7", "owner": "gus", "active": False}],
]

EXPECTED_SUMMARIES = {
    "amara": {"owner": "amara", "open_items": 2, "done_items": 1, "total_points": 12},
    "bo": {"owner": "bo", "open_items": 1, "done_items": 1, "total_points": 6},
    "dee": {"owner": "dee", "open_items": 0, "done_items": 0, "total_points": 0},
    "eli": {"owner": "eli", "open_items": 1, "done_items": 1, "total_points": 3},
}
EXPECTED_ORPHAN_IDS = {"i-4", "i-6", "i-7"}  # inactive, unknown, malformed-account owners


def read_jsonl(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


class SyncFixtureTest(unittest.TestCase):
    """Base: a fresh Directory + Ledger and a fresh out dir per test."""

    directory_throttle = None
    ledger_throttle = None
    seeded_summaries = None

    def setUp(self):
        self.directory_state = _ServiceState(
            "directory", pages={"/v1/items": [list(p) for p in ITEM_PAGES]},
            throttle=self.directory_throttle)
        self.ledger_state = _ServiceState(
            "ledger", pages={"/v1/accounts": [list(p) for p in ACCOUNT_PAGES]},
            summaries=self.seeded_summaries, throttle=self.ledger_throttle)
        self.servers = [_start_service(self.directory_state),
                        _start_service(self.ledger_state)]
        for server in self.servers:  # LIFO: shutdown runs before server_close
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        self.config = Config(
            "http://127.0.0.1:%d" % self.servers[0].server_address[1],
            "http://127.0.0.1:%d" % self.servers[1].server_address[1])
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = os.path.join(self.tmp.name, "out")

    def out_path(self, name):
        return os.path.join(self.out_dir, name)

    def assert_no_rejected_writes(self):
        rejected = [r for r in self.ledger_state.requests
                    if r["method"] == "POST" and r["status"] in REJECTION_STATUSES]
        self.assertEqual(rejected, [],
                         "sync sent POSTs the ledger rejected: %r" % rejected)


class FullSyncTest(SyncFixtureTest):
    def test_one_pass_produces_correct_summaries_orphans_and_errors(self):
        self.assertEqual(run_sync(self.config, self.out_dir), 0)

        # One accepted summary per active owner, with the right arithmetic.
        posted = {b["owner"]: b for b in self.ledger_state.summaries}
        self.assertEqual(posted, EXPECTED_SUMMARIES)
        self.assertEqual(
            {r["status"] for r in self.ledger_state.requests if r["method"] == "POST"},
            {201})
        self.assert_no_rejected_writes()

        # Idempotency keys follow the sync-<owner> convention.
        self.assertEqual(set(self.ledger_state.idempotency),
                         {"sync-" + owner for owner in EXPECTED_SUMMARIES})

        # summary.jsonl records exactly the bodies posted.
        recorded = read_jsonl(self.out_path("summary.jsonl"))
        self.assertEqual({r["owner"]: r for r in recorded}, EXPECTED_SUMMARIES)
        self.assertEqual(len(recorded), len(EXPECTED_SUMMARIES))

        # Orphans: each exactly once, as received, and only orphans.
        orphans = read_jsonl(self.out_path("orphans.jsonl"))
        self.assertEqual({o["id"] for o in orphans}, EXPECTED_ORPHAN_IDS)
        self.assertEqual(len(orphans), len(EXPECTED_ORPHAN_IDS))
        self.assertIn(item("i-4", "chen", "open", 8), orphans)

        # Errors: every malformed record exactly once, tagged by service.
        errors = read_jsonl(self.out_path("errors.jsonl"))
        expected_errors = ([{"service": "directory", "record": r}
                            for r in MALFORMED_ITEMS]
                           + [{"service": "ledger", "record": r}
                              for r in MALFORMED_ACCOUNTS])
        for entry in expected_errors:
            self.assertIn(entry, errors)
        self.assertEqual(len(errors), len(expected_errors))  # dups written once

    def test_pagination_followed_to_the_last_page(self):
        run_sync(self.config, self.out_dir)
        item_pages = sorted(r["page"] for r in self.directory_state.requests
                            if r["path"] == "/v1/items")
        self.assertEqual(item_pages, [1, 2, 3])
        account_pages = sorted(r["page"] for r in self.ledger_state.requests
                               if r["path"] == "/v1/accounts")
        self.assertEqual(account_pages, [1, 2, 3])

    def test_cli_entry_point_runs_sync_and_exits_zero(self):
        config_path = os.path.join(self.tmp.name, "config.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump({"directory_url": self.config.directory_url,
                       "ledger_url": self.config.ledger_url}, handle)
        code = orgsync_main(["sync", "--config", config_path,
                             "--out-dir", self.out_dir])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.out_path("summary.jsonl")))
        self.assert_no_rejected_writes()


class SecondRunIsIdempotentTest(SyncFixtureTest):
    def test_second_run_makes_no_writes_and_leaves_files_identical(self):
        run_sync(self.config, self.out_dir)
        before = {name: read_bytes(self.out_path(name))
                  for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")}
        writes_before = sum(1 for r in self.ledger_state.requests
                            if r["method"] == "POST")

        self.assertEqual(run_sync(self.config, self.out_dir), 0)

        writes_after = sum(1 for r in self.ledger_state.requests
                           if r["method"] == "POST")
        self.assertEqual(writes_after, writes_before,
                         "second run issued write requests")
        for name, content in before.items():
            self.assertEqual(read_bytes(self.out_path(name)), content,
                             "%s changed on the second run" % name)
        self.assert_no_rejected_writes()


class PreexistingSummaryTest(SyncFixtureTest):
    # The ledger already holds a summary for amara (posted by someone else,
    # with different numbers). Sync must not post for amara — doing so would
    # be rejected or would duplicate — and must still post the rest.
    seeded_summaries = [
        {"owner": "amara", "open_items": 99, "done_items": 99, "total_points": 99}]

    def test_only_missing_summaries_are_posted(self):
        self.assertEqual(run_sync(self.config, self.out_dir), 0)
        posted_owners = {b["owner"] for b in self.ledger_state.summaries[1:]}
        self.assertEqual(posted_owners, {"bo", "dee", "eli"})
        self.assert_no_rejected_writes()
        recorded = read_jsonl(self.out_path("summary.jsonl"))
        self.assertEqual({r["owner"] for r in recorded}, {"bo", "dee", "eli"})


class RateLimitTest(SyncFixtureTest):
    RETRY_AFTER = 1.0
    # First hit on items page 2 and the first summary POST get a 429.
    directory_throttle = {("GET", "/v1/items", 2): [1, RETRY_AFTER]}
    ledger_throttle = {("POST", "/v1/summaries"): [1, RETRY_AFTER]}

    def test_429_waits_retries_and_loses_nothing(self):
        self.assertEqual(run_sync(self.config, self.out_dir), 0)

        for state in (self.directory_state, self.ledger_state):
            requests = state.requests
            hits = [i for i, r in enumerate(requests) if r["status"] == 429]
            self.assertEqual(len(hits), 1, "throttle was not consumed")
            index = hits[0]
            self.assertLess(index + 1, len(requests),
                            "429 was never retried")
            gap = requests[index + 1]["ts"] - requests[index]["ts"]
            self.assertGreaterEqual(
                gap, self.RETRY_AFTER - 0.05,
                "%s contacted again %.3fs after a 429" % (state.kind, gap))
            # The retried request is the same one that was throttled.
            self.assertEqual(requests[index + 1]["method"], requests[index]["method"])
            self.assertEqual(requests[index + 1]["path"], requests[index]["path"])
            self.assertEqual(requests[index + 1]["page"], requests[index]["page"])

        # The 429s cost no data and no summaries.
        self.assertEqual({b["owner"] for b in self.ledger_state.summaries},
                         set(EXPECTED_SUMMARIES))
        orphans = read_jsonl(self.out_path("orphans.jsonl"))
        self.assertEqual({o["id"] for o in orphans}, EXPECTED_ORPHAN_IDS)
        self.assert_no_rejected_writes()


class ActiveOwnerWithNoItemsTest(SyncFixtureTest):
    def test_gets_an_all_zero_summary(self):
        run_sync(self.config, self.out_dir)
        self.assertEqual(
            self.ledger_state.idempotency.get("sync-dee"),
            {"owner": "dee", "open_items": 0, "done_items": 0, "total_points": 0})


if __name__ == "__main__":
    unittest.main()
