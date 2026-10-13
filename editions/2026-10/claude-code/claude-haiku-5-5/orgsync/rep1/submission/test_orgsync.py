"""Tests for the orgsync sync command.

The services are replaced by small HTTP servers on 127.0.0.1 with ephemeral
ports, so the suite never touches the services named in config.json. Each
stand-in records every request it receives, which is how the tests check what
the tool sent. The Ledger stand-in enforces the Ledger's rules on POST, so a
rejected write shows up as a failed assertion rather than going unnoticed.
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

from orgsync.config import Config
from orgsync.http_client import Response
from orgsync.service import (DEFAULT_RETRY_AFTER_SECONDS, RateLimitedService,
                             ServiceError, retry_after_seconds)
from orgsync.sync import build_plan, idempotency_key, read_all_pages, run_sync
from orgsync.validate import parse_account, parse_item

ACCOUNTS, ITEMS, SUMMARIES = "/v1/accounts", "/v1/items", "/v1/summaries"


class FakeService:
    """An HTTP server answering from in-memory collections, recording requests.

    `collections` maps a path to `(key, records)` and serves GETs in pages of
    `page_size`. With `accepts_summaries`, POST /v1/summaries follows the Ledger's
    rules: 201 when accepted, 200 for an identical replay, 409 when a key comes
    back with a different body, 422 for an owner without an active account, and
    400 for a body that is not a JSON object. `throttle_first` answers that many
    requests of any kind with 429, and `throttle_posts` does the same for POSTs.
    """

    def __init__(self, collections, page_size=2, accepts_summaries=False,
                 throttle_first=0, throttle_posts=0, retry_after="1"):
        self.collections = collections
        self.page_size = page_size
        self.accepts_summaries = accepts_summaries
        self.throttle_first = throttle_first
        self.throttle_posts = throttle_posts
        self.retry_after = retry_after
        #: (monotonic arrival time, method, path, query, lower-cased headers, body)
        self.requests = []
        #: (method, path, status) for every answer, in order
        self.answers = []
        self.accepted = {}

        service = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                parts = urllib.parse.urlsplit(self.path)
                query = dict(urllib.parse.parse_qsl(parts.query))
                status, headers, payload = service._answer(
                    self.command, parts.path, query, self.headers, body)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _serve
            do_POST = _serve

            def log_message(self, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()

    def close(self):
        self._server.shutdown()
        self._server.server_close()

    def _answer(self, method, path, query, headers, body):
        lowered = {name.lower(): value for name, value in headers.items()}
        self.requests.append((time.monotonic(), method, path, query, lowered, body))
        status, out_headers, payload = self._route(method, path, query, lowered, body)
        self.answers.append((method, path, status))
        return status, out_headers, payload

    def _route(self, method, path, query, headers, body):
        if self.throttle_first > 0:
            self.throttle_first -= 1
            return self._too_many()
        if method == "POST" and self.throttle_posts > 0:
            self.throttle_posts -= 1
            return self._too_many()
        if method == "GET" and path in self.collections:
            return self._page(path, query)
        if method == "POST" and path == SUMMARIES and self.accepts_summaries:
            return self._accept_summary(headers, body)
        return self._reply(404, {"error": "no such route"})

    def _too_many(self):
        return 429, {"Retry-After": self.retry_after}, b'{"error": "slow down"}'

    @staticmethod
    def _reply(status, message):
        return status, {}, json.dumps(message).encode("utf-8")

    def _page(self, path, query):
        key, records = self.collections[path]
        page = int(query.get("page", "1"))
        start = (page - 1) * self.page_size
        more = start + self.page_size < len(records)
        return self._reply(200, {key: records[start:start + self.page_size],
                                 "next_page": page + 1 if more else None})

    def _accept_summary(self, headers, body):
        key = headers.get("idempotency-key")
        try:
            summary = json.loads(body.decode("utf-8"))
        except ValueError:
            return self._reply(400, {"error": "body is not JSON"})
        if key is None or not isinstance(summary, dict):
            return self._reply(400, {"error": "missing key or not an object"})
        if key in self.accepted:
            return self._reply(200 if self.accepted[key] == summary else 409, summary)
        if not self._has_active_account(summary.get("owner")):
            return self._reply(422, {"error": "owner has no active account"})
        self.accepted[key] = summary
        self.collections[SUMMARIES][1].append(summary)
        return self._reply(201, summary)

    def _has_active_account(self, owner):
        accounts = self.collections.get(ACCOUNTS, ("accounts", []))[1]
        return any(isinstance(a, dict) and a.get("owner") == owner and a.get("active") is True
                   for a in accounts)

    def rejected_writes(self):
        return [a for a in self.answers if a[0] == "POST" and a[2] in (400, 409, 422)]

    def posts(self):
        """(arrival time, lower-cased headers, decoded JSON body) for each POST."""
        return [(t, h, json.loads(b.decode("utf-8")))
                for t, method, _, _, h, b in self.requests if method == "POST"]


def account(owner, active=True, account_id=None):
    return {"id": account_id or "acct-" + owner, "owner": owner, "active": active}


def item(item_id, owner, status, points):
    return {"id": item_id, "owner": owner, "status": status, "points": points}


def summary(owner, open_items=0, done_items=0, total_points=0):
    return {"owner": owner, "open_items": open_items, "done_items": done_items,
            "total_points": total_points}


def canonical(entries):
    """Order-free view of a list of JSON values, for comparing output files."""
    return sorted(json.dumps(e, sort_keys=True) for e in entries)


class SyncFixture(unittest.TestCase):
    """Two stand-in services and a fresh out/ directory for each test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = os.path.join(self.tmp.name, "out")

    def serve(self, accounts=(), items=(), summaries=(), page_size=2,
              directory_throttle=0, ledger_throttle=0, ledger_post_throttle=0,
              retry_after="1"):
        self.directory = FakeService({ITEMS: ("items", list(items))},
                                     page_size=page_size, throttle_first=directory_throttle,
                                     retry_after=retry_after)
        self.ledger = FakeService({ACCOUNTS: ("accounts", list(accounts)),
                                   SUMMARIES: ("summaries", list(summaries))},
                                  page_size=page_size, accepts_summaries=True,
                                  throttle_first=ledger_throttle,
                                  throttle_posts=ledger_post_throttle,
                                  retry_after=retry_after)
        self.addCleanup(self.directory.close)
        self.addCleanup(self.ledger.close)
        self.config = Config(directory_url=self.directory.url, ledger_url=self.ledger.url)

    def sync(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_sync(self.config, self.out_dir), 0)

    def read_out(self, name):
        with open(os.path.join(self.out_dir, name), "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def read_bytes(self, name):
        with open(os.path.join(self.out_dir, name), "rb") as handle:
            return handle.read()


class WorkedExampleTest(SyncFixture):
    def test_counts_per_owner_and_summaries_for_active_accounts_only(self):
        self.serve(accounts=[account("kim"), account("bo", active=False), account("lee")],
                   items=[item("i1", "kim", "open", 3), item("i2", "kim", "open", 4),
                          item("i3", "kim", "done", 2)])
        self.sync()

        self.assertEqual(canonical(self.read_out("summary.jsonl")),
                         canonical([summary("kim", 2, 1, 9), summary("lee")]))
        self.assertEqual(self.read_out("orphans.jsonl"), [])
        self.assertEqual(self.read_out("errors.jsonl"), [])

    def test_posts_exact_bodies_with_idempotency_keys(self):
        self.serve(accounts=[account("kim"), account("lee")],
                   items=[item("i1", "kim", "open", 3), item("i2", "kim", "open", 4),
                          item("i3", "kim", "done", 2)])
        self.sync()

        posts = self.ledger.posts()
        self.assertEqual(sorted(h["idempotency-key"] for _, h, _ in posts),
                         ["sync-kim", "sync-lee"])
        self.assertEqual(sorted(json.dumps(body, sort_keys=True) for _, _, body in posts),
                         sorted(json.dumps(body, sort_keys=True) for body in
                                [summary("kim", 2, 1, 9), summary("lee")]))
        self.assertEqual(self.ledger.rejected_writes(), [])

    def test_all_pages_are_read(self):
        items = [item("i%d" % n, "kim", "open", 1) for n in range(7)]
        self.serve(accounts=[account("kim")], items=items, page_size=2)
        self.sync()

        self.assertEqual(self.read_out("summary.jsonl"), [summary("kim", 7, 0, 7)])


class ClassificationTest(SyncFixture):
    def setUp(self):
        super().setUp()
        self.serve(
            accounts=[
                account("kim"),
                account("bo", active=False),
                {"id": 7, "owner": "mia", "active": True},          # malformed: id is an int
                {"id": "acct-x", "owner": "pat", "active": "yes"},  # malformed: active not a bool
            ],
            items=[
                item("i1", "kim", "open", 3),
                item("i1", "kim", "open", 3),                       # same item delivered again
                item("i2", "kim", "done", "2"),                     # points not an int
                item("i3", "mia", "open", 1),                       # mia is not active
                item("i4", "bo", "open", 5),                        # bo is inactive
                item("i5", "zed", "done", 1),                       # no account at all
                item("i5", "zed", "done", 1),
                item("i6", "kim", "open", -1),                      # out of range
                item("i7", "kim", "open", 1.0),                     # float, not an int
                item("i8", "kim", "Open", 1),                       # status not open/done
                item("i9", "kim", "open", 1),
                {"id": "i9", "owner": "kim", "status": "open", "points": 1, "note": "x"},
                True,                                               # not even an object
            ])
        self.malformed_items = [item("i2", "kim", "done", "2"), item("i6", "kim", "open", -1),
                                item("i7", "kim", "open", 1.0), item("i8", "kim", "Open", 1),
                                True]
        self.sync()

    def test_malformed_account_does_not_activate_its_owner(self):
        self.assertEqual([s["owner"] for s in self.read_out("summary.jsonl")], ["kim"])

    def test_only_well_formed_items_are_counted_each_once(self):
        # i1 once, plus the second well-formed i9 (the extra field is ignored).
        self.assertEqual(self.read_out("summary.jsonl"), [summary("kim", 2, 0, 4)])

    def test_orphans_are_written_once_each_as_received(self):
        self.assertEqual(canonical(self.read_out("orphans.jsonl")),
                         canonical([item("i3", "mia", "open", 1),
                                    item("i4", "bo", "open", 5),
                                    item("i5", "zed", "done", 1)]))

    def test_malformed_records_are_reported_once_each_and_never_counted(self):
        expected = [{"service": "directory", "record": r} for r in self.malformed_items]
        expected.append({"service": "ledger",
                         "record": {"id": 7, "owner": "mia", "active": True}})
        expected.append({"service": "ledger",
                         "record": {"id": "acct-x", "owner": "pat", "active": "yes"}})
        self.assertEqual(canonical(self.read_out("errors.jsonl")), canonical(expected))

    def test_only_the_missing_summaries_are_posted(self):
        self.assertEqual([body["owner"] for _, _, body in self.ledger.posts()], ["kim"])
        self.assertEqual(self.ledger.rejected_writes(), [])


class IdempotencyTest(SyncFixture):
    def test_second_run_with_services_unchanged_writes_nothing_and_keeps_output(self):
        self.serve(accounts=[account("kim"), account("lee")],
                   items=[item("i1", "kim", "open", 3), item("i2", "zed", "done", 2)])
        self.sync()
        first = {name: self.read_bytes(name)
                 for name in ("orphans.jsonl", "errors.jsonl", "summary.jsonl")}
        posts_after_first = len(self.ledger.posts())
        ledger_seen, directory_seen = len(self.ledger.requests), len(self.directory.requests)

        self.sync()

        self.assertEqual(len(self.ledger.posts()), posts_after_first)
        self.assertFalse([r for r in self.ledger.requests[ledger_seen:] if r[1] != "GET"])
        self.assertFalse([r for r in self.directory.requests[directory_seen:] if r[1] != "GET"])
        self.assertEqual(self.ledger.rejected_writes(), [])
        for name, content in first.items():
            with self.subTest(file=name):
                self.assertEqual(self.read_bytes(name), content)

    def test_owner_with_existing_summary_is_not_posted_again(self):
        self.serve(accounts=[account("kim"), account("lee")],
                   items=[item("i1", "kim", "open", 3)],
                   summaries=[summary("kim", 9, 9, 9)])
        self.sync()

        self.assertEqual([body["owner"] for _, _, body in self.ledger.posts()], ["lee"])
        self.assertEqual(self.read_out("summary.jsonl"), [summary("lee")])

    def test_summary_file_entries_for_owners_the_ledger_lost_are_dropped(self):
        self.serve(accounts=[account("kim")], items=[item("i1", "kim", "open", 3)])
        os.makedirs(self.out_dir)
        with open(os.path.join(self.out_dir, "summary.jsonl"), "w", encoding="utf-8") as h:
            h.write(json.dumps(summary("ghost", 1, 1, 1)) + "\n")
        self.sync()

        self.assertEqual(self.read_out("summary.jsonl"), [summary("kim", 1, 0, 3)])


class RateLimitTest(SyncFixture):
    def gap_after_429(self, service):
        """Seconds between the 429 answer and the next request to the same service."""
        index = next(i for i, a in enumerate(service.answers) if a[2] == 429)
        return service.requests[index + 1][0] - service.requests[index][0]

    def test_directory_429_is_waited_out_and_retried(self):
        self.serve(accounts=[account("kim")], items=[item("i1", "kim", "open", 3)],
                   directory_throttle=1, retry_after="1")
        self.sync()

        self.assertGreaterEqual(self.gap_after_429(self.directory), 1.0)
        self.assertEqual(self.read_out("summary.jsonl"), [summary("kim", 1, 0, 3)])

    def test_ledger_429_on_a_post_is_retried_with_the_same_body_and_key(self):
        self.serve(accounts=[account("kim")], items=[item("i1", "kim", "open", 3)],
                   ledger_post_throttle=1, retry_after="0")
        self.sync()

        posts = self.ledger.posts()
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0][1:], posts[1][1:])
        self.assertEqual([status for method, _, status in self.ledger.answers
                          if method == "POST"], [429, 201])
        self.assertEqual(self.read_out("summary.jsonl"), [summary("kim", 1, 0, 3)])


