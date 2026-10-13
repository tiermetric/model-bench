"""End-to-end and unit tests for the orgsync `sync` behavior.

The integration tests stand up two local fake services (a Directory and a
Ledger) that implement the contract from the brief — paginated list endpoints,
idempotent summary posts, and optional 429 rate limiting — point a temporary
config.json at them, and run `python3 -m orgsync sync` twice to prove the
idempotency rule. Nothing here touches the shared fixture services except the
final smoke-test class, which performs read-only GETs and skips itself when
the services are not running.
"""

from __future__ import annotations

import contextlib
import email.message
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen

from orgsync.__main__ import main
from orgsync.http_client import Response
from orgsync.sync import (
    RateLimiter,
    RateLimitedService,
    is_well_formed_account,
    is_well_formed_item,
    retry_after_seconds,
)


def run_main(argv):
    """Run `main(argv)`, returning (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


def make_headers(pairs=None):
    message = email.message.Message()
    for key, value in (pairs or {}).items():
        message[key] = value
    return message


def make_response(status, body=b"", headers=None):
    return Response(status, make_headers(headers), body, "http://fake.test")


def read_jsonl(path):
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def read_text(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# Validators.
# ---------------------------------------------------------------------------


class ItemValidationTest(unittest.TestCase):
    def test_well_formed_item_passes(self):
        self.assertTrue(is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "open", "points": 3}))
        self.assertTrue(is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "done", "points": 0,
             "extra": "ignored"}))

    def test_missing_field_is_malformed(self):
        for record in ({"owner": "kim", "status": "open", "points": 1},
                       {"id": "i-1", "status": "open", "points": 1},
                       {"id": "i-1", "owner": "kim", "points": 1},
                       {"id": "i-1", "owner": "kim", "status": "open"}):
            with self.subTest(record=record):
                self.assertFalse(is_well_formed_item(record))

    def test_wrong_json_type_is_malformed(self):
        for record in ({"id": 7, "owner": "kim", "status": "open", "points": 1},
                       {"id": "i-1", "owner": 7, "status": "open", "points": 1},
                       {"id": "i-1", "owner": "kim", "status": "OPEN", "points": 1},
                       {"id": "i-1", "owner": "kim", "status": True, "points": 1},
                       {"id": "i-1", "owner": "kim", "status": "open", "points": "3"},
                       {"id": "i-1", "owner": "kim", "status": "open", "points": 3.0},
                       {"id": "i-1", "owner": "kim", "status": "open", "points": True},
                       {"id": "i-1", "owner": "kim", "status": "open", "points": None},
                       ["not", "a", "dict"],
                       "item",
                       42,
                       None):
            with self.subTest(record=record):
                self.assertFalse(is_well_formed_item(record))

    def test_out_of_range_points_is_malformed(self):
        self.assertFalse(is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "open", "points": -1}))


class AccountValidationTest(unittest.TestCase):
    def test_well_formed_account_passes(self):
        self.assertTrue(is_well_formed_account(
            {"id": "a-1", "owner": "kim", "active": True}))
        self.assertTrue(is_well_formed_account(
            {"id": "a-1", "owner": "kim", "active": False, "tier": "gold"}))

    def test_wrong_json_type_is_malformed(self):
        for record in ({"id": "a-1", "owner": "kim", "active": 1},
                       {"id": "a-1", "owner": "kim", "active": 0},
                       {"id": "a-1", "owner": "kim", "active": "true"},
                       {"id": "a-1", "owner": "kim", "active": None},
                       {"id": "a-1", "owner": "kim"},
                       {"id": None, "owner": "kim", "active": True},
                       {"id": "a-1", "owner": 9, "active": True},
                       {"active": True}):
            with self.subTest(record=record):
                self.assertFalse(is_well_formed_account(record))


# ---------------------------------------------------------------------------
# Rate limiting mechanics (rule 6), with fake clocks and transports.
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


class FakeSleeper:
    def __init__(self, clock):
        self.clock = clock
        self.naps = []

    def __call__(self, seconds):
        self.naps.append(seconds)
        self.clock.now += seconds


class RetryAfterHeaderTest(unittest.TestCase):
    def test_parses_seconds_and_ignores_garbage(self):
        self.assertEqual(retry_after_seconds(make_headers({"Retry-After": "3"})), 3.0)
        self.assertEqual(retry_after_seconds(make_headers({"Retry-After": "0"})), 0.0)
        self.assertEqual(retry_after_seconds(make_headers({"Retry-After": " 2.5 "})), 2.5)
        self.assertEqual(retry_after_seconds(make_headers({"retry-after": "7"})), 7.0)

    def test_missing_or_unreadable_header_falls_back_to_default(self):
        self.assertEqual(retry_after_seconds(make_headers()), 1.0)
        self.assertEqual(retry_after_seconds(make_headers({"Retry-After": "soon"})), 1.0)
        self.assertEqual(retry_after_seconds(None), 1.0)

    def test_negative_value_is_clamped_to_zero(self):
        self.assertEqual(retry_after_seconds(make_headers({"Retry-After": "-5"})), 0.0)


class RateLimiterTest(unittest.TestCase):
    def test_wait_sleeps_until_deferred_moment(self):
        clock = FakeClock()
        sleeper = FakeSleeper(clock)
        limiter = RateLimiter(clock=clock, sleep=sleeper)
        limiter.defer(5)
        limiter.wait()
        self.assertAlmostEqual(sleeper.naps[0], 5.01, places=2)

    def test_no_sleep_once_the_wait_has_passed(self):
        clock = FakeClock()
        sleeper = FakeSleeper(clock)
        limiter = RateLimiter(clock=clock, sleep=sleeper)
        limiter.defer(1)
        clock.now += 2  # the silence has already expired
        limiter.wait()
        self.assertEqual(sleeper.naps, [])

    def test_repeated_defers_extend_not_shrink_the_silence(self):
        clock = FakeClock()
        sleeper = FakeSleeper(clock)
        limiter = RateLimiter(clock=clock, sleep=sleeper)
        limiter.defer(10)
        clock.now += 5
        limiter.defer(2)  # shorter than what remains: no effect
        limiter.wait()
        self.assertAlmostEqual(sleeper.naps[0], 5.01, places=2)


class ScriptedClient:
    """Stands in for HttpClient, replaying queued responses and recording calls."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def _next(self, call):
        self.calls.append(call)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def get(self, path, params=None):
        return self._next(("GET", path, params))

    def post_json(self, path, payload, headers=None):
        return self._next(("POST", path, payload, headers))


