"""Tests for orgsync.sync — the reconciliation logic behind `orgsync sync`.

No sockets: `HttpClient` takes an injected opener (see `orgsync.http_client`
and `tests/existing/test_http_client.py`), so `FakeService` below stands in
for both the Directory and the Ledger, implementing the HTTP contract those
services describe — pagination, well-formedness, the POST validation ladder,
and 429s on demand. This keeps the suite fast, deterministic, and offline
(no need to bring up or mutate any real fixture process), while still
exercising `run_sync` exactly as it would run against the real services.
"""

from __future__ import annotations

import email.message
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

from orgsync.config import Config
from orgsync.http_client import HttpClient
from orgsync.sync import (
    _dedup_key,
    _well_formed_account,
    _well_formed_item,
    run_sync,
)


def make_headers(pairs=None):
    message = email.message.Message()
    for key, value in (pairs or {}).items():
        message[key] = value
    return message


class FakeHTTPResponse:
    """Stands in for http.client.HTTPResponse: context manager + read()."""

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


class FakeService:
    """An in-memory double for one service (Directory or Ledger).

    Implements exactly the contract described in the task: paginated
    `GET /v1/<collection>?page=N` with `next_page`, and for the ledger,
    `POST /v1/summaries` with the 422/200/409/201 validation ladder. Any
    request ordinal named in `rate_limit_ordinals` (1-based, counting every
    request) is answered 429 with `Retry-After` instead of being routed.
    """

    def __init__(self, *, items=None, accounts=None, summaries=None,
                page_size=2, rate_limit_ordinals=(), retry_after=0):
        self.items = list(items or [])
        self.accounts = list(accounts or [])
        self.summaries = list(summaries or [])
        self.page_size = page_size
        self.rate_limit_ordinals = set(rate_limit_ordinals)
        self.retry_after = retry_after
        self.count = 0
        self.requests = []  # each: dict(n, method, path, page, body, idem)
        self._keys = {}

    def _active_owners(self):
        return {
            a["owner"] for a in self.accounts if _well_formed_account(a) and a["active"]
        }

    def _paginate(self, records):
        page = self._pending_page
        start = (page - 1) * self.page_size
        window = records[start:start + self.page_size]
        has_more = len(records) > start + self.page_size
        return window, (page + 1 if has_more else None)

    def open(self, request, timeout=None):
        self.count += 1
        n = self.count
        parsed = urllib.parse.urlparse(request.full_url)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        self._pending_page = int(query.get("page", ["1"])[0])
        method = request.get_method()
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        idem = request.get_header("Idempotency-key")

        self.requests.append(
            {"n": n, "method": method, "path": path, "page": self._pending_page,
             "body": body, "idem": idem})

        if n in self.rate_limit_ordinals:
            return self._respond(429, None, {"Retry-After": str(self.retry_after)})
        if method == "GET" and path == "/v1/items":
            window, next_page = self._paginate(self.items)
            return self._respond(200, {"items": window, "next_page": next_page})
        if method == "GET" and path == "/v1/accounts":
            window, next_page = self._paginate(self.accounts)
            return self._respond(200, {"accounts": window, "next_page": next_page})
        if method == "GET" and path == "/v1/summaries":
            window, next_page = self._paginate(self.summaries)
            return self._respond(200, {"summaries": window, "next_page": next_page})
        if method == "POST" and path == "/v1/summaries":
            return self._post_summary(body, idem)
        return self._respond(404, {"error": "not_found"})

    def _post_summary(self, body, idem):
        owner = body.get("owner") if isinstance(body, dict) else None
        if not isinstance(owner, str) or owner not in self._active_owners():
            return self._respond(422, {"error": "owner_not_active"})
        if idem in self._keys:
            if self._keys[idem] == body:
                return self._respond(200, self._keys[idem])
            return self._respond(409, {"error": "idempotency_key_conflict"})
        stored = dict(body)
        self._keys[idem] = stored
        self.summaries.append(stored)
        return self._respond(201, stored)

    def _respond(self, status, payload, extra_headers=None):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        if status >= 400:
            raise urllib.error.HTTPError(
                "http://fake", status, "error", make_headers(extra_headers), io.BytesIO(body))
        return FakeHTTPResponse(status, body, extra_headers)

    def post_count(self):
        return sum(1 for r in self.requests if r["method"] == "POST")


