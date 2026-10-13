"""Tests for `orgsync sync`.

Most cases run against an in-process fake of the Directory and the Ledger
(`http.server` in a thread) so every behaviour — pagination, rate limiting,
malformed and duplicated records, idempotency, the no-rejected-write rule —
can be exercised deterministically and fast. One smoke test runs the real
command against the services named in `config.json` when they are reachable.

Run: `python3 -m unittest test_orgsync`
"""

from __future__ import annotations

import contextlib
import http.server
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from orgsync.config import Config, load_config
from orgsync.models import Summary
from orgsync.service import Service, ServiceError, parse_retry_after
from orgsync.sync import reconcile, run_sync
from orgsync.validation import parse_account, parse_item

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE_SIZE = 3


# ---------------------------------------------------------------------------
# Fake services
# ---------------------------------------------------------------------------

class FakeState:
    """Data and request log shared by both fake services.

    `rate_limit_every`: every Nth request (per service) is answered 429 with
    `Retry-After: <retry_after>`; the next request to that service must arrive
    after that delay or it is recorded as `early`.
    """

    def __init__(self, accounts, items, summaries=None,
                 rate_limit_every: int = 0, retry_after: float = 0.2) -> None:
        self.accounts = list(accounts)
        self.items = list(items)
        self.summaries: List[Dict[str, Any]] = list(summaries or [])
        self.keys: Dict[str, Dict[str, Any]] = {}
        self.rate_limit_every = rate_limit_every
        self.retry_after = retry_after
        self.requests: Dict[str, List[Dict[str, Any]]] = {"directory": [], "ledger": []}
        self.not_before: Dict[str, float] = {"directory": 0.0, "ledger": 0.0}
        self.early: List[Dict[str, Any]] = []
        self.lock = threading.Lock()

    def active_owners(self):
        return {a["owner"] for a in self.accounts if parse_account(a) is not None and a["active"]}


def _page(records, page):
    start = (page - 1) * PAGE_SIZE
    chunk = records[start:start + PAGE_SIZE]
    next_page = page + 1 if start + PAGE_SIZE < len(records) else None
    return chunk, next_page


def make_handler(state: FakeState, service: str):
    import time

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence
            pass

        def _send(self, status, body=None, headers=None):
            data = b"" if body is None else json.dumps(body).encode()
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _record(self, method, body=None):
            parsed = urllib.parse.urlparse(self.path)
            entry = {"method": method, "path": parsed.path, "query": parsed.query,
                     "idempotency_key": self.headers.get("Idempotency-Key"),
                     "body": body, "status": None, "ts": time.monotonic()}
            with state.lock:
                if entry["ts"] < state.not_before[service]:
                    state.early.append(entry)
                state.requests[service].append(entry)
                n = len(state.requests[service])
                if state.rate_limit_every and n % state.rate_limit_every == 0:
                    entry["status"] = 429
                    state.not_before[service] = entry["ts"] + state.retry_after
                    return entry, True
            return entry, False

        def do_GET(self):
            entry, limited = self._record("GET")
            if limited:
                return self._send(429, headers={"Retry-After": str(state.retry_after)})
            parsed = urllib.parse.urlparse(self.path)
            page = int(urllib.parse.parse_qs(parsed.query).get("page", ["1"])[0])
            if service == "directory" and parsed.path == "/v1/items":
                chunk, nxt = _page(state.items, page)
                entry["status"] = 200
                return self._send(200, {"items": chunk, "next_page": nxt})
            if service == "ledger" and parsed.path == "/v1/accounts":
                chunk, nxt = _page(state.accounts, page)
                entry["status"] = 200
                return self._send(200, {"accounts": chunk, "next_page": nxt})
            if service == "ledger" and parsed.path == "/v1/summaries":
                chunk, nxt = _page(state.summaries, page)
                entry["status"] = 200
                return self._send(200, {"summaries": chunk, "next_page": nxt})
            entry["status"] = 404
            self._send(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
            entry, limited = self._record("POST", body)
            if limited:
                return self._send(429, headers={"Retry-After": str(state.retry_after)})
            if service != "ledger" or urllib.parse.urlparse(self.path).path != "/v1/summaries":
                entry["status"] = 404
                return self._send(404, {"error": "not found"})
            key = self.headers.get("Idempotency-Key")
            if not key or not isinstance(body, dict):
                entry["status"] = 400
                return self._send(400, {"error": "bad request"})
            shape_ok = (isinstance(body.get("owner"), str)
                        and all(isinstance(body.get(f), int) and not isinstance(body.get(f), bool)
                                for f in ("open_items", "done_items", "total_points")))
            if not shape_ok or body["owner"] not in state.active_owners():
                entry["status"] = 422
                return self._send(422, {"error": "invalid summary"})
            with state.lock:
                if key in state.keys:
                    if state.keys[key] == body:
                        entry["status"] = 200
                        return self._send(200, body)
                    entry["status"] = 409
                    return self._send(409, {"error": "key reused with different body"})
                state.keys[key] = body
                state.summaries.append(dict(body))
            entry["status"] = 201
            self._send(201, body)

    return Handler


class FakeServices:
    """Two threaded HTTP servers (Directory, Ledger) backed by one FakeState."""

    def __init__(self, state: FakeState) -> None:
        self.state = state
        self.servers = {}
        self.threads = []
        for name in ("directory", "ledger"):
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, name))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.servers[name] = server
            self.threads.append(thread)

    def url(self, name):
        host, port = self.servers[name].server_address
        return "http://%s:%d" % (host, port)

    @property
    def config(self) -> Config:
        return Config(self.url("directory"), self.url("ledger"))

    def close(self):
        for server in self.servers.values():
            server.shutdown()
            server.server_close()


