"""Behavioral test suite for `orgsync sync`, standard library only.

Two in-process fixture services (a Directory and a Ledger, built on
`http.server`) stand in for the real ones, so every rule — pagination,
malformed records, orphans, idempotent posts, 429/Retry-After handling — is
exercised over real HTTP without touching anything outside 127.0.0.1.

Run from the repo root: `python3 -m unittest test_orgsync`.
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

from orgsync.sync import parse_account, parse_item, summary_owner  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture services.
# ---------------------------------------------------------------------------

class FixtureHandler(BaseHTTPRequestHandler):
    """Every fixture server speaks this one handler: JSON in, JSON out."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        pass

    def _send(self, status, payload=None, extra_headers=None):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        self.server.serve_request(self, "GET")

    def do_POST(self):
        self.server.serve_request(self, "POST")


class FixtureServer(ThreadingHTTPServer):
    """Base fixture: logs every request, can be scripted to answer 429."""

    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.lock = threading.Lock()
        self.log = []
        #: (method, path) -> [retry_after_seconds, ...] consumed front-first.
        self.rate_limits = {}

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server_address[1]

    def _note(self, request, status):
        entry = {"t": time.monotonic(), "method": request.command,
                 "path": urlparse(request.path).path, "status": status}
        if request.command == "POST":
            entry["idempotency_key"] = request.headers.get("Idempotency-Key")
        with self.lock:
            self.log.append(entry)

    def _limited(self, method, path):
        with self.lock:
            pending = self.rate_limits.get((method, path))
            if pending:
                return pending.pop(0)
        return None

    def serve_request(self, request, method):
        parsed = urlparse(request.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        if method == "POST":
            length = int(request.headers.get("Content-Length") or 0)
            request.fixture_body = json.loads(request.rfile.read(length) or b"null")
        retry_after = self._limited(method, path)
        if retry_after is not None:
            self._note(request, 429)
            request._send(429, None, {"Retry-After": "%.3f" % retry_after})
            return
        status = self.dispatch(request, method, path, query)
        self._note(request, status)

    def _paginate(self, request, rows, key):
        query = parse_qs(urlparse(request.path).query)
        page = int((query.get("page") or ["1"])[0])
        size = self.page_size
        chunk = rows[(page - 1) * size: page * size]
        payload = {key: chunk,
                   "next_page": page + 1 if page * size < len(rows) else None}
        request._send(200, payload)
        return 200


class DirectoryServer(FixtureServer):
    """The Directory: pages of work items, nothing else."""

    def __init__(self, items, page_size=2):
        super().__init__()
        self.items = items
        self.page_size = page_size

    def dispatch(self, request, method, path, query):
        if method == "GET" and path == "/v1/items":
            return self._paginate(request, self.items, "items")
        request._send(404, {"error": "not found"})
        return 404


class LedgerServer(FixtureServer):
    """The Ledger: accounts, posted summaries, and their semantics."""

    def __init__(self, accounts, page_size=2, existing=None):
        super().__init__()
        self.accounts = accounts
        self.page_size = page_size
        self.by_key = {}
        self.by_owner = {}
        for key, body in (existing or {}).items():
            self.by_key[key] = body
            self.by_owner[body["owner"]] = body

    def _active_owners(self):
        owners = set()
        for record in self.accounts:
            if (isinstance(record, dict) and isinstance(record.get("owner"), str)
                    and record.get("active") is True):
                owners.add(record["owner"])
        return owners

    def dispatch(self, request, method, path, query):
        if method == "GET" and path == "/v1/accounts":
            return self._paginate(request, self.accounts, "accounts")
        if method == "GET" and path == "/v1/summaries":
            return self._paginate(request, list(self.by_owner.values()), "summaries")
        if method == "POST" and path == "/v1/summaries":
            return self._post_summary(request)
        request._send(404, {"error": "not found"})
        return 404

    def _post_summary(self, request):
        body = getattr(request, "fixture_body", None)
        key = request.headers.get("Idempotency-Key")
        if not isinstance(body, dict) or not isinstance(body.get("owner"), str):
            request._send(400, {"error": "invalid body"})
            return 400
        if body["owner"] not in self._active_owners():
            request._send(422, {"error": "no such active owner"})
            return 422
        if key in self.by_key:
            if self.by_key[key] == body:
                request._send(200, {"status": "replay"})
                return 200
            request._send(409, {"error": "idempotency conflict"})
            return 409
        self.by_key[key] = body
        self.by_owner[body["owner"]] = body
        request._send(201, {"status": "stored"})
        return 201


# ---------------------------------------------------------------------------
# Fixture data: two pages and then some of everything the rules describe.
# ---------------------------------------------------------------------------

ACCOUNTS = [
    {"id": "acct-01", "owner": "amara", "active": True},
    {"id": "acct-02", "owner": "bo", "active": True},
    {"id": "acct-03", "owner": "chen", "active": False},
    {"id": "acct-04", "owner": "dee", "active": True},
    {"id": "acct-05", "owner": "eli", "active": True},
    {"id": "acct-06", "owner": "fen", "active": "yes"},   # active not a bool
    {"id": "acct-07", "owner": "gus", "active": False, "note": "extra"},
    {"id": "acct-08", "owner": "hana", "active": True, "note": "extra"},
]

ITEMS = [
    {"id": "item-101", "owner": "amara", "status": "open", "points": 3},
    {"id": "item-102", "owner": "amara", "status": "done", "points": 2},
    {"id": "item-103", "owner": "bo", "status": "open", "points": 5},
    {"id": "item-104", "owner": "chen", "status": "open", "points": 8},
    {"id": "item-105", "owner": "eli", "status": "done", "points": 1},
    {"id": "item-106", "owner": "zara", "status": "open", "points": 4},
    {"id": "item-107", "owner": "fen", "status": "open", "points": 2},
    {"id": "item-108", "owner": "bo", "status": "done", "points": 1},
    {"id": "item-103", "owner": "bo", "status": "open", "points": 5},  # twice
    {"id": "item-109", "owner": "amara", "status": "open"},            # no points
    {"id": "item-110", "owner": "amara", "status": "open", "points": 7},
    {"id": "item-111", "owner": "eli", "status": "open", "points": 2},
    {"id": "item-112", "owner": "bo", "status": "open", "points": "3"},   # string
    {"id": "item-113", "owner": "bo", "status": "done", "points": -1},    # range
    {"id": "item-114", "owner": "bo", "status": "sideways", "points": 4},  # status
    {"id": "item-115", "owner": 42, "status": "open", "points": 4},        # owner
    {"id": "item-116", "owner": "hana", "status": "done", "points": 0},
    {"id": "item-117", "owner": "zara", "status": "open", "points": 1},    # orphan
    {"id": "item-117", "owner": "zara", "status": "open", "points": 1},    # twice
    {"id": "item-118", "owner": "amara", "status": "done", "points": 0,
     "extra": "ignored"},
]

MALFORMED_ITEM_IDS = {"item-109", "item-112", "item-113", "item-114", "item-115"}
ORPHAN_ITEM_IDS = {"item-104", "item-106", "item-107", "item-117"}
INACTIVE_OR_MALFORMED_OWNERS = {"chen", "gus", "fen", "zara"}

EXPECTED_SUMMARIES = {
    "amara": {"owner": "amara", "open_items": 2, "done_items": 2,
              "total_points": 12},
    "bo": {"owner": "bo", "open_items": 1, "done_items": 1, "total_points": 6},
    "dee": {"owner": "dee", "open_items": 0, "done_items": 0, "total_points": 0},
    "eli": {"owner": "eli", "open_items": 1, "done_items": 1, "total_points": 3},
    "hana": {"owner": "hana", "open_items": 0, "done_items": 1, "total_points": 0},
}


def read_jsonl(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def canonical(rows):
    return sorted(json.dumps(row, sort_keys=True) for row in rows)


# ---------------------------------------------------------------------------
# Harness.
# ---------------------------------------------------------------------------

class SyncTestCase(unittest.TestCase):
    """Starts a fresh pair of fixture services per test, in a temp dir."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.out = os.path.join(self.tmp, "out")

    def start_services(self, accounts=ACCOUNTS, items=ITEMS, existing=None):
        self.directory = DirectoryServer(items)
        self.ledger = LedgerServer(accounts, existing=existing)
        for server in (self.directory, self.ledger):
            thread = threading.Thread(target=server.serve_forever,
                                      kwargs={"poll_interval": 0.02},
                                      daemon=True)
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            thread.start()
        self.config = os.path.join(self.tmp, "config.json")
        with open(self.config, "w", encoding="utf-8") as handle:
            json.dump({"directory_url": self.directory.url,
                       "ledger_url": self.ledger.url}, handle)

    def run_sync(self):
        from orgsync.__main__ import main
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["sync", "--config", self.config, "--out-dir", self.out])
        return code

    def path(self, name):
        return os.path.join(self.out, name)

    def posts(self, server=None):
        server = server or self.ledger
        return [entry for entry in server.log if entry["method"] == "POST"]


# ---------------------------------------------------------------------------
# The behaviors.
# ---------------------------------------------------------------------------

class FullSyncTest(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.start_services()

    def test_exits_zero_and_creates_out_dir(self):
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(self.run_sync(), 0)
        self.assertTrue(os.path.isdir(self.out))

    def test_ledger_ends_up_with_every_required_summary(self):
        self.run_sync()
        self.assertEqual(self.ledger.by_owner, EXPECTED_SUMMARIES)

    def test_active_account_without_items_gets_zeroes(self):
        self.run_sync()
        self.assertEqual(self.ledger.by_owner["dee"],
                         EXPECTED_SUMMARIES["dee"])

    def test_duplicate_item_counted_once(self):
        self.run_sync()
        # item-103 was delivered twice; bo still counts it exactly once.
        self.assertEqual(self.ledger.by_owner["bo"],
                         EXPECTED_SUMMARIES["bo"])

    def test_only_missing_summaries_posted_once_each(self):
        self.run_sync()
        posts = self.posts()
        self.assertEqual([entry["idempotency_key"] for entry in posts],
                         ["sync-" + owner for owner in sorted(EXPECTED_SUMMARIES)])
        self.assertTrue(all(entry["status"] == 201 for entry in posts))

    def test_summary_file_records_exactly_the_posted_bodies(self):
        self.run_sync()
        recorded = read_jsonl(self.path("summary.jsonl"))
        self.assertEqual(canonical(recorded),
                         canonical(list(EXPECTED_SUMMARIES.values())))
        # and each body is a single JSON line equal to what the ledger saw
        self.assertEqual(canonical(list(self.ledger.by_owner.values())),
                         canonical(recorded))

    def test_orphans_file_lists_each_orphan_once_as_received(self):
        self.run_sync()
        orphans = read_jsonl(self.path("orphans.jsonl"))
        self.assertEqual({row["id"] for row in orphans}, ORPHAN_ITEM_IDS)
        for row in orphans:  # written as received, extras and all
            self.assertIn(row, ITEMS)
        # the duplicated orphan (item-117) is written exactly once
        self.assertEqual(sorted(row["id"] for row in orphans),
                         sorted(ORPHAN_ITEM_IDS))

    def test_orphan_rules_cover_inactive_unknown_and_malformed_owners(self):
        self.run_sync()
        orphans = {row["id"]: row["owner"]
                   for row in read_jsonl(self.path("orphans.jsonl"))}
        self.assertEqual(orphans["item-104"], "chen")  # inactive account
        self.assertEqual(orphans["item-106"], "zara")  # no account at all
        self.assertEqual(orphans["item-107"], "fen")   # malformed account

    def test_errors_file_lists_each_malformed_record_once(self):
        self.run_sync()
        errors = read_jsonl(self.path("errors.jsonl"))
        self.assertEqual(len(errors), len(errors and set(
            json.dumps(row, sort_keys=True) for row in errors)))
        self.assertEqual({row["record"]["id"] for row in errors
                          if row["service"] == "directory"},
                         MALFORMED_ITEM_IDS)
        ledger_errors = [row for row in errors if row["service"] == "ledger"]
        self.assertEqual([row["record"]["id"] for row in ledger_errors],
                         ["acct-06"])
        self.assertIn({"service": "ledger", "record": ACCOUNTS[5]}, errors)

    def test_no_rejected_writes(self):
        self.run_sync()
        bad = [entry for entry in self.directory.log + self.ledger.log
               if entry["status"] not in (200, 201, 429)]
        self.assertEqual(bad, [])

    def test_malformed_records_never_enter_any_count(self):
        self.run_sync()
        # every summary matches the hand-computed expectations, which
        # already exclude item-109/112/113/114/115 and acct-06
        self.assertEqual(self.ledger.by_owner, EXPECTED_SUMMARIES)


class IdempotencyTest(SyncTestCase):
    def setUp(self):
        super().setUp()
        self.start_services()

    def test_second_run_posts_nothing_and_leaves_files_identical(self):
        self.assertEqual(self.run_sync(), 0)
        snapshots = {name: read_bytes(self.path(name))
                     for name in ("orphans.jsonl", "errors.jsonl",
                                  "summary.jsonl")}
        posts_after_first = len(self.posts())

        self.assertEqual(self.run_sync(), 0)

        self.assertEqual(len(self.posts()), posts_after_first)  # no writes
        for name, snapshot in snapshots.items():                 # same files
            self.assertEqual(read_bytes(self.path(name)), snapshot)
        # the second run still read everything
        self.assertGreaterEqual(len([e for e in self.ledger.log
                                     if e["method"] == "GET"]), 2)


class ExistingSummariesTest(SyncTestCase):
    def test_only_owners_the_ledger_lacks_are_posted(self):
        self.start_services(existing={"sync-bo": EXPECTED_SUMMARIES["bo"]})
        self.assertEqual(self.run_sync(), 0)

        posted_owners = {self.ledger.by_key[key]["owner"]
                         for key in self.ledger.by_key if key.startswith("sync-")}
        # bo was never posted by this run (only the pre-seeded key exists)
        posts = self.posts()
        self.assertNotIn("bo", [entry["idempotency_key"][5:]
                                for entry in posts])
        self.assertEqual(posted_owners | {"bo"}, set(EXPECTED_SUMMARIES))
        self.assertEqual(self.ledger.by_owner, EXPECTED_SUMMARIES)
        recorded = read_jsonl(self.path("summary.jsonl"))
        self.assertNotIn("bo", [row["owner"] for row in recorded])
        self.assertEqual(len(recorded), len(EXPECTED_SUMMARIES) - 1)


class RateLimitTest(SyncTestCase):
    def test_429_on_reads_and_writes_is_waited_out_and_retried(self):
        self.start_services()
        self.directory.rate_limits[("GET", "/v1/items")] = [0.3]
        self.ledger.rate_limits[("GET", "/v1/accounts")] = [0.25]
        self.ledger.rate_limits[("POST", "/v1/summaries")] = [0.2]

        self.assertEqual(self.run_sync(), 0)
        self.assertEqual(self.ledger.by_owner, EXPECTED_SUMMARIES)

        def assert_waited_then_retried(server, method, path, minimum):
            entries = [entry for entry in server.log
                       if entry["method"] == method]
            limited = [i for i, entry in enumerate(entries)
                       if entry["status"] == 429 and entry["path"] == path]
            self.assertTrue(limited, "expected a 429 on %s %s" % (method, path))
            i = limited[0]
            self.assertLess(i + 1, len(entries))  # the work was not abandoned
            retry = entries[i + 1]
            self.assertEqual((retry["method"], retry["path"]), (method, path))
            self.assertGreaterEqual(retry["t"] - entries[i]["t"], minimum)

        assert_waited_then_retried(self.directory, "GET", "/v1/items", 0.29)
        assert_waited_then_retried(self.ledger, "GET", "/v1/accounts", 0.24)
        assert_waited_then_retried(self.ledger, "POST", "/v1/summaries", 0.19)

    def test_repeated_429s_still_get_there(self):
        self.start_services()
        self.ledger.rate_limits[("GET", "/v1/accounts")] = [0.1, 0.1, 0.1]
        self.assertEqual(self.run_sync(), 0)
        limited = [entry for entry in self.ledger.log
                   if entry["status"] == 429]
        self.assertEqual(len(limited), 3)
        self.assertEqual(self.ledger.by_owner, EXPECTED_SUMMARIES)


class SubprocessTest(SyncTestCase):
    def test_real_cli_exits_zero_and_writes_the_three_files(self):
        self.start_services()
        result = subprocess.run(
            [sys.executable, "-m", "orgsync", "sync",
             "--config", self.config, "--out-dir", self.out],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(canonical(read_jsonl(self.path("summary.jsonl"))),
                         canonical(list(EXPECTED_SUMMARIES.values())))
        self.assertEqual({row["id"] for row in
                          read_jsonl(self.path("orphans.jsonl"))},
                         ORPHAN_ITEM_IDS)
        self.assertEqual({row["record"]["id"] for row in
                          read_jsonl(self.path("errors.jsonl"))} -
                         {"acct-06"}, MALFORMED_ITEM_IDS)


class RecordParsingTest(unittest.TestCase):
    def test_item_accepts_the_well_formed_and_ignores_extras(self):
        record = {"id": "i", "owner": "kim", "status": "done", "points": 0,
                  "extra": [1, 2]}
        self.assertEqual(parse_item(record),
                         parse_item({"id": "i", "owner": "kim",
                                     "status": "done", "points": 0}))

    def test_item_rejects_each_way_of_being_malformed(self):
        base = {"id": "i", "owner": "kim", "status": "open", "points": 1}
        for mutate in (
            lambda r: r.pop("points"),
            lambda r: r.update(points="1"),
            lambda r: r.update(points=True),
            lambda r: r.update(points=1.5),
            lambda r: r.update(points=-1),
            lambda r: r.update(status=" Open"),
            lambda r: r.update(status=None),
            lambda r: r.update(owner=7),
            lambda r: r.pop("id"),
        ):
            record = dict(base)
            mutate(record)
            self.assertIsNone(parse_item(record), repr(record))
        self.assertIsNone(parse_item(["not", "a", "dict"]))

    def test_account_requires_a_real_boolean(self):
        self.assertIsNotNone(parse_account(
            {"id": "a", "owner": "kim", "active": True}))
        self.assertIsNone(parse_account(
            {"id": "a", "owner": "kim", "active": "yes"}))
        self.assertIsNone(parse_account(
            {"id": "a", "owner": "kim", "active": 1}))
        self.assertIsNone(parse_account({"owner": "kim", "active": True}))

    def test_summary_owner_needs_a_string_owner(self):
        self.assertEqual(summary_owner({"owner": "kim", "open_items": 0}), "kim")
        self.assertIsNone(summary_owner({"open_items": 0}))
        self.assertIsNone(summary_owner("nope"))


if __name__ == "__main__":
    unittest.main()
