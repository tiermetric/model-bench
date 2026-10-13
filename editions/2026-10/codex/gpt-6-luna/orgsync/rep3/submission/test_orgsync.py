"""Integration tests for orgsync using only the standard library."""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from orgsync.config import Config
from orgsync.sync import run_sync


class ServiceState:
    def __init__(self):
        self.accounts = []
        self.items = []
        self.summaries = []
        self.requests = []
        self.throttle = set()


def make_handler(state, service):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def send_json(self, status, body, headers=None):
            data = json.dumps(body).encode()
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            state.requests.append((service, "GET", self.path, None))
            if (service, self.path) in state.throttle:
                state.throttle.remove((service, self.path))
                self.send_json(429, {}, {"Retry-After": "0"})
                return
            from urllib.parse import parse_qs, urlsplit
            parsed = urlsplit(self.path)
            page = int(parse_qs(parsed.query).get("page", ["1"])[0])
            if parsed.path == "/v1/accounts":
                key, records = "accounts", state.accounts
            elif parsed.path == "/v1/items":
                key, records = "items", state.items
            elif parsed.path == "/v1/summaries":
                key, records = "summaries", state.summaries
            else:
                self.send_json(404, {})
                return
            pages = [records[i:i + 2] for i in range(0, len(records), 2)] or [[]]
            next_page = page + 1 if page < len(pages) else None
            self.send_json(200, {key: pages[page - 1], "next_page": next_page})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append((service, "POST", self.path, body))
            if (service, self.path) in state.throttle:
                state.throttle.remove((service, self.path))
                self.send_json(429, {}, {"Retry-After": "0"})
                return
            if service != "ledger" or self.path != "/v1/summaries":
                self.send_json(404, {})
                return
            if any(entry["owner"] == body["owner"] for entry in state.summaries):
                self.send_json(409, {})
                return
            state.summaries.append(body)
            self.send_json(201, body)

    return Handler


class SyncIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.state = ServiceState()
        self.servers = []
        for service in ("directory", "ledger"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.state, service))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.servers.append((server, thread))
        self.addCleanup(self.stop_servers)
        self.config = Config("http://127.0.0.1:%d" % self.servers[0][0].server_port,
                             "http://127.0.0.1:%d" % self.servers[1][0].server_port)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out_dir = os.path.join(self.tmp.name, "out")

    def stop_servers(self):
        for server, thread in self.servers:
            server.shutdown()
            server.server_close()
            thread.join()

    def read_lines(self, name):
        path = os.path.join(self.out_dir, name)
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def test_counts_orphans_errors_pagination_and_idempotency(self):
        self.state.accounts = [
            {"id": "a", "owner": "kim", "active": True},
            {"id": "b", "owner": "lee", "active": True},
            {"id": "c", "owner": "off", "active": False},
            {"owner": "broken", "active": True},
        ]
        self.state.items = [
            {"id": "1", "owner": "kim", "status": "open", "points": 3, "extra": 1},
            {"id": "2", "owner": "kim", "status": "open", "points": 4},
            {"id": "3", "owner": "kim", "status": "done", "points": 2},
            {"id": "1", "owner": "kim", "status": "open", "points": 3},
            {"id": "4", "owner": "off", "status": "done", "points": 6},
            {"id": "5", "owner": "nobody", "status": "open", "points": 8},
            {"id": "bad", "owner": "kim", "status": "open", "points": True},
        ]
        self.state.throttle.update({("ledger", "/v1/accounts?page=1"),
                                    ("directory", "/v1/items?page=1")})
        self.assertEqual(run_sync(self.config, self.out_dir), 0)
        summaries = {item["owner"]: item for item in self.read_lines("summary.jsonl")}
        self.assertEqual(summaries["kim"], {"owner": "kim", "open_items": 2,
                                               "done_items": 1, "total_points": 9})
        self.assertEqual(summaries["lee"], {"owner": "lee", "open_items": 0,
                                               "done_items": 0, "total_points": 0})
        self.assertEqual({item["id"] for item in self.read_lines("orphans.jsonl")}, {"4", "5"})
        errors = self.read_lines("errors.jsonl")
        self.assertEqual(len(errors), 2)
        self.assertEqual({item["service"] for item in errors}, {"directory", "ledger"})
        before = {}
        for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl"):
            with open(os.path.join(self.out_dir, name), "rb") as handle:
                before[name] = handle.read()
        writes_before = sum(1 for request in self.state.requests if request[1] == "POST")
        self.assertEqual(run_sync(self.config, self.out_dir), 0)
        writes_after = sum(1 for request in self.state.requests if request[1] == "POST")
        self.assertEqual(writes_before, 2)
        self.assertEqual(writes_after, writes_before)
        after = {}
        for name in before:
            with open(os.path.join(self.out_dir, name), "rb") as handle:
                after[name] = handle.read()
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