def make_clients(directory=None, ledger=None):
    directory = directory or FakeService()
    ledger = ledger or FakeService()
    directory_client = HttpClient("http://directory.test", opener=directory)
    ledger_client = HttpClient("http://ledger.test", opener=ledger)
    return directory, ledger, directory_client, ledger_client


def run(directory, ledger, out_dir):
    _, _, directory_client, ledger_client = make_clients(directory, ledger)
    config = Config("http://directory.test", "http://ledger.test")
    with mock.patch("orgsync.sync.time.sleep"):
        code = run_sync(config, out_dir=out_dir,
                        directory_client=directory_client, ledger_client=ledger_client)
    return code


def read_jsonl(path):
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class WellFormedTest(unittest.TestCase):
    def test_account_requires_str_id_str_owner_bool_active(self):
        self.assertTrue(_well_formed_account({"id": "a-1", "owner": "kim", "active": True}))
        self.assertFalse(_well_formed_account({"owner": "kim", "active": True}))
        self.assertFalse(_well_formed_account({"id": 1, "owner": "kim", "active": True}))
        self.assertFalse(_well_formed_account({"id": "a-1", "owner": "kim", "active": "yes"}))
        # A JSON integer must not satisfy the boolean gate.
        self.assertFalse(_well_formed_account({"id": "a-1", "owner": "kim", "active": 1}))
        self.assertFalse(_well_formed_account("not-a-dict"))

    def test_extra_fields_are_ignored(self):
        self.assertTrue(_well_formed_account(
            {"id": "a-1", "owner": "kim", "active": True, "note": "hi"}))

    def test_item_requires_str_id_str_owner_valid_status_nonneg_int_points(self):
        self.assertTrue(_well_formed_item({"id": "i-1", "owner": "kim", "status": "open", "points": 3}))
        self.assertFalse(_well_formed_item({"id": "i-1", "owner": "kim", "status": "open"}))
        self.assertFalse(_well_formed_item({"id": "i-1", "owner": "kim", "status": "sideways", "points": 1}))
        self.assertFalse(_well_formed_item({"id": "i-1", "owner": "kim", "status": "open", "points": -1}))
        self.assertFalse(_well_formed_item({"id": "i-1", "owner": "kim", "status": "open", "points": "3"}))
        # A JSON boolean must not satisfy the int gate (bool is an int subclass).
        self.assertFalse(_well_formed_item({"id": "i-1", "owner": "kim", "status": "open", "points": True}))
        self.assertTrue(_well_formed_item({"id": "i-1", "owner": "kim", "status": "open", "points": 0}))

    def test_dedup_key_is_stable_regardless_of_key_order(self):
        a = {"id": "i-1", "owner": "kim", "status": "open", "points": 3}
        b = {"points": 3, "status": "open", "owner": "kim", "id": "i-1"}
        self.assertEqual(_dedup_key(a), _dedup_key(b))


class PaginationTest(unittest.TestCase):
    def test_follows_next_page_until_null(self):
        directory = FakeService(
            items=[{"id": "i-%d" % n, "owner": "kim", "status": "open", "points": 1}
                  for n in range(5)],
            page_size=2,
        )
        with tempfile.TemporaryDirectory() as tmp:
            accounts = [{"id": "a-1", "owner": "kim", "active": True}]
            code = run(directory, FakeService(accounts=accounts), os.path.join(tmp, "out"))
            self.assertEqual(code, 0)
            summaries = read_jsonl(os.path.join(tmp, "out", "summary.jsonl"))
            self.assertEqual(summaries, [{"owner": "kim", "open_items": 5,
                                          "done_items": 0, "total_points": 5}])
        item_pages = [r["page"] for r in directory.requests if r["path"] == "/v1/items"]
        self.assertEqual(item_pages, [1, 2, 3])


