"""Tests for the `sync` behaviour of orgsync.

Three layers. The reconciliation rules are tested on plain data through
`reconcile`. The retry, rate-limit and pagination rules are tested through
`ServiceClient` and `fetch_all` with a fake clock and a scripted transport, so
they run without real waiting. The end-to-end tests start two in-process fake
services on 127.0.0.1 and run the real command against them, so no test ever
writes to the real Directory or Ledger.
"""

import contextlib
import email.message
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from collections import namedtuple

from orgsync import __main__ as cli
from orgsync.config import Config
from orgsync.http_client import HttpClientError, Response
from orgsync.models import Summary
from orgsync.service import (MAX_ATTEMPTS, ServiceClient, ServiceError,
                             fetch_all, parse_retry_after)
from orgsync.sync import ERRORS_FILE, ORPHANS_FILE, SUMMARY_FILE, reconcile, run_sync
from orgsync.validate import (is_well_formed_account, is_well_formed_item,
                              is_well_formed_summary)


def headers(**pairs):
    message = email.message.Message()
    for key, value in pairs.items():
        message[key.replace("_", "-")] = value
    return message


def reply(status, body=None, **header_pairs):
    data = b"" if body is None else json.dumps(body).encode("utf-8")
    return Response(status, headers(**header_pairs), data, "http://fake")


