"""Tests for `orgsync sync`.

The end-to-end tests run against in-process fake Directory and Ledger services
(standard-library http.server on 127.0.0.1) so they never alter the real
services named in config.json. The fakes paginate, rate-limit with 429 +
Retry-After, enforce the Ledger's idempotency and validation rules, and log
every request so the tests can check what the tool sent.
"""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from orgsync.config import Config
from orgsync.http_client import Response
from orgsync.models import Summary
from orgsync.sync import ServiceClient, parse_account, parse_item, reconcile, run_sync


ACCOUNTS = [
    {"id": "acct-01", "owner": "amara", "active": True},
    {"id": "acct-02", "owner": "bo", "active": True},
    {"id": "acct-03", "owner": "chen", "active": False},
    {"id": "acct-04", "owner": "dee", "active": True},
    {"id": "acct-05", "owner": "eli", "active": True, "extra": "ignored"},
    {"id": "acct-06", "owner": "fen", "active": "yes"},          # malformed
    {"id": "acct-07", "owner": "gus", "active": False},
    {"id": "acct-08", "active": True},                           # malformed
]
ITEMS = [
    {"id": "item-101", "owner": "amara", "status": "open", "points": 3},
    {"id": "item-102", "owner": "amara", "status": "done", "points": 2},
    {"id": "item-103", "owner": "bo", "status": "open", "points": 5},
    {"id": "item-104", "owner": "chen", "status": "open", "points": 8},   # orphan (inactive)
    {"id": "item-105", "owner": "eli", "status": "done", "points": 1},
    {"id": "item-106", "owner": "zara", "status": "open", "points": 4},   # orphan (unknown)
    {"id": "item-107", "owner": "fen", "status": "open", "points": 2},    # orphan (malformed acct)
    {"id": "item-108", "owner": "bo", "status": "done", "points": 1, "note": "x"},
    {"id": "item-103", "owner": "bo", "status": "open", "points": 5},     # duplicate
    {"id": "item-109", "owner": "amara", "status": "open"},               # malformed
    {"id": "item-110", "owner": "amara", "status": "open", "points": 7},
    {"id": "item-111", "owner": "eli", "status": "open", "points": "2"},  # malformed
    {"id": "item-112", "owner": "eli", "status": "open", "points": True}, # malformed
    {"id": "item-113", "owner": "eli", "status": "closed", "points": 1},  # malformed
    {"id": "item-114", "owner": "eli", "status": "open", "points": -1},   # malformed
    {"id": "item-109", "owner": "amara", "status": "open"},               # malformed, repeated
    {"id": "item-106", "owner": "zara", "status": "open", "points": 4},   # orphan, repeated
]
EXPECTED_SUMMARIES = [
    {"owner": "amara", "open_items": 2, "done_items": 1, "total_points": 12},
    {"owner": "bo", "open_items": 1, "done_items": 1, "total_points": 6},
    {"owner": "dee", "open_items": 0, "done_items": 0, "total_points": 0},
    {"owner": "eli", "open_items": 0, "done_items": 1, "total_points": 1},
]


def canon(value):
    return json.dumps(value, sort_keys=True)


def as_set(entries):
    return sorted(canon(e) for e in entries)


