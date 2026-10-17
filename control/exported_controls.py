#!/usr/bin/env python3
"""Offline public-kit acceptance; run from a real export, stdlib only."""
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
# README's Exported controls blocks are checked against these exact commands.
DRY_RUNS = (
    ('v11', 'OPENCODE_PROVIDER=ollama-cloud EDITION=2026-10 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./run.sh opencode glm-5.3 orgsync 1'),
    ('v11', 'EDITION=2026-10 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./run.sh opencode glm-5.3 orgsync 1'),
    ('v11', 'EDITION=2026-10 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./run.sh muse muse-spark-1.3 orgsync 1'),
    ('v11', 'EDITION=2026-10 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./run.sh claude-code haiku orgsync 1'),
    ('v11', 'EDITION=2026-10 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./run.sh codex gpt-6-luna orgsync 1'),
    ('v13', 'EDITION=2026-10 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./run.sh opencode glm-5.3-flash orgsync 1'),
    ('v13', 'DRY_RUN=1 ./preflight.sh'),
    ('batch', 'EDITION=2026-10 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./batch.sh'),
    ('v11', 'EDITION=2026-07 ALLOW_EXPENSIVE=1 DRY_RUN=1 ./run.sh codex gpt-5.6-luna baseline 1'),
)
CONTROLS = (
    "python3 -m unittest discover -s control -p 'test*.py' -v",
    'bash check-calibration-gate.sh',
    'python3 control/check-cronspec-selftest.py',
    'python3 design/orgsync/controls/control_check.py',
    'python3 verify.py',
    'python3 verify.py editions/2026-10',
    'python3 verify.py --rejudge',
    'python3 verify.py editions/2026-10 --rejudge',
    'python3 verify.py --require-no-skips',
    'python3 verify.py --rejudge --require-no-skips',
    'python3 evidence.py --check',
    'bash -n run.sh batch.sh preflight.sh edition.sh check-calibration-gate.sh',
)


def run(command, directory, env):
    print('+ ' + command, flush=True)
    result = subprocess.run(['bash', '-c', command], cwd=directory, env=env,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        print(result.stdout, flush=True)
        raise SystemExit(result.returncode)
    print(result.stdout, end='', flush=True)
    return result.stdout


def runner_fixture(directory, profile):
    # Only revealed runner dependencies; no sibling import and no sealed reads.
    import sys
    sys.path.insert(0, str(ROOT))
    from export import REVEALED_PROMPTS, REVEALED_JUDGES
    names = ('run.sh', 'batch.sh', 'edition.sh', 'preflight.sh', 'preflight_check.py',
             'run_audit.py', 'score.py', 'token_evidence.py', 'TIER_PIN',
             'editions/2026-10/TIER_PIN', 'editions/2026-10/MANIFEST.json',
             'calibration/orgsync.cleared', *REVEALED_PROMPTS, *REVEALED_JUDGES)
    for name in names:
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / name, target)
    july = '9ab9cba3fca5fef0b8e9eb7f62fe0496c7498a74a94e81e441a52ee66fd53eb0'
    target = directory / f'evidence/prices/{july}.yaml'
    target.parent.mkdir(parents=True)
    shutil.copy(ROOT / target.relative_to(directory), target)
    october = json.loads((directory / 'editions/2026-10/MANIFEST.json').read_text())
    sha = (october['prices_yaml_sha256'] if profile == 'v11'
           else october['late_admissions'][0]['prices_yaml_sha256'])
    shutil.copy(ROOT / f'editions/2026-10/evidence/prices/{sha}.yaml', directory / 'prices.yaml')
    if profile == 'batch':
        # Closed historical editions used two rate pins. For a fresh offline
        # batch only, align the original roster pin to the late v13 snapshot.
        pin = directory / 'editions/2026-10/TIER_PIN'
        text = re.sub(r'(?m)^prices_yaml_sha256:.*$', f'prices_yaml_sha256: {sha}', pin.read_text())
        pin.write_text(re.sub(r'(?m)^prices_yaml_version:.*$', 'prices_yaml_version: 13', text))


def main():
    if (ROOT / '.git').exists():
        raise SystemExit('Run this acceptance command inside an exported kit without private Git history.')
    with tempfile.TemporaryDirectory(prefix='kit-controls-') as tmp:
        work = Path(tmp)
        bindir = work / 'bin'; bindir.mkdir()
        marker = work / 'vendor-called'
        for name in ('claude', 'claude-code', 'codex', 'opencode', 'muse'):
            binary = bindir / name
            binary.write_text('#!/bin/sh\nprintf "called\\n" >> ' + shlex.quote(str(marker)) + '\nexit 97\n')
            binary.chmod(0o755)
        home = work / 'home'; home.mkdir()
        env = dict(PATH=str(bindir) + os.pathsep + os.defpath, HOME=str(home),
                   TMPDIR=str(work), LANG='C', LC_ALL='C', TERM='dumb', NO_COLOR='1',
                   USER='kit-control-' + 'account-8291', LOGNAME='kit-control-' + 'account-8291',
                   PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
                   PYTHONWARNINGS='ignore::ResourceWarning', VENDOR_CALL_MARKER=str(marker))
        # Use a system Python already on PATH; no caller PYTHONPATH/config leaks.
        python = shutil.which('python3')
        (bindir / 'python3').symlink_to(python)
        for index, (profile, command) in enumerate(DRY_RUNS):
            directory = work / str(index); directory.mkdir()
            runner_fixture(directory, profile)
            output = run(command, directory, env)
            if './batch.sh' in command:
                if '12 legs, 12 clean exits, 0 non-zero exits, 0 existing results skipped' not in output:
                    raise SystemExit('dry batch failed to exercise the complete fresh roster')
            elif 'command:' not in output:
                raise SystemExit('dry run did not reach the command display')
            if marker.exists():
                raise SystemExit('vendor binary invoked during DRY_RUN')
        kit = work / 'kit'
        shutil.copytree(ROOT, kit, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        # Plant a sibling-only import and prove the clean Python environment
        # cannot resolve it. A private sibling repository is never on sys.path.
        sibling = work / 'private-sibling'; sibling.mkdir()
        (sibling / 'private_sibling_only_probe.py').write_text('raise RuntimeError("private import")\n')
        subprocess.run([str(bindir / 'python3'), '-c',
                        "import importlib.util; assert importlib.util.find_spec('private_sibling_only_probe') is None"],
                       cwd=kit, env=env, check=True)
        for command in CONTROLS:
            run(command, kit, env)
        if marker.exists():
            raise SystemExit('vendor binary invoked by exported controls')
    print('EXPORTED CONTROLS PASS: all dry paths, fresh batch, zero vendor invocations')


if __name__ == '__main__':
    main()
