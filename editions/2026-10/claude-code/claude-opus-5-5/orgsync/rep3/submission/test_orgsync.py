"""Tests for `orgsync sync`.

The end-to-end tests run against in-process fake Directory and Ledger services
(standard library `http.server`) that follow the published contract: paging,
429 + Retry-After, and the Ledger's 201/200/409/422 answers to a POST. They log
every request, so the tests can check what the tool sent. Nothing here touches
the services named in config.json, so running the suite never changes them.
"""

import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from orgsync.config import Config
from orgsync.models import Summary
from orgsync.sync import parse_account, parse_item, parse_retry_after, reconcile, run_sync


# --------------------------------------------------------------------------
# Fake services


class FakeService:
    """One fake service. `pages` maps path -> list of record pages for GETs.

    `throttle` is a set of (path, page) whose first GET answers 429; a POST
    is throttled once per idempotency key when `throttle_posts` is set.
    """

    def __init__(self, pages, retry_after="0.3", throttle=(), throttle_posts=False,
                 active_owners=()):
        self.pages = pages
        self.retry_after = retry_after
        self.throttle = set(throttle)
        self.throttle_posts = throttle_posts
        self.active_owners = set(active_owners)
        self.posted = {}      # idempotency key -> body
        self.log = []         # dicts: ts, method, path, page, key, body, status
        self.lock = threading.Lock()
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body=None, headers=()):
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
                for key, value in headers:
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                url = urlparse(self.path)
                page = int(parse_qs(url.query).get("page", ["1"])[0])
                with service.lock:
                    status, body = service.handle_get(url.path, page)
                    service.record("GET", url.path, page, None, None, status)
                headers = [("Retry-After", service.retry_after)] if status == 429 else []
                self._send(status, body, headers)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"null")
                key = self.headers.get("Idempotency-Key")
                with service.lock:
                    status = service.handle_post(key, body)
                    service.record("POST", urlparse(self.path).path, None, key, body, status)
                headers = [("Retry-After", service.retry_after)] if status == 429 else []
                self._send(status, {"status": status}, headers)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def record(self, method, path, page, key, body, status):
        self.log.append({"ts": time.monotonic(), "method": method, "path": path,
                         "page": page, "key": key, "body": body, "status": status})

    def handle_get(self, path, page):
        if (path, page) in self.throttle:
            self.throttle.discard((path, page))
            return 429, None
        if path == "/v1/summaries":
            pages = [list(self.posted.values())] + self.pages.get(path, [])[1:]
            pages[0] = self.pages.get(path, [[]])[0] + pages[0]
        elif path in self.pages:
            pages = self.pages[path]
        else:
            return 404, {"error": "not found"}
        key = path.rsplit("/", 1)[1]
        records = pages[page - 1] if 1 <= page <= len(pages) else []
        next_page = page + 1 if page < len(pages) else None
        return 200, {key: records, "next_page": next_page}

    def handle_post(self, key, body):
        if self.throttle_posts and ("post", key) not in self.throttle:
            self.throttle.add(("post", key))
            return 429
        if not key or not isinstance(body, dict):
            return 400
        if body.get("owner") not in self.active_owners:
            return 422
        if key in self.posted:
            return 200 if self.posted[key] == body else 409
        self.posted[key] = body
        return 201


ACCOUNT_PAGES = [
    [{"id": "a1", "owner": "kim", "active": True},
     {"id": "a2", "owner": "lee", "active": True, "extra": "ignored"},
     {"id": "a3", "owner": "max", "active": False}],
    [{"id": "a4", "owner": "ned", "active": "yes"},           # malformed
     {"id": "a5", "owner": "ora", "active": True},            # no items
     {"id": "a6", "owner": "pat", "active": True},            # already summarised
     {"id": "a4", "owner": "ned", "active": "yes"}],          # same bad record again
]
ITEM_PAGES = [
    [{"id": "i1", "owner": "kim", "status": "open", "points": 3},
     {"id": "i2", "owner": "kim", "status": "open", "points": 4},
     {"id": "i3", "owner": "kim", "status": "done", "points": 2},
     {"id": "i4", "owner": "max", "status": "open", "points": 5}],   # inactive -> orphan
    [{"id": "i1", "owner": "kim", "status": "open", "points": 3},    # duplicate delivery
     {"id": "i5", "owner": "lee", "status": "done", "points": 1, "tag": "x"},
     {"id": "i6", "owner": "ned", "status": "open", "points": 2},    # malformed acct -> orphan
     {"id": "i7", "owner": "zed", "status": "open", "points": 1},    # no account -> orphan
     {"id": "i8", "owner": "kim", "status": "open", "points": "3"},  # malformed
     {"id": "i9", "owner": "kim", "status": "open", "points": True}],  # malformed
    [{"id": "i4", "owner": "max", "status": "open", "points": 5},    # orphan again
     {"id": "i8", "owner": "kim", "status": "open", "points": "3"},  # malformed again
     {"id": "i10", "owner": "lee", "status": "stuck", "points": 1},  # malformed
     {"id": "i11", "owner": "pat", "status": "open", "points": 9}],
]
PRESET_SUMMARIES = [[{"owner": "pat", "open_items": 1, "done_items": 0, "total_points": 9}]]