class FakeService:
    """One fake service: pages of records, 429s, and (for the ledger) POSTs."""

    def __init__(self, collections, page_size=3, rate_limit_every=4,
                 retry_after=0.1, active_owners=()):
        self.collections = collections          # path -> (key, list of records)
        self.page_size = page_size
        self.rate_limit_every = rate_limit_every
        self.retry_after = retry_after
        self.active_owners = set(active_owners)
        self.keys = {}                          # idempotency key -> body
        self.log = []
        self.lock = threading.Lock()
        self.blocked_until = 0.0
        self.premature = 0                      # requests sent during a 429 wait
        self.count = 0
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                service.handle(self, "GET")

            def do_POST(self):
                service.handle(self, "POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def handle(self, handler, method):
        path, _, query = handler.path.partition("?")
        body = None
        if method == "POST":
            length = int(handler.headers.get("Content-Length") or 0)
            body = json.loads(handler.rfile.read(length) or b"null")
        with self.lock:
            now = time.monotonic()
            if now < self.blocked_until:
                self.premature += 1
            self.count += 1
            if self.rate_limit_every and self.count % self.rate_limit_every == 0:
                self.blocked_until = now + self.retry_after
                self.log.append((method, path, query, body, 429))
                return self.reply(handler, 429, None, {"Retry-After": str(self.retry_after)})
            status, payload = self.route(method, path, query, body,
                                         handler.headers.get("Idempotency-Key"))
            self.log.append((method, path, query, body, status))
        self.reply(handler, status, payload)

    def route(self, method, path, query, body, key):
        if method == "GET" and path in self.collections:
            name, records = self.collections[path]
            page = int(dict(p.split("=") for p in query.split("&") if p).get("page", 1))
            start = (page - 1) * self.page_size
            more = start + self.page_size < len(records)
            return 200, {name: records[start:start + self.page_size],
                         "next_page": page + 1 if more else None}
        if method == "POST" and path == "/v1/summaries":
            if key in self.keys:
                return (200, body) if self.keys[key] == body else (409, {"error": "conflict"})
            if not isinstance(body, dict) or body.get("owner") not in self.active_owners:
                return 422, {"error": "invalid"}
            self.keys[key] = body
            self.collections["/v1/summaries"][1].append(body)
            return 201, body
        return 404, {"error": "not found"}

    @staticmethod
    def reply(handler, status, payload, headers=None):
        data = b"" if payload is None else json.dumps(payload).encode()
        handler.send_response(status)
        for name, value in (headers or {}).items():
            handler.send_header(name, value)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def writes(self):
        return [entry for entry in self.log if entry[0] != "GET"]


class ValidationTest(unittest.TestCase):
    def test_well_formed_item(self):
        self.assertIsNotNone(parse_item({"id": "i", "owner": "o", "status": "done",
                                         "points": 0, "extra": [1]}))

    def test_malformed_items(self):
        for record in (None, [], "x", {"owner": "o", "status": "open", "points": 1},
                       {"id": 1, "owner": "o", "status": "open", "points": 1},
                       {"id": "i", "owner": None, "status": "open", "points": 1},
                       {"id": "i", "owner": "o", "status": "OPEN", "points": 1},
                       {"id": "i", "owner": "o", "status": "open", "points": 1.0},
                       {"id": "i", "owner": "o", "status": "open", "points": "1"},
                       {"id": "i", "owner": "o", "status": "open", "points": False},
                       {"id": "i", "owner": "o", "status": "open", "points": -3}):
            with self.subTest(record=record):
                self.assertIsNone(parse_item(record))

    def test_accounts(self):
        self.assertTrue(parse_account({"id": "a", "owner": "o", "active": True}).active)
        self.assertFalse(parse_account({"id": "a", "owner": "o", "active": False}).active)
        for record in ({"id": "a", "owner": "o", "active": 1},
                       {"id": "a", "owner": "o", "active": "true"},
                       {"id": "a", "active": True}, {"owner": "o", "active": True}, 7):
            with self.subTest(record=record):
                self.assertIsNone(parse_account(record))


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self.result = reconcile(ACCOUNTS, ITEMS)

    def test_worked_example(self):
        result = reconcile([{"id": "a", "owner": "kim", "active": True}],
                           [{"id": "1", "owner": "kim", "status": "open", "points": 3},
                            {"id": "2", "owner": "kim", "status": "open", "points": 4},
                            {"id": "3", "owner": "kim", "status": "done", "points": 2}])
        self.assertEqual(result.summaries, {"kim": Summary("kim", 2, 1, 9)})

    def test_one_summary_per_active_account_including_empty(self):
        self.assertEqual(as_set(s.to_dict() for s in self.result.summaries.values()),
                         as_set(EXPECTED_SUMMARIES))

    def test_orphans_once_each(self):
        self.assertEqual(sorted(o["id"] for o in self.result.orphans),
                         ["item-104", "item-106", "item-107"])

    def test_errors_once_each_with_service(self):
        self.assertEqual(len(self.result.errors), 7)
        self.assertEqual(len({canon(e) for e in self.result.errors}), 7)
        services = sorted(e["service"] for e in self.result.errors)
        self.assertEqual(services, ["directory"] * 5 + ["ledger"] * 2)


class RateLimitTest(unittest.TestCase):
    def test_waits_retry_after_then_retries_same_request(self):
        class Headers(dict):
            def get(self, key, default=None):
                return dict.get(self, key, default)

        responses = [Response(429, Headers({"Retry-After": "2"}), b"", "u"),
                     Response(200, Headers(), b'{"ok": 1}', "u")]
        calls, slept, clock = [], [], [100.0]

        class FakeHttp:
            def request(self, method, path, **kwargs):
                calls.append((clock[0], method, path, kwargs))
                return responses.pop(0)

        def sleep(seconds):
            slept.append(seconds)
            clock[0] += seconds

        client = ServiceClient("x", FakeHttp(), sleep=sleep, clock=lambda: clock[0])
        response = client.request("POST", "/p", payload={"a": 1})
        self.assertEqual(response.status, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1:], calls[1][1:])
        self.assertGreaterEqual(calls[1][0] - calls[0][0], 2.0)


