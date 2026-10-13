"""Tests for `orgsync sync`.

Three layers:

* Pure logic — validation, reconciliation arithmetic, de-duplication, the
  Retry-After parser, output merging — with no I/O at all.
* The `Service` layer against a fake transport with a fake clock, so the
  rate-limit waiting is checked exactly without sleeping.
* End-to-end — `run_sync` against an in-process fake Directory and Ledger
  (standard-library `http.server`) that paginate, duplicate, rate-limit with
  `Retry-After`, and implement the Ledger's 201/200/409/422 semantics. The
  fake enforces rate limits: any request that arrives before `Retry-After`
  has elapsed is recorded as a violation.

Finally a smoke test runs the real command twice against the services named
in `config.json`, if they are reachable, and checks idempotency.

Run with: python3 -m unittest test_orgsync
"""

from __future__ import annotations

import contextlib
import email.message
import io
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from orgsync.config import Config, load_config
from orgsync.http_client import Response
from orgsync.models import Account, Item, Summary
from orgsync.service import (Service, ServiceError,
                             make_service, parse_retry_after)
from orgsync.sync import (ERRORS_FILE, ORPHANS_FILE, SUMMARY_FILE,
                          canonical, merge_summary_records, owners_with_summaries,
                          reconcile, run_sync, write_jsonl)
from orgsync.validate import validate_account, validate_item

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def read_jsonl(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def paginate(records, size):
    """Split `records` into pages of `size` (at least one page)."""
    pages = [records[i:i + size] for i in range(0, len(records), size)]
    return pages or [[]]


class FakeServices:
    """An in-process Directory and Ledger.

    `items_pages` / `accounts_pages` are lists of pages (lists of raw
    records), served as page 1..N with `next_page` chaining. Existing
    summaries can be seeded with `summaries`. `rate_limits` maps
    `(service, method, path, page)` to a count of 429s to answer before
    serving, each with `Retry-After: <retry_after>`. Every request is logged
    and every rate-limit violation (a request to a service before its
    Retry-After has elapsed) is recorded.
    """

    def __init__(self, items_pages=None, accounts_pages=None, summaries=None,
                 rate_limits=None, retry_after=1, summaries_page_size=100):
        self.items_pages = items_pages if items_pages is not None else [[]]
        self.accounts_pages = accounts_pages if accounts_pages is not None else [[]]
        self.summaries = list(summaries or [])
        self.summaries_page_size = summaries_page_size
        self.rate_limits = dict(rate_limits or {})
        self.retry_after = retry_after
        self.accounts_active = set()
        self.keys = {}
        self.log = []
        self.violations = []
        self.not_before = {"directory": 0.0, "ledger": 0.0}
        self.lock = threading.Lock()
        self.servers = {}
        self.threads = []

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        for name in ("directory", "ledger"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class(name))
            server.daemon_threads = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.servers[name] = server
            self.threads.append(thread)
        return self

    def stop(self):
        for server in self.servers.values():
            server.shutdown()
            server.server_close()

    def url(self, name):
        host, port = self.servers[name].server_address[:2]
        return "http://%s:%d" % (host, port)

    def config(self):
        return Config(self.url("directory"), self.url("ledger"))

    # -- query helpers -----------------------------------------------------

    def requests(self, name, method=None):
        return [entry for entry in self.log
                if entry["service"] == name and (method is None or entry["method"] == method)]

    def posts(self):
        return self.requests("ledger", "POST")

    def active_owners(self):
        owners = set()
        for page in self.accounts_pages:
            for record in page:
                if validate_account(record) is not None and record["active"] is True:
                    owners.add(record["owner"])
        return owners

    # -- the HTTP side -----------------------------------------------------

    def _handler_class(self, name):
        services = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silence
                pass

            def _send(self, status, payload=None, headers=None):
                body = b"" if payload is None else json.dumps(payload).encode("utf-8")
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, str(value))
                if body:
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _handle(self, method):
                parsed = urlparse(self.path)
                query = parse_qs(parsed.query)
                page = int(query.get("page", ["1"])[0]) if "page" in query else None
                length = int(self.headers.get("Content-Length") or 0)
                raw_body = self.rfile.read(length) if length else b""
                body = json.loads(raw_body) if raw_body else None
                key = self.headers.get("Idempotency-Key")
                now = time.monotonic()
                with services.lock:
                    entry = {"service": name, "method": method, "path": parsed.path,
                             "page": page, "key": key, "body": body}
                    if now < services.not_before[name]:
                        services.violations.append(dict(entry, early_by=services.not_before[name] - now))
                    limit_key = (name, method, parsed.path, page)
                    remaining = services.rate_limits.get(limit_key, 0)
                    if remaining > 0:
                        services.rate_limits[limit_key] = remaining - 1
                        services.not_before[name] = now + services.retry_after
                        entry["status"] = 429
                        services.log.append(entry)
                        self._send(429, headers={"Retry-After": services.retry_after})
                        return
                    status, payload = services._respond(name, method, parsed.path, page, key, body)
                    entry["status"] = status
                    services.log.append(entry)
                self._send(status, payload)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

        return Handler

    def _respond(self, name, method, path, page, key, body):
        if name == "directory" and method == "GET" and path == "/v1/items":
            return self._page("items", self.items_pages, page)
        if name == "ledger" and method == "GET" and path == "/v1/accounts":
            return self._page("accounts", self.accounts_pages, page)
        if name == "ledger" and method == "GET" and path == "/v1/summaries":
            pages = paginate(self.summaries, self.summaries_page_size)
            return self._page("summaries", pages, page)
        if name == "ledger" and method == "POST" and path == "/v1/summaries":
            return self._post_summary(key, body)
        return 404, {"error": "no such endpoint"}

    @staticmethod
    def _page(key, pages, page):
        if page is None or page < 1 or page > len(pages):
            return 200, {key: [], "next_page": None}
        next_page = page + 1 if page < len(pages) else None
        return 200, {key: pages[page - 1], "next_page": next_page}

    def _post_summary(self, key, body):
        if key is None:
            return 400, {"error": "missing Idempotency-Key"}
        if not isinstance(body, dict):
            return 400, {"error": "body must be an object"}
        if key in self.keys:
            if self.keys[key] == body:
                return 200, body
            return 409, {"error": "idempotency key reused with a different body"}
        expected = {"owner", "open_items", "done_items", "total_points"}
        if set(body) != expected or not isinstance(body["owner"], str) or not all(
                isinstance(body[f], int) and not isinstance(body[f], bool)
                for f in ("open_items", "done_items", "total_points")):
            return 422, {"error": "invalid summary"}
        if body["owner"] not in self.active_owners():
            return 422, {"error": "owner is not active"}
        if any(s.get("owner") == body["owner"] for s in self.summaries):
            return 409, {"error": "owner already has a summary"}
        self.keys[key] = body
        self.summaries.append(body)
        return 201, body


