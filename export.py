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
import hashlib
import ipaddress
import socket
import subprocess
from pathlib import Path
from redact import redact_file, sha256_file, scan_banned
from verify import sealed_paths, is_sealed
from errata import load_errata

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


# Literal entries only: never widen these to prompts/ or design/orgsync*.
# Keep this list reusable by the separate historical-lineage generator.
REVEALED_PROMPTS = ("prompts/baseline.md", "prompts/cronspec.md", "prompts/orgsync.md")
REVEALED_JUDGES = ("acceptance_test.py", "acceptance_cronspec.py", "acceptance_orgsync.py")
KIT_FILES = (
    "verify.py", "edition_report.py", "export.py", "score.py", "token_evidence.py", "redact.py",
    "errata.py", "aggregate.py", "run_audit.py", "preflight_check.py", "control/test_harness.py",
    "ERRATA.md", "TASKS.md", "PROTOCOL.md", "LICENSE", "LICENSE-DATA", ".export-exclude",
    "README.md", "run.sh", "batch.sh", "edition.sh", "preflight.sh", "preflight_http.py",
    "evidence.py", "master_table.py", "check-calibration-gate.sh", "results.md", "TIER_PIN",
    "calibration/README.md", "calibration/orgsync.cleared",
    *REVEALED_PROMPTS, "ARCHIVE-EXPORT.jsonl",
    "harness_lineage.py", "HARNESS-LINEAGE.json", "harness/README.md",
    "control/test_harness_lineage.py", "control/test_kit_references.py",
    "control/Dockerfile.exported-controls", "control/test_exported_controls.py", "control/exported_controls.py", "control/test_errata.py",
    "control/check-cronspec-selftest.py", "control/cronspec_reference.py",
    "control/cronspec_vixie.py", "control/cronspec_naive_careful.py",
    "rejudge_workflow.py", "rejudge-networkless.sh", "REJUDGE-RECEIPT.json",
    "control/test_rejudge_workflow.py",
)
KIT_DIRECTORIES = ("design/orgsync/",)
REVEALED_TASK_DIRECTORIES = ("fixtures/orgsync/", "tasks/orgsync-skeleton/")
# Execution metadata selected by kit_plan's edition/report checks below.
HARNESS_METADATA_FILES = ("prices.yaml", "editions/2026-10/TIER_PIN", "editions/2026-10/MANIFEST.json")
# Authored kit documents also contain reviewed operator-name references.
KIT_SANITIZE_FILES = ("master_table.py", "TASKS.md", "results.md", "PROTOCOL.md")