class ReconciliationTest(unittest.TestCase):
    """End-to-end behavior of run_sync against a scripted pair of services."""

    def setUp(self):
        self.accounts = [
            {"id": "a-1", "owner": "alice", "active": True},
            {"id": "a-2", "owner": "bob", "active": True},
            {"id": "a-3", "owner": "carol", "active": False},
            # malformed: active is a string, not a JSON boolean
            {"id": "a-4", "owner": "dave", "active": "yes"},
        ]
        self.items = [
            {"id": "i-1", "owner": "alice", "status": "open", "points": 3},
            {"id": "i-2", "owner": "alice", "status": "done", "points": 2},
            {"id": "i-3", "owner": "bob", "status": "open", "points": 5},
            {"id": "i-3", "owner": "bob", "status": "open", "points": 5},  # duplicate delivery
            {"id": "i-4", "owner": "carol", "status": "open", "points": 9},  # inactive owner
            {"id": "i-5", "owner": "erin", "status": "open", "points": 1},  # unknown owner
            {"id": "i-6", "owner": "dave", "status": "open", "points": 4},  # malformed-account owner
            {"id": "i-7", "owner": "alice", "status": "open"},  # malformed: no points
            {"id": "i-8", "owner": "bob", "status": "done", "points": 1},
        ]

    def test_summaries_orphans_and_errors(self):
        directory = FakeService(items=self.items, page_size=3)
        ledger = FakeService(accounts=self.accounts, page_size=3)
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "out")
            code = run(directory, ledger, out_dir)
            self.assertEqual(code, 0)

            summaries = {s["owner"]: s for s in read_jsonl(os.path.join(out_dir, "summary.jsonl"))}
            self.assertEqual(summaries["alice"], {"owner": "alice", "open_items": 1,
                                                   "done_items": 1, "total_points": 5})
            self.assertEqual(summaries["bob"], {"owner": "bob", "open_items": 1,
                                                "done_items": 1, "total_points": 6})
            self.assertNotIn("carol", summaries)
            self.assertNotIn("dave", summaries)
            self.assertEqual(set(summaries), {"alice", "bob"})

            orphans = read_jsonl(os.path.join(out_dir, "orphans.jsonl"))
            orphan_ids = {o["id"] for o in orphans}
            self.assertEqual(orphan_ids, {"i-4", "i-5", "i-6"})

            errors = read_jsonl(os.path.join(out_dir, "errors.jsonl"))
            self.assertEqual(len(errors), 2)
            services = {(e["service"], e["record"]["id"]) for e in errors}
            self.assertEqual(services, {("ledger", "a-4"), ("directory", "i-7")})

        self.assertEqual(ledger.post_count(), 2)
        posted_owners = {r["idem"] for r in ledger.requests if r["method"] == "POST"}
        self.assertEqual(posted_owners, {"sync-alice", "sync-bob"})

    def test_active_account_with_no_items_gets_all_zero_summary(self):
        accounts = [{"id": "a-1", "owner": "lonely", "active": True}]
        directory = FakeService(items=[])
        ledger = FakeService(accounts=accounts)
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "out")
            self.assertEqual(run(directory, ledger, out_dir), 0)
            summaries = read_jsonl(os.path.join(out_dir, "summary.jsonl"))
            self.assertEqual(summaries, [{"owner": "lonely", "open_items": 0,
                                          "done_items": 0, "total_points": 0}])

    def test_creates_out_dir_when_absent(self):
        directory = FakeService(items=[])
        ledger = FakeService(accounts=[])
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "nested", "out")
            self.assertFalse(os.path.isdir(out_dir))
            self.assertEqual(run(directory, ledger, out_dir), 0)
            for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl"):
                self.assertTrue(os.path.isfile(os.path.join(out_dir, name)))

    def test_second_run_is_idempotent_no_posts_same_output(self):
        directory = FakeService(items=self.items, page_size=3)
        ledger = FakeService(accounts=self.accounts, page_size=3)
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "out")
            self.assertEqual(run(directory, ledger, out_dir), 0)
            first_pass = {
                name: read_jsonl(os.path.join(out_dir, name))
                for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")
            }
            self.assertEqual(ledger.post_count(), 2)

            # Same services (accumulated summaries persist), second invocation.
            self.assertEqual(run(directory, ledger, out_dir), 0)
            second_pass = {
                name: read_jsonl(os.path.join(out_dir, name))
                for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")
            }
            self.assertEqual(first_pass, second_pass)
            self.assertEqual(ledger.post_count(), 2)  # no new POSTs