# A fixture mirroring the shape of the provisioned services: overlapping
# pages, a malformed item, a malformed account, inactive accounts, an active
# account with no items, and orphans of three kinds.
ACCOUNTS = [
    [{"id": "acct-01", "owner": "amara", "active": True},
     {"id": "acct-02", "owner": "bo", "active": True},
     {"id": "acct-03", "owner": "chen", "active": False}],
    [{"id": "acct-04", "owner": "dee", "active": True},
     {"id": "acct-05", "owner": "eli", "active": True},
     {"id": "acct-06", "owner": "fen", "active": "yes"}],
    [{"id": "acct-07", "owner": "gus", "active": False}],
]
ITEMS = [
    [{"id": "item-101", "owner": "amara", "status": "open", "points": 3},
     {"id": "item-102", "owner": "amara", "status": "done", "points": 2},
     {"id": "item-103", "owner": "bo", "status": "open", "points": 5},
     {"id": "item-104", "owner": "chen", "status": "open", "points": 8}],
    [{"id": "item-105", "owner": "eli", "status": "done", "points": 1},
     {"id": "item-106", "owner": "zara", "status": "open", "points": 4},
     {"id": "item-107", "owner": "fen", "status": "open", "points": 2},
     {"id": "item-108", "owner": "bo", "status": "done", "points": 1}],
    [{"id": "item-103", "owner": "bo", "status": "open", "points": 5},
     {"id": "item-109", "owner": "amara", "status": "open"},
     {"id": "item-110", "owner": "amara", "status": "open", "points": 7},
     {"id": "item-111", "owner": "eli", "status": "open", "points": 2}],
]
EXPECTED_SUMMARIES = {
    "amara": {"owner": "amara", "open_items": 2, "done_items": 1, "total_points": 12},
    "bo": {"owner": "bo", "open_items": 1, "done_items": 1, "total_points": 6},
    "dee": {"owner": "dee", "open_items": 0, "done_items": 0, "total_points": 0},
    "eli": {"owner": "eli", "open_items": 1, "done_items": 1, "total_points": 3},
}
EXPECTED_ORPHAN_IDS = {"item-104", "item-106", "item-107"}
EXPECTED_ERRORS = [
    {"service": "ledger", "record": {"id": "acct-06", "owner": "fen", "active": "yes"}},
    {"service": "directory", "record": {"id": "item-109", "owner": "amara", "status": "open"}},
]


def sort_records(records):
    return sorted(records, key=canonical)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


