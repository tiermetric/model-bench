"""Tests for `orgsync sync` — the reconciliation logic in `orgsync.sync`.

Two kinds of test live here:

* `Fake*` tests build an in-memory Directory/Ledger pair (no sockets) so
  edge cases — malformed records, rate limiting, rejected POSTs — are
  deterministic and don't depend on the shared live fixture's one-time
  429 schedule or its accumulated request count.
* `Live*` tests talk to the real services named in `config.json` (as the
  task allows), recomputing the expected reconciliation independently of
  `orgsync.sync` so a bug in the implementation can't hide by agreeing with
  itself, then checking the tool's output against that recomputation.
"""

from __future__ import annotations

import email.message
import json
import os
import tempfile
import time
import unittest
import unittest.mock
import urllib.parse

from orgsync.config import DEFAULT_CONFIG_PATH, load_config
from orgsync.http_client import HttpClient
from orgsync.sync import _is_well_formed_account, _is_well_formed_item, _sync, run_sync


# ── Fake transport (no sockets) ──────────────────────────────────────────────


def _headers(pairs=None):
    message = email.message.Message()
    for key, value in (pairs or {}).items():
        message[key] = value
    return message


class _FakeResponse:
    """Stands in for http.client.HTTPResponse: context manager + read()."""

    def __init__(self, status, payload, extra_headers=None):
        self.status = status
        self.headers = _headers(extra_headers)
        self._body = b"" if payload is None else json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeOpener:
    """Routes every request to `router` and records the call for assertions."""

    def __init__(self, router):
        self._router = router
        self.calls = []

    def open(self, request, timeout=None):
        parsed = urllib.parse.urlparse(request.full_url)
        query = urllib.parse.parse_qs(parsed.query)
        body = json.loads(request.data.decode("utf-8")) if request.data is not None else None
        headers = {k.lower(): v for k, v in request.header_items()}
        self.calls.append((request.get_method(), parsed.path, query, body, headers))
        status, payload, extra = self._router(request.get_method(), parsed.path, query,
                                              body, headers)
        return _FakeResponse(status, payload, extra)


def _paginate(records, page, page_size):
    start = (page - 1) * page_size
    window = records[start:start + page_size]
    next_page = page + 1 if len(records) > start + page_size else None
    return window, next_page


class FakePair:
    """A minimal in-memory Directory + Ledger pair implementing the task's
    documented contract: pagination, the well-formedness rules, the POST
    validation ladder, and an optional fixed rate-limit schedule."""

    def __init__(self, accounts, items, page_size=2, rate_limited_directory=(),
                 rate_limited_ledger=()):
        self.accounts = accounts
        self.items = items
        self.summaries = []
        self.keys = {}
        self.page_size = page_size
        self.rate_limited_directory = set(rate_limited_directory)
        self.rate_limited_ledger = set(rate_limited_ledger)
        self.directory_count = 0
        self.ledger_count = 0

    def _active_owners(self):
        return {a["owner"] for a in self.accounts if _is_well_formed_account(a) and a["active"]}

    def directory_router(self, method, path, query, body, headers):
        self.directory_count += 1
        if self.directory_count in self.rate_limited_directory:
            return 429, None, {"Retry-After": "1"}
        if path == "/v1/items":
            page = int(query.get("page", ["1"])[0])
            window, next_page = _paginate(self.items, page, self.page_size)
            return 200, {"items": window, "next_page": next_page}, {}
        return 404, {"error": "not_found"}, {}

    def ledger_router(self, method, path, query, body, headers):
        self.ledger_count += 1
        if self.ledger_count in self.rate_limited_ledger:
            return 429, None, {"Retry-After": "1"}
        if path == "/v1/accounts" and method == "GET":
            page = int(query.get("page", ["1"])[0])
            window, next_page = _paginate(self.accounts, page, self.page_size)
            return 200, {"accounts": window, "next_page": next_page}, {}
        if path == "/v1/summaries" and method == "GET":
            page = int(query.get("page", ["1"])[0])
            window, next_page = _paginate(self.summaries, page, 10)
            return 200, {"summaries": window, "next_page": next_page}, {}
        if path == "/v1/summaries" and method == "POST":
            return self._post_summary(body, headers)
        return 404, {"error": "not_found"}, {}

    def _post_summary(self, body, headers):
        if not isinstance(body, dict) or not isinstance(body.get("owner"), str) \
                or body["owner"] not in self._active_owners():
            return 422, {"error": "owner_not_active"}, {}
        idem = headers.get("idempotency-key")
        if idem in self.keys:
            if self.keys[idem] == body:
                return 200, self.keys[idem], {}
            return 409, {"error": "idempotency_key_conflict"}, {}
        stored = dict(body)
        self.keys[idem] = stored
        self.summaries.append(stored)
        return 201, stored, {}

    def clients(self):
        directory = HttpClient("http://fake-directory", opener=_FakeOpener(self.directory_router))
        ledger = HttpClient("http://fake-ledger", opener=_FakeOpener(self.ledger_router))
        return directory, ledger


