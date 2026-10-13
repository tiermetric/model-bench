#!/usr/bin/env python3
"""Offline assertions and freshness gate for run.sh's paid auth/capture probe."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time

TOOLS = ('claude-code', 'codex', 'opencode', 'muse')
AUTH_PROMPT = re.compile(r'not\s+logged\s+in|/login|authenticat\w*|api\s*key', re.I)
MAX_AGE = 60 * 60
POLICY_FILES = ('run.sh', 'preflight.sh', 'preflight_http.py', 'preflight_check.py',
                'token_evidence.py', 'score.py', 'run_audit.py', 'edition.sh',
                'editions/2026-10/MANIFEST.json')


def probe_models(root):
    manifest = json.loads((root/'editions/2026-10/MANIFEST.json').read_text())
    roster = manifest['roster']
    if manifest.get('late_admissions'):
        models = {}
        for entry in manifest['late_admissions']:
            tool, model = entry['tool'], entry['model']
            if tool not in TOOLS or model not in roster.get(tool, []):
                raise ValueError(f'off-roster preflight pair {tool}/{model}')
            if tool in models:
                raise ValueError(f'{tool}: duplicate late-admission preflight tool')
            models[tool] = model
        return models
    return {tool: roster[tool][-1] if tool in ('claude-code', 'codex') else roster[tool][0]
            for tool in TOOLS}


def policy_key(root):
    # Invalidate passes after runner changes or a different auth-source context.
    # No credential contents/values enter the record or fingerprint.
    context = {k: os.environ.get(k, '') for k in
               ('HOME', 'CODEX_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME',
                'MUSE_AUTH_PATH', 'OPENCODE_PROVIDER', 'OPENCODE_DB', 'MUSE_SESSIONS', 'PATH')}
    payload = b''
    for name in POLICY_FILES:
        data = (root/name).read_bytes()
        payload += name.encode() + b'\0' + hashlib.sha256(data).digest()
    return hashlib.sha256(payload + json.dumps(context, sort_keys=True).encode()).hexdigest()


def assert_probe(tool, model, run_dir, agent_exit, capture, root=None):
    if root is not None and probe_models(root).get(tool) != model:
        raise ValueError(f'{tool}/{model}: not a required preflight pair')
    if agent_exit != 0:
        raise ValueError(f'{tool}: agent exit {agent_exit}, expected 0')
    output = (run_dir / 'agent-output.txt').read_text()
    if AUTH_PROMPT.search(output):
        raise ValueError(f'{tool}: login/auth prompt in output')
    if capture != 'OK':
        raise ValueError(f'{tool}: capture failed ({capture})')
    log = run_dir / 'session.jsonl'
    if not log.is_file():
        raise ValueError(f'{tool}: no captured session')
    from score import sum_claude_jsonl, sum_codex_jsonl
    from token_evidence import sum_native_jsonl
    if tool == 'claude-code':
        usage = sum_claude_jsonl(log)
    elif tool == 'codex':
        usage = sum_codex_jsonl(log)
    else:
        usage = sum_native_jsonl(log, tool)
    tokens = usage['input_total'] + usage['output']
    if not isinstance(tokens, (int, float)) or not math.isfinite(tokens) or tokens <= 0:
        raise ValueError(f'{tool}: zero or invalid tokens in captured session')
    try:
        evidence = json.loads((run_dir/'loopback.json').read_text())
    except (OSError, ValueError):
        raise ValueError(f'{tool}: loopback request not observed') from None
    if (evidence.get('tool') != tool or evidence.get('requested') is not True
            or not isinstance(evidence.get('body'), str) or not evidence['body']
            or evidence['body'] not in output):
        raise ValueError(f'{tool}: loopback request/body missing or mismatched')
    return dict(tool=tool, model=model, loopback=True, passed_at=time.time(),
                date_utc=datetime.now(timezone.utc).isoformat(), tokens=tokens)


def require_fresh(root, now=None):
    now = time.time() if now is None else now
    record = json.loads((root / '.preflight/pass.json').read_text())
    if not isinstance(record, dict):
        raise ValueError('invalid preflight record')
    if record.get('policy_key') != policy_key(root):
        raise ValueError('runner or auth-source context changed')
    models = probe_models(root)
    if set(record['tools']) - set(models):
        raise ValueError('unexpected preflight tool receipt')
    for tool in models:
        receipt = record['tools'][tool]
        if not isinstance(receipt, dict):
            raise ValueError(f'{tool}: invalid preflight receipt')
        if receipt.get('tool') != tool or receipt.get('model') != models[tool]:
            raise ValueError(f'{tool}: preflight tool/model mismatch')
        if receipt.get('loopback') is not True:
            raise ValueError(f'{tool}: loopback preflight missing')
        stamp, tokens = receipt['passed_at'], receipt['tokens']
        if (type(stamp) not in (int, float) or not math.isfinite(stamp)
                or not 0 <= now - stamp <= MAX_AGE):
            raise ValueError(f'{tool}: preflight is stale or future-dated')
        if type(tokens) not in (int, float) or not math.isfinite(tokens) or tokens <= 0:
            raise ValueError(f'{tool}: preflight tokens must be positive')


def save_record(root, run_root):
    receipts = {}
    for tool, model in probe_models(root).items():
        receipt = json.loads((run_root / tool / 'rep1/preflight-pass.json').read_text())
        if receipt['tool'] != tool or receipt['model'] != model:
            raise ValueError('preflight receipt tool/model mismatch')
        receipts[tool] = receipt
    target = root / '.preflight/pass.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(dict(policy_key=policy_key(root), tools=receipts), indent=2) + '\n')
    temporary.replace(target)
    require_fresh(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('assert')
    check.add_argument('--tool', choices=TOOLS, required=True)
    check.add_argument('--model', required=True)
    check.add_argument('--run-dir', type=Path, required=True)
    check.add_argument('--agent-exit', type=int, required=True)
    check.add_argument('--capture', required=True)
    for name in ('require', 'save', 'models'):
        command = sub.add_parser(name)
        command.add_argument('--root', type=Path, required=True)
        if name == 'save':
            command.add_argument('--run-root', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'assert':
            receipt = assert_probe(args.tool, args.model, args.run_dir, args.agent_exit, args.capture,
                                   root=Path(__file__).resolve().parent)
            (args.run_dir / 'preflight-pass.json').write_text(json.dumps(receipt) + '\n')
            print(f"PREFLIGHT PASS {args.tool}: {receipt['tokens']} tokens")
        elif args.command == 'save':
            save_record(args.root, args.run_root)
        elif args.command == 'models':
            for tool, model in probe_models(args.root).items():
                print(tool, model)
        else:
            require_fresh(args.root)
            print('PREFLIGHT: all required tool/model pairs passed within the last 60 minutes')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Do not expose model output or credentials in an assertion failure.
        if args.command == 'require':
            raise SystemExit(f'REFUSED: fresh preflight required ({exc}). Run ./preflight.sh.')
        raise SystemExit(f'PREFLIGHT FAILED: {exc}')


if __name__ == '__main__':
    main()
