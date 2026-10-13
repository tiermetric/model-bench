"""Behavioral tests for `orgsync sync`.

Two layers:

* Scripted tests drive `run_sync_with_clients` against in-memory fakes of the
  Directory and the Ledger, so every rule — validation, deduplication,
  orphaning, posting only what is missing, 429 handling, idempotency — is
  checked without a socket.
* Live tests talk to the real local services named in `config.json`, using
  test-side re-implementations of the rules (not orgsync's own helpers) to
  compute what the answers must be.
"""

import email.message
import json
import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request

from orgsync.config import load_config
from orgsync.http_client import HttpClient, Response
from orgsync.models import Item
from orgsync.sync import (
    RateLimitedClient,
    fetch_all_pages,
    parse_account,
    parse_item,
    run_sync_with_clients,
    summarize,
)


def make_headers(pairs=None):
    message = email.message.Message()
    for key, value in (pairs or {}).items():
        message[key] = value
    return message


def response(status, body, headers=None):
    if not isinstance(body, bytes):
        body = json.dumps(body).encode("utf-8")
    return Response(status, make_headers(headers), body, "http://fake.test")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeClock:
    """A monotonic clock that only advances when `sleep` is called."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class StaticClock:
    """A clock that never advances: every wait shows up as a sleep."""

    def __init__(self):
        self.slept = []

    def monotonic(self):
        return 1000.0

    def sleep(self, seconds):
        self.slept.append(seconds)


class FakeService:
    """An in-memory stand-in for one service, page-aware.

    `pages` maps path -> list of page bodies, delivered in order. `script`
    maps path -> queue of responses handed out first on a GET (used to inject
    429s); `post_script` is the same for POSTs, whatever the path. POSTs are
    recorded and by default accepted (201). `echo_posts_into=(path, key)`
    makes GETs of that path include the bodies posted so far, so a second
    run sees its own earlier writes.
    """

    def __init__(self, pages=None, script=None, post_statuses=None,
                 echo_posts_into=None, post_script=None):
        self.pages = dict(pages or {})
        self.script = {path: list(q) for path, q in (script or {}).items()}
        self.post_script = list(post_script or [])
        self.post_statuses = list(post_statuses or [])
        self.echo_posts_into = echo_posts_into
        self.posts = []
        self.gets = []

    def request(self, method, path, params=None, payload=None, headers=None):
        queue = self.script.get(path)
        if queue:
            early = queue.pop(0)
            if isinstance(early, Exception):
                raise early
            return early
        if method.upper() == "GET":
            return self._get(path, params)
        return self._post(path, payload, headers)

    def _get(self, path, params):
        self.gets.append((path, dict(params or {})))
        pages = self.pages[path]
        page = int((params or {}).get("page", 1))
        if not 1 <= page <= len(pages):
            return response(200, {"next_page": None})
        body = pages[page - 1]
        if self.echo_posts_into and path == self.echo_posts_into[0] and page == 1:
            key = self.echo_posts_into[1]
            body = dict(body)
            body[key] = list(body[key]) + [post["payload"] for post in self.posts]
        return response(200, body)

    def _post(self, path, payload, headers):
        self.posts.append({"path": path, "payload": payload,
                           "headers": dict(headers or {})})
        if self.post_script:
            early = self.post_script.pop(0)
            if isinstance(early, Exception):
                raise early
            return early
        status = self.post_statuses.pop(0) if self.post_statuses else 201
        return response(status, {"stored": True})

    def get(self, path, params=None):
        return self.request("GET", path, params=params)

    def post_json(self, path, payload, headers=None):
        return self.request("POST", path, payload=payload, headers=headers)


# ---------------------------------------------------------------------------
# Unit tests: record validation
# ---------------------------------------------------------------------------


class ParseItemTest(unittest.TestCase):
    def test_accepts_well_formed_record_and_ignores_extra_fields(self):
        record = {"id": "i-1", "owner": "kim", "status": "open", "points": 3,
                  "note": "extra"}
        self.assertEqual(parse_item(record),
                         Item(id="i-1", owner="kim", status="open", points=3))

    def test_rejects_missing_fields(self):
        base = {"id": "i-1", "owner": "kim", "status": "open", "points": 3}
        for key in base:
            with self.subTest(missing=key):
                record = dict(base)
                del record[key]
                self.assertIsNone(parse_item(record))

    def test_rejects_wrong_json_types(self):
        bad = [
            {"id": 7, "owner": "kim", "status": "open", "points": 3},
            {"id": "i-1", "owner": 7, "status": "open", "points": 3},
            {"id": "i-1", "owner": "kim", "status": "OPEN", "points": 3},
            {"id": "i-1", "owner": "kim", "status": "done", "points": "3"},
            {"id": "i-1", "owner": "kim", "status": "done", "points": 3.0},
            {"id": "i-1", "owner": "kim", "status": "done", "points": True},
            {"id": "i-1", "owner": "kim", "status": "done", "points": None},
        ]
        for record in bad:
            with self.subTest(record=record):
                self.assertIsNone(parse_item(record))

    def test_rejects_negative_points_and_non_dict_records(self):
        self.assertIsNone(parse_item({"id": "i", "owner": "k", "status": "open",
                                      "points": -1}))
        self.assertIsNone(parse_item(["not", "a", "dict"]))
        self.assertIsNone(parse_item("string"))

    def test_zero_points_is_well_formed(self):
        record = {"id": "i", "owner": "k", "status": "done", "points": 0}
        self.assertEqual(parse_item(record).points, 0)


class ParseAccountTest(unittest.TestCase):
    def test_accepts_well_formed_record_and_ignores_extra_fields(self):
        record = {"id": "a-1", "owner": "kim", "active": True, "tier": "gold"}
        account = parse_account(record)
        self.assertEqual((account.id, account.owner, account.active),
                         ("a-1", "kim", True))

    def test_rejects_missing_fields(self):
        base = {"id": "a-1", "owner": "kim", "active": True}
        for key in base:
            with self.subTest(missing=key):
                record = dict(base)
                del record[key]
                self.assertIsNone(parse_account(record))

    def test_active_must_be_a_json_boolean(self):
        for bad in ("yes", 1, 0, None, []):
            with self.subTest(active=bad):
                self.assertIsNone(
                    parse_account({"id": "a", "owner": "k", "active": bad}))
        self.assertIsNone(parse_account({"id": 1, "owner": "k", "active": True}))
        self.assertIsNone(parse_account({"id": "a", "owner": 1, "active": True}))


class SummarizeTest(unittest.TestCase):
    def test_worked_example(self):
        items = [Item("i1", "kim", "open", 3), Item("i2", "kim", "open", 4),
                 Item("i3", "kim", "done", 2)]
        self.assertEqual(summarize("kim", items).to_dict(),
                         {"owner": "kim", "open_items": 2, "done_items": 1,
                          "total_points": 9})

    def test_no_items_gives_all_zeros(self):
        self.assertEqual(summarize("nim", []).to_dict(),
                         {"owner": "nim", "open_items": 0, "done_items": 0,
                          "total_points": 0})


# ---------------------------------------------------------------------------
# Unit tests: pagination
# ---------------------------------------------------------------------------


class FetchAllPagesTest(unittest.TestCase):
    def test_follows_next_page_until_null(self):
        pages = [{"items": [1], "next_page": 2},
                 {"items": [2, 3], "next_page": 3},
                 {"items": [4], "next_page": None}]
        client = FakeService(pages={"/v1/items": pages})
        self.assertEqual(fetch_all_pages(client, "/v1/items", "items"),
                         [1, 2, 3, 4])
        self.assertEqual([g[1].get("page") for g in client.gets], [1, 2, 3])

    def test_stops_when_next_page_repeats_a_visited_page(self):
        pages = [{"items": [1], "next_page": 2},
                 {"items": [2], "next_page": 2}]
        client = FakeService(pages={"/v1/items": pages})
        self.assertEqual(fetch_all_pages(client, "/v1/items", "items"), [1, 2])

    def test_non_200_raises(self):
        client = FakeService(script={"/v1/items": [response(500, {})]})
        with self.assertRaises(Exception):
            fetch_all_pages(client, "/v1/items", "items")


# ---------------------------------------------------------------------------
# Unit tests: rate limiting
# ---------------------------------------------------------------------------


class ScriptedOpener:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def open(self, request, timeout=None):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeHTTPResponse:
    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self.headers = make_headers(headers)
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class RateLimitedClientTest(unittest.TestCase):
    def make_client(self, clock, *outcomes):
        http = HttpClient("http://fake.test", opener=ScriptedOpener(*outcomes))
        return RateLimitedClient(http, sleep=clock.sleep,
                                 monotonic=clock.monotonic)

    def test_retries_a_429_and_honours_retry_after(self):
        clock = FakeClock()
        client = self.make_client(
            clock,
            FakeHTTPResponse(429, b"", {"Retry-After": "2"}),
            FakeHTTPResponse(200, b'{"ok": true}'))
        result = client.get("/v1/things")
        self.assertEqual(result.status, 200)
        self.assertEqual(result.json(), {"ok": True})
        self.assertEqual(len(clock.slept), 1)
        self.assertGreaterEqual(clock.slept[0], 2.0)

    def test_later_requests_wait_out_the_cooldown_too(self):
        clock = StaticClock()
        client = self.make_client(
            clock,
            FakeHTTPResponse(429, b"", {"Retry-After": "3"}),
            FakeHTTPResponse(200, b"{}"),
            FakeHTTPResponse(200, b"{}"))
        client.get("/a")
        client.get("/b")
        self.assertEqual(len(clock.slept), 2)
        for seconds in clock.slept:
            self.assertGreaterEqual(seconds, 3.0)

    def test_missing_or_bad_retry_after_falls_back_to_a_default(self):
        for header in (None, "soon"):
            with self.subTest(retry_after=header):
                clock = FakeClock()
                headers = {"Retry-After": header} if header else None
                client = self.make_client(
                    clock,
                    FakeHTTPResponse(429, b"{}", headers),
                    FakeHTTPResponse(200, b"{}"))
                client.get("/a")
                self.assertEqual(len(clock.slept), 1)
                self.assertGreaterEqual(clock.slept[0], 1.0)

    def test_a_429_body_is_never_returned_as_data(self):
        clock = FakeClock()
        client = self.make_client(
            clock,
            FakeHTTPResponse(429, b'{"items": "poison"}', {"Retry-After": "0"}),
            FakeHTTPResponse(200, b'{"items": [1], "next_page": null}'))
        records = fetch_all_pages(client, "/v1/items", "items")
        self.assertEqual(records, [1])


# ---------------------------------------------------------------------------
# Scripted end-to-end: run_sync_with_clients over fakes
# ---------------------------------------------------------------------------


def fixture_services():
    """A Directory/Ledger pair covering every rule at once."""
    accounts = [
        {"id": "acct-1", "owner": "amara", "active": True},
        {"id": "acct-2", "owner": "bo", "active": False},          # inactive
        {"id": "acct-3", "owner": "cyd", "active": "yes"},          # malformed
        {"id": "acct-4", "owner": "dee", "active": True, "x": 1},   # extra ok
        {"id": "acct-5", "owner": "eli", "active": True},
    ]
    items = [
        {"id": "i-1", "owner": "amara", "status": "open", "points": 3},
        {"id": "i-2", "owner": "amara", "status": "done", "points": 2},
        {"id": "i-3", "owner": "bo", "status": "open", "points": 9},   # orphan
        {"id": "i-4", "owner": "cyd", "status": "done", "points": 1},  # orphan
        {"id": "i-5", "owner": "dee", "status": "open", "points": 0},
        {"id": "i-6", "owner": "eli", "status": "open"},              # malformed
        {"id": "i-7", "owner": "eli", "status": "open", "points": -2},  # malformed
        {"id": "i-8", "owner": "ghost", "status": "open", "points": 4},  # orphan
    ]
    items_page_two = [
        {"id": "i-1", "owner": "amara", "status": "open", "points": 3},  # dup
        {"id": "i-9", "owner": "eli", "status": "done", "points": 5},
    ]
    directory = FakeService(pages={
        "/v1/items": [
            {"items": items, "next_page": 2},
            {"items": items_page_two, "next_page": None},
        ],
    })
    ledger = FakeService(
        pages={
            "/v1/accounts": [{"accounts": accounts, "next_page": None}],
            "/v1/summaries": [{"summaries": [
                {"owner": "dee", "open_items": 0, "done_items": 0,
                 "total_points": 0},
            ], "next_page": None}],
        },
        echo_posts_into=("/v1/summaries", "summaries"),
    )
    return directory, ledger


EXPECTED_POSTS = [
    {"headers": {"Idempotency-Key": "sync-amara"},
     "payload": {"owner": "amara", "open_items": 1, "done_items": 1,
                 "total_points": 5}},
    {"headers": {"Idempotency-Key": "sync-eli"},
     "payload": {"owner": "eli", "open_items": 0, "done_items": 1,
                 "total_points": 5}},
]

EXPECTED_ORPHANS = [
    {"id": "i-3", "owner": "bo", "status": "open", "points": 9},
    {"id": "i-4", "owner": "cyd", "status": "done", "points": 1},
    {"id": "i-8", "owner": "ghost", "status": "open", "points": 4},
]

EXPECTED_ERRORS = [
    {"service": "ledger", "record": {"id": "acct-3", "owner": "cyd",
                                     "active": "yes"}},
    {"service": "directory", "record": {"id": "i-6", "owner": "eli",
                                        "status": "open"}},
    {"service": "directory", "record": {"id": "i-7", "owner": "eli",
                                        "status": "open", "points": -2}},
]


def read_jsonl(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class ScriptedSyncTest(unittest.TestCase):
    def setUp(self):
        self.directory, self.ledger = fixture_services()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = self.tmp.name

    def path(self, name):
        return os.path.join(self.out, name)

    def test_posts_only_missing_summaries_with_idempotency_keys(self):
        code = run_sync_with_clients(self.directory, self.ledger, self.out)
        self.assertEqual(code, 0)
        posts = [{"headers": p["headers"], "payload": p["payload"]}
                 for p in self.ledger.posts]
        self.assertEqual(posts, EXPECTED_POSTS)

    def test_writes_orphans_errors_and_posted_summaries(self):
        run_sync_with_clients(self.directory, self.ledger, self.out)
        self.assertEqual(sorted(map(json.dumps,
                                    read_jsonl(self.path("orphans.jsonl")))),
                         sorted(map(json.dumps, EXPECTED_ORPHANS)))
        self.assertEqual(sorted(map(json.dumps,
                                    read_jsonl(self.path("errors.jsonl")))),
                         sorted(map(json.dumps, EXPECTED_ERRORS)))
        self.assertEqual(read_jsonl(self.path("summary.jsonl")),
                         [post["payload"] for post in EXPECTED_POSTS])

    def test_duplicate_item_id_is_counted_once(self):
        run_sync_with_clients(self.directory, self.ledger, self.out)
        amara = [p["payload"] for p in self.ledger.posts
                 if p["payload"]["owner"] == "amara"]
        self.assertEqual(amara, [{"owner": "amara", "open_items": 1,
                                  "done_items": 1, "total_points": 5}])

    def test_second_run_posts_nothing_and_rewrites_identical_files(self):
        run_sync_with_clients(self.directory, self.ledger, self.out)
        with open(self.path("summary.jsonl"), "rb") as handle:
            before = handle.read()
        posts_before = len(self.ledger.posts)

        code = run_sync_with_clients(self.directory, self.ledger, self.out)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.ledger.posts), posts_before)
        with open(self.path("summary.jsonl"), "rb") as handle:
            after = handle.read()
        self.assertEqual(before, after)

    def test_rate_limited_post_is_retried_after_waiting(self):
        clock = FakeClock()
        self.ledger.post_script = [
            response(429, {}, {"Retry-After": "2"})]
        ledger = RateLimitedClient(self.ledger, sleep=clock.sleep,
                                   monotonic=clock.monotonic)
        code = run_sync_with_clients(self.directory, ledger, self.out)
        self.assertEqual(code, 0)
        # One POST hit the rate limit and was sent again unchanged.
        self.assertEqual(len(self.ledger.posts), len(EXPECTED_POSTS) + 1)
        self.assertEqual(self.ledger.posts[0]["payload"],
                         self.ledger.posts[1]["payload"])
        self.assertEqual(self.ledger.posts[0]["headers"],
                         self.ledger.posts[1]["headers"])
        self.assertTrue(any(s >= 2.0 for s in clock.slept),
                        "expected a sleep of at least Retry-After seconds")

    def test_rate_limited_get_is_retried_and_not_counted_as_data(self):
        clock = FakeClock()
        self.directory.script["/v1/items"] = [
            response(429, {"items": ["poison"], "next_page": None},
                     {"Retry-After": "0"})]
        directory = RateLimitedClient(self.directory, sleep=clock.sleep,
                                      monotonic=clock.monotonic)
        code = run_sync_with_clients(directory, self.ledger, self.out)
        self.assertEqual(code, 0)
        self.assertEqual(read_jsonl(self.path("orphans.jsonl")),
                         EXPECTED_ORPHANS)

    def test_rejected_post_fails_the_run_loudly(self):
        self.ledger.post_statuses = [422]
        with self.assertRaises(Exception):
            run_sync_with_clients(self.directory, self.ledger, self.out)


# ---------------------------------------------------------------------------
# Live tests against the services named in config.json
# ---------------------------------------------------------------------------


def live_get(url, timeout=10.0):
    """One GET with a small independent 429/Retry-After loop."""
    while True:
        request = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(float(exc.headers.get("Retry-After") or 1))
                continue
            raise


def live_pages(base, path, key):
    records, page = [], 1
    while page is not None:
        body = live_get("%s%s?page=%d" % (base, path, page))
        records.extend(body[key])
        page = body.get("next_page")
    return records


def is_well_formed_item(record):
    if not isinstance(record, dict):
        return False
    points = record.get("points")
    return (isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and record.get("status") in ("open", "done")
            and isinstance(points, int) and not isinstance(points, bool)
            and points >= 0)


def is_well_formed_account(record):
    return (isinstance(record, dict)
            and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and isinstance(record.get("active"), bool))


def compute_expected(accounts, items):
    """A test-side reimplementation of the sync rules."""
    active_owners = {a["owner"] for a in accounts
                     if is_well_formed_account(a) and a["active"]}
    errors, seen, orphans = [], set(), []
    tallies = {owner: [0, 0, 0] for owner in active_owners}
    for record in items:
        if not is_well_formed_item(record):
            errors.append({"service": "directory", "record": record})
            continue
        if record["id"] in seen:
            continue
        seen.add(record["id"])
        owner = record["owner"]
        if owner in active_owners:
            tallies[owner][0 if record["status"] == "open" else 1] += 1
            tallies[owner][2] += record["points"]
        else:
            orphans.append(record)
    for record in accounts:
        if not is_well_formed_account(record):
            errors.append({"service": "ledger", "record": record})
    summaries = {owner: {"owner": owner, "open_items": t[0],
                         "done_items": t[1], "total_points": t[2]}
                 for owner, t in tallies.items()}
    return active_owners, summaries, orphans, errors


class CountingHttpClient(HttpClient):
    """A real HTTP client that counts the POSTs it issues."""

    def __init__(self, base_url):
        super().__init__(base_url)
        self.post_count = 0

    def post_json(self, path, payload, headers=None):
        self.post_count += 1
        return super().post_json(path, payload, headers=headers)


@unittest.skipUnless(os.path.exists("config.json"), "config.json not present")
class LiveSyncTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config()
        try:
            accounts = live_pages(cls.config.ledger_url, "/v1/accounts",
                                  "accounts")
            items = live_pages(cls.config.directory_url, "/v1/items", "items")
        except OSError as exc:
            raise unittest.SkipTest("services unreachable: %s" % exc)
        (cls.active_owners, cls.summaries, cls.orphans,
         cls.errors) = compute_expected(accounts, items)

    def make_clients(self):
        ledger_http = CountingHttpClient(self.config.ledger_url)
        return (RateLimitedClient(CountingHttpClient(self.config.directory_url)),
                RateLimitedClient(ledger_http), ledger_http)

    def summaries_in_ledger(self):
        return {s.get("owner"): s for s in
                live_pages(self.config.ledger_url, "/v1/summaries", "summaries")
                if isinstance(s, dict)}

    def test_sync_reconciles_the_two_services(self):
        directory, ledger, ledger_http = self.make_clients()
        missing = self.active_owners - set(self.summaries_in_ledger())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        code = run_sync_with_clients(directory, ledger, tmp.name)
        self.assertEqual(code, 0)
        self.assertEqual(ledger_http.post_count, len(missing))

        final = self.summaries_in_ledger()
        self.assertTrue(self.active_owners <= set(final))
        for owner, expected in self.summaries.items():
            self.assertEqual({k: final[owner].get(k) for k in expected},
                             expected, "summary mismatch for %r" % owner)

        self.assertEqual(
            sorted(map(json.dumps, read_jsonl(os.path.join(tmp.name,
                                                           "orphans.jsonl")))),
            sorted(map(json.dumps, self.orphans)))
        self.assertEqual(
            sorted(map(json.dumps, read_jsonl(os.path.join(tmp.name,
                                                           "errors.jsonl")))),
            sorted(map(json.dumps, self.errors)))

    def test_second_sync_issues_no_write_and_files_are_stable(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        summary_path = os.path.join(tmp.name, "summary.jsonl")

        first = self.make_clients()
        run_sync_with_clients(first[0], first[1], tmp.name)
        with open(summary_path, "rb") as handle:
            before = handle.read()
        ledger_before = self.summaries_in_ledger()

        second = self.make_clients()
        code = run_sync_with_clients(second[0], second[1], tmp.name)
        self.assertEqual(code, 0)
        self.assertEqual(second[2].post_count, 0,
                         "second sync must issue no write request")
        self.assertEqual(self.summaries_in_ledger(), ledger_before)
        with open(summary_path, "rb") as handle:
            after = handle.read()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
