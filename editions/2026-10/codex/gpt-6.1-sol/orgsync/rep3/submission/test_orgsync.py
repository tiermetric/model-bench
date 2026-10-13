"""Offline integration tests using two in-process loopback HTTP services."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from orgsync.config import Config
from orgsync.sync import run_sync


class Service:
    def __init__(self, collections, active=(), rate_limit=False):
        self.collections = collections
        self.active = set(active)
        self.requests = []
        self.accepted = []
        self.keys = {}
        self.limited = set()
        self.rate_limit = rate_limit
        self.deadline = 0
        self.early = []
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, status, body, retry=None):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                if retry is not None:
                    self.send_header('Retry-After', str(retry))
                self.end_headers()
                self.wfile.write(data)

            def handle_request(self):
                parsed = urlsplit(self.path)
                page = int(parse_qs(parsed.query).get('page', ['1'])[0])
                body = None
                if self.command == 'POST':
                    body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                service.requests.append((self.command, parsed.path, page, body,
                                         self.headers.get('Idempotency-Key')))
                now = time.monotonic()
                if now < service.deadline:
                    service.early.append(self.path)
                key = (self.command, parsed.path, page)
                if service.rate_limit and key not in service.limited:
                    service.limited.add(key)
                    service.deadline = now + 0.02
                    self.respond(429, {'ignored': True}, 0.02)
                    return
                collection = parsed.path.rsplit('/', 1)[-1]
                if self.command == 'GET':
                    pages = service.collections[collection]
                    records, next_page = pages[page]
                    if collection == 'summaries' and next_page is None:
                        records = records + service.accepted
                    self.respond(200, {collection: records, 'next_page': next_page})
                else:
                    token = self.headers.get('Idempotency-Key')
                    if body['owner'] not in service.active:
                        self.respond(422, {})
                    elif token in service.keys:
                        self.respond(200 if service.keys[token] == body else 409, {})
                    elif token != 'sync-' + body['owner']:
                        self.respond(400, {})
                    else:
                        service.keys[token] = body
                        service.accepted.append(body)
                        self.respond(201, {})

            do_GET = handle_request
            do_POST = handle_request

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d' % self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def account(owner, active=True):
    return {'id': 'a-' + owner, 'owner': owner, 'active': active}


def item(id, owner='kim', status='open', points=3, **extra):
    return dict(id=id, owner=owner, status=status, points=points, **extra)


def summary(owner, opened=0, done=0, points=0):
    return dict(owner=owner, open_items=opened, done_items=done, total_points=points)


class SyncTest(unittest.TestCase):
    def services(self, items, accounts, summaries, active=(), rate_limit=False):
        directory = Service({'items': items}, rate_limit=rate_limit)
        ledger = Service({'accounts': accounts, 'summaries': summaries}, active, rate_limit)
        self.addCleanup(directory.close)
        self.addCleanup(ledger.close)
        return directory, ledger

    def test_cli_pagination_validation_arithmetic_rate_limits_and_idempotence(self):
        malformed_items = [item('bool', points=True), item('negative', points=-1),
                           item('float', points=1.0), item('string', points='3'),
                           item('status', status='pending'), {'id': 'missing'},
                           item(23), item('owner', owner=None), None, ['bad']]
        malformed_accounts = [account('bad', 1), {'owner': 'missing', 'active': True},
                              account( 'numeric', 'true'), None]
        orphan = item('orphan', 'inactive', extra={'keep': 'me'})
        other_orphan = item('bad-owner', 'bad')
        directory, ledger = self.services(
            {1: ([item('one'), orphan] + malformed_items, 4),
             4: ([item('one'), item('two', points=4), item('three', status='done', points=2),
                  orphan, other_orphan] + malformed_items, None)},
            {1: ([account('kim'), account('inactive', False)] + malformed_accounts, 3),
             3: ([account('empty'), account('already')] + malformed_accounts, None)},
            {1: ([], 5), 5: ([summary('already', 99, 99, 99)], None)},
            active=('kim', 'empty', 'already'), rate_limit=True)
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.json'
            config.write_text(json.dumps(dict(directory_url=directory.url, ledger_url=ledger.url)))
            output = Path(tmp) / 'out'
            command = [sys.executable, '-m', 'orgsync', 'sync', '--config', str(config),
                       '--out-dir', str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            before = {p.name: p.read_bytes() for p in output.iterdir()}
            read = lambda name: [json.loads(line) for line in before[name].splitlines()]
            self.assertCountEqual(read('summary.jsonl'), [summary('kim', 2, 1, 9), summary('empty')])
            self.assertCountEqual(read('orphans.jsonl'), [orphan, other_orphan])
            self.assertCountEqual(read('errors.jsonl'),
                                  [{'service': 'directory', 'record': r} for r in malformed_items]
                                  + [{'service': 'ledger', 'record': r} for r in malformed_accounts])
            post_count = sum(r[0] == 'POST' for r in ledger.requests)
            self.assertEqual(post_count, 3)  # First POST is rate limited, then retried.
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})
            self.assertEqual(post_count, sum(r[0] == 'POST' for r in ledger.requests))
            self.assertFalse(directory.early + ledger.early)
            for service, collection, pages in ((directory, 'items', {1, 4}),
                                               (ledger, 'accounts', {1, 3}),
                                               (ledger, 'summaries', {1, 5})):
                self.assertEqual({r[2] for r in service.requests if r[1] == '/v1/' + collection}, pages)

    def test_empty_services_create_empty_files(self):
        directory, ledger = self.services({1: ([], None)}, {1: ([], None)}, {1: ([], None)})
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run_sync(Config(directory.url, ledger.url), tmp), 0)
            self.assertEqual({p.name: p.read_bytes() for p in Path(tmp).iterdir()},
                             {name + '.jsonl': b'' for name in ('summary', 'orphans', 'errors')})
            self.assertFalse(any(r[0] == 'POST' for r in ledger.requests))


if __name__ == '__main__':
    unittest.main()
