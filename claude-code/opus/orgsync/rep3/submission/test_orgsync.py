"""Tests for the `orgsync sync` reconciliation.

Two layers, deliberately:

* **Against fakes.** In-memory stand-ins for the two services, with the same
  rules the real ones enforce — pagination, rate limits, idempotency keys, and
  a Ledger that refuses a summary for anyone but an active account. These pin
  the behaviour precisely, including the cases the live fixture may not
  happen to contain, and they run without a socket.
* **Against the live services.** The same tool, run end to end, checked
  against expectations recomputed from the raw pages by a separate, simpler
  reading of the rules. These catch a fake that has drifted from reality.

Nothing here ever provokes a rejected write against the live Ledger: whether
this tool declines to send an invalid summary is settled against the fake,
where a rejection can be observed without leaving one in the real service's
request log.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from orgsync import sync
from orgsync.__main__ import main
from orgsync.config import load_config
from orgsync.http_client import HttpClient, Response
from orgsync.service import (DEFAULT_RETRY_AFTER_SECONDS,
                             MAX_RETRY_AFTER_SECONDS, Service, ServiceError,
                             retry_after_seconds)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class FakeService:
    """An in-memory service: paginated collections and scriptable 429s.

    Implements the one method `Service` uses of `HttpClient`, so a `Service`
    can be pointed straight at it.
    """

    def __init__(self, page_size=2, retry_after="1"):
        self.collections = {}       # path -> (key, [records])
        self.page_size = page_size
        self.retry_after = retry_after
        self.rate_limit_at = set()  # 1-based request ordinals to refuse
        self.requests = []          # (method, path, params, payload, headers)
        self.count = 0

    def serve(self, path, key, records):
        self.collections[path] = (key, list(records))

    # -- transport --------------------------------------------------------

    def request(self, method, path, params=None, payload=None, headers=None):
        self.count += 1
        self.requests.append((method, path, dict(params or {}), payload,
                              dict(headers or {})))
        if self.count in self.rate_limit_at:
            return self._response(429, {"Retry-After": self.retry_after})
        if method == "GET":
            return self._page(path, params)
        return self.handle_post(path, payload, headers or {})

    def writes(self):
        """Every non-GET request received."""
        return [call for call in self.requests if call[0] != "GET"]

    def handle_post(self, path, payload, headers):
        return self._response(405, body={"error": "not allowed"})

    def _response(self, status, headers=None, body=None):
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        return Response(status, headers or {}, raw, "fake://" + str(status))

    def _page(self, path, params):
        if path not in self.collections:
            return self._response(404, body={"error": "no such collection"})
        key, records = self.collections[path]
        page = int((params or {}).get("page", 1))
        start = (page - 1) * self.page_size
        batch = records[start:start + self.page_size]
        more = len(records) > start + self.page_size
        return self._response(
            200, body={key: batch, "next_page": page + 1 if more else None})


class FakeLedger(FakeService):
    """A FakeService that also accepts summaries, the way the Ledger does."""

    SUMMARY_FIELDS = ("owner", "open_items", "done_items", "total_points")

    def __init__(self, accounts=(), summaries=(), **kwargs):
        super().__init__(**kwargs)
        self.serve("/v1/accounts", "accounts", accounts)
        self.serve("/v1/summaries", "summaries", summaries)
        self.keys = {}  # Idempotency-Key -> body accepted under it

    def active_owners(self):
        return {account["owner"] for account in self.collections["/v1/accounts"][1]
                if isinstance(account, dict)
                and isinstance(account.get("owner"), str)
                and isinstance(account.get("id"), str)
                and account.get("active") is True}

    def handle_post(self, path, payload, headers):
        if path != "/v1/summaries":
            return self._response(404, body={"error": "no such collection"})
        if not self._well_formed(payload):
            return self._response(400, body={"error": "bad body"})
        if payload["owner"] not in self.active_owners():
            return self._response(422, body={"error": "not an active owner"})
        key = headers.get("Idempotency-Key")
        if key in self.keys:
            same = self.keys[key] == payload
            return self._response(200 if same else 409, body=self.keys[key])
        self.keys[key] = dict(payload)
        self.collections["/v1/summaries"][1].append(dict(payload))
        return self._response(201, body=payload)

    def _well_formed(self, payload):
        if not isinstance(payload, dict):
            return False
        if not isinstance(payload.get("owner"), str):
            return False
        return all(isinstance(payload.get(field), int)
                   and not isinstance(payload.get(field), bool)
                   for field in self.SUMMARY_FIELDS[1:])


def wire(fake, name="fake", **kwargs):
    """A `Service` talking to `fake`, sleeping instantly unless told otherwise."""
    kwargs.setdefault("sleep", lambda seconds: None)
    return Service(name, fake, **kwargs)


def lines(path):
    """The JSON objects in a .jsonl file, as a list."""
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def as_set(records):
    """A set of records, compared by content, so file order cannot matter."""
    return {json.dumps(record, sort_keys=True) for record in records}


def read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


# --------------------------------------------------------------------------
# Well-formedness
# --------------------------------------------------------------------------

class ParseItemTest(unittest.TestCase):
    GOOD = {"id": "i-1", "owner": "kim", "status": "open", "points": 3}

    def test_accepts_a_well_formed_item(self):
        item = sync.parse_item(self.GOOD)
        self.assertEqual((item.id, item.owner, item.status, item.points),
                         ("i-1", "kim", "open", 3))

    def test_extra_fields_never_make_an_item_malformed(self):
        raw = dict(self.GOOD, note="hello", tags=[1, 2])
        self.assertIsNotNone(sync.parse_item(raw))

    def test_zero_points_is_in_range(self):
        self.assertIsNotNone(sync.parse_item(dict(self.GOOD, points=0)))

    def test_rejects_malformed_items(self):
        cases = {
            "missing id": {k: v for k, v in self.GOOD.items() if k != "id"},
            "missing owner": {k: v for k, v in self.GOOD.items() if k != "owner"},
            "missing status": {k: v for k, v in self.GOOD.items() if k != "status"},
            "missing points": {k: v for k, v in self.GOOD.items() if k != "points"},
            "id not a string": dict(self.GOOD, id=1),
            "owner not a string": dict(self.GOOD, owner=None),
            "points as a string": dict(self.GOOD, points="3"),
            "points as a float": dict(self.GOOD, points=3.5),
            "points as a bool": dict(self.GOOD, points=True),
            "points negative": dict(self.GOOD, points=-1),
            "unknown status": dict(self.GOOD, status="closed"),
            "status not a string": dict(self.GOOD, status=1),
            "null": None,
            "a string": "item",
            "a list": [self.GOOD],
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(sync.parse_item(raw))


class ParseAccountTest(unittest.TestCase):
    GOOD = {"id": "a-1", "owner": "kim", "active": True}

    def test_accepts_well_formed_accounts_either_way_round(self):
        for active in (True, False):
            account = sync.parse_account(dict(self.GOOD, active=active))
            self.assertEqual((account.id, account.owner, account.active),
                             ("a-1", "kim", active))

    def test_extra_fields_never_make_an_account_malformed(self):
        self.assertIsNotNone(sync.parse_account(dict(self.GOOD, tier="gold")))

    def test_rejects_malformed_accounts(self):
        cases = {
            "missing id": {"owner": "kim", "active": True},
            "missing owner": {"id": "a-1", "active": True},
            "missing active": {"id": "a-1", "owner": "kim"},
            "active as a string": dict(self.GOOD, active="yes"),
            "active as an int": dict(self.GOOD, active=1),
            "active as null": dict(self.GOOD, active=None),
            "owner not a string": dict(self.GOOD, owner=7),
            "null": None,
            "a list": ["a-1", "kim", True],
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(sync.parse_account(raw))


# --------------------------------------------------------------------------
# Rate limits
# --------------------------------------------------------------------------

class RetryAfterTest(unittest.TestCase):
    def test_reads_delay_seconds(self):
        self.assertEqual(retry_after_seconds({"Retry-After": "2"}), 2.0)
        self.assertEqual(retry_after_seconds({"Retry-After": " 0.5 "}), 0.5)

    def test_falls_back_when_absent_or_unreadable(self):
        for headers in ({}, None, {"Retry-After": "soon"}):
            with self.subTest(headers=headers):
                self.assertEqual(retry_after_seconds(headers),
                                 DEFAULT_RETRY_AFTER_SECONDS)

    def test_clamps_out_of_range_values(self):
        self.assertEqual(retry_after_seconds({"Retry-After": "-5"}), 0.0)
        self.assertEqual(retry_after_seconds({"Retry-After": "999999"}),
                         MAX_RETRY_AFTER_SECONDS)


class ServiceRateLimitTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeService(retry_after="2")
        self.fake.serve("/v1/items", "items", [{"id": "i-1"}])
        self.slept = []
        self.now = [1000.0]

    def _service(self):
        return wire(self.fake, sleep=self._sleep, clock=lambda: self.now[0])

    def _sleep(self, seconds):
        self.slept.append(seconds)
        self.now[0] += seconds

    def test_a_429_is_waited_out_and_the_request_retried(self):
        self.fake.rate_limit_at = {1}
        records = self._service().fetch_all("/v1/items", "items")
        self.assertEqual(records, [{"id": "i-1"}])
        self.assertEqual(len(self.slept), 1)
        self.assertGreaterEqual(self.slept[0], 2.0)

    def test_the_wait_applies_to_the_next_request_whatever_it_is(self):
        """A 429 silences the service, not merely the refused request."""
        self.fake.serve("/v1/items", "items", [{"id": "i-1"}, {"id": "i-2"},
                                               {"id": "i-3"}])
        self.fake.rate_limit_at = {1}
        service = self._service()
        service.fetch_all("/v1/items", "items")
        # One wait, before the retry; page 2 follows with no further pause.
        self.assertEqual(len(self.slept), 1)
        self.assertEqual([call[2].get("page") for call in self.fake.requests],
                         [1, 1, 2])

    def test_repeated_429s_are_each_waited_out(self):
        self.fake.rate_limit_at = {1, 2, 3}
        self._service().fetch_all("/v1/items", "items")
        self.assertEqual(len(self.slept), 3)

    def test_a_429_is_never_data_and_never_ends_the_run(self):
        self.fake.rate_limit_at = set(range(1, 200))
        with self.assertRaises(ServiceError):
            self._service().fetch_all("/v1/items", "items")

    def test_the_other_service_is_not_silenced(self):
        """Each service keeps its own deadline."""
        other = FakeService()
        other.serve("/v1/accounts", "accounts", [{"id": "a-1"}])
        self.fake.rate_limit_at = {1}
        service, unaffected = self._service(), wire(other, "other")
        service.fetch_all("/v1/items", "items")
        before = len(self.slept)
        unaffected.fetch_all("/v1/accounts", "accounts")
        self.assertEqual(len(self.slept), before)


class ServicePaginationTest(unittest.TestCase):
    def test_follows_next_page_until_null(self):
        fake = FakeService(page_size=2)
        records = [{"id": "i-%d" % n} for n in range(5)]
        fake.serve("/v1/items", "items", records)
        self.assertEqual(wire(fake).fetch_all("/v1/items", "items"), records)
        self.assertEqual([call[2]["page"] for call in fake.requests], [1, 2, 3])

    def test_an_empty_collection_is_one_empty_page(self):
        fake = FakeService()
        fake.serve("/v1/items", "items", [])
        self.assertEqual(wire(fake).fetch_all("/v1/items", "items"), [])

    def test_a_non_200_is_an_error_not_an_empty_collection(self):
        fake = FakeService()
        with self.assertRaises(ServiceError):
            wire(fake).fetch_all("/v1/absent", "items")


# --------------------------------------------------------------------------
# The reconciliation, against fakes
# --------------------------------------------------------------------------

ACCOUNTS = [
    {"id": "a-1", "owner": "kim", "active": True},
    {"id": "a-2", "owner": "lee", "active": True},
    {"id": "a-3", "owner": "moe", "active": False},       # inactive: no summary
    {"id": "a-4", "owner": "nan", "active": "yes"},       # malformed: not active
    {"id": "a-5", "owner": "pat", "active": True},        # active, no items
    {"owner": "qi", "active": True},                      # malformed: no id
]

ITEMS = [
    {"id": "i-1", "owner": "kim", "status": "open", "points": 3},
    {"id": "i-2", "owner": "kim", "status": "open", "points": 4},
    {"id": "i-3", "owner": "kim", "status": "done", "points": 2},
    {"id": "i-1", "owner": "kim", "status": "open", "points": 3},   # redelivered
    {"id": "i-4", "owner": "lee", "status": "done", "points": 0},
    {"id": "i-5", "owner": "moe", "status": "open", "points": 9},   # orphan
    {"id": "i-6", "owner": "nan", "status": "open", "points": 1},   # orphan
    {"id": "i-7", "owner": "zed", "status": "open", "points": 5},   # orphan
    {"id": "i-8", "owner": "kim", "status": "open"},                # malformed
    {"id": "i-9", "owner": "kim", "status": "open", "points": "2"},  # malformed
    "not even an object",                                           # malformed
]

EXPECTED_SUMMARIES = [
    {"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9},
    {"owner": "lee", "open_items": 0, "done_items": 1, "total_points": 0},
    {"owner": "pat", "open_items": 0, "done_items": 0, "total_points": 0},
]

EXPECTED_ORPHANS = [ITEMS[5], ITEMS[6], ITEMS[7]]

EXPECTED_ERRORS = [
    {"service": "ledger", "record": ACCOUNTS[3]},
    {"service": "ledger", "record": ACCOUNTS[5]},
    {"service": "directory", "record": ITEMS[8]},
    {"service": "directory", "record": ITEMS[9]},
    {"service": "directory", "record": ITEMS[10]},
]


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self.directory = FakeService(page_size=3)
        self.directory.serve("/v1/items", "items", ITEMS)
        self.ledger = FakeLedger(accounts=ACCOUNTS, page_size=3)

    def run_once(self):
        return sync.reconcile(wire(self.directory, "directory"),
                              wire(self.ledger, "ledger"))

    def test_one_summary_per_active_well_formed_account(self):
        result = self.run_once()
        self.assertEqual([summary.to_dict() for summary in result.required],
                         EXPECTED_SUMMARIES)

    def test_orphans_are_the_items_with_no_active_owner(self):
        self.assertEqual(as_set(self.run_once().orphans), as_set(EXPECTED_ORPHANS))

    def test_malformed_records_are_reported_against_their_service(self):
        self.assertEqual(as_set(self.run_once().errors), as_set(EXPECTED_ERRORS))

    def test_a_malformed_account_does_not_make_its_owner_active(self):
        result = self.run_once()
        owners = {summary.owner for summary in result.required}
        self.assertNotIn("nan", owners)
        self.assertNotIn("qi", owners)

    def test_a_redelivered_item_is_counted_once_and_orphaned_once(self):
        self.directory.serve("/v1/items", "items", ITEMS + [ITEMS[0], ITEMS[7]])
        result = self.run_once()
        self.assertEqual([summary.to_dict() for summary in result.required],
                         EXPECTED_SUMMARIES)
        self.assertEqual(len(result.orphans), len(EXPECTED_ORPHANS))

    def test_a_redelivered_malformed_record_is_written_once(self):
        self.directory.serve("/v1/items", "items", ITEMS + [ITEMS[8]])
        self.assertEqual(as_set(self.run_once().errors), as_set(EXPECTED_ERRORS))
        self.assertEqual(len(self.run_once().errors), len(EXPECTED_ERRORS))

    def test_the_same_junk_from_both_services_is_two_errors(self):
        junk = {"id": "x", "owner": "kim"}
        self.directory.serve("/v1/items", "items", ITEMS + [junk])
        self.ledger.serve("/v1/accounts", "accounts", ACCOUNTS + [junk])
        errors = self.run_once().errors
        self.assertIn({"service": "directory", "record": junk}, errors)
        self.assertIn({"service": "ledger", "record": junk}, errors)

    def test_every_missing_summary_is_posted_under_its_owner_key(self):
        result = self.run_once()
        self.assertEqual([summary.to_dict() for summary in result.posted],
                         EXPECTED_SUMMARIES)
        posts = self.ledger.writes()
        self.assertEqual([call[4]["Idempotency-Key"] for call in posts],
                         ["sync-kim", "sync-lee", "sync-pat"])
        self.assertEqual([call[3] for call in posts], EXPECTED_SUMMARIES)

    def test_a_summary_the_ledger_already_holds_is_not_posted_again(self):
        self.ledger.serve("/v1/summaries", "summaries", [EXPECTED_SUMMARIES[0]])
        result = self.run_once()
        self.assertEqual([summary.owner for summary in result.posted],
                         ["lee", "pat"])

    def test_a_stale_summary_is_left_alone_rather_than_rewritten(self):
        """Posting over it would be a 409; the ledger has to be asked, not told."""
        stale = dict(EXPECTED_SUMMARIES[0], total_points=999)
        self.ledger.serve("/v1/summaries", "summaries", [stale])
        result = self.run_once()
        self.assertEqual([summary.owner for summary in result.posted],
                         ["lee", "pat"])
        self.assertEqual(result.rejections, [])

    def test_no_write_is_ever_rejected(self):
        result = self.run_once()
        self.assertEqual(result.rejections, [])
        for call in self.ledger.writes():
            with self.subTest(key=call[4]["Idempotency-Key"]):
                self.assertEqual(self.ledger.keys.get(call[4]["Idempotency-Key"]),
                                 call[3])

    def test_rate_limits_do_not_change_the_outcome(self):
        self.directory.rate_limit_at = {1, 2, 5}
        self.ledger.rate_limit_at = {1, 3, 4, 6}
        result = self.run_once()
        self.assertEqual([summary.to_dict() for summary in result.posted],
                         EXPECTED_SUMMARIES)
        self.assertEqual(as_set(result.orphans), as_set(EXPECTED_ORPHANS))
        self.assertEqual(as_set(result.errors), as_set(EXPECTED_ERRORS))

    def test_a_second_run_writes_nothing(self):
        self.run_once()
        writes = len(self.ledger.writes())
        second = self.run_once()
        self.assertEqual(second.posted, [])
        self.assertEqual(second.rejections, [])
        self.assertEqual(len(self.ledger.writes()), writes)

    def test_an_inactive_owner_is_never_posted_for(self):
        """The Ledger answers 422 for these; the tool must not ask."""
        posted_owners = {summary.owner for summary in self.run_once().posted}
        self.assertEqual(posted_owners & {"moe", "nan", "qi", "zed"}, set())


class WriteOutputsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = os.path.join(self.tmp, "out")
        self.directory = FakeService(page_size=3)
        self.directory.serve("/v1/items", "items", ITEMS)
        self.ledger = FakeLedger(accounts=ACCOUNTS, page_size=3)

    def sync_once(self):
        result = sync.reconcile(wire(self.directory, "directory"),
                                wire(self.ledger, "ledger"))
        sync.write_outputs(self.out, result)
        return result

    def files(self):
        return {name: lines(os.path.join(self.out, name))
                for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")}

    def test_creates_the_out_directory_and_all_three_files(self):
        self.assertFalse(os.path.exists(self.out))
        self.sync_once()
        for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl"):
            self.assertTrue(os.path.isfile(os.path.join(self.out, name)), name)

    def test_files_hold_exactly_the_expected_records(self):
        self.sync_once()
        files = self.files()
        self.assertEqual(as_set(files["summary.jsonl"]), as_set(EXPECTED_SUMMARIES))
        self.assertEqual(as_set(files["orphans.jsonl"]), as_set(EXPECTED_ORPHANS))
        self.assertEqual(as_set(files["errors.jsonl"]), as_set(EXPECTED_ERRORS))

    def test_a_second_run_leaves_the_files_byte_identical(self):
        self.sync_once()
        with open(os.path.join(self.out, "summary.jsonl"), "rb") as handle:
            first = handle.read()
        self.sync_once()
        with open(os.path.join(self.out, "summary.jsonl"), "rb") as handle:
            self.assertEqual(handle.read(), first)

    def test_the_summary_file_survives_being_deleted_between_runs(self):
        """It records what the Ledger holds, not what this process remembers."""
        self.sync_once()
        expected = self.files()
        shutil.rmtree(self.out)
        self.sync_once()
        self.assertEqual(as_set(self.files()["summary.jsonl"]),
                         as_set(expected["summary.jsonl"]))

    def test_each_record_appears_exactly_once(self):
        self.sync_once()
        for name, records in self.files().items():
            with self.subTest(file=name):
                self.assertEqual(len(records), len(as_set(records)))


# --------------------------------------------------------------------------
# Against the live services
# --------------------------------------------------------------------------

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "config.json")
LIVE = None


def setUpModule():
    """Load the config and make sure both services answer, or skip."""
    global LIVE
    try:
        config = load_config(CONFIG_PATH)
    except Exception as exc:                      # pragma: no cover - env issue
        raise unittest.SkipTest("no usable config.json: %s" % exc)
    for url in (config.directory_url, config.ledger_url):
        try:
            urllib.request.urlopen(url + "/v1/nothing", timeout=5).close()
        except urllib.error.HTTPError as exc:
            exc.close()                           # answered; that is all we need
        except OSError as exc:                    # pragma: no cover - env issue
            raise unittest.SkipTest("%s is not answering: %s" % (url, exc))
    LIVE = config


def live_get(url):
    """GET `url` as JSON, waiting out rate limits the way the tool does."""
    for _ in range(20):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code != 429:
                raise
            delay = exc.headers.get("Retry-After")
            time.sleep(float(delay) if delay else 1.0)
    raise AssertionError("still rate limited after 20 attempts: %s" % url)


def live_all(base, path, key):
    """Every record of a live collection, following `next_page`."""
    records, page = [], 1
    while page is not None:
        body = live_get("%s%s?page=%d" % (base, path, page))
        records.extend(body.get(key, []))
        page = body.get("next_page")
    return records


def reference_expectations(raw_items, raw_accounts):
    """The three files, worked out again from the raw pages.

    An independent reading of the rules — `type(x) is T` rather than
    `isinstance`, an explicit dict of tallies — so that a shared misreading of
    the spec has to be made twice to go unnoticed.
    """
    active, errors = set(), []
    for account in raw_accounts:
        if (type(account) is dict and type(account.get("id")) is str
                and type(account.get("owner")) is str
                and type(account.get("active")) is bool):
            if account["active"]:
                active.add(account["owner"])
        else:
            errors.append({"service": "ledger", "record": account})

    seen, orphans = {}, []
    tallies = {owner: {"owner": owner, "open_items": 0, "done_items": 0,
                       "total_points": 0} for owner in active}
    for item in raw_items:
        if not (type(item) is dict and type(item.get("id")) is str
                and type(item.get("owner")) is str
                and item.get("status") in ("open", "done")
                and type(item.get("points")) is int and item["points"] >= 0):
            errors.append({"service": "directory", "record": item})
            continue
        if item["id"] in seen:
            continue
        seen[item["id"]] = item
        if item["owner"] not in active:
            orphans.append(item)
            continue
        tallies[item["owner"]]["%s_items" % item["status"]] += 1
        tallies[item["owner"]]["total_points"] += item["points"]
    return list(tallies.values()), orphans, errors


class LiveSyncTest(unittest.TestCase):
    """End to end against the two services the environment provides."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.out = os.path.join(cls.tmp, "out")
        cls.raw_items = live_all(LIVE.directory_url, "/v1/items", "items")
        cls.raw_accounts = live_all(LIVE.ledger_url, "/v1/accounts", "accounts")
        cls.summaries, cls.orphans, cls.errors = reference_expectations(
            cls.raw_items, cls.raw_accounts)
        cls.first = main(["sync", "--config", CONFIG_PATH, "--out-dir", cls.out])
        cls.snapshot = {name: read_bytes(os.path.join(cls.out, name))
                        for name in ("summary.jsonl", "orphans.jsonl",
                                     "errors.jsonl")}
        cls.second = main(["sync", "--config", CONFIG_PATH, "--out-dir", cls.out])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def file(self, name):
        return lines(os.path.join(self.out, name))

    def test_both_runs_exit_zero(self):
        self.assertEqual((self.first, self.second), (0, 0))

    def test_orphans_match_an_independent_reading_of_the_rules(self):
        self.assertEqual(as_set(self.file("orphans.jsonl")), as_set(self.orphans))

    def test_errors_match_an_independent_reading_of_the_rules(self):
        self.assertEqual(as_set(self.file("errors.jsonl")),
                         as_set(dedupe(self.errors)))

    def test_the_ledger_ends_up_holding_every_required_summary(self):
        posted = live_all(LIVE.ledger_url, "/v1/summaries", "summaries")
        by_owner = {summary["owner"]: summary for summary in posted}
        for expected in self.summaries:
            with self.subTest(owner=expected["owner"]):
                self.assertIn(expected["owner"], by_owner)
                actual = by_owner[expected["owner"]]
                for field, value in expected.items():
                    self.assertEqual(actual.get(field), value, field)

    def test_summary_file_records_the_summaries_this_tool_posted(self):
        recorded = self.file("summary.jsonl")
        self.assertLessEqual(as_set(recorded), as_set(self.summaries))
        self.assertEqual({record["owner"] for record in recorded},
                         {summary["owner"] for summary in self.summaries})

    def test_no_summary_is_written_for_an_inactive_or_unknown_owner(self):
        summarised = {record["owner"] for record in self.file("summary.jsonl")}
        orphaned = {item["owner"] for item in self.file("orphans.jsonl")}
        self.assertEqual(summarised & orphaned, set())

    def test_every_record_appears_exactly_once(self):
        for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl"):
            with self.subTest(file=name):
                records = self.file(name)
                self.assertEqual(len(records), len(as_set(records)))

    def test_the_second_run_left_the_files_unchanged(self):
        for name, content in self.snapshot.items():
            with self.subTest(file=name):
                with open(os.path.join(self.out, name), "rb") as handle:
                    self.assertEqual(handle.read(), content)

    def test_a_further_run_would_post_nothing(self):
        """Everything the Ledger needs is already there, so there is no write."""
        result = sync.reconcile(
            Service("directory", HttpClient(LIVE.directory_url)),
            Service("ledger", HttpClient(LIVE.ledger_url)))
        self.assertEqual(result.posted, [])
        self.assertEqual(result.rejections, [])


def dedupe(records):
    """`records` with content-duplicates removed, order preserved."""
    kept = {}
    for record in records:
        kept.setdefault(json.dumps(record, sort_keys=True), record)
    return list(kept.values())


if __name__ == "__main__":
    unittest.main()
