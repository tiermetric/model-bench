"""Offline contract tests using the real HTTP client with an in-memory transport."""

import copy
from email.message import Message
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
from urllib.parse import parse_qs, urlsplit

from orgsync.__main__ import main
from orgsync.config import Config
from orgsync.sync import run_sync


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Reply(io.BytesIO):
    def __init__(self, data):
        super().__init__(json.dumps(data).encode())
        self.status = 200
        self.headers = Message()


class Services:
    def __init__(self, accounts=None, items=None, summaries=None, throttle=False):
        self.data = {'accounts': accounts or [], 'items': items or [],
                     'summaries': summaries or []}
        self.clock = Clock()
        self.calls = []
        self.posts = []
        self.rejections = []
        self.attempts = {}
        self.blocked_until = {}
        self.throttle = throttle
        self.keys = {}

    def open(self, request, timeout=None):
        url = urlsplit(request.full_url)
        method = request.get_method()
        body = json.loads(request.data) if request.data else None
        key = request.get_header('Idempotency-key')
        if self.clock.now < self.blocked_until.get(url.netloc, 0):
            raise AssertionError('Service contacted before Retry-After elapsed')
        self.calls.append((method, request.full_url, body, key, self.clock.now))
        identity = (method, request.full_url, key)
        attempt = self.attempts.get(identity, 0)
        self.attempts[identity] = attempt + 1
        if self.throttle and attempt < 2:
            self.blocked_until[url.netloc] = self.clock.now + 0.25
            headers = Message()
            headers['Retry-After'] = '0.25'
            raise urllib.error.HTTPError(request.full_url, 429, 'limited',
                                         headers, io.BytesIO(b'not data'))
        collection = url.path.rsplit('/', 1)[-1]
        expected_host = 'directory.test' if collection == 'items' else 'ledger.test'
        if url.netloc != expected_host:
            raise AssertionError('Collection requested from wrong service')
        if method == 'GET':
            # Nonconsecutive page numbers catch implementations that increment.
            page = int(parse_qs(url.query)['page'][0])
            offset = (page - 1) // 3
            records = self.data[collection]
            return Reply({collection: records[offset:offset + 1],
                          'next_page': page + 3 if offset + 1 < len(records) else None})
        active = {a['owner'] for a in self.data['accounts']
                  if isinstance(a, dict) and type(a.get('id')) is str
                  and type(a.get('owner')) is str and a.get('active') is True}
        status = 201
        if body['owner'] not in active:
            status = 422
        elif key != 'sync-' + body['owner']:
            status = 400
        elif key in self.keys:
            status = 200 if self.keys[key] == body else 409
        elif any(s['owner'] == body['owner'] for s in self.data['summaries']):
            status = 409
        if status in (400, 409, 422):
            self.rejections.append(body)
        else:
            if status == 201:
                self.data['summaries'].append(body)
                self.keys[key] = body
            self.posts.append(body)
        reply = Reply({})
        reply.status = status
        return reply


def account(owner, active=True):
    return {'id': 'account-' + owner, 'owner': owner, 'active': active}


def item(identifier, owner='kim', status='open', points=3):
    return {'id': identifier, 'owner': owner, 'status': status, 'points': points}