class ValidateAccountTest(unittest.TestCase):
    def test_well_formed_account(self):
        self.assertEqual(validate_account({"id": "a", "owner": "kim", "active": True}),
                         Account("a", "kim", True))
        self.assertEqual(validate_account({"id": "a", "owner": "kim", "active": False}),
                         Account("a", "kim", False))

    def test_extra_fields_are_ignored(self):
        self.assertEqual(validate_account({"id": "a", "owner": "kim", "active": True,
                                           "plan": "gold", "nested": {"x": 1}}),
                         Account("a", "kim", True))

    def test_malformed_accounts(self):
        bad = [
            {"owner": "kim", "active": True},            # missing id
            {"id": "a", "active": True},                 # missing owner
            {"id": "a", "owner": "kim"},                 # missing active
            {"id": 1, "owner": "kim", "active": True},   # id not a string
            {"id": "a", "owner": None, "active": True},  # owner not a string
            {"id": "a", "owner": "kim", "active": "yes"},
            {"id": "a", "owner": "kim", "active": 1},
            {"id": "a", "owner": "kim", "active": 0},
            {"id": "a", "owner": "kim", "active": None},
            "not an object", 42, None, ["id", "owner", "active"],
        ]
        for record in bad:
            with self.subTest(record=record):
                self.assertIsNone(validate_account(record))


class ValidateItemTest(unittest.TestCase):
    def test_well_formed_item(self):
        self.assertEqual(validate_item({"id": "i", "owner": "kim", "status": "open", "points": 3}),
                         Item("i", "kim", "open", 3))
        self.assertEqual(validate_item({"id": "i", "owner": "kim", "status": "done", "points": 0}),
                         Item("i", "kim", "done", 0))

    def test_extra_fields_are_ignored(self):
        self.assertEqual(validate_item({"id": "i", "owner": "kim", "status": "open",
                                        "points": 3, "title": "x", "tags": []}),
                         Item("i", "kim", "open", 3))

    def test_malformed_items(self):
        bad = [
            {"owner": "kim", "status": "open", "points": 3},
            {"id": "i", "status": "open", "points": 3},
            {"id": "i", "owner": "kim", "points": 3},
            {"id": "i", "owner": "kim", "status": "open"},
            {"id": "i", "owner": "kim", "status": "closed", "points": 3},
            {"id": "i", "owner": "kim", "status": "OPEN", "points": 3},
            {"id": "i", "owner": "kim", "status": None, "points": 3},
            {"id": "i", "owner": "kim", "status": "open", "points": "3"},
            {"id": "i", "owner": "kim", "status": "open", "points": 3.0},
            {"id": "i", "owner": "kim", "status": "open", "points": True},
            {"id": "i", "owner": "kim", "status": "open", "points": -1},
            {"id": "i", "owner": "kim", "status": "open", "points": None},
            {"id": 7, "owner": "kim", "status": "open", "points": 3},
            {"id": "i", "owner": ["kim"], "status": "open", "points": 3},
            "item", 3, None, [],
        ]
        for record in bad:
            with self.subTest(record=record):
                self.assertIsNone(validate_item(record))


# ---------------------------------------------------------------------------
# reconciliation (pure)
# ---------------------------------------------------------------------------


