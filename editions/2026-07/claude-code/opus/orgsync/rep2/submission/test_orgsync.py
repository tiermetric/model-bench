"""Tests for `orgsync sync`.

Three kinds, in one file because they test one command:

* Rule tests — `parse_item`, `parse_account`, `plan`. Pure functions, no
  transport, so each rule about well-formedness, deduplication, orphans and
  what to post is pinned on its own.
* Simulated-service tests — a fake Directory and a fake Ledger that paginate,
  answer 429 with a `Retry-After`, and enforce the real POST semantics
  (201/200/409/422). These drive whole `run_sync` passes over a virtual clock,
  which is how rate-limit obedience and idempotency get tested without waiting.
* Live tests — the same, against the two services named in `config.json`.

Run: `python3 -m unittest test_orgsync`.
"""

import contextlib
import email.message
import io
import json
import os
import shutil
import tempfile
import unittest

from orgsync.__main__ import main
from orgsync.config import load_config
from orgsync.http_client import HttpClient, Response
from orgsync.models import Summary
from orgsync.sync import (ACCOUNTS_PATH, DEFAULT_RETRY_AFTER_SECONDS, DIRECTORY,
                          ERRORS_FILENAME, ITEMS_PATH, LEDGER, ORPHANS_FILENAME,
                          REJECTED_STATUSES, SUMMARIES_PATH, SUMMARY_FILENAME,
                          Plan, ServiceClient, SyncError, idempotency_key,
                          parse_account, parse_item, parse_retry_after, plan,
                          post_summaries, read_jsonl, run_sync)

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(REPO_ROOT, "config.json")

#: Pagination size the fakes use — small, so every walk crosses a page break.
PAGE_SIZE = 2


# ==========================================================================
# Helpers
# ==========================================================================


def item(identifier, owner, status="open", points=1, **extra):
    record = {"id": identifier, "owner": owner, "status": status, "points": points}
    record.update(extra)
    return record


def account(identifier, owner, active=True, **extra):
    record = {"id": identifier, "owner": owner, "active": active}
    record.update(extra)
    return record


def summaries_of(records):
    """`{owner: summary dict}` from a list of summary dicts."""
    return {record["owner"]: record for record in records}


def headers_of(pairs):
    message = email.message.Message()
    for key, value in (pairs or {}).items():
        message[key] = str(value)
    return message


def paginate(records, key, page):
    """One page of `records` under `key`, in the services' pagination scheme."""
    start = (page - 1) * PAGE_SIZE
    chunk = records[start:start + PAGE_SIZE]
    has_more = len(records) > start + PAGE_SIZE
    return {key: chunk, "next_page": page + 1 if has_more else None}


class FakeClock:
    """A monotonic clock that only moves when something sleeps on it."""

    def __init__(self):
        self.now = 100.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class FakeService:
    """Stands in for `HttpClient`: records every call, answers from a handler.

    `handler(call)` returns `(status, body_object_or_None, headers_dict)`.
    """

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def request(self, method, path, params=None, payload=None, headers=None):
        call = {"method": method.upper(), "path": path, "params": dict(params or {}),
                "payload": payload, "headers": dict(headers or {})}
        self.calls.append(call)
        status, body, extra = self.handler(call)
        call["status"] = status
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        return Response(status, headers_of(extra), raw, "fake://" + path)

    def requests_for(self, path, method="GET"):
        return [c for c in self.calls if c["path"] == path and c["method"] == method]


class FakeDirectory:
    """A Directory whose item pages can be interrupted by a rate limit."""

    def __init__(self, items, rate_limit_on=(), retry_after=2):
        self.items = list(items)
        #: 1-based request numbers that answer 429 instead of data.
        self.rate_limit_on = set(rate_limit_on)
        self.retry_after = retry_after
        self.service = FakeService(self._handle)
        self.seen = 0

    def _handle(self, call):
        self.seen += 1
        if self.seen in self.rate_limit_on:
            return 429, {"error": "slow down"}, {"Retry-After": self.retry_after}
        if call["path"] == ITEMS_PATH:
            return 200, paginate(self.items, "items", int(call["params"]["page"])), {}
        return 404, {"error": "no such path"}, {}


