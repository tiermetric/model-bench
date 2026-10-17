#!/usr/bin/env python3
"""Generate screened harness lineage from exact export-allowlisted paths.

Private maintainer: python3 harness_lineage.py [root] [--out DIR] [--check]
Public readers need no private Git history; see harness/README.md.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
import export

# Eight report/task-ledger comparison revisions, plus the public score.py
# portability revision. The sanitized October copy is published as blob
# ea60b6552b3eadad2608698061824f7ad2d07d36.
COMPARISON_REVISIONS = (
    'da2561f0f228467d0914cba9e2d8b2624d93cca1',
    '14f21dce0b8db594001ea368ad7ce3fe2edd6090',
    'ec9574f7de40d07e81d093d6fd4eabc99f79cf93',
    '849c8cc561919c57b23cccc14e92985ba490d9d8',
    '3a5c952577b779c324d27e303224908ae2c55e32',
    '69e184364164b83005718c4fbb71f6aafc8d2c8c',
    '66bd8ecc5e0af404121c8f4f68a895de43cc0728',
    '1ad7ea8196a619fb60124986ede05af557fb3a18',
    '2d1a87b2217b65fd219b3139ba0fc68a2b8a7d1a',
)
# Commit times measured once from the three July run.sh revisions (UTC).
JULY_RUN_REVISIONS = (
    ('2026-07-22T22:25:56Z', '8fce6350c86786c2e68979cf498b8b99e2d96bd4'),
    ('2026-07-22T23:48:01Z', '5508c75b7603db3ddf2c501e7999c8fd9f6f1281'),
    ('2026-07-23T00:36:17Z', 'da2561f0f228467d0914cba9e2d8b2624d93cca1'),
)
TRANSFORMS = {'sanitize-home-v1', 'sanitize-archive-v1', 'split-scan-markers-v1'}
# Public validation tooling is not a historical runtime dependency.
GENERATED_FILES = {'harness_lineage.py', 'control/test_harness_lineage.py',
                   'control/test_kit_references.py', 'control/test_exported_controls.py',
                   'control/exported_controls.py', 'control/test_errata.py',
                   'control/check-cronspec-selftest.py', 'control/cronspec_reference.py',
                   'control/cronspec_vixie.py', 'control/cronspec_naive_careful.py',
                   'rejudge_workflow.py', 'rejudge-networkless.sh',
                   'control/test_rejudge_workflow.py'}
OID = re.compile(r'[0-9a-f]{40}')


def git(root, *args, input=None):
    if input is None:
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    else:
        # Observed on macOS (Python 3.14.8): communicate() blocked writing stdin while
        # git blocked on a full stdout pipe. A file as stdin cannot fill.
        with tempfile.TemporaryFile() as request:
            request.write(input)
            request.seek(0)
            result = subprocess.run(['git', '-C', str(root), *args], stdin=request,
                                    capture_output=True)
    if result.returncode:
        raise ValueError('refusing lineage: Git lookup failed: ' + ' '.join(args[:2]))
    return result.stdout


def blob_id(payload):
    return hashlib.sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()


def encoded(manifest):
    return (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()


def allowlist(root):
    """Use the current kit boundary; never discover historical tree contents."""
    root = Path(root)
    if not (root / '.export-exclude').is_file():
        raise ValueError('refusing lineage: .export-exclude absent')
    names = {n for n in export.KIT_FILES if
             (n.endswith(('.py', '.sh')) and n not in GENERATED_FILES) or
             n in (*export.REVEALED_PROMPTS, 'TIER_PIN', 'calibration/orgsync.cleared')}
    names.update(export.REVEALED_JUDGES)
    names.update(export.HARNESS_METADATA_FILES)
    directories = (*export.KIT_DIRECTORIES, *export.REVEALED_TASK_DIRECTORIES)
    if (root / '.git').exists():
        leaves = git(root, 'ls-files', '-z', '--', *directories).decode().split('\0') if directories else []
    else:
        # Public kits have no Git index. Revalidate their declared leaves
        # against the same anchored directory boundary, without discovering files.
        leaves = json.loads((root / 'HARNESS-LINEAGE.json').read_text())['allowlist']
    for name in leaves:
        if any(name.startswith(directory) for directory in directories):
            path = root / name
            if '__pycache__' not in path.parts and not path.name.endswith('.pyc'):
                if path.is_symlink():
                    raise ValueError('refusing lineage: symlink in allowlist')
                names.add(name)
    for name in names:
        if Path(name).is_absolute() or '..' in Path(name).parts or name != Path(name).as_posix():
            raise ValueError('refusing lineage: invalid allowlisted path')
        if export.scan_export_text(name, source_path=True):
            raise ValueError('refusing lineage: identity in source path')
    export.refuse_sealed(names, export.sealed_paths(root), root)
    return sorted(names)


def scored_revision(row, stamp):
    """Legacy stamps omit commits: use stamp times or the October report.

    This is an attribution, never a claim that the stamp captured a checkout.
    New stamps may supply an explicit harness_commit instead.
    """
    if stamp.get('harness_commit'):
        ref = stamp['harness_commit']
        if not isinstance(ref, str) or not re.fullmatch(r'[0-9a-f]{7,40}', ref):
            raise ValueError('refusing lineage: invalid stamped harness commit')
        return ref, 'stamp.harness_commit'
    if row['edition'] == '2026-07':
        time = datetime.fromisoformat(stamp['date_utc'].replace('Z', '+00:00'))
        candidates = [ref for committed, ref in JULY_RUN_REVISIONS
                      if datetime.fromisoformat(committed.replace('Z', '+00:00')) < time]
        if not candidates:
            raise ValueError('refusing lineage: July stamp predates committed run.sh')
        return candidates[-1], "last committed run.sh before the run's stamp time; the working tree was not captured"
    if row['edition'] == '2026-10':
        if row['model'] == 'glm-5.3-flash':
            ref = '66bd8ec'
        elif row['rep'] in (2, 3):
            ref = '849c8cc'
        elif row['rep'] == 1:
            ref = '14f21dc' if row['tool'] == 'claude-code' else 'ec9574f'
        else:
            raise ValueError('refusing lineage: unknown scored repetition')
        return ref, 'editions/2026-10/REPORT.md, Runs and harness heads; commit not captured in stamp'
    raise ValueError('refusing lineage: no scored revision attribution')


def historical_files(root, revision, names):
    """Batch only literal allowlisted queries; no history/tree enumeration."""
    output = git(root, 'cat-file', '--batch',
                 input=b''.join(f'{revision}:{name}\n'.encode() for name in names))
    for name in names:
        header, output = output.split(b'\n', 1)
        if header.endswith(b' missing'):
            yield name, None, None
            continue
        oid, kind, size = header.split()
        size = int(size)
        payload, output = output[:size], output[size + 1:]
        if kind != b'blob' or blob_id(payload) != oid.decode():
            raise ValueError('refusing lineage: dependency is not a Git blob')
        yield name, oid.decode(), payload
    if output:
        raise ValueError('refusing lineage: unexpected Git batch output')


def publishable(name, payload, scratch, contents):
    """Screen originals for sealed copies BEFORE reviewed portability edits."""
    source, target = scratch / 'source', scratch / 'target'
    source.write_bytes(payload)
    export.refuse_sealed_content(source, contents)
    edits = []
    if name == 'score.py' and re.search(rb'(?m)^DEFAULT_PRICES = "/(?:Users|home)/', payload):
        payload = re.sub(rb'(?m)^DEFAULT_PRICES = "/(?:Users|home)/[^"\r\n]*"',
                         b'DEFAULT_PRICES = "<path-to>/prices.yaml"', payload)
        edits.append('sanitize-home-v1')
    if name in export.KIT_SANITIZE_FILES:
        source.write_bytes(payload)
        export.sanitize_archive(source, target)
        if target.read_bytes() != payload:
            payload = target.read_bytes()
            edits.append('sanitize-archive-v1')
    if name in ('redact.py', 'run_audit.py'):
        # Historical safety checks mention their own banned markers. Split
        # string literals / group the regex without changing their behavior.
        text = payload.decode()
        marker = 'base_' + 'instructions'
        updated = text.replace('"' + marker + '"', '"base_" + "instructions"')
        updated = updated.replace("'" + marker + "'", "'base_' + 'instructions'")
        updated = updated.replace('# ' + marker + ' is', '# The vendor instruction field is')
        updated = updated.replace('r"/' + 'Users/', 'r"/(?:Users)/')
        if updated != text:
            payload = updated.encode()
            edits.append('split-scan-markers-v1')
    target.write_bytes(payload)
    export.refuse_sealed_content(target, contents)
    hits = export.scan_export_file(target)
    export.refuse_identity_keys(target, name)
    if hits:
        raise ValueError(f'refusing lineage: unsafe historical dependency {name}: {hits}')
    return payload, edits


def build(root):
    root = Path(root).resolve()
    names = allowlist(root)
    contents = export.sealed_contents(root, export.sealed_paths(root))
    bundles, refs = [], set(COMPARISON_REVISIONS)
    for row in export.kit_rows(root, 'all'):
        stamp_path = root / row['bundle'] / 'stamp.json'
        stamp = json.loads(stamp_path.read_text())
        ref, basis = scored_revision(row, stamp)
        refs.add(ref)
        bundles.append(dict(bundle=row['bundle'], revision=ref, revision_source=basis,
                            stamp_sha256=export.sha256_file(stamp_path),
                            tool_version=stamp.get('tool_version') or 'not captured'))
    resolved = {ref: git(root, 'rev-parse', '--verify', ref + '^{commit}').decode().strip()
                for ref in sorted(refs)}
    revisions, blobs = {}, {}
    with tempfile.TemporaryDirectory(prefix='harness-lineage-') as tmp:
        for commit in sorted(set(resolved.values())):
            files, absent = {}, []
            for name, original, payload in historical_files(root, commit, names):
                if payload is None:
                    absent.append(name)
                    continue
                published, edits = publishable(name, payload, Path(tmp), contents)
                oid = blob_id(published)
                blobs[oid] = published
                files[name] = dict(blob=oid, sha256=hashlib.sha256(published).hexdigest(), transforms=edits)
                if not edits:
                    files[name]['source_blob'] = original
            if 'run.sh' not in files:
                raise ValueError('refusing lineage: scored/cited revision lacks run.sh')
            revisions[commit] = dict(aliases=sorted(ref for ref in resolved if resolved[ref] == commit),
                                     files=files, absent=absent)
    for bundle in bundles:
        bundle['revision'] = resolved[bundle['revision']]
        bundle['run_sh_blob'] = revisions[bundle['revision']]['files']['run.sh']['blob']
    manifest = dict(schema_version=1, allowlist=names, revisions=revisions,
                    bundles=sorted(bundles, key=lambda b: b['bundle']))
    return manifest, blobs


def public_files(root, selected_bundles=None, edition='all'):
    """Validate manifest paths and content addresses without private Git history.

    All-edition exports require exactly the selected bundle IDs. Single-edition
    exports may retain unselected entries from the shared manifest, including
    other editions, but every selected ID must occur exactly once. Duplicate IDs
    are always refused. Only selected stamps must exist and hash-match, so a
    single-edition kit can be checked without the other editions' run files.

    The exporter additionally runs its sealed-content and identity checks over
    every referenced blob before and after staging the public kit.
    """
    root = Path(root)
    manifest = json.loads((root / 'HARNESS-LINEAGE.json').read_text())
    names = allowlist(root)
    if manifest.get('schema_version') != 1 or manifest.get('allowlist') != names:
        raise ValueError('refusing lineage: allowlist/schema mismatch; regenerate')
    blobs = set()
    for commit, revision in manifest['revisions'].items():
        if not OID.fullmatch(commit):
            raise ValueError('refusing lineage: invalid provenance commit')
        files, absent = revision['files'], revision['absent']
        if set(files) & set(absent) or sorted([*files, *absent]) != names:
            raise ValueError('refusing lineage: dependency outside allowlist or unaccounted for')
        for name, entry in files.items():
            transforms = entry.get('transforms')
            if (not isinstance(transforms, list) or
                    any(not isinstance(t, str) or t not in TRANSFORMS for t in transforms)):
                raise ValueError('refusing lineage: unknown transformation')
            if transforms and 'source_blob' in entry:
                raise ValueError('refusing lineage: transformed source_blob must be absent')
            keys = ('blob',) if transforms else ('blob', 'source_blob')
            if not all(isinstance(entry.get(key), str) and OID.fullmatch(entry[key]) for key in keys):
                raise ValueError('refusing lineage: invalid blob address')
            path = 'harness/blobs/' + entry['blob']
            payload = (root / path).read_bytes()
            if blob_id(payload) != entry['blob'] or hashlib.sha256(payload).hexdigest() != entry['sha256']:
                raise ValueError('refusing lineage: blob digest mismatch')
            if not entry['transforms'] and entry['source_blob'] != entry['blob']:
                raise ValueError('refusing lineage: unrecorded transformation')
            blobs.add(path)
    bundles = {}
    for bundle in manifest['bundles']:
        bundle_id = bundle['bundle']
        if bundle_id in bundles:
            raise ValueError(f'refusing lineage: duplicate bundle: {bundle_id}')
        bundles[bundle_id] = bundle
        files = manifest['revisions'][bundle['revision']]['files']
        if bundle['run_sh_blob'] != files['run.sh']['blob']:
            raise ValueError('refusing lineage: bundle run.sh mismatch')
        path = Path(bundle['bundle'])
        if path.is_absolute() or '..' in path.parts or export.scan_export_text(str(path), source_path=True):
            raise ValueError('refusing lineage: invalid bundle path')
    selected = set(selected_bundles if selected_bundles is not None else
                   (row['bundle'] for row in export.kit_rows(root, edition)))
    missing = selected - bundles.keys()
    if missing:
        raise ValueError('refusing lineage: missing selected bundles: ' + ', '.join(sorted(missing)))
    unexpected = bundles.keys() - selected
    if edition == 'all' and unexpected:
        raise ValueError('refusing lineage: unexpected bundles: ' + ', '.join(sorted(unexpected)))
    for bundle_id in sorted(selected):
        if bundles[bundle_id].get('stamp_sha256') != export.sha256_file(root / bundle_id / 'stamp.json'):
            raise ValueError(f'refusing lineage: stamp_sha256 mismatch for bundle: {bundle_id}')
    export.refuse_sealed(blobs, export.sealed_paths(root), root)
    return sorted(blobs)


def generate(root, out, check=False):
    manifest, blobs = build(root)
    # macOS /tmp and /var are normal symlinked aliases for /private paths.
    out = Path(out).resolve()
    outputs = {'HARNESS-LINEAGE.json': encoded(manifest),
               **{'harness/blobs/' + oid: data for oid, data in blobs.items()}}
    directory = out / 'harness/blobs'
    if directory.is_symlink() or (out / 'harness').is_symlink():
        raise ValueError('refusing lineage: symlink output')
    stale = {p.name for p in directory.iterdir()} - blobs.keys() if directory.exists() else set()
    if stale:
        previous = json.loads((out / 'HARNESS-LINEAGE.json').read_text())
        owned = {entry['blob'] for revision in previous['revisions'].values()
                 for entry in revision['files'].values()}
        if stale - owned or any(not OID.fullmatch(oid) or
                                (directory / oid).is_symlink() or
                                blob_id((directory / oid).read_bytes()) != oid for oid in stale):
            raise ValueError('refusing lineage: unexpected files in blob directory')
        if check:
            raise ValueError('refusing lineage: stale output blobs')
    for name, payload in outputs.items():
        target = out / name
        if target.is_symlink() or target.parent.is_symlink():
            raise ValueError('refusing lineage: symlink output')
        if check:
            if not target.is_file() or target.read_bytes() != payload:
                raise ValueError('refusing lineage: stale output ' + name)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
    for oid in stale:
        (directory / oid).unlink()
    return len(manifest['bundles']), len(blobs)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('root', nargs='?', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    try:
        bundles, blobs = generate(args.root, args.out or args.root, args.check)
    except (OSError, ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f'HARNESS LINEAGE: {bundles} scored bundles; {blobs} screened content-addressed blobs')
    return 0


if __name__ == '__main__':
    sys.exit(main())