class RateLimitedServiceTest(unittest.TestCase):
    def test_429_is_retried_after_the_header_says_so(self):
        client = ScriptedClient(
            make_response(429, headers={"Retry-After": "5"}),
            make_response(200, b"{}"))
        clock, sleeper = FakeClock(), FakeSleeper(FakeClock())
        sleeper.clock = clock
        service = RateLimitedService(client, RateLimiter(clock=clock, sleep=sleeper))

        response = service.get("/v1/items", {"page": 1})

        self.assertEqual(response.status, 200)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0], client.calls[1])  # identical retry
        self.assertAlmostEqual(sleeper.naps[0], 5.01, places=2)

    def test_429_on_post_is_retried_with_the_same_body_and_headers(self):
        first = make_response(429, headers={"Retry-After": "0"})
        second = make_response(201, b"{}")
        client = ScriptedClient(first, second)
        service = RateLimitedService(client, RateLimiter())

        response = service.post_json("/v1/summaries", {"owner": "kim"},
                                     {"Idempotency-Key": "sync-kim"})

        self.assertEqual(response.status, 201)
        self.assertEqual(client.calls[0], client.calls[1])

    def test_one_service_429_does_not_silence_another(self):
        directory_clock = FakeClock()
        directory_sleeper = FakeSleeper(directory_clock)
        ledger_clock = FakeClock()
        ledger_sleeper = FakeSleeper(ledger_clock)
        blocked = ScriptedClient(
            make_response(429, headers={"Retry-After": "60"}),
            make_response(200, b"{}"))
        directory = RateLimitedService(
            blocked, RateLimiter(clock=directory_clock, sleep=directory_sleeper))
        free = ScriptedClient(make_response(200, b"{}"))
        ledger = RateLimitedService(
            free, RateLimiter(clock=ledger_clock, sleep=ledger_sleeper))

        # The directory 429s once, waits out its own silence, and retries.
        self.assertEqual(directory.get("/v1/items").status, 200)
        self.assertEqual(len(blocked.calls), 2)
        self.assertAlmostEqual(directory_sleeper.naps[0], 60.01, places=2)

        # The ledger was never 429'd, so it never waited on anything.
        self.assertEqual(ledger.get("/v1/accounts").status, 200)
        self.assertEqual(ledger_sleeper.naps, [])

    def test_repeated_429s_keep_deferring(self):
        client = ScriptedClient(
            make_response(429, headers={"Retry-After": "1"}),
            make_response(429, headers={"Retry-After": "1"}),
            make_response(200, b"{}"))
        clock = FakeClock()
        sleeper = FakeSleeper(clock)
        service = RateLimitedService(client, RateLimiter(clock=clock, sleep=sleeper))

        self.assertEqual(service.get("/v1/items").status, 200)
        self.assertEqual(len(client.calls), 3)
        self.assertGreaterEqual(clock.now, 1000.0 + 2.0)


