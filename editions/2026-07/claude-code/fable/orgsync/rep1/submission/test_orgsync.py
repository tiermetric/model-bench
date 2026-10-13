"""End-to-end tests for `orgsync sync` against simulated services.

The fake opener below implements both services faithfully — pagination,
429 rate limiting with Retry-After, and the ledger's idempotent POST
semantics (201/200/409/422) — so every behavior of the sync command can be
exercised deterministically without a socket and without touching the real
fixtures' request logs.
"""

import json
import os
import tempfile
import unittest
import urllib.parse

from orgsync.config import Config
from orgsync.sync import run_sync

DIRECTORY_URL = "http://directory.invalid"
LEDGER_URL = "http://ledger.invalid"

CONFIG = Config(directory_url=DIRECTORY_URL, ledger_url=LEDGER_URL)


class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status = status
        self.headers = dict(headers or {})
        self._body = json.dumps(body).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeServices:
    """Both services behind one urllib-style opener.

    `rate_limits` maps (method, host, path) to a list of Retry-After values;
    each pending value answers one request to that endpoint with a 429 before
    the endpoint behaves normally again.
    """

    def __init__(self, items=(), accounts=(), summaries=(), page_size=2):
        self.items = list(items)
        self.accounts = list(accounts)
        self.summaries = list(summaries)
        self.page_size = page_size
        self.rate_limits = {}
        self.requests = []  # (method, host, path, params, idempotency_key, body)
        self.idempotency = {}

    # -- request log helpers -------------------------------------------------

    def posts(self):
        return [r for r in self.requests if r[0] == "POST"]

    # -- the opener interface ------------------------------------------------

    def open(self, request, timeout=None):
        method = request.get_method()
        url = urllib.parse.urlsplit(request.full_url)
        host = "directory" if "directory" in url.netloc else "ledger"
        params = dict(urllib.parse.parse_qsl(url.query))
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        idempotency_key = request.get_header("Idempotency-key")
        self.requests.append((method, host, url.path, params, idempotency_key, body))

        pending = self.rate_limits.get((method, host, url.path))
        if pending:
            retry_after = pending.pop(0)
            return FakeResponse(429, {"error": "rate limited"},
                                {"Retry-After": str(retry_after)})

        if method == "GET":
            collections = {("directory", "/v1/items"): (self.items, "items"),
                           ("ledger", "/v1/accounts"): (self.accounts, "accounts"),
                           ("ledger", "/v1/summaries"): (self.summaries, "summaries")}
            if (host, url.path) not in collections:
                return FakeResponse(404, {"error": "no such endpoint"})
            collection, key = collections[(host, url.path)]
            page = int(params.get("page", 1))
            start = (page - 1) * self.page_size
            chunk = collection[start:start + self.page_size]
            next_page = page + 1 if start + self.page_size < len(collection) else None
            return FakeResponse(200, {key: chunk, "next_page": next_page})

        if method == "POST" and (host, url.path) == ("ledger", "/v1/summaries"):
            return self._post_summary(idempotency_key, body)
        return FakeResponse(404, {"error": "no such endpoint"})

    def _post_summary(self, key, body):
        active_owners = {a["owner"] for a in self.accounts
                         if isinstance(a, dict)
                         and isinstance(a.get("id"), str)
                         and isinstance(a.get("owner"), str)
                         and a.get("active") is True}
        valid = (isinstance(body, dict)
                 and isinstance(body.get("owner"), str)
                 and body.get("owner") in active_owners
                 and all(type(body.get(f)) is int and body[f] >= 0
                         for f in ("open_items", "done_items", "total_points")))
        if not valid:
            return FakeResponse(422, {"error": "invalid summary"})
        if key in self.idempotency:
            if self.idempotency[key] == body:
                return FakeResponse(200, body)
            return FakeResponse(409, {"error": "idempotency key reused"})
        self.idempotency[key] = body
        self.summaries.append(body)
        return FakeResponse(201, body)


def canonical(records):
    """Order-insensitive representation of a list of JSON values."""
    return sorted(json.dumps(r, sort_keys=True) for r in records)


