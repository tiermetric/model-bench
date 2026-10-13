#!/usr/bin/env python3
"""Generate an edition's CSV and numeric tables from archived evidence. Offline.

python3 edition_report.py editions/2026-10
No model invocation; result.json and rate snapshots are never edited.
"""
import argparse
import csv
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

from verify import (METRIC_FIELDS, FIGURE_NAMES, derive_run_metrics, edition_context,
                    load_prices, metric_strings, sha256_file)


SCOPE = "*One task — the integration anchor only; per-task results, not a model ranking.*"

# Preserve only this measured sentence; all other section content is generated.
CHECK_COUNTS_NOTE_PATTERN = (
    r'^At this head, `python3 verify\.py` reports \*\*\d+ checks: \d+ PASS · \d+ FAIL · \d+ SKIP\*\* '
    r'and `python3 verify\.py --rejudge` reports \*\*\d+ checks: \d+ PASS · \d+ FAIL · \d+ SKIP\*\*; '
    r'these counts include the checks this section adds, and the earlier 342-check figure is the count before this section existed\.$'
)


def model_groups(metrics, manifest):
    if len({r['task'] for r in metrics}) != 1:
        raise ValueError('model groups require exactly one task; never pool tasks')
    groups = defaultdict(list)
    for row in metrics:
        groups[(row['tool'], row['model'])].append(row)
    expected = [(tool, model) for tool, models in manifest['roster'].items() for model in models]
    if set(groups) != set(expected):
        raise ValueError('metrics must cover exactly the manifest roster')
    return [(model, sorted(groups[(tool, model)], key=lambda r: r['rep'])) for tool, model in expected]


def cost_stats(rows):
    costs = [r['cost_list_usd'] for r in rows]
    accepted = sum(r['outcome'] == 'PASS' for r in rows)
    total = sum(costs)
    return dict(accepted=accepted, total=total, expected=total/accepted if accepted else None,
                tier=5*accepted/(total/1000) if accepted else None,
                low=min(costs), high=max(costs),
                dispersion=100*statistics.stdev(costs)/statistics.mean(costs) if len(costs)>1 else None)


def median_range(rows, key):
    values = [r[key] for r in rows]
    if any(v is None for v in values):
        return 'not reported'
    return f'{statistics.median(values):,} ({min(values):,} – {max(values):,})'


def generated_tables(metrics, manifest):
    tasks = list(manifest.get('tasks') or dict.fromkeys(r['task'] for r in metrics))
    if set(tasks) != {r['task'] for r in metrics}:
        raise ValueError('metrics must cover exactly the manifest tasks')
    if len(tasks) > 1:
        tables = {}
        for task in tasks:
            scoped = generated_tables([r for r in metrics if r['task'] == task],
                                      {**manifest, 'tasks': {task: manifest.get('tasks', {}).get(task, {})}})
            for name in ('how-models-worked', 'per-run'):
                tables[f'{name}:{task}'] = scoped[name]
        return tables
    groups = model_groups(metrics, manifest)
    results = ['| model | outcome | rep1 $ | rep2 $ | rep3 $ | TIER ↑ | E[$/accepted] ↓ | run-to-run range | dispersion |',
               '|---|---|---:|---:|---:|---:|---:|---|---:|']
    work = ['| model | E[$/accepted] ↓ | wall seconds median (range) | model calls / responses median (range) | billed tokens median (range) |',
            '|---|---:|---:|---:|---:|']
    runs = ['| model | rep | outcome | wall seconds | model calls / responses | uncached input | cache read | cache write | output (incl. reasoning) | of which reasoning | all tokens billed | list cost $ |',
            '|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    money = ['| model | uncached input | cache read | cache write | output (incl. reasoning) | of which reasoning | all tokens billed |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for model, rows in groups:
        st = cost_stats(rows)
        if [r['rep'] for r in rows] != [1, 2, 3]:
            raise ValueError(f'{model}: report requires rep1..3')
        costs = ' | '.join(f'{r["cost_list_usd"]:.6f}' for r in rows)
        tier = f'{st["tier"]:,.0f}' if st['accepted'] else 'NO SCORE'
        expected = f'${st["expected"]:.4f}' if st['accepted'] else 'NO SCORE'
        results.append(f'| {model} | {st["accepted"]}/{len(rows)} | {costs} | {tier} | {expected} | ${st["low"]:.4f} – ${st["high"]:.4f} | {st["dispersion"]:.1f}% |')
        work.append(f'| {model} | {expected} | {median_range(rows,"wall_seconds")} | {median_range(rows,"model_calls")} | {median_range(rows,"total_tokens")} |')
        totals = ['not reported' if any(r[k] is None for r in rows) else f'{sum(r[k] for r in rows):,}'
                  for k in ('input_uncached','cache_read','cache_write','output','reasoning','total_tokens')]
        money.append(f'| {model} | ' + ' | '.join(totals) + ' |')
        for r in rows:
            values = [str(r['rep']),r['outcome'],str(r['wall_seconds']),str(r['model_calls'])]
            values += ['not reported' if r[k] is None else f'{r[k]:,}' for k in
                       ('input_uncached','cache_read','cache_write','output','reasoning','total_tokens')]
            runs.append(f'| {model} | ' + ' | '.join(values) + f' | {r["cost_list_usd"]:.6f} |')
    return {'results':'\n'.join(results), 'how-models-worked':'\n'.join(work),
            'per-run':'\n'.join(runs), 'token-totals':'\n'.join(money)}


def load_metrics(root, bundle_root=None, manifest=None):
    root, bundle_root, manifest = edition_context(root, bundle_root, manifest)
    prices = None
    if 'report_rate_table' in manifest:
        table = manifest['report_rate_table']
        repo = Path(__file__).resolve().parent
        path = (repo/table['path']).resolve()
        if Path(table['path']).is_absolute() or not path.is_relative_to(repo):
            raise ValueError('report rate table must be inside repo')
        if sha256_file(path) != table['sha256']:
            raise ValueError('report rate table sha256 mismatch')
        if not re.search(r'^version:\s*'+str(table['version'])+r'\s*$',path.read_text(),re.M):
            raise ValueError('report rate table version mismatch')
        prices = load_prices(path)
    metrics, errors = [], []
    for p in sorted(bundle_root.glob('*/*/*/rep*/result.json')):
        try:
            metrics.append(derive_run_metrics(p.parent, prices, bundle_root, manifest.get('edition')))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f'{p.parent.relative_to(bundle_root)}: {exc}')
    if errors:
        raise ValueError('Cannot derive metrics:\n' + '\n'.join(errors))
    metrics.sort(key=lambda r: r['bundle'])
    return manifest, metrics