class FakeLedger:
    """A Ledger with the real POST semantics, so a bad write cannot pass.

    Rejects a summary whose owner is not the owner of an active, well-formed
    account (422), replays an identical post under a known key (200), and
    refuses a known key carrying a different body (409).
    """

    def __init__(self, accounts, summaries=(), rate_limit_on=(), retry_after=2):
        self.accounts = list(accounts)
        self.summaries = list(summaries)
        self.keys = {}
        self.rate_limit_on = set(rate_limit_on)
        self.retry_after = retry_after
        self.service = FakeService(self._handle)
        self.seen = 0

    @property
    def active_owners(self):
        return {a["owner"] for a in self.accounts
                if isinstance(a.get("active"), bool) and a["active"]
                and isinstance(a.get("owner"), str) and isinstance(a.get("id"), str)}

    def _handle(self, call):
        self.seen += 1
        if self.seen in self.rate_limit_on:
            return 429, {"error": "slow down"}, {"Retry-After": self.retry_after}
        if call["method"] == "GET" and call["path"] == ACCOUNTS_PATH:
            return 200, paginate(self.accounts, "accounts",
                                 int(call["params"]["page"])), {}
        if call["method"] == "GET" and call["path"] == SUMMARIES_PATH:
            return 200, paginate(self.summaries, "summaries",
                                 int(call["params"]["page"])), {}
        if call["method"] == "POST" and call["path"] == SUMMARIES_PATH:
            return self._post(call)
        return 404, {"error": "no such path"}, {}

    def _post(self, call):
        body, key = call["payload"], call["headers"].get("Idempotency-Key")
        if key is None:
            return 400, {"error": "missing Idempotency-Key"}, {}
        if key in self.keys:
            return (200, self.keys[key], {}) if self.keys[key] == body \
                else (409, {"error": "key reused with a different body"}, {})
        if not isinstance(body, dict) or body.get("owner") not in self.active_owners:
            return 422, {"error": "unknown or inactive owner"}, {}
        self.keys[key] = body
        self.summaries.append(dict(body))
        return 201, dict(body), {}

    def posted_bodies(self):
        return [c["payload"] for c in self.service.requests_for(SUMMARIES_PATH, "POST")]


def clients_for(directory, ledger, clock):
    """Wire two fakes and a fake clock into the pair of `ServiceClient`s."""
    return (ServiceClient(DIRECTORY, directory.service, sleep=clock.sleep,
                          monotonic=clock.monotonic),
            ServiceClient(LEDGER, ledger.service, sleep=clock.sleep,
                          monotonic=clock.monotonic))


def quietly(function, *args, **kwargs):
    """Call `function`, swallowing the progress line the command prints."""
    with contextlib.redirect_stdout(io.StringIO()):
        return function(*args, **kwargs)


class OutDirCase(unittest.TestCase):
    """Gives each test its own `out/`, and reads the three files back."""

    def setUp(self):
        self.out_dir = tempfile.mkdtemp(prefix="orgsync-out-")
        self.addCleanup(shutil.rmtree, self.out_dir, True)

    def read(self, filename):
        return read_jsonl(os.path.join(self.out_dir, filename))

    def raw(self, filename):
        with open(os.path.join(self.out_dir, filename), "r", encoding="utf-8") as fh:
            return fh.read()

    def assert_lines_unique(self, filename):
        lines = [ln for ln in self.raw(filename).splitlines() if ln.strip()]
        self.assertCountEqual(lines, set(lines),
                              "%s repeats a line" % filename)


# ==========================================================================
# The rules: what a well-formed record is
# ==========================================================================


