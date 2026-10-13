"""Tests for `orgsync sync` — the reconciliation the tool exists to perform.

Two layers. Most tests drive `run_sync` against fake Directory and Ledger
services built in-process: the fakes enforce the real ledger's rules (422 for
an owner without an active account, 409 for a key reused with a different
body, 200 for an exact replay) and can be told to answer 429 at chosen points,
so behaviour that is awkward to provoke against a live service — rate limits,
malformed pages, a ledger that already holds summaries — is exercised exactly.

The last class then runs the same code against the two services named by
`config.json`, and skips rather than fails if they are not reachable.
"""

import email.message
import json
import os
import shutil
import tempfile
import unittest

from orgsync.__main__ import main
from orgsync.config import Config, load_config
from orgsync.http_client import HttpClient, HttpClientError, Response
from orgsync.models import Account, Item
from orgsync.sync import (DEFAULT_RETRY_AFTER_SECONDS, ERRORS_FILENAME,
                          ORPHANS_FILENAME, SUMMARY_FILENAME, ErrorLog,
                          ServiceClient, SyncError, build_summaries,
                          find_orphans, iter_pages, parse_account, parse_item,
                          parse_retry_after, read_items, run_sync)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

#: The three files a run writes, in no particular order.
OUTPUT_FILENAMES = (ORPHANS_FILENAME, ERRORS_FILENAME, SUMMARY_FILENAME)