class RateLimitTest(unittest.TestCase):
    def test_429_is_retried_after_waiting_retry_after_seconds(self):
        directory = FakeService(
            items=[{"id": "i-1", "owner": "kim", "status": "open", "points": 1}],
            rate_limit_ordinals={1}, retry_after=3,
        )
        ledger = FakeService(accounts=[{"id": "a-1", "owner": "kim", "active": True}])
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "out")
            _, _, directory_client, ledger_client = make_clients(directory, ledger)
            config = Config("http://directory.test", "http://ledger.test")
            with mock.patch("orgsync.sync.time.sleep") as sleep_mock:
                code = run_sync(config, out_dir=out_dir,
                                directory_client=directory_client, ledger_client=ledger_client)
            self.assertEqual(code, 0)
            sleep_mock.assert_called_once_with(3.0)
        # Ordinal 1 was the 429; ordinal 2 was the real, successful retry.
        self.assertEqual([r["n"] for r in directory.requests], [1, 2])

    def test_429_never_counts_as_data_and_never_abandons_the_work(self):
        directory = FakeService(
            items=[{"id": "i-%d" % n, "owner": "kim", "status": "open", "points": 1}
                  for n in range(3)],
            page_size=1, rate_limit_ordinals={2}, retry_after=0,
        )
        ledger = FakeService(accounts=[{"id": "a-1", "owner": "kim", "active": True}])
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = os.path.join(tmp, "out")
            self.assertEqual(run(directory, ledger, out_dir), 0)
            summaries = read_jsonl(os.path.join(out_dir, "summary.jsonl"))
            self.assertEqual(summaries, [{"owner": "kim", "open_items": 3,
                                          "done_items": 0, "total_points": 3}])


class RejectedPostTest(unittest.TestCase):
    def test_a_rejected_post_is_surfaced_not_swallowed(self):
        # A ledger that rejects every summary post (owner never active from its
        # point of view) simulates a bug that would otherwise post a rejected
        # write silently; run_sync must raise rather than proceed as if it
        # succeeded.
        class AlwaysRejectingLedger(FakeService):
            def _post_summary(self, body, idem):
                return self._respond(422, {"error": "owner_not_active"})

        directory = FakeService(items=[{"id": "i-1", "owner": "kim", "status": "open", "points": 1}])
        ledger = AlwaysRejectingLedger(accounts=[{"id": "a-1", "owner": "kim", "active": True}])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                run(directory, ledger, os.path.join(tmp, "out"))


class DefaultClientConstructionTest(unittest.TestCase):
    def test_run_sync_builds_its_own_clients_when_none_are_injected(self):
        # No live server is reachable at this port; run_sync must attempt a
        # real connection (and fail) rather than silently using a fake.
        from orgsync.http_client import HttpClientError
        config = Config("http://127.0.0.1:1", "http://127.0.0.1:1")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(HttpClientError):
                run_sync(config, out_dir=os.path.join(tmp, "out"))


if __name__ == "__main__":
    unittest.main()
