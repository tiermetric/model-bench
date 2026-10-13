"""Tests for `orgsync sync` against fake in-process Directory/Ledger services.

Standard library only. Every test spins up its own HTTP services on
ephemeral ports, so nothing here depends on the shared dev fixtures — these
tests pass in any environment that can run them.
"""

import http.server
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.parse

from orgsync.__main__ import main as orgsync_main
from orgsync.config import Config
from orgsync.sync import run_sync


class _JsonHandler(http.server.BaseHTTPRequestHandler):
    """Quiet handler that always answers with a JSON body (or none)."""

    server_version = "test-fake/1.0"

    def log_message(self, *args):
        pass

    def _send(self, status, obj, extra_headers=None):
        body = b"" if obj is None else json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)


class DirectoryFake:
    """Serves GET /v1/items?page=N from fixed pages, with optional 429s."""

    def __init__(self, pages, get_429s=0, retry_after="1"):
        self.pages = dict(pages)
        self.get_429s_remaining = get_429s
        self.retry_after = retry_after
        self.requests = []

    def handler(self):
        fake = self

        class Handler(_JsonHandler):
            def do_GET(self):
                t_recv = time.monotonic()
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                headers = {}
                if parsed.path != "/v1/items":
                    status, payload = 404, {"error": "not found"}
                elif fake.get_429s_remaining > 0:
                    fake.get_429s_remaining -= 1
                    status, payload = 429, None
                    headers = {"Retry-After": fake.retry_after}
                else:
                    try:
                        page = int(query.get("page", ["1"])[0])
                    except (ValueError, TypeError):
                        page = -1
                    if page < 1:
                        status, payload = 400, {"error": "bad page"}
                    else:
                        items, nxt = fake.pages.get(page, ([], None))
                        status, payload = 200, {"items": items, "next_page": nxt}
                t_sent = time.monotonic()
                self._send(status, payload, headers)
                fake.requests.append({"t_recv": t_recv, "method": "GET",
                                      "path": self.path, "status": status,
                                      "t_sent": t_sent, "key": None, "raw": None})

        return Handler


