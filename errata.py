#!/usr/bin/env python3
"""Append immutable errata sidecars; never rewrite published edition artifacts."""
import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import sys
import tempfile

from redact import scan_banned, sha256_file


def validate_entries(entries, identity=False):
    if not isinstance(entries, list) or not entries:
        raise ValueError('errata entries must be a nonempty list')
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'date', 'claim', 'replacement', 'evidence'}:
            raise ValueError('errata entry requires date/claim/replacement/evidence')
        if any(not isinstance(entry[k], str) or not entry[k].strip()
               for k in ('date', 'claim', 'replacement')):
            raise ValueError('errata date/claim/replacement must be nonempty strings')
        if date.fromisoformat(entry['date']).isoformat() != entry['date']:
            raise ValueError('errata date must be YYYY-MM-DD')
        evidence = entry['evidence']
        if not isinstance(evidence, list) or not evidence:
            raise ValueError('errata evidence must be a nonempty list of public references')
        for ref in evidence:
            if (not isinstance(ref, str) or not ref.strip()
                    # Remove the allowed URL introducer so its slashes do not
                    # look like a local path; inspect all punctuation boundaries.
                    or re.search(r'(?<![A-Za-z0-9])[/\\]|~[^/\\\s]*[/\\]|[A-Za-z]:[/\\]',
                                 ref.replace('https://', ''))
                    or '..' in re.split(r'[/\\]', ref)
                    or any(match.group() != 'https:' or ref[match.end():match.end()+2] != '//'
                           for match in re.finditer(r'[A-Za-z][A-Za-z0-9+.-]*:', ref))):
                raise ValueError('errata evidence must cite public artifacts, never local paths')
    payload = json.dumps(entries, ensure_ascii=False)
    if re.search(r'/(?:private/)?tmp/', payload):
        raise ValueError('errata contains a local temporary path')
    if identity:
        with tempfile.NamedTemporaryFile(mode='w+', encoding='utf-8') as scratch:
            scratch.write(payload); scratch.flush()
            if scan_banned(scratch.name):
                raise ValueError('errata identity/redaction scan failed')


def read_object(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'errata required regular file absent or symlink: {path.name}')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(f'errata JSON object required: {path.name}')
    return data


def check_reference(ref, path):
    if (not isinstance(ref, dict) or set(ref) != {'path', 'sha256'}
            or ref['path'] != path.name
            or not isinstance(ref['sha256'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', ref['sha256'])):
        raise ValueError(f'errata invalid reference: {path.name}')
    read_object(path)
    if sha256_file(path) != ref['sha256']:
        raise ValueError(f'errata sha256 mismatch: {path.name}')


def load_errata(edition_dir):
    """Validate every revision, returning relative filenames and cumulative entries.

    Legacy editions with no sidecars return empty lists.
    The highest contiguous revision is current; older sidecars remain required.
    """
    root = Path(edition_dir).resolve()
    names = {p.name for pattern in ('MANIFEST.v*.json', 'ERRATA.v*.json') for p in root.glob(pattern)}
    if not names:
        return [], []
    revisions = set()
    for name in names:
        match = re.fullmatch(r'(?:MANIFEST|ERRATA)\.v([1-9][0-9]*)\.json', name)
        if not match:
            raise ValueError(f'errata invalid revision filename: {name}')
        revisions.add(int(match[1]))
    if sorted(revisions) != list(range(1, len(revisions) + 1)):
        raise ValueError('errata revision gap')
    original = root / 'MANIFEST.json'
    base = read_object(original)
    if {'revision', 'predecessor', 'errata'} & base.keys():
        raise ValueError('errata original manifest must remain unchanged')
    if base.get('edition') != root.name:
        raise ValueError('errata edition directory differs from original manifest')
    files, entries, previous = [], [], original
    for number in sorted(revisions):
        manifest_path = root / f'MANIFEST.v{number}.json'
        errata_path = root / f'ERRATA.v{number}.json'
        manifest = read_object(manifest_path)
        if (set(manifest) != set(base) | {'revision', 'predecessor', 'errata'}
                or any(manifest.get(k) != v for k, v in base.items())
                or type(manifest.get('revision')) is not int or manifest['revision'] != number):
            raise ValueError('errata successor must preserve original manifest fields')
        check_reference(manifest['predecessor'], previous)
        check_reference(manifest['errata'], errata_path)
        data = read_object(errata_path)
        if (set(data) != {'schema_version', 'edition', 'revision', 'entries'}
                or type(data['schema_version']) is not int or data['schema_version'] != 1
                or data['edition'] != base['edition']
                or type(data['revision']) is not int or data['revision'] != number):
            raise ValueError('errata document schema/edition/revision invalid')
        validate_entries(data['entries'])
        if data['entries'][:len(entries)] != entries or len(data['entries']) <= len(entries):
            raise ValueError('errata revisions must append entries without rewriting history')
        entries, previous = data['entries'], manifest_path
        files.extend([errata_path.name, manifest_path.name])
    return files, entries


def append_errata(edition_dir, additions):
    """Create the next pair using exclusive writes; keep all predecessor bytes."""
    root = Path(edition_dir).resolve()
    files, entries = load_errata(root)
    validate_entries(additions, identity=True)
    newest = max((date.fromisoformat(entry['date']) for entry in entries), default=date.min)
    today = datetime.now(timezone.utc).date()
    for entry in additions:
        added = date.fromisoformat(entry['date'])
        if added < newest:
            raise ValueError('errata addition date precedes newest existing entry')
        if added > today:
            raise ValueError('errata addition date is after today (UTC)')
    number = len(files) // 2 + 1
    previous = root / ('MANIFEST.json' if number == 1 else f'MANIFEST.v{number-1}.json')
    base = read_object(root / 'MANIFEST.json')
    if base.get('edition') != root.name or {'revision', 'predecessor', 'errata'} & base.keys():
        raise ValueError('errata requires an unchanged edition manifest')
    errata_path, manifest_path = root / f'ERRATA.v{number}.json', root / f'MANIFEST.v{number}.json'
    data = dict(schema_version=1, edition=base['edition'], revision=number, entries=entries + additions)
    # Track exclusive creations so refusal never removes an existing artifact.
    created = []
    try:
        with errata_path.open('x', encoding='utf-8') as target:
            created.append(errata_path)
            target.write(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
        successor = dict(base, revision=number,
                         predecessor=dict(path=previous.name, sha256=sha256_file(previous)),
                         errata=dict(path=errata_path.name, sha256=sha256_file(errata_path)))
        with manifest_path.open('x', encoding='utf-8') as target:
            created.append(manifest_path)
            target.write(json.dumps(successor, indent=2, ensure_ascii=False) + '\n')
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
    return manifest_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('edition_dir')
    parser.add_argument('--entries', required=True, help='JSON list of new entries to append')
    args = parser.parse_args()
    try:
        print(append_errata(args.edition_dir, json.loads(Path(args.entries).read_text(encoding='utf-8'))))
    except (OSError, ValueError) as exc:
        print(f'errata refused: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