def scan_export_text(text, source_path=False):
    """Identity checks apply to source components as well as published bytes."""
    if not source_path:
        # Existing public attribution and policy statements are intentional.
        text = text.replace("- Intenteon properties **link** the report; they do not restate its numbers.", "")
        text = text.replace("Attribution: TIER Model Report / Inten" + "teon", "")
        # Rate tables use model@provider as a key, not an operator address.
        text = re.sub(r'(?m)^  "[\w.+/-]+@(?:openrouter\.ai|api\.(?:together|fireworks|groq|deepinfra)\.(?:ai|com))":', '', text)
        # Revealed orgsync fixtures use reserved example addresses.
        text = re.sub(r'[\w.+-]+@(?:example\.(?:com|net|org|invalid)|[\w.-]+\.(?:test|invalid))(?![\w.-])', '', text)
    patterns = (
        (r"inten" + "teon", "private identity"),
        (r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", "email address"),
        (r"\b[\w-]+\.local\b", "host name"),
        (r"(?:^|/)(?:-?(?:Users|home)/|-(?:Users|home)-)[^/\s]+", "home-directory source path"),
    )
    hits = [(label, len(re.findall(pattern, text, re.I))) for pattern, label in patterns
            if re.search(pattern, text, re.I)]
    host = socket.gethostname().casefold().rstrip('.')
    generic = {'localhost', 'localhost.localdomain', 'localhost6', 'localhost6.localdomain6',
               'ip6-localhost', 'ip6-loopback', 'localdomain', 'host', 'hostname', 'unknown', 'none', '(none)'}
    try:
        ipaddress.ip_address(host.strip('[]'))
        host = ''
    except ValueError:
        pass
    if (host and host not in generic and
            re.search(r'(?<![\w.-])' + re.escape(host) + r'(?![\w.-])', text, re.I)):
        hits.append(("operator host name", 1))
    return hits


def scan_export_file(path):
    return scan_banned(path) + scan_export_text(path.read_text(encoding='utf-8', errors='replace'))


def archive_manifest(root):
    """Each archived file and its transformation must be reviewed explicitly."""
    entries = {}
    for line in (Path(root) / 'ARCHIVE-EXPORT.jsonl').read_text().splitlines():
        entry = json.loads(line)
        if not isinstance(entry, dict) or set(entry) != {'path', 'transform'}:
            raise ValueError('refusing public kit: invalid archive manifest entry')
        name, transform = entry['path'], entry['transform']
        if (not isinstance(name, str) or name != Path(name).as_posix() or
                '..' in Path(name).parts or not name.startswith('archive/') or
                name in entries or transform not in ('copy', 'usage', 'sanitize')):
            raise ValueError('refusing public kit: invalid archive manifest entry')
        if Path(name).name.casefold() in ('agent-output.txt', 'session.jsonl', 'cronspec-pilot.log') and transform != 'usage':
            raise ValueError('refusing public kit: archive raw logs require usage transformation')
        entries[name] = transform
    return entries


def sealed_contents(root, exclusions):
    """Include historical sealed blobs, even when the working copy changed."""
    contents = {p.read_bytes() for p in root.rglob('*')
                if p.is_file() and not p.is_symlink() and is_sealed(p, exclusions, root)}
    if exclusions and (root / '.git').exists():
        objects = subprocess.run(['git', '-C', str(root), 'rev-list', '--objects', '--all',
                                  '--', *exclusions], check=True, capture_output=True).stdout
        ids = sorted({line.split(b' ', 1)[0] for line in objects.splitlines()})
        result = b''
        if ids:
            # Observed on macOS (Python 3.14.8): communicate() blocked writing stdin while
            # git blocked on a full stdout pipe. A file as stdin cannot fill.
            with tempfile.TemporaryFile() as request:
                request.write(b'\n'.join(ids) + b'\n')
                request.seek(0)
                result = subprocess.run(['git', '-C', str(root), 'cat-file', '--batch'],
                                        stdin=request, check=True, capture_output=True).stdout
        while result:
            header, result = result.split(b'\n', 1)
            _, kind, size = header.split()
            size = int(size)
            if kind == b'blob':
                contents.add(result[:size])
            result = result[size + 1:]
    return contents


def normalize_sealed_text(payload):
    text = payload.decode('utf-8', errors='replace')

    def strings(value):
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [s for pair in value.items() for item in pair for s in strings(item)]
        if isinstance(value, list):
            return [s for item in value for s in strings(item)]
        return []

    try:
        text = '\n'.join(strings(json.loads(text)))
    except (ValueError, RecursionError):
        # JSONL wrappers can carry a copied prompt in a single record, too.
        decoded = []
        for line in text.splitlines():
            try:
                decoded.extend(strings(json.loads(line)))
            except (ValueError, RecursionError):
                decoded.append(line)
        text = '\n'.join(decoded)
    text = text.replace('\r', '')
    text = re.sub(r'(?m)^[ \t]*(?:(?:>|#)[ \t]*)+', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def refuse_sealed_content(source, contents):
    payload = source.read_bytes()
    normalized = normalize_sealed_text(payload)
    # Detect exact and normalized copies, wrappers, and small internal edits.
    # This is a similarity gate, not semantic detection: paraphrases, encodings
    # beyond JSON strings, and edits affecting multiple windows may escape it.
    for secret in contents:
        for candidate, published in ((secret, payload), (normalize_sealed_text(secret), normalized)):
            copied = candidate == published or (len(candidate) >= 80 and candidate in published)
            if not copied and len(candidate) >= 512:
                # Seven of eight distributed 128-unit windows survive a small edit;
                # one shared phrase or a public hash citation cannot trip this gate.
                windows = [candidate[(len(candidate) - 128) * i // 7:][:128] for i in range(8)]
                copied = sum(window in published for window in windows) >= 7
            if copied:
                raise ValueError(f'refusing public kit: sealed content in {source.name}')


def identity_map():
    """Load the reviewed private mapping without disclosing its keys."""
    raw = os.environ.get('EXPORT_IDENTITY_MAP')
    try:
        mapping = json.loads(raw) if raw else None
    except ValueError:
        mapping = None
    if (not isinstance(mapping, dict) or not mapping or
            any(not isinstance(k, str) or not k.strip() or
                not isinstance(v, str) or not v.strip() for k, v in mapping.items())):
        raise ValueError('refusing public kit: EXPORT_IDENTITY_MAP requires a nonempty JSON string mapping')
    return mapping


def refuse_identity_keys(path, name):
    """Fail closed on known identity substrings in any publishable text."""
    mapping = identity_map()
    text = path.read_text(encoding='utf-8', errors='replace').casefold()
    if any(key.casefold() in text for key in mapping):
        raise ValueError(f'refusing public kit: identity remains in {name}')


def sanitize_archive(source, target):
    """Only publishable copies change; originals and their evidence stay put."""
    from redact import BANNED
    text = source.read_text(encoding='utf-8')
    text = re.sub(BANNED[0][0].pattern, '[home]', text, flags=re.I)
    text = re.sub(r'(?<=/)-?(?:Users|home)[-/][^/\\\" \r\n]+', '[home]', text, flags=re.I)
    # Identity spellings belong only in the maintainer's private environment.
    # Refuse every sanitize operation without a reviewed mapping: guessing
    # whether unknown private identities occur would fail open.
    mapping = identity_map()
    for private, public in sorted(mapping.items(), key=lambda pair: -len(pair[0])):
        text = re.sub(r'(?<!\w)' + re.escape(private) + r'(?!\w)',
                      lambda match: public, text, flags=re.I)
    target.write_text(text, encoding='utf-8')


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
        tasks = {Path(path).stem.casefold() for path in exclusions
                 if path.startswith('prompts/') and path.endswith('.md')}
        if any(Path(part).stem.casefold() in tasks for part in parts):
            raise ValueError(f"refusing public kit: sealed path {name}")
        if any(is_sealed(Path(*parts[i:]), exclusions, root) for i in range(len(parts))):
            raise ValueError(f"refusing public kit: sealed path {name}")


def kit_plan(root, data, edition):
    """Allow only verification code, revealed dependencies, and run evidence."""
    root = Path(root).resolve()
    exclusions = sealed_paths(root)
    if not (root / ".export-exclude").is_file():
        raise ValueError("refusing public kit: .export-exclude absent")
    contents = sealed_contents(root, exclusions)
    sealed_hashes = {hashlib.sha256(payload).hexdigest() for payload in contents}
    sealed_tasks = {Path(name).stem.casefold() for name in exclusions
                    if name.startswith('prompts/') and name.endswith('.md')}
    revealed_hashes = {sha256_file(root / name) for name in REVEALED_PROMPTS
                       if (root / name).is_file() and not is_sealed(name, exclusions, root)}
    files = set()

    def add(name):
        name = Path(name).as_posix()
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError(f"refusing public kit: invalid artifact path {name}")
        refuse_sealed([name], exclusions, root)
        if {'publish', 'edition2-raw'} & {part.casefold() for part in Path(name).parts}:
            raise ValueError(f"refusing public kit: private export path {name}")
        hits = scan_export_text(name, source_path=True)
        if hits:
            raise ValueError(f"refusing public kit: redaction leak in source path: {hits}")
        source = kit_source(root, name, edition)
        if any(p.is_symlink() for p in (source, *source.parents) if p != root and root in p.parents):
            raise ValueError(f"refusing public kit: symlink {name}")
        if source.is_dir():
            for child in sorted(source.iterdir()):
                if child.name != "__pycache__":
                    add(f'{name}/{child.name}')
        elif source.is_file():
            if not source.name.endswith(".pyc"):
                refuse_sealed_content(source, contents)
                files.add(name)
        else:
            raise ValueError(f"refusing public kit: required artifact absent: {name}")

    for name in (*KIT_FILES, *KIT_DIRECTORIES):
        add(name)
    for name in archive_manifest(root):
        if not (root / name).is_file():
            raise ValueError(f'refusing public kit: archive entry is not a file: {name}')
        add(name)
    # No edition-1 pin was recorded; the root pin is already in KIT_FILES.
    if edition in ('all', '2026-10'):
        add('editions/2026-10/TIER_PIN')
    if edition == 'all' and any(row['edition'] not in EDITIONS for row in data):
        raise ValueError('refusing public kit: unknown bundle edition')
    for ed in EDITIONS if edition == 'all' else (edition,):
        for name in ('REPORT.md', 'MANIFEST.json', 'metrics.csv'):
            add(f'editions/{ed}/{name}')
        manifest = json.loads((root / f'editions/{ed}/MANIFEST.json').read_text())
        for name in load_errata(root / 'editions' / ed)[0]:
            add(f'editions/{ed}/{name}')
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
        evidence = ('editions/2026-10/evidence' if edition == 'all' and row['edition'] == '2026-10'
                    else 'evidence')
        judge = row["judge"]
        if not judge or Path(judge).is_absolute() or ".." in Path(judge).parts:
            raise ValueError("refusing public kit: invalid stamped judge path")
        # Revealed judges only. A sealed/absent judge travels as a hash, so
        # the verifier will SKIP it rather than disclosing an answer key.
        if not is_sealed(judge, exclusions, root) and (root / judge).is_file():
            if judge not in REVEALED_JUDGES:
                raise ValueError('refusing public kit: judge is not allowlisted')
            judge_evidence = f"{evidence}/judges/{row['judge_sha256']}.py"
            if (row['judge_sha256'] != sha256_file(root / judge)
                    or sha256_file(root / judge_evidence) != row['judge_sha256']):
                raise ValueError('refusing public kit: evidence must hash-match a revealed judge')
            add(judge)
            add(judge_evidence)
            if judge == "acceptance_orgsync.py":
                for name in REVEALED_TASK_DIRECTORIES:
                    add(name)
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
    # Content addresses are enumerated by the screened lineage manifest, never
    # by recursively allowing everything someone puts under harness/blobs/.
    from harness_lineage import public_files
    for name in public_files(root, (row['bundle'] for row in data), edition):
        add(name)
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
        for ed in EDITIONS if edition == 'all' else (edition,):
            source_files, _ = load_errata(root / 'editions' / ed)
            staged_files, _ = load_errata(stage / 'editions' / ed)
            if source_files != staged_files:
                raise ValueError(f'refusing public kit: omitted errata for {ed}')
        contents = sealed_contents(root, exclusions)
        for path in stage.rglob('*'):
            if path.is_file():
                refuse_sealed_content(path, contents)
                refuse_identity_keys(path, path.relative_to(stage))
                hits = scan_export_file(path)
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
    archives = archive_manifest(root)
    for name in files:
        if name in generated:
            continue
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        source = kit_source(root, name, edition)
        transform = archives.get(name, 'copy')
        if transform != 'copy':
            if transform == 'usage':
                _, kept = redact_file(source, target)
                if kept == 0:
                    raise ValueError(f'refusing public kit: usage transform kept 0 records: {name}')
            else:
                sanitize_archive(source, target)
            hits = scan_export_file(target)
            if hits:
                raise ValueError(f'refusing public kit: redaction leak in {name}: {hits}')
            continue
        hits = scan_export_file(source)
        if hits:
            raise ValueError(f'refusing public kit: redaction leak in {name}: {hits}')
        # Actual session logs nested in submissions must already be usage-only.
        if source.name.casefold() in ('session.jsonl', 'agent-output.txt'):
            redacted = out / '.redaction-check'
            redact_file(source, redacted)
            if source.read_bytes() != redacted.read_bytes():
                raise ValueError(f'refusing public kit: unredacted session log {name}')
            redacted.unlink()
        if name in KIT_SANITIZE_FILES:
            sanitize_archive(source, target)
            shutil.copymode(source, target)
        else:
            shutil.copy(source, target)
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
