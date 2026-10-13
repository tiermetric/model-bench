"""Reconcile Directory items with Ledger accounts and retain a posting journal."""

from __future__ import annotations

import json
from pathlib import Path
import time

from .config import Config
from .http_client import HttpClient

DEFAULT_OUT_DIR = "out"


def _request(client, method, path, **kwargs):
    """Retry rate limits before allowing any further request to this service."""
    while True:
        response = client.request(method, path, **kwargs)
        if response.status != 429:
            if not 200 <= response.status < 300:
                raise RuntimeError("%s %s returned HTTP %s" %
                                   (method, response.url, response.status))
            return response
        deadline = time.monotonic() + max(0.0, float(response.headers['Retry-After']))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(remaining)


def _pages(client, resource):
    page = 1
    while page is not None:
        data = _request(client, 'GET', '/v1/' + resource,
                        params={'page': page}).json()
        yield from data[resource]
        page = data['next_page']


def _valid(record, fields):
    return (type(record) is dict and
            all(type(record.get(key)) is kind for key, kind in fields.items()))


def _account(record):
    return _valid(record, {'id': str, 'owner': str, 'active': bool})


def _item(record):
    return (_valid(record, {'id': str, 'owner': str, 'status': str, 'points': int})
            and record['status'] in ('open', 'done') and record['points'] >= 0)


def _summary(record):
    return (_valid(record, {'owner': str, 'open_items': int, 'done_items': int,
                            'total_points': int}) and
            all(record[key] >= 0 for key in ('open_items', 'done_items', 'total_points')))


def _unique(records):
    seen = set()
    for record in records:
        key = json.dumps(record, sort_keys=True, ensure_ascii=True)
        if key not in seen:
            seen.add(key)
            yield record


def _write(path, records):
    content = ''.join(json.dumps(record, sort_keys=True, ensure_ascii=True) + '\n'
                      for record in _unique(records))
    if not path.exists() or path.read_text(encoding='utf-8') != content:
        temporary = path.with_suffix('.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)


def run_sync(config: Config, out_dir: str = DEFAULT_OUT_DIR) -> int:
    """Read all pages, post missing active-owner summaries, and write JSONL."""
    directory = HttpClient(config.directory_url)
    ledger = HttpClient(config.ledger_url)
    accounts = list(_pages(ledger, 'accounts'))
    items = list(_pages(directory, 'items'))
    existing = list(_pages(ledger, 'summaries'))
    errors = []
    totals = {}
    for record in accounts:
        if not _account(record):
            errors.append({'service': 'ledger', 'record': record})
        elif record['active']:
            totals[record['owner']] = {'owner': record['owner'], 'open_items': 0,
                                       'done_items': 0, 'total_points': 0}

    orphans, seen_items = [], set()
    for record in items:
        if not _item(record):
            errors.append({'service': 'directory', 'record': record})
            continue
        if record['id'] in seen_items:
            continue
        seen_items.add(record['id'])
        summary = totals.get(record['owner'])
        if summary is None:
            orphans.append(record)
        else:
            summary[record['status'] + '_items'] += 1
            summary['total_points'] += record['points']

    existing_owners = set()
    for record in existing:
        # An owner already represented in the ledger must never be posted again.
        if type(record) is dict and type(record.get('owner')) is str:
            existing_owners.add(record['owner'])
        if not _summary(record):
            errors.append({'service': 'ledger', 'record': record})

    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    journal = output / 'summary.jsonl'
    posted = ([json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()
               if line.strip()] if journal.exists() else [])
    _write(output / 'orphans.jsonl', orphans)
    _write(output / 'errors.jsonl', errors)
    _write(journal, posted)
    for owner, summary in totals.items():
        if owner in existing_owners:
            continue
        _request(ledger, 'POST', '/v1/summaries', payload=summary,
                 headers={'Idempotency-Key': 'sync-' + owner})
        posted.append(summary)
        _write(journal, posted)
    return 0
