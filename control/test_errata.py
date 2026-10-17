"""Synthetic errata fixtures only; no published correction text is authored."""
import copy
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import export
import errata as errata_module
import verify
from control import test_harness


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


def entry(label='fixture'):
    return dict(date='2026-10-14', claim=f'{label} claim',
                replacement=f'{label} replacement', evidence=['REPORT.md', 'https://example.org/issues/20'])


class ErrataTests(unittest.TestCase):
    def test_current_handoff_pins_match_newest_sidecars(self):
        text = (ROOT / 'ERRATA.md').read_text(encoding='utf-8')
        marker = 'Current handoff pins for publication outside the downloaded kit:'
        self.assertEqual(text.count(marker), 1)
        table = text.split(marker, 1)[1].strip().split('\n\n', 1)[0].splitlines()
        self.assertEqual(table[:2], [
            '| Edition | Errata revision | Newest-manifest SHA-256 |',
            '| --- | --- | --- |',
        ])
        pins = {}
        for row in table[2:]:
            match = re.fullmatch(
                r'\|\s*(\d{4}-\d{2})\s*\|\s*(\d+)\s*\|\s*`([0-9a-f]{64})`\s*\|', row)
            self.assertIsNotNone(match, f'malformed handoff pin: {row}')
            edition, revision, sha256 = match.groups()
            self.assertNotIn(edition, pins, f'duplicate handoff pin: {edition}')
            pins[edition] = (int(revision), sha256)

        expected = {}
        for edition in sorted((ROOT / 'editions').iterdir()):
            sidecars = list(edition.glob('MANIFEST.v*.json'))
            if not sidecars:
                continue
            newest = max(sidecars, key=lambda path: int(path.stem.split('.v')[1]))
            expected[edition.name] = (int(newest.stem.split('.v')[1]), digest(newest))
        self.assertEqual(pins, expected)

    def test_public_revision_claims_and_new_section_have_report_locators(self):
        private_ids = r'\bU\d{2,3}\b|\bD-\d+\b'
        for path in sorted((ROOT / 'editions').glob('*/ERRATA.v*.json')):
            if int(path.stem.split('.v')[1]) < 2:
                continue
            for entry in json.loads(path.read_text())['entries']:
                with self.subTest(sidecar=path.relative_to(ROOT), claim=entry['claim'].split(':')[0]):
                    self.assertNotRegex(entry['claim'], private_ids)
                    self.assertIn('REPORT.md', entry['claim'])
        section = (ROOT / 'ERRATA.md').read_text().split('## Dated corrections — 2026-10-16', 1)[1]
        self.assertNotRegex(section, private_ids)

    def setUp(self):
        self.case = test_harness.EditionOneVerificationTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = self.case.p
        self.ed = self.case.manifest_path.parent

    def revision(self, number=1, entries=None):
        previous = self.ed / ('MANIFEST.json' if number == 1 else f'MANIFEST.v{number-1}.json')
        errata = self.ed / f'ERRATA.v{number}.json'
        save(errata, dict(schema_version=1, edition='2026-07', revision=number,
                          entries=entries or [entry()]))
        manifest = copy.deepcopy(self.case.manifest)
        manifest.update(revision=number, predecessor=dict(path=previous.name, sha256=digest(previous)),
                        errata=dict(path=errata.name, sha256=digest(errata)))
        path = self.ed / f'MANIFEST.v{number}.json'
        save(path, manifest)
        return errata, path

    def check(self, status):
        report = verify.Report()
        getattr(verify, 'verify_errata', lambda *args: None)(self.ed, report)
        self.assertEqual([row[0] for row in report.rows], [status], report.rows)
        return report

    def test_versioned_append_preserves_originals(self):
        from errata import append_errata, load_errata
        (self.ed / 'REPORT.md').write_text('unchanged fixture report\n')
        originals = {p.name: p.read_bytes() for p in self.ed.iterdir() if p.is_file()}
        append_errata(self.ed, [entry()])
        first = {p.name: p.read_bytes() for p in self.ed.glob('*.v1.json')}
        append_errata(self.ed, [entry('second')])
        self.assertEqual(load_errata(self.ed)[1], [entry(), entry('second')])
        # Verification must not depend on the reader's login name.
        with patch('getpass.getuser', return_value='fixture'):
            self.check('PASS')
        for name, data in {**originals, **first}.items():
            self.assertEqual((self.ed / name).read_bytes(), data)
        successor = json.loads((self.ed / 'MANIFEST.v2.json').read_text())
        self.assertEqual(successor['predecessor'], dict(path='MANIFEST.v1.json',
                                                       sha256=digest(self.ed / 'MANIFEST.v1.json')))

    def test_missing_errata_fails(self):
        errata, _ = self.revision()
        errata.unlink()
        self.check('FAIL')

    def test_tampered_errata_fails(self):
        errata, _ = self.revision()
        errata.write_text(errata.read_text() + ' ')
        self.check('FAIL')

    def test_schema_invalid_even_with_matching_hash(self):
        for mutation in ('date', 'claim', 'replacement', 'evidence', 'schema_version', 'edition', 'revision'):
            with self.subTest(field=mutation):
                errata, manifest = self.revision()
                data = json.loads(errata.read_text())
                if mutation in ('date', 'claim', 'replacement', 'evidence'):
                    data['entries'][0][mutation] = 'invalid' if mutation == 'date' else []
                else:
                    data[mutation] = 'invalid'
                save(errata, data)
                m = json.loads(manifest.read_text()); m['errata']['sha256'] = digest(errata); save(manifest, m)
                self.check('FAIL')

    def test_omitted_manifest_errata_reference_fails(self):
        _, path = self.revision()
        data = json.loads(path.read_text()); data.pop('errata'); save(path, data)
        self.check('FAIL')

    def test_orphan_errata_fails(self):
        _, path = self.revision()
        path.unlink()
        self.check('FAIL')

    def test_predecessor_tamper_or_missing_fails(self):
        self.revision()
        for absent in (False, True):
            with self.subTest(absent=absent):
                if absent:
                    self.case.manifest_path.unlink()
                else:
                    self.case.manifest_path.write_text(self.case.manifest_path.read_text() + ' ')
                self.check('FAIL')

    def test_revision_gap_and_rewritten_history_fail(self):
        first, _ = self.revision()
        second, manifest = self.revision(2, [entry('changed'), entry('second')])
        self.check('FAIL')
        save(second, dict(schema_version=1, edition='2026-07', revision=2,
                          entries=[entry(), entry('second')]))
        data = json.loads(manifest.read_text()); data['errata']['sha256'] = digest(second); save(manifest, data)
        self.check('PASS')
        first.unlink()
        self.check('FAIL')

    def test_successor_cannot_change_original_manifest(self):
        _, path = self.revision()
        data = json.loads(path.read_text()); data['scored_models'] = 99; save(path, data)
        self.check('FAIL')

    def test_unsafe_errata_paths_and_symlinks_fail(self):
        errata, path = self.revision()
        original = json.loads(path.read_text())
        for value in ('../ERRATA.v1.json', '/tmp/ERRATA.v1.json'):
            data = copy.deepcopy(original); data['errata']['path'] = value; save(path, data)
            self.check('FAIL')
        save(path, original)
        external = self.root / 'external.json'; external.write_bytes(errata.read_bytes())
        errata.unlink(); errata.symlink_to(external)
        self.check('FAIL')

    def test_export_includes_every_revision_byte_identically(self):
        data, _ = self.case.public_kit_fixture()
        errata, manifest = self.revision()
        self.revision(2, [entry(), entry('second')])
        out = self.root / 'publish'
        export.export_kit(self.root, out, data, '2026-07')
        for path in self.ed.glob('*.v*.json'):
            target = out / path.relative_to(self.root)
            self.assertTrue(target.is_file(), target)
            self.assertEqual(target.read_bytes(), path.read_bytes())
        self.assertTrue((out / 'errata.py').is_file())
        result = subprocess.run([sys.executable, 'verify.py'], cwd=out, text=True, capture_output=True)
        self.assertIn('[PASS] report errata integrity', result.stdout)

    def test_export_omission_control_rejects_missing_sidecar(self):
        data, _ = self.case.public_kit_fixture()
        self.revision()
        self.revision(2, [entry(), entry('second')])
        original = export.kit_plan
        for omitted in (('ERRATA.v1.json',), ('ERRATA.v2.json', 'MANIFEST.v2.json'),
                        ('ERRATA.v1.json', 'MANIFEST.v1.json', 'ERRATA.v2.json', 'MANIFEST.v2.json')):
            with self.subTest(omitted=omitted):
                def omit(*args):
                    files, excluded = original(*args)
                    return {p for p in files if Path(p).name not in omitted}, excluded
                with patch('export.kit_plan', side_effect=omit):
                    with self.assertRaisesRegex(ValueError, 'errata'):
                        export.export_kit(self.root, self.root / 'publish', data, '2026-07')
                self.assertFalse((self.root / 'publish').exists())

    def test_identity_scan_and_local_evidence_refused(self):
        from errata import append_errata
        data, _ = self.case.public_kit_fixture()
        for field in ('claim', 'replacement', 'evidence'):
            for leak in ('/' + 'Users/fixture/private', getpass.getuser(), '/private/tmp/receipt'):
                with self.subTest(field=field, leak=leak):
                    bad = entry(); bad[field] = [leak] if field == 'evidence' else leak
                    with self.assertRaises(ValueError):
                        append_errata(self.ed, [bad])
                    self.assertEqual(list(self.ed.glob('*.v*.json')), [])
                    errata, manifest = self.revision(entries=[bad])
                    with self.assertRaises(ValueError):
                        export.export_kit(self.root, self.root / 'publish', data, '2026-07', dry_run=True)
                    errata.unlink(); manifest.unlink()
        errata, _ = self.revision()
        bad = entry(); bad['replacement'] = '/' + 'Users/fixture/private'
        save(errata, dict(entries=[bad]))
        checked = subprocess.run([sys.executable, str(ROOT / 'redact.py'), '--check',
                                  '--out', str(self.ed)], text=True, capture_output=True)
        self.assertEqual(checked.returncode, 1)
        self.assertIn('LEAK ERRATA.v1.json', checked.stdout)

    def test_verifier_cli_rejects_errata_tamper_for_both_editions(self):
        data, _ = self.case.combined_kit_fixture()
        for edition in ('2026-07', '2026-10'):
            ed = self.root / 'editions' / edition
            manifest = json.loads((ed / 'MANIFEST.json').read_text())
            errata = ed / 'ERRATA.v1.json'
            save(errata, dict(schema_version=1, edition=edition, revision=1, entries=[entry()]))
            manifest.update(revision=1, predecessor=dict(path='MANIFEST.json', sha256=digest(ed / 'MANIFEST.json')),
                            errata=dict(path=errata.name, sha256=digest(errata)))
            save(ed / 'MANIFEST.v1.json', manifest)
        out = self.root / 'publish'
        export.export_kit(self.root, out, data, 'all')
        for edition in ('2026-07', '2026-10'):
            with self.subTest(edition=edition):
                errata = out / 'editions' / edition / 'ERRATA.v1.json'
                self.assertTrue(errata.is_file())
                original = errata.read_bytes(); errata.write_bytes(original + b' ')
                result = subprocess.run([sys.executable, 'verify.py', f'editions/{edition}'],
                                        cwd=out, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('[FAIL] report errata integrity', result.stdout)
                errata.write_bytes(original)

    def test_truncation_pin_and_unpinned_revision_receipts(self):
        for revision, removed in ((1, (2,)), (0, (1, 2))):
            with self.subTest(revision=revision):
                for path in self.ed.glob('*.v*.json'):
                    path.unlink()
                self.revision()
                _, newest = self.revision(2, [entry(), entry('second')])
                pin = f'2026-07=2:{digest(newest)}'
                for number in removed:
                    for prefix in ('ERRATA', 'MANIFEST'):
                        (self.ed / f'{prefix}.v{number}.json').unlink()
                latest = self.ed / ('MANIFEST.json' if revision == 0 else f'MANIFEST.v{revision}.json')
                unpinned = self.case.invoke()
                self.assertEqual(unpinned.returncode, 0, unpinned.stdout + unpinned.stderr)
                self.assertIn(f'errata revision {revision}, newest manifest sha256 {digest(latest)}',
                              unpinned.stdout)
                pinned = self.case.invoke('--expect', pin)
                self.assertNotEqual(pinned.returncode, 0)
                self.assertIn('[FAIL] report errata pin', pinned.stdout)

    def test_intact_chain_accepts_pin_and_prints_newest_hash(self):
        self.revision()
        _, newest = self.revision(2, [entry(), entry('second')])
        result = self.case.invoke('--expect', f'2026-07=2:{digest(newest)}')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f'errata revision 2, newest manifest sha256 {digest(newest)}', result.stdout)

    def test_pin_checks_revision_hash_and_requested_edition(self):
        self.revision()
        latest = self.ed / 'MANIFEST.v1.json'
        for pin in (f'2026-07=0:{digest(latest)}', f'2026-07=1:{"0" * 64}',
                    f'absent=0:{digest(latest)}'):
            with self.subTest(pin=pin):
                result = self.case.invoke('--expect', pin)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('[FAIL] report errata pin', result.stdout)
        for pin in ('2026-07=1:no-hash', f'2026-07=-1:{digest(latest)}'):
            with self.subTest(malformed=pin):
                result = self.case.invoke('--expect', pin)
                self.assertEqual(result.returncode, 2)
                self.assertIn('invalid errata pin', result.stderr)

    def test_local_paths_and_nonpublic_schemes_append_export_refusal(self):
        data, _ = self.case.public_kit_fixture()
        for ref in (r'\rooted\receipt', r'\\host\share\receipt', '~someone/receipt',
                    r'~someone\receipt', r'C:\receipt', 'C:/receipt',
                    r'public reference \\host\share\receipt',
                    '[receipt](/etc/company/internal-receipt)', '(//server/share/receipt)',
                    '`file:///var/private/x`', r'(C:\work\x)', 'see ftp://host/receipt',
                    '"/var/private/receipt"', 'see:/etc/receipt', 'see,/etc/receipt',
                    'see./etc/receipt', 'see;/etc/receipt', 'see!/etc/receipt',
                    'see?/etc/receipt', 'see=/etc/receipt', '`~/receipt`',
                    'see_/etc/receipt', 'see//server/share/receipt',
                    '(~someone/receipt)', r'"\\server\share\receipt"',
                    '[receipt](http://host/receipt)', '(mailto:private@example.com)',
                    'see:custom+scheme.v1:receipt', 'https:receipt'):
            for action in ('append', 'export dry-run', 'export write'):
                with self.subTest(ref=ref, action=action):
                    shutil.rmtree(self.root / 'publish', ignore_errors=True)
                    for path in self.ed.glob('*.v*.json'):
                        path.unlink()
                    bad = entry(); bad['evidence'] = [ref]
                    if action == 'append':
                        with self.assertRaisesRegex(ValueError, 'local paths'):
                            errata_module.append_errata(self.ed, [bad])
                        self.assertEqual(list(self.ed.glob('*.v*.json')), [])
                    else:
                        self.revision(entries=[bad])
                        with self.assertRaisesRegex(ValueError, 'local paths'):
                            export.export_kit(self.root, self.root / 'publish', data, '2026-07',
                                              dry_run=action == 'export dry-run')
                        self.assertFalse((self.root / 'publish').exists())

    def test_public_https_evidence_passes_append_and_export(self):
        data, _ = self.case.public_kit_fixture()
        addition = entry()
        addition['evidence'] = ['https://example.org/public/receipt']
        errata_module.append_errata(self.ed, [addition])
        self.assertEqual(errata_module.load_errata(self.ed)[1], [addition])
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                export.export_kit(self.root, self.root / 'publish', data, '2026-07', dry_run=dry_run)

    def test_append_refuses_date_before_newest_existing_entry(self):
        newest, older = entry('newest'), entry('older')
        newest['date'], older['date'] = '2026-10-15', '2026-10-13'
        self.revision(entries=[newest, older])
        originals = {p.name: p.read_bytes() for p in self.ed.iterdir() if p.is_file()}
        with patch.object(errata_module, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 15, tzinfo=timezone.utc)
            with self.assertRaisesRegex(ValueError, 'precedes newest existing entry'):
                errata_module.append_errata(self.ed, [entry('backdated')])
            clock.now.assert_called_once_with(timezone.utc)
        self.assertEqual({p.name: p.read_bytes() for p in self.ed.iterdir() if p.is_file()}, originals)

    def test_append_refuses_date_after_today_utc(self):
        addition = entry('future')
        addition['date'] = '2026-10-16'
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.revision()
                originals = {p.name: p.read_bytes() for p in self.ed.iterdir() if p.is_file()}
                with patch.object(errata_module, 'datetime') as clock:
                    clock.now.return_value = datetime(2026, 10, 15, tzinfo=timezone.utc)
                    with self.assertRaisesRegex(ValueError, 'after today \\(UTC\\)'):
                        errata_module.append_errata(self.ed, [addition])
                    clock.now.assert_called_once_with(timezone.utc)
                self.assertEqual({p.name: p.read_bytes() for p in self.ed.iterdir() if p.is_file()}, originals)

    def test_append_accepts_same_day_as_newest_entry_and_today_utc(self):
        first, second = entry('first'), entry('second')
        first['date'] = second['date'] = '2026-10-15'
        with patch.object(errata_module, 'datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 15, tzinfo=timezone.utc)
            errata_module.append_errata(self.ed, [first])
            errata_module.append_errata(self.ed, [second])
            self.assertEqual(clock.now.call_count, 2)
            clock.now.assert_called_with(timezone.utc)
        self.assertEqual(errata_module.load_errata(self.ed)[1], [first, second])

    def guard_fixture(self, guard):
        for path in self.ed.glob('*.v*.json'):
            path.unlink()
        save(self.ed / 'MANIFEST.json', self.case.manifest)
        sidecar, manifest_path = self.revision()
        if guard == 'gap':
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            document = json.loads(sidecar.read_text(encoding='utf-8'))
            sidecar.unlink(); manifest_path.unlink()
            document['revision'] = 2
            save(self.ed / 'ERRATA.v2.json', document)
            manifest.update(revision=2, errata=dict(path='ERRATA.v2.json',
                                                   sha256=digest(self.ed / 'ERRATA.v2.json')))
            # The predecessor is valid MANIFEST.json: only the gap guard catches this.
            save(self.ed / 'MANIFEST.v2.json', manifest)
        elif guard == 'filename':
            save(self.ed / 'MANIFEST.v0.json', {})
        elif guard == 'original':
            base = dict(self.case.manifest, revision=1)
            save(self.ed / 'MANIFEST.json', base)
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['predecessor']['sha256'] = digest(self.ed / 'MANIFEST.json')
            save(manifest_path, manifest)
        elif guard == 'manifest_revision':
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['revision'] = '1'
            save(manifest_path, manifest)
        elif guard in ('schema_bool', 'revision_bool'):
            document = json.loads(sidecar.read_text(encoding='utf-8'))
            document['schema_version' if guard == 'schema_bool' else 'revision'] = True
            save(sidecar, document)
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['errata']['sha256'] = digest(sidecar)
            save(manifest_path, manifest)
        elif guard == 'growth':
            self.revision(2, [entry()])
        elif guard == 'edition':
            base = dict(self.case.manifest, edition='other-edition')
            save(self.ed / 'MANIFEST.json', base)
            document = json.loads(sidecar.read_text(encoding='utf-8'))
            document['edition'] = base['edition']
            save(sidecar, document)
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest['edition'] = base['edition']
            manifest['predecessor']['sha256'] = digest(self.ed / 'MANIFEST.json')
            manifest['errata']['sha256'] = digest(sidecar)
            save(manifest_path, manifest)

    GUARDS = {
        'gap': ("if sorted(revisions) != list(range(1, len(revisions) + 1)):",
                'if False:', 'revision gap'),
        'filename': ("if not match:\n            raise ValueError(f'errata invalid revision filename: {name}')",
                     'if not match:\n            continue', 'invalid revision filename'),
        'original': ("if {'revision', 'predecessor', 'errata'} & base.keys():",
                     'if False:', 'original manifest must remain unchanged'),
        'manifest_revision': ("or type(manifest.get('revision')) is not int or manifest['revision'] != number",
                              '', 'successor must preserve original manifest fields'),
        'schema_bool': ("or type(data['schema_version']) is not int ", '',
                        'document schema/edition/revision invalid'),
        'revision_bool': ("or type(data['revision']) is not int ", '',
                          'document schema/edition/revision invalid'),
        'growth': (" or len(data['entries']) <= len(entries)", '',
                   'revisions must append entries'),
        'edition': ("if base.get('edition') != root.name:", 'if False:',
                    'edition directory differs from original manifest'),
    }

    def assert_guard_receipt(self, module, guard):
        with self.assertRaisesRegex(ValueError, self.GUARDS[guard][2]):
            module.load_errata(self.ed)

    def test_each_guard_has_an_isolated_receipt(self):
        for guard in self.GUARDS:
            with self.subTest(guard=guard):
                self.guard_fixture(guard)
                self.assert_guard_receipt(errata_module, guard)

    def test_guard_deletion_controls_make_the_same_receipts_fail(self):
        source = (ROOT / 'errata.py').read_text(encoding='utf-8')
        for guard, (before, after, _) in self.GUARDS.items():
            with self.subTest(guard=guard):
                self.guard_fixture(guard)
                self.assertEqual(source.count(before), 1)
                mutant = types.ModuleType('errata_mutant')
                exec(compile(source.replace(before, after), '<errata-mutant>', 'exec'), mutant.__dict__)
                with self.assertRaises(AssertionError):
                    self.assert_guard_receipt(mutant, guard)

    def test_root_cli_checks_all_editions_and_repeatable_pins(self):
        out = self.case.verification_kit_fixture()
        pins = []
        for edition in ('2026-07', '2026-10'):
            pins.extend(['--expect', f'{edition}=0:{digest(out / "editions" / edition / "MANIFEST.json")}'])
        result = subprocess.run([sys.executable, 'verify.py', *pins], cwd=out,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for edition in ('2026-07', '2026-10'):
            self.assertIn(f'{edition}: errata revision 0, newest manifest sha256 ', result.stdout)
        # Even a directory beyond the two currently published editions is checked.
        extra = out / 'editions' / 'future'; extra.mkdir()
        save(extra / 'MANIFEST.json', dict(edition='future'))
        save(extra / 'MANIFEST.v0.json', {})
        result = subprocess.run([sys.executable, 'verify.py'], cwd=out, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('invalid revision filename: MANIFEST.v0.json', result.stdout)

    def test_relative_edition_path_and_cli_from_inside_edition(self):
        out = self.case.verification_kit_fixture()
        ed = out / 'editions' / '2026-10'
        errata_module.append_errata(ed, [entry()])
        previous_cwd = Path.cwd()
        try:
            os.chdir(ed)
            with self.subTest(operation='load'):
                self.assertEqual(errata_module.load_errata('.')[1], [entry()])
            with self.subTest(operation='append'):
                errata_module.append_errata('.', [entry('second')])
                self.assertEqual(errata_module.load_errata('.')[1], [entry(), entry('second')])
            with self.subTest(operation='CLI'):
                result = subprocess.run([sys.executable, str(out / 'verify.py'), '.'], cwd=ed,
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('errata revision ', result.stdout)
                self.assertIn('[PASS] report errata integrity', result.stdout)
        finally:
            os.chdir(previous_cwd)

    def test_root_cli_refuses_errata_in_october_and_every_other_directory(self):
        out = self.case.verification_kit_fixture()
        for edition in ('2026-10', 'future'):
            with self.subTest(edition=edition):
                ed = out / 'editions' / edition
                if not ed.exists():
                    ed.mkdir()
                    save(ed / 'MANIFEST.json', dict(edition=edition))
                invalid = ed / 'MANIFEST.v0.json'
                save(invalid, {})
                result = subprocess.run([sys.executable, 'verify.py'], cwd=out,
                                        text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('[FAIL] report errata integrity', result.stdout)
                self.assertIn('invalid revision filename: MANIFEST.v0.json', result.stdout)
                invalid.unlink()

    def test_append_write_failures_clean_up_both_files(self):
        real_open = Path.open
        for failing in ('ERRATA', 'MANIFEST'):
            for failure in (OSError, UnicodeError, RuntimeError):
                with self.subTest(file=failing, exception=failure.__name__):
                    for path in self.ed.glob('*.v*.json'):
                        path.unlink()
                    def interrupted(path, mode='r', *args, **kwargs):
                        target = real_open(path, mode, *args, **kwargs)
                        if path.name == f'{failing}.v1.json' and mode == 'x':
                            class InterruptedWrite:
                                def __enter__(self):
                                    return self
                                def write(self, text):
                                    target.write('{partial')
                                    raise failure('injected write failure')
                                def __exit__(self, *exc):
                                    target.close()
                            return InterruptedWrite()
                        return target
                    with patch.object(Path, 'open', interrupted), self.assertRaises(failure):
                        errata_module.append_errata(self.ed, [entry()])
                    self.assertEqual(list(self.ed.glob('*.v*.json')), [])

    def test_exclusive_write_refusal_preserves_existing_sidecars(self):
        for prefix in ('ERRATA', 'MANIFEST'):
            with self.subTest(existing=prefix):
                path = self.ed / f'{prefix}.v1.json'
                path.write_bytes(b'preexisting bytes')
                with patch.object(errata_module, 'load_errata', return_value=([], [])):
                    with self.assertRaises(FileExistsError):
                        errata_module.append_errata(self.ed, [entry()])
                self.assertEqual(path.read_bytes(), b'preexisting bytes')
                self.assertEqual(list(self.ed.glob('*.v*.json')), [path])
                path.unlink()

    def test_append_uses_utf8_for_non_ascii_entries(self):
        real_open = Path.open
        def require_encoding(path, mode='r', *args, **kwargs):
            if 'b' not in mode:
                self.assertEqual(kwargs.get('encoding'), 'utf-8', str(path))
            return real_open(path, mode, *args, **kwargs)
        addition = entry('caf\u00e9 \u65e5\u672c\u8a9e')
        with patch.object(Path, 'open', require_encoding):
            errata_module.append_errata(self.ed, [addition])
            self.assertEqual(errata_module.load_errata(self.ed)[1], [addition])
        self.assertIn('caf\u00e9 \u65e5\u672c\u8a9e', (self.ed / 'ERRATA.v1.json').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
