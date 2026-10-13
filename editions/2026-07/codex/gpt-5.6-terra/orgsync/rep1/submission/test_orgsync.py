"""End-to-end tests for the sync protocol, using only local HTTP servers."""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from orgsync.config import Config
from orgsync.sync import run_sync


class LocalService:
    def __init__(self, data, ledger=False):
        self.data, self.ledger = data, ledger
        self.requests, self.posts, self.rate_limited = [], [], False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status, body, headers=None):
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self):
                outer.requests.append(("GET", self.path))
                # Exercise retry behavior once without making this test slow.
                if not outer.rate_limited:
                    outer.rate_limited = True
                    self._reply(429, {}, {"Retry-After": "0"})
                    return
                endpoint = self.path.split("?")[0].rsplit("/", 1)[-1]
                page = int(self.path.split("page=")[-1])
                pages = outer.data[endpoint]
                values = pages[page - 1]
                self._reply(200, {endpoint: values,
                                  "next_page": page + 1 if page < len(pages) else None})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(("POST", self.path))
                outer.posts.append(body)
                active = {a["owner"] for page in outer.data["accounts"] for a in page
                          if isinstance(a, dict) and a.get("active") is True
                          and isinstance(a.get("id"), str) and isinstance(a.get("owner"), str)}
                if body["owner"] not in active:
                    self._reply(422, {})
                else:
                    outer.data["summaries"][0].append(body)
                    self._reply(201, body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
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


class SyncTest(unittest.TestCase):
    def test_sync_paginates_validates_deduplicates_and_is_idempotent(self):
        try:
            directory = LocalService({"items": [[
            {"id": "1", "owner": "kim", "status": "open", "points": 3, "extra": 1},
            {"id": "bad", "owner": "kim", "status": "open", "points": True},
            {"id": "orphan", "owner": "nobody", "status": "done", "points": 7},
        ], [
            {"id": "1", "owner": "kim", "status": "open", "points": 3, "extra": 1},
            {"id": "2", "owner": "kim", "status": "done", "points": 2},
        ]]})
            ledger = LocalService({"accounts": [[
            {"id": "a", "owner": "kim", "active": True},
            {"id": "b", "owner": "idle", "active": True},
            {"id": "c", "owner": "old", "active": False},
            {"id": 4, "owner": "broken", "active": True},
        ]], "summaries": [[{"owner": "already", "open_items": 0,
                               "done_items": 0, "total_points": 0}]]}, ledger=True)
        except PermissionError:
            self.skipTest("the current sandbox does not permit loopback listeners")
        directory.start(); ledger.start()
        self.addCleanup(directory.close); self.addCleanup(ledger.close)
        with tempfile.TemporaryDirectory() as output:
            config = Config(directory.url, ledger.url)
            self.assertEqual(run_sync(config, output), 0)
            def lines(name):
                with open(os.path.join(output, name), encoding="utf-8") as handle:
                    return [json.loads(line) for line in handle]
            self.assertEqual({x["owner"] for x in lines("summary.jsonl")}, {"kim", "idle"})
            self.assertIn({"owner": "kim", "open_items": 1, "done_items": 1,
                           "total_points": 5}, lines("summary.jsonl"))
            self.assertEqual(lines("orphans.jsonl"), [{"id": "orphan", "owner": "nobody",
                                                       "status": "done", "points": 7}])
            errors = lines("errors.jsonl")
            self.assertIn({"service": "directory", "record": {"id": "bad", "owner": "kim",
                         "status": "open", "points": True}}, errors)
            self.assertTrue(any(x["service"] == "ledger" for x in errors))
            before = {name: open(os.path.join(output, name), "rb").read()
                      for name in ("summary.jsonl", "orphans.jsonl", "errors.jsonl")}
            post_count = len(ledger.posts)
            self.assertEqual(run_sync(config, output), 0)
            self.assertEqual(len(ledger.posts), post_count)
            self.assertEqual(before, {name: open(os.path.join(output, name), "rb").read()
                                      for name in before})


if __name__ == "__main__":
    unittest.main()