class FakeSyncTestCase(unittest.TestCase):
    """Base class giving subclasses a temp out-dir and a `run` helper."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = os.path.join(self.tmp.name, "out")

    def run_sync(self, pair):
        directory, ledger = pair.clients()
        code = _sync(directory, ledger, self.out_dir)
        return code, directory, ledger

    def read_jsonl(self, name):
        path = os.path.join(self.out_dir, name)
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]


class WellFormedPartitionTest(FakeSyncTestCase):
    """Items/accounts partition into: counted, orphaned, or malformed."""

    def setUp(self):
        super().setUp()
        self.accounts = [
            {"id": "a-1", "owner": "kim", "active": True},
            {"id": "a-2", "owner": "sol", "active": False},
            # malformed: active is a string, not a JSON boolean
            {"id": "a-3", "owner": "tam", "active": "true"},
        ]
        self.items = [
            {"id": "i-1", "owner": "kim", "status": "open", "points": 3},
            {"id": "i-1", "owner": "kim", "status": "open", "points": 3},  # duplicate delivery
            {"id": "i-2", "owner": "kim", "status": "done", "points": 2},
            {"id": "i-3", "owner": "sol", "status": "open", "points": 5},  # inactive owner
            {"id": "i-4", "owner": "unknown", "status": "open", "points": 1},  # no account
            {"id": "i-5", "owner": "tam", "status": "open", "points": 9},  # malformed account
            # malformed items: bad status, negative points, points as string, bad id type
            {"id": "i-6", "owner": "kim", "status": "blocked", "points": 1},
            {"id": "i-7", "owner": "kim", "status": "open", "points": -1},
            {"id": "i-8", "owner": "kim", "status": "open", "points": "3"},
            {"id": 9, "owner": "kim", "status": "open", "points": 3},
        ]

    def test_summary_counts_well_formed_items_once_each(self):
        pair = FakePair(self.accounts, self.items)
        self.run_sync(pair)
        summaries = self.read_jsonl("summary.jsonl")
        self.assertEqual(summaries, [{"owner": "kim", "open_items": 1, "done_items": 1,
                                      "total_points": 5}])

    def test_orphans_are_items_without_an_active_well_formed_owner(self):
        pair = FakePair(self.accounts, self.items)
        self.run_sync(pair)
        orphans = self.read_jsonl("orphans.jsonl")
        self.assertEqual({o["id"] for o in orphans}, {"i-3", "i-4", "i-5"})

    def test_malformed_records_go_to_errors_with_their_service_and_never_stop_the_run(self):
        pair = FakePair(self.accounts, self.items)
        code, _, _ = self.run_sync(pair)
        self.assertEqual(code, 0)
        errors = self.read_jsonl("errors.jsonl")
        by_service = {}
        for entry in errors:
            by_service.setdefault(entry["service"], set()).add(json.dumps(entry["record"],
                                                                           sort_keys=True))
        self.assertEqual(by_service["ledger"],
                         {json.dumps(self.accounts[2], sort_keys=True)})
        malformed_item_ids = {entry["record"].get("id") for entry in errors
                              if entry["service"] == "directory"}
        self.assertEqual(malformed_item_ids, {"i-6", "i-7", "i-8", 9})

    def test_active_owner_with_no_items_gets_an_all_zero_summary(self):
        accounts = [{"id": "a-1", "owner": "kim", "active": True}]
        pair = FakePair(accounts, [])
        self.run_sync(pair)
        self.assertEqual(self.read_jsonl("summary.jsonl"),
                         [{"owner": "kim", "open_items": 0, "done_items": 0,
                           "total_points": 0}])

    def test_inactive_account_gets_no_summary(self):
        accounts = [{"id": "a-1", "owner": "kim", "active": False}]
        items = [{"id": "i-1", "owner": "kim", "status": "open", "points": 1}]
        pair = FakePair(accounts, items)
        self.run_sync(pair)
        self.assertEqual(self.read_jsonl("summary.jsonl"), [])
        self.assertEqual([o["id"] for o in self.read_jsonl("orphans.jsonl")], ["i-1"])


class PostingBehaviorTest(FakeSyncTestCase):
    def setUp(self):
        super().setUp()
        self.accounts = [{"id": "a-1", "owner": "kim", "active": True},
                         {"id": "a-2", "owner": "sol", "active": True}]
        self.items = [{"id": "i-1", "owner": "kim", "status": "open", "points": 3}]

    def test_posts_with_idempotency_key_sync_dash_owner(self):
        pair = FakePair(self.accounts, self.items)
        _, directory, ledger = self.run_sync(pair)
        posts = [c for c in ledger._opener.calls if c[0] == "POST"]
        keys = {c[4].get("idempotency-key") for c in posts}
        self.assertEqual(keys, {"sync-kim", "sync-sol"})

    def test_never_posts_for_an_owner_the_ledger_would_reject(self):
        # sol has no account at all here; if the tool ever posted for sol the
        # fake ledger's 422 branch would fire and _sync would raise.
        accounts = [{"id": "a-1", "owner": "kim", "active": True}]
        items = [{"id": "i-1", "owner": "kim", "status": "open", "points": 1},
                {"id": "i-2", "owner": "sol", "status": "open", "points": 1}]
        pair = FakePair(accounts, items)
        code, _, _ = self.run_sync(pair)
        self.assertEqual(code, 0)

    def test_owner_with_existing_summary_is_not_reposted(self):
        pair = FakePair(self.accounts, self.items)
        pair.summaries.append({"owner": "kim", "open_items": 1, "done_items": 0,
                               "total_points": 3})
        pair.keys["sync-kim"] = pair.summaries[0]
        _, _, ledger = self.run_sync(pair)
        posted_owners = {c[3]["owner"] for c in ledger._opener.calls if c[0] == "POST"}
        self.assertEqual(posted_owners, {"sol"})
        # but the output file still reports kim's summary — it's the target
        # state, not just what this run happened to write.
        summaries = self.read_jsonl("summary.jsonl")
        self.assertEqual({s["owner"] for s in summaries}, {"kim", "sol"})

    def test_second_run_against_unchanged_services_posts_nothing(self):
        pair = FakePair(self.accounts, self.items)
        self.run_sync(pair)
        first = self.read_jsonl("summary.jsonl")

        second_out = os.path.join(self.tmp.name, "out2")
        directory, ledger = pair.clients()
        code = _sync(directory, ledger, second_out)
        self.assertEqual(code, 0)

        posts = [c for c in ledger._opener.calls if c[0] == "POST"]
        self.assertEqual(posts, [])
        with open(os.path.join(second_out, "summary.jsonl"), "r", encoding="utf-8") as handle:
            second = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(sorted(first, key=lambda s: s["owner"]),
                         sorted(second, key=lambda s: s["owner"]))


class RateLimitTest(FakeSyncTestCase):
    """429 handling: wait out Retry-After, retry the same request, and never
    treat the 429 itself as data."""

    def test_429_on_a_get_is_retried_after_sleeping_retry_after(self):
        accounts = [{"id": "a-1", "owner": "kim", "active": True}]
        items = [{"id": "i-1", "owner": "kim", "status": "open", "points": 1}]
        # ledger's 1st request (accounts page 1) gets rate limited.
        pair = FakePair(accounts, items, rate_limited_ledger={1})

        sleeps = []
        with unittest.mock.patch("orgsync.sync.time.sleep", side_effect=sleeps.append):
            code, _, ledger = self.run_sync(pair)

        self.assertEqual(code, 0)
        self.assertEqual(sleeps, [1.0])
        self.assertEqual(self.read_jsonl("summary.jsonl"),
                         [{"owner": "kim", "open_items": 1, "done_items": 0,
                           "total_points": 1}])
        # the retried GET landed, and no phantom accounts page was consumed.
        get_accounts = [c for c in ledger._opener.calls
                        if c[0] == "GET" and c[1] == "/v1/accounts"]
        self.assertEqual(len(get_accounts), 2)  # the 429'd attempt + the retry

    def test_429_on_a_post_retries_the_identical_post(self):
        accounts = [{"id": "a-1", "owner": "kim", "active": True}]
        items = [{"id": "i-1", "owner": "kim", "status": "open", "points": 1}]
        pair = FakePair(accounts, items, page_size=10)
        # figure out which ledger ordinal the POST will be: accounts(1) + summaries(1) + post(1)=3
        pair.rate_limited_ledger = {3}

        with unittest.mock.patch("orgsync.sync.time.sleep"):
            code, _, ledger = self.run_sync(pair)

        self.assertEqual(code, 0)
        self.assertEqual(len(pair.summaries), 1)
        self.assertEqual(pair.summaries[0], {"owner": "kim", "open_items": 1,
                                             "done_items": 0, "total_points": 1})


class WellFormedPredicateTest(unittest.TestCase):
    """Direct tests of the validation rules, including the bool/int trap."""

    def test_account_active_must_be_a_real_bool_not_an_int(self):
        self.assertTrue(_is_well_formed_account({"id": "a", "owner": "o", "active": True}))
        self.assertFalse(_is_well_formed_account({"id": "a", "owner": "o", "active": 1}))

    def test_account_requires_string_id_and_owner(self):
        self.assertFalse(_is_well_formed_account({"id": 1, "owner": "o", "active": True}))
        self.assertFalse(_is_well_formed_account({"id": "a", "owner": None, "active": True}))

    def test_item_points_must_be_a_real_int_not_a_bool_or_float_or_negative(self):
        base = {"id": "i", "owner": "o", "status": "open"}
        self.assertTrue(_is_well_formed_item({**base, "points": 0}))
        self.assertFalse(_is_well_formed_item({**base, "points": True}))
        self.assertFalse(_is_well_formed_item({**base, "points": 1.0}))
        self.assertFalse(_is_well_formed_item({**base, "points": -1}))

    def test_item_status_must_be_exactly_open_or_done(self):
        base = {"id": "i", "owner": "o", "points": 1}
        self.assertTrue(_is_well_formed_item({**base, "status": "open"}))
        self.assertTrue(_is_well_formed_item({**base, "status": "done"}))
        self.assertFalse(_is_well_formed_item({**base, "status": "Open"}))

    def test_extra_fields_do_not_make_a_record_malformed(self):
        self.assertTrue(_is_well_formed_account(
            {"id": "a", "owner": "o", "active": True, "note": "x"}))
        self.assertTrue(_is_well_formed_item(
            {"id": "i", "owner": "o", "status": "open", "points": 1, "note": "x"}))


# ── Live integration against the real services in config.json ──────────────


def _live_fetch_all(client, path, key):
    """Independent pagination + 429 retry, so the recomputation below does
    not depend on the correctness of anything in `orgsync.sync`."""
    records = []
    page = 1
    while page is not None:
        while True:
            response = client.get(path, {"page": page})
            if response.status == 429:
                time.sleep(float(response.headers.get("Retry-After", "1")))
                continue
            break
        data = response.json()
        records.extend(data.get(key) or [])
        page = data.get("next_page")
    return records


def _well_formed_account(record):
    return (isinstance(record, dict) and isinstance(record.get("id"), str)
            and isinstance(record.get("owner"), str)
            and type(record.get("active")) is bool)


def _well_formed_item(record):
    if not isinstance(record, dict):
        return False
    if not isinstance(record.get("id"), str) or not isinstance(record.get("owner"), str):
        return False
    if record.get("status") not in ("open", "done"):
        return False
    points = record.get("points")
    return type(points) is int and points >= 0


def _config_available():
    try:
        load_config(DEFAULT_CONFIG_PATH)
        return True
    except Exception:
        return False


@unittest.skipUnless(_config_available(), "no config.json / services for live tests")
class LiveSyncTest(unittest.TestCase):
    """End-to-end against the real fixture services named in config.json.

    The expectation is recomputed here from first principles (independent
    pagination + the spec's well-formedness rules), not by re-using
    `orgsync.sync`'s helpers, so this can't pass merely by agreeing with a
    bug in the implementation.
    """

    def setUp(self):
        self.config = load_config(DEFAULT_CONFIG_PATH)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _clients(self):
        return (HttpClient(self.config.directory_url), HttpClient(self.config.ledger_url))

    def test_sync_matches_independent_recomputation(self):
        directory, ledger = self._clients()
        accounts_before = _live_fetch_all(ledger, "/v1/accounts", "accounts")
        items_before = _live_fetch_all(directory, "/v1/items", "items")

        active_owners = {a["owner"] for a in accounts_before
                         if _well_formed_account(a) and a["active"]}
        counts = {owner: {"open": 0, "done": 0, "points": 0} for owner in active_owners}
        by_id = {}
        expected_errors_directory = 0
        for item in items_before:
            if _well_formed_item(item):
                by_id[item["id"]] = item
            else:
                expected_errors_directory += 1
        expected_orphan_ids = set()
        for item in by_id.values():
            owner = item["owner"]
            if owner in active_owners:
                bucket = counts[owner]
                bucket["open" if item["status"] == "open" else "done"] += 1
                bucket["points"] += item["points"]
            else:
                expected_orphan_ids.add(item["id"])
        expected_summaries = {
            owner: {"owner": owner, "open_items": c["open"], "done_items": c["done"],
                   "total_points": c["points"]}
            for owner, c in counts.items()
        }
        expected_errors_ledger = sum(1 for a in accounts_before if not _well_formed_account(a))

        out_dir = os.path.join(self.tmp.name, "out")
        code = run_sync(self.config, out_dir=out_dir)
        self.assertEqual(code, 0)

        with open(os.path.join(out_dir, "summary.jsonl"), "r", encoding="utf-8") as handle:
            got_summaries = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual({s["owner"] for s in got_summaries}, set(expected_summaries))
        for summary in got_summaries:
            self.assertEqual(summary, expected_summaries[summary["owner"]])

        with open(os.path.join(out_dir, "orphans.jsonl"), "r", encoding="utf-8") as handle:
            got_orphans = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual({o["id"] for o in got_orphans}, expected_orphan_ids)

        with open(os.path.join(out_dir, "errors.jsonl"), "r", encoding="utf-8") as handle:
            got_errors = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(sum(1 for e in got_errors if e["service"] == "directory"),
                         expected_errors_directory)
        self.assertEqual(sum(1 for e in got_errors if e["service"] == "ledger"),
                         expected_errors_ledger)
        for entry in got_errors:
            self.assertIn(entry["service"], ("directory", "ledger"))

        # the ledger now actually has a summary for every active owner.
        ledger2 = HttpClient(self.config.ledger_url)
        summaries_after = _live_fetch_all(ledger2, "/v1/summaries", "summaries")
        owners_after = {s["owner"] for s in summaries_after if isinstance(s, dict)}
        self.assertTrue(active_owners <= owners_after)

    def test_sync_command_exits_zero(self):
        out_dir = os.path.join(self.tmp.name, "out-cli")
        code = run_sync(self.config, out_dir=out_dir)
        self.assertEqual(code, 0)
        for name in ("orphans.jsonl", "errors.jsonl", "summary.jsonl"):
            self.assertTrue(os.path.exists(os.path.join(out_dir, name)))

    def test_second_run_is_idempotent_and_issues_no_write_requests(self):
        out_dir_1 = os.path.join(self.tmp.name, "out-1")
        out_dir_2 = os.path.join(self.tmp.name, "out-2")

        self.assertEqual(run_sync(self.config, out_dir=out_dir_1), 0)

        original_post_json = HttpClient.post_json
        calls = []

        def spy_post_json(self, path, payload, headers=None):
            calls.append((path, payload, headers))
            return original_post_json(self, path, payload, headers)

        with unittest.mock.patch.object(HttpClient, "post_json", spy_post_json):
            self.assertEqual(run_sync(self.config, out_dir=out_dir_2), 0)

        self.assertEqual(calls, [])

        for name in ("orphans.jsonl", "errors.jsonl", "summary.jsonl"):
            with open(os.path.join(out_dir_1, name), "r", encoding="utf-8") as handle:
                first = sorted(handle.readlines())
            with open(os.path.join(out_dir_2, name), "r", encoding="utf-8") as handle:
                second = sorted(handle.readlines())
            self.assertEqual(first, second, "%s differs between runs" % name)


if __name__ == "__main__":
    unittest.main()