class WellFormednessTest(unittest.TestCase):
    def test_item_accepts_both_statuses_zero_points_and_extra_fields(self):
        self.assertIsNotNone(parse_item(item("i-1", "kim", "open", 0)))
        self.assertIsNotNone(parse_item(item("i-2", "kim", "done", 5)))
        self.assertIsNotNone(parse_item(item("i-3", "kim", "open", 1, note="hi")))

    def test_item_rejects_missing_fields(self):
        for field in ("id", "owner", "status", "points"):
            record = item("i-1", "kim")
            del record[field]
            with self.subTest(missing=field):
                self.assertIsNone(parse_item(record))

    def test_item_rejects_wrong_json_types(self):
        cases = [item("i-1", "kim", points="3"), item("i-1", "kim", points=1.5),
                 item("i-1", "kim", points=True), item(1, "kim"), item("i-1", 7),
                 item("i-1", "kim", status=None), item("i-1", "kim", status=True)]
        for record in cases:
            with self.subTest(record=record):
                self.assertIsNone(parse_item(record))

    def test_item_rejects_out_of_range_values(self):
        self.assertIsNone(parse_item(item("i-1", "kim", points=-1)))
        self.assertIsNone(parse_item(item("i-1", "kim", status="sideways")))

    def test_item_rejects_non_objects(self):
        for record in (None, [], "i-1", 3):
            with self.subTest(record=record):
                self.assertIsNone(parse_item(record))

    def test_account_requires_a_json_boolean_for_active(self):
        self.assertIsNotNone(parse_account(account("a-1", "kim", True)))
        self.assertIsNotNone(parse_account(account("a-1", "kim", False)))
        for bad in ("yes", "true", 1, 0, None):
            with self.subTest(active=bad):
                self.assertIsNone(parse_account(account("a-1", "kim", bad)))

    def test_account_rejects_missing_fields_wrong_types_and_non_objects(self):
        self.assertIsNone(parse_account({"id": "a-1", "active": True}))
        self.assertIsNone(parse_account({"owner": "kim", "active": True}))
        self.assertIsNone(parse_account(account("a-1", 7)))
        self.assertIsNone(parse_account("a-1"))

    def test_account_ignores_extra_fields(self):
        self.assertIsNotNone(parse_account(account("a-1", "kim", True, tier="gold")))


# ==========================================================================
# The rules: reconciliation
# ==========================================================================


class PlanArithmeticTest(unittest.TestCase):
    def test_worked_example(self):
        decided = plan([account("a-1", "kim")],
                       [item("i-1", "kim", "open", 3), item("i-2", "kim", "open", 4),
                        item("i-3", "kim", "done", 2)], [])
        self.assertEqual(decided.required,
                         [Summary("kim", open_items=2, done_items=1, total_points=9)])
        self.assertEqual(decided.to_post, decided.required)
        self.assertEqual((decided.orphans, decided.errors), ([], []))

    def test_active_account_with_no_items_gets_a_zero_summary(self):
        decided = plan([account("a-1", "dee")], [], [])
        self.assertEqual(decided.required, [Summary("dee", 0, 0, 0)])

    def test_one_summary_per_active_account_and_none_for_inactive(self):
        decided = plan([account("a-1", "kim"), account("a-2", "lee", False)],
                       [item("i-1", "kim"), item("i-2", "lee")], [])
        self.assertEqual([s.owner for s in decided.required], ["kim"])
        self.assertEqual(decided.orphans, [item("i-2", "lee")])

    def test_a_repeated_item_id_is_counted_once(self):
        twice = item("i-1", "kim", "open", 3)
        decided = plan([account("a-1", "kim")], [twice, dict(twice), twice], [])
        self.assertEqual(decided.required, [Summary("kim", 1, 0, 3)])

    def test_points_sum_over_both_statuses(self):
        decided = plan([account("a-1", "kim")],
                       [item("i-1", "kim", "open", 10), item("i-2", "kim", "done", 5)],
                       [])
        self.assertEqual(decided.required[0].total_points, 15)


class PlanOrphanTest(unittest.TestCase):
    def test_items_of_unknown_inactive_and_malformed_owners_are_orphans(self):
        decided = plan(
            [account("a-1", "kim"), account("a-2", "lee", False),
             account("a-3", "fen", "yes")],
            [item("i-1", "kim"), item("i-2", "lee"), item("i-3", "fen"),
             item("i-4", "zara")], [])
        self.assertEqual([o["id"] for o in decided.orphans],
                         ["i-2", "i-3", "i-4"])

    def test_an_orphan_is_written_once_and_exactly_as_received(self):
        received = item("i-1", "zara", "open", 4, extra={"nested": [1, 2]})
        decided = plan([], [received, dict(received)], [])
        self.assertEqual(decided.orphans, [received])

    def test_a_malformed_item_is_an_error_not_an_orphan(self):
        decided = plan([], [{"id": "i-1", "owner": "zara", "status": "open"}], [])
        self.assertEqual(decided.orphans, [])
        self.assertEqual([e["record"]["id"] for e in decided.errors], ["i-1"])