# ---------------------------------------------------------------------------
# Fixture data mirroring the shape of the real services
# ---------------------------------------------------------------------------

ACCOUNTS = [
    {"id": "acct-01", "owner": "amara", "active": True},
    {"id": "acct-02", "owner": "bo", "active": True, "note": "extra field ignored"},
    {"id": "acct-03", "owner": "chen", "active": False},
    {"id": "acct-04", "owner": "dee", "active": True},
    {"id": "acct-05", "owner": "eli", "active": True},
    {"id": "acct-06", "owner": "fen", "active": "yes"},        # malformed: not a bool
    {"id": "acct-07", "owner": "gus", "active": False},
    {"id": 8, "owner": "hal", "active": True},                 # malformed: id not a str
    {"owner": "ida", "active": True},                          # malformed: missing id
]

ITEMS = [
    {"id": "item-101", "owner": "amara", "status": "open", "points": 3},
    {"id": "item-102", "owner": "amara", "status": "done", "points": 2},
    {"id": "item-103", "owner": "bo", "status": "open", "points": 5},
    {"id": "item-104", "owner": "chen", "status": "open", "points": 8},      # inactive owner
    {"id": "item-105", "owner": "eli", "status": "done", "points": 1},
    {"id": "item-106", "owner": "zara", "status": "open", "points": 4},      # unknown owner
    {"id": "item-107", "owner": "fen", "status": "open", "points": 2},       # malformed account's owner
    {"id": "item-108", "owner": "bo", "status": "done", "points": 1},
    {"id": "item-103", "owner": "bo", "status": "open", "points": 5},        # duplicate delivery
    {"id": "item-109", "owner": "amara", "status": "open"},                  # malformed: no points
    {"id": "item-110", "owner": "amara", "status": "open", "points": 7, "tag": "x"},
    {"id": "item-111", "owner": "eli", "status": "open", "points": 2},
    {"id": "item-112", "owner": "eli", "status": "open", "points": "2"},     # malformed: str points
    {"id": "item-113", "owner": "eli", "status": "closed", "points": 2},     # malformed: bad status
    {"id": "item-114", "owner": "eli", "status": "open", "points": -1},      # malformed: negative
    {"id": "item-115", "owner": "eli", "status": "open", "points": True},    # malformed: bool points
    {"id": "item-109", "owner": "amara", "status": "open"},                  # malformed, delivered twice
    {"id": "item-116", "owner": "hal", "status": "open", "points": 1},       # owner's account malformed
    {"id": "item-104", "owner": "chen", "status": "open", "points": 8},      # orphan delivered twice
]

