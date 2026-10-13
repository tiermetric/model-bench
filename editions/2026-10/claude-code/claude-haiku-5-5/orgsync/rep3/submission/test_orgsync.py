"""Tests for orgsync's sync behaviour.

Each test that touches the network starts two local stand-ins, one for the
Directory and one for the Ledger, on ephemeral 127.0.0.1 ports. They serve
scripted pages, record every request, and can be told to answer particular
requests with a 429 or a 5xx. So the suite runs offline and does not depend on
whatever is serving config.json.

Run with: python3 -m unittest test_orgsync
"""

import contextlib
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.parse
import urllib.request
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from orgsync.__main__ import main
from orgsync.client import ServiceClient, SyncError, retry_after_seconds
from orgsync.http_client import HttpClient
from orgsync.sync import EXIT_SERVICE_ERROR, idempotency_key, reconcile

ACCOUNTS = "/v1/accounts"
ITEMS = "/v1/items"
SUMMARIES = "/v1/summaries"

Recorded = namedtuple("Recorded", "at method path query idempotency_key body")


def direct_opener():
    """An opener that ignores proxy settings, as the tool does for its own."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def account(owner, active=True):
    return {"id": "acct-" + owner, "owner": owner, "active": active}


def item(item_id, owner, status, points):
    return {"id": item_id, "owner": owner, "status": status, "points": points}


class FakeService:
    """A local HTTP server standing in for one service. It records every request.

    `routes` maps a GET path to (envelope key, pages), where `pages` is a list
    of record lists, one per page. The summaries path is served from
    `summaries`. POST /v1/summaries behaves as the Ledger contract says: 422
    for an owner that is not active, 201 for a new key, 200 for a replay of the
    same key and body, and 409 for a reused key with a different body.

    `inject` is a list of (method, status, headers) answers to send ahead of
    the real one; a method of None matches any request.
    """

    def __init__(self, routes=None, active_owners=(), summaries=()):
        self.routes = dict(routes or {})
        self.active_owners = set(active_owners)
        self.summaries = list(summaries)
        self.keys = {}
        self.inject = []
        self.requests = []
        self.rejections = 0
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(self))
        self._server.daemon_threads = True
        self.url = "http://127.0.0.1:%d" % self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()

    def posts(self):
        return [r for r in self.requests if r.method == "POST"]

    def dispatch(self, method, path, query, idempotency_key, body):
        with self._lock:
            self.requests.append(Recorded(time.monotonic(), method, path,
                                          dict(query), idempotency_key, body))
            for index, (wanted, status, headers) in enumerate(self.inject):
                if wanted in (None, method):
                    del self.inject[index]
                    return status, headers, {"error": "injected"}
            if method == "GET":
                return self._get(path, query)
            if method == "POST" and path == SUMMARIES:
                return self._post_summary(idempotency_key, body)
            return 404, {}, {"error": "no route"}

    def _get(self, path, query):
        if path == SUMMARIES:
            key, pages = "summaries", [list(self.summaries)]
        elif path in self.routes:
            key, pages = self.routes[path]
        else:
            return 404, {}, {"error": "no route"}
        try:
            page = int(query.get("page", "0"))
        except ValueError:
            page = 0
        if not 1 <= page <= len(pages):
            return 404, {}, {"error": "no such page"}
        next_page = page + 1 if page < len(pages) else None
        return 200, {}, {key: pages[page - 1], "next_page": next_page}

    def _post_summary(self, key, body):
        if not isinstance(body, dict) or body.get("owner") not in self.active_owners:
            self.rejections += 1
            return 422, {}, {"error": "owner is not an active account"}
        if key in self.keys:
            if self.keys[key] == body:
                return 200, {}, body
            self.rejections += 1
            return 409, {}, {"error": "key reused with a different body"}
        self.keys[key] = body
        self.summaries.append(body)
        return 201, {}, body


def _handler_for(service):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._answer("GET")

        def do_POST(self):
            self._answer("POST")

        def log_message(self, format, *args):
            pass

        def _answer(self, method):
            parts = urllib.parse.urlsplit(self.path)
            query = dict(urllib.parse.parse_qsl(parts.query))
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw.decode("utf-8")) if raw else None
            status, headers, payload = service.dispatch(
                method, parts.path, query, self.headers.get("Idempotency-Key"), body)
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return Handler


class FakeClock:
    """A clock whose sleep advances it, so rate-limit waits take no real time."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = os.path.join(self.tmp.name, "out")

    def service(self, **kwargs):
        fake = FakeService(**kwargs)
        self.addCleanup(fake.close)
        return fake

    def start(self, account_pages, item_pages, summaries=()):
        """Start a Directory and a Ledger holding the given records."""
        active = {a["owner"] for page in account_pages for a in page
                  if isinstance(a, dict) and a.get("active") is True}
        directory = self.service(routes={ITEMS: ("items", item_pages)})
        ledger = self.service(routes={ACCOUNTS: ("accounts", account_pages)},
                              active_owners=active, summaries=summaries)
        return directory, ledger

    def run_cli(self, directory_url, ledger_url):
        config_path = os.path.join(self.tmp.name, "config.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump({"directory_url": directory_url, "ledger_url": ledger_url}, handle)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["sync", "--config", config_path, "--out-dir", self.out_dir])
        return code, err.getvalue()

    def read_jsonl(self, name):
        with open(os.path.join(self.out_dir, name), "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def snapshot(self):
        contents = {}
        for name in sorted(os.listdir(self.out_dir)):
            with open(os.path.join(self.out_dir, name), "rb") as handle:
                contents[name] = handle.read()
        return contents


class ReconcileTest(unittest.TestCase):
    """The rules, applied to records in memory: no network, no files."""

    def test_worked_example_from_the_spec(self):
        plan = reconcile([account("kim")],
                         [item("i1", "kim", "open", 3), item("i2", "kim", "open", 4),
                          item("i3", "kim", "done", 2)], [])
        self.assertEqual([s.to_dict() for s in plan.summaries],
                         [{"owner": "kim", "open_items": 2, "done_items": 1,
                           "total_points": 9}])
        self.assertEqual(plan.orphans, [])
        self.assertEqual(plan.errors, [])

    def test_active_account_without_items_gets_a_zero_summary(self):
        plan = reconcile([account("kim")], [], [])
        self.assertEqual(plan.summaries[0].to_dict(),
                         {"owner": "kim", "open_items": 0, "done_items": 0,
                          "total_points": 0})

    def test_inactive_owners_get_no_summary_and_their_items_are_orphans(self):
        items = [item("i1", "lee", "open", 5), item("i2", "zed", "done", 1)]
        plan = reconcile([account("lee", active=False)], items, [])
        self.assertEqual(plan.summaries, [])
        self.assertEqual(plan.orphans, items)

    def test_duplicate_delivery_counts_once_and_orphan_is_written_once(self):
        kept = item("i1", "kim", "open", 3)
        orphan = item("i9", "zed", "open", 1)
        plan = reconcile([account("kim"), account("kim")],
                         [kept, dict(kept), orphan, dict(orphan)], [])
        self.assertEqual(len(plan.summaries), 1)
        self.assertEqual((plan.summaries[0].open_items, plan.summaries[0].total_points),
                         (1, 3))
        self.assertEqual(plan.orphans, [orphan])

    def test_extra_fields_are_ignored_and_orphans_are_written_as_received(self):
        orphan = dict(item("i9", "zed", "open", 1), label="from elsewhere")
        counted = dict(item("i1", "kim", "done", 2), note="ignored")
        plan = reconcile([account("kim")], [orphan, counted], [])
        self.assertEqual(plan.orphans, [orphan])
        self.assertEqual(plan.summaries[0].to_dict(),
                         {"owner": "kim", "open_items": 0, "done_items": 1,
                          "total_points": 2})

    def test_malformed_items_are_errors_and_never_counted(self):
        bad = [
            {"id": "b1", "owner": "kim", "status": "open"},
            item("b2", "kim", "open", "3"),
            item("b3", "kim", "open", 3.0),
            item("b4", "kim", "open", True),
            item("b5", "kim", "open", -1),
            item("b6", "kim", "sideways", 1),
            {"id": 5, "owner": "kim", "status": "open", "points": 1},
            {"id": "b7", "owner": None, "status": "open", "points": 1},
            "not an object",
        ]
        plan = reconcile([account("kim")], bad, [])
        self.assertEqual(plan.errors, [{"service": "directory", "record": r} for r in bad])
        self.assertEqual(plan.summaries[0].to_dict(),
                         {"owner": "kim", "open_items": 0, "done_items": 0,
                          "total_points": 0})
        self.assertEqual(plan.orphans, [])

    def test_malformed_accounts_are_errors_and_do_not_activate_their_owner(self):
        bad = [
            {"id": "a1", "owner": "kim", "active": "true"},
            {"id": "a2", "owner": "kim"},
            {"owner": "kim", "active": True},
            {"id": "a3", "owner": "kim", "active": 1},
        ]
        items = [item("i1", "kim", "open", 1)]
        plan = reconcile(bad, items, [])
        self.assertEqual(plan.summaries, [])
        self.assertEqual(plan.orphans, items)
        self.assertEqual(plan.errors, [{"service": "ledger", "record": r} for r in bad])

    def test_a_malformed_record_delivered_twice_is_recorded_once(self):
        bad = item("b1", "kim", "open", "x")
        plan = reconcile([account("kim")], [bad, dict(bad)], [])
        self.assertEqual(plan.errors, [{"service": "directory", "record": bad}])

    def test_owner_with_a_ledger_summary_is_not_missing(self):
        plan = reconcile([account("kim"), account("bo")], [],
                         [{"owner": "kim", "open_items": 9, "done_items": 9,
                           "total_points": 9}])
        self.assertEqual([s.owner for s in plan.missing()], ["bo"])


class IdempotencyKeyTest(unittest.TestCase):
    def test_plain_owner_uses_the_documented_form(self):
        self.assertEqual(idempotency_key("kim"), "sync-kim")

    def test_owner_that_cannot_travel_in_a_header_is_percent_encoded(self):
        self.assertEqual(idempotency_key("müller"), "sync-m%C3%BCller")
        self.assertEqual(idempotency_key("kim "), "sync-kim%20")
        self.assertEqual(idempotency_key("a\nb"), "sync-a%0Ab")


class RetryAfterTest(unittest.TestCase):
    def test_parses_seconds_and_falls_back_to_one_second(self):
        cases = {"3": 3.0, "0": 0.0, "1.5": 1.5, "": 1.0, "soon": 1.0,
                 "-2": 1.0, "nan": 1.0, "inf": 1.0}
        for raw, expected in cases.items():
            with self.subTest(header=raw):
                self.assertEqual(retry_after_seconds({"Retry-After": raw}), expected)
        self.assertEqual(retry_after_seconds({}), 1.0)
        self.assertEqual(retry_after_seconds(None), 1.0)


class ServiceClientTest(ServiceTestCase):
    def client_for(self, fake, clock):
        http = HttpClient(fake.url, opener=direct_opener())
        return ServiceClient("ledger", http, clock=clock, sleep=clock.sleep)

    def test_429_waits_retry_after_then_retries_the_same_request(self):
        fake = self.service(routes={ITEMS: ("items", [[item("i1", "kim", "open", 1)]])})
        fake.inject = [("GET", 429, {"Retry-After": "3"})]
        clock = FakeClock()

        records = self.client_for(fake, clock).fetch_all(ITEMS, "items")

        self.assertEqual(records, [item("i1", "kim", "open", 1)])
        self.assertEqual(clock.sleeps, [3.0])
        self.assertEqual([r.path for r in fake.requests], [ITEMS, ITEMS])

    def test_5xx_is_retried_a_few_times_then_abandoned(self):
        fake = self.service(routes={ITEMS: ("items", [[]])})
        fake.inject = [(None, 500, {})] * 3
        clock = FakeClock()

        with self.assertRaises(SyncError):
            self.client_for(fake, clock).send("GET", ITEMS, params={"page": 1})

        self.assertEqual(len(fake.requests), 3)
        self.assertEqual(clock.sleeps, [0.5, 1.0])

    def test_fetch_all_follows_next_page_until_null(self):
        pages = [[item("i1", "kim", "open", 1)], [item("i2", "kim", "done", 2)],
                 [item("i3", "bo", "open", 3)]]
        fake = self.service(routes={ITEMS: ("items", pages)})

        records = self.client_for(fake, FakeClock()).fetch_all(ITEMS, "items")

        self.assertEqual(records, [r for page in pages for r in page])
        self.assertEqual([r.query["page"] for r in fake.requests], ["1", "2", "3"])

    def test_unknown_path_is_a_sync_error(self):
        fake = self.service(routes={})
        with self.assertRaises(SyncError):
            self.client_for(fake, FakeClock()).fetch_all(ITEMS, "items")


class SyncRunTest(ServiceTestCase):
    def test_posts_only_missing_summaries_with_idempotency_keys(self):
        accounts = [account("kim"), account("bo"), account("lee", active=False)]
        items = [item("i1", "kim", "open", 3), item("i2", "kim", "open", 4),
                 item("i3", "kim", "done", 2), item("i4", "bo", "open", 1),
                 item("i5", "lee", "open", 5), item("i6", "zed", "done", 7)]
        existing = {"owner": "bo", "open_items": 0, "done_items": 0, "total_points": 0}
        directory, ledger = self.start([accounts], [items], summaries=[existing])

        code, err = self.run_cli(directory.url, ledger.url)

        self.assertEqual(code, 0, err)
        posts = ledger.posts()
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].idempotency_key, "sync-kim")
        expected = {"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9}
        self.assertEqual(posts[0].body, expected)
        self.assertEqual(self.read_jsonl("summary.jsonl"), [expected])
        self.assertCountEqual(self.read_jsonl("orphans.jsonl"), [items[4], items[5]])
        self.assertEqual(self.read_jsonl("errors.jsonl"), [])
        self.assertEqual(ledger.rejections, 0)

    def test_reads_every_page_of_accounts_items_and_summaries(self):
        account_pages = [[account("kim")], [account("bo")]]
        item_pages = [[item("i1", "kim", "open", 3)], [item("i2", "bo", "done", 2)]]
        directory = self.service(routes={ITEMS: ("items", item_pages)})
        ledger = self.service(routes={ACCOUNTS: ("accounts", account_pages)},
                              active_owners={"kim", "bo"})

        code, err = self.run_cli(directory.url, ledger.url)

        self.assertEqual(code, 0, err)
        self.assertCountEqual([p.body["owner"] for p in ledger.posts()], ["kim", "bo"])
        self.assertEqual(self.read_jsonl("orphans.jsonl"), [])

    def test_malformed_records_are_logged_and_the_run_completes(self):
        bad_account = {"id": "a9", "owner": "ghost", "active": "yes"}
        bad_item = {"id": "b9", "owner": "kim", "status": "open", "points": "3"}
        directory, ledger = self.start(
            [[account("kim"), bad_account]],
            [[item("i1", "kim", "open", 3), bad_item]])

        code, err = self.run_cli(directory.url, ledger.url)

        self.assertEqual(code, 0, err)
        self.assertCountEqual(self.read_jsonl("errors.jsonl"), [
            {"service": "ledger", "record": bad_account},
            {"service": "directory", "record": bad_item},
        ])
        self.assertEqual(self.read_jsonl("summary.jsonl"),
                         [{"owner": "kim", "open_items": 1, "done_items": 0,
                           "total_points": 3}])

    def test_second_run_issues_no_writes_and_leaves_outputs_unchanged(self):
        accounts = [account("kim"), account("lee", active=False)]
        items = [item("i1", "kim", "open", 3), item("i2", "lee", "done", 4)]
        directory, ledger = self.start([accounts], [items])
        self.assertEqual(self.run_cli(directory.url, ledger.url)[0], 0)
        first_outputs = self.snapshot()
        before = (len(directory.requests), len(ledger.requests))

        code, err = self.run_cli(directory.url, ledger.url)

        self.assertEqual(code, 0, err)
        self.assertEqual(self.snapshot(), first_outputs)
        new = directory.requests[before[0]:] + ledger.requests[before[1]:]
        self.assertTrue(new)
        self.assertEqual({r.method for r in new}, {"GET"})
        self.assertEqual(len(ledger.posts()), 1)
        self.assertEqual(ledger.rejections, 0)

    def test_rate_limited_post_waits_and_is_retried_with_the_same_key(self):
        directory, ledger = self.start([[account("kim")]],
                                       [[item("i1", "kim", "open", 3)]])
        ledger.inject = [("POST", 429, {"Retry-After": "1"})]

        code, err = self.run_cli(directory.url, ledger.url)

        self.assertEqual(code, 0, err)
        posts = ledger.posts()
        self.assertEqual(len(posts), 2)
        self.assertEqual({p.idempotency_key for p in posts}, {"sync-kim"})
        self.assertEqual(posts[0].body, posts[1].body)
        self.assertGreaterEqual(posts[1].at - posts[0].at, 1.0)
        self.assertEqual(ledger.rejections, 0)
        self.assertEqual(self.read_jsonl("summary.jsonl"), [posts[1].body])

    def test_rate_limit_on_directory_is_honored_before_the_next_read(self):
        directory, ledger = self.start([[account("kim")]],
                                       [[item("i1", "kim", "open", 3)],
                                        [item("i2", "kim", "done", 1)]])
        directory.inject = [(None, 429, {"Retry-After": "1"})]

        code, err = self.run_cli(directory.url, ledger.url)

        self.assertEqual(code, 0, err)
        self.assertEqual(len(directory.requests), 3)
        limited, retried = directory.requests[0], directory.requests[1]
        self.assertGreaterEqual(retried.at - limited.at, 1.0)
        self.assertEqual(self.read_jsonl("summary.jsonl")[0]["total_points"], 4)

    def test_unreachable_directory_fails_with_exit_4_and_writes_nothing(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        dead_url = "http://127.0.0.1:%d" % probe.getsockname()[1]
        probe.close()
        ledger = self.service(routes={ACCOUNTS: ("accounts", [[account("kim")]])},
                              active_owners={"kim"})

        code, err = self.run_cli(dead_url, ledger.url)

        self.assertEqual(code, EXIT_SERVICE_ERROR)
        self.assertIn("directory", err)
        self.assertFalse(os.path.exists(self.out_dir))


if __name__ == "__main__":
    unittest.main()