# ---------------------------------------------------------------------------
# Fake services implementing the contract, for end-to-end tests.
# ---------------------------------------------------------------------------


class FakeState:
    """Shared mutable state for one fake Directory or Ledger."""

    def __init__(self, kind, pages_by_endpoint, active_owners=None):
        self.kind = kind
        self.pages_by_endpoint = pages_by_endpoint  # path -> list of record lists
        self.active_owners = set(active_owners or ())
        self.stored_summaries = []  # accepted POST bodies, in order
        self.requests = []  # (kind, method, path, idempotency_key, status)
        self.pending_429 = 0  # the next N requests get a 429
        self.pending_post_429 = 0  # the next N POSTs get a 429
        self.retry_after = "0"
        self.lock = threading.Lock()

    def _listing_for(self, path):
        """The flat record list a GET should serve right now.

        The summaries listing is live: preloaded summaries plus everything
        accepted so far, like the real ledger. Items and accounts are static.
        The handler re-chunks any listing into pages of three records.
        """
        if path == "/v1/summaries":
            preloaded = [record
                         for page in self.pages_by_endpoint.get("/v1/summaries", [])
                         for record in page]
            return preloaded + list(self.stored_summaries)
        return [record for page in self.pages_by_endpoint.get(path, [])
                for record in page]

    def record(self, method, path, idempotency_key, status):
        with self.lock:
            self.requests.append((self.kind, method, path, idempotency_key, status))

    def posts(self):
        return [entry for entry in self.requests if entry[1] == "POST"]

    def rejected_posts(self):
        """POSTs the ledger answered 400/409/422 — a correct run makes none."""
        return [entry for entry in self.posts() if entry[4] in (400, 409, 422)]


