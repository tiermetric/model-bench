#!/usr/bin/env python3
"""Strict public-kit rejudge and flipped-verdict control; standard library only.

Run inside a network-disabled container using rejudge-networkless.sh.
Both entry points require an exported --edition all kit. Only the Docker launcher
provides network isolation; a bare-host run executes model-written code with
the user's account and network.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
# Published manifests and the dated REJUDGE-RECEIPT.json account for these runs.
# July's edition mirror is checked by verify.py; its outcomes count only once.
EDITIONS = (('editions/2026-07', 63), ('editions/2026-10', 30))
LIMITATION = 'No third-party clean-rejudge receipt has been established'
SUMMARY = re.compile(r'^RE-JUDGE (\d+) bundles · PASS (\d+) · FAIL (\d+) · SKIP (\d+)$', re.M)
FLIPPED_CHECK = '''import sys
sys.path.insert(0, sys.argv[1])
from verify import Report, rejudge_run
report = Report()
rejudge_run(sys.argv[2], report, sys.argv[1])
raise SystemExit(1 if report.emit(require_no_skips=True) else 0)
'''


def run(command, kit, home):
    # No credentials, caller Python configuration, or vendor environment.
    # These proxy settings are not forwarded by verify.py to judged programs
    # and provide no network isolation. Use the Docker launcher for isolation.
    env = dict(PATH=os.defpath, HOME=str(home), LANG='C.UTF-8', LC_ALL='C.UTF-8',
               PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
               HTTP_PROXY='http://127.0.0.1:9', HTTPS_PROXY='http://127.0.0.1:9',
               ALL_PROXY='http://127.0.0.1:9', NO_PROXY='localhost,127.0.0.1,::1')
    return subprocess.run(command, cwd=kit, env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=1800)


def check_edition(kit, edition, count, home):
    for rejudge in (False, True):
        command = [sys.executable, '-B', str(kit / 'verify.py'), edition,
                   '--require-no-skips']
        if rejudge:
            command.append('--rejudge')
        result = run(command, kit, home)
        print(result.stdout, end='', flush=True)
        if result.returncode:
            raise ValueError(f'{edition}: strict verification exited {result.returncode}')
        if rejudge:
            summaries = SUMMARY.findall(result.stdout)
            if summaries != [(str(count), str(count), '0', '0')]:
                raise ValueError(f'{edition}: incomplete rejudge receipt')


def negative_control(kit, work):
    # Copy only this revealed bundle. Never mutate published evidence or import
    # directory modes from a read-only mount into the disposable target.
    source = kit / 'codex/gpt-5.6-luna/baseline/rep1'
    bundle = work / 'flipped'
    shutil.copytree(source, bundle, copy_function=shutil.copyfile)
    for directory in (bundle, *(p for p in bundle.rglob('*') if p.is_dir())):
        directory.chmod(0o700)
    path = bundle / 'result.json'
    recorded = json.loads(path.read_text())
    if recorded['outcome'] != 'PASS':
        raise ValueError('negative-control source must have recorded PASS')
    recorded['outcome'] = 'FAIL'
    path.write_text(json.dumps(recorded) + '\n')
    result = run([sys.executable, '-B', '-c', FLIPPED_CHECK, str(kit), str(bundle)],
                 kit, work)
    # Nonzero alone could be an environment error or a strict-mode SKIP.
    # Require the verdict disagreement and exactly one failed rejudge check.
    if (result.returncode != 1 or 'recorded FAIL, re-judged PASS' not in result.stdout
            or 'PASS 0 · FAIL 1 · SKIP 0' not in result.stdout):
        raise ValueError('flipped-verdict control did not detect the planted disagreement')
    print('FLIPPED-VERDICT control: detected recorded FAIL, re-judged PASS; exit 1', flush=True)


def main():
    print(LIMITATION + '.', flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix='public-rejudge-') as tmp:
            work = Path(tmp)
            for edition, count in EDITIONS:
                check_edition(ROOT, edition, count, work)
            negative_control(ROOT, work)
        print(f'REJUDGE WORKFLOW PASS: {sum(n for _, n in EDITIONS)} outcomes; zero SKIPs; flipped verdict detected.')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f'REJUDGE WORKFLOW FAIL: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
