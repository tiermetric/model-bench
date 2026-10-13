#!/usr/bin/env python3
"""Flatten an edition's run bundles into machine-readable datasets.

Produces two files, both stable-schema and safe to append to across editions:

  results.jsonl  -- ONE LINE PER RUN. The atomic record. Never per-cell:
                    aggregation is a group-by the reader performs, so nobody
                    has to trust our summary. This is also what makes a
                    cherry-picked edition detectable -- the runs are all here.
  results.csv    -- the same rows, for spreadsheets and quick plotting.

Why per-run and not per-cell: a cell figure is a claim; a run is evidence. Every
number in a published edition must be recomputable from these rows plus the
archived session logs, which is exactly what verify.py checks.

Usage:  python3 export.py [root] --edition NAME
        python3 export.py [root] --edition all --out DIR [--dry-run]
        python3 export.py [root] --edition 2026-07 --out DIR [--dry-run]

--out builds a local public verification kit, including archived submissions
and redacted usage evidence. It never publishes or pushes. --dry-run validates
the complete file plan without writing; sealed paths are a hard refusal.
"""
import argparse
import csv
import json
import os
import sys
sys.dont_write_bytecode = True
import glob
import shutil
import re
import tempfile
from pathlib import Path
from redact import redact_file, sha256_file, scan_banned
from verify import sealed_paths, is_sealed

FIELDS = [
    "effort", "effort_source", "effort_source_checked", "billing_route",
    "edition", "task", "tool", "model", "model_key", "rep",
    "outcome", "acceptance", "own_tests", "edge_failures",
    "cost_list_usd", "priced",
    "input_uncached", "cache_read", "cache_write", "output", "reasoning",
    "wall_seconds", "agent_exit", "timed_out", "capture",
    "date_utc", "tier_version", "tool_version",
    "prompt", "prompt_file_sha256", "judge", "judge_sha256",
    "prices_yaml_sha256", "python_version", "machine", "bundle",
]

EDITIONS = ('2026-07', '2026-10')


def kit_rows(root, edition):
    """Keep July at root for legacy verification; October is edition-relative."""
    if edition != 'all':
        return list(rows(root, edition))
    data = []
    for ed in EDITIONS:
        source = Path(root) if ed == '2026-07' else Path(root) / 'editions' / ed
        runs = list(rows(source, ed))
        if not runs:
            raise ValueError(f'refusing public kit: no run bundles for {ed}')
        for row in runs:
            if ed != '2026-07':
                row['bundle'] = f'editions/{ed}/{row["bundle"]}'
        data.extend(runs)
    return data


def kit_source(root, name, edition):
    """July's edition directory mirrors the unchanged legacy bundle layout."""
    if edition == 'all' and re.match(r'^editions/2026-07/[^/]+/[^/]+/[^/]+/rep[^/]+/', name):
        name = name.removeprefix('editions/2026-07/')
    return Path(root) / name


def rows(root, edition):
    for p in sorted(glob.glob(os.path.join(root, "*/*/*/rep*/result.json"))):
        d = os.path.dirname(p)
        try:
            with open(p) as fh:
                res = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"WARN: unreadable {p}: {exc}", file=sys.stderr)
            continue
        sp = os.path.join(d, "stamp.json")
        st = {}
        if os.path.exists(sp):
            with open(sp) as fh:
                st = json.load(fh)
        tok = res.get("tokens") or {}
        recorded_edition = res.get("edition") or st.get("edition") or "2026-07"
        if recorded_edition != edition:
            raise ValueError(f"edition mismatch in {p}: {recorded_edition} != {edition}")
        yield {
            "edition": recorded_edition,
            **{k: res.get(k, st.get(k)) for k in ("effort", "effort_source", "effort_source_checked", "billing_route")},
            "task": res.get("task") or st.get("prompt"),
            "tool": res.get("tool"),
            "model": res.get("label"),
            "model_key": res.get("model_key") or st.get("model_flag"),
            "rep": res.get("rep"),
            # outcome is the scored fact; acceptance/own_tests are its inputs.
            # own_tests is diagnostic only -- own PASS + judge FAIL is the
            # "confident failure" signal, and it is only visible if we keep both.
            "outcome": res.get("outcome"),
            "acceptance": res.get("acceptance"),
            "own_tests": res.get("own_tests"),
            "edge_failures": res.get("edge_failures"),
            "cost_list_usd": res.get("cost_list_usd"),
            "priced": res.get("priced"),
            "input_uncached": tok.get("input_uncached"),
            "cache_read": tok.get("cache_read"),
            "cache_write": tok.get("cache_write"),
            "output": tok.get("output"),
            "reasoning": tok.get("reasoning"),
            "wall_seconds": res.get("wall_seconds"),
            "agent_exit": res.get("agent_exit"),
            "timed_out": res.get("timed_out"),
            "capture": res.get("capture"),
            "date_utc": st.get("date_utc"),
            "tier_version": st.get("tier_version"),
            "tool_version": st.get("tool_version"),
            "prompt": st.get("prompt"),
            "prompt_file_sha256": st.get("prompt_file_sha256"),
            "judge": st.get("judge"),
            "judge_sha256": st.get("judge_sha256"),
            "prices_yaml_sha256": st.get("prices_yaml_sha256"),
            "python_version": st.get("python_version"),
            "machine": st.get("machine"),
            # Relative, never absolute: the bundle must be findable after the
            # archive is cloned somewhere else, on someone else's machine.
            "bundle": os.path.relpath(d, root),
        }