class FakeHandler(BaseHTTPRequestHandler):
    """Serves the orgsync contract for whichever fake service hosts it."""

    def log_message(self, *args):
        pass

    @property
    def state(self):
        return self.server.state

    def _send_json(self, status, payload, extra_headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _rate_limited(self):
        if self.state.pending_429 > 0:
            self.state.pending_429 -= 1
            return True
        return False

    def _post_rate_limited(self):
        if self.state.pending_post_429 > 0:
            self.state.pending_post_429 -= 1
            return True
        return False

    def _too_many_requests(self, method, idempotency_key):
        self.state.record(method, self.path, idempotency_key, 429)
        self._send_json(429, {"error": "rate_limited"},
                        {"Retry-After": self.state.retry_after})

    def do_GET(self):
        if self._rate_limited():
            self._too_many_requests("GET", None)
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        listing = self.state._listing_for(parsed.path)
        if listing is None or "page" not in query:
            self.state.record("GET", self.path, None, 404)
            self._send_json(404, {"error": "not_found"})
            return
        try:
            page_number = int(query["page"][0])
        except ValueError:
            self.state.record("GET", self.path, None, 404)
            self._send_json(404, {"error": "not_found"})
            return
        pages = [listing[i:i + 3] for i in range(0, len(listing), 3)] or [[]]
        if page_number < 1 or page_number > len(pages):
            self.state.record("GET", self.path, None, 404)
            self._send_json(404, {"error": "not_found"})
            return
        key = {"/v1/items": "items", "/v1/accounts": "accounts",
               "/v1/summaries": "summaries"}[parsed.path]
        next_page = page_number + 1 if page_number < len(pages) else None
        self.state.record("GET", self.path, None, 200)
        self._send_json(200, {key: pages[page_number - 1], "next_page": next_page})

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        idempotency_key = self.headers.get("Idempotency-Key")
        if parsed.path != "/v1/summaries":
            self.state.record("POST", self.path, idempotency_key, 404)
            self._send_json(404, {"error": "not_found"})
            return
        if self._rate_limited() or self._post_rate_limited():
            self._too_many_requests("POST", idempotency_key)
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError:
            self.state.record("POST", self.path, idempotency_key, 400)
            self._send_json(400, {"error": "bad_json"})
            return
        existing = self.server.summaries_by_key.get(idempotency_key)
        if existing is not None:
            status = 200 if existing == payload else 409
        elif payload.get("owner") not in self.state.active_owners:
            status = 422
        else:
            self.server.summaries_by_key[idempotency_key] = payload
            self.state.stored_summaries.append(payload)
            status = 201
        self.state.record("POST", self.path, idempotency_key, status)
        self._send_json(status, payload)


class FakeLedgerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state):
        self.state = state
        self.summaries_by_key = {}
        super().__init__(("127.0.0.1", 0), FakeHandler)


class FakeDirectoryServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state):
        self.state = state
        self.summaries_by_key = {}
        super().__init__(("127.0.0.1", 0), FakeHandler)


class FakeServices:
    """A Directory and a Ledger wired to shared fakes, plus their config file."""

    def __init__(self, item_pages, account_pages, summary_pages=None,
                 active_owners=None):
        self.directory_state = FakeState("directory", {"/v1/items": item_pages})
        self.ledger_state = FakeState(
            "ledger",
            {"/v1/accounts": account_pages,
             "/v1/summaries": summary_pages if summary_pages is not None else [[]]},
            active_owners=active_owners)
        self.directory = FakeDirectoryServer(self.directory_state)
        self.ledger = FakeLedgerServer(self.ledger_state)
        self.threads = [
            threading.Thread(target=self.directory.serve_forever, daemon=True),
            threading.Thread(target=self.ledger.serve_forever, daemon=True),
        ]
        for thread in self.threads:
            thread.start()
        self.tmp = tempfile.TemporaryDirectory()
        config_path = os.path.join(self.tmp.name, "config.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump({"directory_url": "http://127.0.0.1:%d" % self.directory.server_port,
                       "ledger_url": "http://127.0.0.1:%d" % self.ledger.server_port},
                      handle)
        self.config_path = config_path

    @property
    def out_dir(self):
        return os.path.join(self.tmp.name, "out")

    def stop(self):
        self.directory.shutdown()
        self.directory.server_close()
        self.ledger.shutdown()
        self.ledger.server_close()
        self.tmp.cleanup()


# ---------------------------------------------------------------------------
# End-to-end: the acceptance behaviors, against the fake services.
# ---------------------------------------------------------------------------


