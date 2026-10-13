"""Tests for `orgsync sync`.

Three layers, standard library only:

* unit tests for record validation, the reconciliation arithmetic, and the
  rate-limit handling (no sockets; time is faked);
* end-to-end tests that run `run_sync` against in-process fixture services
  with scripted data, 429s, pre-existing summaries and a request log, so each
  rule of the sync contract can be checked exactly;
* an integration test that runs the real command twice against the services
  named in `config.json`, through a recording proxy, and is skipped if those
  services are not reachable.

Run with `python3 -m unittest test_orgsync`.
"""

from __future__ import annotations

import copy
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from orgsync.config import Config, load_config
from orgsync.http_client import HttpClient
from orgsync.models import Summary
from orgsync.service import (DEFAULT_RETRY_AFTER_SECONDS, Service, ServiceError,
                             parse_retry_after)
from orgsync.sync import (EXIT_SYNC_ERROR, build_plan, idempotency_key,
                          parse_account, parse_item, run_sync)

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def read_jsonl(path):
    """Parsed JSON objects of a .jsonl file, in file order."""
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def canon(obj):
    return json.dumps(obj, sort_keys=True)


def as_multiset(records):
    return sorted(canon(r) for r in records)


def read_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def paginate(records, per_page):
    """Split `records` into pages of `per_page` (always at least one page)."""
    pages = [records[i:i + per_page] for i in range(0, len(records), per_page)]
    return pages or [[]]


class FixtureService:
    """An in-process stand-in for one of the two services.

    Serves scripted pages for its list endpoints, applies the Ledger's POST
    semantics to `/v1/summaries`, can be told to answer given request numbers
    with a 429, and logs every request with a timestamp and status.
    """

    def __init__(self, role, accounts=(), items=(), summaries=(), per_page=2,
                 rate_limit_on=(), retry_after="1", active_owners=None):
        self.role = role
        self.accounts = list(accounts)
        self.items = list(items)
        self.summaries = list(summaries)
        self.per_page = per_page
        self.rate_limit_on = set(rate_limit_on)
        self.retry_after = retry_after
        self.active_owners = active_owners
        self.posted = {}  # idempotency key -> body
        for summary in self.summaries:
            self.posted[idempotency_key(summary["owner"])] = summary
        self.log = []
        self.lock = threading.Lock()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def requests(self, method=None):
        return [r for r in self.log if method is None or r["method"] == method]

    def _handler(self):
        service = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _page(self, key, records):
                query = self.path.partition("?")[2]
                page = 1
                for pair in query.split("&"):
                    if pair.startswith("page="):
                        page = int(pair[5:])
                pages = paginate(records, service.per_page)
                if not 1 <= page <= len(pages):
                    return 404, {"error": "no such page"}
                next_page = page + 1 if page < len(pages) else None
                return 200, {key: pages[page - 1], "next_page": next_page}

            def _respond(self, status, body, headers=None):
                payload = b"" if body is None else json.dumps(body).encode("utf-8")
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _handle(self, method):
                with service.lock:
                    n = len(service.log) + 1
                    entry = {"n": n, "ts": time.monotonic(), "method": method,
                             "path": self.path, "status": None, "body": None,
                             "idempotency_key": self.headers.get("Idempotency-Key")}
                    service.log.append(entry)
                    if n in service.rate_limit_on:
                        entry["status"] = 429
                        self._respond(429, None, {"Retry-After": service.retry_after})
                        return
                    status, body = self._dispatch(method, entry)
                    entry["status"] = status
                    self._respond(status, body)

            def _dispatch(self, method, entry):
                path = self.path.partition("?")[0]
                if method == "GET":
                    if path == "/v1/accounts":
                        return self._page("accounts", service.accounts)
                    if path == "/v1/items":
                        return self._page("items", service.items)
                    if path == "/v1/summaries":
                        return self._page("summaries", service.summaries)
                    return 404, {"error": "not found"}
                if method == "POST" and path == "/v1/summaries":
                    length = int(self.headers.get("Content-Length") or 0)
                    try:
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                    except ValueError:
                        return 400, {"error": "bad json"}
                    entry["body"] = body
                    key = self.headers.get("Idempotency-Key")
                    if not key:
                        return 400, {"error": "missing Idempotency-Key"}
                    if key in service.posted:
                        if service.posted[key] == body:
                            return 200, body
                        return 409, {"error": "key reused with a different body"}
                    if not service._valid_summary(body):
                        return 422, {"error": "invalid summary"}
                    service.posted[key] = body
                    service.summaries.append(body)
                    return 201, body
                return 404, {"error": "not found"}

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        return Handler

    def _valid_summary(self, body):
        if not isinstance(body, dict):
            return False
        if set(body) != {"owner", "open_items", "done_items", "total_points"}:
            return False
        for key in ("open_items", "done_items", "total_points"):
            if not isinstance(body[key], int) or isinstance(body[key], bool):
                return False
        if self.active_owners is None:
            active = {a["owner"] for a in self.accounts
                      if parse_account(a) is not None and a["active"] is True}
        else:
            active = set(self.active_owners)
        return body["owner"] in active