class PlanErrorTest(unittest.TestCase):
    def test_errors_name_their_service_and_carry_the_record_as_received(self):
        bad_item = {"id": "i-1", "owner": "kim", "status": "open", "points": "3"}
        bad_account = account("a-9", "fen", "yes")
        decided = plan([bad_account], [bad_item], [])
        self.assertIn({"service": DIRECTORY, "record": bad_item}, decided.errors)
        self.assertIn({"service": LEDGER, "record": bad_account}, decided.errors)

    def test_a_repeated_malformed_record_is_written_once(self):
        bad = {"id": "i-1", "owner": "kim", "status": "open"}
        decided = plan([], [bad, dict(bad), bad], [])
        self.assertEqual(len(decided.errors), 1)

    def test_malformed_records_never_enter_a_count(self):
        decided = plan([account("a-1", "kim")],
                       [item("i-1", "kim", "open", 3),
                        {"id": "i-2", "owner": "kim", "status": "open"},
                        item("i-3", "kim", "open", -5)], [])
        self.assertEqual(decided.required, [Summary("kim", 1, 0, 3)])
        self.assertEqual(len(decided.errors), 2)

    def test_a_malformed_account_does_not_make_its_owner_active(self):
        decided = plan([account("a-1", "fen", "yes")], [item("i-1", "fen")], [])
        self.assertEqual(decided.required, [])
        self.assertEqual(decided.to_post, [])
        self.assertEqual([o["id"] for o in decided.orphans], ["i-1"])

    def test_malformed_records_do_not_stop_the_run(self):
        decided = plan([account("a-1", "kim"), "not an account", None],
                       ["not an item", item("i-1", "kim", "done", 2)], [])
        self.assertEqual(decided.required, [Summary("kim", 0, 1, 2)])
        self.assertEqual(len(decided.errors), 3)


class PlanPostSelectionTest(unittest.TestCase):
    def test_posts_only_summaries_the_ledger_is_missing(self):
        decided = plan([account("a-1", "kim"), account("a-2", "lee")],
                       [item("i-1", "kim", "open", 1)],
                       [{"owner": "kim", "open_items": 1, "done_items": 0,
                         "total_points": 1}])
        self.assertEqual([s.owner for s in decided.required], ["kim", "lee"])
        self.assertEqual([s.owner for s in decided.to_post], ["lee"])

    def test_posts_nothing_when_every_owner_already_has_a_summary(self):
        decided = plan([account("a-1", "kim")], [],
                       [{"owner": "kim", "open_items": 0, "done_items": 0,
                         "total_points": 0}])
        self.assertEqual(decided.to_post, [])

    def test_an_existing_summary_for_an_inactive_owner_changes_nothing(self):
        decided = plan([account("a-1", "kim"), account("a-2", "lee", False)], [],
                       [{"owner": "lee"}])
        self.assertEqual([s.owner for s in decided.to_post], ["kim"])

    def test_plan_defaults_are_empty(self):
        self.assertEqual((Plan().errors, Plan().orphans, Plan().to_post),
                         ([], [], []))


# ==========================================================================
# Transport: pagination and rate limiting
# ==========================================================================


