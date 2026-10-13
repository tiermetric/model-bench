"""Integration tests for orgsync using only in-process standard-library servers."""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from orgsync.config import Config
from orgsync.sync import run_sync


class Service:
    def __init__(self, accounts=(), items=(), summaries=(), rate_once=False):
        self.accounts = list(accounts)
        self.items = list(items)
        self.summaries = list(summaries)
        self.posts = []
        self.requests = []
        self.rate_once = rate_once
        self.rated = set()

        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                page = int(parse_qs(parsed.query).get("page", ["1"])[0])
                service.requests.append(("GET", parsed.path, page))
                if service.rate_once and parsed.path not in service.rated:
                    service.rated.add(parsed.path)
                    self.send_response(429)
                    self.send_header("Retry-After", "0")
                    self.end_headers()
                    return
                key = {"/v1/accounts": "accounts", "/v1/items": "items",
                       "/v1/summaries": "summaries"}[parsed.path]
                records = getattr(service, key)
                size = 2
                batch = records[(page - 1) * size:page * size]
                next_page = page + 1 if page * size < len(records) else None
                body = json.dumps({key: batch, "next_page": next_page}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                service.requests.append(("POST", self.path, data))
                service.posts.append(data)
                service.summaries.append(data)
                self.send_response(201)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return "http://127.0.0.1:%d" % self.server.server_port

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.services = []
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        for service in self.services:
            service.close()
        self.tmp.cleanup()

    def service(self, **kwargs):
        service = Service(**kwargs)
        self.services.append(service)
        return service, service.start()

    def test_counts_orphans_errors_pagination_and_repeat(self):
        ledger, ledger_url = self.service(accounts=[
            {"id": "a", "owner": "kim", "active": True, "extra": 1},
            {"id": "b", "owner": "lee", "active": False},
            {"id": 3, "owner": "bad", "active": True},
        ])
        directory, directory_url = self.service(items=[
            {"id": "1", "owner": "kim", "status": "open", "points": 3},
            {"id": "1", "owner": "kim", "status": "open", "points": 3},
            {"id": "2", "owner": "kim", "status": "open", "points": 4},
            {"id": "3", "owner": "kim", "status": "done", "points": 2},
            {"id": "4", "owner": "lee", "status": "done", "points": 8},
            {"id": "5", "owner": "nobody", "status": "open", "points": 1},
            {"id": "6", "owner": "kim", "status": "bad", "points": 1},
        ])
        out = os.path.join(self.tmp.name, "out")
        config = Config(directory_url, ledger_url)
        self.assertEqual(run_sync(config, out), 0)
        def read(name):
            with open(os.path.join(out, name), encoding="utf-8") as handle:
                return handle.read()
        self.assertEqual(json.loads(read("summary.jsonl")), {
            "owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9})
        self.assertEqual([json.loads(x) for x in read("orphans.jsonl").splitlines()], [
            {"id": "4", "owner": "lee", "status": "done", "points": 8},
            {"id": "5", "owner": "nobody", "status": "open", "points": 1}])
        errors = [json.loads(x) for x in read("errors.jsonl").splitlines()]
        self.assertEqual(len(errors), 2)
        before = {name: read(name) for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")}
        post_count = sum(r[0] == "POST" for r in ledger.requests)
        run_sync(config, out)
        self.assertEqual(sum(r[0] == "POST" for r in ledger.requests), post_count)
        self.assertEqual(before, {name: read(name) for name in before})
        self.assertTrue(any(r[2] == 2 for r in directory.requests if r[0] == "GET"))

    def test_rate_limits_are_retried(self):
        ledger, ledger_url = self.service(accounts=[{"id": "a", "owner": "a", "active": True}],
                                          rate_once=True)
        directory, directory_url = self.service(rate_once=True)
        run_sync(Config(directory_url, ledger_url), os.path.join(self.tmp.name, "out"))
        self.assertGreaterEqual(sum(r[0] == "GET" for r in ledger.requests), 4)
        self.assertGreaterEqual(sum(r[0] == "GET" for r in directory.requests), 2)
        self.assertEqual(ledger.posts, [{"owner": "a", "open_items": 0,
                                        "done_items": 0, "total_points": 0}])


if __name__ == "__main__":
    unittest.main()
