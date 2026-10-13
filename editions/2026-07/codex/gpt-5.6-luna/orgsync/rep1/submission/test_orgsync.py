import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from orgsync.config import Config
from orgsync.sync import run_sync


class _Service:
    def __init__(self, pages, summaries=None):
        self.pages = pages
        self.summaries = list(summaries or [])
        self.requests = []
        self.rate_limited = False
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                service.requests.append(("GET", self.path, None))
                if self.path.startswith("/v1/items") and not service.rate_limited:
                    service.rate_limited = True
                    self.send_response(429)
                    self.send_header("Retry-After", "0")
                    self.end_headers()
                    return
                page = int(self.path.split("page=")[1]) if "page=" in self.path else 1
                if self.path.startswith("/v1/summaries"):
                    value = {"summaries": service.summaries, "next_page": None}
                else:
                    value = service.pages[page - 1]
                self._json(200, value)

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(length))
                service.requests.append(("POST", self.path, body))
                service.summaries.append(body)
                self._json(201, {"ok": True})

            def _json(self, status, value):
                data = json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server.server_port

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class SyncIntegrationTest(unittest.TestCase):
    def test_reconciles_and_second_run_has_no_posts(self):
        try:
            directory = _Service([
                {"items": [
                    {"id": "i1", "owner": "alice", "status": "open", "points": 3},
                    {"id": "i1", "owner": "alice", "status": "open", "points": 3},
                    {"id": "i2", "owner": "nobody", "status": "done", "points": 4},
                    {"id": "bad", "owner": "alice", "status": "open", "points": -1},
                ], "next_page": 2},
                {"items": [
                    {"id": "i3", "owner": "alice", "status": "done", "points": 2},
                    {"owner": "alice", "status": "open", "points": 1},
                ], "next_page": None},
            ])
            ledger = _Service([
                {"accounts": [
                    {"id": "a1", "owner": "alice", "active": True},
                    {"id": "a2", "owner": "bob", "active": True},
                    {"id": "a3", "owner": "old", "active": False},
                    {"id": "bad", "owner": "ghost", "active": "yes"},
                ], "next_page": None},
            ], summaries=[])
        except PermissionError:
            self.skipTest("local socket binding is unavailable")
        directory.start()
        ledger.start()
        try:
            with tempfile.TemporaryDirectory() as out:
                config = Config(directory.url, ledger.url)
                self.assertEqual(run_sync(config, out), 0)
                self.assertEqual(run_sync(config, out), 0)
                self.assertEqual(_lines(os.path.join(out, "orphans.jsonl")), [
                    {"id": "i2", "owner": "nobody", "status": "done", "points": 4}
                ])
                self.assertEqual(_lines(os.path.join(out, "errors.jsonl")), [
                    {"service": "directory", "record":
                     {"id": "bad", "owner": "alice", "status": "open", "points": -1}},
                    {"service": "directory", "record":
                     {"owner": "alice", "status": "open", "points": 1}},
                    {"service": "ledger", "record":
                     {"id": "bad", "owner": "ghost", "active": "yes"}},
                ])
                self.assertEqual(sorted(_lines(os.path.join(out, "summary.jsonl")),
                                        key=lambda x: x["owner"]), [
                    {"owner": "alice", "open_items": 1, "done_items": 1, "total_points": 5},
                    {"owner": "bob", "open_items": 0, "done_items": 0, "total_points": 0},
                ])
                self.assertEqual(sum(r[0] == "POST" for r in ledger.requests), 2)
        finally:
            directory.close()
            ledger.close()


def _lines(path):
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


if __name__ == "__main__":
    unittest.main()