class ReconcileTest(unittest.TestCase):
    def test_worked_example(self):
        accounts = [{"id": "a", "owner": "kim", "active": True}]
        items = [{"id": "1", "owner": "kim", "status": "open", "points": 3},
                 {"id": "2", "owner": "kim", "status": "open", "points": 4},
                 {"id": "3", "owner": "kim", "status": "done", "points": 2}]
        plan = reconcile(accounts, items)
        self.assertEqual(plan.summaries, {"kim": Summary("kim", 2, 1, 9)})
        self.assertEqual(plan.summaries["kim"].to_dict(),
                         {"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9})
        self.assertEqual(plan.orphans, {})
        self.assertEqual(plan.errors, {})

    def test_fixture_shape(self):
        plan = reconcile([r for page in ACCOUNTS for r in page],
                         [r for page in ITEMS for r in page])
        self.assertEqual({o: s.to_dict() for o, s in plan.summaries.items()}, EXPECTED_SUMMARIES)
        self.assertEqual(set(plan.orphans), EXPECTED_ORPHAN_IDS)
        self.assertEqual(sort_records(plan.errors.values()), sort_records(EXPECTED_ERRORS))

    def test_active_account_with_no_items_gets_zero_summary(self):
        plan = reconcile([{"id": "a", "owner": "solo", "active": True}], [])
        self.assertEqual(plan.summaries, {"solo": Summary("solo", 0, 0, 0)})

    def test_inactive_account_gets_no_summary_and_its_items_are_orphans(self):
        item = {"id": "1", "owner": "off", "status": "open", "points": 1}
        plan = reconcile([{"id": "a", "owner": "off", "active": False}], [item])
        self.assertEqual(plan.summaries, {})
        self.assertEqual(plan.orphans, {"1": item})

    def test_item_with_unknown_owner_is_an_orphan(self):
        item = {"id": "1", "owner": "nobody", "status": "done", "points": 1, "extra": "kept"}
        plan = reconcile([], [item])
        self.assertEqual(plan.orphans, {"1": item})
        self.assertIs(plan.orphans["1"], item)  # the record as received

    def test_malformed_account_is_not_an_account(self):
        item = {"id": "1", "owner": "fen", "status": "open", "points": 2}
        plan = reconcile([{"id": "a", "owner": "fen", "active": "yes"}], [item])
        self.assertEqual(plan.summaries, {})
        self.assertEqual(plan.orphans, {"1": item})
        self.assertEqual(list(plan.errors.values()),
                         [{"service": "ledger", "record": {"id": "a", "owner": "fen", "active": "yes"}}])

    def test_duplicate_items_count_once(self):
        item = {"id": "1", "owner": "kim", "status": "open", "points": 3}
        plan = reconcile([{"id": "a", "owner": "kim", "active": True}],
                         [item, dict(item), item])
        self.assertEqual(plan.summaries["kim"], Summary("kim", 1, 0, 3))

    def test_duplicate_orphans_written_once(self):
        item = {"id": "1", "owner": "ghost", "status": "open", "points": 3}
        plan = reconcile([], [item, dict(item)])
        self.assertEqual(len(plan.orphans), 1)

    def test_duplicate_malformed_records_written_once(self):
        bad_item = {"id": "x", "owner": "kim", "status": "open"}
        bad_account = {"id": "a", "owner": "kim", "active": "yes"}
        plan = reconcile([bad_account, dict(bad_account)], [bad_item, dict(bad_item), bad_item])
        self.assertEqual(sort_records(plan.errors.values()), sort_records([
            {"service": "ledger", "record": bad_account},
            {"service": "directory", "record": bad_item}]))

    def test_malformed_items_never_enter_counts(self):
        accounts = [{"id": "a", "owner": "kim", "active": True}]
        items = [{"id": "1", "owner": "kim", "status": "open", "points": 3},
                 {"id": "2", "owner": "kim", "status": "open", "points": "4"},
                 {"id": "3", "owner": "kim", "status": "done", "points": -2},
                 {"id": "4", "owner": "kim", "status": "started", "points": 1},
                 "garbage"]
        plan = reconcile(accounts, items)
        self.assertEqual(plan.summaries["kim"], Summary("kim", 1, 0, 3))
        self.assertEqual(len(plan.errors), 4)
        self.assertEqual(plan.orphans, {})

    def test_same_record_malformed_in_both_services_reported_per_service(self):
        record = {"id": "x"}
        plan = reconcile([record], [record])
        self.assertEqual(sort_records(plan.errors.values()), sort_records([
            {"service": "ledger", "record": record},
            {"service": "directory", "record": record}]))

    def test_owners_with_summaries_reads_owner_strings_only(self):
        self.assertEqual(owners_with_summaries([
            {"owner": "kim", "open_items": 1, "done_items": 0, "total_points": 2},
            {"owner": 5}, "junk", None, {"open_items": 1}]), {"kim"})


# ---------------------------------------------------------------------------
# Retry-After parsing and the Service layer (fake clock, no sockets)
# ---------------------------------------------------------------------------


class ParseRetryAfterTest(unittest.TestCase):
    def test_delta_seconds(self):
        self.assertEqual(parse_retry_after("3"), 3.0)
        self.assertEqual(parse_retry_after(" 2 "), 2.0)
        self.assertEqual(parse_retry_after("0.5"), 0.5)
        self.assertEqual(parse_retry_after("0"), 0.0)

    def test_http_date(self):
        now = 1_700_000_000.0
        future = email.utils.formatdate(now + 7, usegmt=True)
        self.assertAlmostEqual(parse_retry_after(future, now=now), 7.0, places=0)
        past = email.utils.formatdate(now - 7, usegmt=True)
        self.assertEqual(parse_retry_after(past, now=now), 0.0)

    def test_missing_or_garbage_falls_back_to_a_positive_wait(self):
        self.assertGreater(parse_retry_after(None), 0)
        self.assertGreater(parse_retry_after("soon"), 0)
        self.assertGreater(parse_retry_after(""), 0)


def make_headers(pairs=None):
    message = email.message.Message()
    for key, value in (pairs or {}).items():
        message[key] = value
    return message


class ScriptedClient:
    """Stands in for HttpClient: answers from a script, records requests."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def request(self, method, path, params=None, payload=None, headers=None):
        self.requests.append((method, path, dict(params or {}), payload, dict(headers or {})))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        status, body, headers = outcome
        return Response(status, make_headers(headers), json.dumps(body).encode("utf-8")
                        if body is not None else b"", path)


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def service_with(*outcomes):
    clock = FakeClock()
    client = ScriptedClient(*outcomes)
    service = Service("test", client, sleep=clock.sleep, clock=clock, log=lambda m: None)
    return service, client, clock


class ServiceRateLimitTest(unittest.TestCase):
    def test_429_waits_retry_after_then_retries_the_same_request(self):
        service, client, clock = service_with(
            (429, None, {"Retry-After": "3"}),
            (200, {"items": [1], "next_page": None}, {}))
        body = service.get_json("/v1/items", {"page": 1})
        self.assertEqual(body, {"items": [1], "next_page": None})
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(client.requests[0], client.requests[1])
        self.assertEqual(len(clock.sleeps), 1)
        self.assertGreaterEqual(clock.sleeps[0], 3.0)
        self.assertEqual(service.rate_limit_hits, 1)

    def test_repeated_429s_never_give_up(self):
        outcomes = [(429, None, {"Retry-After": "1"})] * 25 + [(200, {"ok": 1}, {})]
        service, client, clock = service_with(*outcomes)
        self.assertEqual(service.get_json("/x"), {"ok": 1})
        self.assertEqual(len(client.requests), 26)
        self.assertEqual(len(clock.sleeps), 25)
        self.assertTrue(all(s >= 1.0 for s in clock.sleeps))

    def test_wait_applies_to_the_next_request_of_any_kind(self):
        # A 429 on a GET must delay a following POST to the same service.
        service, client, clock = service_with(
            (429, None, {"Retry-After": "2"}),
            (200, {"summaries": [], "next_page": None}, {}),
            (201, {}, {}))
        service.get_json("/v1/summaries", {"page": 1})
        t_after_get = clock.now
        clock.sleeps.clear()
        service.post_json("/v1/summaries", {"owner": "kim"}, {"Idempotency-Key": "sync-kim"})
        # The wait was already consumed by the retried GET; no second wait needed.
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(client.requests[-1][0], "POST")
        self.assertGreaterEqual(clock.now, t_after_get)

    def test_429_on_post_is_retried_not_rejected(self):
        service, client, clock = service_with(
            (429, None, {"Retry-After": "1"}),
            (201, {"stored": True}, {}))
        response = service.post_json("/v1/summaries", {"owner": "kim"},
                                     {"Idempotency-Key": "sync-kim"})
        self.assertEqual(response.status, 201)
        self.assertEqual(len(client.requests), 2)
        self.assertEqual(client.requests[0], client.requests[1])
        self.assertEqual(client.requests[1][4], {"Idempotency-Key": "sync-kim"})
        self.assertGreaterEqual(clock.sleeps[0], 1.0)

    def test_429_without_retry_after_still_waits(self):
        service, client, clock = service_with((429, None, {}), (200, {"ok": 1}, {}))
        service.get_json("/x")
        self.assertEqual(len(clock.sleeps), 1)
        self.assertGreater(clock.sleeps[0], 0)

    def test_no_wait_when_the_window_has_already_elapsed(self):
        service, client, clock = service_with(
            (429, None, {"Retry-After": "1"}), (200, {"ok": 1}, {}), (200, {"ok": 2}, {}))
        service.get_json("/x")
        clock.sleeps.clear()
        clock.now += 100
        service.get_json("/y")
        self.assertEqual(clock.sleeps, [])

    def test_rejections_are_returned_not_retried(self):
        for status in (400, 409, 422):
            with self.subTest(status=status):
                service, client, clock = service_with((status, {"error": "no"}, {}))
                response = service.post_json("/v1/summaries", {"owner": "kim"})
                self.assertEqual(response.status, status)
                self.assertEqual(len(client.requests), 1)

    def test_5xx_is_retried_then_raised(self):
        service, client, clock = service_with((503, None, {}), (200, {"ok": 1}, {}))
        self.assertEqual(service.get_json("/x"), {"ok": 1})
        service, client, clock = service_with(*([(503, None, {})] * 10))
        with self.assertRaises(ServiceError):
            service.get_json("/x")


class ServicePaginationTest(unittest.TestCase):
    def test_follows_next_page_until_null(self):
        service, client, clock = service_with(
            (200, {"items": ["a", "b"], "next_page": 2}, {}),
            (429, None, {"Retry-After": "1"}),
            (200, {"items": ["c"], "next_page": 3}, {}),
            (200, {"items": [], "next_page": None}, {}))
        self.assertEqual(list(service.iter_pages("/v1/items", "items")), ["a", "b", "c"])
        self.assertEqual([r[2] for r in client.requests],
                         [{"page": 1}, {"page": 2}, {"page": 2}, {"page": 3}])

    def test_single_page(self):
        service, client, clock = service_with((200, {"accounts": [1], "next_page": None}, {}))
        self.assertEqual(list(service.iter_pages("/v1/accounts", "accounts")), [1])
        self.assertEqual(len(client.requests), 1)

    def test_does_not_loop_on_a_repeated_page_number(self):
        service, client, clock = service_with(
            (200, {"items": [1], "next_page": 2}, {}),
            (200, {"items": [2], "next_page": 1}, {}))
        self.assertEqual(list(service.iter_pages("/v1/items", "items")), [1, 2])

    def test_non_integer_next_page_ends_the_walk(self):
        for next_page in ("2", True, -1, 0, 2.5, {}, []):
            with self.subTest(next_page=next_page):
                service, client, clock = service_with((200, {"items": [1], "next_page": next_page}, {}))
                self.assertEqual(list(service.iter_pages("/v1/items", "items")), [1])

    def test_missing_list_yields_nothing_for_that_page(self):
        service, client, clock = service_with(
            (200, {"next_page": 2}, {}),
            (200, {"items": [7], "next_page": None}, {}))
        self.assertEqual(list(service.iter_pages("/v1/items", "items")), [7])

    def test_non_2xx_read_is_an_error(self):
        service, client, clock = service_with((404, {"error": "gone"}, {}))
        with self.assertRaises(ServiceError):
            list(service.iter_pages("/v1/items", "items"))


# ---------------------------------------------------------------------------
# output files
# ---------------------------------------------------------------------------


class OutputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_write_jsonl_one_line_per_record(self):
        path = os.path.join(self.tmp.name, "x.jsonl")
        write_jsonl(path, [{"a": 1}, {"b": [1, 2]}])
        self.assertEqual(read_bytes(path), b'{"a": 1}\n{"b": [1, 2]}\n')
        write_jsonl(path, [])
        self.assertEqual(read_bytes(path), b"")
        self.assertEqual([n for n in os.listdir(self.tmp.name) if n.startswith(".tmp")], [])

    def test_merge_summary_records_keeps_previous_posts_and_replaces_by_owner(self):
        previous = [{"owner": "kim", "open_items": 1, "done_items": 0, "total_points": 1},
                    {"owner": "bo", "open_items": 0, "done_items": 0, "total_points": 0},
                    "junk", {"owner": 3}]
        posted = [{"owner": "kim", "open_items": 2, "done_items": 0, "total_points": 5},
                  {"owner": "ann", "open_items": 0, "done_items": 1, "total_points": 1}]
        merged = merge_summary_records(previous, posted)
        self.assertEqual(merged, [
            {"owner": "ann", "open_items": 0, "done_items": 1, "total_points": 1},
            {"owner": "bo", "open_items": 0, "done_items": 0, "total_points": 0},
            {"owner": "kim", "open_items": 2, "done_items": 0, "total_points": 5}])
        self.assertEqual(merge_summary_records([], []), [])
        self.assertEqual(merge_summary_records(previous[:2], []), [previous[1], previous[0]])


# ---------------------------------------------------------------------------
# end to end against the in-process fake services
# ---------------------------------------------------------------------------


class EndToEndBase(unittest.TestCase):
    retry_after = 1
    rate_limits = {}
    summaries = ()
    accounts_pages = ACCOUNTS
    items_pages = ITEMS

    def setUp(self):
        self.services = FakeServices(
            items_pages=[list(p) for p in self.items_pages],
            accounts_pages=[list(p) for p in self.accounts_pages],
            summaries=list(self.summaries), rate_limits=dict(self.rate_limits),
            retry_after=self.retry_after).start()
        self.addCleanup(self.services.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = os.path.join(self.tmp.name, "out")

    def sync(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = run_sync(self.services.config(), self.out_dir)
        return code, err.getvalue()

    def outputs(self):
        return {name: read_bytes(os.path.join(self.out_dir, name))
                for name in (ORPHANS_FILE, ERRORS_FILE, SUMMARY_FILE)}

    def assert_outputs_match_fixture(self):
        orphans = read_jsonl(os.path.join(self.out_dir, ORPHANS_FILE))
        errors = read_jsonl(os.path.join(self.out_dir, ERRORS_FILE))
        all_items = {r["id"]: r for page in ITEMS for r in page if validate_item(r)}
        self.assertEqual(sort_records(orphans),
                         sort_records(all_items[i] for i in EXPECTED_ORPHAN_IDS))
        self.assertEqual(sort_records(errors), sort_records(EXPECTED_ERRORS))

    def assert_no_violations(self):
        self.assertEqual(self.services.violations, [],
                         "requests were sent before Retry-After elapsed")

    def assert_no_rejections(self):
        rejected = [e for e in self.services.posts() if e["status"] in (400, 409, 422)]
        self.assertEqual(rejected, [])


class EndToEndTest(EndToEndBase):
    def test_first_run_posts_every_missing_summary_and_writes_files(self):
        code, err = self.sync()
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isdir(self.out_dir))

        posts = self.services.posts()
        self.assertEqual({p["body"]["owner"]: p["body"] for p in posts}, EXPECTED_SUMMARIES)
        self.assertEqual({p["key"] for p in posts}, {"sync-" + o for o in EXPECTED_SUMMARIES})
        self.assertTrue(all(p["status"] == 201 for p in posts))
        self.assert_no_rejections()
        self.assert_no_violations()

        summary = read_jsonl(os.path.join(self.out_dir, SUMMARY_FILE))
        self.assertEqual({s["owner"]: s for s in summary}, EXPECTED_SUMMARIES)
        self.assertEqual(len(summary), len(EXPECTED_SUMMARIES))
        self.assert_outputs_match_fixture()

        # Every page of every collection was read exactly as far as next_page led.
        self.assertEqual([r["page"] for r in self.services.requests("directory", "GET")], [1, 2, 3])
        accounts = [r["page"] for r in self.services.requests("ledger", "GET")
                    if r["path"] == "/v1/accounts"]
        self.assertEqual(accounts, [1, 2, 3])
        summaries = [r["page"] for r in self.services.requests("ledger", "GET")
                     if r["path"] == "/v1/summaries"]
        self.assertEqual(summaries, [1])

    def test_second_run_is_idempotent(self):
        code, err = self.sync()
        self.assertEqual(code, 0, err)
        first = self.outputs()
        first_posts = len(self.services.posts())

        code, err = self.sync()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.outputs(), first)
        self.assertEqual(len(self.services.posts()), first_posts, "second run issued writes")
        self.assert_no_rejections()
        self.assert_no_violations()
        self.assertEqual({s["owner"] for s in self.services.summaries}, set(EXPECTED_SUMMARIES))

    def test_summary_lines_are_exactly_the_post_bodies(self):
        self.sync()
        lines = read_bytes(os.path.join(self.out_dir, SUMMARY_FILE)).decode("utf-8").splitlines()
        self.assertEqual(sort_records(json.loads(line) for line in lines),
                         sort_records(p["body"] for p in self.services.posts()))
        for line in lines:
            self.assertEqual(list(json.loads(line)),
                             ["owner", "open_items", "done_items", "total_points"])


class PreexistingSummariesTest(EndToEndBase):
    # The ledger already has amara (with different numbers, so a re-post would
    # 409) and a stale summary for someone no longer active.
    summaries = (
        {"owner": "amara", "open_items": 1, "done_items": 1, "total_points": 1},
        {"owner": "gus", "open_items": 9, "done_items": 9, "total_points": 9},
    )

    def test_posts_only_what_the_ledger_is_missing(self):
        code, err = self.sync()
        self.assertEqual(code, 0, err)
        posted = {p["body"]["owner"] for p in self.services.posts()}
        self.assertEqual(posted, {"bo", "dee", "eli"})
        self.assert_no_rejections()
        summary = read_jsonl(os.path.join(self.out_dir, SUMMARY_FILE))
        self.assertEqual({s["owner"] for s in summary}, {"bo", "dee", "eli"})
        self.assert_outputs_match_fixture()

        first = self.outputs()
        self.sync()
        self.assertEqual(self.outputs(), first)
        self.assertEqual(len(self.services.posts()), 3)


class PaginatedSummariesTest(EndToEndBase):
    summaries = tuple({"owner": o, "open_items": 0, "done_items": 0, "total_points": 0}
                      for o in ("amara", "bo", "dee"))

    def setUp(self):
        super().setUp()
        self.services.summaries_page_size = 1

    def test_reads_every_summary_page_before_deciding(self):
        code, err = self.sync()
        self.assertEqual(code, 0, err)
        pages = [r["page"] for r in self.services.requests("ledger", "GET")
                 if r["path"] == "/v1/summaries"]
        self.assertEqual(pages, [1, 2, 3])
        self.assertEqual({p["body"]["owner"] for p in self.services.posts()}, {"eli"})
        self.assert_no_rejections()


class RateLimitedEndToEndTest(EndToEndBase):
    retry_after = 1
    rate_limits = {
        ("directory", "GET", "/v1/items", 2): 1,
        ("ledger", "GET", "/v1/accounts", 2): 2,
        ("ledger", "GET", "/v1/summaries", 1): 1,
        ("ledger", "POST", "/v1/summaries", None): 2,
    }

    def test_waits_out_every_429_and_still_completes(self):
        started = time.monotonic()
        code, err = self.sync()
        elapsed = time.monotonic() - started
        self.assertEqual(code, 0, err)
        self.assert_no_violations()
        self.assert_no_rejections()
        hits = [e for e in self.services.log if e["status"] == 429]
        self.assertEqual(len(hits), 6)
        self.assertGreaterEqual(elapsed, 6 * self.retry_after)

        # Each rate-limited request was retried verbatim.
        posts = self.services.posts()
        self.assertEqual({p["body"]["owner"]: p["body"] for p in posts if p["status"] == 201},
                         EXPECTED_SUMMARIES)
        limited_posts = [p for p in posts if p["status"] == 429]
        self.assertEqual(len(limited_posts), 2)
        for limited in limited_posts:
            self.assertIn((limited["key"], limited["body"]),
                          [(p["key"], p["body"]) for p in posts if p["status"] == 201])

        self.assertEqual({s["owner"] for s in self.services.summaries}, set(EXPECTED_SUMMARIES))
        self.assert_outputs_match_fixture()
        summary = read_jsonl(os.path.join(self.out_dir, SUMMARY_FILE))
        self.assertEqual({s["owner"]: s for s in summary}, EXPECTED_SUMMARIES)


class EmptyServicesTest(EndToEndBase):
    accounts_pages = [[]]
    items_pages = [[]]

    def test_nothing_to_do_still_writes_empty_files(self):
        code, err = self.sync()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.outputs(), {ORPHANS_FILE: b"", ERRORS_FILE: b"", SUMMARY_FILE: b""})
        self.assertEqual(self.services.posts(), [])


class CliEndToEndTest(EndToEndBase):
    def test_python_m_orgsync_sync_twice(self):
        config_path = os.path.join(self.tmp.name, "config.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump({"directory_url": self.services.url("directory"),
                       "ledger_url": self.services.url("ledger")}, handle)
        argv = [sys.executable, "-m", "orgsync", "sync", "--config", config_path,
                "--out-dir", self.out_dir]
        first = subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        outputs = self.outputs()
        second = subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.outputs(), outputs)
        self.assertEqual(len(self.services.posts()), len(EXPECTED_SUMMARIES))
        self.assert_no_rejections()
        self.assert_no_violations()


# ---------------------------------------------------------------------------
# smoke test against the provisioned services, when they are reachable
# ---------------------------------------------------------------------------


def _reachable(url, path):
    """True if GET `url + path` answers at all (a 429 counts as answering)."""
    try:
        with urllib.request.urlopen(url + path + "?page=1", timeout=2) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        exc.close()
        return exc.code == 429
    except Exception:  # noqa: BLE001 - any other failure means "not reachable"
        return False


class LiveServicesSmokeTest(unittest.TestCase):
    """Runs the real command twice against the services in config.json.

    The ledger's state is whatever it is (earlier runs may have posted
    summaries already), so this checks the invariants that must hold
    regardless: exit 0, consistent files, summaries only for active owners,
    and a second run that changes nothing.
    """

    @classmethod
    def setUpClass(cls):
        path = os.path.join(REPO_ROOT, "config.json")
        if not os.path.exists(path):
            raise unittest.SkipTest("no config.json")
        cls.config = load_config(path)
        if not (_reachable(cls.config.directory_url, "/v1/items")
                and _reachable(cls.config.ledger_url, "/v1/accounts")):
            raise unittest.SkipTest("services in config.json are not reachable")

    def test_two_runs_against_live_services(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out_dir = os.path.join(tmp.name, "out")
        argv = [sys.executable, "-m", "orgsync", "sync", "--out-dir", out_dir]

        first = subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(first.returncode, 0, first.stderr)
        files = {n: read_bytes(os.path.join(out_dir, n))
                 for n in (ORPHANS_FILE, ERRORS_FILE, SUMMARY_FILE)}

        second = subprocess.run(argv, cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual({n: read_bytes(os.path.join(out_dir, n)) for n in files}, files)

        ledger = make_service("ledger", self.config.ledger_url, log=lambda m: None)
        directory = make_service("directory", self.config.directory_url, log=lambda m: None)
        accounts = list(ledger.iter_pages("/v1/accounts", "accounts"))
        items = list(directory.iter_pages("/v1/items", "items"))
        existing = list(ledger.iter_pages("/v1/summaries", "summaries"))
        plan = reconcile(accounts, items)

        # Every required summary is now in the ledger, exactly once.
        owners = [s["owner"] for s in existing if isinstance(s, dict)]
        self.assertEqual(len(owners), len(set(owners)))
        self.assertTrue(set(plan.summaries) <= set(owners))

        orphans = read_jsonl(os.path.join(out_dir, ORPHANS_FILE))
        self.assertEqual(sort_records(orphans), sort_records(plan.orphans.values()))
        errors = read_jsonl(os.path.join(out_dir, ERRORS_FILE))
        self.assertEqual(sort_records(errors), sort_records(plan.errors.values()))
        for line in read_jsonl(os.path.join(out_dir, SUMMARY_FILE)):
            self.assertEqual(line, plan.summaries[line["owner"]].to_dict())


if __name__ == "__main__":
    unittest.main()