class ServiceUnitTest(unittest.TestCase):
    class ScriptedClient:
        def __init__(self, *responses):
            self.responses = list(responses)
            self.calls = 0

        def request(self, method, path, params=None, payload=None, headers=None):
            self.calls += 1
            return self.responses.pop(0)

    class FakeClock:
        def __init__(self):
            self.now = 100.0
            self.sleeps = []

        def __call__(self):
            return self.now

        def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.now += seconds

    @staticmethod
    def reply(status, retry_after=None):
        headers = email.message.Message()
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        return Response(status, headers, b"{}", "u")

    def service(self, *responses):
        clock = self.FakeClock()
        client = self.ScriptedClient(*responses)
        return RateLimitedService(client, clock=clock, sleep=clock.sleep), clock, client

    def test_429_waits_retry_after_before_retrying(self):
        service, clock, client = self.service(self.reply(429, "3"), self.reply(200))
        self.assertEqual(service.request("GET", "/v1/x").status, 200)
        self.assertEqual(clock.sleeps, [3.0])
        self.assertEqual(client.calls, 2)

    def test_repeated_429s_each_wait_their_own_retry_after(self):
        service, clock, client = self.service(self.reply(429, "2"), self.reply(429, "5"),
                                              self.reply(200))
        self.assertEqual(service.request("GET", "/v1/x").status, 200)
        self.assertEqual(clock.sleeps, [2.0, 5.0])
        self.assertEqual(client.calls, 3)

    def test_a_missing_retry_after_uses_the_default(self):
        service, clock, _ = self.service(self.reply(429), self.reply(200))
        service.request("GET", "/v1/x")
        self.assertEqual(clock.sleeps, [DEFAULT_RETRY_AFTER_SECONDS])

    def test_transient_5xx_is_retried_then_succeeds(self):
        service, clock, client = self.service(self.reply(500), self.reply(200))
        self.assertEqual(service.request("GET", "/v1/x").status, 200)
        self.assertEqual(client.calls, 2)
        self.assertEqual(clock.sleeps, [0.5])

    def test_persistent_5xx_gives_up_with_service_error(self):
        service, _, client = self.service(self.reply(500), self.reply(500), self.reply(503))
        with self.assertRaises(ServiceError):
            service.request("GET", "/v1/x")
        self.assertEqual(client.calls, 3)

    def test_retry_after_parsing(self):
        self.assertEqual(retry_after_seconds(self.reply(429, "2").headers), 2.0)
        self.assertEqual(retry_after_seconds(self.reply(429, " 1.5 ").headers), 1.5)
        self.assertEqual(retry_after_seconds(self.reply(429, "-4").headers), 0.0)
        self.assertEqual(retry_after_seconds(self.reply(429, "nan").headers),
                         DEFAULT_RETRY_AFTER_SECONDS)
        self.assertEqual(retry_after_seconds(self.reply(429, "soon").headers),
                         DEFAULT_RETRY_AFTER_SECONDS)
        self.assertEqual(retry_after_seconds(self.reply(429).headers),
                         DEFAULT_RETRY_AFTER_SECONDS)
        self.assertEqual(retry_after_seconds(self.reply(429, "Wed, 21 Oct 2015 07:28:00 GMT").headers),
                         0.0)

    def test_read_all_pages_rejects_a_page_of_the_wrong_shape(self):
        bad = Response(200, email.message.Message(),
                       b'{"items": "nope", "next_page": null}', "u")
        service, _, _ = self.service(bad)
        with self.assertRaises(ServiceError):
            read_all_pages(service, ITEMS, "items")

    def test_read_all_pages_rejects_a_repeated_page(self):
        to_two = Response(200, email.message.Message(),
                          b'{"items": [], "next_page": 2}', "u")
        back_to_one = Response(200, email.message.Message(),
                               b'{"items": [], "next_page": 1}', "u")
        service, _, client = self.service(to_two, back_to_one)
        with self.assertRaises(ServiceError):
            read_all_pages(service, ITEMS, "items")