def summary(owner, opened=0, done=0, points=0):
    return {'owner': owner, 'open_items': opened, 'done_items': done,
            'total_points': points}


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / 'out'
        self.config = Config('http://directory.test', 'http://ledger.test')

    def run_sync(self, services, cli=False):
        with patch('orgsync.http_client.urllib.request.build_opener', return_value=services), \
             patch('orgsync.sync.time.monotonic', services.clock.monotonic), \
             patch('orgsync.sync.time.sleep', services.clock.sleep):
            if cli:
                config = Path(self.tmp.name) / 'config.json'
                config.write_text(json.dumps(self.config.__dict__))
                code = main(['sync', '--config', str(config), '--out-dir', str(self.out)])
            else:
                code = run_sync(self.config, str(self.out))
        self.assertEqual(code, 0)
        self.assertEqual(services.rejections, [])

    def records(self, name):
        return [json.loads(line) for line in (self.out / (name + '.jsonl')).read_text().splitlines()]

    def test_reconciliation_pagination_duplicates_and_second_run(self):
        orphan = dict(item('orphan', 'missing'), extra={'keep': [1, True]})
        bad = dict(account('invalid'), active=1)
        bad_item = dict(item('bad'), points=True)
        services = Services(
            accounts=[account('kim'), account('inactive', False), bad,
                      account('zero'), account('already'), bad],
            items=[item('one'), item('two', points=4), item('three', status='done', points=2),
                   item('one'), orphan, orphan, item('inactive', 'inactive'),
                   item('invalid', 'invalid'), bad_item, bad_item],
            summaries=[summary('unrelated'), summary('already', 99, 99, 99)])
        self.run_sync(services, cli=True)
        self.assertCountEqual(services.posts, [summary('kim', 2, 1, 9), summary('zero')])
        self.assertCountEqual(self.records('summary'), services.posts)
        self.assertCountEqual(self.records('orphans'),
                              [orphan, item('inactive', 'inactive'), item('invalid', 'invalid')])
        self.assertCountEqual(self.records('errors'),
                              [{'service': 'ledger', 'record': bad},
                               {'service': 'directory', 'record': bad_item}])
        for collection, count in [('accounts', 6), ('items', 10), ('summaries', 2)]:
            pages = [int(parse_qs(urlsplit(c[1]).query)['page'][0]) for c in services.calls
                     if c[0] == 'GET' and urlsplit(c[1]).path == '/v1/' + collection]
            self.assertEqual(pages, [1 + 3 * n for n in range(count)])
        first_post = next(i for i, call in enumerate(services.calls) if call[0] == 'POST')
        self.assertTrue(all(c[0] == 'GET' for c in services.calls[:first_post]))
        self.assertEqual(first_post, 18)
        before = {p.name: p.read_bytes() for p in self.out.iterdir()}
        services.calls.clear()
        self.run_sync(services)
        self.assertTrue(all(call[0] == 'GET' for call in services.calls))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.out.iterdir()})

    def test_exact_json_types_missing_fields_and_extra_fields(self):
        malformed_items = [None, [], 'text', 42, True]
        malformed_accounts = [None, [], 'text', 42, False]
        for base, destination, invalid in [
            (item('bad'), malformed_items,
             {'id': 1, 'owner': [], 'status': ['open'], 'points': True}),
            (account('bad'), malformed_accounts,
             {'id': 1, 'owner': {}, 'active': 1})]:
            for field in base:
                missing = copy.deepcopy(base)
                del missing[field]
                destination.append(missing)
                wrong = dict(base, **{field: invalid[field]})
                destination.append(wrong)
        for points in [-1, 1.0, '3', None, False]:
            malformed_items.append(dict(item('bad'), points=points))
        malformed_items.append(item('bad', status='unknown'))
        malformed_accounts.extend([dict(account('bad'), active='true'),
                                   dict(account('bad'), active=None)])
        good_account = dict(account('kim'), ignored=['anything'])
        good_item = dict(item('good', points=0), ignored={'arbitrary': True})
        services = Services(accounts=malformed_accounts * 2 + [good_account],
                            items=malformed_items * 2 + [good_item])
        self.run_sync(services)
        self.assertEqual(self.records('summary'), [summary('kim', 1)])
        expected = [{'service': source, 'record': record}
                    for source, records in [('directory', malformed_items),
                                            ('ledger', malformed_accounts)]
                    for record in records]
        self.assertCountEqual(self.records('errors'), expected)
        self.assertEqual(self.records('orphans'), [])

    def test_repeated_rate_limits_on_all_gets_and_posts(self):
        services = Services(accounts=[account('kim'), account('zero')],
                            items=[item('one'), item('two', status='done')], throttle=True)
        self.run_sync(services)
        self.assertCountEqual(self.records('summary'),
                              [summary('kim', 1, 1, 6), summary('zero')])
        self.assertEqual(len(services.clock.sleeps), 14)
        self.assertTrue(all(delay >= 0.25 for delay in services.clock.sleeps))
        for offset in range(0, len(services.calls), 3):
            attempts = services.calls[offset:offset + 3]
            self.assertEqual(attempts[0][:4], attempts[1][:4])
            self.assertEqual(attempts[1][:4], attempts[2][:4])
            self.assertGreaterEqual(attempts[1][4] - attempts[0][4], 0.25)
            self.assertGreaterEqual(attempts[2][4] - attempts[1][4], 0.25)

    def test_empty_services_create_empty_files(self):
        services = Services()
        self.run_sync(services)
        self.assertEqual({p.name: p.read_bytes() for p in self.out.iterdir()},
                         {name + '.jsonl': b'' for name in ('summary', 'errors', 'orphans')})
        self.assertEqual(services.posts, [])

    def test_preexisting_summary_is_not_logged_or_reposted(self):
        services = Services(accounts=[account('kim')], items=[item('one')],
                            summaries=[summary('kim', 10, 20, 30)])
        self.run_sync(services)
        self.assertEqual(self.records('summary'), [])
        self.assertEqual(services.posts, [])


if __name__ == '__main__':
    unittest.main()