def read_text(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def read_lines(path):
    """Every JSON value in a .jsonl file."""
    return [json.loads(line) for line in read_text(path).splitlines() if line.strip()]


def headers(pairs=None):
    """An email.message.Message, which is what urllib hands back for headers."""
    message = email.message.Message()
    for key, value in (pairs or {}).items():
        message[key] = str(value)
    return message


def response(status, payload=None, header_pairs=None):
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    return Response(status, headers(header_pairs), body, "http://fake.test")


def page_of(records, page, per_page=None):
    """The envelope for 1-based `page` of `records`, split into pages."""
    pages = records if per_page is None else [records[i:i + per_page]
                                              for i in range(0, len(records), per_page)]
    if page < 1 or page > len(pages):
        return [], None
    return pages[page - 1], (page + 1 if page < len(pages) else None)


# --------------------------------------------------------------------------
# Fake services
# --------------------------------------------------------------------------


class FakeService:
    """Base for the two fakes: counts requests and can answer 429.

    `rate_limit_at` holds 1-based request ordinals that answer 429 instead of
    doing anything. The request is *not* otherwise processed, so a rate-limited
    POST must be re-sent for its summary to be stored — which is what makes
    "a 429 is not a rejection and never abandons the work" testable.
    """

    def __init__(self, rate_limit_at=(), retry_after="1"):
        self.rate_limit_at = set(rate_limit_at)
        self.retry_after = retry_after
        self.requests = []
        self.count = 0

    def _rate_limited(self, method, path):
        self.count += 1
        self.requests.append((method, path))
        if self.count in self.rate_limit_at:
            pairs = {} if self.retry_after is None else {"Retry-After": self.retry_after}
            return response(429, None, pairs)
        return None

    @property
    def gets(self):
        return [path for method, path in self.requests if method == "GET"]

    @property
    def writes(self):
        return [path for method, path in self.requests if method != "GET"]


class FakeDirectory(FakeService):
    """Serves `/v1/items` from a list of pages (each page a list of records)."""

    def __init__(self, pages, **kwargs):
        super().__init__(**kwargs)
        self.pages = pages

    def get(self, path, params=None, header_map=None):
        limited = self._rate_limited("GET", path)
        if limited is not None:
            return limited
        items, next_page = page_of(self.pages, int((params or {}).get("page", 1)))
        return response(200, {"items": items, "next_page": next_page})

    def post_json(self, path, payload, header_map=None):  # pragma: no cover
        raise AssertionError("the directory is read-only")


class FakeLedger(FakeService):
    """Serves accounts and summaries, and enforces the POST contract.

    Rejection is the point of this fake: it answers 422 for an owner that is
    not the owner of an active, well-formed account and 409 for an idempotency
    key reused with a different body, so any test that drives a real sync
    through it fails loudly if the sync would have made a rejected write.
    """

    def __init__(self, account_pages, summaries=(), **kwargs):
        super().__init__(**kwargs)
        self.account_pages = account_pages
        self.summaries = [dict(summary) for summary in summaries]
        self.keys = {}
        self.rejections = []

    @property
    def active_owners(self):
        owners = set()
        for page in self.account_pages:
            for record in page:
                account = parse_account(record)
                if account is not None and account.active:
                    owners.add(account.owner)
        return owners

    def get(self, path, params=None, header_map=None):
        limited = self._rate_limited("GET", path)
        if limited is not None:
            return limited
        page = int((params or {}).get("page", 1))
        if path == "/v1/accounts":
            records, next_page = page_of(self.account_pages, page)
            return response(200, {"accounts": records, "next_page": next_page})
        if path == "/v1/summaries":
            records, next_page = page_of(self.summaries, page, per_page=2)
            return response(200, {"summaries": records, "next_page": next_page})
        raise AssertionError("unexpected path %r" % path)  # pragma: no cover

    def post_json(self, path, payload, header_map=None):
        limited = self._rate_limited("POST", path)
        if limited is not None:
            return limited
        key = dict(header_map or {}).get("Idempotency-Key")
        if payload.get("owner") not in self.active_owners:
            self.rejections.append((422, key, payload))
            return response(422, {"error": "unknown or inactive owner"})
        if key in self.keys:
            if self.keys[key] == payload:
                return response(200, payload)
            self.rejections.append((409, key, payload))
            return response(409, {"error": "key reused with a different body"})
        self.keys[key] = dict(payload)
        self.summaries.append(dict(payload))
        return response(201, payload)


class RecordingClient:
    """Wraps a real HttpClient, recording every request it passes through."""

    def __init__(self, inner):
        self.inner = inner
        self.requests = []

    def get(self, path, params=None, header_map=None):
        self.requests.append(("GET", path, dict(params or {})))
        return self.inner.get(path, params, header_map)

    def post_json(self, path, payload, header_map=None):
        self.requests.append(("POST", path, payload))
        return self.inner.post_json(path, payload, header_map)

    @property
    def writes(self):
        return [req for req in self.requests if req[0] != "GET"]


class SyncHarness:
    """A whole sync: two fakes, a temp out dir, and the run itself."""

    def __init__(self, test, item_pages, account_pages, summaries=(),
                 directory_limits=(), ledger_limits=()):
        self.slept = []
        self.clock = [0.0]
        self.directory = FakeDirectory(item_pages, rate_limit_at=directory_limits)
        self.ledger = FakeLedger(account_pages, summaries, rate_limit_at=ledger_limits)
        self.out_dir = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, self.out_dir, True)

    def _sleep(self, seconds):
        self.slept.append(seconds)
        self.clock[0] += seconds

    def _service(self, name, fake):
        return ServiceClient(name, client=fake, sleep=self._sleep,
                             monotonic=lambda: self.clock[0])

    def run(self):
        return run_sync(Config("http://directory.test", "http://ledger.test"),
                        self.out_dir,
                        directory=self._service("directory", self.directory),
                        ledger=self._service("ledger", self.ledger))

    def lines(self, filename):
        return read_lines(os.path.join(self.out_dir, filename))

    def contents(self):
        return {name: read_text(os.path.join(self.out_dir, name))
                for name in OUTPUT_FILENAMES}