def worked_section(metrics, manifest, bundle_root, verification_note=""):
    """The retrospective multi-task section, regenerated only from evidence."""
    stamps = [json.loads((bundle_root/r['bundle']/'stamp.json').read_text()) for r in metrics]
    def recorded(key, transform=lambda v: v):
        return '; '.join(sorted({str(transform(s[key])) if s.get(key) else 'not recorded' for s in stamps}))
    lines = ['<!-- section:how-models-worked -->', '## How each model worked', '',
             'This section was added on 2026-10-13, derived from the unchanged July bundles, and changes no published Edition 1 number.', '',
             '**Recorded environment.** From the bundles’ `stamp.json` files only:', '',
             '| field | recorded value |', '|---|---|',
             f'| machine / OS / architecture | {recorded("machine")} |',
             f'| CLI versions | {recorded("tool_version")} |',
             f'| dates (UTC) | {recorded("date_utc", lambda v: v.split("T")[0])} |',
             '| machine load | not recorded |', '| concurrency | not recorded |',
             '| network conditions / vendor load | not recorded |', '| effort settings | not recorded |', '',
             'Wall time depends on the machine and network, is shown for context, and is never used to rank.', '',
             'Each task is shown separately, in manifest roster order. Summaries give the median and full observed range per run on that task. E[$/accepted] retains the cost of failed runs; zero accepted outcomes mean NO SCORE. There is no combined or cross-task score.', '',
             'For Claude Code, model calls / responses come from `tokens.api_calls`. The count is checked against the session’s usage records, with repeated message IDs counted once. For Codex, the count comes from cumulative `token_count` records, with adjacent identical usage snapshots counted once using the shared Edition 2 derivation. This measures model responses; it does not establish HTTP request counts. The CSV records the method for each run.', '',
             'Wall time comes from each run’s `result.json.wall_seconds`. Token usage is recalculated from the archived session log, and list cost from that bundle’s pinned price snapshot. Billed tokens include each cache read. Output already includes reasoning; adding it separately would double-count it. Unknown reasoning is shown as not reported and left empty in `metrics.csv`, as in Edition 2. Bundle paths in the CSV are relative to the repository or archive root.', '']
    differences = []
    for row in metrics:
        recorded_cost = json.loads((bundle_root/row['bundle']/'result.json').read_text())['cost_list_usd']
        if row['cost_list_usd'] != recorded_cost:
            differences.append(f"`{row['bundle']}`: derived ${row['cost_list_usd']:.6f}, recorded ${recorded_cost:.6f}")
    if differences:
        lines += ['Six-decimal derived versus recorded list costs differ for ' + '; '.join(differences) + '.', '']
    if verification_note:
        lines += [verification_note, '']
    tables = generated_tables(metrics, manifest)
    for task, spec in manifest['tasks'].items():
        lines += [f'### `{task}` — {spec["identity"]}', '']
        for name in ('how-models-worked', 'per-run'):
            key = f'{name}:{task}'
            lines += [f'<!-- table:{key} -->', tables[key], f'<!-- /table:{key} -->', '']
        for name in FIGURE_NAMES:
            lines += [f'<!-- figure:{name}:{task} -->', '']
    lines += ['<!-- /section:how-models-worked -->']
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('root', type=Path)
    args = ap.parse_args()
    root, bundle_root, manifest = edition_context(args.root)
    manifest, metrics = load_metrics(root, bundle_root, manifest)
    tables = generated_tables(metrics, manifest)
    path = root/'REPORT.md'
    text = path.read_text()
    if len(manifest.get('tasks', {})) > 1:
        pattern = r'<!-- section:how-models-worked -->.*?<!-- /section:how-models-worked -->'
        existing = re.search(pattern, text, re.S)
        note = re.search(CHECK_COUNTS_NOTE_PATTERN, existing[0], re.M) if existing else None
        section = worked_section(metrics, manifest, bundle_root, note[0] if note else "")
        if re.search(pattern, text, re.S):
            text, n = re.subn(pattern, lambda m: section, text, flags=re.S)
            if n != 1:
                raise ValueError('expected one how-models-worked section')
        else:
            anchor = '## What this edition does and does not show'
            if text.count(anchor) != 1:
                raise ValueError('expected one results/limitations boundary')
            text = text.replace(anchor, section + '\n\n---\n\n' + anchor)
        with (root/manifest.get('metrics_file', 'metrics.csv')).open('w', newline='') as fh:
            writer = csv.DictWriter(fh, fieldnames=METRIC_FIELDS)
            writer.writeheader(); writer.writerows(metric_strings(r) for r in metrics)
        path.write_text(text)
        print(f'Generated {len(metrics)} per-run metrics and {len(tables)} per-task tables; no cross-task score.')
        return
    with (root/manifest.get('metrics_file', 'metrics.csv')).open('w',newline='') as fh:
        writer = csv.DictWriter(fh,fieldnames=METRIC_FIELDS)
        writer.writeheader();writer.writerows(metric_strings(r) for r in metrics)
    total = sum(r['cost_list_usd'] for r in metrics)
    accepted = sum(r['outcome']=='PASS' for r in metrics)
    n = len(metrics)
    model_count = sum(len(models) for models in manifest['roster'].values())
    words = ('zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten')
    model_word = words[model_count] if model_count < len(words) else str(model_count)
    reps = sorted({len(rows) for _, rows in model_groups(metrics, manifest)})
    replication = '/'.join(map(str, reps))
    edition = manifest.get('edition', metrics[0]['edition'])
    text = re.sub(r'^\*\*'+re.escape(edition)+r' ·.*?\*\*$',
                  f'**{edition} · {model_word} model{"s" if model_count != 1 else ""} · one task · {n} runs · ${total:.4f} at list rates · {accepted} accepted / {n-accepted} not**',
                  text, flags=re.M)
    text = re.sub(r'^\| runs \|.*$', f'| runs | **{n}** ({model_count} models × 1 task × N={replication}) |',text,flags=re.M)
    recorded = sum(json.loads((bundle_root/r['bundle']/'result.json').read_text())['cost_list_usd'] for r in metrics)
    versions = sorted({int(re.search(r'^version:\s*(\d+)\s*$',
                                    (bundle_root/r['bundle']/'prices.snapshot.yaml').read_text(), re.M).group(1))
                       for r in metrics})
    recorded_versions = '/'.join(f'v{v}' for v in versions)
    text = re.sub(r'^\| total spend \|.*$',
                  f'| total spend | **${total:.4f}** (list rates, report table `prices.yaml` v{manifest["report_rate_table"]["version"]}; sum of {n} six-decimal run costs, ${total:.6f}; recorded {recorded_versions} bundle costs sum to ${recorded:.6f}) |',text,flags=re.M)
    text = re.sub(r'^\| outcomes \|.*$',f'| outcomes | {accepted} accepted · {n-accepted} not accepted |',text,flags=re.M)
    for name, table in tables.items():
        pattern = r'(<!-- table:'+name+r' -->\n).*?(\n<!-- /table:'+name+r' -->)'
        text, n = re.subn(pattern,lambda m:m[1]+table+m[2],text,flags=re.S)
        if n != 1:
            raise ValueError(f'expected one table placeholder for {name}')
    path.write_text(text)
    print(f'Generated {len(metrics)} per-run metrics and four report tables; {accepted} accepted; ${total:.6f} at v{manifest["report_rate_table"]["version"]}.')


if __name__ == '__main__':
    main()