class ValidationTest(unittest.TestCase):
    def test_well_formed_item_ignores_extra_fields(self):
        parsed = parse_item({"id": "i1", "owner": "kim", "status": "done",
                             "points": 0, "extra": [1]})
        self.assertEqual((parsed.id, parsed.status, parsed.points), ("i1", "done", 0))

    def test_malformed_items_are_rejected(self):
        for record in ({"id": "i1", "owner": "kim", "status": "open"},
                       {"id": 1, "owner": "kim", "status": "open", "points": 1},
                       {"id": "i1", "owner": "kim", "status": "open", "points": True},
                       {"id": "i1", "owner": "kim", "status": "open", "points": "1"},
                       {"id": "i1", "owner": "kim", "status": "open", "points": -1},
                       {"id": "i1", "owner": "kim", "status": "archived", "points": 1},
                       ["i1"], None):
            with self.subTest(record=record):
                self.assertIsNone(parse_item(record))

    def test_malformed_accounts_are_rejected(self):
        for record in ({"id": "a", "owner": "kim"},
                       {"id": "a", "owner": "kim", "active": 1},
                       {"id": "a", "owner": "kim", "active": "true"},
                       {"id": None, "owner": "kim", "active": True}):
            with self.subTest(record=record):
                self.assertIsNone(parse_account(record))

    def test_plan_matches_the_worked_example_without_any_service(self):
        plan = build_plan([account("kim")],
                          [item("i1", "kim", "open", 3), item("i2", "kim", "open", 4),
                           item("i3", "kim", "done", 2)])
        self.assertEqual(plan.required["kim"].to_dict(), summary("kim", 2, 1, 9))
        self.assertEqual(plan.orphans, [])
        self.assertEqual(plan.errors, [])

    def test_idempotency_key_is_sync_owner_and_survives_odd_owners(self):
        self.assertEqual(idempotency_key("kim"), "sync-kim")
        self.assertEqual(idempotency_key("zoë"), "sync-zoë")
        self.assertEqual(idempotency_key("a\nb"), "sync-a%0Ab")
        self.assertEqual(idempotency_key("名前"), "sync-%E5%90%8D%E5%89%8D")


if __name__ == "__main__":
    unittest.main()
