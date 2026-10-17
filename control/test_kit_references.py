#!/usr/bin/env python3
"""Resolve documented repository references against export.py's exact kit plan."""
import fnmatch
from pathlib import Path
import posixpath
import re
import shlex
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import export

# These references describe private/sealed inputs, never public deliverables.
# Keep exact paths (or the explicit sealed directory boundary), with reasons.
PRIVATE_REFERENCES = {
    'internal/store/prices.yaml': 'private sibling pricing source',
    '.git': 'private Git history; published lineage replaces it',
    'publish/edition2-raw/': 'private raw export before redaction',
    'prompts/cutover.md': 'sealed frontier prompt',
    'prompts/foreman.md': 'sealed frontier prompt',
    'acceptance_cutover.py': 'sealed frontier judge',
    'acceptance_foreman.py': 'sealed frontier judge',
    'design/cutover/CONTROLS.md': 'sealed frontier design',
    'design/cutover/FIXTURES.md': 'sealed frontier design',
    'design/cutover/JUDGE_SPEC.md': 'sealed frontier design',
    'design/foreman/CONTROLS.md': 'sealed frontier design',
    'design/foreman/FIXTURES.md': 'sealed frontier design',
    '.preflight/pass.json': 'local paid-probe receipt, generated at runtime',
    'auth.json': 'local vendor credentials, never exported',
    'subagents/': 'local vendor session evidence, never exported',
    'settings.json': 'local vendor settings, never exported',
    'trust.json': 'local vendor trust state, never exported',
    '.auth.json.lock': 'local vendor credential lock, never exported',
    '.tool-config/<run-UUID>/': 'fresh runtime config directory, never exported',
    '.tool-config/<run-UUID>/data/opencode/opencode.db': 'runtime vendor usage database, never exported',
}
# Historical shorthand in published/dated text (R-2026-10-13-2).
# Keys are (repository-relative file, exact backticked token). These preserve
# historical wording; they do not assert that the shorthand is a shipped path.
HISTORICAL_REFERENCES = {
    ('PROTOCOL.md', '`FINDING.md`'): 'graduation artifact shorthand; archive/cronspec-v1-easy/FINDING.md',
    ('PROTOCOL.md', '`result.json`'): 'per-run artifact shorthand, relative to the cited run bundle',
    ('PROTOCOL.md', '`stamp.json`'): 'per-run provenance shorthand, relative to the cited run bundle',
    ('TASKS.md', '`config.json`'): '2026-10-13 archived orgsync bundle layout, not a root file',
    ('TASKS.md', '`orgsync/`'): '2026-10-13 archived orgsync bundle package, not a root directory',
    ('TASKS.md', '`submission/`'): '2026-10-13 archived run-bundle submission directory',
    ('TASKS.md', '`test_orgsync.py`'): '2026-10-13 archived submission test, not a root file',
    ('TASKS.md', '`tests/existing/`'): '2026-10-13 archived orgsync bundle tests, not a root directory',
    ('PROTOCOL.md', '`haiku/orgsync/rep1`'): '2026-07-23 quoted timeline and 2026-07-29 changelog 1.2: run id omits the tool prefix',
    ('PROTOCOL.md', '`haiku/orgsync/rep2`'): '2026-07-23 quoted timeline: run id omits the tool prefix',
    ('PROTOCOL.md', '`haiku/orgsync/rep3`'): '2026-07-23 quoted timeline: run id omits the tool prefix',
    ('TASKS.md', '`FINDING.md`'): '2026-07-22 ledger: basename refers to archive/cronspec-v1-easy/FINDING.md',
    ('TASKS.md', '`JUDGE_SPEC.md`'): '2026-07-29 ledger: basename refers to the sealed design/cutover/JUDGE_SPEC.md',
    ('editions/2026-10/REPORT.md', '`acceptance.log`'): 'Edition 2 REPORT.md, published 2026-10-13: historical judge-log citation; the kit ships verdicts in result.json',
}
# Task/version labels, numeric expressions, CLI switches and vendor-local
# path examples are not repository paths. Enumerate ambiguous labels explicitly.
NON_PATHS = {'duration/v1', 'cronspec/v1', 'cronspec/v2', 'orgsync/v1',
             'E[$/accepted]', '/login', '/reports/<yyyy-mm>', '<family>/v<N>', '.json',
             'A9.file_summary', 'TestA09Idempotent.test_second_run',
             'result.json.wall_seconds', 'tokens.api_calls',
             # Python/JSON member names, not file names.
             'redact.scan_banned', 'session_meta.cwd', 'stream.id',
             # Vendor catalog identifiers, not repository directories.
             'zai-coding-plan/glm-5.3', 'ollama-cloud/glm-5.3'}