EXPECTED_SUMMARIES = {
    "amara": {"owner": "amara", "open_items": 2, "done_items": 1, "total_points": 12},
    "bo": {"owner": "bo", "open_items": 1, "done_items": 1, "total_points": 6},
    "dee": {"owner": "dee", "open_items": 0, "done_items": 0, "total_points": 0},
    "eli": {"owner": "eli", "open_items": 1, "done_items": 1, "total_points": 3},
}
EXPECTED_ORPHAN_IDS = {"item-104", "item-106", "item-107", "item-116"}
EXPECTED_ERROR_RECORDS = (
    [{"service": "ledger", "record": a} for a in ACCOUNTS if parse_account(a) is None]
    + [{"service": "directory", "record": i} for i in ITEMS[:16] if parse_item(i) is None])


def read_jsonl(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def canon(record):
    return json.dumps(record, sort_keys=True)


def quiet_sync(config, out_dir, **kw):
    return run_sync(config, out_dir, log=lambda msg: None, **kw)


# ---------------------------------------------------------------------------
# Unit tests: validation and the pure reconcile step
# ---------------------------------------------------------------------------

class ValidationTest(unittest.TestCase):
    def test_well_formed_account(self):
        account = parse_account({"id": "a", "owner": "kim", "active": True, "x": 1})
        self.assertEqual((account.id, account.owner, account.active), ("a", "kim", True))

    def test_malformed_accounts(self):
        for raw in ({"id": "a", "owner": "kim"},               # missing active
                    {"id": "a", "owner": "kim", "active": "yes"},
                    {"id": "a", "owner": "kim", "active": 1},
                    {"id": "a", "owner": 7, "active": True},
                    {"owner": "kim", "active": True},
                    ["id", "owner", "active"], None, "kim"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_account(raw))

    def test_well_formed_item(self):
        item = parse_item({"id": "i", "owner": "kim", "status": "done", "points": 0, "e": 1})
        self.assertEqual((item.id, item.owner, item.status, item.points), ("i", "kim", "done", 0))

    def test_malformed_items(self):
        for raw in ({"id": "i", "owner": "kim", "status": "open"},
                    {"id": "i", "owner": "kim", "status": "open", "points": "3"},
                    {"id": "i", "owner": "kim", "status": "open", "points": 3.0},
                    {"id": "i", "owner": "kim", "status": "open", "points": True},
                    {"id": "i", "owner": "kim", "status": "open", "points": -1},
                    {"id": "i", "owner": "kim", "status": "closed", "points": 1},
                    {"id": "i", "owner": "kim", "status": None, "points": 1},
                    {"id": "i", "status": "open", "points": 1},
                    {"id": None, "owner": "kim", "status": "open", "points": 1},
                    [], "item"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_item(raw))


class ReconcileTest(unittest.TestCase):
    def test_worked_example(self):
        plan = reconcile([{"id": "a", "owner": "kim", "active": True}],
                         [{"id": "1", "owner": "kim", "status": "open", "points": 3},
                          {"id": "2", "owner": "kim", "status": "open", "points": 4},
                          {"id": "3", "owner": "kim", "status": "done", "points": 2}], [])
        self.assertEqual(plan.required["kim"].to_dict(),
                         {"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9})

    def test_full_fixture(self):
        plan = reconcile(ACCOUNTS, ITEMS, [])
        self.assertEqual({o: s.to_dict() for o, s in plan.required.items()}, EXPECTED_SUMMARIES)
        self.assertEqual({o["id"] for o in plan.orphans}, EXPECTED_ORPHAN_IDS)
        self.assertEqual(len(plan.orphans), len(EXPECTED_ORPHAN_IDS))
        self.assertEqual(sorted(map(canon, plan.errors)), sorted(map(canon, EXPECTED_ERROR_RECORDS)))

    def test_active_account_without_items_gets_zero_summary(self):
        plan = reconcile([{"id": "a", "owner": "dee", "active": True}], [], [])
        self.assertEqual(plan.required["dee"], Summary("dee", 0, 0, 0))

    def test_inactive_and_malformed_accounts_get_no_summary(self):
        plan = reconcile([{"id": "a", "owner": "chen", "active": False},
                          {"id": "b", "owner": "fen", "active": "yes"}],
                         [{"id": "1", "owner": "fen", "status": "open", "points": 1}], [])
        self.assertEqual(plan.required, {})
        self.assertEqual([o["id"] for o in plan.orphans], ["1"])

    def test_existing_summaries_are_not_reposted(self):
        plan = reconcile([{"id": "a", "owner": "kim", "active": True},
                          {"id": "b", "owner": "lee", "active": True}], [],
                         [{"owner": "kim", "open_items": 0, "done_items": 0, "total_points": 0}])
        self.assertEqual([s.owner for s in plan.to_post], ["lee"])


class RetryAfterTest(unittest.TestCase):
    def test_delta_seconds_and_fallbacks(self):
        self.assertEqual(parse_retry_after("2", 0.0), 2.0)
        self.assertEqual(parse_retry_after(" 0.5 ", 0.0), 0.5)
        self.assertEqual(parse_retry_after("-3", 0.0), 0.0)
        self.assertGreater(parse_retry_after(None, 0.0), 0.0)
        self.assertGreater(parse_retry_after("garbage", 0.0), 0.0)

    def test_http_date(self):
        import email.utils
        now = 1_700_000_000.0
        stamp = email.utils.formatdate(now + 30, usegmt=True)
        self.assertAlmostEqual(parse_retry_after(stamp, now), 30.0, places=0)


# ---------------------------------------------------------------------------
# End-to-end against the fake services
# ---------------------------------------------------------------------------

class FakeServiceTestCase(unittest.TestCase):
    rate_limit_every = 0
    summaries: List[Dict[str, Any]] = []

    def setUp(self):
        self.state = FakeState(ACCOUNTS, ITEMS, self.summaries,
                               rate_limit_every=self.rate_limit_every, retry_after=0.15)
        self.services = FakeServices(self.state)
        self.addCleanup(self.services.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "out")

    def posts(self):
        return [r for r in self.state.requests["ledger"] if r["method"] == "POST"]

    def assert_outputs_match_fixture(self):
        summaries = read_jsonl(os.path.join(self.out, "summary.jsonl"))
        self.assertEqual({s["owner"]: s for s in summaries}, EXPECTED_SUMMARIES)
        self.assertEqual(len(summaries), len(EXPECTED_SUMMARIES))
        orphans = read_jsonl(os.path.join(self.out, "orphans.jsonl"))
        self.assertEqual({o["id"] for o in orphans}, EXPECTED_ORPHAN_IDS)
        self.assertEqual(len(orphans), len(EXPECTED_ORPHAN_IDS))
        errors = read_jsonl(os.path.join(self.out, "errors.jsonl"))
        self.assertEqual(sorted(map(canon, errors)), sorted(map(canon, EXPECTED_ERROR_RECORDS)))


class SyncEndToEndTest(FakeServiceTestCase):
    def test_first_run_posts_every_missing_summary_once_and_writes_files(self):
        self.assertEqual(quiet_sync(self.services.config, self.out), 0)
        self.assert_outputs_match_fixture()

        posts = self.posts()
        self.assertEqual({p["idempotency_key"] for p in posts},
                         {"sync-" + o for o in EXPECTED_SUMMARIES})
        self.assertEqual(len(posts), len(EXPECTED_SUMMARIES))
        for post in posts:
            self.assertEqual(post["status"], 201)
            self.assertEqual(post["body"], EXPECTED_SUMMARIES[post["body"]["owner"]])
        self.assertEqual({s["owner"]: s for s in self.state.summaries}, EXPECTED_SUMMARIES)

    def test_reads_every_page_of_every_list(self):
        quiet_sync(self.services.config, self.out)
        gets = [(r["path"], r["query"]) for r in
                self.state.requests["directory"] + self.state.requests["ledger"]
                if r["method"] == "GET"]
        self.assertEqual(set(gets), {
            ("/v1/items", "page=%d" % p) for p in range(1, 8)
        } | {("/v1/accounts", "page=%d" % p) for p in range(1, 4)} | {("/v1/summaries", "page=1")})

    def test_second_run_issues_no_writes_and_leaves_files_unchanged(self):
        quiet_sync(self.services.config, self.out)
        before = {name: read_text(os.path.join(self.out, name))
                  for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")}
        first_posts = len(self.posts())

        self.assertEqual(quiet_sync(self.services.config, self.out), 0)

        self.assertEqual(len(self.posts()), first_posts)
        after = {name: read_text(os.path.join(self.out, name))
                 for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")}
        self.assertEqual(after, before)
        self.assert_outputs_match_fixture()

    def test_no_post_is_ever_rejected(self):
        quiet_sync(self.services.config, self.out)
        quiet_sync(self.services.config, self.out)
        self.assertEqual([p for p in self.posts() if p["status"] in (400, 409, 422)], [])

    def test_creates_out_dir_when_absent(self):
        nested = os.path.join(self.tmp.name, "a", "b", "out")
        self.assertEqual(quiet_sync(self.services.config, nested), 0)
        for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl"):
            self.assertTrue(os.path.exists(os.path.join(nested, name)))


class ExistingSummariesTest(FakeServiceTestCase):
    summaries = [{"owner": "amara", "open_items": 2, "done_items": 1, "total_points": 12},
                 {"owner": "chen", "open_items": 0, "done_items": 0, "total_points": 0}]

    def test_only_missing_summaries_are_posted(self):
        self.assertEqual(quiet_sync(self.services.config, self.out), 0)
        self.assertEqual({p["idempotency_key"] for p in self.posts()},
                         {"sync-bo", "sync-dee", "sync-eli"})
        posted = read_jsonl(os.path.join(self.out, "summary.jsonl"))
        self.assertEqual({s["owner"] for s in posted}, {"bo", "dee", "eli"})
        for s in posted:
            self.assertEqual(s, EXPECTED_SUMMARIES[s["owner"]])


class RateLimitTest(FakeServiceTestCase):
    rate_limit_every = 2   # every second request to each service is a 429

    def test_429s_are_retried_after_waiting_and_never_counted(self):
        self.assertEqual(quiet_sync(self.services.config, self.out), 0)
        limited = [r for s in ("directory", "ledger") for r in self.state.requests[s]
                   if r["status"] == 429]
        self.assertGreater(len(limited), 0)
        self.assertTrue(any(r["method"] == "POST" for r in limited),
                        "fixture should have rate limited at least one POST")
        self.assertEqual(self.state.early, [], "a request was sent before Retry-After elapsed")
        self.assert_outputs_match_fixture()
        # The retried POSTs were accepted exactly once each.
        accepted = [p for p in self.posts() if p["status"] == 201]
        self.assertEqual(sorted(p["idempotency_key"] for p in accepted),
                         sorted("sync-" + o for o in EXPECTED_SUMMARIES))
        self.assertEqual([p for p in self.posts() if p["status"] in (400, 409, 422)], [])

    def test_second_run_still_idempotent_under_rate_limiting(self):
        quiet_sync(self.services.config, self.out)
        before = read_text(os.path.join(self.out, "summary.jsonl"))
        posts = len(self.posts())
        self.assertEqual(quiet_sync(self.services.config, self.out), 0)
        self.assertEqual(len(self.posts()), posts)
        self.assertEqual(read_text(os.path.join(self.out, "summary.jsonl")), before)
        self.assertEqual(self.state.early, [])


class ServiceUnitTest(unittest.TestCase):
    """Service-level behaviour without sockets."""

    class Opener:
        def __init__(self, outcomes):
            self.outcomes, self.requests = list(outcomes), []

        def open(self, request, timeout=None):
            self.requests.append(request)
            status, body, headers = self.outcomes.pop(0)
            import email.message
            msg = email.message.Message()
            for k, v in headers.items():
                msg[k] = v
            if status >= 400:
                raise urllib.error.HTTPError(request.full_url, status, "x", msg, io.BytesIO(body))
            resp = io.BytesIO(body)
            resp.status, resp.headers = status, msg
            resp.__enter__ = lambda: resp
            return contextlib.nullcontext(resp)

    def make(self, outcomes):
        from orgsync.http_client import HttpClient
        opener = self.Opener(outcomes)
        slept = []
        service = Service("t", HttpClient("http://t", opener=opener),
                          sleep=slept.append, clock=lambda: 0.0, log=lambda m: None)
        return service, opener, slept

    def test_429_sleeps_for_retry_after_then_resends_same_request(self):
        service, opener, slept = self.make([
            (429, b"", {"Retry-After": "3"}),
            (200, b'{"items": [1], "next_page": null}', {})])
        self.assertEqual(service.fetch_all("/v1/items", "items"), [1])
        self.assertEqual(len(slept), 1)
        self.assertGreaterEqual(slept[0], 3.0)
        self.assertEqual([r.full_url for r in opener.requests],
                         ["http://t/v1/items?page=1"] * 2)

    def test_pagination_follows_next_page_until_null(self):
        service, opener, _ = self.make([
            (200, b'{"items": [1, 2], "next_page": 2}', {}),
            (200, b'{"items": [3], "next_page": 3}', {}),
            (200, b'{"items": [], "next_page": null}', {})])
        self.assertEqual(service.fetch_all("/v1/items", "items"), [1, 2, 3])
        self.assertEqual([r.full_url for r in opener.requests],
                         ["http://t/v1/items?page=%d" % p for p in (1, 2, 3)])

    def test_unexpected_status_on_read_is_an_error(self):
        service, _, _ = self.make([(404, b"{}", {})])
        with self.assertRaises(ServiceError):
            service.fetch_all("/v1/items", "items")


# ---------------------------------------------------------------------------
# Smoke test against the real services from config.json (if reachable)
# ---------------------------------------------------------------------------

def _reachable(url):
    try:
        urllib.request.urlopen(url + "/v1/accounts?page=1", timeout=2).close()
        return True
    except urllib.error.HTTPError as exc:
        exc.close()
        return exc.code == 429 or exc.code < 500
    except (urllib.error.URLError, OSError):
        return False


class RealServicesSmokeTest(unittest.TestCase):
    """Runs `python3 -m orgsync sync` twice against the configured services.

    The ledger may already hold summaries from earlier runs, so this checks
    the invariants that hold regardless: exit 0, every active well-formed
    account ends up with exactly one summary in the ledger, the second run
    changes nothing, and anything this tool posted matches the ledger.
    """

    def setUp(self):
        config_path = os.path.join(HERE, "config.json")
        if not os.path.exists(config_path):
            self.skipTest("no config.json")
        self.config = load_config(config_path)
        if not _reachable(self.config.ledger_url):
            self.skipTest("ledger not reachable")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "out")

    def run_cli(self):
        return subprocess.run([sys.executable, "-m", "orgsync", "sync", "--config",
                               os.path.join(HERE, "config.json"), "--out-dir", self.out],
                              cwd=HERE, capture_output=True, text=True, timeout=300)

    def test_sync_twice(self):
        first = self.run_cli()
        self.assertEqual(first.returncode, 0, first.stderr)
        files = {n: read_text(os.path.join(self.out, n))
                 for n in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")}

        second = self.run_cli()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("0 summary(ies) posted", second.stderr)
        self.assertEqual({n: read_text(os.path.join(self.out, n)) for n in files}, files)

        # Cross-check against what the services hold now.
        from orgsync.http_client import HttpClient
        ledger = Service("ledger", HttpClient(self.config.ledger_url), log=lambda m: None)
        directory = Service("directory", HttpClient(self.config.directory_url), log=lambda m: None)
        accounts = ledger.fetch_all("/v1/accounts", "accounts")
        items = directory.fetch_all("/v1/items", "items")
        held = ledger.fetch_all("/v1/summaries", "summaries")
        plan = reconcile(accounts, items, held)
        self.assertEqual(plan.to_post, [], "ledger is still missing summaries")
        held_by_owner = {s["owner"]: s for s in held}
        for line in read_jsonl(os.path.join(self.out, "summary.jsonl")):
            self.assertIn(line["owner"], plan.required)
            self.assertEqual(line, plan.required[line["owner"]].to_dict())
            self.assertEqual(line, held_by_owner[line["owner"]])
        self.assertEqual(sorted(map(canon, read_jsonl(os.path.join(self.out, "orphans.jsonl")))),
                         sorted(map(canon, plan.orphans)))
        self.assertEqual(sorted(map(canon, read_jsonl(os.path.join(self.out, "errors.jsonl")))),
                         sorted(map(canon, plan.errors)))


if __name__ == "__main__":
    unittest.main()