class LedgerFake:
    """Serves ledger endpoints with real POST semantics: 201/200/409/422."""

    def __init__(self, accounts_pages, summaries_seed=(), summaries_page_size=10,
                 summaries_raw_extra=(), get_429s=0, post_429s=0, retry_after="1"):
        self.accounts_pages = dict(accounts_pages)
        self.summaries_page_size = summaries_page_size
        self.summaries_raw_extra = list(summaries_raw_extra)
        self.store = {}
        for summary in summaries_seed:
            if isinstance(summary, dict) and isinstance(summary.get("owner"), str):
                self.store[summary["owner"]] = dict(summary)
        self.idempotency = {}
        self.get_429s_remaining = get_429s
        self.post_429s_remaining = post_429s
        self.retry_after = retry_after
        self.requests = []
        self.active_owners = set()
        for items, _next_page in self.accounts_pages.values():
            for account in items:
                if (isinstance(account, dict)
                        and isinstance(account.get("id"), str)
                        and isinstance(account.get("owner"), str)
                        and isinstance(account.get("active"), bool)
                        and account["active"]):
                    self.active_owners.add(account["owner"])

    @staticmethod
    def _valid_body(body):
        if not isinstance(body, dict):
            return False
        if not isinstance(body.get("owner"), str):
            return False
        for key in ("open_items", "done_items", "total_points"):
            value = body.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return False
        return True

    def _summaries_list(self):
        ordered = sorted(self.store.values(), key=lambda s: s["owner"])
        return ordered + self.summaries_raw_extra

    def _accounts_page(self, query):
        try:
            page = int(query.get("page", ["1"])[0])
        except (ValueError, TypeError):
            return 400, {"error": "bad page"}
        if page < 1:
            return 400, {"error": "bad page"}
        items, nxt = self.accounts_pages.get(page, ([], None))
        return 200, {"accounts": items, "next_page": nxt}

    def _summaries_page(self, query):
        try:
            page = int(query.get("page", ["1"])[0])
        except (ValueError, TypeError):
            return 400, {"error": "bad page"}
        if page < 1:
            return 400, {"error": "bad page"}
        full = self._summaries_list()
        size = self.summaries_page_size
        start = (page - 1) * size
        batch = full[start:start + size]
        nxt = page + 1 if start + size < len(full) else None
        return 200, {"summaries": batch, "next_page": nxt}

    def handler(self):
        fake = self

        class Handler(_JsonHandler):
            def do_GET(self):
                t_recv = time.monotonic()
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                headers = {}
                if fake.get_429s_remaining > 0:
                    fake.get_429s_remaining -= 1
                    status, payload = 429, None
                    headers = {"Retry-After": fake.retry_after}
                elif parsed.path == "/v1/accounts":
                    status, payload = fake._accounts_page(query)
                elif parsed.path == "/v1/summaries":
                    status, payload = fake._summaries_page(query)
                else:
                    status, payload = 404, {"error": "not found"}
                t_sent = time.monotonic()
                self._send(status, payload, headers)
                fake.requests.append({"t_recv": t_recv, "method": "GET",
                                      "path": self.path, "status": status,
                                      "t_sent": t_sent, "key": None, "raw": None})

            def do_POST(self):
                t_recv = time.monotonic()
                parsed = urllib.parse.urlparse(self.path)
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except (ValueError, TypeError):
                    length = 0
                raw = self.rfile.read(length) if length > 0 else b""
                key = self.headers.get("Idempotency-Key")
                headers = {}
                if parsed.path != "/v1/summaries":
                    status, payload = 404, {"error": "not found"}
                elif fake.post_429s_remaining > 0:
                    fake.post_429s_remaining -= 1
                    status, payload = 429, None
                    headers = {"Retry-After": fake.retry_after}
                else:
                    try:
                        body = json.loads(raw.decode("utf-8"))
                    except Exception:
                        body = None
                    if not key:
                        status, payload = 400, {"error": "missing Idempotency-Key"}
                    elif key in fake.idempotency:
                        if fake.idempotency[key] == body:
                            status, payload = 200, {"ok": True, "replay": True}
                        else:
                            status, payload = 409, {"error": "idempotency conflict"}
                    elif not fake._valid_body(body):
                        status, payload = 400, {"error": "invalid summary"}
                    elif body["owner"] not in fake.active_owners:
                        status, payload = 422, {"error": "unknown owner"}
                    else:
                        fake.idempotency[key] = body
                        fake.store[body["owner"]] = dict(body)
                        status, payload = 201, {"ok": True}
                t_sent = time.monotonic()
                self._send(status, payload, headers)
                fake.requests.append({"t_recv": t_recv, "method": "POST",
                                      "path": self.path, "status": status,
                                      "t_sent": t_sent, "key": key, "raw": raw})

        return Handler


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    # -- helpers ---------------------------------------------------------

    def _serve(self, handler_cls):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def _start(self, directory, ledger):
        dir_server = self._serve(directory.handler())
        led_server = self._serve(ledger.handler())
        return Config(
            directory_url="http://127.0.0.1:%d" % dir_server.server_address[1],
            ledger_url="http://127.0.0.1:%d" % led_server.server_address[1],
        )

    def _standard_fakes(self, summaries_seed=(), summaries_page_size=10,
                        summaries_raw_extra=(), dir_kwargs=None, led_kwargs=None):
        directory_pages = {
            1: ([{"id": "i1", "owner": "kim", "status": "open", "points": 3,
                   "note": "extra fields are ignored"},
                 {"id": "i2", "owner": "kim", "status": "open", "points": 4},
                 {"id": "i3", "owner": "kim", "status": "done", "points": 2},
                 {"id": "i4", "owner": "lee", "status": "open", "points": 5}], 2),
            2: ([{"id": "i4", "owner": "lee", "status": "open", "points": 5},
                 {"id": "i5", "owner": "zed", "status": "open", "points": 1},
                 {"id": "i6", "owner": "kim", "status": "open", "points": -1},
                 {"id": "i7", "owner": "lee", "status": "done", "points": "2"},
                 {"id": "i8", "owner": "max", "status": "open", "points": 2}], None),
        }
        accounts_pages = {
            1: ([{"id": "a1", "owner": "kim", "active": True},
                 {"id": "a2", "owner": "lee", "active": True},
                 {"id": "a3", "owner": "max", "active": False}], 2),
            2: ([{"id": "a4", "owner": "hal"},
                 {"id": "a5", "owner": "ivy", "active": "yes"}], None),
        }
        directory = DirectoryFake(directory_pages, **(dir_kwargs or {}))
        ledger = LedgerFake(accounts_pages, summaries_seed=summaries_seed,
                            summaries_page_size=summaries_page_size,
                            summaries_raw_extra=summaries_raw_extra,
                            **(led_kwargs or {}))
        return directory, ledger

    @staticmethod
    def _read_jsonl(path):
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    @staticmethod
    def _canon(rows):
        return sorted(json.dumps(row, sort_keys=True) for row in rows)

    def _assert_no_rejected_writes(self, ledger):
        rejected = [r for r in ledger.requests
                    if r["method"] == "POST" and r["status"] in (400, 409, 422)]
        self.assertEqual(rejected, [])

    def _assert_gaps_honored(self, requests, minimum):
        saw_429 = False
        for index, entry in enumerate(requests):
            if entry["status"] == 429:
                saw_429 = True
                nxt = requests[index + 1]
                self.assertGreaterEqual(nxt["t_recv"] - entry["t_sent"], minimum)
        self.assertTrue(saw_429, "expected at least one 429")

    # -- behavior tests --------------------------------------------------

    def test_reconciles_end_to_end(self):
        directory, ledger = self._standard_fakes()
        config = self._start(directory, ledger)
        out = os.path.join(self.tmp.name, "out")

        self.assertEqual(run_sync(config, out), 0)

        summaries = self._read_jsonl(os.path.join(out, "summary.jsonl"))
        self.assertEqual(self._canon(summaries), self._canon([
            {"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9},
            {"owner": "lee", "open_items": 1, "done_items": 0, "total_points": 5},
        ]))

        orphans = self._read_jsonl(os.path.join(out, "orphans.jsonl"))
        self.assertEqual(self._canon(orphans), self._canon([
            {"id": "i5", "owner": "zed", "status": "open", "points": 1},
            {"id": "i8", "owner": "max", "status": "open", "points": 2},
        ]))

        errors = self._read_jsonl(os.path.join(out, "errors.jsonl"))
        self.assertEqual(self._canon(errors), self._canon([
            {"service": "directory",
             "record": {"id": "i6", "owner": "kim", "status": "open", "points": -1}},
            {"service": "directory",
             "record": {"id": "i7", "owner": "lee", "status": "done", "points": "2"}},
            {"service": "ledger", "record": {"id": "a4", "owner": "hal"}},
            {"service": "ledger",
             "record": {"id": "a5", "owner": "ivy", "active": "yes"}},
        ]))

        posts = [r for r in ledger.requests if r["method"] == "POST"]
        self.assertEqual(len(posts), 2)
        self.assertEqual({p["key"] for p in posts}, {"sync-kim", "sync-lee"})
        posted = sorted((json.loads(p["raw"].decode("utf-8")) for p in posts),
                        key=lambda b: b["owner"])
        self.assertEqual(self._canon(posted), self._canon(summaries))
        self._assert_no_rejected_writes(ledger)

        dir_pages = sorted(r["path"] for r in directory.requests if r["method"] == "GET")
        self.assertEqual(dir_pages, ["/v1/items?page=1", "/v1/items?page=2"])
        led_pages = sorted(r["path"] for r in ledger.requests if r["method"] == "GET")
        self.assertEqual(led_pages, ["/v1/accounts?page=1", "/v1/accounts?page=2",
                                     "/v1/summaries?page=1"])

    def test_posts_only_missing_summaries(self):
        kim = {"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9}
        directory, ledger = self._standard_fakes(summaries_seed=[kim])
        config = self._start(directory, ledger)
        out = os.path.join(self.tmp.name, "out")

        self.assertEqual(run_sync(config, out), 0)

        posts = [r for r in ledger.requests if r["method"] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["key"], "sync-lee")
        self.assertEqual(json.loads(posts[0]["raw"].decode("utf-8")),
                         {"owner": "lee", "open_items": 1, "done_items": 0,
                          "total_points": 5})
        self._assert_no_rejected_writes(ledger)

        summaries = self._read_jsonl(os.path.join(out, "summary.jsonl"))
        self.assertEqual(self._canon(summaries), self._canon([
            kim,
            {"owner": "lee", "open_items": 1, "done_items": 0, "total_points": 5},
        ]))

    def test_second_run_issues_no_writes_and_same_files(self):
        directory, ledger = self._standard_fakes()
        config = self._start(directory, ledger)
        out = os.path.join(self.tmp.name, "out")

        self.assertEqual(run_sync(config, out), 0)
        first_bytes = {}
        for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl"):
            with open(os.path.join(out, name), "rb") as handle:
                first_bytes[name] = handle.read()
        posts_after_first = len([r for r in ledger.requests if r["method"] == "POST"])
        self.assertGreater(posts_after_first, 0)

        self.assertEqual(run_sync(config, out), 0)
        posts_total = len([r for r in ledger.requests if r["method"] == "POST"])
        self.assertEqual(posts_total, posts_after_first)
        for name, before in first_bytes.items():
            with open(os.path.join(out, name), "rb") as handle:
                self.assertEqual(handle.read(), before)

    def test_reads_every_summaries_page(self):
        seed = [{"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9},
                {"owner": "lee", "open_items": 1, "done_items": 0, "total_points": 5}]
        directory, ledger = self._standard_fakes(summaries_seed=seed,
                                                 summaries_page_size=1)
        config = self._start(directory, ledger)
        out = os.path.join(self.tmp.name, "out")

        self.assertEqual(run_sync(config, out), 0)

        summary_gets = sorted(r["path"] for r in ledger.requests
                              if r["method"] == "GET" and "summaries" in r["path"])
        self.assertEqual(summary_gets, ["/v1/summaries?page=1", "/v1/summaries?page=2"])
        posts = [r for r in ledger.requests if r["method"] == "POST"]
        self.assertEqual(posts, [])
        summaries = self._read_jsonl(os.path.join(out, "summary.jsonl"))
        self.assertEqual(self._canon(summaries), self._canon(seed))

    def test_get_rate_limit_is_honored_and_retried(self):
        directory, ledger = self._standard_fakes(dir_kwargs={"get_429s": 2,
                                                             "retry_after": "1"})
        config = self._start(directory, ledger)
        out = os.path.join(self.tmp.name, "out")

        self.assertEqual(run_sync(config, out), 0)
        self._assert_gaps_honored(directory.requests, 1.0)

        summaries = self._read_jsonl(os.path.join(out, "summary.jsonl"))
        self.assertEqual(self._canon(summaries), self._canon([
            {"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9},
            {"owner": "lee", "open_items": 1, "done_items": 0, "total_points": 5},
        ]))
        self._assert_no_rejected_writes(ledger)

    def test_post_rate_limit_is_retried_with_same_body(self):
        directory, ledger = self._standard_fakes(led_kwargs={"post_429s": 1,
                                                             "retry_after": "1"})
        config = self._start(directory, ledger)
        out = os.path.join(self.tmp.name, "out")

        self.assertEqual(run_sync(config, out), 0)
        self._assert_gaps_honored(ledger.requests, 1.0)

        posts = [r for r in ledger.requests if r["method"] == "POST"]
        self.assertEqual([p["status"] for p in posts], [429, 201, 201])
        self.assertEqual(posts[0]["raw"], posts[1]["raw"])
        self.assertEqual(posts[0]["key"], posts[1]["key"])
        self.assertEqual(ledger.store["kim"],
                         {"owner": "kim", "open_items": 2, "done_items": 1,
                          "total_points": 9})
        self.assertEqual(ledger.store["lee"],
                         {"owner": "lee", "open_items": 1, "done_items": 0,
                          "total_points": 5})

    def test_malformed_records_never_count(self):
        good_item = {"id": "g1", "owner": "kim", "status": "open", "points": 3}
        good_item_extra = {"id": "g2", "owner": "kim", "status": "done", "points": 4,
                           "extra": [1, 2]}
        bad_items = [
            {"owner": "kim", "status": "open", "points": 1},
            {"id": 5, "owner": "kim", "status": "open", "points": 1},
            {"id": "b3", "owner": None, "status": "open", "points": 1},
            {"id": "b4", "owner": "kim", "status": "closed", "points": 1},
            {"id": "b5", "owner": "kim", "status": "open", "points": True},
            {"id": "b6", "owner": "kim", "status": "open", "points": "3"},
            {"id": "b7", "owner": "kim", "status": "open", "points": 3.0},
            {"id": "b8", "owner": "kim", "status": "open", "points": -2},
            "just a string",
            42,
            None,
            ["id", "x"],
        ]
        good_account = {"id": "a1", "owner": "kim", "active": True, "extra": 1}
        bad_accounts = [
            {"owner": "kim", "active": True},
            {"id": "b", "owner": "kim", "active": 1},
            {"id": "c", "owner": "kim", "active": "true"},
            {"id": "d", "active": True},
            7,
        ]
        directory = DirectoryFake({1: ([good_item, good_item_extra] + bad_items, None)})
        ledger = LedgerFake({1: ([good_account] + bad_accounts, None)})
        config = self._start(directory, ledger)
        out = os.path.join(self.tmp.name, "out")

        self.assertEqual(run_sync(config, out), 0)

        summaries = self._read_jsonl(os.path.join(out, "summary.jsonl"))
        self.assertEqual(summaries, [{"owner": "kim", "open_items": 1, "done_items": 1,
                                      "total_points": 7}])
        self.assertEqual(self._read_jsonl(os.path.join(out, "orphans.jsonl")), [])
        errors = self._read_jsonl(os.path.join(out, "errors.jsonl"))
        expected = ([{"service": "directory", "record": r} for r in bad_items]
                    + [{"service": "ledger", "record": r} for r in bad_accounts])
        self.assertEqual(self._canon(errors), self._canon(expected))
        self._assert_no_rejected_writes(ledger)

    def test_active_account_without_items_gets_zeros(self):
        directory = DirectoryFake({1: ([{"id": "i1", "owner": "max", "status": "open",
                                          "points": 2}], None)})
        ledger = LedgerFake({1: ([{"id": "a1", "owner": "kim", "active": True},
                                   {"id": "a2", "owner": "max", "active": False}], None)})
        config = self._start(directory, ledger)
        out = os.path.join(self.tmp.name, "out")

        self.assertEqual(run_sync(config, out), 0)

        summaries = self._read_jsonl(os.path.join(out, "summary.jsonl"))
        self.assertEqual(summaries, [{"owner": "kim", "open_items": 0, "done_items": 0,
                                      "total_points": 0}])
        orphans = self._read_jsonl(os.path.join(out, "orphans.jsonl"))
        self.assertEqual(orphans, [{"id": "i1", "owner": "max", "status": "open",
                                    "points": 2}])
        posts = [r for r in ledger.requests if r["method"] == "POST"]
        self.assertEqual(len(posts), 1)
        self._assert_no_rejected_writes(ledger)

    def test_duplicate_malformed_record_written_once(self):
        bad = {"id": "b1", "owner": "kim", "status": "open"}
        good = {"id": "g1", "owner": "kim", "status": "done", "points": 2}
        directory = DirectoryFake({1: ([bad, good], 2), 2: ([bad, good], None)})
        ledger = LedgerFake({1: ([{"id": "a1", "owner": "kim", "active": True}], None)})
        config = self._start(directory, ledger)
        out = os.path.join(self.tmp.name, "out")

        self.assertEqual(run_sync(config, out), 0)

        errors = self._read_jsonl(os.path.join(out, "errors.jsonl"))
        self.assertEqual(errors, [{"service": "directory", "record": bad}])
        summaries = self._read_jsonl(os.path.join(out, "summary.jsonl"))
        self.assertEqual(summaries, [{"owner": "kim", "open_items": 0, "done_items": 1,
                                      "total_points": 2}])

    def test_unattributable_summary_is_an_error(self):
        directory = DirectoryFake({1: ([{"id": "g1", "owner": "kim", "status": "open",
                                          "points": 1}], None)})
        ledger = LedgerFake(
            {1: ([{"id": "a1", "owner": "kim", "active": True}], None)},
            summaries_seed=[{"owner": "kim", "open_items": 0, "done_items": 0,
                             "total_points": 0}],
            summaries_raw_extra=["junk", {"open_items": 1}])
        config = self._start(directory, ledger)
        out = os.path.join(self.tmp.name, "out")

        self.assertEqual(run_sync(config, out), 0)

        errors = self._read_jsonl(os.path.join(out, "errors.jsonl"))
        self.assertEqual(self._canon(errors), self._canon([
            {"service": "ledger", "record": "junk"},
            {"service": "ledger", "record": {"open_items": 1}},
        ]))
        posts = [r for r in ledger.requests if r["method"] == "POST"]
        self.assertEqual(posts, [])

    def test_creates_missing_out_dir(self):
        directory, ledger = self._standard_fakes()
        config = self._start(directory, ledger)
        out = os.path.join(self.tmp.name, "nested", "dir", "out")
        self.assertFalse(os.path.exists(out))

        self.assertEqual(run_sync(config, out), 0)

        for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl"):
            self.assertTrue(os.path.isfile(os.path.join(out, name)))

    def test_cli_sync_reports_success(self):
        directory, ledger = self._standard_fakes()
        config = self._start(directory, ledger)
        config_path = os.path.join(self.tmp.name, "config.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump({"directory_url": config.directory_url,
                       "ledger_url": config.ledger_url}, handle)
        out = os.path.join(self.tmp.name, "cli-out")

        self.assertEqual(orgsync_main(["sync", "--config", config_path,
                                       "--out-dir", out]), 0)

        summaries = self._read_jsonl(os.path.join(out, "summary.jsonl"))
        self.assertEqual(len(summaries), 2)


if __name__ == "__main__":
    unittest.main()