def standard_fixture():
    """A scenario exercising every rule at once.

    Items span three pages and include a duplicate delivery (item-103), a
    malformed item (item-109, no points), an item owned by an inactive account
    (item-104, chen), and an item whose owner has no account at all (item-111,
    eli). Accounts span two pages and include a malformed account. The ledger
    already holds a summary for bo.
    """
    item_pages = [
        [{"id": "item-101", "owner": "amara", "status": "open", "points": 3},
         {"id": "item-102", "owner": "amara", "status": "done", "points": 2},
         {"id": "item-103", "owner": "bo", "status": "open", "points": 5}],
        [{"id": "item-103", "owner": "bo", "status": "open", "points": 5},
         {"id": "item-104", "owner": "chen", "status": "open", "points": 8},
         {"id": "item-109", "owner": "amara", "status": "open"}],
        [{"id": "item-110", "owner": "amara", "status": "open", "points": 7},
         {"id": "item-111", "owner": "eli", "status": "open", "points": 2}],
    ]
    account_pages = [
        [{"id": "acct-01", "owner": "amara", "active": True},
         {"id": "acct-02", "owner": "bo", "active": True},
         {"id": "acct-03", "owner": "chen", "active": False}],
        [{"id": "acct-04", "owner": "pat", "active": "yes"},
         {"id": "acct-07", "owner": "gus", "active": False}],
    ]
    summary_pages = [
        [{"owner": "bo", "open_items": 99, "done_items": 99, "total_points": 99}],
    ]
    return item_pages, account_pages, summary_pages


