"""Offline tests for orgsync's reconciliation rules."""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from orgsync.config import Config
from orgsync.sync import run_sync


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.summaries = []
        self.rate_limited = set()
        self.accounts = [
            {"id": "1", "owner": "kim", "active": True},
            {"id": "2", "owner": "lee", "active": True},
            {"id": "3", "owner": "off", "active": False},
            {"id": "bad", "owner": "ghost", "active": "true"},
        ]
        self.items = [
            {"id": "a", "owner": "kim", "status": "open", "points": 3},
            {"id": "b", "owner": "kim", "status": "open", "points": 4},
            {"id": "c", "owner": "kim", "status": "done", "points": 2},
            {"id": "a", "owner": "kim", "status": "open", "points": 3},
            {"id": "d", "owner": "off", "status": "done", "points": 5},
            {"id": "e", "owner": "ghost", "status": "open", "points": 1},
            {"id": "bad", "owner": "kim", "status": "open", "points": True},
        ]
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                page = int(parse_qs(parsed.query).get("page", ["0"])[0])
                outer.requests.append(("GET", parsed.path, page))
                if parsed.path not in outer.rate_limited:
                    outer.rate_limited.add(parsed.path)
                    self.send_response(429)
                    self.send_header("Retry-After", "0")
                    self.end_headers()
                    return
                if parsed.path.endswith("/accounts"):
                    rows = outer.accounts if page == 1 else []
                    key = "accounts"
                elif parsed.path.endswith("/items"):
                    rows = outer.items if page == 1 else []
                    key = "items"
                else:
                    rows = outer.summaries
                    key = "summaries"
                body = json.dumps({key: rows, "next_page": 2 if page == 1 else None}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                outer.requests.append(("POST", self.path, self.headers.get("Idempotency-Key")))
                if any(s["owner"] == body["owner"] for s in outer.summaries):
                    self.send_response(409)
                else:
                    outer.summaries.append(body)
                    self.send_response(201)
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_port
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def test_pagination_validation_orphans_and_idempotence(self):
        config = Config(self.url, self.url)
        out = os.path.join(self.temp.name, "out")
        self.assertEqual(run_sync(config, out), 0)
        self.assertCountEqual(self.summaries, [
            {"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9},
            {"owner": "lee", "open_items": 0, "done_items": 0, "total_points": 0},
        ])
        def read(name):
            with open(os.path.join(out, name), encoding="utf-8") as handle:
                return handle.read()
        before = {name: read(name) for name in ("orphans.jsonl", "errors.jsonl", "summary.jsonl")}
        self.assertEqual(len(before["orphans.jsonl"].splitlines()), 2)
        errors = [json.loads(line) for line in before["errors.jsonl"].splitlines()]
        self.assertEqual(len(errors), 2)
        snapshot = list(self.requests)
        self.assertEqual(run_sync(config, out), 0)
        self.assertEqual(before, {name: read(name) for name in before})
        self.assertFalse(any(req[0] == "POST" for req in self.requests[len(snapshot):]))
        self.assertEqual(sum(r[0] == "GET" and r[2] == 2 for r in self.requests), 6)


if __name__ == "__main__":
    unittest.main()
