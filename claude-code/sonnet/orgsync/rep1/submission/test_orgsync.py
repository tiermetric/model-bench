"""Tests for `orgsync sync` — the reconciliation logic in orgsync.sync.

Most tests run against a small in-process fake of the Directory and Ledger
HTTP APIs (`FakeState` + `_FakeHandler`, below), built to the exact contract
in the task spec: pagination via `next_page`, 429 + Retry-After, and the
Ledger's 200/201/409/422 POST semantics. That gives full, deterministic
control over edge cases (rate limits, malformed records, duplicate delivery,
idempotency-key conflicts) without depending on timing or on any particular
dataset in the real fixture.

`LiveFixtureSmokeTest` additionally runs the real thing end to end against
the services named by `config.json`, to check the implementation against the
actual fixture and not just the fake's understanding of the contract.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import tempfile
import threading
import unittest
from typing import Any, Dict, List, Optional

from orgsync.config import Config, load_config
from orgsync.models import Summary
from orgsync.sync import (
    SyncError,
    is_well_formed_account,
    is_well_formed_item,
    run_sync,
)

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# A small fake of the Directory/Ledger contract, for deterministic tests.
# ---------------------------------------------------------------------------

class FakeState:
    """Mutable state behind one fake service. `items_pages`/`accounts_pages`/
    `summaries_pages` are lists of pages (each page a list of raw records);
    `next_page` is derived from position, `null` after the last page.
    """

    def __init__(self) -> None:
        self.items_pages: List[List[Any]] = [[]]
        self.accounts_pages: List[List[Any]] = [[]]
        self.summaries_pages: List[List[Any]] = [[]]
        # path -> number of remaining requests to that path that should 429.
        self.rate_limited: Dict[str, int] = {}
        self.retry_after = 0.01
        # Idempotency-Key -> body already accepted, for replay/conflict.
        self.posts: Dict[str, Dict[str, Any]] = {}
        self.post_requests: List[Dict[str, Any]] = []
        # Owners the fake Ledger will accept a POST for.
        self.active_owners: set = set()
        self.lock = threading.Lock()

    def pages_for(self, resource: str) -> List[List[Any]]:
        return {"items": self.items_pages, "accounts": self.accounts_pages,
                "summaries": self.summaries_pages}[resource]


def _make_handler(state: FakeState):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silence per-request logging
            pass

        def _send_json(self, status: int, body: Any, extra_headers=None) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            for key, value in (extra_headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

        def _rate_limited(self, key: str) -> bool:
            with state.lock:
                remaining = state.rate_limited.get(key, 0)
                if remaining <= 0:
                    return False
                state.rate_limited[key] = remaining - 1
            self._send_json(429, {"error": "rate_limited"},
                             {"Retry-After": str(state.retry_after)})
            return True

        def do_GET(self):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            if self._rate_limited("GET %s" % parsed.path):
                return
            params = parse_qs(parsed.query)
            page = int(params.get("page", ["1"])[0])

            resource = {"/v1/items": "items", "/v1/accounts": "accounts",
                        "/v1/summaries": "summaries"}.get(parsed.path)
            if resource is None:
                self._send_json(404, {"error": "not_found"})
                return

            pages = state.pages_for(resource)
            index = page - 1
            records = pages[index] if 0 <= index < len(pages) else []
            next_page = page + 1 if index + 1 < len(pages) else None
            list_key = resource
            self._send_json(200, {list_key: records, "next_page": next_page})

        def do_POST(self):
            if self._rate_limited("POST /v1/summaries"):
                return
            if self.path != "/v1/summaries":
                self._send_json(404, {"error": "not_found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            key = self.headers.get("Idempotency-Key")

            with state.lock:
                state.post_requests.append({"key": key, "body": body})
                if key in state.posts:
                    if state.posts[key] == body:
                        self._send_json(200, state.posts[key])
                    else:
                        self._send_json(409, {"error": "idempotency_key_conflict"})
                    return
                if body.get("owner") not in state.active_owners:
                    self._send_json(422, {"error": "owner_not_active"})
                    return
                state.posts[key] = body

            self._send_json(201, body)

    return Handler


class FakeServer:
    """A running fake service on 127.0.0.1, torn down with `.stop()`."""

    def __init__(self) -> None:
        self.state = FakeState()
        self._httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _make_handler(self.state))
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return "http://%s:%d" % (host, port)

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


class FakeSyncTestCase(unittest.TestCase):
    """Base class wiring up a fake Directory + Ledger and a scratch out-dir."""

    def setUp(self) -> None:
        self.directory = FakeServer()
        self.ledger = FakeServer()
        self.addCleanup(self.directory.stop)
        self.addCleanup(self.ledger.stop)

        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.out_dir = os.path.join(self.tmp, "out")

        self.config = Config(directory_url=self.directory.url, ledger_url=self.ledger.url)

    def sync(self, sleep=lambda seconds: None) -> int:
        return run_sync(self.config, self.out_dir, sleep=sleep)

    def read_jsonl(self, name: str) -> List[Any]:
        path = os.path.join(self.out_dir, name)
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def as_set(self, records: List[Any]) -> set:
        return {json.dumps(record, sort_keys=True) for record in records}


# ---------------------------------------------------------------------------
# Well-formedness
# ---------------------------------------------------------------------------

class WellFormedItemTest(unittest.TestCase):
    def test_accepts_a_well_formed_item_and_ignores_extra_fields(self):
        self.assertTrue(is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "open", "points": 3, "extra": "x"}))

    def test_rejects_missing_field(self):
        self.assertFalse(is_well_formed_item({"owner": "kim", "status": "open", "points": 3}))

    def test_rejects_wrong_status(self):
        self.assertFalse(is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "sideways", "points": 3}))

    def test_rejects_points_as_string(self):
        self.assertFalse(is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "open", "points": "3"}))

    def test_rejects_negative_points(self):
        self.assertFalse(is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "open", "points": -1}))

    def test_rejects_bool_points_even_though_bool_is_an_int_subclass(self):
        self.assertFalse(is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "open", "points": True}))

    def test_rejects_non_dict(self):
        self.assertFalse(is_well_formed_item("not a dict"))
        self.assertFalse(is_well_formed_item(None))


class WellFormedAccountTest(unittest.TestCase):
    def test_accepts_a_well_formed_account_and_ignores_extra_fields(self):
        self.assertTrue(is_well_formed_account(
            {"id": "a-1", "owner": "kim", "active": True, "extra": "x"}))

    def test_rejects_active_as_string(self):
        self.assertFalse(is_well_formed_account({"id": "a-1", "owner": "kim", "active": "yes"}))

    def test_rejects_active_as_int(self):
        self.assertFalse(is_well_formed_account({"id": "a-1", "owner": "kim", "active": 1}))

    def test_rejects_missing_owner(self):
        self.assertFalse(is_well_formed_account({"id": "a-1", "active": True}))


# ---------------------------------------------------------------------------
# End-to-end reconciliation against the fake services
# ---------------------------------------------------------------------------

class ReconciliationTest(FakeSyncTestCase):
    def _seed_typical_dataset(self):
        # amara: active, two open (one malformed, dropped) + one done.
        # bo: active, one item delivered twice across pages (dedupe to one).
        # chen: well-formed but inactive account -> its item is an orphan.
        # dee: active, no items -> all-zero summary.
        # fen: malformed account (active is a string) -> not active -> orphan.
        # zara: no account at all -> orphan.
        self.directory.state.items_pages = [
            [{"id": "item-101", "owner": "amara", "status": "open", "points": 3},
             {"id": "item-102", "owner": "amara", "status": "done", "points": 2},
             {"id": "item-103", "owner": "bo", "status": "open", "points": 5},
             {"id": "item-104", "owner": "chen", "status": "open", "points": 8}],
            [{"id": "item-105", "owner": "zara", "status": "open", "points": 4},
             {"id": "item-106", "owner": "fen", "status": "open", "points": 2},
             {"id": "item-103", "owner": "bo", "status": "open", "points": 5},
             {"id": "item-107", "owner": "amara", "status": "open"}],
        ]
        self.ledger.state.accounts_pages = [
            [{"id": "acct-01", "owner": "amara", "active": True},
             {"id": "acct-02", "owner": "bo", "active": True},
             {"id": "acct-03", "owner": "chen", "active": False}],
            [{"id": "acct-04", "owner": "dee", "active": True},
             {"id": "acct-05", "owner": "fen", "active": "yes"}],
        ]
        self.ledger.state.active_owners = {"amara", "bo", "dee"}

    def test_summaries_orphans_and_errors_match_hand_computed_values(self):
        self._seed_typical_dataset()
        code = self.sync()
        self.assertEqual(code, 0)

        summaries = self.read_jsonl("summary.jsonl")
        self.assertEqual(self.as_set(summaries), self.as_set([
            {"owner": "amara", "open_items": 1, "done_items": 1, "total_points": 5},
            {"owner": "bo", "open_items": 1, "done_items": 0, "total_points": 5},
            {"owner": "dee", "open_items": 0, "done_items": 0, "total_points": 0},
        ]))

        orphans = self.read_jsonl("orphans.jsonl")
        self.assertEqual(self.as_set(orphans), self.as_set([
            {"id": "item-104", "owner": "chen", "status": "open", "points": 8},
            {"id": "item-105", "owner": "zara", "status": "open", "points": 4},
            {"id": "item-106", "owner": "fen", "status": "open", "points": 2},
        ]))

        errors = self.read_jsonl("errors.jsonl")
        self.assertEqual(self.as_set(errors), self.as_set([
            {"service": "directory", "record": {"id": "item-107", "owner": "amara", "status": "open"}},
            {"service": "ledger", "record": {"id": "acct-05", "owner": "fen", "active": "yes"}},
        ]))

    def test_duplicate_item_id_counted_once(self):
        # item-103 for bo appears on both pages above; confirm it contributed
        # exactly one open item, not two.
        self._seed_typical_dataset()
        self.sync()
        summaries = {s["owner"]: s for s in self.read_jsonl("summary.jsonl")}
        self.assertEqual(summaries["bo"]["open_items"], 1)

    def test_posts_use_sync_owner_idempotency_key(self):
        self._seed_typical_dataset()
        self.sync()
        keys = {req["key"] for req in self.ledger.state.post_requests}
        self.assertEqual(keys, {"sync-amara", "sync-bo", "sync-dee"})

    def test_no_post_for_inactive_or_missing_accounts(self):
        self._seed_typical_dataset()
        self.sync()
        posted_owners = {req["body"]["owner"] for req in self.ledger.state.post_requests}
        self.assertNotIn("chen", posted_owners)
        self.assertNotIn("fen", posted_owners)
        self.assertNotIn("zara", posted_owners)

    def test_active_account_with_no_items_gets_all_zero_summary(self):
        self.directory.state.items_pages = [[]]
        self.ledger.state.accounts_pages = [[{"id": "a-1", "owner": "dee", "active": True}]]
        self.ledger.state.active_owners = {"dee"}
        self.sync()
        self.assertEqual(self.read_jsonl("summary.jsonl"),
                         [{"owner": "dee", "open_items": 0, "done_items": 0, "total_points": 0}])

    def test_duplicate_malformed_record_written_once(self):
        malformed = {"id": "item-bad", "owner": "kim", "status": "open"}  # no points
        self.directory.state.items_pages = [[malformed], [dict(malformed)]]
        self.ledger.state.accounts_pages = [[{"id": "a-1", "owner": "kim", "active": True}]]
        self.ledger.state.active_owners = {"kim"}
        self.sync()
        errors = self.read_jsonl("errors.jsonl")
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0], {"service": "directory", "record": malformed})

    def test_out_dir_is_created_if_absent(self):
        self.assertFalse(os.path.isdir(self.out_dir))
        self.sync()
        self.assertTrue(os.path.isdir(self.out_dir))


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class IdempotencyTest(FakeSyncTestCase):
    def test_second_run_issues_no_writes_and_same_output(self):
        self.directory.state.items_pages = [
            [{"id": "item-1", "owner": "kim", "status": "open", "points": 3}]]
        self.ledger.state.accounts_pages = [[{"id": "a-1", "owner": "kim", "active": True}]]
        self.ledger.state.active_owners = {"kim"}

        self.assertEqual(self.sync(), 0)
        first = {name: self.as_set(self.read_jsonl(name))
                 for name in ("orphans.jsonl", "errors.jsonl", "summary.jsonl")}
        posts_after_first = len(self.ledger.state.post_requests)
        self.assertEqual(posts_after_first, 1)

        # The ledger now has kim's summary via /v1/summaries, as a fresh
        # `sync` invocation would discover it.
        self.ledger.state.summaries_pages = [[
            {"owner": "kim", "open_items": 1, "done_items": 0, "total_points": 3}]]

        self.assertEqual(self.sync(), 0)
        second = {name: self.as_set(self.read_jsonl(name))
                 for name in ("orphans.jsonl", "errors.jsonl", "summary.jsonl")}

        self.assertEqual(first, second)
        self.assertEqual(len(self.ledger.state.post_requests), posts_after_first,
                         "second run must not POST anything")

    def test_existing_summary_reported_verbatim_even_if_current_items_differ(self):
        # The ledger's already-posted summary is authoritative once posted;
        # a later change to the items must not trigger a re-post.
        self.ledger.state.accounts_pages = [[{"id": "a-1", "owner": "kim", "active": True}]]
        self.ledger.state.active_owners = {"kim"}
        self.ledger.state.summaries_pages = [[
            {"owner": "kim", "open_items": 9, "done_items": 9, "total_points": 99}]]
        self.directory.state.items_pages = [
            [{"id": "item-1", "owner": "kim", "status": "open", "points": 1}]]

        self.sync()

        self.assertEqual(self.read_jsonl("summary.jsonl"),
                         [{"owner": "kim", "open_items": 9, "done_items": 9, "total_points": 99}])
        self.assertEqual(len(self.ledger.state.post_requests), 0)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class RateLimitTest(FakeSyncTestCase):
    def test_get_429_is_retried_after_waiting(self):
        self.directory.state.items_pages = [
            [{"id": "item-1", "owner": "kim", "status": "open", "points": 1}]]
        self.ledger.state.accounts_pages = [[{"id": "a-1", "owner": "kim", "active": True}]]
        self.ledger.state.active_owners = {"kim"}
        self.directory.state.rate_limited["GET /v1/items"] = 2
        self.directory.state.retry_after = 0.05

        sleeps = []
        code = self.sync(sleep=sleeps.append)

        self.assertEqual(code, 0)
        self.assertEqual(sleeps, [0.05, 0.05])
        self.assertEqual(self.read_jsonl("summary.jsonl"),
                         [{"owner": "kim", "open_items": 1, "done_items": 0, "total_points": 1}])

    def test_post_429_is_retried_and_does_not_duplicate_the_post(self):
        self.directory.state.items_pages = [[]]
        self.ledger.state.accounts_pages = [[{"id": "a-1", "owner": "kim", "active": True}]]
        self.ledger.state.active_owners = {"kim"}
        self.ledger.state.rate_limited["POST /v1/summaries"] = 1
        self.ledger.state.retry_after = 0.05

        sleeps = []
        code = self.sync(sleep=sleeps.append)

        self.assertEqual(code, 0)
        self.assertIn(0.05, sleeps)
        # The 429 is answered before the body is even logged; only the retry
        # that actually gets processed shows up here.
        self.assertEqual(len(self.ledger.state.post_requests), 1)
        self.assertEqual(self.read_jsonl("summary.jsonl"),
                         [{"owner": "kim", "open_items": 0, "done_items": 0, "total_points": 0}])

    def test_a_429_never_stops_the_run(self):
        self.directory.state.items_pages = [[], []]
        self.directory.state.rate_limited["GET /v1/items"] = 3
        self.directory.state.retry_after = 0.01
        self.ledger.state.accounts_pages = [[]]

        self.assertEqual(self.sync(sleep=lambda s: None), 0)


# ---------------------------------------------------------------------------
# Rejections (used to exercise _post_summary directly; run_sync's own logic
# should never produce a rejected write for a passing dataset).
# ---------------------------------------------------------------------------

class PostSummaryRejectionTest(FakeSyncTestCase):
    def test_conflicting_body_under_same_idempotency_key_raises(self):
        from orgsync.http_client import HttpClient
        from orgsync.sync import _post_summary

        self.ledger.state.active_owners = {"kim"}
        client = HttpClient(self.ledger.url)

        first = _post_summary(client, Summary(owner="kim", open_items=1, done_items=0,
                                              total_points=1), sleep=lambda s: None)
        self.assertEqual(first, {"owner": "kim", "open_items": 1, "done_items": 0,
                                 "total_points": 1})
        # Same owner (-> same Idempotency-Key), different body.
        with self.assertRaises(SyncError):
            _post_summary(client, Summary(owner="kim", open_items=2, done_items=0,
                                          total_points=2), sleep=lambda s: None)

    def test_replay_of_identical_post_succeeds_without_error(self):
        from orgsync.http_client import HttpClient
        from orgsync.sync import _post_summary

        self.ledger.state.active_owners = {"kim"}
        client = HttpClient(self.ledger.url)
        summary = Summary(owner="kim", open_items=1, done_items=0, total_points=1)

        first = _post_summary(client, summary, sleep=lambda s: None)
        second = _post_summary(client, summary, sleep=lambda s: None)
        self.assertEqual(first, second)

    def test_post_for_inactive_owner_raises(self):
        from orgsync.http_client import HttpClient
        from orgsync.sync import _post_summary

        client = HttpClient(self.ledger.url)  # active_owners left empty
        with self.assertRaises(SyncError):
            _post_summary(client, Summary(owner="ghost"), sleep=lambda s: None)


# ---------------------------------------------------------------------------
# Smoke test against the real fixture named by config.json
# ---------------------------------------------------------------------------

class LiveFixtureSmokeTest(unittest.TestCase):
    """Runs the real CLI against whatever services config.json names.

    Deliberately light and tolerant of state left behind by earlier runs
    (including manual ones): it only checks that two consecutive real
    invocations succeed and agree with each other, which is the guarantee
    the task cares about (idempotency), without assuming anything about the
    fixture's specific dataset.
    """

    def setUp(self):
        config_path = os.path.join(REPO_ROOT, "config.json")
        if not os.path.exists(config_path):
            self.skipTest("config.json not present")
        self.config = load_config(config_path)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _read_jsonl(self, out_dir, name):
        path = os.path.join(out_dir, name)
        if not os.path.exists(path):
            return set()
        with open(path, "r", encoding="utf-8") as handle:
            return {line.strip() for line in handle if line.strip()}

    def test_two_runs_succeed_and_agree(self):
        out1 = os.path.join(self.tmp, "run1")
        out2 = os.path.join(self.tmp, "run2")

        self.assertEqual(run_sync(self.config, out1), 0)
        self.assertEqual(run_sync(self.config, out2), 0)

        for name in ("orphans.jsonl", "errors.jsonl", "summary.jsonl"):
            content1 = {json.dumps(json.loads(line), sort_keys=True)
                       for line in self._read_jsonl(out1, name)}
            content2 = {json.dumps(json.loads(line), sort_keys=True)
                       for line in self._read_jsonl(out2, name)}
            self.assertEqual(content1, content2, "%s differed between runs" % name)


if __name__ == "__main__":
    unittest.main()