class SyncEndToEndTest(unittest.TestCase):
    def setUp(self):
        item_pages, account_pages, summary_pages = standard_fixture()
        self.services = FakeServices(item_pages, account_pages, summary_pages,
                                     active_owners={"amara", "bo"})
        self.addCleanup(self.services.stop)

    def out_path(self, name):
        return os.path.join(self.services.out_dir, name)

    def test_first_run_posts_only_what_is_missing_and_writes_reports(self):
        code, _, _ = run_main(["sync", "--config", self.services.config_path,
                               "--out-dir", self.services.out_dir])
        self.assertEqual(code, 0)

        # Exactly one POST, for the one active owner without a summary, under
        # the specified idempotency key and body.
        posts = self.services.ledger_state.posts()
        self.assertEqual(len(posts), 1)
        _, _, _, key, status = posts[0]
        self.assertEqual(key, "sync-amara")
        self.assertEqual(status, 201)
        self.assertEqual(self.services.ledger_state.stored_summaries,
                         [{"owner": "amara", "open_items": 2, "done_items": 1,
                           "total_points": 12}])
        self.assertEqual(self.services.ledger_state.rejected_posts(), [])

        # summary.jsonl records exactly the POST body.
        self.assertEqual(read_jsonl(self.out_path("summary.jsonl")),
                         [{"owner": "amara", "open_items": 2, "done_items": 1,
                           "total_points": 12}])

        # Orphans: chen's item (inactive owner) and eli's (no account), the
        # records as received, each exactly once.
        self.assertEqual(read_jsonl(self.out_path("orphans.jsonl")),
                         [{"id": "item-104", "owner": "chen", "status": "open",
                           "points": 8},
                          {"id": "item-111", "owner": "eli", "status": "open",
                           "points": 2}])

        # Malformed records from both services, wrapped with their service.
        # (Order within an output file is unspecified.)
        self.assertEqual(
            sorted(read_jsonl(self.out_path("errors.jsonl")),
                   key=lambda entry: json.dumps(entry, sort_keys=True)),
            sorted([{"service": "directory",
                     "record": {"id": "item-109", "owner": "amara",
                                "status": "open"}},
                    {"service": "ledger",
                     "record": {"id": "acct-04", "owner": "pat",
                                "active": "yes"}}],
                   key=lambda entry: json.dumps(entry, sort_keys=True)))

    def test_second_run_issues_no_writes_and_leaves_files_identical(self):
        run_main(["sync", "--config", self.services.config_path,
                  "--out-dir", self.services.out_dir])
        posts_after_first = self.services.ledger_state.posts()
        contents_after_first = {
            name: read_bytes(self.out_path(name))
            for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")}

        code, _, _ = run_main(["sync", "--config", self.services.config_path,
                               "--out-dir", self.services.out_dir])
        self.assertEqual(code, 0)

        self.assertEqual(self.services.ledger_state.posts(), posts_after_first)
        for name, content in contents_after_first.items():
            self.assertEqual(read_bytes(self.out_path(name)), content,
                             "%s changed on the second run" % name)

        # The stored summaries are untouched: nothing was replayed or doubled.
        self.assertEqual(len(self.services.ledger_state.stored_summaries), 1)

    def test_the_duplicate_item_is_counted_once(self):
        services = FakeServices(
            item_pages=[[{"id": "d1", "owner": "kim", "status": "open",
                          "points": 5}],
                        [{"id": "d1", "owner": "kim", "status": "open",
                          "points": 5}]],
            account_pages=[[{"id": "a1", "owner": "kim", "active": True}]],
            active_owners={"kim"})
        self.addCleanup(services.stop)

        code, _, _ = run_main(["sync", "--config", services.config_path,
                               "--out-dir", services.out_dir])
        self.assertEqual(code, 0)
        # item d1 arrives on two pages; the tally counts it once.
        self.assertEqual(services.ledger_state.stored_summaries,
                         [{"owner": "kim", "open_items": 1, "done_items": 0,
                           "total_points": 5}])
        self.assertEqual(read_jsonl(os.path.join(services.out_dir,
                                                 "orphans.jsonl")), [])


class SyncArithmeticTest(unittest.TestCase):
    """The worked example, plus the zeros case and the inactive case."""

    def run_once(self, services):
        code, _, _ = run_main(["sync", "--config", services.config_path,
                               "--out-dir", services.out_dir])
        self.assertEqual(code, 0)
        return services.ledger_state.stored_summaries

    def test_worked_example_and_special_cases(self):
        services = FakeServices(
            item_pages=[[{"id": "i1", "owner": "kim", "status": "open", "points": 3},
                         {"id": "i2", "owner": "kim", "status": "open", "points": 4},
                         {"id": "i3", "owner": "kim", "status": "done", "points": 2},
                         {"id": "i4", "owner": "off", "status": "open", "points": 5}]],
            account_pages=[[{"id": "a1", "owner": "kim", "active": True},
                            {"id": "a2", "owner": "idle", "active": True},
                            {"id": "a3", "owner": "gone", "active": True},
                            {"id": "a4", "owner": "off", "active": False}]],
            active_owners={"kim", "idle", "gone"})
        self.addCleanup(services.stop)

        stored = self.run_once(services)
        by_owner = {summary["owner"]: summary for summary in stored}
        self.assertEqual(by_owner["kim"],
                         {"owner": "kim", "open_items": 2, "done_items": 1,
                          "total_points": 9})
        # An active account with no items gets all zeros.
        self.assertEqual(by_owner["idle"],
                         {"owner": "idle", "open_items": 0, "done_items": 0,
                          "total_points": 0})
        self.assertEqual(by_owner["gone"],
                         {"owner": "gone", "open_items": 0, "done_items": 0,
                          "total_points": 0})
        # An inactive account gets no summary, even when it has items.
        self.assertNotIn("off", by_owner)
        # Its items are orphans.
        orphans = read_jsonl(os.path.join(services.out_dir, "orphans.jsonl"))
        self.assertEqual([record["id"] for record in orphans], ["i4"])
        self.assertEqual(services.ledger_state.rejected_posts(), [])


class SyncMalformedOnlyOwnerTest(unittest.TestCase):
    def test_active_owner_whose_items_are_all_malformed_still_gets_zeros(self):
        services = FakeServices(
            item_pages=[[{"id": "i1", "owner": "kim", "status": "open"},
                         {"id": "i2", "owner": "kim", "status": "open",
                          "points": -3}]],
            account_pages=[[{"id": "a1", "owner": "kim", "active": True}]],
            active_owners={"kim"})
        self.addCleanup(services.stop)

        code, _, _ = run_main(["sync", "--config", services.config_path,
                               "--out-dir", services.out_dir])
        self.assertEqual(code, 0)
        self.assertEqual(services.ledger_state.stored_summaries,
                         [{"owner": "kim", "open_items": 0, "done_items": 0,
                           "total_points": 0}])
        self.assertEqual(
            read_jsonl(os.path.join(services.out_dir, "errors.jsonl")),
            [{"service": "directory",
              "record": {"id": "i1", "owner": "kim", "status": "open"}},
             {"service": "directory",
              "record": {"id": "i2", "owner": "kim", "status": "open",
                         "points": -3}}])


class SyncEmptyServicesTest(unittest.TestCase):
    def test_no_records_anywhere_yields_empty_files_and_no_posts(self):
        services = FakeServices(item_pages=[[]], account_pages=[[]],
                                active_owners=set())
        self.addCleanup(services.stop)

        code, _, _ = run_main(["sync", "--config", services.config_path,
                               "--out-dir", services.out_dir])
        self.assertEqual(code, 0)
        for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl"):
            self.assertEqual(read_text(os.path.join(services.out_dir, name)), "")
        self.assertEqual(services.ledger_state.posts(), [])


class SyncRateLimitTest(unittest.TestCase):
    def setUp(self):
        self.services = FakeServices(
            item_pages=[[{"id": "i1", "owner": "kim", "status": "open",
                          "points": 2}]],
            account_pages=[[{"id": "a1", "owner": "kim", "active": True}]],
            active_owners={"kim"})
        self.addCleanup(self.services.stop)

    def test_429_on_read_is_retried_after_retry_after(self):
        self.services.directory_state.pending_429 = 2
        self.services.directory_state.retry_after = "0"

        code, _, _ = run_main(["sync", "--config", self.services.config_path,
                               "--out-dir", self.services.out_dir])
        self.assertEqual(code, 0)

        item_gets = [entry for entry in self.services.directory_state.requests
                     if entry[2].startswith("/v1/items")]
        # Two 429s, then the same pages read again to completion.
        self.assertEqual([entry[4] for entry in item_gets],
                         [429, 429, 200])
        self.assertEqual(self.services.ledger_state.stored_summaries,
                         [{"owner": "kim", "open_items": 1, "done_items": 0,
                           "total_points": 2}])

    def test_429_on_post_is_retried_and_never_records_a_rejection(self):
        # The GETs go through; exactly the first summary POST is rate-limited.
        self.services.ledger_state.pending_post_429 = 1
        self.services.ledger_state.retry_after = "0"

        code, _, _ = run_main(["sync", "--config", self.services.config_path,
                               "--out-dir", self.services.out_dir])
        self.assertEqual(code, 0)
        statuses = [entry[4] for entry in self.services.ledger_state.posts()]
        self.assertEqual(statuses, [429, 201])  # retried, not abandoned
        self.assertEqual(self.services.ledger_state.rejected_posts(), [])
        self.assertEqual(self.services.ledger_state.stored_summaries,
                         [{"owner": "kim", "open_items": 1, "done_items": 0,
                           "total_points": 2}])


class SyncNeverRejectsTest(unittest.TestCase):
    def test_no_post_is_sent_for_inactive_or_already_summarized_owners(self):
        item_pages, account_pages, summary_pages = standard_fixture()
        services = FakeServices(item_pages, account_pages, summary_pages,
                                active_owners={"amara", "bo"})
        self.addCleanup(services.stop)

        code, _, _ = run_main(["sync", "--config", services.config_path,
                               "--out-dir", services.out_dir])
        self.assertEqual(code, 0)

        keys = [entry[3] for entry in services.ledger_state.posts()]
        self.assertEqual(keys, ["sync-amara"])
        # In particular: no sync-bo (summary exists), no sync-chen or sync-gus
        # (inactive), which the ledger would have answered 422/409.
        self.assertEqual(services.ledger_state.rejected_posts(), [])


class SyncOutDirTest(unittest.TestCase):
    def test_creates_a_nested_out_dir_that_does_not_exist(self):
        services = FakeServices(
            item_pages=[[{"id": "i1", "owner": "kim", "status": "open",
                          "points": 1}]],
            account_pages=[[{"id": "a1", "owner": "kim", "active": True}]],
            active_owners={"kim"})
        self.addCleanup(services.stop)
        nested = os.path.join(services.tmp.name, "deep", "deeper", "out")

        code, _, _ = run_main(["sync", "--config", services.config_path,
                               "--out-dir", nested])
        self.assertEqual(code, 0)
        for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl"):
            self.assertTrue(os.path.exists(os.path.join(nested, name)))


class SyncGarbageRetryAfterTest(unittest.TestCase):
    def test_a_429_without_a_usable_retry_after_still_retries(self):
        services = FakeServices(
            item_pages=[[{"id": "i1", "owner": "kim", "status": "done",
                          "points": 1}]],
            account_pages=[[{"id": "a1", "owner": "kim", "active": True}]],
            active_owners={"kim"})
        self.addCleanup(services.stop)

        original = FakeHandler._rate_limited

        def rate_limited_with_garbage(self):
            if self.state.pending_429 > 0:
                self.state.pending_429 -= 1
                self.state.retry_after = "not-a-number"
                return True
            return False

        FakeHandler._rate_limited = rate_limited_with_garbage
        services.directory_state.pending_429 = 1
        try:
            code, _, _ = run_main(["sync", "--config", services.config_path,
                                   "--out-dir", services.out_dir])
        finally:
            FakeHandler._rate_limited = original
        self.assertEqual(code, 0)
        self.assertEqual(services.ledger_state.stored_summaries,
                         [{"owner": "kim", "open_items": 0, "done_items": 1,
                           "total_points": 1}])


# ---------------------------------------------------------------------------
# A read-only smoke test against the real fixture services, when they are up.
# ---------------------------------------------------------------------------


def load_live_config():
    path = os.path.join(os.getcwd(), "config.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except ValueError:
            return None


class LiveServicesSmokeTest(unittest.TestCase):
    """Read-only checks against the configured services. Skips when down."""

    def fetch(self, url):
        try:
            with urlopen(url, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:  # the rate limit is not the test's subject
                self.skipTest("service rate-limited the smoke test")
            raise

    def setUp(self):
        config = load_live_config()
        if not config:
            self.skipTest("no config.json with service URLs")
        self.config = config
        try:
            self.fetch(config["ledger_url"] + "/v1/accounts?page=1")
        except (URLError, OSError, ValueError):
            self.skipTest("live services are not running")

    def test_directory_items_page_is_readable(self):
        status, envelope = self.fetch(self.config["directory_url"] + "/v1/items?page=1")
        self.assertEqual(status, 200)
        self.assertIsInstance(envelope.get("items"), list)
        self.assertIn("next_page", envelope)

    def test_ledger_accounts_and_summaries_are_readable(self):
        status, accounts = self.fetch(self.config["ledger_url"] + "/v1/accounts?page=1")
        self.assertEqual(status, 200)
        self.assertIsInstance(accounts.get("accounts"), list)
        status, summaries = self.fetch(self.config["ledger_url"] + "/v1/summaries?page=1")
        self.assertEqual(status, 200)
        self.assertIsInstance(summaries.get("summaries"), list)


if __name__ == "__main__":
    unittest.main()