def read_jsonl(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def run_quietly(argv_or_call, *args):
    """Run `main` or a callable with stdout captured; return (result, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        result = argv_or_call(*args)
    return result, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------- validation


class WellFormedTest(unittest.TestCase):
    def test_items(self):
        self.assertTrue(is_well_formed_item({"id": "i", "owner": "o", "status": "open", "points": 0}))
        self.assertTrue(is_well_formed_item({"id": "i", "owner": "o", "status": "done",
                                             "points": 3, "extra": "ignored"}))
        for bad in ({"id": "i", "owner": "o", "status": "open"},
                    {"id": 1, "owner": "o", "status": "open", "points": 1},
                    {"id": "i", "owner": "o", "status": "blocked", "points": 1},
                    {"id": "i", "owner": "o", "status": "open", "points": -1},
                    {"id": "i", "owner": "o", "status": "open", "points": "3"},
                    {"id": "i", "owner": "o", "status": "open", "points": True},
                    {"id": "i", "owner": "o", "status": "open", "points": 2.0},
                    ["i", "o", "open", 1], "text", None):
            with self.subTest(record=bad):
                self.assertFalse(is_well_formed_item(bad))

    def test_accounts(self):
        self.assertTrue(is_well_formed_account({"id": "a", "owner": "o", "active": False}))
        for bad in ({"id": "a", "owner": "o"},
                    {"id": "a", "owner": "o", "active": "true"},
                    {"id": "a", "owner": "o", "active": 1},
                    {"id": "a", "active": True}):
            with self.subTest(record=bad):
                self.assertFalse(is_well_formed_account(bad))

    def test_summaries(self):
        self.assertTrue(is_well_formed_summary({"owner": "o", "open_items": 0,
                                                "done_items": 0, "total_points": 0}))
        self.assertFalse(is_well_formed_summary({"owner": "o", "open_items": -1,
                                                 "done_items": 0, "total_points": 0}))


# ---------------------------------------------------------------- reconcile


def item(item_id, owner, status, points):
    return {"id": item_id, "owner": owner, "status": status, "points": points}


def account(owner, active=True, account_id=None):
    return {"id": account_id or "acct-" + owner, "owner": owner, "active": active}


class ReconcileTest(unittest.TestCase):
    def test_worked_example(self):
        plan = reconcile([account("kim")],
                         [item("1", "kim", "open", 3), item("2", "kim", "open", 4),
                          item("3", "kim", "done", 2)], [])
        self.assertEqual(plan.required, [Summary("kim", 2, 1, 9)])
        self.assertEqual(plan.missing, [Summary("kim", 2, 1, 9)])
        self.assertEqual(plan.orphans, [])
        self.assertEqual(plan.errors, [])

    def test_active_account_without_items_gets_zeros_and_inactive_gets_nothing(self):
        plan = reconcile([account("kim"), account("lee", active=False)], [], [])
        self.assertEqual(plan.required, [Summary("kim", 0, 0, 0)])

    def test_duplicate_delivery_is_counted_once_and_orphan_once(self):
        records = [item("1", "kim", "open", 3), item("1", "kim", "open", 3),
                   item("9", "nobody", "done", 1), item("9", "nobody", "done", 1)]
        plan = reconcile([account("kim")], records, [])
        self.assertEqual(plan.required, [Summary("kim", 1, 0, 3)])
        self.assertEqual(plan.orphans, [item("9", "nobody", "done", 1)])

    def test_orphans_include_items_of_inactive_accounts_and_keep_extra_fields(self):
        extra = dict(item("5", "lee", "open", 2), title="kept as received")
        plan = reconcile([account("lee", active=False)], [extra], [])
        self.assertEqual(plan.orphans, [extra])
        self.assertEqual(plan.required, [])

    def test_malformed_account_does_not_make_its_owner_active(self):
        plan = reconcile([{"id": "a", "owner": "kim", "active": "true"}],
                         [item("1", "kim", "open", 3)], [])
        self.assertEqual(plan.required, [])
        self.assertEqual(plan.orphans, [item("1", "kim", "open", 3)])
        self.assertEqual(plan.errors, [{"service": "ledger",
                                        "record": {"id": "a", "owner": "kim", "active": "true"}}])

    def test_malformed_items_are_errors_and_never_counted(self):
        bad = {"id": "2", "owner": "kim", "status": "open", "points": "3"}
        plan = reconcile([account("kim")], [item("1", "kim", "open", 1), bad], [])
        self.assertEqual(plan.required, [Summary("kim", 1, 0, 1)])
        self.assertEqual(plan.errors, [{"service": "directory", "record": bad}])

    def test_identical_malformed_records_are_reported_once(self):
        bad = "not an object"
        plan = reconcile([], [bad, bad], [])
        self.assertEqual(plan.errors, [{"service": "directory", "record": bad}])

    def test_existing_summary_suppresses_a_post_even_if_stale_or_malformed(self):
        plan = reconcile([account("kim"), account("lee")], [], [
            {"owner": "kim", "open_items": 9, "done_items": 9, "total_points": 9},
            {"owner": "lee", "open_items": "many"},
        ])
        self.assertEqual(plan.missing, [])
        self.assertEqual([e["record"] for e in plan.errors], [{"owner": "lee", "open_items": "many"}])


# ----------------------------------------------------- client, retries, pages


class FakeClock:
    """Time that only moves when something sleeps."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ScriptedHttp:
    """Stands in for HttpClient: returns queued responses or raises queued errors."""

    def __init__(self, clock, *outcomes):
        self.clock = clock
        self.outcomes = list(outcomes)
        self.calls = []  # (time, method, path, params)

    def request(self, method, path, params=None, payload=None, headers=None):
        self.calls.append((self.clock.now, method, path, params))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class ParseRetryAfterTest(unittest.TestCase):
    def test_values(self):
        self.assertEqual(parse_retry_after("2"), 2.0)
        self.assertEqual(parse_retry_after(" 0.5 "), 0.5)
        self.assertEqual(parse_retry_after("0"), 0.0)
        for unusable in (None, "soon", "-3", "inf", "nan"):
            with self.subTest(value=unusable):
                self.assertEqual(parse_retry_after(unusable), 1.0)


class ServiceClientTest(unittest.TestCase):
    def setUp(self):
        self.time = FakeClock()

    def client(self, *outcomes):
        http = ScriptedHttp(self.time, *outcomes)
        return ServiceClient("svc", http, clock=self.time.clock, sleep=self.time.sleep), http

    def test_429_waits_at_least_retry_after_before_the_same_request_is_retried(self):
        client, http = self.client(reply(429, {"error": "slow"}, Retry_After="3"),
                                   reply(200, {"ok": 1}))
        response = client.get("/v1/things", {"page": 1})
        self.assertEqual(response.status, 200)
        (first_time, _, _, first_params), (second_time, _, _, second_params) = http.calls
        self.assertGreaterEqual(second_time - first_time, 3.0)
        self.assertEqual(first_params, second_params)

    def test_429_without_retry_after_still_waits_and_retries(self):
        client, http = self.client(reply(429), reply(200))
        self.assertEqual(client.get("/v1/things").status, 200)
        self.assertGreaterEqual(http.calls[1][0] - http.calls[0][0], 1.0)

    def test_429_is_never_an_answer_however_many_times_it_repeats(self):
        outcomes = [reply(429, Retry_After="1")] * 10 + [reply(200, {"ok": 1})]
        client, http = self.client(*outcomes)
        self.assertEqual(client.get("/v1/things").status, 200)
        self.assertEqual(len(http.calls), 11)

    def test_transport_failure_is_retried_then_becomes_a_service_error(self):
        client, http = self.client(*[HttpClientError("refused")] * MAX_ATTEMPTS)
        with self.assertRaises(ServiceError):
            client.get("/v1/things")
        self.assertEqual(len(http.calls), MAX_ATTEMPTS)

    def test_5xx_is_retried(self):
        client, http = self.client(reply(503), reply(200, {"ok": 1}))
        self.assertEqual(client.get("/v1/things").status, 200)
        self.assertEqual(len(http.calls), 2)

    def test_4xx_is_returned_to_the_caller(self):
        client, http = self.client(reply(422, {"error": "no"}))
        self.assertEqual(client.request("POST", "/v1/summaries", payload={}).status, 422)
        self.assertEqual(len(http.calls), 1)


class FetchAllTest(unittest.TestCase):
    def setUp(self):
        self.time = FakeClock()

    def test_follows_next_page_until_null(self):
        http = ScriptedHttp(self.time,
                            reply(200, {"items": [1, 2], "next_page": 2}),
                            reply(200, {"items": [3], "next_page": 3}),
                            reply(200, {"items": [], "next_page": None}))
        client = ServiceClient("svc", http, clock=self.time.clock, sleep=self.time.sleep)
        self.assertEqual(fetch_all(client, "/v1/items", "items"), [1, 2, 3])
        self.assertEqual([c[3] for c in http.calls], [{"page": 1}, {"page": 2}, {"page": 3}])

    def test_unusable_page_is_a_service_error(self):
        for bad in (reply(200, {"nope": []}), reply(200, {"items": "x", "next_page": None}),
                    reply(404), reply(200, {"items": [], "next_page": "2"})):
            with self.subTest(response=bad):
                http = ScriptedHttp(self.time, bad)
                client = ServiceClient("svc", http, clock=self.time.clock, sleep=self.time.sleep)
                with self.assertRaises(ServiceError):
                    fetch_all(client, "/v1/items", "items")


# ------------------------------------------------------- fake services, e2e


Logged = namedtuple("Logged", "at method path idempotency_key status")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._serve("GET")

    def do_POST(self):
        self._serve("POST")

    def _serve(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        status, payload, extra = self.server.fake.route(method, self.path, self.headers, body)
        data = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        for key, value in extra.items():
            self.send_header(key, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        pass


class FakeService:
    """An HTTP server on 127.0.0.1 answering from `_respond`, logging every request.

    `throttle` holds path prefixes; the first request matching each gets a 429
    with `Retry-After: 1` and is then answered normally.
    """

    PAGE_SIZE = 2

    def __init__(self):
        self.log = []
        self.throttle = []
        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self._server.fake = self
        self.url = "http://127.0.0.1:%d" % self._server.server_port
        thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        thread.start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()

    def route(self, method, raw_path, request_headers, body):
        at = time.monotonic()
        path, _, query = raw_path.partition("?")
        page = int(urllib.parse.parse_qs(query).get("page", ["1"])[0])
        idem = request_headers.get("Idempotency-Key")
        for prefix in self.throttle:
            if path.startswith(prefix):
                self.throttle.remove(prefix)
                self.log.append(Logged(at, method, path, idem, 429))
                return 429, {"error": "slow down"}, {"Retry-After": "1"}
        status, payload = self._respond(method, path, page, idem, body)
        self.log.append(Logged(at, method, path, idem, status))
        return status, payload, {}

    def paginate(self, records, page, key):
        start = (page - 1) * self.PAGE_SIZE
        chunk = records[start:start + self.PAGE_SIZE]
        more = start + self.PAGE_SIZE < len(records)
        return 200, {key: chunk, "next_page": page + 1 if more else None}

    def writes(self):
        return [entry for entry in self.log if entry.method != "GET"]


class FakeDirectory(FakeService):
    def __init__(self, items):
        self.items = items
        super().__init__()

    def _respond(self, method, path, page, idem, body):
        if method == "GET" and path == "/v1/items":
            return self.paginate(self.items, page, "items")
        return 404, None


class FakeLedger(FakeService):
    def __init__(self, accounts, summaries):
        self.accounts = accounts
        self.summaries = list(summaries)
        self.accepted = {}  # Idempotency-Key -> body
        super().__init__()

    def _active_owners(self):
        return {a["owner"] for a in self.accounts
                if isinstance(a, dict) and a.get("active") is True}

    def _respond(self, method, path, page, idem, body):
        if method == "GET" and path == "/v1/accounts":
            return self.paginate(self.accounts, page, "accounts")
        if method == "GET" and path == "/v1/summaries":
            return self.paginate(self.summaries, page, "summaries")
        if method == "POST" and path == "/v1/summaries":
            return self._post(idem, body)
        return 404, None

    def _post(self, idem, raw):
        try:
            payload = json.loads(raw)
        except ValueError:
            return 400, {"error": "bad json"}
        if not isinstance(payload, dict) or payload.get("owner") not in self._active_owners():
            return 422, {"error": "owner has no active account"}
        if idem in self.accepted:
            return (200, payload) if self.accepted[idem] == payload else (409, {"error": "conflict"})
        self.accepted[idem] = payload
        self.summaries.append(payload)
        return 201, payload


DIRECTORY_ITEMS = [
    item("item-101", "amara", "open", 3),
    item("item-102", "amara", "done", 2),
    item("item-101", "amara", "open", 3),            # same item delivered twice
    item("item-103", "bo", "open", 5),
    item("item-104", "chen", "open", 8),             # chen is inactive: orphan
    item("item-105", "dana", "done", 1),             # no account: orphan
    dict(item("item-106", "erin", "open", 4), title="kept as received"),  # erin malformed: orphan
    {"id": "item-107", "owner": "amara", "status": "open", "points": "3"},
    {"id": "item-108", "owner": "bo", "status": "open", "points": -1},
    {"id": "item-109", "owner": "bo", "status": "blocked", "points": 1},
    {"id": "item-110", "owner": "bo", "status": "open"},
    {"id": "item-111", "owner": "amara", "status": "open", "points": True},
    {"id": "item-112", "owner": "amara", "status": "open", "points": 2.0},
    item("item-113", "bo", "open", 1),
    "not an object",
    "not an object",
]

LEDGER_ACCOUNTS = [
    account("amara", account_id="acct-01"),
    account("bo", account_id="acct-02"),
    account("chen", active=False, account_id="acct-03"),
    account("frank", account_id="acct-04"),          # active, no items
    {"id": "acct-05", "owner": "erin", "active": "yes"},
    {"id": "acct-06", "owner": "gus"},
]

LEDGER_SUMMARIES = [
    {"owner": "bo", "open_items": 0, "done_items": 0, "total_points": 0},  # stale, already present
]


class SyncEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.directory = FakeDirectory(list(DIRECTORY_ITEMS))
        self.ledger = FakeLedger(list(LEDGER_ACCOUNTS), list(LEDGER_SUMMARIES))
        for fake in (self.directory, self.ledger):
            self.addCleanup(fake.close)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.out_dir = os.path.join(tmp.name, "out")
        self.config = Config(directory_url=self.directory.url, ledger_url=self.ledger.url)

    def sync(self):
        return run_quietly(run_sync, self.config, self.out_dir)

    def out(self, name):
        return os.path.join(self.out_dir, name)

    def test_first_run_posts_only_missing_summaries_and_writes_outputs(self):
        code, _, err = self.sync()
        self.assertEqual(code, 0, err)
        posts =[entry for entry in self.ledger.log if entry.method == "POST"]
        self.assertEqual([entry.status for entry in posts], [201, 201])
        self.assertEqual(sorted(entry.idempotency_key for entry in posts),
                         ["sync-amara", "sync-frank"])
        self.assertEqual(
            sorted(read_jsonl(self.out(SUMMARY_FILE)), key=lambda s: s["owner"]),
            [{"owner": "amara", "open_items": 1, "done_items": 1, "total_points": 5},
             {"owner": "frank", "open_items": 0, "done_items": 0, "total_points": 0}])

    def test_orphans_and_errors_are_written_once_each(self):
        self.sync()
        orphans = read_jsonl(self.out(ORPHANS_FILE))
        self.assertEqual(sorted(o["id"] for o in orphans), ["item-104", "item-105", "item-106"])
        erin = next(o for o in orphans if o["id"] == "item-106")
        self.assertEqual(erin["title"], "kept as received")

        errors = read_jsonl(self.out(ERRORS_FILE))
        directory_errors = [e["record"] for e in errors if e["service"] == "directory"]
        ledger_errors = [e["record"] for e in errors if e["service"] == "ledger"]
        self.assertEqual(len(directory_errors), 7)  # six malformed items + one repeated string
        self.assertEqual(directory_errors.count("not an object"), 1)
        self.assertEqual(len(ledger_errors), 2)
        self.assertEqual(len(errors), 9)

    def test_second_run_issues_no_writes_and_leaves_outputs_identical(self):
        self.sync()
        names = (ORPHANS_FILE, ERRORS_FILE, SUMMARY_FILE)
        first = {name: read_bytes(self.out(name)) for name in names}
        writes_before = len(self.ledger.writes())

        code, _, err = self.sync()

        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.ledger.writes()), writes_before)
        self.assertEqual(len(self.directory.writes()), 0)
        for name in names:
            self.assertEqual(read_bytes(self.out(name)), first[name], name)

    def test_no_rejected_writes_are_sent(self):
        self.sync()
        self.sync()
        self.assertNotIn(422, [e.status for e in self.ledger.log if e.method == "POST"])
        self.assertNotIn(409, [e.status for e in self.ledger.log if e.method == "POST"])

    def test_429_from_either_service_is_waited_out_and_never_abandons_the_run(self):
        self.directory.throttle = ["/v1/items"]
        self.ledger.throttle = ["/v1/summaries"]
        code, _, err = self.sync()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(read_jsonl(self.out(SUMMARY_FILE))), 2)

        for fake in (self.directory, self.ledger):
            limited = [e for e in fake.log if e.status == 429]
            self.assertEqual(len(limited), 1)
            after = [e for e in fake.log if e.at > limited[0].at]
            self.assertTrue(after, "no request followed the 429")
            self.assertGreaterEqual(after[0].at - limited[0].at, 1.0)

    def test_stale_ledger_summary_is_left_alone(self):
        self.sync()
        self.assertFalse(any(e.idempotency_key == "sync-bo" for e in self.ledger.log))

    def test_command_line_sync_returns_zero(self):
        config_path = os.path.join(os.path.dirname(self.out_dir), "config.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump({"directory_url": self.directory.url,
                       "ledger_url": self.ledger.url}, handle)
        code, _, _ = run_quietly(cli.main, ["sync", "--config", config_path,
                                            "--out-dir", self.out_dir])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.out(SUMMARY_FILE)))


if __name__ == "__main__":
    unittest.main()
