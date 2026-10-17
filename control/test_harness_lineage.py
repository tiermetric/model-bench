#!/usr/bin/env python3
"""Offline lineage/export controls; no vendor CLI or network access."""
import hashlib
from collections import Counter
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import export
import harness_lineage as lineage


PRIVATE_REVISIONS_AVAILABLE = (ROOT / '.git').exists() and all(
    subprocess.run(['git', '-C', str(ROOT), 'cat-file', '-e', ref + '^{commit}'],
                   capture_output=True).returncode == 0
    for ref in json.loads((ROOT / 'HARNESS-LINEAGE.json').read_text())['revisions'])


class HarnessLineageTests(unittest.TestCase):
    def setUp(self):
        identity = 'public-kit-' + 'control-8291'
        mapping = patch.dict(os.environ, USER=identity, LOGNAME=identity,
                             EXPORT_IDENTITY_MAP=json.dumps({'Av' + 'ery Example': 'the operator'}))
        mapping.start(); self.addCleanup(mapping.stop)

    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ROOT / 'HARNESS-LINEAGE.json').read_text())

    @contextmanager
    def fixture_repo(self, files):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.export-exclude').write_text('sealed/\n')
            for name, payload in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture',
                            '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
                            # No detached auto-maintenance may write into .git during cleanup.
                            '-c', 'maintenance.auto=false', '-c', 'gc.auto=0',
                            'commit', '-qm', 'lineage fixture'], check=True)
            commit = lineage.git(root, 'rev-parse', 'HEAD').decode().strip()
            stamp = root / 'fixture/stamp.json'; stamp.parent.mkdir()
            stamp.write_text(json.dumps({'harness_commit': commit}))
            with patch.multiple(export, KIT_FILES=tuple(n for n in files if not n.startswith('sealed/')),
                                KIT_DIRECTORIES=(), REVEALED_PROMPTS=(), REVEALED_JUDGES=(),
                                REVEALED_TASK_DIRECTORIES=(), HARNESS_METADATA_FILES=()), \
                 patch('export.kit_rows', return_value=[dict(bundle='fixture', edition='fixture')]), \
                 patch.object(lineage, 'COMPARISON_REVISIONS', (commit,)):
                yield root

    @contextmanager
    def fixture_manifest(self, **changes):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.export-exclude').write_text('')
            payload = b'#!/bin/sh\nexit 0\n'
            oid = lineage.blob_id(payload)
            entry = dict(blob=oid, source_blob=oid, transforms=[],
                         sha256=hashlib.sha256(payload).hexdigest())
            entry.update(changes)
            stamp = root / 'fixture/stamp.json'
            stamp.parent.mkdir()
            stamp.write_bytes(b'{"harness_commit": "fixture"}\n')
            bundle = dict(bundle='fixture', revision='1' * 40, run_sh_blob=oid,
                          stamp_sha256=export.sha256_file(stamp))
            manifest = dict(schema_version=1, allowlist=['run.sh'], bundles=[bundle],
                            revisions={'1' * 40: dict(files={'run.sh': entry}, absent=[])})
            (root / 'harness/blobs').mkdir(parents=True)
            (root / 'harness/blobs' / oid).write_bytes(payload)
            (root / 'HARNESS-LINEAGE.json').write_bytes(lineage.encoded(manifest))
            with patch.object(lineage, 'allowlist', return_value=['run.sh']):
                yield root

    def test_git_batch_uses_file_stdin(self):
        for request in (b'abc\n', b''):
            with self.subTest(request=request):
                def run(command, **kwargs):
                    self.assertIsNotNone(kwargs.get('stdin'), 'batch input must use a real file')
                    self.assertTrue(stat.S_ISREG(os.fstat(kwargs['stdin'].fileno()).st_mode))
                    self.assertEqual(kwargs['stdin'].tell(), 0)
                    self.assertEqual(kwargs['stdin'].read(), request)
                    self.assertNotIn('input', kwargs)
                    self.assertTrue(kwargs['capture_output'])
                    self.assertEqual(command, ['git', '-C', str(ROOT), 'cat-file', '--batch'])
                    return subprocess.CompletedProcess(command, 0, stdout=b'output\x00\n')

                with patch.object(lineage.subprocess, 'run', side_effect=run) as execute:
                    self.assertEqual(lineage.git(ROOT, 'cat-file', '--batch', input=request),
                                     b'output\x00\n')
                execute.assert_called_once()

    def test_sealed_batch_uses_file_stdin(self):
        oid = b'a' * 40
        payload = b'historical sealed bytes\x00\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '.git').touch()

            def run(command, **kwargs):
                self.assertTrue(kwargs['check'])
                self.assertTrue(kwargs['capture_output'])
                if command[3] == 'rev-list':
                    return subprocess.CompletedProcess(command, 0, stdout=oid + b' sealed/key\n')
                self.assertEqual(command, ['git', '-C', str(root), 'cat-file', '--batch'])
                self.assertIsNotNone(kwargs.get('stdin'), 'batch input must use a real file')
                self.assertTrue(stat.S_ISREG(os.fstat(kwargs['stdin'].fileno()).st_mode))
                self.assertEqual(kwargs['stdin'].tell(), 0)
                self.assertEqual(kwargs['stdin'].read(), oid + b'\n')
                self.assertNotIn('input', kwargs)
                output = oid + b' blob ' + str(len(payload)).encode() + b'\n' + payload + b'\n'
                return subprocess.CompletedProcess(command, 0, stdout=output)

            with patch.object(export.subprocess, 'run', side_effect=run) as execute:
                self.assertEqual(export.sealed_contents(root, ['sealed/']), {payload})
            self.assertEqual(execute.call_count, 2)

    @unittest.skipUnless(hasattr(signal, 'SIGALRM'), 'needs SIGALRM for the hang guard')
    def test_large_real_git_batch_parses_every_blob(self):
        payload = b'blob with a NUL\x00and embedded\nnewline\n'
        names = ['run.sh'] * 6000
        request = b''.join(f'HEAD:{name}\n'.encode() for name in names)
        self.assertGreater(len(request), 64 * 1024)
        with self.fixture_repo({'run.sh': payload}) as root:
            def timed_out(signum, frame):
                self.fail('git cat-file --batch timed out')

            previous = signal.signal(signal.SIGALRM, timed_out)
            try:
                signal.alarm(20)
                result = list(lineage.historical_files(root, 'HEAD', names))
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, previous)
            self.assertEqual(result, [('run.sh', lineage.blob_id(payload), payload)] * len(names))

    @unittest.skipUnless(hasattr(signal, 'SIGALRM'), 'needs SIGALRM for the hang guard')
    def test_large_real_git_sealed_batch_reads_every_blob(self):
        payloads = {f'sealed/f{i:04d}.txt': f'sealed {i}\x00\n'.encode() for i in range(2000)}
        with self.fixture_repo(payloads) as root:
            # Only Git history can supply the payloads once the working copies are gone.
            shutil.rmtree(root / 'sealed')

            def timed_out(signum, frame):
                self.fail('git cat-file --batch timed out')

            previous = signal.signal(signal.SIGALRM, timed_out)
            try:
                signal.alarm(20)
                with patch.object(export.subprocess, 'run', wraps=subprocess.run) as execute:
                    contents = export.sealed_contents(root, ['sealed/'])
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, previous)
            batch = [c for c in execute.call_args_list if c.args[0][3] == 'cat-file']
            self.assertEqual(len(batch), 1)
            request = batch[0].kwargs['stdin']
            self.assertTrue(request.closed)
            self.assertEqual(contents, set(payloads.values()))

    def test_selected_bundles_are_bound_to_current_stamp_bytes(self):
        with self.fixture_manifest() as root:
            selected = ['fixture']
            self.assertTrue(lineage.public_files(root, selected))
            stamp = root / 'fixture/stamp.json'
            # The JSON value stays identical; the digest must bind exact bytes.
            stamp.write_bytes(stamp.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'stamp_sha256 mismatch.*fixture'):
                lineage.public_files(root, selected)

    def test_single_edition_checks_only_selected_stamps(self):
        with self.fixture_manifest() as root:
            path = root / 'HARNESS-LINEAGE.json'
            manifest = json.loads(path.read_text())
            other = dict(manifest['bundles'][0], bundle='editions/other/fixture',
                         stamp_sha256='0' * 64)
            manifest['bundles'].append(other)
            path.write_bytes(lineage.encoded(manifest))
            self.assertTrue(lineage.public_files(root, ['fixture'], '2026-07'))
            with self.assertRaisesRegex(ValueError, 'unexpected bundles'):
                lineage.public_files(root, ['fixture'], 'all')
            manifest['bundles'].append(dict(other))
            path.write_bytes(lineage.encoded(manifest))
            with self.assertRaisesRegex(ValueError, 'duplicate bundle'):
                lineage.public_files(root, ['fixture'], '2026-07')

    def test_transformed_entry_cannot_publish_source_blob(self):
        with self.fixture_manifest(transforms=['sanitize-home-v1']) as root:
            with self.assertRaisesRegex(ValueError, 'transformed source_blob'):
                lineage.public_files(root)

    def test_unrecorded_transformation_refuses(self):
        with self.fixture_manifest(source_blob='0' * 40) as root:
            with self.assertRaisesRegex(ValueError, 'unrecorded transformation'):
                lineage.public_files(root)

    def test_untransformed_entry_requires_source_blob(self):
        with self.fixture_manifest() as root:
            path = root / 'HARNESS-LINEAGE.json'
            manifest = json.loads(path.read_text())
            del manifest['revisions']['1' * 40]['files']['run.sh']['source_blob']
            path.write_bytes(lineage.encoded(manifest))
            with self.assertRaisesRegex(ValueError, 'invalid blob address'):
                lineage.public_files(root)

    def test_unknown_transform_refuses(self):
        with self.fixture_manifest(transforms=['unreviewed-transform']) as root:
            with self.assertRaisesRegex(ValueError, 'unknown transformation'):
                lineage.public_files(root)

    def test_july_bundle_counts_from_stamp_time_fixture(self):
        # Independent measurement: baseline 22:27:17–22:52:44Z (21);
        # cronspec 22:56:23Z–00:19:13Z (11 before 23:48:01Z, 10 after);
        # orgsync 00:54:01–04:17:05Z (21), all after 00:36:17Z.
        expected = {
            'a2b437ebb77d8e31b99160cef91c447e470c5688': 32,
            '77bcd13ae3d622efdd8baae1f97d0c3e999ba12f': 10,
            'd37daca54cb0bfac22679c3491761d818b7b090a': 21,
        }
        july = [b for b in self.manifest['bundles'] if not b['bundle'].startswith('editions/')]
        self.assertEqual(Counter(b['run_sh_blob'] for b in july), expected)
        for bundle in july:
            self.assertEqual(bundle['revision_source'],
                             "last committed run.sh before the run's stamp time; the working tree was not captured")

    def test_sealed_score_refuses_before_transform_with_post_check_bypassed(self):
        secret = b'DEFAULT_PRICES = "/' b'Users/x/private/prices.yaml"\n' + b'private answer row\n' * 80
        original_check = export.refuse_sealed_content
        def pre_check_only(path, contents):
            if path.name == 'source':
                original_check(path, contents)
        with self.fixture_repo({'run.sh': b'exit 0\n', 'score.py': secret, 'sealed/key': secret}) as root:
            with patch('export.refuse_sealed_content', side_effect=pre_check_only):
                with self.assertRaisesRegex(ValueError, 'sealed content'):
                    lineage.build(root)

    def test_publishable_refuses_historical_home_path(self):
        with self.fixture_repo({'run.sh': b'exit 0\n', 'safe.py': b'path = "/' b'Users/x/private"\n'}) as root:
            with self.assertRaisesRegex(ValueError, 'unsafe historical dependency'):
                lineage.build(root)

    def test_publishable_refuses_casefolded_identity_substrings(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'identity.*safe.py') as raised:
                lineage.publishable('safe.py', b'AV' + b'ERY EXAMPLEton\n', Path(tmp), set())
            self.assertNotIn('av' + 'ery', str(raised.exception).casefold())

    def test_score_home_transform_removes_entire_private_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload, edits = lineage.publishable('score.py',
                b'DEFAULT_PRICES = "/' b'Users/x/private/layout/prices.yaml"\n', Path(tmp), set())
            self.assertEqual(payload, b'DEFAULT_PRICES = "<path-to>/prices.yaml"\n')
            self.assertEqual(edits, ['sanitize-home-v1'])

    def test_allowlist_ignores_untracked_directory_leaves(self):
        with self.fixture_repo({'run.sh': b'exit 0\n', 'design/orgsync/known.py': b'pass\n'}) as root:
            (root / 'design/orgsync/.DS_Store').write_bytes(b'untracked')
            with patch('export.KIT_DIRECTORIES', ('design/orgsync/',)):
                names = lineage.allowlist(root)
            self.assertIn('design/orgsync/known.py', names)
            self.assertNotIn('design/orgsync/.DS_Store', names)

    def test_historical_git_blob_digest_is_verified(self):
        payload = b'exit 0\n'
        batch = b'0' * 40 + b' blob ' + str(len(payload)).encode() + b'\n' + payload + b'\n'
        with patch.object(lineage, 'git', return_value=batch):
            with self.assertRaisesRegex(ValueError, 'not a Git blob'):
                list(lineage.historical_files(ROOT, 'fixture', ['run.sh']))

    def test_cited_revision_requires_run_sh(self):
        with self.fixture_repo({'safe.py': b'pass\n'}) as root:
            with self.assertRaisesRegex(ValueError, 'lacks run.sh'):
                lineage.build(root)

    def test_every_scored_bundle_has_exact_run_sh_and_cli_receipt(self):
        actual = {b['bundle']: b for b in self.manifest['bundles']}
        rows = export.kit_rows(ROOT, 'all')
        self.assertEqual(set(actual), {r['bundle'] for r in rows})
        self.assertEqual(len(actual), 93)
        prefixes = set()
        for row in rows:
            entry = actual[row['bundle']]
            stamp_path = ROOT / row['bundle'] / 'stamp.json'
            stamp = json.loads(stamp_path.read_text())
            ref, basis = lineage.scored_revision(row, stamp)
            revision = self.manifest['revisions'][entry['revision']]
            self.assertIn(ref, revision['aliases'])
            self.assertEqual(entry['revision_source'], basis)
            self.assertEqual(entry['stamp_sha256'], export.sha256_file(stamp_path))
            self.assertEqual(entry['tool_version'], stamp.get('tool_version') or 'not captured')
            run = revision['files']['run.sh']
            self.assertEqual(entry['run_sh_blob'], run['blob'])
            self.assertEqual(run['source_blob'], run['blob'])
            self.assertEqual(run['transforms'], [])
            self.assertEqual(lineage.blob_id((ROOT / 'harness/blobs' / run['blob']).read_bytes()), run['blob'])
            prefixes.add(run['blob'][:7])
        self.assertEqual(prefixes, {'a2b437e', '77bcd13', 'd37daca', '8768731', '4294038', '5e22dd7', '501ed64'})

    def test_cited_comparisons_and_score_provenance(self):
        by_ref = {key: r for r in self.manifest['revisions'].values()
                  for ref in r['aliases'] for key in (ref, ref[:7])}
        self.assertTrue(set(lineage.COMPARISON_REVISIONS) <= by_ref.keys())
        self.assertEqual(by_ref['14f21dc']['files']['score.py']['blob'], 'ea60b6552b3eadad2608698061824f7ad2d07d36')
        self.assertNotIn('source_blob', by_ref['14f21dc']['files']['score.py'])
        self.assertTrue(by_ref['2d1a87b']['files']['score.py']['blob'].startswith('94094d3'))
        for name in ('run.sh', 'batch.sh', 'edition.sh', 'score.py', 'token_evidence.py', 'run_audit.py'):
            self.assertEqual(len({by_ref[r]['files'][name]['blob']
                                  for r in ('3a5c952', '849c8cc', '69e1843')}), 1)
        for name in ('run.sh', 'score.py', 'acceptance_orgsync.py', 'batch.sh', 'edition.sh',
                     'token_evidence.py', 'run_audit.py', 'preflight.sh', 'preflight_check.py', 'preflight_http.py'):
            self.assertEqual(by_ref['66bd8ec']['files'][name]['blob'], by_ref['1ad7ea8']['files'][name]['blob'])
        self.assertNotEqual(by_ref['14f21dc']['files']['run.sh']['blob'], by_ref['ec9574f']['files']['run.sh']['blob'])

    def test_every_source_is_allowlisted_and_every_blob_is_screened(self):
        names = lineage.allowlist(ROOT)
        self.assertEqual(self.manifest['allowlist'], names)
        with patch('export.sealed_contents', return_value=set()):
            planned, _ = export.kit_plan(ROOT, export.kit_rows(ROOT, 'all'), 'all')
        self.assertTrue(set(names) <= set(planned))
        exclusions = export.sealed_paths(ROOT)
        # Real sealed sources are never opened by controls. Synthetic sealed
        # fixtures below exercise the disclosure gate and private history.
        for revision in self.manifest['revisions'].values():
            self.assertEqual(sorted([*revision['files'], *revision['absent']]), names)
            export.refuse_sealed(revision['files'], exclusions, ROOT)
        files = lineage.public_files(ROOT)
        self.assertEqual(set(files), {p.relative_to(ROOT).as_posix() for p in (ROOT / 'harness/blobs').iterdir()})
        for name in files:
            path = ROOT / name
            self.assertEqual(export.scan_export_file(path), [], name)
            export.refuse_identity_keys(path, name)

    @unittest.skipUnless(PRIVATE_REVISIONS_AVAILABLE, 'private lineage revisions unavailable')
    def test_real_root_lineage_is_fresh_against_private_git(self):
        sanitize = export.sanitize_archive
        def reviewed_sanitize(source, target):
            # Pin both ends of the one reviewed historical identity edit.
            # Tests need no private name or identity mapping to reproduce it.
            if lineage.blob_id(source.read_bytes()) == '7342e0498ca50a754fd317770d2f2fbad8a4d272':
                target.write_bytes((ROOT / 'harness/blobs/45bceda6c8f85d6a44bd7da9cfbce07ad5768358').read_bytes())
            else:
                sanitize(source, target)
        # Real sealed sources remain unopened by controls; synthetic tests
        # cover screening. Every allowlisted historical dependency is rebuilt.
        with patch('export.sealed_contents', return_value=set()), \
             patch('export.sanitize_archive', side_effect=reviewed_sanitize):
            lineage.generate(ROOT, ROOT, check=True)

    def test_generator_is_deterministic_and_detects_stale_fixture_output(self):
        # Synthetic history also exercises determinism and stale-output refusal.
        with self.fixture_repo({'run.sh': b'#!/bin/sh\nexit 0\n'}) as root, \
             tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            lineage.generate(root, a)
            lineage.generate(root, b)
            first = {p.relative_to(a).as_posix(): p.read_bytes() for p in Path(a).rglob('*') if p.is_file()}
            second = {p.relative_to(b).as_posix(): p.read_bytes() for p in Path(b).rglob('*') if p.is_file()}
            self.assertEqual(first, second)
            lineage.generate(root, a, check=True)
            (Path(b) / 'HARNESS-LINEAGE.json').write_text('{}\n')
            with self.assertRaisesRegex(ValueError, 'stale output'):
                lineage.generate(root, b, check=True)

    def test_sealed_path_in_allowlist_refuses(self):
        with patch('export.KIT_FILES', (*export.KIT_FILES, 'acceptance_foreman.py')):
            with self.assertRaisesRegex(ValueError, 'sealed path'):
                lineage.allowlist(ROOT)

    def test_historical_sealed_copy_under_safe_name_refuses_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'; root.mkdir()
            (root / '.export-exclude').write_text('sealed/\n')
            (root / 'sealed').mkdir()
            secret = b''.join(f'Private answer row {i:03d}: {i * 7919:08d}.\n'.encode() for i in range(80))
            (root / 'sealed/key').write_bytes(secret)
            (root / 'run.sh').write_text('#!/bin/sh\nexit 0\n')
            (root / 'safe.py').write_bytes(secret.replace(b'00000000', b'00000001', 1))
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture',
                            '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
                            'commit', '-qm', 'sealed negative fixture'], check=True)
            commit = lineage.git(root, 'rev-parse', 'HEAD').decode().strip()
            # Remove the original from the working copy: the gate must still
            # find its historical fingerprint and reject the edited safe copy.
            (root / 'sealed/key').write_text('changed current sealed content')
            stamp = root / 'fixture/stamp.json'; stamp.parent.mkdir()
            stamp.write_text(json.dumps({'harness_commit': commit}))
            with patch.multiple(export, KIT_FILES=('run.sh', 'safe.py'), KIT_DIRECTORIES=(),
                                REVEALED_PROMPTS=(), REVEALED_JUDGES=(),
                                REVEALED_TASK_DIRECTORIES=(), HARNESS_METADATA_FILES=()), \
                 patch('export.kit_rows', return_value=[dict(bundle='fixture', edition='fixture')]), \
                 patch.object(lineage, 'COMPARISON_REVISIONS', (commit,)):
                out = Path(tmp) / 'output'
                with self.assertRaisesRegex(ValueError, 'sealed content'):
                    lineage.generate(root, out)
                self.assertFalse(out.exists())

    def test_export_revalidates_blobs_and_ignores_unlisted_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('HARNESS-LINEAGE.json', '.export-exclude'):
                shutil.copyfile(ROOT / name, root / name)
            for name in (*export.KIT_DIRECTORIES, *export.REVEALED_TASK_DIRECTORIES):
                shutil.copytree(ROOT / name, root / name)
            shutil.copytree(ROOT / 'harness/blobs', root / 'harness/blobs')
            selected = [b['bundle'] for b in self.manifest['bundles']]
            for bundle in selected:
                stamp = root / bundle / 'stamp.json'
                stamp.parent.mkdir(parents=True)
                shutil.copyfile(ROOT / bundle / 'stamp.json', stamp)
            extra = root / 'harness/blobs/unlisted'
            extra.write_text('not exported')
            self.assertNotIn(extra.relative_to(root).as_posix(), lineage.public_files(root, selected))
            entry = next(iter(self.manifest['revisions'].values()))['files']['run.sh']
            (root / 'harness/blobs' / entry['blob']).write_text('tampered')
            with self.assertRaisesRegex(ValueError, 'blob digest mismatch'):
                lineage.public_files(root, selected)

    def test_manifest_cannot_smuggle_nonallowlisted_source_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copyfile(ROOT / '.export-exclude', root / '.export-exclude')
            for name in (*export.KIT_DIRECTORIES, *export.REVEALED_TASK_DIRECTORIES):
                shutil.copytree(ROOT / name, root / name)
            manifest = json.loads(json.dumps(self.manifest))
            revision = next(iter(manifest['revisions'].values()))
            revision['files']['prompts/foreman.md'] = revision['files'].pop('run.sh')
            (root / 'HARNESS-LINEAGE.json').write_bytes(lineage.encoded(manifest))
            with self.assertRaisesRegex(ValueError, 'outside allowlist'):
                lineage.public_files(root)


if __name__ == '__main__':
    unittest.main()
