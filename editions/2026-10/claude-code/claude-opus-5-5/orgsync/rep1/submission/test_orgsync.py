"""Behaviour tests for `orgsync sync`.

Each test starts its own in-process fake Directory and Ledger (stdlib
http.server on ephemeral ports), so the suite never writes to the real
services. The fakes implement the documented API: pagination, 429 with
Retry-After, and the Ledger's idempotency / validation rules on POST.
"""

import contextlib
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from orgsync.__main__ import main
from orgsync.config import Config
from orgsync.sync import parse_account, parse_item, parse_retry_after, reconcile


class FakeService:
    """One fake service: serves paged collections, logs every request."""

    def __init__(self, collections, page_size=2, throttle_every=0, retry_after="0.2"):
        self.collections = collections          # path -> (key, list of records)
        self.page_size = page_size
        self.throttle_every = throttle_every    # every Nth request gets a 429
        self.retry_after = retry_after
        self.log = []                           # (time, method, path, status)
        self.last_429 = None
        self.violations = []                    # requests sent before Retry-After
        self.posts = {}                         # idempotency key -> body
        self.active_owners = set()
        self.lock = threading.Lock()
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body=None, headers=None):
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _gate(self):
                with service.lock:
                    now = time.monotonic()
                    if service.last_429 is not None:
                        until = service.last_429[0] + service.last_429[1]
                        if now < until:
                            service.violations.append((self.command, self.path))
                    n = len(service.log) + 1
                    throttled = service.throttle_every and n % service.throttle_every == 0
                    service.log.append((now, self.command, self.path,
                                        429 if throttled else None))
                    if throttled:
                        service.last_429 = (now, float(service.retry_after))
                if throttled:
                    self._send(429, headers={"Retry-After": service.retry_after})
                return not throttled

            def do_GET(self):
                if not self._gate():
                    return
                url = urllib.parse.urlparse(self.path)
                page = int(urllib.parse.parse_qs(url.query).get("page", ["1"])[0])
                if url.path not in service.collections:
                    return self._send(404, {"error": "not found"})
                key, records = service.collections[url.path]
                start = (page - 1) * service.page_size
                chunk = records[start:start + service.page_size]
                more = start + service.page_size < len(records)
                self._send(200, {key: chunk, "next_page": page + 1 if more else None})

            def do_POST(self):
                if not self._gate():
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                key = self.headers.get("Idempotency-Key")
                with service.lock:
                    if key in service.posts:
                        status = 200 if service.posts[key] == body else 409
                    elif body.get("owner") not in service.active_owners:
                        status = 422
                    else:
                        service.posts[key] = body
                        service.collections["/v1/summaries"][1].append(body)
                        status = 201
                    service.log[-1] = service.log[-1][:3] + (status,)
                self._send(status, body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def writes(self):
        return [entry for entry in self.log if entry[1] != "GET"]


ACCOUNTS = [
    {"id": "a1", "owner": "amara", "active": True},
    {"id": "a2", "owner": "bo", "active": True},
    {"id": "a3", "owner": "chen", "active": False},
    {"id": "a4", "owner": "dee", "active": True},          # no items
    {"id": "a5", "owner": "fen", "active": "yes"},         # malformed
    {"owner": "gus", "active": True},                      # malformed: no id
    {"id": "a7", "owner": "hal", "active": True, "extra": 1},  # already summarised
]

ITEMS = [
    {"id": "i1", "owner": "amara", "status": "open", "points": 3},
    {"id": "i2", "owner": "amara", "status": "done", "points": 2},
    {"id": "i3", "owner": "bo", "status": "open", "points": 5, "tag": "x"},
    {"id": "i4", "owner": "chen", "status": "open", "points": 8},   # orphan (inactive)
    {"id": "i5", "owner": "zara", "status": "open", "points": 4},   # orphan (unknown)
    {"id": "i6", "owner": "fen", "status": "open", "points": 2},    # orphan (malformed acct)
    {"id": "i3", "owner": "bo", "status": "open", "points": 5, "tag": "x"},  # duplicate
    {"id": "i7", "owner": "amara", "status": "open"},                # malformed
    {"id": "i8", "owner": "amara", "status": "open", "points": "7"},  # malformed
    {"id": "i9", "owner": "amara", "status": "closed", "points": 1},  # malformed
    {"id": "i10", "owner": "amara", "status": "open", "points": -1},  # malformed
    {"id": "i11", "owner": "amara", "status": "open", "points": True},  # malformed
    {"id": "i12", "owner": "amara", "status": "open", "points": 7},
    {"id": "i5", "owner": "zara", "status": "open", "points": 4},   # duplicate orphan
    {"id": "i7", "owner": "amara", "status": "open"},                # duplicate malformed
    {"id": "i13", "owner": "gus", "status": "done", "points": 1},   # orphan
]

EXISTING_SUMMARIES = [
    {"owner": "hal", "open_items": 0, "done_items": 0, "total_points": 0},
]


def read_jsonl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def canon(entries):
    return sorted(json.dumps(e, sort_keys=True) for e in entries)


class SyncEndToEndTest(unittest.TestCase):
    throttle_every = 0

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = FakeService({"/v1/items": ("items", list(ITEMS))},
                                     page_size=3, throttle_every=self.throttle_every)
        self.ledger = FakeService({"/v1/accounts": ("accounts", list(ACCOUNTS)),
                                   "/v1/summaries": ("summaries", list(EXISTING_SUMMARIES))},
                                  page_size=2, throttle_every=self.throttle_every)
        self.ledger.active_owners = {"amara", "bo", "dee", "hal"}
        self.addCleanup(self.directory.stop)
        self.addCleanup(self.ledger.stop)
        self.config_path = os.path.join(self.tmp.name, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump({"directory_url": self.directory.url,
                       "ledger_url": self.ledger.url}, handle)
        self.out = os.path.join(self.tmp.name, "out")

    def sync(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            code = main(["sync", "--config", self.config_path, "--out-dir", self.out])
        self.assertEqual(code, 0, err.getvalue())

    def outputs(self):
        return {name: read_jsonl(os.path.join(self.out, name))
                for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")}

    def test_summaries_posted_for_missing_active_owners(self):
        self.sync()
        expected = [
            {"owner": "amara", "open_items": 2, "done_items": 1, "total_points": 12},
            {"owner": "bo", "open_items": 1, "done_items": 0, "total_points": 5},
            {"owner": "dee", "open_items": 0, "done_items": 0, "total_points": 0},
        ]
        self.assertEqual(canon(self.outputs()["summary.jsonl"]), canon(expected))
        self.assertEqual(canon(self.ledger.posts.values()), canon(expected))
        self.assertEqual(sorted(self.ledger.posts), ["sync-amara", "sync-bo", "sync-dee"])

    def test_no_rejected_writes_and_existing_summary_not_reposted(self):
        self.sync()
        statuses = [entry[3] for entry in self.ledger.writes()]
        self.assertTrue(statuses)
        self.assertTrue(all(s in (201, 200, 429) for s in statuses), statuses)
        self.assertNotIn("sync-hal", self.ledger.posts)
        self.assertEqual(self.directory.writes(), [])

    def test_orphans_written_once_each(self):
        self.sync()
        orphans = self.outputs()["orphans.jsonl"]
        self.assertEqual(sorted(o["id"] for o in orphans), ["i13", "i4", "i5", "i6"])

    def test_malformed_records_written_once_with_service(self):
        self.sync()
        errors = self.outputs()["errors.jsonl"]
        expected = [{"service": "directory", "record": r} for r in ITEMS
                    if r["id"] in ("i7", "i8", "i9", "i10", "i11")]
        expected += [{"service": "ledger", "record": ACCOUNTS[4]},
                     {"service": "ledger", "record": ACCOUNTS[5]}]
        # i7 is delivered twice but recorded once.
        unique = {json.dumps(e, sort_keys=True) for e in expected}
        self.assertEqual(canon(errors), sorted(unique))
        self.assertEqual(len(errors), 7)

    def test_second_run_writes_nothing_and_outputs_unchanged(self):
        self.sync()
        first = self.outputs()
        writes_before = len(self.ledger.writes())
        self.sync()
        self.assertEqual(len(self.ledger.writes()), writes_before)
        second = self.outputs()
        for name in first:
            self.assertEqual(canon(first[name]), canon(second[name]), name)

    def test_reads_every_page(self):
        self.sync()
        pages = {e[2] for e in self.ledger.log + self.directory.log if e[1] == "GET"}
        self.assertIn("/v1/items?page=6", pages)
        self.assertIn("/v1/accounts?page=4", pages)


class SyncUnderRateLimitTest(SyncEndToEndTest):
    """Same behaviours with every third request answered 429."""

    throttle_every = 3

    def test_retry_after_is_honoured(self):
        self.sync()
        self.assertTrue(any(e[3] == 429 for e in self.ledger.log))
        self.assertEqual(self.ledger.violations, [])
        self.assertEqual(self.directory.violations, [])


class ValidationTest(unittest.TestCase):
    def test_item_shapes(self):
        self.assertIsNotNone(parse_item({"id": "x", "owner": "o", "status": "done", "points": 0}))
        for bad in ({"id": 1, "owner": "o", "status": "open", "points": 1},
                    {"id": "x", "owner": None, "status": "open", "points": 1},
                    {"id": "x", "owner": "o", "status": "open", "points": 1.0},
                    {"id": "x", "owner": "o", "status": "OPEN", "points": 1},
                    ["x"], None, "item"):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_item(bad))

    def test_account_shapes(self):
        self.assertIsNotNone(parse_account({"id": "a", "owner": "o", "active": False}))
        for bad in ({"id": "a", "owner": "o", "active": 1},
                    {"id": "a", "owner": "o"}, {"id": "a", "active": True}, 42):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_account(bad))

    def test_worked_example(self):
        plan = reconcile([{"id": "a", "owner": "kim", "active": True}],
                         [{"id": "1", "owner": "kim", "status": "open", "points": 3},
                          {"id": "2", "owner": "kim", "status": "open", "points": 4},
                          {"id": "3", "owner": "kim", "status": "done", "points": 2}])
        self.assertEqual(plan.required["kim"].to_dict(),
                         {"owner": "kim", "open_items": 2, "done_items": 1,
                          "total_points": 9})

    def test_inactive_account_gets_no_summary(self):
        plan = reconcile([{"id": "a", "owner": "kim", "active": False}], [])
        self.assertEqual(plan.required, {})

    def test_retry_after_parsing(self):
        self.assertEqual(parse_retry_after("2"), 2.0)
        self.assertEqual(parse_retry_after("0.5"), 0.5)
        self.assertEqual(parse_retry_after(None), 1.0)
        self.assertEqual(parse_retry_after("garbage"), 1.0)
        self.assertEqual(parse_retry_after("-3"), 0.0)


if __name__ == "__main__":
    unittest.main()