# Literal sanitized example value, not a promised pricing snapshot location.
PLACEHOLDER_REFERENCES = {
    ('harness/README.md', '`<path-to>/prices.yaml`'): 'redacted historical DEFAULT_PRICES value',
}
PATH_TOKEN = re.compile(r'(?:[\w.*{},<>?+~-]+/)+[\w.*{},<>?+~-]*|[\w.*{},<>?+~-]+\.[A-Za-z][\w.-]*|\.[\w.-]+')


def references(text):
    for span in re.findall(r'(?<!`)`([^`\n]+)`(?!`)', text):
        if span in NON_PATHS or span.startswith('~/'):
            continue
        quoted = f'`{span}`'
        # Drop provenance revision prefixes but keep the repository path.
        span = re.sub(r'\b[0-9a-f]{7,40}:', '', span)
        # Tokens in prose-like code (machine descriptions, ratios) are ignored;
        # command arguments and bare paths share the same extraction.
        try:
            tokens = shlex.split(span)
        except ValueError:
            tokens = [span]
        for token in tokens:
            token = token.removeprefix('./')
            if token in NON_PATHS or token.startswith(('~/', '-', 'http:', 'https:')):
                continue
            # Extensionless public roots (for example the instrument pin)
            # also come directly from the export allowlist.
            if PATH_TOKEN.fullmatch(token) or token in export.KIT_FILES:
                yield quoted, token


def expanded(path):
    match = re.search(r'\{([^{}]+)\}', path)
    if match:
        for part in match[1].split(','):
            yield from expanded(path[:match.start()] + part + path[match.end():])
    else:
        yield path


def unresolved(document, text, files):
    missing = []
    for quoted, reference in references(text):
        if (document.as_posix(), quoted) in HISTORICAL_REFERENCES or \
                (document.as_posix(), quoted) in PLACEHOLDER_REFERENCES:
            continue
        if reference in PRIVATE_REFERENCES:
            continue
        for name in expanded(reference):
            # Placeholder paths describe a family of actual kit artifacts.
            pattern = re.sub(r'<[^>]+>', '*', name).rstrip('/')
            # Normalize explicit parent-relative links such as the shipped
            # harness README's ../HARNESS-LINEAGE.json, without filesystem reads.
            candidates = [posixpath.normpath(pattern),
                          posixpath.normpath(str(document.parent / pattern))]
            # Edition reports may cite evidence relative to their own bundles.
            # Root documents must resolve their stated paths directly.
            if document.parent != Path('.') and ('/' not in pattern or name.endswith('/')):
                prefix = '' if document.parent == Path('.') else document.parent.as_posix() + '/'
                candidates.append(prefix + '*/' + pattern)
            if not any(fnmatch.fnmatchcase(f, p) or fnmatch.fnmatchcase(f, p + '/*')
                       for f in files for p in candidates):
                missing.append(reference)
    return sorted(set(missing))


def validate(documents, files):
    missing = [f'{document}: {name}' for document, text in documents.items()
               for name in unresolved(document, text, files)]
    if missing:
        raise ValueError('Unshipped repository references:\n' + '\n'.join(missing))


class KitReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The file plan is the exporter's source of truth. Controls never read
        # real sealed inputs; the real export performs content screening.
        with patch('export.sealed_contents', return_value=set()):
            cls.files, _ = export.kit_plan(ROOT, export.kit_rows(ROOT, 'all'), 'all')

    def test_documented_references_ship_or_are_explicit_exceptions(self):
        documents = [ROOT / 'README.md', ROOT / 'harness/README.md',
                     ROOT / 'PROTOCOL.md', ROOT / 'TASKS.md',
                     *sorted((ROOT / 'editions').glob('*/REPORT*.md'))]
        validate({p.relative_to(ROOT): p.read_text() for p in documents}, self.files)

    def test_planted_missing_backticked_path_fails(self):
        read_text = Path.read_text
        for document in ('README.md', 'harness/README.md', 'PROTOCOL.md'):
            with self.subTest(document=document):
                def planted(path, *args, **kwargs):
                    text = read_text(path, *args, **kwargs)
                    if path == ROOT / document:
                        text += '\n`control/planted-missing.py`\n'
                    return text
                # Use the actual document scan so dropping either README from
                # its input list also breaks this positive control.
                with patch.object(Path, 'read_text', planted):
                    with self.assertRaisesRegex(ValueError, 'control/planted-missing.py'):
                        self.test_documented_references_ship_or_are_explicit_exceptions()

    def test_historical_exceptions_require_exact_file_and_backticked_token(self):
        for (filename, quoted), reason in HISTORICAL_REFERENCES.items():
            with self.subTest(file=filename, token=quoted):
                self.assertTrue(reason)
                self.assertEqual(unresolved(Path(filename), quoted, []), [])
                with self.assertRaisesRegex(ValueError, 'Unshipped repository references'):
                    validate({Path('control/unlisted.md'): quoted}, [])
                # Embedding a listed path in another code span is not allowlisted.
                self.assertTrue(unresolved(Path(filename), f'`cat {quoted[1:-1]}`', []))
                self.assertTrue(unresolved(Path(filename), f'`abc1234:{quoted[1:-1]}`', []))
                with self.assertRaisesRegex(ValueError, 'control/planted-missing.py'):
                    validate({Path(filename): quoted + '\n`control/planted-missing.py`'}, [])

    def test_root_document_cannot_resolve_a_wrong_directory_basename(self):
        for document in ('PROTOCOL.md', 'TASKS.md'):
            with self.subTest(document=document):
                self.assertEqual(unresolved(Path(document), '`planted.py`',
                                            ['archive/nested/planted.py']), ['planted.py'])
                self.assertEqual(unresolved(Path(document), '`planted.py`', ['planted.py']), [])

    def test_parent_relative_reference_resolves_from_document_directory(self):
        document = Path('harness/README.md')
        text = '`../HARNESS-LINEAGE.json`'
        self.assertEqual(unresolved(document, text, ['HARNESS-LINEAGE.json']), [])
        self.assertEqual(unresolved(document, text, ['archive/HARNESS-LINEAGE.json']),
                         ['../HARNESS-LINEAGE.json'])
        self.assertEqual(unresolved(document, '`../planted-missing.py`', self.files),
                         ['../planted-missing.py'])

    def test_commands_revision_paths_and_braces_are_checked(self):
        text = '`git diff abc1234 def5678 -- run.sh` `abc1234:score.py` `a/rep{1,2}/result.json`'
        self.assertEqual(unresolved(Path('TASKS.md'), text, ['run.sh', 'score.py', 'a/rep1/result.json']),
                         ['a/rep{1,2}/result.json'])
        self.assertEqual(unresolved(Path('PROTOCOL.md'), '`TIER_PIN` `.planted-hidden`', []),
                         ['.planted-hidden', 'TIER_PIN'])
        # An unrelated archive with the same basename cannot satisfy a report's
        # claim about its own run-bundle evidence.
        self.assertEqual(unresolved(Path('editions/2026-07/REPORT.md'), '`acceptance.log`',
                                    ['archive/old/acceptance.log']), ['acceptance.log'])


if __name__ == '__main__':
    unittest.main()
