"""Offline reconciliation tests using scripted service responses."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orgsync.config import Config
from orgsync.http_client import Response
from orgsync.sync import run_sync


def item(id='i', owner='kim', status='open', points=3, **extra):
    return dict(id=id, owner=owner, status=status, points=points, **extra)


def account(owner='kim', active=True, **extra):
    return dict(id='a-' + owner, owner=owner, active=active, **extra)


def summary(owner='kim', open_items=0, done_items=0, total_points=0):
    return dict(owner=owner, open_items=open_items, done_items=done_items,
                total_points=total_points)


class Service:
    def __init__(self, resources):
        self.resources = resources
        self.calls = []
        self.posts = []
        self.limits = {}
        self.keys = {}

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        resource = path.rsplit('/', 1)[-1]
        key = (method, resource, kwargs.get('params', {}).get('page'))
        if self.limits.get(key, 0):
            self.limits[key] -= 1
            return Response(429, {'Retry-After': '0.25'}, b'not data', path)
        if method == 'GET':
            page = kwargs['params']['page']
            records, next_page = self.resources[resource][page]
            return Response(200, {}, json.dumps({resource: records,
                            'next_page': next_page}).encode(), path)
        body = kwargs['payload']
        self.posts.append((body.copy(), kwargs['headers']))
        active = {a['owner'] for records, _ in self.resources['accounts'].values()
                  for a in records if isinstance(a, dict) and
                  type(a.get('active')) is bool and a['active'] and
                  type(a.get('id')) is str and type(a.get('owner')) is str}
        if body['owner'] not in active:
            raise AssertionError('invalid owner posted')
        idempotency = kwargs['headers']['Idempotency-Key']
        if idempotency in self.keys:
            if self.keys[idempotency] != body:
                raise AssertionError('conflicting key')
            return Response(200, {}, b'{}', path)
        self.keys[idempotency] = body.copy()
        self.resources['summaries'][1][0].append(body.copy())
        return Response(201, {}, b'{}', path)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / 'out'
        self.directory = Service({'items': {1: ([], None)}})
        self.ledger = Service({'accounts': {1: ([], None)},
                               'summaries': {1: ([], None)}})
        self.config = Config('directory', 'ledger')
        self.clock = 0.0

    def run_sync(self):
        def sleep(seconds):
            self.clock += seconds
        with patch('orgsync.sync.HttpClient', side_effect=lambda url:
                   self.directory if url == 'directory' else self.ledger), \
             patch('orgsync.sync.time.monotonic', side_effect=lambda: self.clock), \
             patch('orgsync.sync.time.sleep', side_effect=sleep):
            self.assertEqual(run_sync(self.config, str(self.out)), 0)

    def rows(self, name):
        return [json.loads(line) for line in (self.out / name).read_text().splitlines()]

    def test_pagination_ownership_deduplication_and_repeat_run(self):
        orphan = item('orphan', 'inactive', extra='preserved')
        bad_account = account('invalid', active=1)
        bad_item = item('bad', points=True)
        self.ledger.resources['accounts'] = {
            1: ([account(), account('inactive', False), bad_account], 7),
            7: ([account('empty'), account('already')], None)}
        self.ledger.resources['summaries'] = {
            1: ([], 4), 4: ([summary('already', total_points=99)], None)}
        self.directory.resources['items'] = {
            1: ([item(extra='ignored'), orphan, bad_item], 3),
            3: ([item(extra='ignored'), item('j', points=4),
                 item('k', status='done', points=2), orphan, bad_item,
                 item('invalid-owner', 'invalid')], None)}
        self.run_sync()
        self.assertCountEqual(self.rows('summary.jsonl'),
                              [summary('kim', 2, 1, 9), summary('empty')])
        self.assertCountEqual(self.rows('orphans.jsonl'),
                              [orphan, item('invalid-owner', 'invalid')])
        self.assertCountEqual(self.rows('errors.jsonl'), [
            {'service': 'ledger', 'record': bad_account},
            {'service': 'directory', 'record': bad_item}])
        self.assertEqual([h['Idempotency-Key'] for _, h in self.ledger.posts],
                         ['sync-kim', 'sync-empty'])
        before = {p.name: p.read_bytes() for p in self.out.iterdir()}
        self.run_sync()
        self.assertEqual(len(self.ledger.posts), 2)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.out.iterdir()})
        self.assertIn(('GET', '/v1/items', {'params': {'page': 3}}),
                      self.directory.calls)
        self.assertIn(('GET', '/v1/accounts', {'params': {'page': 7}}),
                      self.ledger.calls)

    def test_malformed_records_are_skipped_and_preserved_once(self):
        malformed_items = [None, [], 'bad', {}, item(id=1), item(owner=False),
                           item(status='pending'), item(points=-1), item(points=1.5),
                           item(points='3'), item(points=False)]
        malformed_accounts = [None, [], 'bad', {}, account(active='true'),
                              account(active=0), {'id': 2, 'owner': 'x', 'active': True},
                              {'id': 'x', 'owner': 3, 'active': True}]
        self.directory.resources['items'][1] = (malformed_items * 2, None)
        self.ledger.resources['accounts'][1] = (malformed_accounts * 2, None)
        self.run_sync()
        self.assertCountEqual(self.rows('errors.jsonl'),
            [{'service': 'directory', 'record': r} for r in malformed_items] +
            [{'service': 'ledger', 'record': r} for r in malformed_accounts])
        self.assertEqual(self.rows('summary.jsonl'), [])
        self.assertEqual(self.rows('orphans.jsonl'), [])
        self.assertEqual(self.ledger.posts, [])

    def test_repeated_rate_limits_on_every_endpoint_and_post(self):
        self.ledger.resources['accounts'][1] = ([account()], None)
        self.directory.limits[('GET', 'items', 1)] = 2
        for resource in ('accounts', 'summaries'):
            self.ledger.limits[('GET', resource, 1)] = 2
        self.ledger.limits[('POST', 'summaries', None)] = 2
        self.run_sync()
        self.assertGreaterEqual(self.clock, 2.0)
        self.assertEqual(self.rows('summary.jsonl'), [summary()])
        self.assertEqual(len(self.ledger.posts), 1)
        # Each rate-limited request must be retried unchanged before moving on.
        for service in (self.directory, self.ledger):
            for i in range(0, len(service.calls), 3):
                self.assertEqual(service.calls[i:i+3], [service.calls[i]] * 3)

    def test_accepted_replay_is_recorded(self):
        self.ledger.resources['accounts'][1] = ([account()], None)
        self.ledger.keys['sync-kim'] = summary()
        self.run_sync()
        self.assertEqual(self.rows('summary.jsonl'), [summary()])


if __name__ == '__main__':
    unittest.main()
