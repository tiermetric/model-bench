"""Tests for `orgsync sync` against in-process fake Directory/Ledger servers."""
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from orgsync.config import Config
from orgsync.sync import run_sync

ACCOUNTS = [
    {"id": "a1", "owner": "kim", "active": True},
    {"id": "a2", "owner": "lee", "active": True},
    {"id": "a3", "owner": "old", "active": False},
    {"id": "a4", "owner": "bad", "active": "yes"},
]
ITEMS = [
    {"id": "i1", "owner": "kim", "status": "open", "points": 3},
    {"id": "i2", "owner": "kim", "status": "open", "points": 4},
    {"id": "i2", "owner": "kim", "status": "open", "points": 4},
    {"id": "i3", "owner": "kim", "status": "done", "points": 2},
    {"id": "i4", "owner": "old", "status": "open", "points": 1},
    {"id": "i5", "owner": "bad", "status": "open", "points": 1},
    {"id": "i6", "owner": "kim", "status": "open", "points": "5"},
    {"id": "i7", "owner": "kim", "status": "open", "points": -1},
    {"id": "i8", "owner": "kim", "status": "open", "points": True},
]


def make_server(state):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, headers=()):
            data = json.dumps(body).encode()
            self.send_response(code)
            for k, v in headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _limited(self):
            if state["limit"] > 0:
                state["limit"] -= 1
                self._send(429, {}, [("Retry-After", "1")])
                return True
            return False

        def do_GET(self):
            if self._limited():
                return
            path, _, q = self.path.partition("?")
            page = int(q.split("=")[1])
            data = state["data"][path]
            self._send(200, {state["key"][path]: data[page - 1],
                             "next_page": page + 1 if page < len(data) else None})

        def do_POST(self):
            if self._limited():
                return
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state["posts"].append((self.headers["Idempotency-Key"], body))
            self._send(201, body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def read(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.dstate = {"limit": 1, "posts": [], "key": {"/v1/items": "items"},
                       "data": {"/v1/items": [ITEMS[:4], ITEMS[4:]]}}
        self.lstate = {"limit": 1, "posts": [],
                       "key": {"/v1/accounts": "accounts", "/v1/summaries": "summaries"},
                       "data": {"/v1/accounts": [ACCOUNTS[:2], ACCOUNTS[2:]],
                                "/v1/summaries": [[]]}}
        self.d, self.l = make_server(self.dstate), make_server(self.lstate)
        self.addCleanup(self.d.shutdown)
        self.addCleanup(self.l.shutdown)
        self.cfg = Config("http://127.0.0.1:%d" % self.d.server_port,
                          "http://127.0.0.1:%d" % self.l.server_port)
        self.out = os.path.join(tempfile.mkdtemp(), "out")

    def test_sync(self):
        self.assertEqual(run_sync(self.cfg, self.out), 0)
        posts = self.lstate["posts"]
        expected = [{"owner": "kim", "open_items": 2, "done_items": 1, "total_points": 9},
                    {"owner": "lee", "open_items": 0, "done_items": 0, "total_points": 0}]
        self.assertEqual(sorted(p[1]["owner"] for p in posts), ["kim", "lee"])
        self.assertEqual([p[0] for p in sorted(posts, key=lambda p: p[0])],
                         ["sync-kim", "sync-lee"])
        self.assertEqual(sorted(read(self.out + "/summary.jsonl"), key=lambda r: r["owner"]),
                         expected)
        self.assertEqual(sorted(r["id"] for r in read(self.out + "/orphans.jsonl")),
                         ["i4", "i5"])
        errs = read(self.out + "/errors.jsonl")
        self.assertEqual(len(errs), 4)
        self.assertEqual(sum(e["service"] == "ledger" for e in errs), 1)

        before = {n: read(self.out + "/" + n) for n in os.listdir(self.out)}
        # Ledger now has the summaries; a rerun must not write.
        self.lstate["data"]["/v1/summaries"] = [expected]
        self.lstate["posts"].clear()
        self.assertEqual(run_sync(self.cfg, self.out), 0)
        self.assertEqual(self.lstate["posts"], [])
        after = {n: read(self.out + "/" + n) for n in os.listdir(self.out)}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