class RecordingProxy:
    """Forwards every request to an upstream base URL and logs what happened.

    Used to observe what the real command sends to the real services without
    needing access to their own logs.
    """

    def __init__(self, upstream):
        self.upstream = upstream.rstrip("/")
        self.log = []
        self.lock = threading.Lock()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        proxy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _forward(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(length) if length else None
                headers = {}
                for name in ("Content-Type", "Idempotency-Key"):
                    if self.headers.get(name):
                        headers[name] = self.headers[name]
                request = urllib.request.Request(proxy.upstream + self.path, data=data,
                                                 method=method, headers=headers)
                try:
                    with urllib.request.urlopen(request, timeout=30) as resp:
                        status, resp_headers, body = resp.status, resp.headers, resp.read()
                except urllib.error.HTTPError as exc:
                    status, resp_headers, body = exc.code, exc.headers, exc.read()
                    exc.close()
                with proxy.lock:
                    proxy.log.append({
                        "ts": time.monotonic(), "method": method, "path": self.path,
                        "status": status, "retry_after": resp_headers.get("Retry-After"),
                        "idempotency_key": headers.get("Idempotency-Key"),
                        "body": json.loads(data.decode("utf-8")) if data else None})
                self.send_response(status)
                for name in ("Content-Type", "Retry-After"):
                    if resp_headers.get(name):
                        self.send_header(name, resp_headers[name])
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._forward("GET")

            def do_POST(self):
                self._forward("POST")

        return Handler


def fetch_all_pages(base_url, path, key):
    """Read every page of a list endpoint, waiting out any 429s."""
    records, page = [], 1
    while page is not None:
        request = urllib.request.Request("%s%s?page=%d" % (base_url, path, page))
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                document = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = parse_retry_after(exc.headers.get("Retry-After"))
                exc.close()
                time.sleep(wait + 0.05)
                continue
            raise
        records.extend(document.get(key) or [])
        page = document.get("next_page")
    return records


# --------------------------------------------------------------------------
# Scripted data shared by the end-to-end tests
# --------------------------------------------------------------------------

ACCOUNTS = [
    {"id": "acct-01", "owner": "amara", "active": True},
    {"id": "acct-02", "owner": "bo", "active": True, "extra": "ignored"},
    {"id": "acct-03", "owner": "chen", "active": False},
    {"id": "acct-04", "owner": "dee", "active": True},
    {"id": "acct-05", "owner": "fen", "active": "yes"},        # malformed: not a bool
    {"id": "acct-06", "owner": "gus", "active": 1},            # malformed: int, not bool
    {"id": 7, "owner": "hal", "active": True},                 # malformed: id not a str
    "not even an object",                                      # malformed
]

ITEMS = [
    {"id": "item-101", "owner": "amara", "status": "open", "points": 3},
    {"id": "item-102", "owner": "amara", "status": "done", "points": 2},
    {"id": "item-103", "owner": "bo", "status": "open", "points": 5, "note": "x"},
    {"id": "item-104", "owner": "chen", "status": "open", "points": 8},    # inactive owner
    {"id": "item-105", "owner": "zara", "status": "open", "points": 4},    # unknown owner
    {"id": "item-106", "owner": "fen", "status": "open", "points": 2},     # malformed acct
    {"id": "item-103", "owner": "bo", "status": "open", "points": 5},      # duplicate
    {"id": "item-107", "owner": "amara", "status": "open"},                # missing points
    {"id": "item-108", "owner": "amara", "status": "open", "points": "7"}, # str points
    {"id": "item-109", "owner": "amara", "status": "open", "points": -1},  # negative
    {"id": "item-110", "owner": "amara", "status": "closed", "points": 1}, # bad status
    {"id": "item-111", "owner": "amara", "status": "open", "points": True},# bool points
    {"id": "item-112", "owner": "bo", "status": "done", "points": 1},
    {"id": "item-112", "owner": "bo", "status": "done", "points": 1},      # duplicate
    {"id": "item-107", "owner": "amara", "status": "open"},                # dup malformed
    {"id": "item-105", "owner": "zara", "status": "open", "points": 4},    # dup orphan
    {"id": "item-113", "owner": "amara", "status": "open", "points": 0},
]

EXPECTED_SUMMARIES = {
    "amara": {"owner": "amara", "open_items": 2, "done_items": 1, "total_points": 5},
    "bo": {"owner": "bo", "open_items": 1, "done_items": 1, "total_points": 6},
    "dee": {"owner": "dee", "open_items": 0, "done_items": 0, "total_points": 0},
}

EXPECTED_ORPHANS = [ITEMS[3], ITEMS[4], ITEMS[5]]

EXPECTED_ERRORS = (
    [{"service": "ledger", "record": ACCOUNTS[i]} for i in (4, 5, 6, 7)]
    + [{"service": "directory", "record": ITEMS[i]} for i in (7, 8, 9, 10, 11)]
)


# --------------------------------------------------------------------------
# Unit tests: validation
# --------------------------------------------------------------------------

class ParseAccountTest(unittest.TestCase):
    def test_well_formed_account_with_extra_fields(self):
        account = parse_account({"id": "a", "owner": "kim", "active": False, "x": 1})
        self.assertEqual(account.to_dict(), {"id": "a", "owner": "kim", "active": False})

    def test_malformed_accounts(self):
        bad = [
            {"owner": "kim", "active": True},                 # missing id
            {"id": "a", "active": True},                      # missing owner
            {"id": "a", "owner": "kim"},                      # missing active
            {"id": "a", "owner": "kim", "active": "true"},    # string, not bool
            {"id": "a", "owner": "kim", "active": 1},         # int, not bool
            {"id": "a", "owner": "kim", "active": None},
            {"id": 1, "owner": "kim", "active": True},        # id not str
            {"id": "a", "owner": ["kim"], "active": True},    # owner not str
            ["id", "owner", "active"], "kim", None, 3,
        ]
        for record in bad:
            with self.subTest(record=record):
                self.assertIsNone(parse_account(record))


class ParseItemTest(unittest.TestCase):
    def test_well_formed_items(self):
        for status in ("open", "done"):
            item = parse_item({"id": "i", "owner": "kim", "status": status,
                               "points": 0, "extra": None})
            self.assertEqual(item.to_dict(),
                             {"id": "i", "owner": "kim", "status": status, "points": 0})

    def test_malformed_items(self):
        base = {"id": "i", "owner": "kim", "status": "open", "points": 3}
        bad = []
        for key in base:
            record = dict(base)
            del record[key]
            bad.append(record)
        for key, value in [("id", 1), ("owner", None), ("status", "closed"),
                           ("status", "OPEN"), ("points", "3"), ("points", 3.0),
                           ("points", -1), ("points", True), ("points", None)]:
            bad.append(dict(base, **{key: value}))
        bad.extend([[], "item", None, 7])
        for record in bad:
            with self.subTest(record=record):
                self.assertIsNone(parse_item(record))


class ParseRetryAfterTest(unittest.TestCase):
    def test_delta_seconds(self):
        self.assertEqual(parse_retry_after("3"), 3.0)
        self.assertEqual(parse_retry_after(" 2.5 "), 2.5)
        self.assertEqual(parse_retry_after("-4"), 0.0)

    def test_http_date(self):
        now = 1_800_000_000.0
        from email.utils import formatdate
        wait = parse_retry_after(formatdate(now + 10, usegmt=True), now=now)
        self.assertAlmostEqual(wait, 10.0, delta=1.0)

    def test_missing_or_garbage_uses_default(self):
        for value in (None, "", "soon", "1s"):
            self.assertEqual(parse_retry_after(value), DEFAULT_RETRY_AFTER_SECONDS)


# --------------------------------------------------------------------------
# Unit tests: reconciliation arithmetic
# --------------------------------------------------------------------------

class BuildPlanTest(unittest.TestCase):
    def test_worked_example(self):
        accounts = [{"id": "a", "owner": "kim", "active": True}]
        items = [{"id": "1", "owner": "kim", "status": "open", "points": 3},
                 {"id": "2", "owner": "kim", "status": "open", "points": 4},
                 {"id": "3", "owner": "kim", "status": "done", "points": 2}]
        plan = build_plan(accounts, items, [])
        self.assertEqual(plan.required["kim"],
                         Summary("kim", open_items=2, done_items=1, total_points=9))
        self.assertEqual(plan.missing, ["kim"])
        self.assertEqual((plan.orphans, plan.errors), ([], []))

    def test_scripted_data(self):
        plan = build_plan(ACCOUNTS, ITEMS, [])
        self.assertEqual({o: s.to_dict() for o, s in plan.required.items()},
                         EXPECTED_SUMMARIES)
        self.assertEqual(sorted(plan.missing), sorted(EXPECTED_SUMMARIES))
        self.assertEqual(as_multiset(plan.orphans), as_multiset(EXPECTED_ORPHANS))
        self.assertEqual(as_multiset(plan.errors), as_multiset(EXPECTED_ERRORS))

    def test_duplicate_delivery_counts_once(self):
        accounts = [{"id": "a", "owner": "kim", "active": True}]
        item = {"id": "1", "owner": "kim", "status": "open", "points": 3}
        plan = build_plan(accounts, [item, item, dict(item)], [])
        self.assertEqual(plan.required["kim"], Summary("kim", 1, 0, 3))

    def test_active_account_without_items_gets_zero_summary(self):
        plan = build_plan([{"id": "a", "owner": "kim", "active": True}], [], [])
        self.assertEqual(plan.required["kim"], Summary("kim", 0, 0, 0))
        self.assertEqual(plan.missing, ["kim"])

    def test_inactive_and_malformed_accounts_get_no_summary(self):
        accounts = [{"id": "a", "owner": "kim", "active": False},
                    {"id": "b", "owner": "lee", "active": "true"}]
        items = [{"id": "1", "owner": "kim", "status": "open", "points": 3},
                 {"id": "2", "owner": "lee", "status": "done", "points": 1}]
        plan = build_plan(accounts, items, [])
        self.assertEqual(plan.required, {})
        self.assertEqual(as_multiset(plan.orphans), as_multiset(items))
        self.assertEqual(plan.errors, [{"service": "ledger", "record": accounts[1]}])

    def test_malformed_items_never_enter_counts_or_orphans(self):
        accounts = [{"id": "a", "owner": "kim", "active": True}]
        items = [{"id": "1", "owner": "kim", "status": "open", "points": "3"},
                 {"id": "2", "owner": "nobody", "status": "open"}]
        plan = build_plan(accounts, items, [])
        self.assertEqual(plan.required["kim"], Summary("kim", 0, 0, 0))
        self.assertEqual(plan.orphans, [])
        self.assertEqual(as_multiset(plan.errors), as_multiset(
            [{"service": "directory", "record": items[0]},
             {"service": "directory", "record": items[1]}]))

    def test_errors_and_orphans_are_recorded_once(self):
        accounts = [{"id": "a", "owner": "kim", "active": "yes"}]
        orphan = {"id": "1", "owner": "kim", "status": "open", "points": 1}
        bad = {"id": "2", "owner": "kim", "status": "open"}
        plan = build_plan(accounts + accounts, [orphan, bad, orphan, bad], [])
        self.assertEqual(plan.orphans, [orphan])
        self.assertEqual(plan.errors, [{"service": "ledger", "record": accounts[0]},
                                       {"service": "directory", "record": bad}])

    def test_existing_summaries_are_not_missing(self):
        accounts = [{"id": "a", "owner": "kim", "active": True},
                    {"id": "b", "owner": "lee", "active": True}]
        existing = [{"owner": "kim", "open_items": 0, "done_items": 0, "total_points": 0}]
        plan = build_plan(accounts, [], existing)
        self.assertEqual(sorted(plan.required), ["kim", "lee"])
        self.assertEqual(plan.missing, ["lee"])


# --------------------------------------------------------------------------
# Unit tests: rate limiting and pagination (fake transport, fake time)
# --------------------------------------------------------------------------

class ScriptedOpener:
    """Hands back scripted (status, headers, body) outcomes; records requests."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        status, headers, body = self.outcomes.pop(0)
        payload = json.dumps(body).encode("utf-8") if body is not None else b""
        import email.message
        message = email.message.Message()
        for key, value in headers.items():
            message[key] = value
        if status >= 400:
            import io
            raise urllib.error.HTTPError(request.full_url, status, "x", message,
                                         io.BytesIO(payload))

        class Resp:
            def __init__(self):
                self.status, self.headers = status, message

            def read(self):
                return payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False
        return Resp()


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_service(outcomes, clock):
    client = HttpClient("http://svc.test", opener=ScriptedOpener(outcomes))
    return Service("svc", client, clock=clock, sleep=clock.sleep, log=lambda m: None)


class ServiceTest(unittest.TestCase):
    def test_429_is_retried_after_retry_after_and_never_returned(self):
        clock = FakeClock()
        service = make_service([(429, {"Retry-After": "3"}, None),
                                (429, {"Retry-After": "2"}, None),
                                (200, {}, {"ok": True})], clock)
        response = service.get("/v1/x")
        self.assertEqual(response.status, 200)
        self.assertEqual(len(service.client._opener.requests), 3)
        self.assertEqual(service.rate_limit_hits, 2)
        self.assertEqual(len(clock.sleeps), 2)
        self.assertGreaterEqual(clock.sleeps[0], 3.0)
        self.assertGreaterEqual(clock.sleeps[1], 2.0)

    def test_retry_after_blocks_the_next_request_to_the_same_service(self):
        clock = FakeClock()
        service = make_service([(429, {"Retry-After": "5"}, None),
                                (200, {}, {"a": 1}),
                                (200, {}, {"b": 2})], clock)
        start = clock.now
        service.get("/v1/x")
        self.assertGreaterEqual(clock.now - start, 5.0)
        # The limit is spent once honoured: the next request goes straight out.
        before = len(clock.sleeps)
        service.get("/v1/y")
        self.assertEqual(len(clock.sleeps), before)

    def test_missing_retry_after_uses_default_wait(self):
        clock = FakeClock()
        service = make_service([(429, {}, None), (200, {}, {})], clock)
        service.get("/v1/x")
        self.assertGreaterEqual(clock.sleeps[0], DEFAULT_RETRY_AFTER_SECONDS)

    def test_rejections_are_returned_to_the_caller(self):
        clock = FakeClock()
        service = make_service([(422, {}, {"error": "no"})], clock)
        self.assertEqual(service.post_json("/v1/summaries", {}, {}).status, 422)
        self.assertEqual(clock.sleeps, [])

    def test_server_errors_are_retried_then_raised(self):
        clock = FakeClock()
        service = make_service([(503, {}, None), (200, {}, {"ok": 1})], clock)
        self.assertEqual(service.get("/v1/x").status, 200)
        service = make_service([(500, {}, None)] * 10, clock)
        with self.assertRaises(ServiceError):
            service.get("/v1/x")

    def test_fetch_all_follows_next_page_until_null(self):
        clock = FakeClock()
        service = make_service([
            (200, {}, {"items": [1, 2], "next_page": 2}),
            (429, {"Retry-After": "1"}, None),
            (200, {}, {"items": [3], "next_page": 3}),
            (200, {}, {"items": [], "next_page": None}),
        ], clock)
        self.assertEqual(service.fetch_all("/v1/items", "items"), [1, 2, 3])
        urls = [r.full_url for r in service.client._opener.requests]
        self.assertEqual(urls, ["http://svc.test/v1/items?page=1",
                                "http://svc.test/v1/items?page=2",
                                "http://svc.test/v1/items?page=2",
                                "http://svc.test/v1/items?page=3"])

    def test_fetch_all_rejects_unreadable_pages(self):
        clock = FakeClock()
        for outcome in [(404, {}, {"error": "x"}), (200, {}, [1, 2]),
                        (200, {}, {"items": "nope", "next_page": None}),
                        (200, {}, {"items": [], "next_page": "2"})]:
            with self.subTest(outcome=outcome):
                with self.assertRaises(ServiceError):
                    make_service([outcome], clock).fetch_all("/v1/items", "items")


# --------------------------------------------------------------------------
# End-to-end against in-process fixture services
# --------------------------------------------------------------------------

class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = os.path.join(self.tmp.name, "nested", "out")

    def start(self, accounts=ACCOUNTS, items=ITEMS, summaries=(), **kwargs):
        ledger_kwargs = {k: v for k, v in kwargs.items() if k.startswith("ledger_")}
        directory_kwargs = {k: v for k, v in kwargs.items() if k.startswith("directory_")}
        self.ledger = FixtureService(
            "ledger", accounts=copy.deepcopy(accounts), summaries=copy.deepcopy(summaries),
            **{k[len("ledger_"):]: v for k, v in ledger_kwargs.items()})
        self.directory = FixtureService(
            "directory", items=copy.deepcopy(items),
            **{k[len("directory_"):]: v for k, v in directory_kwargs.items()})
        self.addCleanup(self.ledger.close)
        self.addCleanup(self.directory.close)
        return Config(self.directory.url, self.ledger.url)

    def sync(self, config):
        return run_sync(config, self.out_dir, log=lambda message: None)

    def outputs(self):
        return {name: read_file(os.path.join(self.out_dir, name + ".jsonl"))
                for name in ("orphans", "errors", "summary")}

    def assert_no_rejected_writes(self):
        for entry in self.ledger.requests("POST") + self.directory.requests("POST"):
            self.assertNotIn(entry["status"], (400, 409, 422), entry)

    def test_first_run_posts_every_missing_summary_and_writes_outputs(self):
        config = self.start()
        self.assertEqual(self.sync(config), 0)

        self.assertTrue(os.path.isdir(self.out_dir))
        out = self.outputs()
        self.assertEqual(as_multiset(read_jsonl(os.path.join(self.out_dir, "summary.jsonl"))),
                         as_multiset(EXPECTED_SUMMARIES.values()))
        self.assertEqual(as_multiset(read_jsonl(os.path.join(self.out_dir, "orphans.jsonl"))),
                         as_multiset(EXPECTED_ORPHANS))
        self.assertEqual(as_multiset(read_jsonl(os.path.join(self.out_dir, "errors.jsonl"))),
                         as_multiset(EXPECTED_ERRORS))
        # One line per entry, no blank lines, no duplicates.
        for name, text in out.items():
            lines = text.splitlines()
            self.assertEqual(len(lines), len(set(lines)), name)
            self.assertTrue(all(lines), name)

        posts = self.ledger.requests("POST")
        self.assertEqual(as_multiset(p["body"] for p in posts),
                         as_multiset(EXPECTED_SUMMARIES.values()))
        for post in posts:
            self.assertEqual(post["status"], 201)
            self.assertEqual(post["idempotency_key"], "sync-%s" % post["body"]["owner"])
        self.assert_no_rejected_writes()
        self.assertEqual(self.directory.requests("POST"), [])
        # The ledger now holds exactly one summary per active account.
        self.assertEqual(as_multiset(self.ledger.summaries),
                         as_multiset(EXPECTED_SUMMARIES.values()))
        # Summary lines are exactly the POST bodies.
        self.assertEqual(sorted(json.dumps(p["body"]) for p in posts),
                         sorted(out["summary"].splitlines()))

    def test_second_run_is_idempotent(self):
        config = self.start()
        self.assertEqual(self.sync(config), 0)
        first = self.outputs()
        ledger_before = copy.deepcopy(self.ledger.summaries)
        writes_before = len(self.ledger.requests("POST"))

        self.assertEqual(self.sync(config), 0)

        self.assertEqual(self.outputs(), first)
        self.assertEqual(len(self.ledger.requests("POST")), writes_before)
        self.assertEqual(self.ledger.summaries, ledger_before)
        self.assert_no_rejected_writes()
        # Every page was read again: the run never trusts stale state.
        self.assertTrue(any(r["path"].startswith("/v1/summaries")
                            for r in self.ledger.requests("GET")[-3:]))

    def test_reads_every_page_of_every_endpoint(self):
        config = self.start(ledger_per_page=1, directory_per_page=3)
        self.assertEqual(self.sync(config), 0)
        pages = lambda svc, path: sorted(
            int(r["path"].partition("page=")[2]) for r in svc.requests("GET")
            if r["path"].startswith(path) and r["status"] == 200)
        self.assertEqual(pages(self.ledger, "/v1/accounts"), list(range(1, len(ACCOUNTS) + 1)))
        self.assertEqual(pages(self.directory, "/v1/items"),
                         list(range(1, -(-len(ITEMS) // 3) + 1)))
        self.assertEqual(pages(self.ledger, "/v1/summaries"), [1])
        self.assertEqual(as_multiset(self.ledger.summaries),
                         as_multiset(EXPECTED_SUMMARIES.values()))

    def test_posts_only_what_the_ledger_is_missing(self):
        existing = {"owner": "bo", "open_items": 0, "done_items": 0, "total_points": 0}
        config = self.start(summaries=[existing])
        self.assertEqual(self.sync(config), 0)
        posts = self.ledger.requests("POST")
        self.assertEqual(sorted(p["body"]["owner"] for p in posts), ["amara", "dee"])
        self.assert_no_rejected_writes()
        self.assertEqual(sorted(s["owner"] for s in self.ledger.summaries),
                         ["amara", "bo", "dee"])
        self.assertEqual(sorted(s["owner"] for s in
                                read_jsonl(os.path.join(self.out_dir, "summary.jsonl"))),
                         ["amara", "dee"])

    def test_rate_limits_are_honoured_on_reads_and_writes(self):
        # Ledger: 429 on the 2nd request (an accounts page) and on the first
        # POST; Directory: 429 on its very first request.
        config = self.start(ledger_rate_limit_on=(2, 6), ledger_retry_after="1",
                            directory_rate_limit_on=(1,), directory_retry_after="1")
        self.assertEqual(self.sync(config), 0)

        for service in (self.ledger, self.directory):
            log = service.log
            limited = [e for e in log if e["status"] == 429]
            self.assertTrue(limited, service.role)
            for entry in limited:
                later = [e for e in log if e["n"] > entry["n"]]
                self.assertTrue(later, "request after 429 was never retried")
                nxt = later[0]
                self.assertGreaterEqual(nxt["ts"] - entry["ts"], 1.0,
                                        "%s was called too soon after a 429" % service.role)
                self.assertEqual((nxt["method"], nxt["path"], nxt["idempotency_key"]),
                                 (entry["method"], entry["path"], entry["idempotency_key"]),
                                 "the retried request should be the same request")
        # The 429s changed nothing about the result.
        self.assertEqual(as_multiset(self.ledger.summaries),
                         as_multiset(EXPECTED_SUMMARIES.values()))
        self.assert_no_rejected_writes()
        self.assertEqual(as_multiset(read_jsonl(os.path.join(self.out_dir, "summary.jsonl"))),
                         as_multiset(EXPECTED_SUMMARIES.values()))
        # A 429'd POST is not a rejection and is not recorded twice.
        self.assertEqual(sum(1 for e in self.ledger.log
                             if e["method"] == "POST" and e["status"] == 201), 3)

    def test_empty_services_produce_empty_outputs_and_no_writes(self):
        config = self.start(accounts=[], items=[])
        self.assertEqual(self.sync(config), 0)
        self.assertEqual(self.outputs(), {"orphans": "", "errors": "", "summary": ""})
        self.assertEqual(self.ledger.requests("POST"), [])

    def test_unreachable_service_is_reported_not_raised(self):
        config = self.start()
        dead = Config("http://127.0.0.1:1", self.ledger.url)
        self.assertEqual(self.sync(dead), EXIT_SYNC_ERROR)
        self.assertEqual(self.ledger.requests("POST"), [])

    def test_summary_file_survives_a_rerun_from_a_fresh_out_dir_when_nothing_is_posted(self):
        # Not required by the contract, but documents the behaviour: a rerun
        # that posts nothing and has no earlier summary file writes an empty
        # one rather than inventing lines for summaries it never sent.
        config = self.start()
        self.assertEqual(self.sync(config), 0)
        self.out_dir = os.path.join(self.tmp.name, "other")
        self.assertEqual(self.sync(config), 0)
        self.assertEqual(self.outputs()["summary"], "")


# --------------------------------------------------------------------------
# Integration: the real command against the real services (if reachable)
# --------------------------------------------------------------------------

def services_reachable(config):
    """True if both services answer HTTP at all (any status counts)."""
    for url, path in ((config.directory_url, "/v1/items"),
                      (config.ledger_url, "/v1/summaries")):
        try:
            urllib.request.urlopen(url + path + "?page=1", timeout=3).close()
        except urllib.error.HTTPError as exc:
            exc.close()
        except (urllib.error.URLError, OSError):
            return False
    return True


class RealServicesIntegrationTest(unittest.TestCase):
    """Runs `python3 -m orgsync sync` twice against the configured services.

    The services keep state across runs, so this checks invariants rather
    than exact counts: no rejected write, every active account summarised,
    rate limits honoured, and a second run that writes nothing.
    """

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(CONFIG_PATH):
            raise unittest.SkipTest("no config.json")
        cls.config = load_config(CONFIG_PATH)
        if not services_reachable(cls.config):
            raise unittest.SkipTest("services in config.json are not reachable")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = RecordingProxy(self.config.directory_url)
        self.ledger = RecordingProxy(self.config.ledger_url)
        self.addCleanup(self.directory.close)
        self.addCleanup(self.ledger.close)
        self.config_path = os.path.join(self.tmp.name, "config.json")
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump({"directory_url": self.directory.url,
                       "ledger_url": self.ledger.url}, handle)
        self.out_dir = os.path.join(self.tmp.name, "out")

    def run_sync_command(self):
        return subprocess.run(
            [sys.executable, "-m", "orgsync", "sync", "--config", self.config_path,
             "--out-dir", self.out_dir],
            cwd=HERE, capture_output=True, text=True, timeout=300)

    def outputs(self):
        return {name: read_file(os.path.join(self.out_dir, name + ".jsonl"))
                for name in ("orphans", "errors", "summary")}

    def assert_rate_limits_honoured(self, proxy):
        for index, entry in enumerate(proxy.log):
            if entry["status"] != 429:
                continue
            self.assertLess(index + 1, len(proxy.log), "429 was never retried")
            nxt = proxy.log[index + 1]
            self.assertGreaterEqual(nxt["ts"] - entry["ts"],
                                    parse_retry_after(entry["retry_after"]))
            self.assertEqual((nxt["method"], nxt["path"]), (entry["method"], entry["path"]))

    def test_two_runs_reconcile_without_rejected_writes(self):
        first = self.run_sync_command()
        self.assertEqual(first.returncode, 0, first.stderr)
        first_outputs = self.outputs()

        # No rejected write, ever.
        for proxy in (self.ledger, self.directory):
            for entry in proxy.log:
                if entry["method"] == "POST":
                    self.assertIn(entry["status"], (200, 201, 429), entry)
                    self.assertEqual(entry["idempotency_key"],
                                     "sync-%s" % entry["body"]["owner"])
            self.assert_rate_limits_honoured(proxy)
        self.assertEqual([e for e in self.directory.log if e["method"] == "POST"], [])

        # Every page of every endpoint was read, from page 1 to the last.
        for proxy, path in ((self.ledger, "/v1/accounts"), (self.directory, "/v1/items"),
                            (self.ledger, "/v1/summaries")):
            pages = sorted(int(e["path"].partition("page=")[2]) for e in proxy.log
                           if e["path"].startswith(path) and e["status"] == 200)
            self.assertEqual(pages, list(range(1, len(pages) + 1)), path)
            self.assertGreaterEqual(len(pages), 1)

        # The ledger now holds exactly one summary per active, well-formed
        # account, with the right arithmetic, and none for anyone else.
        accounts = fetch_all_pages(self.config.ledger_url, "/v1/accounts", "accounts")
        items = fetch_all_pages(self.config.directory_url, "/v1/items", "items")
        summaries = fetch_all_pages(self.config.ledger_url, "/v1/summaries", "summaries")
        plan = build_plan(accounts, items, [])
        owners = [s["owner"] for s in summaries]
        self.assertEqual(sorted(owners), sorted(plan.required))
        by_owner = {s["owner"]: s for s in summaries}
        for owner, summary in plan.required.items():
            self.assertEqual(by_owner[owner], summary.to_dict(), owner)

        # The output files reflect the same decision.
        self.assertEqual(as_multiset(read_jsonl(os.path.join(self.out_dir, "orphans.jsonl"))),
                         as_multiset(plan.orphans))
        self.assertEqual(as_multiset(read_jsonl(os.path.join(self.out_dir, "errors.jsonl"))),
                         as_multiset(plan.errors))
        posted = [e["body"] for e in self.ledger.log
                  if e["method"] == "POST" and e["status"] in (200, 201)]
        self.assertEqual(as_multiset(read_jsonl(os.path.join(self.out_dir, "summary.jsonl"))),
                         as_multiset(posted))
        for body in posted:
            self.assertEqual(body, plan.required[body["owner"]].to_dict())

        # Second run: no writes, same files.
        writes_before = len([e for e in self.ledger.log if e["method"] == "POST"])
        second = self.run_sync_command()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.outputs(), first_outputs)
        self.assertEqual(len([e for e in self.ledger.log if e["method"] == "POST"]),
                         writes_before)
        self.assert_rate_limits_honoured(self.ledger)
        self.assert_rate_limits_honoured(self.directory)


if __name__ == "__main__":
    unittest.main()