EXPECTED_POSTED = [
    {"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9},
    {"owner": "lee", "open_items": 0, "done_items": 1, "total_points": 1},
    {"owner": "ora", "open_items": 0, "done_items": 0, "total_points": 0},
]


def read_jsonl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def sort_key(record):
    return json.dumps(record, sort_keys=True)


# --------------------------------------------------------------------------
# Unit tests: validation and arithmetic


class ValidationTest(unittest.TestCase):
    def test_well_formed_item_with_extra_fields(self):
        self.assertIsNotNone(parse_item({"id": "i", "owner": "o", "status": "done",
                                         "points": 0, "more": [1]}))

    def test_malformed_items(self):
        base = {"id": "i", "owner": "o", "status": "open", "points": 1}
        cases = [
            {k: v for k, v in base.items() if k != "points"},
            dict(base, points="1"), dict(base, points=1.0), dict(base, points=True),
            dict(base, points=-1), dict(base, status="closed"), dict(base, status=None),
            dict(base, id=7), dict(base, owner=None), "not a dict", None, [base],
        ]
        for record in cases:
            with self.subTest(record=record):
                self.assertIsNone(parse_item(record))

    def test_malformed_accounts(self):
        base = {"id": "a", "owner": "o", "active": False}
        self.assertIsNotNone(parse_account(base))
        for record in (dict(base, active="true"), dict(base, active=1),
                       {"id": "a", "owner": "o"}, dict(base, owner=3), 42):
            with self.subTest(record=record):
                self.assertIsNone(parse_account(record))

    def test_retry_after_parsing(self):
        self.assertEqual(parse_retry_after("2"), 2.0)
        self.assertEqual(parse_retry_after(" 0.5 "), 0.5)
        self.assertEqual(parse_retry_after(None), 1.0)
        self.assertEqual(parse_retry_after("soon"), 1.0)


class ReconcileTest(unittest.TestCase):
    def test_worked_example(self):
        plan = reconcile([{"id": "a", "owner": "kim", "active": True}],
                         [{"id": "1", "owner": "kim", "status": "open", "points": 3},
                          {"id": "2", "owner": "kim", "status": "open", "points": 4},
                          {"id": "3", "owner": "kim", "status": "done", "points": 2}])
        self.assertEqual(plan.summaries["kim"].to_dict(),
                         {"owner": "kim", "open_items": 2, "done_items": 1,
                          "total_points": 9})

    def test_active_account_without_items_gets_zero_summary(self):
        plan = reconcile([{"id": "a", "owner": "kim", "active": True}], [])
        self.assertEqual(plan.summaries, {"kim": Summary("kim")})

    def test_inactive_and_malformed_accounts_get_no_summary(self):
        plan = reconcile([{"id": "a", "owner": "kim", "active": False},
                          {"id": "b", "owner": "lee", "active": "yes"}],
                         [{"id": "1", "owner": "lee", "status": "open", "points": 1}])
        self.assertEqual(plan.summaries, {})
        self.assertEqual([o["id"] for o in plan.orphans], ["1"])
        self.assertEqual(plan.errors, [{"service": "ledger",
                                        "record": {"id": "b", "owner": "lee",
                                                   "active": "yes"}}])

    def test_duplicates_count_once(self):
        item = {"id": "1", "owner": "kim", "status": "open", "points": 5}
        orphan = {"id": "2", "owner": "zed", "status": "open", "points": 1}
        bad = {"id": "3", "owner": "kim", "status": "open"}
        plan = reconcile([{"id": "a", "owner": "kim", "active": True}],
                         [item, orphan, bad, item, orphan, bad])
        self.assertEqual(plan.summaries["kim"], Summary("kim", 1, 0, 5))
        self.assertEqual(plan.orphans, [orphan])
        self.assertEqual(plan.errors, [{"service": "directory", "record": bad}])


# --------------------------------------------------------------------------
# End-to-end against fake services


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.directory = FakeService(
            {"/v1/items": ITEM_PAGES},
            throttle={("/v1/items", 1), ("/v1/items", 3)})
        self.ledger = FakeService(
            {"/v1/accounts": ACCOUNT_PAGES, "/v1/summaries": PRESET_SUMMARIES},
            throttle={("/v1/accounts", 2), ("/v1/summaries", 1)},
            throttle_posts=True, active_owners={"kim", "lee", "ora", "pat"})
        self.addCleanup(self.directory.close)
        self.addCleanup(self.ledger.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "out")
        self.config = Config(self.directory.url, self.ledger.url)

    def run_sync(self):
        self.assertEqual(run_sync(self.config, self.out), 0)
        return {name: sorted(read_jsonl(os.path.join(self.out, name)), key=sort_key)
                for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")}

    def posts(self):
        return [entry for entry in self.ledger.log if entry["method"] == "POST"]

    def test_first_run_outputs(self):
        files = self.run_sync()
        self.assertEqual(files["summary.jsonl"], sorted(EXPECTED_POSTED, key=sort_key))
        self.assertEqual(sorted(o["id"] for o in files["orphans.jsonl"]), ["i4", "i6", "i7"])
        self.assertEqual(files["errors.jsonl"], sorted([
            {"service": "ledger", "record": ACCOUNT_PAGES[1][0]},
            {"service": "directory", "record": ITEM_PAGES[1][4]},
            {"service": "directory", "record": ITEM_PAGES[1][5]},
            {"service": "directory", "record": ITEM_PAGES[2][2]},
        ], key=sort_key))

    def test_reads_every_page(self):
        self.run_sync()
        pages = lambda svc, path: {e["page"] for e in svc.log
                                   if e["path"] == path and e["status"] == 200}
        self.assertEqual(pages(self.directory, "/v1/items"), {1, 2, 3})
        self.assertEqual(pages(self.ledger, "/v1/accounts"), {1, 2})
        self.assertEqual(pages(self.ledger, "/v1/summaries"), {1})

    def test_posts_only_missing_summaries_with_keys_and_no_rejections(self):
        self.run_sync()
        accepted = [p for p in self.posts() if p["status"] in (200, 201)]
        self.assertEqual(sorted((p["key"], sort_key(p["body"])) for p in accepted),
                         sorted(("sync-" + b["owner"], sort_key(b)) for b in EXPECTED_POSTED))
        self.assertFalse([p for p in self.posts() if p["status"] in (400, 409, 422)])
        self.assertNotIn("pat", [p["body"]["owner"] for p in self.posts()])

    def test_rate_limits_are_honored(self):
        self.run_sync()
        for service in (self.directory, self.ledger):
            log = service.log
            throttled = [i for i, e in enumerate(log) if e["status"] == 429]
            self.assertTrue(throttled)
            for i in throttled:
                if i + 1 < len(log):
                    gap = log[i + 1]["ts"] - log[i]["ts"]
                    self.assertGreaterEqual(gap, float(service.retry_after))
                # The throttled request was retried, not abandoned.
                retried = [e for e in log[i + 1:] if (e["path"], e["page"], e["key"])
                           == (log[i]["path"], log[i]["page"], log[i]["key"])]
                self.assertTrue(retried)

    def test_second_run_writes_nothing_and_keeps_outputs(self):
        first = self.run_sync()
        snapshot = {name: read_text(os.path.join(self.out, name)) for name in first}
        posts_before = len(self.posts())
        second = self.run_sync()
        self.assertEqual(first, second)
        self.assertEqual(len(self.posts()), posts_before)
        for name, text in snapshot.items():
            self.assertEqual(read_text(os.path.join(self.out, name)), text)


if __name__ == "__main__":
    unittest.main()
