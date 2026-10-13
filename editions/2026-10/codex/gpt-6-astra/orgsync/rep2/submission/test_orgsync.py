"""Offline integration tests using the real HTTP client and an in-memory wire."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from orgsync.__main__ import main
from orgsync.config import Config
from orgsync.sync import run_sync


class WireResponse:
    def __init__(self, status, body):
        self.status = status
        self.headers = {}
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class Services:
    """Track pagination, rate-limit deadlines, accepted bodies, and rejections."""
    def __init__(self, accounts=(), items=(), summaries=(), rate_limit=False):
        self.collections = dict(accounts=list(accounts), items=list(items),
                                summaries=list(summaries))
        self.now = 0.0
        self.blocked = {}
        self.attempts = {}
        self.requests = []
        self.posts = []
        self.rate_limit = rate_limit
        self.rejections = []
        self.post_status = 201

    def sleep(self, delay):
        self.now += delay

    def open(self, request, timeout=None):
        url = urlsplit(request.full_url)
        method = request.get_method()
        collection = url.path.rsplit('/', 1)[1]
        page = int(parse_qs(url.query).get('page', ['1'])[0])
        body = json.loads(request.data) if request.data else None
        key = (url.netloc, method, collection, page,
               body['owner'] if body else None)
        assert self.now >= self.blocked.get(url.netloc, 0), 'early request'
        self.requests.append((key, self.now, body))
        self.attempts[key] = self.attempts.get(key, 0) + 1
        if self.rate_limit and self.attempts[key] <= 2:
            self.blocked[url.netloc] = self.now + 0.25
            raise HTTPError(request.full_url, 429, 'slow down',
                            {'Retry-After': '0.25'}, io.BytesIO(b'not data'))
        if method == 'GET':
            assert url.netloc == ('directory' if collection == 'items' else 'ledger')
            # Nonconsecutive page tokens catch implementations that increment.
            offset = (page - 1) // 3
            records = self.collections[collection]
            return WireResponse(200, {collection: records[offset:offset + 1],
                                      'next_page': page + 3 if offset + 1 < len(records) else None})
        assert collection == 'summaries' and url.netloc == 'ledger'
        assert request.get_header('Idempotency-key') == 'sync-' + body['owner']
        assert request.get_header('Content-type') == 'application/json'
        active = {r['owner'] for r in self.collections['accounts']
                  if isinstance(r, dict) and type(r.get('id')) is str
                  and type(r.get('owner')) is str and r.get('active') is True}
        status = self.post_status
        if body['owner'] not in active:
            status = 422
        elif any(s['owner'] == body['owner'] for s in self.collections['summaries']):
            status = 409
        if status in (400, 409, 422):
            self.rejections.append(body)
        else:
            self.posts.append(body)
            self.collections['summaries'].append(body)
        return WireResponse(status, {})


def account(owner, active=True):
    return dict(id='a-' + owner, owner=owner, active=active)


def item(id, owner='kim', status='open', points=3, **extra):
    return dict(id=id, owner=owner, status=status, points=points, **extra)


def summary(owner, open_items=0, done_items=0, total_points=0):
    return dict(owner=owner, open_items=open_items, done_items=done_items,
                total_points=total_points)


class SyncTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.out = self.root / 'out'
        self.config = Config('http://directory', 'http://ledger')

    def run_service(self, service, cli=False):
        with patch('orgsync.http_client.urllib.request.build_opener', return_value=service), \
             patch('orgsync.sync.time.monotonic', side_effect=lambda: service.now), \
             patch('orgsync.sync.time.sleep', side_effect=service.sleep):
            if cli:
                config_path = self.root / 'test-config.json'
                config_path.write_text(json.dumps(self.config.__dict__))
                result = main(['sync', '--config', str(config_path), '--out-dir', str(self.out)])
            else:
                result = run_sync(self.config, str(self.out))
        self.assertEqual(result, 0)
        self.assertEqual(service.rejections, [])

    def read(self, name):
        return [json.loads(line) for line in (self.out / (name + '.jsonl')).read_text().splitlines()]

    def test_full_sync_and_second_run(self):
        bad_account = dict(id='bad', owner='bad', active=1)
        bad_item = item('bad-item', points=True)
        orphan = item('orphan', owner='absent', note={'keep': [1, 2]})
        inactive = item('inactive', owner='off')
        malformed_owner = item('bad-owner', owner='bad')
        first = item('one', note='extra fields are fine')
        service = Services(
            [account('kim'), account('empty'), account('off', False),
             bad_account, account('already'), bad_account],
            [first, item('two', points=4), item('three', status='done', points=2),
             first, orphan, orphan, inactive, malformed_owner, bad_item, bad_item,
             item('preexisting', owner='already')],
            [summary('unrelated'), summary('already', 99, 99, 99)], rate_limit=True)
        self.run_service(service, cli=True)
        self.assertCountEqual(service.posts, [summary('kim', 2, 1, 9), summary('empty')])
        self.assertCountEqual(self.read('summary'), service.posts)
        self.assertCountEqual(self.read('orphans'), [orphan, inactive, malformed_owner])
        self.assertCountEqual(self.read('errors'), [
            {'service': 'ledger', 'record': bad_account},
            {'service': 'directory', 'record': bad_item}])
        before = {p.name: p.read_bytes() for p in self.out.iterdir()}
        request_count = len(service.requests)
        self.run_service(service, cli=True)
        self.assertTrue(all(r[0][1] == 'GET' for r in service.requests[request_count:]))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.out.iterdir()})
        self.assertTrue(service.now > 0)

    def test_all_malformed_shapes_are_skipped_and_reported_once(self):
        bad_items = [None, [], 'bad', 4, False, {}]
        bad_accounts = [None, [], 'bad', 4, False, {}]
        for field, values in {'id': [None, 1], 'owner': [None, 2],
                              'status': [None, [], 'OPEN', 1],
                              'points': [None, True, False, -1, 1.0, '2']}.items():
            missing = item('missing-' + field)
            del missing[field]
            bad_items.append(missing)
            for value in values:
                record = item('bad-' + field + repr(value))
                record[field] = value
                bad_items.append(record)
        for field, values in {'id': [None, 1], 'owner': [None, 2],
                              'active': [None, 0, 1, 'true', [], {}]}.items():
            missing = account('missing-' + field)
            del missing[field]
            bad_accounts.append(missing)
            for value in values:
                record = account('bad-' + field + repr(value))
                record[field] = value
                bad_accounts.append(record)
        service = Services(bad_accounts * 2 + [dict(account('kim'), extra=0)],
                           bad_items * 2 + [item('zero', points=0)])
        self.run_service(service)
        self.assertEqual(service.posts, [summary('kim', 1, 0, 0)])
        expected = [{'service': s, 'record': r} for s, records in
                    [('ledger', bad_accounts), ('directory', bad_items)] for r in records]
        self.assertCountEqual(self.read('errors'), expected)
        self.assertEqual(self.read('orphans'), [])

    def test_empty_services_create_all_files(self):
        self.run_service(Services())
        for name in ('summary', 'errors', 'orphans'):
            self.assertEqual(self.read(name), [])

    def test_successful_replay_is_journaled(self):
        service = Services([account('kim')])
        service.post_status = 200
        self.run_service(service)
        self.assertEqual(self.read('summary'), [summary('kim')])

    def test_early_sleep_return_still_waits_until_deadline(self):
        service = Services([account('kim')], rate_limit=True)
        sleeps = []

        def interrupted_sleep(delay):
            sleeps.append(delay)
            service.now += delay / 2 if len(sleeps) % 2 else delay

        service.sleep = interrupted_sleep
        self.run_service(service)
        self.assertGreater(len(sleeps), 8)
        self.assertEqual(service.posts, [summary('kim')])


if __name__ == '__main__':
    unittest.main()