def refuse_sealed(paths, exclusions, root=None):
    for name in paths:
        # The same boundary applies inside editions and archived submissions.
        parts = Path(name).parts
        if any(is_sealed(Path(*parts[i:]), exclusions, root) for i in range(len(parts))):
            raise ValueError(f"refusing public kit: sealed path {name}")


def kit_plan(root, data, edition):
    """Allow only verification code, revealed dependencies, and run evidence."""
    root = Path(root).resolve()
    exclusions = sealed_paths(root)
    if not (root / ".export-exclude").is_file():
        raise ValueError("refusing public kit: .export-exclude absent")
    sealed_hashes = {sha256_file(p) for p in root.rglob("*")
                     if p.is_file() and not p.is_symlink() and is_sealed(p, exclusions, root)}
    sealed_tasks = {Path(name).stem.casefold() for name in exclusions
                    if name.startswith('prompts/') and name.endswith('.md')}
    revealed_hashes = {sha256_file(p) for p in (root / 'prompts').glob('*.md')
                       if not is_sealed(p, exclusions, root)}
    files = set()

    def add(name):
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError(f"refusing public kit: invalid artifact path {name}")
        refuse_sealed([name], exclusions, root)
        if {'publish', 'edition2-raw'} & {part.casefold() for part in Path(name).parts}:
            raise ValueError(f"refusing public kit: private export path {name}")
        source = kit_source(root, name, edition)
        if any(p.is_symlink() for p in (source, *source.parents) if p != root and root in p.parents):
            raise ValueError(f"refusing public kit: symlink {name}")
        if source.is_dir():
            for child in sorted(source.iterdir()):
                if child.name != "__pycache__":
                    add(f'{name}/{child.name}')
        elif source.is_file():
            if not source.name.endswith(".pyc"):
                if sha256_file(source) in sealed_hashes:
                    raise ValueError(f"refusing public kit: sealed content in {name}")
                files.add(name)
        else:
            raise ValueError(f"refusing public kit: required artifact absent: {name}")

    for name in ("verify.py", "edition_report.py", "export.py", "score.py", "token_evidence.py", "redact.py",
                 "aggregate.py", "run_audit.py", "preflight_check.py", "control/test_harness.py",
                 "TASKS.md", "PROTOCOL.md", "LICENSE", "LICENSE-DATA", ".export-exclude"):
        add(name)
    if edition == 'all' and any(row['edition'] not in EDITIONS for row in data):
        raise ValueError('refusing public kit: unknown bundle edition')
    for ed in EDITIONS if edition == 'all' else (edition,):
        for name in ('REPORT.md', 'MANIFEST.json', 'metrics.csv'):
            add(f'editions/{ed}/{name}')
        manifest = json.loads((root / f'editions/{ed}/MANIFEST.json').read_text())
        if manifest.get('edition') != ed:
            raise ValueError(f'refusing public kit: manifest edition mismatch for {ed}')
        if 'report_rate_table' in manifest:
            table = manifest['report_rate_table']
            if table['path'] != 'prices.yaml':
                raise ValueError('refusing public kit: report rate table accepts only prices.yaml')
            if sha256_file(root / 'prices.yaml') != table['sha256']:
                raise ValueError('refusing public kit: report rate table sha256 mismatch')
            version = re.search(r'^version:\s*(\d+)\s*$', (root / 'prices.yaml').read_text(), re.M)
            if version is None or int(version[1]) != table['version']:
                raise ValueError('refusing public kit: report rate table version mismatch')
            add('prices.yaml')
        expected = sum(map(len, manifest['roster'].values())) * sum(
            spec.get('reps_run', spec['reps']) for spec in manifest['tasks'].values())
        if sum(row['edition'] == ed for row in data) != expected:
            raise ValueError(f'refusing public kit: bundle count differs from manifest for {ed}')
        for name in ('EVIDENCE-MANIFEST.jsonl', 'CODEX-RESPONSE-AUDIT.json'):
            exists = (root / f'editions/{ed}/{name}').is_file()
            if ed == '2026-10' and name == 'EVIDENCE-MANIFEST.jsonl' and not exists:
                raise ValueError(f'refusing public kit: missing original receipt for {ed}')
            if ed == '2026-10' or exists:
                add(f'editions/{ed}/{name}')
    for row in data:
        if any(Path(part).stem.casefold() in sealed_tasks
               for field in ('task', 'prompt', 'bundle')
               for part in Path(row.get(field) or '').parts):
            raise ValueError('refusing public kit: sealed task in run path or name')
        for field in ("prompt_file_sha256", "prices_yaml_sha256"):
            if not isinstance(row[field], str) or not re.fullmatch(r"[0-9a-f]{64}", row[field]):
                raise ValueError(f"refusing public kit: invalid {field}")
        if row["prompt_file_sha256"] in sealed_hashes:
            raise ValueError("refusing public kit: prompt hash identifies sealed content")
        bundle = row["bundle"]
        for name in ("result.json", "stamp.json", "prices.snapshot.yaml",
                     "session.jsonl", "session.jsonl.sha256", "submission"):
            add(f"{bundle}/{name}")
        # Preserve these original archives where they are. This does not
        # assemble an orgsync re-judge target or change submission/.
        if row["task"] == "orgsync":
            for name in ("orgsync", "config.json", "tests/existing"):
                add(f"{bundle}/{name}")
        judge = row["judge"]
        if not judge or Path(judge).is_absolute() or ".." in Path(judge).parts:
            raise ValueError("refusing public kit: invalid stamped judge path")
        # Revealed judges only. A sealed/absent judge travels as a hash, so
        # the verifier will SKIP it rather than disclosing an answer key.
        if not is_sealed(judge, exclusions, root) and (root / judge).is_file():
            add(judge)
            if judge == "acceptance_orgsync.py":
                add("fixtures/orgsync")
                add("tasks/orgsync-skeleton")
        evidence = ('editions/2026-10/evidence' if edition == 'all' and row['edition'] == '2026-10'
                    else 'evidence')
        prompt = f"{evidence}/prompts/{row['prompt_file_sha256']}.md"
        if (row['prompt_file_sha256'] not in revealed_hashes
                or sha256_file(root / prompt) != row['prompt_file_sha256']):
            raise ValueError('refusing public kit: evidence must hash-match a revealed prompt')
        add(prompt)
        add(f"{evidence}/prices/{row['prices_yaml_sha256']}.yaml")
    if edition == 'all':
        for name in list(files):
            if any(name.startswith(row['bundle'] + '/') for row in data if row['edition'] == '2026-07'):
                add('editions/2026-07/' + name)
    files.update(("results.csv", "results.jsonl", "EVIDENCE-MANIFEST.jsonl"))
    refuse_sealed(files, exclusions, root)
    return sorted(files), exclusions


