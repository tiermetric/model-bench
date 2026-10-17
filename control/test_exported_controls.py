"""Keep the public acceptance recipe executable and complete."""
from pathlib import Path
import contextlib
import io
import re
import unittest
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import exported_controls
import test_harness
from exported_controls import CONTROLS, DRY_RUNS

ROOT = Path(__file__).resolve().parents[1]


class ExportedCommandTests(unittest.TestCase):
    def test_readme_commands_match_acceptance_script(self):
        text = (ROOT / 'README.md').read_text()
        section = text.split('<!-- exported-controls:start -->', 1)[1].split('<!-- exported-controls:end -->', 1)[0]
        blocks = re.findall(r'```bash\n(.*?)```', section, re.S)
        documented = [line.split('  #', 1)[0] for block in blocks for line in block.splitlines()
                      if line and not line.startswith('#')]
        self.assertEqual(documented, [command for _, command in DRY_RUNS] + list(CONTROLS))
        # Every concrete README dry-run command is covered, even outside this block.
        commands = [line for block in re.findall(r'```bash\n(.*?)```', text, re.S)
                    for line in block.splitlines()]
        commands += re.findall(r'(?<!`)`([^`\n]+)`(?!`)', text)
        for command in commands:
            if re.search(r'\bDRY_RUN=1 \./(?:run|batch|preflight)\.sh\b', command):
                self.assertIn(command.split('  #', 1)[0], [actual for _, actual in DRY_RUNS])

    def test_dry_run_coverage_requires_the_full_command(self):
        original = (ROOT / 'README.md').read_text()
        for command in ('CUSTOM_PROVIDER=fixture ' + DRY_RUNS[0][1],
                        'env custom_provider=fixture ' + DRY_RUNS[0][1],
                        DRY_RUNS[0][1] + ' extra-argument'):
            with self.subTest(command=command), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / 'README.md').write_text(original + '\n`' + command + '`\n')
                with patch(__name__ + '.ROOT', root):
                    with self.assertRaises(AssertionError):
                        self.test_readme_commands_match_acceptance_script()

    def test_acceptance_success_with_real_dry_runs(self):
        # Reuse the small exported verification panel, including export safety
        # checks, rather than constructing a second kit-shaped test harness.
        fixture = test_harness.EditionOneVerificationTests()
        self.addCleanup(fixture.doCleanups)
        fixture.setUp()
        kit = fixture.verification_kit_fixture(runner_inputs=True)
        # Discovery here would recursively invoke this test. Exercise real
        # shipped controls on the reduced July panel and shell scripts instead.
        controls = ('python3 verify.py --require-no-skips',
                    'bash -n run.sh batch.sh preflight.sh edition.sh check-calibration-gate.sh')
        output = io.StringIO()
        with patch.object(exported_controls, 'ROOT', kit), \
             patch.object(exported_controls, 'CONTROLS', controls), \
             contextlib.redirect_stdout(output):
            # main supplies exit-97 vendor stubs and checks their call marker.
            # Keep runner_fixture, run, all DRY_RUNS and subprocesses real.
            exported_controls.main()
        receipt = output.getvalue()
        for _, command in DRY_RUNS:
            self.assertEqual(receipt.count('+ ' + command + '\n'), 1)
        for command in controls:
            self.assertIn('+ ' + command + '\n', receipt)
        self.assertIn('12 legs, 12 clean exits, 0 non-zero exits, 0 existing results skipped', receipt)
        self.assertIn('FAIL 0', receipt)
        self.assertIn('EXPORTED CONTROLS PASS: all dry paths, fresh batch, zero vendor invocations', receipt)

    def test_acceptance_refuses_git_directory_or_worktree_file(self):
        for directory in (False, True):
            with self.subTest(directory=directory), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                if directory:
                    (root / '.git').mkdir()
                else:
                    (root / '.git').write_text('gitdir: fixture\n')
                with patch.object(exported_controls, 'ROOT', root):
                    with self.assertRaisesRegex(SystemExit, 'without private Git history'):
                        exported_controls.main()

    def test_acceptance_refuses_vendor_marker_in_dry_runs_and_controls(self):
        for phase in ('dry', 'controls'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                def plant(command, directory, env):
                    Path(env['VENDOR_CALL_MARKER']).touch()
                    return 'command: fixture\n'
                with patch.object(exported_controls, 'ROOT', root), \
                     patch.object(exported_controls, 'DRY_RUNS', (('v11', 'fixture'),) if phase == 'dry' else ()), \
                     patch.object(exported_controls, 'CONTROLS', ('fixture',)), \
                     patch.object(exported_controls, 'runner_fixture'), \
                     patch.object(exported_controls, 'run', side_effect=plant):
                    with self.assertRaisesRegex(SystemExit, 'vendor binary invoked'):
                        exported_controls.main()

    def test_acceptance_refuses_incomplete_batch_summary(self):
        for output in ('command: fixture\n',
                       '12 legs, 11 clean exits, 1 non-zero exits, 0 existing results skipped',
                       '12 legs, 11 clean exits, 0 non-zero exits, 1 existing results skipped'):
            with self.subTest(output=output), tempfile.TemporaryDirectory() as tmp:
                with patch.object(exported_controls, 'ROOT', Path(tmp)), \
                     patch.object(exported_controls, 'DRY_RUNS', (('batch', './batch.sh'),)), \
                     patch.object(exported_controls, 'runner_fixture'), \
                     patch.object(exported_controls, 'run', return_value=output):
                    with self.assertRaisesRegex(SystemExit, 'complete fresh roster'):
                        exported_controls.main()
