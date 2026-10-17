#!/usr/bin/env python3
"""Run-scoped capture from the same local stores as TIER's OpenCode/Muse collectors.

OpenCode: read-only SQLite (WAL-aware), exact fresh cwd + unique title -> session
id; archive message/part JSON blobs selected by that id. Muse: caller-selected
UUID + exact metadata workspace, plus explicitly linked nested child sessions.
No mtime-only attribution, CLI stdout usage, or client-reported cost is used.
Malformed billable evidence fails the entire benchmark capture rather than
publishing a partial cost. Stdlib only; fixtures never call a model.
"""
import argparse
import json
import sqlite3
from pathlib import Path

LIMIT = 1 << 29


def records(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def db_open(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def opencode_before(db, cwd):
    if not Path(db).exists():
        return
    with db_open(db) as con:
        if con.execute('SELECT id FROM session WHERE directory=?', (cwd,)).fetchone():
            raise ValueError('pre-existing OpenCode session for fresh cwd')


def capture_opencode(db, cwd, identity):
    with db_open(db) as con:
        sessions = con.execute('SELECT id FROM session WHERE directory=?', (cwd,)).fetchall()
        if len(sessions) != 1:
            raise ValueError(f'expected exactly one session for cwd, found {len(sessions)}')
        sid = sessions[0][0]
        title = con.execute('SELECT title FROM session WHERE id=?', (sid,)).fetchone()[0]
        if title != identity:
            raise ValueError('OpenCode session title differs from run identity')
        out = [{'type': 'opencode_session', 'session_id': sid, 'cwd': cwd,
                'title': identity, 'scope': 'exact-cwd+unique-title->session_id'}]
        for table in ('message', 'part'):
            for rid, raw in con.execute(f'SELECT id, data FROM {table} WHERE session_id=? ORDER BY id', (sid,)):
                out.append({'type': 'opencode_'+table, 'id': rid, 'session_id': sid,
                            'data': json.loads(raw)})
        # Child sessions would violate this panel's one-session discipline.
        if con.execute('SELECT id FROM session WHERE parent_id=?', (sid,)).fetchone():
            raise ValueError('OpenCode child session: capture needs a reviewed child-session policy')
        return out, sid


def muse_paths(root, identity):
    return list(Path(root).glob(f'*/*/*/{identity}/session.jsonl'))


def capture_muse(root, cwd, identity):
    paths = muse_paths(root, identity)
    if len(paths) != 1:
        raise ValueError(f'expected one Muse UUID log, found {len(paths)}')
    path = paths[0]
    out = records(path)
    sids = {o.get('stream', {}).get('id') for o in out if o.get('id')}
    if sids != {identity}:
        raise ValueError('Muse stream.id differs from requested UUID')
    roots = {o.get('payload', {}).get('record', {}).get('workspace_root')
             for o in out if o.get('payload', {}).get('kind') in ('metadata', 'workspace_branch')}
    roots.discard(None)
    if roots != {cwd}:
        raise ValueError('Muse workspace metadata differs from exact run cwd')
    # Muse reminders/subagents are billable too. Follow only the same explicit
    # links + directory nesting TIER uses; never scoop unrelated session files.
    def children(parent, lines):
        linked = {}
        for o in lines:
            p = o.get('payload', {}); e = p.get('event', {})
            if p.get('kind') == 'run' and e.get('kind') == 'memory_reminder_child_session_linked':
                child = e.get('child_session_id'); run = e.get('parent_run_id') or p.get('run_id')
                if not child or not run or Path(child).name != child:
                    raise ValueError('invalid Muse child link')
                if child in linked and linked[child] != run:
                    raise ValueError('ambiguous Muse child link')
                linked[child] = run
        found = {p.parent.name: p for p in parent.parent.glob('subagent/*/session.jsonl')}
        if set(found) != set(linked):
            raise ValueError('missing or unlinked Muse child log')
        for child, childpath in sorted(found.items()):
            rows = records(childpath)
            if {o.get('stream', {}).get('id') for o in rows if o.get('id')} != {child}:
                raise ValueError('Muse child stream identity mismatch')
            out.extend(rows)
            children(childpath, rows)
    children(path, out[:])
    return out, identity


def count(v):
    if type(v) is not int or not 0 <= v <= LIMIT:
        raise ValueError(f'invalid token count {v!r}')
    return v


def empty_tokens():
    return dict(api_calls=0, model_ids=[], input_uncached=0, cache_read=0,
                cache_write=0, cache_w5m=0, cache_w1h=0, output=0,
                reasoning=0, input_total=0)


def sum_native_jsonl(path, fmt):
    totals = empty_tokens(); models = set(); providers = set(); seen = set(); per_model = {}
    for o in records(path):
        if fmt == 'opencode':
            if o.get('type') != 'opencode_message':
                continue
            m = o['data']
            if m.get('role') != 'assistant':
                continue
            u = m.get('tokens', {}); cache = u.get('cache', {})
            inp, out, reason, cr, cw = [count(v) for v in
                (u.get('input', 0), u.get('output', 0), u.get('reasoning', 0),
                 cache.get('read', 0), cache.get('write', 0))]
            if not any((inp, out, reason, cr, cw, u.get('total', 0))):
                continue
            if not m.get('time', {}).get('completed'):
                raise ValueError('unfinished OpenCode assistant with partial usage')
            if 'total' not in u or count(u['total']) != inp + out + reason + cr + cw:
                raise ValueError('OpenCode additive identity broken or missing total')
            out += reason  # BESIDE output, same OutputTok mapping as TIER.
            provider = m.get('providerID')
            # TIER providers.go only admits this route. In particular Ollama
            # cloud has no published per-token rate; using Z.ai's model-only
            # price there would break capture parity with TIER.
            if provider != 'zai-coding-plan':
                raise ValueError(f'OpenCode provider {provider!r} excluded by TIER capture policy')
            providers.add(provider)
            model = m.get('modelID'); key = (o.get('session_id'), o.get('id'))
        else:
            p = o.get('payload', {}); e = p.get('event', {}); u = e.get('usage', {})
            agent = p.get('kind') == 'run' and e.get('kind') == 'model_completed'
            review = p.get('kind') == 'approval' and e.get('kind') == 'automated_review_completed'
            if not (agent or review):
                continue
            inp = count(u['input_tokens']); out = count(u['output_tokens'])
            reason = count(u.get('reasoning_tokens', 0))
            if agent:
                cr = count(u.get('cache_read_tokens', u.get('cached_tokens', 0)))
                cw = count(u.get('cache_write_tokens', 0))
                if 'cached_tokens' in u and 'cache_read_tokens' in u and count(u['cached_tokens']) != cr:
                    raise ValueError('Muse cache aliases disagree')
                model = e.get('model')
            else:
                cr = count(u.get('cached_input_tokens', 0)); cw = 0
                if 'non_cached_input_tokens' in u and cr + count(u['non_cached_input_tokens']) != inp:
                    raise ValueError('Muse review input identity broken')
                total = count(u.get('total_tokens', 0))
                if total and total != inp + out:
                    raise ValueError('Muse review total identity broken')
                model = e.get('model', {}).get('model_id')
            if cr + cw > inp or reason > out:
                raise ValueError('Muse containment broken')
            timestamp = o.get('recorded_at', 0)
            fallback = e.get('completed_at_ms', 0) if review else 0
            if not ((type(timestamp) is int and timestamp > 0) or
                    (type(fallback) is int and fallback > 0)):
                raise ValueError('Muse usage lacks positive timestamp')
            inp -= cr
            cw = 0  # write remains INSIDE uncached input, as TIER documents.
            key = ('meta', o.get('id'))  # TIER idempotency is provider + record UUID.
        if not model or not all(key) or key in seen:
            raise ValueError('missing model/identity or duplicate usage record')
        seen.add(key); models.add(model)
        values = dict(input_uncached=inp, cache_read=cr, cache_write=cw,
                      output=out, reasoning=reason, input_total=inp+cr+cw)
        totals['api_calls'] += 1
        model_totals = per_model.setdefault(model, empty_tokens())
        for k, v in values.items():
            totals[k] += v; model_totals[k] += v
    totals['model_ids'] = sorted(models)
    totals['by_model'] = per_model
    if fmt == 'opencode': totals['provider_ids'] = sorted(providers)
    return totals


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('phase', choices=['before', 'after'])
    ap.add_argument('--tool', required=True, choices=['opencode', 'muse'])
    ap.add_argument('--store', required=True); ap.add_argument('--cwd', required=True)
    ap.add_argument('--identity', required=True); ap.add_argument('--out')
    a = ap.parse_args()
    try:
        if a.phase == 'before':
            if a.tool == 'opencode': opencode_before(a.store, a.cwd)
            elif muse_paths(a.store, a.identity): raise ValueError('Muse UUID log already exists')
            return 0
        fn = capture_opencode if a.tool == 'opencode' else capture_muse
        rows, sid = fn(a.store, a.cwd, a.identity)
        # Archive first, even if invalid usage later makes this a capture failure.
        with open(a.out, 'w') as fh:
            for o in rows: fh.write(json.dumps(o, sort_keys=True)+'\n')
        sum_native_jsonl(a.out, a.tool)
        print(sid)
        return 0
    except (ValueError, KeyError, OSError, sqlite3.Error) as e:
        print(f'CAPTURE FAILURE: {e}', file=__import__('sys').stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