class PaginationTest(unittest.TestCase):
    def test_follows_next_page_until_null_and_returns_every_record(self):
        items = [item("i-%d" % n, "kim") for n in range(5)]
        directory = FakeDirectory(items)
        clock = FakeClock()
        client = ServiceClient(DIRECTORY, directory.service, sleep=clock.sleep,
                               monotonic=clock.monotonic)

        self.assertEqual(client.fetch_all(ITEMS_PATH, "items"), items)
        self.assertEqual([c["params"]["page"] for c in directory.service.calls],
                         [1, 2, 3])

    def test_a_single_page_asks_once(self):
        directory = FakeDirectory([item("i-1", "kim")])
        client = ServiceClient(DIRECTORY, directory.service)
        self.assertEqual(len(client.fetch_all(ITEMS_PATH, "items")), 1)
        self.assertEqual(len(directory.service.calls), 1)

    def test_an_empty_service_yields_nothing(self):
        directory = FakeDirectory([])
        self.assertEqual(ServiceClient(DIRECTORY, directory.service)
                         .fetch_all(ITEMS_PATH, "items"), [])

    def test_a_repeating_next_page_does_not_loop_forever(self):
        service = FakeService(lambda call: (200, {"items": [], "next_page": 1}, {}))
        self.assertEqual(ServiceClient(DIRECTORY, service).fetch_all(ITEMS_PATH,
                                                                     "items"), [])
        self.assertEqual(len(service.calls), 1)

    def test_a_non_200_page_raises_rather_than_reporting_partial_data(self):
        service = FakeService(lambda call: (500, {"error": "boom"}, {}))
        client = ServiceClient(DIRECTORY, service, sleep=lambda s: None,
                               max_attempts=2)
        with self.assertRaises(SyncError):
            client.fetch_all(ITEMS_PATH, "items")


