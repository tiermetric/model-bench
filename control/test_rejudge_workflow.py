"""Public workflow: strict coverage, planted verdict, and environment scrubbing."""
import json
import csv
from collections import Counter
import os
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import rejudge_workflow as workflow
from control import test_harness


class WorkflowTests(unittest.TestCase):
    def test_docker_receipt_pins_launcher_image_files_and_zero_skips(self):
        receipt = json.loads((ROOT / 'REJUDGE-RECEIPT.json').read_text())['docker_launcher_receipt']
        script = (ROOT / 'rejudge-networkless.sh').read_text()
        self.assertEqual(re.findall(r'python@sha256:[0-9a-f]{64}', script),
                         [receipt['image_reference']])
        # Any pinned file hash change requires a new Docker measurement with a new started_at, not a hash edit.
        names = ('rejudge-networkless.sh', 'rejudge_workflow.py', 'verify.py',
                 'score.py', 'token_evidence.py', 'errata.py', 'redact.py',
                 'acceptance_test.py')
        self.assertEqual(set(receipt['file_sha256']), set(names))
        for name in names:
            with self.subTest(file=name):
                self.assertEqual(receipt['file_sha256'][name],
                                 hashlib.sha256((ROOT / name).read_bytes()).hexdigest())
        self.assertEqual(receipt['exit_code'], 0)
        self.assertEqual(receipt['checks'], [
            {'edition': Path(edition).name,
             'rejudge_summary': f'RE-JUDGE {count} bundles · PASS {count} · FAIL 0 · SKIP 0',
             'original_manifest_sha256': hashlib.sha256(
                 (ROOT / edition / 'MANIFEST.json').read_bytes()).hexdigest()}
            for edition, count in workflow.EDITIONS
        ])
        total = sum(count for _, count in workflow.EDITIONS)
        self.assertEqual(receipt['workflow_summary'],
                         f'REJUDGE WORKFLOW PASS: {total} outcomes; zero SKIPs; flipped verdict detected.')
        controls = receipt['controls']
        self.assertIs(controls['identity_and_filesystem']['non_root'], True)
        self.assertNotEqual(controls['identity_and_filesystem']['uid'], 0)
        self.assertIn('Network is unreachable', controls['network']['socket_connect_result'])

    def test_receipt_and_workflow_cover_manifest_outcomes(self):
        receipt = json.loads((ROOT / 'REJUDGE-RECEIPT.json').read_text())
        self.assertEqual(receipt['limitation'], workflow.LIMITATION)
        self.assertEqual(receipt['verifier_sha256'], hashlib.sha256((ROOT / 'verify.py').read_bytes()).hexdigest())
        self.assertEqual([check['edition'] for check in receipt['checks']],
                         [Path(edition).name for edition, _ in workflow.EDITIONS])
        for check, (edition, count) in zip(receipt['checks'], workflow.EDITIONS):
            manifest = ROOT / edition / 'MANIFEST.json'
            data = json.loads(manifest.read_text())
            expected = sum(map(len, data['roster'].values())) * sum(
                task.get('reps_run', task['reps']) for task in data['tasks'].values())
            self.assertEqual(count, expected)
            with (ROOT / edition / 'metrics.csv').open(newline='') as source:
                rows = list(csv.DictReader(source))
            outcomes = Counter()
            for row in rows:
                # July bundles live at root in the source; October is edition-local.
                bundle = ROOT / row['bundle']
                if not bundle.is_dir():
                    bundle = ROOT / edition / row['bundle']
                outcome = json.loads((bundle / 'result.json').read_text())['outcome']
                self.assertEqual(outcome, row['outcome'])
                outcomes[outcome] += 1
            self.assertEqual(dict(outcomes), check['recorded_outcomes'])
            self.assertEqual(sum(outcomes.values()), count)
            self.assertEqual(check['exit_code'], 0)
            self.assertEqual(check['verification_exit_code'], 0)
            self.assertEqual(check['original_manifest_sha256'], hashlib.sha256(manifest.read_bytes()).hexdigest())
            self.assertEqual(check['rejudge_summary'], f'RE-JUDGE {count} bundles · PASS {count} · FAIL 0 · SKIP 0')

    def test_receipt_records_workflow_commands(self):
        receipt = json.loads((ROOT / 'REJUDGE-RECEIPT.json').read_text())
        for check, (edition, count) in zip(receipt['checks'], workflow.EDITIONS):
            output = f'RE-JUDGE {count} bundles · PASS {count} · FAIL 0 · SKIP 0\n'
            with patch.object(workflow, 'run', return_value=subprocess.CompletedProcess([], 0, output)) as run:
                workflow.check_edition(ROOT, edition, count, Path('.'))
            commands = []
            for call in run.call_args_list:
                command = call.args[0]
                self.assertEqual(command[0], sys.executable)
                self.assertEqual(command[2], str(ROOT / 'verify.py'))
                # Receipt paths are portable; preserve every actual argument.
                commands.append(['python3', command[1], 'verify.py', *command[3:]])
            self.assertEqual([check['verification_command'], check['command']], commands)

    def test_complete_receipt_requires_both_strict_commands(self):
        output = 'RE-JUDGE 63 bundles · PASS 63 · FAIL 0 · SKIP 0\n'
        with patch.object(workflow, 'run', return_value=subprocess.CompletedProcess([], 0, output)) as run:
            workflow.check_edition(ROOT, 'editions/2026-07', 63, Path('.'))
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertTrue(all('--require-no-skips' in command for command in commands))
        self.assertNotIn('--rejudge', commands[0])
        self.assertIn('--rejudge', commands[1])

    def test_incomplete_skip_and_nonzero_receipts_fail(self):
        for status, output in ((0, ''), (0, 'RE-JUDGE 63 bundles · PASS 62 · FAIL 0 · SKIP 1'),
                               (0, 'RE-JUDGE 62 bundles · PASS 62 · FAIL 0 · SKIP 0'),
                               (1, 'RE-JUDGE 63 bundles · PASS 63 · FAIL 0 · SKIP 0')):
            with self.subTest(status=status, output=output):
                with patch.object(workflow, 'run', return_value=subprocess.CompletedProcess([], status, output)):
                    with self.assertRaises(ValueError):
                        workflow.check_edition(ROOT, 'editions/2026-07', 63, Path('.'))

    def test_networkless_launcher_never_pulls_and_mounts_readonly(self):
        script = (ROOT / 'rejudge-networkless.sh').read_text()
        for flag in ('--pull never', '--network none', '--read-only',
                     '--user "$(id -u):$(id -g)"', '--pids-limit 256', '--memory 2g',
                     '--tmpfs /tmp:rw,nosuid,nodev,size=512m',
                     '--mount "type=bind,src=$PWD,dst=/kit,readonly"',
                     'python3 -B rejudge_workflow.py'):
            self.assertIn(flag, script)
        self.assertRegex(script, r'python@sha256:[0-9a-f]{64}\s*\\')

    def test_caller_environment_is_scrubbed(self):
        with tempfile.TemporaryDirectory() as tmp:
            # A real child receives proxy settings and no caller configuration.
            # This checks environment scrubbing, not network isolation.
            code = ('import os; assert "WORKFLOW_SECRET_FIXTURE" not in os.environ; '
                    'assert "PYTHONPATH" not in os.environ; '
                    'assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:9"')
            with patch.dict(os.environ, WORKFLOW_SECRET_FIXTURE='synthetic', PYTHONPATH='untrusted'):
                result = workflow.run([sys.executable, '-B', '-c', code], ROOT, Path(tmp))
            self.assertEqual(result.returncode, 0, result.stdout)

    def test_real_flipped_verdict_control_preserves_source(self):
        case = test_harness.EditionOneVerificationTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        for name in ('verify.py', 'score.py', 'token_evidence.py', 'errata.py',
                     'redact.py', 'acceptance_test.py'):
            shutil.copyfile(ROOT / name, case.p / name)
        original = (case.bundle / 'result.json').read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            workflow.negative_control(case.p, Path(tmp))
        self.assertEqual((case.bundle / 'result.json').read_bytes(), original)
        # Kill the verdict comparison: the workflow must reject the control
        # even though the judge still runs and returns a valid verdict.
        verifier = case.p / 'verify.py'
        source = verifier.read_text()
        comparison = '"PASS" if verdict == recorded else "FAIL"'
        self.assertEqual(source.count(comparison), 1)
        verifier.write_text(source.replace(comparison, '"PASS"'))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'did not detect'):
                workflow.negative_control(case.p, Path(tmp))

    def test_environment_error_cannot_satisfy_negative_control(self):
        for output in ('PASS 0 · FAIL 1 · SKIP 0',
                       'recorded FAIL, re-judged PASS\nPASS 0 · FAIL 0 · SKIP 1'):
            with self.subTest(output=output), tempfile.TemporaryDirectory() as tmp:
                with patch.object(workflow, 'run', return_value=subprocess.CompletedProcess([], 1, output)):
                    with self.assertRaisesRegex(ValueError, 'did not detect'):
                        workflow.negative_control(ROOT, Path(tmp))


if __name__ == '__main__':
    unittest.main()