# A small world exercising every rule at once: a duplicate delivery, a
# malformed item and a malformed account, an active owner with no items, an
# inactive owner, and an owner with no account at all.
ITEM_PAGES = [
    [{"id": "i-1", "owner": "kim", "status": "open", "points": 3},
     {"id": "i-2", "owner": "kim", "status": "open", "points": 4}],
    [{"id": "i-3", "owner": "kim", "status": "done", "points": 2},
     {"id": "i-1", "owner": "kim", "status": "open", "points": 3},   # same item twice
     {"id": "i-4", "owner": "rex", "status": "open", "points": 5}],  # inactive owner
    [{"id": "i-5", "owner": "nobody", "status": "done", "points": 1},  # no account
     {"id": "i-6", "owner": "kim", "status": "open"},                  # no points
     {"id": "i-7", "owner": "vic", "status": "open", "points": 9}],    # malformed account
]
ACCOUNT_PAGES = [
    [{"id": "a-1", "owner": "kim", "active": True},
     {"id": "a-2", "owner": "rex", "active": False}],
    [{"id": "a-3", "owner": "ida", "active": True, "note": "no items"},
     {"id": "a-4", "owner": "vic", "active": "yes"}],
]
KIM = {"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9}
IDA = {"owner": "ida", "open_items": 0, "done_items": 0, "total_points": 0}


# --------------------------------------------------------------------------
# Well-formedness
# --------------------------------------------------------------------------


class ParseItemTest(unittest.TestCase):
    def test_accepts_a_well_formed_item_and_ignores_extra_fields(self):
        self.assertEqual(
            parse_item({"id": "i-1", "owner": "kim", "status": "open", "points": 3}),
            Item("i-1", "kim", "open", 3))
        self.assertEqual(
            parse_item({"id": "i-1", "owner": "kim", "status": "done",
                        "points": 0, "extra": ["anything"]}),
            Item("i-1", "kim", "done", 0))

    def test_rejects_missing_fields(self):
        full = {"id": "i-1", "owner": "kim", "status": "open", "points": 3}
        for field in full:
            with self.subTest(missing=field):
                record = dict(full)
                del record[field]
                self.assertIsNone(parse_item(record))

    def test_rejects_wrong_json_types(self):
        for record in ({"id": 1, "owner": "kim", "status": "open", "points": 3},
                       {"id": "i", "owner": None, "status": "open", "points": 3},
                       {"id": "i", "owner": "kim", "status": 1, "points": 3},
                       {"id": "i", "owner": "kim", "status": "open", "points": "3"},
                       {"id": "i", "owner": "kim", "status": "open", "points": 3.0},
                       {"id": "i", "owner": "kim", "status": "open", "points": True},
                       {"id": "i", "owner": "kim", "status": "open", "points": None}):
            with self.subTest(record=record):
                self.assertIsNone(parse_item(record))

    def test_rejects_out_of_range_or_unknown_values(self):
        self.assertIsNone(parse_item({"id": "i", "owner": "k", "status": "open",
                                      "points": -1}))
        self.assertIsNone(parse_item({"id": "i", "owner": "k", "status": "sideways",
                                      "points": 1}))

    def test_rejects_a_record_that_is_not_an_object(self):
        for record in (None, [], "i-1", 7):
            with self.subTest(record=record):
                self.assertIsNone(parse_item(record))


class ParseAccountTest(unittest.TestCase):
    def test_accepts_a_well_formed_account_and_ignores_extra_fields(self):
        self.assertEqual(parse_account({"id": "a-1", "owner": "kim", "active": True}),
                         Account("a-1", "kim", True))
        self.assertEqual(
            parse_account({"id": "a-1", "owner": "kim", "active": False, "tier": "x"}),
            Account("a-1", "kim", False))

    def test_active_must_be_a_json_boolean(self):
        for active in ("yes", "true", 1, 0, None):
            with self.subTest(active=active):
                self.assertIsNone(parse_account({"id": "a", "owner": "k",
                                                 "active": active}))

    def test_rejects_missing_fields_and_non_objects(self):
        self.assertIsNone(parse_account({"owner": "kim", "active": True}))
        self.assertIsNone(parse_account({"id": "a", "active": True}))
        self.assertIsNone(parse_account({"id": "a", "owner": "kim"}))
        self.assertIsNone(parse_account(["a", "kim", True]))


# --------------------------------------------------------------------------
# The arithmetic
# --------------------------------------------------------------------------


class BuildSummariesTest(unittest.TestCase):
    def setUp(self):
        self.accounts = {"kim": Account("a-1", "kim", True),
                         "rex": Account("a-2", "rex", False),
                         "ida": Account("a-3", "ida", True)}

    def summaries(self, items):
        return build_summaries(self.accounts, {item.id: item for item in items})

    def test_the_worked_example(self):
        got = self.summaries([Item("i-1", "kim", "open", 3),
                              Item("i-2", "kim", "open", 4),
                              Item("i-3", "kim", "done", 2)])
        self.assertEqual(got["kim"].to_dict(), KIM)

    def test_an_active_account_with_no_items_gets_zeroes(self):
        self.assertEqual(self.summaries([])["ida"].to_dict(), IDA)

    def test_inactive_accounts_get_no_summary(self):
        got = self.summaries([Item("i-4", "rex", "open", 5)])
        self.assertNotIn("rex", got)
        self.assertEqual(set(got), {"kim", "ida"})

    def test_items_of_other_owners_never_leak_into_a_summary(self):
        got = self.summaries([Item("i-5", "nobody", "done", 1),
                              Item("i-1", "kim", "open", 3)])
        self.assertEqual(got["kim"].to_dict(),
                         {"owner": "kim", "open_items": 1, "done_items": 0,
                          "total_points": 3})
        self.assertEqual(got["ida"].to_dict(), IDA)


class DuplicateAndOrphanTest(unittest.TestCase):
    def test_an_item_delivered_twice_is_counted_once(self):
        directory = FakeDirectory([
            [{"id": "i-1", "owner": "kim", "status": "open", "points": 3}],
            [{"id": "i-1", "owner": "kim", "status": "open", "points": 3}],
        ])
        errors = ErrorLog()
        items, _ = read_items(ServiceClient("directory", client=directory), errors)
        self.assertEqual(list(items), ["i-1"])
        summaries = build_summaries({"kim": Account("a", "kim", True)}, items)
        self.assertEqual(summaries["kim"].to_dict(),
                         {"owner": "kim", "open_items": 1, "done_items": 0,
                          "total_points": 3})
        self.assertEqual(errors.entries, [])

    def test_orphans_are_the_items_no_active_account_owns(self):
        accounts = {"kim": Account("a-1", "kim", True),
                    "rex": Account("a-2", "rex", False)}
        items = {"i-1": Item("i-1", "kim", "open", 3),
                 "i-4": Item("i-4", "rex", "open", 5),
                 "i-5": Item("i-5", "nobody", "done", 1)}
        raw = {key: item.to_dict() for key, item in items.items()}
        self.assertEqual(find_orphans(accounts, items, raw),
                         [raw["i-4"], raw["i-5"]])


class ErrorLogTest(unittest.TestCase):
    def test_the_same_malformed_record_is_recorded_once_per_service(self):
        log = ErrorLog()
        record = {"id": "i-6", "owner": "kim", "status": "open"}
        log.add("directory", record)
        log.add("directory", dict(record))
        log.add("ledger", record)
        self.assertEqual(log.entries, [{"service": "directory", "record": record},
                                       {"service": "ledger", "record": record}])

    def test_records_that_are_not_objects_are_still_recorded(self):
        log = ErrorLog()
        log.add("directory", None)
        log.add("directory", "not-a-record")
        log.add("directory", None)
        self.assertEqual([entry["record"] for entry in log.entries],
                         [None, "not-a-record"])


# --------------------------------------------------------------------------
# Reading the services
# --------------------------------------------------------------------------


class PaginationTest(unittest.TestCase):
    def service(self, pages, **kwargs):
        return ServiceClient("directory", client=FakeDirectory(pages, **kwargs))

    def test_follows_next_page_until_null(self):
        service = self.service([[1, 2], [3], [4, 5]])
        self.assertEqual(list(iter_pages(service, "/v1/items", "items")),
                         [1, 2, 3, 4, 5])
        self.assertEqual(service.client.count, 3)

    def test_a_single_page_is_one_request(self):
        service = self.service([[1]])
        self.assertEqual(list(iter_pages(service, "/v1/items", "items")), [1])
        self.assertEqual(service.client.count, 1)

    def test_an_empty_collection_yields_nothing(self):
        service = self.service([[]])
        self.assertEqual(list(iter_pages(service, "/v1/items", "items")), [])

    def test_a_malformed_envelope_ends_the_walk_without_raising(self):
        class Odd(FakeDirectory):
            def get(self, path, params=None, header_map=None):
                self.count += 1
                return response(200, {"items": "not a list", "next_page": None})

        service = ServiceClient("directory", client=Odd([]))
        self.assertEqual(list(iter_pages(service, "/v1/items", "items")), [])

    def test_a_pagination_cycle_terminates(self):
        class Loop(FakeDirectory):
            def get(self, path, params=None, header_map=None):
                self.count += 1
                return response(200, {"items": [self.count], "next_page": 1})

        service = ServiceClient("directory", client=Loop([]))
        self.assertEqual(list(iter_pages(service, "/v1/items", "items")), [1])


class RetryAfterTest(unittest.TestCase):
    def test_reads_the_header(self):
        self.assertEqual(parse_retry_after(headers({"Retry-After": "2"})), 2.0)
        self.assertEqual(parse_retry_after(headers({"Retry-After": "0.5"})), 0.5)

    def test_falls_back_when_absent_or_unreadable(self):
        self.assertEqual(parse_retry_after(headers()), DEFAULT_RETRY_AFTER_SECONDS)
        self.assertEqual(parse_retry_after(headers({"Retry-After": "soon"})),
                         DEFAULT_RETRY_AFTER_SECONDS)
        self.assertEqual(parse_retry_after(None), DEFAULT_RETRY_AFTER_SECONDS)

    def test_never_negative(self):
        self.assertEqual(parse_retry_after(headers({"Retry-After": "-5"})), 0.0)


class RateLimitTest(unittest.TestCase):
    def make(self, fake):
        self.slept = []
        self.clock = [0.0]

        def sleep(seconds):
            self.slept.append(seconds)
            self.clock[0] += seconds

        return ServiceClient("directory", client=fake, sleep=sleep,
                             monotonic=lambda: self.clock[0])

    def test_a_429_is_waited_out_and_the_request_retried(self):
        fake = FakeDirectory([[{"id": "i-1", "owner": "kim",
                               "status": "open", "points": 3}]],
                             rate_limit_at=(1,), retry_after="2")
        service = self.make(fake)

        records = list(iter_pages(service, "/v1/items", "items"))

        self.assertEqual(records, [{"id": "i-1", "owner": "kim",
                                    "status": "open", "points": 3}])
        self.assertEqual(fake.count, 2, "the same request must be re-sent")
        self.assertEqual(len(self.slept), 1)
        self.assertGreaterEqual(self.slept[0], 2.0,
                                "must wait at least Retry-After seconds")

    def test_a_429_is_never_data_and_never_ends_the_walk(self):
        fake = FakeDirectory([[1], [2], [3]], rate_limit_at=(1, 3, 5), retry_after="1")
        service = self.make(fake)
        self.assertEqual(list(iter_pages(service, "/v1/items", "items")), [1, 2, 3])
        self.assertEqual(len(self.slept), 3)

    def test_a_429_without_a_header_still_waits(self):
        fake = FakeDirectory([[1]], rate_limit_at=(1,), retry_after=None)
        service = self.make(fake)
        self.assertEqual(list(iter_pages(service, "/v1/items", "items")), [1])
        self.assertGreaterEqual(self.slept[0], DEFAULT_RETRY_AFTER_SECONDS)

    def test_the_wait_holds_across_calls_not_just_within_one(self):
        fake = FakeDirectory([[1]], rate_limit_at=(1,), retry_after="5")
        service = self.make(fake)
        service.client.rate_limit_at = {1}
        list(iter_pages(service, "/v1/items", "items"))
        self.assertGreaterEqual(self.slept[0], 5.0)

    def test_one_services_rate_limit_does_not_stall_the_other(self):
        harness = SyncHarness(self, ITEM_PAGES, ACCOUNT_PAGES, ledger_limits=(1,))
        self.assertEqual(harness.run(), 0)
        self.assertEqual(len(harness.slept), 1)

    def test_a_rate_limited_post_is_retried_and_still_lands(self):
        # The 5th ledger request is the first POST; it is rate limited, so the
        # summary only exists if the POST was sent again.
        harness = SyncHarness(self, ITEM_PAGES, ACCOUNT_PAGES, ledger_limits=(5,))
        self.assertEqual(harness.run(), 0)
        self.assertEqual(harness.ledger.rejections, [])
        self.assertEqual(sorted(harness.ledger.summaries, key=lambda s: s["owner"]),
                         [IDA, KIM])
        self.assertEqual(harness.lines(SUMMARY_FILENAME), [IDA, KIM])


class UnreadableServiceTest(unittest.TestCase):
    def test_a_persistent_error_status_is_raised_not_silently_counted(self):
        class Broken(FakeDirectory):
            def get(self, path, params=None, header_map=None):
                self.count += 1
                return response(404, {"error": "gone"})

        service = ServiceClient("directory", client=Broken([]), sleep=lambda s: None)
        with self.assertRaises(SyncError):
            list(iter_pages(service, "/v1/items", "items"))

    def test_an_unreadable_body_is_raised(self):
        class Garbled(FakeDirectory):
            def get(self, path, params=None, header_map=None):
                self.count += 1
                return Response(200, headers(), b"not json", "http://fake.test")

        service = ServiceClient("directory", client=Garbled([]))
        with self.assertRaises(SyncError):
            list(iter_pages(service, "/v1/items", "items"))


# --------------------------------------------------------------------------
# A whole run
# --------------------------------------------------------------------------


class RunSyncTest(unittest.TestCase):
    def setUp(self):
        self.harness = SyncHarness(self, ITEM_PAGES, ACCOUNT_PAGES)
        self.assertEqual(self.harness.run(), 0)

    def test_writes_one_summary_per_active_account(self):
        self.assertEqual(self.harness.lines(SUMMARY_FILENAME), [IDA, KIM])

    def test_posts_every_summary_with_its_idempotency_key(self):
        self.assertEqual(self.harness.ledger.keys, {"sync-ida": IDA, "sync-kim": KIM})

    def test_makes_no_rejected_write(self):
        self.assertEqual(self.harness.ledger.rejections, [])

    def test_orphans_are_written_once_as_received(self):
        self.assertEqual(
            self.harness.lines(ORPHANS_FILENAME),
            [{"id": "i-4", "owner": "rex", "status": "open", "points": 5},
             {"id": "i-5", "owner": "nobody", "status": "done", "points": 1},
             {"id": "i-7", "owner": "vic", "status": "open", "points": 9}])

    def test_malformed_records_are_written_with_their_service(self):
        self.assertEqual(
            sorted(self.harness.lines(ERRORS_FILENAME), key=lambda e: e["service"]),
            [{"service": "directory",
              "record": {"id": "i-6", "owner": "kim", "status": "open"}},
             {"service": "ledger",
              "record": {"id": "a-4", "owner": "vic", "active": "yes"}}])

    def test_malformed_records_enter_no_count(self):
        # i-6 has no points and vic's account is not an account: kim's totals
        # exclude i-6, and vic gets no summary while vic's item is an orphan.
        summaries = {line["owner"]: line for line in
                     self.harness.lines(SUMMARY_FILENAME)}
        self.assertEqual(summaries["kim"], KIM)
        self.assertNotIn("vic", summaries)

    def test_creates_the_output_directory_if_absent(self):
        out_dir = os.path.join(tempfile.mkdtemp(), "deeper", "out")
        self.addCleanup(shutil.rmtree, os.path.dirname(out_dir), True)
        harness = SyncHarness(self, ITEM_PAGES, ACCOUNT_PAGES)
        harness.out_dir = out_dir
        self.assertEqual(harness.run(), 0)
        for name in (ORPHANS_FILENAME, ERRORS_FILENAME, SUMMARY_FILENAME):
            self.assertTrue(os.path.isfile(os.path.join(out_dir, name)), name)


class SkipWhatTheLedgerAlreadyHasTest(unittest.TestCase):
    def test_an_existing_summary_is_not_posted_again(self):
        harness = SyncHarness(self, ITEM_PAGES, ACCOUNT_PAGES, summaries=[KIM])
        self.assertEqual(harness.run(), 0)
        self.assertEqual(list(harness.ledger.keys), ["sync-ida"])
        self.assertEqual(harness.ledger.rejections, [])

    def test_an_existing_summary_still_appears_in_the_output_file(self):
        harness = SyncHarness(self, ITEM_PAGES, ACCOUNT_PAGES, summaries=[KIM])
        harness.run()
        self.assertEqual(harness.lines(SUMMARY_FILENAME), [IDA, KIM])

    def test_a_stale_existing_summary_is_left_alone_rather_than_collided_with(self):
        # The ledger holds numbers we would not compute. Posting would be a
        # 409, so the run must not post — and must not claim the stale row.
        stale = dict(KIM, total_points=999)
        harness = SyncHarness(self, ITEM_PAGES, ACCOUNT_PAGES, summaries=[stale])
        self.assertEqual(harness.run(), 0)
        self.assertEqual(harness.ledger.rejections, [])
        self.assertEqual(list(harness.ledger.keys), ["sync-ida"])
        self.assertEqual(harness.lines(SUMMARY_FILENAME), [IDA])

    def test_summaries_are_read_across_every_page(self):
        harness = SyncHarness(self, ITEM_PAGES, ACCOUNT_PAGES,
                              summaries=[KIM, IDA, {"owner": "rex"}])
        self.assertEqual(harness.run(), 0)
        self.assertEqual(harness.ledger.keys, {}, "nothing was missing")
        self.assertEqual(harness.lines(SUMMARY_FILENAME), [IDA, KIM])


class IdempotenceTest(unittest.TestCase):
    def test_a_second_run_writes_nothing_and_changes_no_file(self):
        harness = SyncHarness(self, ITEM_PAGES, ACCOUNT_PAGES)
        self.assertEqual(harness.run(), 0)
        first = harness.contents()
        self.assertEqual(len(harness.ledger.writes), 2)

        harness.ledger.requests = []
        harness.directory.requests = []
        self.assertEqual(harness.run(), 0)

        self.assertEqual(harness.ledger.writes, [], "a repeat run must not write")
        self.assertEqual(harness.contents(), first)

    def test_a_second_run_reproduces_the_files_even_if_out_was_cleared(self):
        harness = SyncHarness(self, ITEM_PAGES, ACCOUNT_PAGES)
        harness.run()
        first = harness.contents()

        for name in first:
            os.remove(os.path.join(harness.out_dir, name))
        harness.ledger.requests = []
        self.assertEqual(harness.run(), 0)

        self.assertEqual(harness.ledger.writes, [])
        self.assertEqual(harness.contents(), first)

    def test_a_run_that_hits_rate_limits_produces_the_same_files(self):
        plain = SyncHarness(self, ITEM_PAGES, ACCOUNT_PAGES)
        plain.run()
        limited = SyncHarness(self, ITEM_PAGES, ACCOUNT_PAGES,
                              directory_limits=(1, 2, 5), ledger_limits=(2, 4))
        self.assertEqual(limited.run(), 0)
        self.assertEqual(limited.contents(), plain.contents())
        self.assertEqual(limited.ledger.rejections, [])


class RejectedWriteTest(unittest.TestCase):
    def test_no_post_is_made_for_an_inactive_or_unknown_owner(self):
        harness = SyncHarness(self, ITEM_PAGES, ACCOUNT_PAGES)
        harness.run()
        self.assertEqual(sorted(harness.ledger.keys), ["sync-ida", "sync-kim"])
        for owner in ("rex", "vic", "nobody"):
            self.assertNotIn("sync-" + owner, harness.ledger.keys)

    def test_a_rejection_is_surfaced_rather_than_recorded_as_posted(self):
        # A ledger that rejects everything: the run must fail loudly instead
        # of writing a summary.jsonl line for a summary it never stored.
        class Rejecting(FakeLedger):
            def post_json(self, path, payload, header_map=None):
                self.count += 1
                self.requests.append(("POST", path))
                return response(422, {"error": "no"})

        harness = SyncHarness(self, ITEM_PAGES, ACCOUNT_PAGES)
        harness.ledger = Rejecting(ACCOUNT_PAGES)
        with self.assertRaises(SyncError):
            harness.run()
        self.assertFalse(os.path.exists(os.path.join(harness.out_dir,
                                                     SUMMARY_FILENAME)))


# --------------------------------------------------------------------------
# Against the real services
# --------------------------------------------------------------------------


class LiveServiceTest(unittest.TestCase):
    """The same run, against the services named by `config.json`."""

    @classmethod
    def setUpClass(cls):
        cls.config = load_config(CONFIG_PATH)
        try:
            HttpClient(cls.config.ledger_url, timeout=10.0).get("/v1/accounts",
                                                                {"page": 1})
        except HttpClientError as exc:  # pragma: no cover - environment dependent
            raise unittest.SkipTest("services are not reachable: %s" % exc)

    def setUp(self):
        self.out_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.out_dir, True)
        self.directory = RecordingClient(HttpClient(self.config.directory_url))
        self.ledger = RecordingClient(HttpClient(self.config.ledger_url))

    def sync(self):
        return run_sync(self.config, self.out_dir,
                        directory=ServiceClient("directory", client=self.directory),
                        ledger=ServiceClient("ledger", client=self.ledger))

    def lines(self, name):
        return read_lines(os.path.join(self.out_dir, name))

    def contents(self):
        return {name: read_text(os.path.join(self.out_dir, name))
                for name in OUTPUT_FILENAMES}

    def fetch_all(self, client, path, key):
        service = ServiceClient("probe", client=client)
        return list(iter_pages(service, path, key))

    def test_a_run_succeeds_and_writes_all_three_files(self):
        self.assertEqual(self.sync(), 0)
        for name in (ORPHANS_FILENAME, ERRORS_FILENAME, SUMMARY_FILENAME):
            self.assertTrue(os.path.isfile(os.path.join(self.out_dir, name)), name)

    def test_the_summaries_match_the_two_services(self):
        self.assertEqual(self.sync(), 0)
        accounts = {}
        for record in self.fetch_all(HttpClient(self.config.ledger_url),
                                     "/v1/accounts", "accounts"):
            account = parse_account(record)
            if account is not None:
                accounts.setdefault(account.owner, account)
        items = {}
        for record in self.fetch_all(HttpClient(self.config.directory_url),
                                     "/v1/items", "items"):
            item = parse_item(record)
            if item is not None:
                items.setdefault(item.id, item)

        expected = build_summaries(accounts, items)
        self.assertEqual(self.lines(SUMMARY_FILENAME),
                         [expected[owner].to_dict() for owner in sorted(expected)])

        # Every orphan belongs to nobody active; no malformed record is an orphan.
        active = {owner for owner, acct in accounts.items() if acct.active}
        orphan_ids = set()
        for record in self.lines(ORPHANS_FILENAME):
            item = parse_item(record)
            self.assertIsNotNone(item)
            self.assertNotIn(item.owner, active)
            self.assertNotIn(item.id, orphan_ids, "an orphan was written twice")
            orphan_ids.add(item.id)
        self.assertEqual(orphan_ids,
                         {i.id for i in items.values() if i.owner not in active})

    def test_every_error_record_really_is_malformed(self):
        self.assertEqual(self.sync(), 0)
        seen = []
        for entry in self.lines(ERRORS_FILENAME):
            self.assertIn(entry["service"], ("directory", "ledger"))
            parse = parse_item if entry["service"] == "directory" else parse_account
            self.assertIsNone(parse(entry["record"]),
                              "%r is well-formed" % (entry["record"],))
            self.assertNotIn(entry, seen, "an error was written twice")
            seen.append(entry)

    def test_the_ledger_never_rejects_a_write(self):
        self.assertEqual(self.sync(), 0)
        posts = [req for req in self.ledger.requests if req[0] == "POST"]
        owners = [payload["owner"] for _, _, payload in posts]
        self.assertEqual(len(owners), len(set(owners)), "an owner was posted twice")
        for line in self.lines(SUMMARY_FILENAME):
            self.assertEqual(sorted(line), ["done_items", "open_items", "owner",
                                            "total_points"])

    def test_a_second_run_issues_no_writes_and_leaves_the_files_identical(self):
        self.assertEqual(self.sync(), 0)
        before = self.contents()

        self.ledger = RecordingClient(HttpClient(self.config.ledger_url))
        self.directory = RecordingClient(HttpClient(self.config.directory_url))
        self.assertEqual(self.sync(), 0)

        self.assertEqual(self.ledger.writes, [], "a repeat run must issue no writes")
        self.assertEqual(self.contents(), before)

    def test_the_cli_runs_a_sync_and_exits_zero(self):
        self.assertEqual(main(["sync", "--config", CONFIG_PATH,
                               "--out-dir", self.out_dir]), 0)
        self.assertTrue(os.path.isfile(os.path.join(self.out_dir, SUMMARY_FILENAME)))


if __name__ == "__main__":
    unittest.main()