class RateLimitTest(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def client_for(self, service, **kwargs):
        return ServiceClient(DIRECTORY, service, sleep=self.clock.sleep,
                             monotonic=self.clock.monotonic, **kwargs)

    def test_a_429_is_waited_out_and_the_same_request_retried(self):
        directory = FakeDirectory([item("i-1", "kim")], rate_limit_on=(1,),
                                  retry_after=3)
        client = self.client_for(directory.service)

        self.assertEqual(len(client.fetch_all(ITEMS_PATH, "items")), 1)

        first, retried = directory.service.calls
        self.assertEqual(first["status"], 429)
        self.assertEqual(retried["status"], 200)
        self.assertEqual({k: first[k] for k in ("method", "path", "params")},
                         {k: retried[k] for k in ("method", "path", "params")})
        self.assertGreaterEqual(sum(self.clock.slept), 3)

    def test_a_429_never_counts_as_data_and_never_abandons_the_work(self):
        items = [item("i-%d" % n, "kim") for n in range(5)]
        directory = FakeDirectory(items, rate_limit_on=(1, 3, 5))
        client = self.client_for(directory.service)
        self.assertEqual(client.fetch_all(ITEMS_PATH, "items"), items)

    def test_the_pause_applies_to_the_whole_service_not_one_path(self):
        ledger = FakeLedger([account("a-1", "kim")], rate_limit_on=(1,),
                            retry_after=5)
        client = ServiceClient(LEDGER, ledger.service, sleep=self.clock.sleep,
                               monotonic=self.clock.monotonic)

        client.fetch_all(ACCOUNTS_PATH, "accounts")
        elapsed_before = self.clock.now
        client.fetch_all(SUMMARIES_PATH, "summaries")

        # The window was opened by the accounts call; the summaries call must
        # not have needed a second wait, and none of it may overrun the window.
        self.assertEqual(self.clock.now, elapsed_before)
        self.assertGreaterEqual(sum(self.clock.slept), 5)

    def test_requests_resume_only_after_the_window_and_not_before(self):
        directory = FakeDirectory([item("i-1", "kim")], rate_limit_on=(1,),
                                  retry_after=4)
        client = self.client_for(directory.service)
        limited_at = self.clock.now
        client.fetch_all(ITEMS_PATH, "items")
        self.assertGreaterEqual(self.clock.now - limited_at, 4)

    def test_retry_after_parsing_falls_back_when_the_header_is_unusable(self):
        self.assertEqual(parse_retry_after(headers_of({"Retry-After": "2"})), 2.0)
        self.assertEqual(parse_retry_after(headers_of({"Retry-After": " 0 "})), 0.0)
        for bad in ({}, {"Retry-After": "soon"}, None):
            with self.subTest(headers=bad):
                self.assertEqual(parse_retry_after(headers_of(bad) if bad is not None
                                                   else None),
                                 DEFAULT_RETRY_AFTER_SECONDS)

    def test_an_endless_rate_limit_eventually_raises_rather_than_spinning(self):
        directory = FakeDirectory([item("i-1", "kim")],
                                  rate_limit_on=range(1, 50))
        client = self.client_for(directory.service, max_attempts=3)
        with self.assertRaises(SyncError):
            client.fetch_all(ITEMS_PATH, "items")
        self.assertEqual(len(directory.service.calls), 3)


# ==========================================================================
# Whole passes over the simulated services
# ==========================================================================


class SimulatedSyncTest(OutDirCase):
    """A whole `run_sync` against fakes: what is written, and what is sent."""

    ACCOUNTS = [account("a-1", "amara"), account("a-2", "bo"),
                account("a-3", "chen", False), account("a-4", "dee"),
                account("a-5", "fen", "yes")]
    ITEMS = [item("i-1", "amara", "open", 3), item("i-2", "amara", "done", 2),
             item("i-3", "bo", "open", 5), item("i-1", "amara", "open", 3),
             item("i-4", "chen", "open", 8), item("i-5", "zara", "open", 4),
             item("i-6", "fen", "done", 1),
             {"id": "i-7", "owner": "amara", "status": "open"}]

    EXPECTED = {"amara": {"owner": "amara", "open_items": 1, "done_items": 1,
                          "total_points": 5},
                "bo": {"owner": "bo", "open_items": 1, "done_items": 0,
                       "total_points": 5},
                "dee": {"owner": "dee", "open_items": 0, "done_items": 0,
                        "total_points": 0}}

    def build(self, summaries=(), directory_limits=(), ledger_limits=()):
        self.directory = FakeDirectory(self.ITEMS, rate_limit_on=directory_limits)
        self.ledger = FakeLedger(self.ACCOUNTS, summaries=summaries,
                                 rate_limit_on=ledger_limits)
        self.clock = FakeClock()
        return clients_for(self.directory, self.ledger, self.clock)

    def sync(self, **kwargs):
        directory, ledger = self.build(**kwargs)
        return quietly(run_sync, None, self.out_dir, directory=directory,
                       ledger=ledger)

    def test_a_pass_writes_every_required_summary_and_exits_zero(self):
        self.assertEqual(self.sync(), 0)
        self.assertEqual(summaries_of(self.read(SUMMARY_FILENAME)), self.EXPECTED)
        self.assertEqual(summaries_of(self.ledger.posted_bodies()), self.EXPECTED)

    def test_orphans_are_the_items_with_no_active_owner(self):
        self.sync()
        self.assertEqual([o["id"] for o in self.read(ORPHANS_FILENAME)],
                         ["i-4", "i-5", "i-6"])
        self.assert_lines_unique(ORPHANS_FILENAME)

    def test_malformed_records_from_both_services_are_written_once_each(self):
        self.sync()
        errors = self.read(ERRORS_FILENAME)
        self.assertEqual(sorted(e["service"] for e in errors), [DIRECTORY, LEDGER])
        self.assertEqual([e["record"] for e in errors if e["service"] == LEDGER],
                         [account("a-5", "fen", "yes")])
        self.assert_lines_unique(ERRORS_FILENAME)

    def test_every_post_carries_its_owner_idempotency_key(self):
        self.sync()
        posts = self.ledger.service.requests_for(SUMMARIES_PATH, "POST")
        self.assertEqual(sorted(p["headers"]["Idempotency-Key"] for p in posts),
                         sorted(idempotency_key(o) for o in self.EXPECTED))

    def test_the_ledger_accepts_every_write_and_rejects_none(self):
        self.sync()
        posts = self.ledger.service.requests_for(SUMMARIES_PATH, "POST")
        self.assertEqual([p["status"] for p in posts], [201] * len(self.EXPECTED))
        self.assertFalse([p for p in posts if p["status"] in REJECTED_STATUSES])

    def test_only_missing_summaries_are_posted(self):
        self.sync(summaries=[dict(self.EXPECTED["amara"])])

        self.assertEqual([b["owner"] for b in self.ledger.posted_bodies()],
                         ["bo", "dee"])
        self.assertEqual(sorted(s["owner"] for s in self.read(SUMMARY_FILENAME)),
                         ["bo", "dee"])

    def test_a_second_pass_writes_nothing_and_leaves_the_files_identical(self):
        self.assertEqual(self.sync(), 0)
        before = {name: self.raw(name) for name in
                  (ERRORS_FILENAME, ORPHANS_FILENAME, SUMMARY_FILENAME)}

        # The same services, now carrying the summaries the first pass posted.
        self.assertEqual(self.sync(summaries=self.ledger.summaries), 0)

        self.assertEqual(self.ledger.posted_bodies(), [])
        self.assertEqual([c for c in self.ledger.service.calls
                          if c["method"] != "GET"], [])
        for name, content in before.items():
            with self.subTest(file=name):
                self.assertEqual(self.raw(name), content)

    def test_rate_limits_on_both_services_do_not_change_the_outcome(self):
        self.assertEqual(self.sync(directory_limits=(1, 3),
                                   ledger_limits=(2, 5, 7)), 0)
        self.assertEqual(summaries_of(self.read(SUMMARY_FILENAME)), self.EXPECTED)
        self.assertEqual(summaries_of(self.ledger.posted_bodies()), self.EXPECTED)
        self.assertGreater(sum(self.clock.slept), 0)

    def test_the_out_directory_is_created_when_absent(self):
        self.out_dir = os.path.join(self.out_dir, "nested", "out")
        self.sync()
        for name in (ERRORS_FILENAME, ORPHANS_FILENAME, SUMMARY_FILENAME):
            self.assertTrue(os.path.exists(os.path.join(self.out_dir, name)))


class RejectedWriteTest(unittest.TestCase):
    """What happens if a write is refused anyway — it must not pass silently."""

    def setUp(self):
        self.ledger = FakeLedger([account("a-1", "kim")])
        self.client = ServiceClient(LEDGER, self.ledger.service,
                                    sleep=lambda seconds: None)

    def test_a_refusal_is_reported_and_the_remaining_writes_still_go(self):
        written, rejected = post_summaries(
            self.client, [Summary("ghost"), Summary("kim", 1, 0, 2)])

        self.assertEqual(written, [Summary("kim", 1, 0, 2).to_dict()])
        self.assertEqual(len(rejected), 1)
        self.assertIn("ghost", rejected[0])

    def test_a_replay_of_an_identical_post_counts_as_held(self):
        first, _ = post_summaries(self.client, [Summary("kim", 1, 0, 2)])
        second, rejected = post_summaries(self.client, [Summary("kim", 1, 0, 2)])

        self.assertEqual([c["status"] for c in
                          self.ledger.service.requests_for(SUMMARIES_PATH, "POST")],
                         [201, 200])
        self.assertEqual((second, rejected), (first, []))

    def test_the_same_key_with_a_different_body_is_a_conflict_not_a_write(self):
        post_summaries(self.client, [Summary("kim", 1, 0, 2)])
        written, rejected = post_summaries(self.client, [Summary("kim", 9, 9, 9)])

        self.assertEqual(written, [])
        self.assertEqual(len(rejected), 1)


# ==========================================================================
# The live services
# ==========================================================================


def fetch_live(config):
    """Everything both live services hold, as received."""
    directory = ServiceClient(DIRECTORY, HttpClient(config.directory_url))
    ledger = ServiceClient(LEDGER, HttpClient(config.ledger_url))
    return (ledger.fetch_all(ACCOUNTS_PATH, "accounts"),
            directory.fetch_all(ITEMS_PATH, "items"),
            ledger.fetch_all(SUMMARIES_PATH, "summaries"))


def expected_from(accounts, items):
    """Recompute the answers straight from the raw records.

    Deliberately written out longhand rather than by calling `plan`, so the
    live assertions are not the implementation checking itself.
    """
    active, malformed_accounts = set(), []
    for record in accounts:
        ok = (isinstance(record, dict) and isinstance(record.get("id"), str)
              and isinstance(record.get("owner"), str)
              and isinstance(record.get("active"), bool))
        if not ok:
            malformed_accounts.append(record)
        elif record["active"]:
            active.add(record["owner"])

    tallies = {owner: [0, 0, 0] for owner in active}
    orphans, malformed_items, seen = [], [], set()
    for record in items:
        ok = (isinstance(record, dict) and isinstance(record.get("id"), str)
              and isinstance(record.get("owner"), str)
              and record.get("status") in ("open", "done")
              and isinstance(record.get("points"), int)
              and not isinstance(record.get("points"), bool)
              and record["points"] >= 0)
        if not ok:
            malformed_items.append(record)
            continue
        if record["id"] in seen:
            continue
        seen.add(record["id"])
        if record["owner"] not in active:
            orphans.append(record)
            continue
        tally = tallies[record["owner"]]
        tally[0 if record["status"] == "open" else 1] += 1
        tally[2] += record["points"]

    summaries = {owner: {"owner": owner, "open_items": t[0], "done_items": t[1],
                         "total_points": t[2]} for owner, t in tallies.items()}
    return summaries, orphans, malformed_items, malformed_accounts


class CountingClient(ServiceClient):
    """A `ServiceClient` that remembers every POST it made and its status."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.posts = []

    def post_json(self, path, payload, headers=None):
        response = super().post_json(path, payload, headers)
        self.posts.append({"payload": payload, "headers": dict(headers or {}),
                           "status": response.status})
        return response


@unittest.skipUnless(os.path.exists(CONFIG_PATH), "config.json is not present")
class LiveServiceTest(OutDirCase):
    """Runs real passes against the two services named in config.json."""

    def setUp(self):
        super().setUp()
        self.config = load_config(CONFIG_PATH)

    def live_clients(self):
        return (CountingClient(DIRECTORY, HttpClient(self.config.directory_url)),
                CountingClient(LEDGER, HttpClient(self.config.ledger_url)))

    def test_two_passes_reconcile_the_services_and_the_second_writes_nothing(self):
        directory, ledger = self.live_clients()
        self.assertEqual(quietly(run_sync, self.config, self.out_dir,
                                 directory=directory, ledger=ledger), 0)

        accounts, items, _ = fetch_live(self.config)
        expected, orphans, bad_items, bad_accounts = expected_from(accounts, items)

        with self.subTest("summaries the ledger now holds"):
            _, _, posted = fetch_live(self.config)
            held = summaries_of([s for s in posted if s.get("owner") in expected])
            self.assertEqual(held, expected)

        with self.subTest("orphans"):
            self.assertCountEqual(self.read(ORPHANS_FILENAME), orphans)
            self.assert_lines_unique(ORPHANS_FILENAME)

        with self.subTest("errors"):
            written = self.read(ERRORS_FILENAME)
            self.assertCountEqual(
                [e["record"] for e in written if e["service"] == DIRECTORY],
                bad_items)
            self.assertCountEqual(
                [e["record"] for e in written if e["service"] == LEDGER],
                bad_accounts)
            self.assert_lines_unique(ERRORS_FILENAME)

        with self.subTest("summary.jsonl covers every required owner"):
            recorded = summaries_of(self.read(SUMMARY_FILENAME))
            for owner, summary in expected.items():
                if owner in recorded:
                    self.assertEqual(recorded[owner], summary)
            self.assert_lines_unique(SUMMARY_FILENAME)

        with self.subTest("no write was rejected"):
            for post in directory.posts + ledger.posts:
                self.assertNotIn(post["status"], REJECTED_STATUSES)
                self.assertIn(post["status"], (200, 201))
                self.assertEqual(post["headers"]["Idempotency-Key"],
                                 idempotency_key(post["payload"]["owner"]))

        before = {name: self.raw(name) for name in
                  (ERRORS_FILENAME, ORPHANS_FILENAME, SUMMARY_FILENAME)}

        with self.subTest("second pass"):
            directory2, ledger2 = self.live_clients()
            self.assertEqual(quietly(run_sync, self.config, self.out_dir,
                                     directory=directory2, ledger=ledger2), 0)
            self.assertEqual(directory2.posts + ledger2.posts, [])
            for name, content in before.items():
                self.assertEqual(self.raw(name), content, "%s changed" % name)

    def test_the_cli_runs_a_pass_and_exits_zero(self):
        code = quietly(main, ["sync", "--config", CONFIG_PATH,
                              "--out-dir", self.out_dir])
        self.assertEqual(code, 0)
        for name in (ERRORS_FILENAME, ORPHANS_FILENAME, SUMMARY_FILENAME):
            self.assertTrue(os.path.exists(os.path.join(self.out_dir, name)))


if __name__ == "__main__":
    unittest.main()