def write_datasets(root, data):
    root = Path(root)
    with (root / "results.jsonl").open("w") as fh:
        for row in data:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    with (root / "results.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(data)


def export_kit(root, out, data, edition, dry_run=False):
    root, out = Path(root).resolve(), Path(out).resolve()
    files, exclusions = kit_plan(root, data, edition)
    if out == root or root in out.parents and out.name != "publish":
        raise ValueError("refusing public kit: use an external directory or <root>/publish")
    if out.exists() and any(out.iterdir()):
        refuse_sealed([p.relative_to(out).as_posix() for p in out.rglob("*")], exclusions, out)
        raise ValueError("refusing public kit: output directory must be empty")
    submissions = sum("/submission/" in name for name in files)
    submission_dirs = len({name.split("/submission/", 1)[0] for name in files if "/submission/" in name})
    # Validate the complete payload in scratch before touching the destination,
    # including dry runs. Raw logs are read only by the redactor, never copied.
    with tempfile.TemporaryDirectory(prefix='model-bench-kit-stage-') as tmp:
        stage = Path(tmp)
        write_kit(root, stage, data, edition, files, exclusions)
        for path in stage.rglob('*'):
            if path.is_file():
                hits = scan_banned(path)
                if hits:
                    raise ValueError(f'refusing public kit: redaction leak in {path.relative_to(stage)}: {hits}')
        if not dry_run:
            out.mkdir(parents=True, exist_ok=True)
            shutil.copytree(stage, out, dirs_exist_ok=True)
    print(f"PUBLIC KIT {'DRY-RUN' if dry_run else 'LOCAL'}: {len(data)} bundles · "
          f"{submission_dirs} submission directories · {submissions} submission files · "
          f"{len(files)} files · 0 sealed paths")


def write_kit(root, out, data, edition, files, exclusions):
    generated = {"results.csv", "results.jsonl", "EVIDENCE-MANIFEST.jsonl"}
    generated.update(f"{row['bundle']}/{name}" for row in data
                     for name in ("result.json", "session.jsonl", "session.jsonl.sha256"))
    if edition == 'all':
        generated.update('editions/2026-07/' + name for name in list(generated)
                         if any(name.startswith(row['bundle'] + '/') for row in data if row['edition'] == '2026-07'))
    for name in files:
        if name in generated:
            continue
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        source = kit_source(root, name, edition)
        hits = scan_banned(source)
        if hits:
            raise ValueError(f'refusing public kit: redaction leak in {name}: {hits}')
        # Actual session logs nested in submissions must already be usage-only.
        if source.name in ('session.jsonl', 'agent-output.txt'):
            redacted = out / '.redaction-check'
            redact_file(source, redacted)
            if source.read_bytes() != redacted.read_bytes():
                raise ValueError(f'refusing public kit: unredacted session log {name}')
            redacted.unlink()
        shutil.copyfile(source, target)
    receipts = []
    for row in data:
        bundle = row["bundle"]
        source, target = root / bundle, out / bundle
        fmt, kept = redact_file(source / "session.jsonl", target / "session.jsonl")
        digest = sha256_file(target / "session.jsonl")
        (target / "session.jsonl.sha256").write_text(digest + "\n")
        res = json.loads((source / "result.json").read_text())
        for key, value in res.items():
            if isinstance(value, str) and value.startswith("/"):
                res[key] = os.path.basename(value)
        (target / "result.json").write_text(json.dumps(res, indent=2, sort_keys=True) + "\n")
        original_sha256 = sha256_file(source / 'session.jsonl')
        original_bytes = (source / 'session.jsonl').stat().st_size
        edition_root = root / 'editions' / row['edition']
        receipt_path = edition_root / 'EVIDENCE-MANIFEST.jsonl'
        if receipt_path.is_file() or original_sha256 == digest:
            relative_bundle = source.relative_to(edition_root).as_posix() if edition_root in source.parents else bundle
            committed = [json.loads(line) for line in receipt_path.read_text().splitlines()
                         if line.strip()] if receipt_path.is_file() else []
            matches = [r for r in committed if r.get('bundle') == relative_bundle]
            if len(matches) != 1 or not all(k in matches[0] for k in ('original_sha256', 'original_bytes')):
                raise ValueError(f'refusing public kit: missing original receipt for {bundle}')
            original_sha256, original_bytes = matches[0]['original_sha256'], matches[0]['original_bytes']
        receipts.append(dict(bundle=bundle, format=fmt, records_kept=kept,
                             original_sha256=original_sha256, original_bytes=original_bytes,
                             redacted_sha256=digest))
        if edition == 'all' and row['edition'] == '2026-07':
            for name in ('result.json', 'session.jsonl', 'session.jsonl.sha256'):
                mirror = out / 'editions/2026-07' / bundle / name
                mirror.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target / name, mirror)
    (out / "EVIDENCE-MANIFEST.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in receipts))
    write_datasets(out, data)
    refuse_sealed([p.relative_to(out).as_posix() for p in out.rglob("*")], exclusions, out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('root', nargs='?', default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument('--edition', required=True, choices=[*EDITIONS, 'all'])
    ap.add_argument('--out', help='build a local public kit in an empty directory')
    ap.add_argument('--dry-run', action='store_true', help='validate the public kit without writing')
    args = ap.parse_args()
    root, edition = args.root, args.edition
    if args.dry_run and not args.out:
        ap.error('--dry-run requires --out')
    if edition == 'all' and not args.out:
        ap.error('--edition all requires --out')

    try:
        data = kit_rows(root, edition)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not data:
        print("no run bundles found", file=sys.stderr)
        return 1

    if args.out:
        try:
            export_kit(root, args.out, data, edition, args.dry_run)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    write_datasets(root, data)
    jl, cv = os.path.join(root, "results.jsonl"), os.path.join(root, "results.csv")

    passes = sum(1 for r in data if r["outcome"] == "PASS")
    spend = sum(r["cost_list_usd"] or 0 for r in data)
    tasks = sorted({r["task"] for r in data})
    models = sorted({r["model"] for r in data})
    print(f"exported {len(data)} runs -> {jl} + {cv}")
    print(f"  edition : {edition}")
    print(f"  tasks   : {', '.join(tasks)}")
    print(f"  models  : {len(models)} ({', '.join(models)})")
    print(f"  outcomes: {passes} PASS / {len(data) - passes} not-PASS")
    print(f"  spend   : ${spend:.4f} (list rates, all runs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