class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.ledger = FakeService(
            {"/v1/accounts": ("accounts", list(ACCOUNTS)),
             "/v1/summaries": ("summaries", [])},
            active_owners=[a["owner"] for a in ACCOUNTS if parse_account(a)
                           and parse_account(a).active])
        self.directory = FakeService({"/v1/items": ("items", list(ITEMS))})
        self.addCleanup(self.ledger.close)
        self.addCleanup(self.directory.close)
        self.config = Config(self.directory.url, self.ledger.url)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out = os.path.join(self.tmp, "out")

    def read(self, name):
        with open(os.path.join(self.out, name), encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def snapshot(self):
        result = {}
        for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl"):
            with open(os.path.join(self.out, name), encoding="utf-8") as handle:
                result[name] = sorted(handle.read().splitlines())
        return result

    def test_first_run_posts_missing_summaries_and_writes_outputs(self):
        self.assertEqual(run_sync(self.config, self.out), 0)
        writes = self.ledger.writes()
        accepted = [w for w in writes if w[4] in (200, 201)]
        self.assertEqual([w for w in writes if w[4] in (400, 409, 422)], [])
        self.assertEqual(as_set(w[3] for w in accepted), as_set(EXPECTED_SUMMARIES))
        self.assertEqual(as_set(self.read("summary.jsonl")), as_set(EXPECTED_SUMMARIES))
        self.assertEqual(sorted(o["id"] for o in self.read("orphans.jsonl")),
                         ["item-104", "item-106", "item-107"])
        self.assertEqual(len(self.read("errors.jsonl")), 7)
        self.assertEqual(self.directory.writes(), [])

    def test_all_pages_read_and_rate_limits_honoured(self):
        self.assertEqual(run_sync(self.config, self.out), 0)
        self.assertTrue(any(e[4] == 429 for e in self.ledger.log + self.directory.log))
        self.assertEqual(self.ledger.premature, 0)
        self.assertEqual(self.directory.premature, 0)
        item_pages = {e[2] for e in self.directory.log if e[4] == 200}
        self.assertEqual(len(item_pages), -(-len(ITEMS) // 3))

    def test_idempotency_key_is_sync_owner(self):
        run_sync(self.config, self.out)
        self.assertEqual(sorted(self.ledger.keys),
                         ["sync-amara", "sync-bo", "sync-dee", "sync-eli"])

    def test_second_run_writes_nothing_and_keeps_outputs(self):
        self.assertEqual(run_sync(self.config, self.out), 0)
        first, writes = self.snapshot(), len(self.ledger.writes())
        self.assertEqual(run_sync(self.config, self.out), 0)
        self.assertEqual(len(self.ledger.writes()), writes)
        self.assertEqual(self.snapshot(), first)

    def test_only_missing_summaries_are_posted(self):
        existing = {"owner": "bo", "open_items": 9, "done_items": 9, "total_points": 9}
        self.ledger.collections["/v1/summaries"][1].append(existing)
        self.assertEqual(run_sync(self.config, self.out), 0)
        posted_owners = sorted(w[3]["owner"] for w in self.ledger.writes() if w[4] != 429)
        self.assertEqual(posted_owners, ["amara", "dee", "eli"])
        self.assertEqual(sorted(s["owner"] for s in self.read("summary.jsonl")),
                         ["amara", "dee", "eli"])


if __name__ == "__main__":
    unittest.main()