class SyncTestCase(unittest.TestCase):
    """Base: run sync against a FakeServices into a temp out dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = os.path.join(self.tmp.name, "out")
        self.sleeps = []

    def run_sync(self, services):
        return run_sync(CONFIG, self.out_dir,
                        opener=services, sleep=self.sleeps.append)

    def read_jsonl(self, name):
        with open(os.path.join(self.out_dir, name), "r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def read_bytes(self, name):
        with open(os.path.join(self.out_dir, name), "rb") as fh:
            return fh.read()


class HappyPathTest(SyncTestCase):
    def make_services(self):
        return FakeServices(
            accounts=[
                {"id": "a1", "owner": "amara", "active": True},
                {"id": "a2", "owner": "bo", "active": True, "tier": "gold"},
                {"id": "a3", "owner": "chen", "active": False},
                {"id": "a4", "owner": "dana", "active": True},
                {"id": "a5", "owner": "evil", "active": "yes"},
            ],
            items=[
                {"id": "i1", "owner": "amara", "status": "open", "points": 3},
                {"id": "i2", "owner": "amara", "status": "open", "points": 4},
                {"id": "i3", "owner": "amara", "status": "done", "points": 2},
                {"id": "i4", "owner": "bo", "status": "open", "points": 5, "note": "x"},
                {"id": "i4", "owner": "bo", "status": "open", "points": 5, "note": "x"},
                {"id": "i5", "owner": "chen", "status": "open", "points": 1},
                {"id": "i6", "owner": "ghost", "status": "done", "points": 2},
                {"id": "i7", "owner": "evil", "status": "open", "points": 1},
                {"id": "i8", "owner": "amara", "status": "open", "points": "3"},
                {"id": "i9", "owner": "amara", "status": "open", "points": True},
                {"id": "i10", "owner": "amara", "status": "open", "points": -1},
                {"id": "i11", "status": "open", "points": 1},
                {"id": "i12", "owner": "amara", "status": "OPEN", "points": 1},
                "junk",
            ],
            page_size=3)

    def test_summaries_posted_for_every_active_owner(self):
        services = self.make_services()
        self.assertEqual(self.run_sync(services), 0)
        expected = [
            {"owner": "amara", "open_items": 2, "done_items": 1, "total_points": 9},
            {"owner": "bo", "open_items": 1, "done_items": 0, "total_points": 5},
            {"owner": "dana", "open_items": 0, "done_items": 0, "total_points": 0},
        ]
        self.assertEqual(canonical(self.read_jsonl("summary.jsonl")),
                         canonical(expected))
        # The ledger accepted exactly those bodies, each under key sync-<owner>.
        self.assertEqual(canonical(services.idempotency.values()), canonical(expected))
        for _, _, _, _, key, body in services.posts():
            self.assertEqual(key, "sync-%s" % body["owner"])

    def test_orphans_written_once_as_received(self):
        services = self.make_services()
        self.run_sync(services)
        expected = [
            {"id": "i5", "owner": "chen", "status": "open", "points": 1},
            {"id": "i6", "owner": "ghost", "status": "done", "points": 2},
            {"id": "i7", "owner": "evil", "status": "open", "points": 1},
        ]
        self.assertEqual(canonical(self.read_jsonl("orphans.jsonl")),
                         canonical(expected))

    def test_malformed_records_reported_once_each(self):
        services = self.make_services()
        self.run_sync(services)
        expected = [
            {"service": "ledger",
             "record": {"id": "a5", "owner": "evil", "active": "yes"}},
            {"service": "directory",
             "record": {"id": "i8", "owner": "amara", "status": "open", "points": "3"}},
            {"service": "directory",
             "record": {"id": "i9", "owner": "amara", "status": "open", "points": True}},
            {"service": "directory",
             "record": {"id": "i10", "owner": "amara", "status": "open", "points": -1}},
            {"service": "directory", "record": {"id": "i11", "status": "open", "points": 1}},
            {"service": "directory",
             "record": {"id": "i12", "owner": "amara", "status": "OPEN", "points": 1}},
            {"service": "directory", "record": "junk"},
        ]
        self.assertEqual(canonical(self.read_jsonl("errors.jsonl")),
                         canonical(expected))

    def test_duplicate_delivery_counted_once(self):
        services = self.make_services()
        self.run_sync(services)
        bo = [s for s in self.read_jsonl("summary.jsonl") if s["owner"] == "bo"]
        self.assertEqual(bo, [{"owner": "bo", "open_items": 1,
                               "done_items": 0, "total_points": 5}])

    def test_pagination_followed_to_the_last_page(self):
        services = self.make_services()
        self.run_sync(services)
        item_pages = sorted(int(r[3]["page"]) for r in services.requests
                            if r[2] == "/v1/items")
        self.assertEqual(item_pages, [1, 2, 3, 4, 5])


class WorkedExampleTest(SyncTestCase):
    def test_kim_arithmetic(self):
        services = FakeServices(
            accounts=[{"id": "a1", "owner": "kim", "active": True}],
            items=[{"id": "i1", "owner": "kim", "status": "open", "points": 3},
                   {"id": "i2", "owner": "kim", "status": "open", "points": 4},
                   {"id": "i3", "owner": "kim", "status": "done", "points": 2}])
        self.assertEqual(self.run_sync(services), 0)
        self.assertEqual(self.read_jsonl("summary.jsonl"),
                         [{"owner": "kim", "open_items": 2,
                           "done_items": 1, "total_points": 9}])


class SecondRunTest(SyncTestCase):
    def test_second_run_writes_nothing_and_changes_nothing(self):
        services = HappyPathTest.make_services(self)
        self.assertEqual(self.run_sync(services), 0)
        first = {name: self.read_bytes(name)
                 for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")}
        first_posts = len(services.posts())
        self.assertGreater(first_posts, 0)

        self.assertEqual(self.run_sync(services), 0)
        self.assertEqual(len(services.posts()), first_posts,
                         "second run must issue no write requests")
        for name, content in first.items():
            self.assertEqual(self.read_bytes(name), content,
                             "%s changed on the second run" % name)


class ExistingSummariesTest(SyncTestCase):
    def test_only_missing_summaries_are_posted(self):
        posted_before = {"owner": "amara", "open_items": 7,
                         "done_items": 7, "total_points": 7}
        services = FakeServices(
            accounts=[{"id": "a1", "owner": "amara", "active": True},
                      {"id": "a2", "owner": "bo", "active": True}],
            items=[{"id": "i1", "owner": "amara", "status": "open", "points": 1},
                   {"id": "i2", "owner": "bo", "status": "done", "points": 2}],
            summaries=[posted_before])
        self.assertEqual(self.run_sync(services), 0)
        self.assertEqual([r[5] for r in services.posts()],
                         [{"owner": "bo", "open_items": 0,
                           "done_items": 1, "total_points": 2}])
        self.assertEqual(self.read_jsonl("summary.jsonl"),
                         [{"owner": "bo", "open_items": 0,
                           "done_items": 1, "total_points": 2}])
        # The ledger's pre-existing summary is untouched.
        self.assertIn(posted_before, services.summaries)

    def test_active_owner_with_no_items_gets_zero_summary(self):
        services = FakeServices(
            accounts=[{"id": "a1", "owner": "solo", "active": True}])
        self.assertEqual(self.run_sync(services), 0)
        self.assertEqual(self.read_jsonl("summary.jsonl"),
                         [{"owner": "solo", "open_items": 0,
                           "done_items": 0, "total_points": 0}])


class RateLimitTest(SyncTestCase):
    def test_429_is_waited_out_and_retried(self):
        services = FakeServices(
            accounts=[{"id": "a1", "owner": "kim", "active": True}],
            items=[{"id": "i1", "owner": "kim", "status": "open", "points": 3}])
        services.rate_limits = {
            ("GET", "directory", "/v1/items"): [2, 1],
            ("GET", "ledger", "/v1/accounts"): [3],
            ("POST", "ledger", "/v1/summaries"): [1],
        }
        self.assertEqual(self.run_sync(services), 0)
        # Every 429 produced a wait of at least its Retry-After, then a retry.
        self.assertEqual(len(self.sleeps), 4)
        self.assertEqual([s >= w for s, w in zip(sorted(self.sleeps),
                                                 sorted([2, 1, 3, 1]))],
                         [True] * 4)
        # The work still completed: exactly one summary was accepted.
        self.assertEqual(services.summaries,
                         [{"owner": "kim", "open_items": 1,
                           "done_items": 0, "total_points": 3}])
        # The rate-limited POST was retried, not abandoned or double-counted.
        self.assertEqual(len(services.posts()), 2)
        self.assertEqual(len(self.read_jsonl("summary.jsonl")), 1)


class MalformedLedgerSummaryTest(SyncTestCase):
    def test_malformed_summary_record_goes_to_errors(self):
        services = FakeServices(
            accounts=[{"id": "a1", "owner": "kim", "active": True}],
            summaries=[{"owner": 42, "open_items": 0,
                        "done_items": 0, "total_points": 0}])
        self.assertEqual(self.run_sync(services), 0)
        self.assertEqual(self.read_jsonl("errors.jsonl"),
                         [{"service": "ledger",
                           "record": {"owner": 42, "open_items": 0,
                                      "done_items": 0, "total_points": 0}}])
        # The malformed record is not a summary, so kim's is still posted.
        self.assertEqual(self.read_jsonl("summary.jsonl"),
                         [{"owner": "kim", "open_items": 0,
                           "done_items": 0, "total_points": 0}])


if __name__ == "__main__":
    unittest.main()
