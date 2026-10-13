#!/usr/bin/env python3
"""Offline harness regression fixtures. No real vendor CLI is ever invoked.
Run: python3 -m unittest discover -s control -p 'test_harness.py' -v
"""
import hashlib
import contextlib
import importlib.util
import io
import json
import os
import re
import signal
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
sys.dont_write_bytecode = True
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from token_evidence import capture_opencode, capture_muse, opencode_before, sum_native_jsonl
from score import load_prices, price_native, scan_web_tools
from redact import redact_file, scan_banned
from verify import Report, verify_run, verify_cells, rejudge_run, verify_manifest
from export import rows, kit_rows, kit_plan, export_kit
from aggregate import cell_stats, saturation_verdict
from run_audit import codex_logs, claude_subagents, effort_receipt, sandbox_denials, check_environment
from redact import redact_muse
from preflight_check import assert_probe, policy_key, TOOLS, POLICY_FILES, probe_models, require_fresh, save_record


RUN_TABLE_SHA = 'b3aa945f75af6573c29a3f47610f6f3460afa0665188b679dd2ea261265c9a15'


def use_run_table(root):
    """Disposable runner fixtures reproduce the open edition's v11 run pin."""
    shutil.copy(ROOT/'evidence/prices'/f'{RUN_TABLE_SHA}.yaml', root/'prices.yaml')


def fixture_env():
    # Each test supplies its own vendor controls, independent of the caller.
    return {k: v for k, v in os.environ.items() if
            not re.search(r'EFFORT|REASONING|VARIANT|MAX_THINKING_TOKENS', k, re.I)
            and not k.startswith(('CLAUDE_', 'ANTHROPIC_', 'CODEX_', 'OPENCODE_', 'MUSE_', 'TBH_'))
            and k != 'CLAUDECODE'}


def assert_minimal_environment(test, directory, stamp, tool):
    seen = set(json.loads((directory/'stub-env-names.json').read_text()))
    test.assertEqual(stamp['model_environment'], 'env -i')
    # macOS's framework Python synthesises this after env -i at startup; it is
    # not a variable supplied by the harness or inherited from the caller.
    test.assertEqual(seen - {'__CF_USER_TEXT_ENCODING'}, set(stamp['model_environment_allowlist']))
    common = {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TERM', 'NO_COLOR', 'TMPDIR'}
    keychain = tool in ('claude-code', 'muse')
    test.assertEqual(stamp['clean_config'], not keychain)
    network = stamp['network_policy']
    if tool == 'codex':test.assertIn('network_access=true',network)
    elif tool == 'muse':
        test.assertIn('network=enabled',network)
        test.assertIn('inherited trust cannot be disabled',stamp['trust_policy'])
    else:test.assertIn('shell network available',network)
    if keychain:
        test.assertEqual(seen - {'__CF_USER_TEXT_ENCODING'}, common | {'USER', 'LOGNAME'})
        test.assertFalse({'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME',
            'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'MUSE_AUTH_PATH'} & seen)
    else:
        test.assertTrue(common | {'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME',
            'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'MUSE_AUTH_PATH'} <= seen)
        test.assertFalse({'USER', 'LOGNAME'} & seen)
    test.assertFalse({'CLAUDE_EFFORT', 'CLAUDE_CODE_MAX_OUTPUT_TOKENS', 'CLAUDECODE',
        'CLAUDE_CODE_SESSION_ID', 'CLAUDE_CODE_ENTRYPOINT', 'CLAUDE_CODE_MESSAGING_PIPE',
        'CLAUDE_PLUGIN_DATA', 'CODEX_SESSION_ID', 'SECRET_X'} & seen)
    test.assertNotIn('ANTHROPIC_API_KEY', seen)
    test.assertNotIn('META_API_KEY', seen)
    test.assertEqual('OPENCODE_CONFIG_CONTENT' in seen, tool == 'opencode')
    for secret in ('dummy-secret', 'dummy-anthropic-key', 'dummy-meta-key'):
        test.assertNotIn(secret, (directory/'stamp.json').read_text())


def write_preflight_record(root, env, age=0):
    for name in POLICY_FILES:
        target = root/name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT/name, target)
    models = probe_models(root)
    with patch.dict(os.environ, env, clear=True):
        key = policy_key(root)
    p = root/'.preflight/pass.json'; p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(dict(policy_key=key, tools={tool: dict(
        tool=tool, model=model, loopback=True, passed_at=time.time()-age, tokens=110) for tool, model in models.items()})))


def polluted_session_env():
    # CLAUDE_EFFORT is absent so the real refusal guard stays enabled in receipt
    # runs. Deliberate CLAUDE_EFFORT refusal is tested separately, pre-spend.
    return dict(CLAUDE_CODE_MAX_OUTPUT_TOKENS='17', CLAUDECODE='1',
        CLAUDE_CODE_SESSION_ID='parent', CLAUDE_CODE_ENTRYPOINT='cli',
        CLAUDE_CODE_MESSAGING_PIPE='parent', CLAUDE_PLUGIN_DATA='parent',
        CODEX_SESSION_ID='parent', SECRET_X='dummy-secret',
        ANTHROPIC_API_KEY='dummy-anthropic-key', META_API_KEY='dummy-meta-key')


def write_log(path, objects):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(o)+'\n' for o in objects))


def oc_message():
    return dict(role='assistant', modelID='glm-5.3', providerID='zai-coding-plan',
                path={'cwd': '/RUN'}, time={'completed': 1791800000000}, cost=0,
                tokens=dict(total=172, input=100, output=10, reasoning=50,
                            cache=dict(read=10, write=2)))


def muse_event(rid='a', sid='parent', kind='run', event='model_completed', model='muse-spark-1.3', usage=None):
    return dict(id=rid, stream=dict(kind='session', id=sid), recorded_at=1791800000000000,
                payload=dict(kind=kind, run_id='run1', event=dict(kind=event, model=model,
                usage=usage or dict(input_tokens=100, output_tokens=20, cached_tokens=30,
                    cache_read_tokens=30, cache_write_tokens=5, reasoning_tokens=15))))


class NativeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.p = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_redaction_check_rejects_non_session_paths_and_username(self):
        import getpass
        write_log(self.p/'codex/fixture/orgsync/rep1/session.jsonl', [])
        output = self.p/'codex/fixture/orgsync/rep1/own-tests.log'
        for leak in ('/var/' + 'folders/fixture/T/run', getpass.getuser(), '/' + 'Users/fixture',
                     '/' + 'home/fixture', 'base_' + 'instructions', '"content"' + ':'):
            with self.subTest(marker='temp path' if leak.startswith('/') else 'username'):
                output.write_text(leak)
                p = subprocess.run([sys.executable, str(ROOT/'redact.py'), '--check',
                                    '--out', str(self.p)], capture_output=True, text=True)
                self.assertEqual(p.returncode, 1)
                self.assertIn('LEAK codex/fixture/orgsync/rep1/own-tests.log', p.stdout)
        output.write_text('clean test output')
        p = subprocess.run([sys.executable, str(ROOT/'redact.py'), '--check',
                            '--out', str(self.p)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
        hidden = output.with_name('.hidden-receipt.log'); hidden.write_text('/var/' + 'folders/fixture/T/run')
        p = subprocess.run([sys.executable, str(ROOT/'redact.py'), '--check',
                            '--out', str(self.p)], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn('LEAK codex/fixture/orgsync/rep1/.hidden-receipt.log', p.stdout)

    def test_redaction_plain_windows_home_and_url_path(self):
        path = self.p / 'receipt.txt'
        for value in ('C:' + chr(92) + 'Users' + chr(92) + 'name',
                      json.dumps('C:' + chr(92) + 'Users' + chr(92) + 'name')):
            path.write_text(value)
            self.assertIn(('home-directory path', 1), scan_banned(path))
        path.write_text('https://x.com/' + 'home/page')
        self.assertEqual(scan_banned(path), [])

    def oc_db(self):
        db = self.p/'opencode.db'
        with sqlite3.connect(db) as c:
            c.executescript('CREATE TABLE session(id TEXT, directory TEXT, title TEXT, parent_id TEXT);'
                'CREATE TABLE message(id TEXT, session_id TEXT, data TEXT);'
                'CREATE TABLE part(id TEXT, session_id TEXT, data TEXT);')
            c.executemany('INSERT INTO session VALUES(?,?,?,?)', [('target','/RUN','unique',None),('other','/OTHER','unique',None)])
            c.executemany('INSERT INTO message VALUES(?,?,?)', [('m','target',json.dumps(oc_message())),('noise','other',json.dumps(oc_message()))])
            c.execute('INSERT INTO part VALUES(?,?,?)', ('p','target',json.dumps(dict(type='tool',tool='webfetch',state={'input':{'url':'private'}}))))
        return db

    def native_log(self, objects, fmt):
        path = self.p/'session.jsonl'; write_log(path, objects)
        return path, sum_native_jsonl(path, fmt)

    def test_opencode_exact_session_and_additive_reasoning(self):
        db = self.oc_db()
        opencode_before(db, '/FRESH')
        with self.assertRaises(ValueError): opencode_before(db, '/RUN')
        objects, sid = capture_opencode(db, '/RUN', 'unique')
        self.assertEqual(sid, 'target')
        path, t = self.native_log(objects, 'opencode')
        self.assertEqual((t['input_uncached'],t['cache_read'],t['cache_write'],t['output'],t['reasoning']), (100,10,2,60,50))
        self.assertEqual(t['api_calls'], 1)
        self.assertEqual(t['provider_ids'], ['zai-coding-plan'])
        self.assertEqual(scan_web_tools(path,'opencode'), (1,['webfetch']))
        prices, _ = load_prices(ROOT/'prices.yaml')
        self.assertAlmostEqual(price_native(t, prices, 'opencode'), (102*1.4+10*.26+60*4.4)/1e6)
        with self.assertRaises(ValueError): capture_opencode(db,'/RUN','wrong-title')
        with sqlite3.connect(db) as c: c.execute('INSERT INTO session VALUES(?,?,?,?)',('extra','/RUN','unique',None))
        with self.assertRaises(ValueError): capture_opencode(db,'/RUN','unique')

    def test_opencode_rejects_invalid_or_partial_usage(self):
        for mutate in (lambda m: m['tokens'].update(total=171),
                       lambda m: m['tokens'].pop('total'),
                       lambda m: m['time'].pop('completed'),
                       lambda m: m['tokens'].update(input=-1),
                       lambda m: m['tokens'].update(input=1<<30)):
            m = oc_message(); mutate(m)
            write_log(self.p/'bad', [dict(type='opencode_message', id='m', session_id='s',data=m)])
            with self.assertRaises(ValueError): sum_native_jsonl(self.p/'bad','opencode')

    def test_opencode_excluded_provider_cannot_be_priced_as_zai(self):
        m=oc_message();m['providerID']='ollama-cloud'
        write_log(self.p/'bad',[dict(type='opencode_message',id='m',session_id='s',data=m)])
        with self.assertRaisesRegex(ValueError, 'excluded'):
            sum_native_jsonl(self.p/'bad','opencode')

    def test_muse_containment_review_and_write_not_double_counted(self):
        agent=muse_event()
        review=muse_event('b',kind='approval', event='automated_review_completed',
            model={'model_id':'muse-spark-1.3'},usage=dict(input_tokens=40, cached_input_tokens=10,
            non_cached_input_tokens=30, output_tokens=8, reasoning_tokens=4,total_tokens=48))
        path,t=self.native_log([{'payload':{'kind':'retained_frame','text':'ignore tokens'}},agent,review], 'muse')
        self.assertEqual((t['input_uncached'], t['cache_read'], t['cache_write'],t['output'],t['reasoning']), (100,40,0,28,19))
        self.assertEqual(t['input_total'],140)
        prices,_=load_prices(ROOT/'prices.yaml')
        self.assertAlmostEqual(price_native(t,prices,'muse'), (100*1.25+40*.15+28*4.25)/1e6)
        redacted=self.p/'redacted/session.jsonl'; redact_file(path,redacted)
        self.assertEqual(sum_native_jsonl(redacted,'muse'),t)
        self.assertEqual(scan_banned(redacted),[])

    def test_muse_invalid_usage_never_prices_partial_cost(self):
        for patch in ({'cached_tokens':31},{'reasoning_tokens':21},{'cache_write_tokens':71},
                      {'input_tokens':-1},{'input_tokens':1<<30}):
            o=muse_event();o['payload']['event']['usage'].update(patch)
            write_log(self.p/'bad',[o])
            with self.assertRaises(ValueError):sum_native_jsonl(self.p/'bad','muse')
        o=muse_event(); del o['payload']['event']['usage']['output_tokens']
        write_log(self.p/'bad',[o])
        with self.assertRaises(KeyError):sum_native_jsonl(self.p/'bad','muse')
        write_log(self.p/'bad',[muse_event(),muse_event()])
        with self.assertRaises(ValueError):sum_native_jsonl(self.p/'bad','muse')

    def test_muse_uuid_workspace_and_linked_children(self):
        root=self.p/'sessions'; path=root/'2026/10/12/parent/session.jsonl'
        meta=dict(id='meta',stream=dict(kind='session',id='parent'),payload=dict(kind='metadata',record=dict(workspace_root='/RUN')))
        link=muse_event('link',event='memory_reminder_child_session_linked')
        link['payload']['event'].update(child_session_id='child', parent_run_id='run1')
        write_log(path,[meta,muse_event(),link])
        write_log(path.parent/'subagent/child/session.jsonl',[muse_event('child-a','child')])
        write_log(root/'2026/10/12/unrelated/session.jsonl',[muse_event('noise','unrelated')])
        objects,sid=capture_muse(root,'/RUN','parent')
        _,t=self.native_log(objects,'muse')
        self.assertEqual(t['api_calls'],2);self.assertEqual(sid,'parent')
        with self.assertRaises(ValueError):capture_muse(root,'/OTHER','parent')
        write_log(path,[meta,muse_event()])
        with self.assertRaises(ValueError):capture_muse(root,'/RUN','parent')

    def test_opencode_redaction_preserves_counts_and_audit(self):
        objects,_=capture_opencode(self.oc_db(),'/RUN','unique')
        path,t=self.native_log(objects,'opencode');dest=self.p/'redacted/session.jsonl'
        redact_file(path,dest)
        self.assertEqual(sum_native_jsonl(dest,'opencode'),t)
        self.assertEqual(scan_web_tools(dest,'opencode'),scan_web_tools(path,'opencode'))
        self.assertEqual(scan_banned(dest),[])

    def test_native_verifier_and_export_round_trip(self):
        for fmt,key in (('muse','muse-spark-1.3'),('opencode','glm-5.3')):
            d=self.p/fmt/key/'baseline/rep1';d.mkdir(parents=True)
            objects=[muse_event()] if fmt=='muse' else capture_opencode(self.oc_db(),'/RUN','unique')[0]
            write_log(d/'session.jsonl',objects)
            t=sum_native_jsonl(d/'session.jsonl',fmt); prices,_=load_prices(ROOT/'prices.yaml')
            shutil.copy(ROOT/'prices.yaml', d/'prices.snapshot.yaml')
            (d/'session.jsonl.sha256').write_text(hashlib.sha256((d/'session.jsonl').read_bytes()).hexdigest())
            res=dict(edition='2026-10',tool=fmt,label=key,model_key=key,tokens=t,cost_list_usd=price_native(t,prices,fmt),capture='OK')
            (d/'result.json').write_text(json.dumps(res))
            stamp=dict(edition='2026-10',judge='acceptance_test.py',judge_sha256=hashlib.sha256((ROOT/'acceptance_test.py').read_bytes()).hexdigest(),
                       prices_yaml_sha256=hashlib.sha256((d/'prices.snapshot.yaml').read_bytes()).hexdigest())
            (d/'stamp.json').write_text(json.dumps(stamp))
            r=Report();verify_run(str(d),'rep1',r)
            self.assertTrue(all(st=='PASS' for st,_,_ in r.rows),r.rows)
        self.assertEqual(len(list(rows(self.p,'2026-10'))),2)
        with self.assertRaises(ValueError):list(rows(self.p,'2026-07'))


class VerifyReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.p = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.edition = self.p/'editions/2026-10'
        for rep in (1, 2):
            d=self.edition/f'codex/fixture/baseline/rep{rep}';d.mkdir(parents=True)
            write_log(d/'session.jsonl', [dict(type='event_msg', payload=dict(
                type='token_count', info=dict(total_token_usage=dict(
                    input_tokens=1000, output_tokens=0))))])
            (d/'session.jsonl.sha256').write_text(hashlib.sha256((d/'session.jsonl').read_bytes()).hexdigest())
            (d/'prices.snapshot.yaml').write_text('models:\n  fixture: { input_per_m: 1, output_per_m: 1, provider: openai }\n')
            (d/'result.json').write_text(json.dumps(dict(tool='codex', label='fixture',
                model_key='fixture', task='baseline', outcome='PASS', cost_list_usd=0.001)))
            (d/'stamp.json').write_text(json.dumps(dict(judge='acceptance_test.py',
                judge_sha256=hashlib.sha256((ROOT/'acceptance_test.py').read_bytes()).hexdigest(),
                prices_yaml_sha256=hashlib.sha256((d/'prices.snapshot.yaml').read_bytes()).hexdigest())))
        self.report = self.edition/'REPORT.md'
        self.write_report('0.0020')

    def write_report(self, total):
        self.report.write_text(f'**2** runs · ${total} total · 2 accepted / 0 not accepted\n'
            '| model | E[$/accepted] | TIER |\n|---|---|---|\n| fixture | $0.0010 | 1,000,000 |\n')

    def invoke(self, script=None):
        saved = os.environ.get('VERIFY_RECEIPT_SCRIPT')
        if saved and script is None:
            # Execute the saved bytes with the real repo location for containment.
            code = (f"import sys;sys.path.insert(0,{str(ROOT)!r});"
                    f"__file__={str(ROOT/'verify.py')!r};"
                    f"exec(compile(open({saved!r}).read(),__file__,'exec'))")
            argv = [sys.executable, '-c', code, str(self.edition)]
        else:
            argv = [sys.executable, str(script or ROOT/'verify.py'), str(self.edition)]
        return subprocess.run(argv, capture_output=True, text=True)

    def test_report_in_passed_edition_is_checked(self):
        p=self.invoke();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        report_lines=[line for line in p.stdout.splitlines() if str(self.report) in line]
        self.assertEqual(len(report_lines),5,p.stdout)
        self.assertTrue(all('[PASS]' in line for line in report_lines),p.stdout)
        self.assertNotIn('[SKIP]',p.stdout)

    def test_report_total_off_by_0001_fails(self):
        self.write_report('0.0021')
        p=self.invoke();self.assertNotEqual(p.returncode,0,p.stdout+p.stderr)
        total_lines=[line for line in p.stdout.splitlines() if 'total spend' in line]
        self.assertEqual(len(total_lines),1,p.stdout)
        self.assertIn('[FAIL]',total_lines[0]);self.assertIn(str(self.report),total_lines[0])
        self.assertNotIn('[SKIP]',p.stdout)

    def test_missing_edition_report_is_skipped(self):
        self.report.unlink()
        p=self.invoke();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn(f'[SKIP] report {self.report}: no REPORT.md to check',p.stdout)

    def declare_report_table(self):
        table_tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(table_tmp.cleanup)
        table_path = Path(table_tmp.name)/'prices.yaml'
        table_path.write_text('version: 12\nmodels:\n'
            '  fixture: { input_per_m: 1, output_per_m: 1, cache_read_mult: 0.05, provider: openai }\n')
        table = dict(path=str(table_path.relative_to(ROOT)), version=12,
                     sha256=hashlib.sha256(table_path.read_bytes()).hexdigest())
        (self.edition/'MANIFEST.json').write_text(json.dumps(dict(report_rate_table=table)))
        for d in self.edition.glob('codex/fixture/baseline/rep*'):
            write_log(d/'session.jsonl', [dict(type='event_msg', payload=dict(
                type='token_count', info=dict(total_token_usage=dict(
                    input_tokens=1000000, cached_input_tokens=1000000, output_tokens=0))))])
            (d/'session.jsonl.sha256').write_text(hashlib.sha256((d/'session.jsonl').read_bytes()).hexdigest())
            (d/'prices.snapshot.yaml').write_text('version: 11\nmodels:\n'
                '  fixture: { input_per_m: 1, output_per_m: 1, cache_read_mult: 0.1, provider: openai }\n')
            stamp=json.loads((d/'stamp.json').read_text())
            stamp['prices_yaml_sha256']=hashlib.sha256((d/'prices.snapshot.yaml').read_bytes()).hexdigest()
            (d/'stamp.json').write_text(json.dumps(stamp))
            res=json.loads((d/'result.json').read_text());res['cost_list_usd']=0.1
            (d/'result.json').write_text(json.dumps(res))
        return table_path, table

    def write_repriced_report(self, total, cost, tier):
        self.report.write_text(f'**2** runs · ${total} total · 2 accepted / 0 not accepted\n'
            f'| model | E[$/accepted] | TIER |\n|---|---|---|\n| fixture | ${cost} | {tier} |\n')

    def test_report_table_sha_mismatch_fails(self):
        path, _ = self.declare_report_table()
        path.write_text(path.read_text()+'# changed bytes\n')
        p=self.invoke();self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertIn('[FAIL] report rate table -- sha256 mismatch',p.stdout)

    def checkout_verifier(self):
        checkout = self.p/'checkout';checkout.mkdir()
        for name in ('verify.py', 'score.py', 'token_evidence.py', 'acceptance_test.py'):
            source = Path(os.environ.get('VERIFY_RECEIPT_SCRIPT', str(ROOT/name))) if name == 'verify.py' else ROOT/name
            shutil.copy(source, checkout/name)
        return checkout

    def test_report_table_parent_path_fails(self):
        path, table = self.declare_report_table()
        checkout = self.checkout_verifier()
        outside = self.p/'x';outside.mkdir()
        shutil.copy(path,outside/'prices.yaml')  # Existing v12 file, correct SHA.
        table['path'] = '../x/prices.yaml'
        (self.edition/'MANIFEST.json').write_text(json.dumps(dict(report_rate_table=table)))
        self.write_repriced_report('0.1000', '0.0500', '20,000')
        p = self.invoke(checkout/'verify.py');self.assertEqual(p.returncode, 1, p.stdout+p.stderr)
        self.assertIn('[FAIL] report rate table -- report rate table must be a path inside the repo', p.stdout)

    def test_report_table_absolute_path_fails(self):
        path, table = self.declare_report_table()
        table['path'] = str(path)
        (self.edition/'MANIFEST.json').write_text(json.dumps(dict(report_rate_table=table)))
        self.write_repriced_report('0.1000', '0.0500', '20,000')
        p = self.invoke();self.assertEqual(p.returncode, 1, p.stdout+p.stderr)
        self.assertIn('[FAIL] report rate table -- report rate table must be a path inside the repo', p.stdout)

    def test_report_table_version_mismatch_fails(self):
        _, table = self.declare_report_table()
        table['version'] = 13  # Keep the correct SHA of the v12 bytes.
        (self.edition/'MANIFEST.json').write_text(json.dumps(dict(report_rate_table=table)))
        self.write_repriced_report('0.1000', '0.0500', '20,000')
        p = self.invoke();self.assertEqual(p.returncode, 1, p.stdout+p.stderr)
        self.assertIn('[FAIL] report rate table -- report rate table version mismatch', p.stdout)

    def test_non_object_manifest_fails(self):
        for manifest in ([], ['report_rate_table'], 'snapshot', 12, None):
            with self.subTest(manifest=manifest):
                (self.edition/'MANIFEST.json').write_text(json.dumps(manifest))
                p = self.invoke();self.assertEqual(p.returncode, 1, p.stdout+p.stderr)
                self.assertIn('[FAIL] report rate table -- manifest must be a JSON object', p.stdout)

    def test_report_table_through_symlinked_checkout(self):
        checkout = self.checkout_verifier()
        link = self.p/'checkout-link';link.symlink_to(checkout, target_is_directory=True)
        (checkout/'prices.yaml').write_text('version: 12\nmodels:\n  fixture: { input_per_m: 1, output_per_m: 1, provider: openai }\n')
        table = dict(path='prices.yaml', version=12,
                     sha256=hashlib.sha256((checkout/'prices.yaml').read_bytes()).hexdigest())
        (self.edition/'MANIFEST.json').write_text(json.dumps(dict(report_rate_table=table)))
        p = self.invoke(link/'verify.py');self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
        self.assertIn('[PASS] report rate table', p.stdout);self.assertNotIn('[SKIP]', p.stdout)

    def test_snapshot_figures_fail_and_report_table_figures_pass(self):
        self.declare_report_table()
        self.write_repriced_report('0.2000','0.1000','10,000')
        p=self.invoke();self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertIn('expected to find \'$0.1000\'',p.stdout)
        self.assertIn('bundles give $0.0500',p.stdout)
        self.write_repriced_report('0.1000','0.0500','10,000')
        p=self.invoke();self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertIn('bundles give 20,000',p.stdout)
        self.write_repriced_report('0.1000','0.0500','20,000')
        p=self.invoke();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('report diff uses ',p.stdout);self.assertIn(' v12 sha256 ',p.stdout)
        self.assertNotIn('[SKIP]',p.stdout)
        for d in self.edition.glob('codex/fixture/baseline/rep*'):
            self.assertEqual(json.loads((d/'result.json').read_text())['cost_list_usd'],0.1)

    def test_missing_report_table_fails_even_without_report(self):
        path, _ = self.declare_report_table();path.unlink();self.report.unlink()
        p=self.invoke();self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertIn('[FAIL] report rate table',p.stdout)

    def test_no_report_table_keeps_snapshot_figures(self):
        self.declare_report_table();(self.edition/'MANIFEST.json').write_text('{}\n')
        self.write_repriced_report('0.2000','0.1000','10,000')
        p=self.invoke();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
        self.assertIn('Report diff uses bundle snapshots',p.stdout)


class VerifySharedCostTests(unittest.TestCase):
    """One pricing fixture checks both consumers, including stamp fallback."""
    def setUp(self):
        script = os.environ.get('VERIFY_RECEIPT_SCRIPT', str(ROOT/'verify.py'))
        spec = importlib.util.spec_from_file_location('cost_receipt_verify', script)
        self.verify = importlib.util.module_from_spec(spec);spec.loader.exec_module(self.verify)
        self.verify.__file__ = str(ROOT/'verify.py')
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT);self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_all_pricing_branches_share_helper_and_stamp_inputs(self):
        cases = [
            ('codex', 'fixture', [dict(type='event_msg', payload=dict(type='token_count',
                info=dict(total_token_usage=dict(input_tokens=100, cached_input_tokens=20,
                    output_tokens=10, cache_write_input_tokens=5))))],
                'input_per_m: 2, output_per_m: 10, cache_read_mult: 0.1, provider: openai', 0.000274),
            ('claude-code', 'fixture', [dict(type='assistant', message=dict(id='a',model='fixture',
                usage=dict(input_tokens=100, output_tokens=10, cache_read_input_tokens=20,
                    cache_creation=dict(ephemeral_5m_input_tokens=4, ephemeral_1h_input_tokens=3))))],
                'input_per_m: 2, output_per_m: 10, cache_read_mult: 0.1, provider: anthropic', 0.000326),
            # Two calls straddle the tier: choose per request, never from their sum.
            ('claude-code', 'fixture', [dict(type='assistant', message=dict(id=str(n),model='fixture',
                usage=dict(input_tokens=n, output_tokens=10))) for n in (50, 150)],
                'input_per_m: 2, output_per_m: 10, cache_read_mult: 0.1, provider: anthropic, '
                'context_threshold: 100, input_per_m_over: 4, output_per_m_over: 20', 0.001),
            ('opencode', 'glm-5.3', [dict(type='opencode_message', session_id='fixture', id='m', data=oc_message())],
                'input_per_m: 1.4, output_per_m: 4.4, cache_read_mult: 0.18571428571428572, provider: zai', 0.0004094),
            ('muse', 'muse-spark-1.3', [muse_event()],
                'input_per_m: 1.25, output_per_m: 4.25, cache_read_mult: 0.12, provider: meta', 0.000177),
        ]
        for n, (tool, key, objects, row, expected) in enumerate(cases):
            with self.subTest(tool=tool, row=row):
                d=self.base/f'bundle{n}';d.mkdir()
                write_log(d/'session.jsonl',objects)
                (d/'session.jsonl.sha256').write_text(hashlib.sha256((d/'session.jsonl').read_bytes()).hexdigest())
                table=self.base/f'prices{n}.yaml';table.write_text(f'version: 12\nmodels:\n  {key}: {{ {row} }}\n')
                shutil.copy(table,d/'prices.snapshot.yaml')
                stamp=dict(model_flag=key,judge='acceptance_test.py',
                    judge_sha256=hashlib.sha256((ROOT/'acceptance_test.py').read_bytes()).hexdigest(),
                    prices_yaml_sha256=hashlib.sha256(table.read_bytes()).hexdigest())
                (d/'stamp.json').write_text(json.dumps(stamp))
                # model_flag on result is deliberately wrong: the fallback is the stamp.
                res=dict(tool=tool, model_flag='wrong-result-flag', capture='OK', cost_list_usd=expected)
                if tool in ('opencode','muse'):
                    res.update(model_key=key,tokens=sum_native_jsonl(d/'session.jsonl',tool))
                (d/'result.json').write_text(json.dumps(res))
                (self.base/'MANIFEST.json').write_text(json.dumps(dict(report_rate_table=dict(
                    path=str(table.relative_to(ROOT)),version=12,
                    sha256=hashlib.sha256(table.read_bytes()).hexdigest()))))
                # A single helper must be called by each route with the same inputs.
                helper=getattr(self.verify,'derive_run_cost',None)
                self.assertIsNotNone(helper,'bundle and report must use one shared pricing helper')
                with patch.object(self.verify,'derive_run_cost',wraps=helper) as shared:
                    r=self.verify.Report();self.verify.verify_run(str(d),'rep1',r)
                    self.assertTrue(all(st=='PASS' for st,_,_ in r.rows),r.rows)
                    report=self.verify.Report()
                    priced=self.verify.report_priced_runs([{**res,'_bundle_dir':str(d)}],report,str(self.base))
                    self.assertIsNotNone(priced,report.rows)
                    self.assertEqual(priced[0]['cost_list_usd'],round(expected,6))
                    self.assertEqual(shared.call_count,2)
                    bundle_args,report_args=[call.args for call in shared.call_args_list]
                    self.assertEqual(bundle_args[0],report_args[0])
                    self.assertEqual({k:v for k,v in report_args[1].items() if k!='_bundle_dir'},bundle_args[1])
                    self.assertEqual(bundle_args[2:],report_args[2:])

    def test_report_stamp_fallback_prices_without_result_model_key(self):
        d=self.base/'bundle';d.mkdir()
        write_log(d/'session.jsonl',[dict(type='event_msg',payload=dict(type='token_count',
            info=dict(total_token_usage=dict(input_tokens=100,output_tokens=10))))])
        (d/'stamp.json').write_text(json.dumps(dict(model_flag='fixture')))
        table=self.base/'prices.yaml';table.write_text('version: 12\nmodels:\n  fixture: { input_per_m: 2, output_per_m: 10, provider: openai }\n')
        (self.base/'MANIFEST.json').write_text(json.dumps(dict(report_rate_table=dict(
            path=str(table.relative_to(ROOT)),version=12,sha256=hashlib.sha256(table.read_bytes()).hexdigest()))))
        r=self.verify.Report()
        priced=self.verify.report_priced_runs([dict(tool='codex',cost_list_usd=0.0003,_bundle_dir=str(d))],r,str(self.base))
        self.assertIsNotNone(priced,r.rows)
        self.assertEqual(priced[0]['cost_list_usd'],0.0003)


class VerifyReviewReceiptTests(unittest.TestCase):
    """PR #14 receipts; optionally load a saved verifier/revision for RED checks."""
    def setUp(self):
        script = os.environ.get('VERIFY_RECEIPT_SCRIPT', str(ROOT/'verify.py'))
        spec = importlib.util.spec_from_file_location('receipt_verify', script)
        self.verify = importlib.util.module_from_spec(spec);spec.loader.exec_module(self.verify)
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.edition = Path(self.tmp.name)
        self.runs = [dict(label='alpha', task='orgsync', outcome='PASS', cost_list_usd=1.0),
                     dict(label='beta', task='orgsync', outcome='PASS', cost_list_usd=2.0)]
        self.text = ('**2** runs · $3.0000 total · 2 accepted / 0 not\n'
                     '### INTEGRATION — `orgsync`\n'
                     '| model | TIER ↑ | E[$/accepted] ↓ |\n|---|---|---|\n'
                     '| alpha | 5,000 | $1.0000 |\n| beta | 2,500 | $2.0000 |\n')

    def check(self, text=None, runs=None):
        (self.edition/'REPORT.md').write_text(self.text if text is None else text)
        report = self.verify.Report()
        self.verify.verify_report(self.runs if runs is None else runs, report, str(self.edition))
        return report.rows

    def source(self, path):
        revision = os.environ.get('VERIFY_RECEIPT_REV')
        if revision:
            return subprocess.check_output(['git', 'show', f'{revision}:{path}'], cwd=ROOT, text=True)
        return (ROOT/path).read_text()

    def test_repo_root_keeps_edition_one_report(self):
        # Resolve __file__ to the repository for both the current and saved copy.
        with patch.object(self.verify, '__file__', str(ROOT/'verify.py')), \
                patch.object(sys, 'argv', ['verify.py', str(ROOT)]), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = self.verify.main()
        self.assertEqual(code, 0, output.getvalue())
        self.assertIn('[PASS] report metrics.csv -- every value matches across 63 runs', output.getvalue())
        from verify import FIGURE_NAMES
        for task in ('baseline', 'cronspec', 'orgsync'):
            for name in FIGURE_NAMES:
                self.assertIn(f'[PASS] report {name}:{task} figure marker', output.getvalue())
            for name in ('how-models-worked', 'per-run'):
                self.assertIn(f'[PASS] report {name}:{task} table matches metrics', output.getvalue())
        lines = [line for line in output.getvalue().splitlines()
                 if 'report ' in line and 'REPORT.md' in line]
        self.assertEqual(len(lines), 5, output.getvalue())
        self.assertTrue(all('[PASS]' in line and 'editions/2026-07/REPORT.md' in line for line in lines), output.getvalue())

    def test_labelled_accepted_split_rejects_eight_of_nine(self):
        runs = [dict(label='alpha', task='orgsync', outcome='PASS', cost_list_usd=1.0) for _ in range(9)]
        text = ('**9** runs · $9.0000 total · 8 accepted / 1 not\n'
                '| model | TIER | E[$/accepted] |\n|---|---|---|\n| alpha | 5,000 | $1.0000 |\n')
        rows = self.check(text, runs)
        for label in ('accepted count', 'not-accepted count'):
            self.assertTrue(any(st == 'FAIL' and label in what for st, what, _ in rows), rows)

    def test_swapped_models_fail_each_metric(self):
        for old, new, label in (('$1.0000', '$2.0000', 'E[$/accepted]'), ('5,000', '2,500', 'TIER')):
            with self.subTest(metric=label):
                text = self.text.replace(old, 'SWAP').replace(new, old).replace('SWAP', new)
                rows = self.check(text)
                self.assertTrue(any(st == 'FAIL' and label in what for st, what, _ in rows), rows)

    def test_each_report_arm_rejects_broken_evidence(self):
        mutations = (
            ('run count', self.text.replace('**2** runs', '**3** runs') + '\nUnrelated **2**.\n'),
            ('accepted count', self.text.replace('2 accepted / 0 not', '1 accepted / 1 not')),
            ('not-accepted count', self.text.replace('2 accepted / 0 not', '2 accepted / 1 not')),
            ('E[$/accepted]', self.text.replace('| alpha | 5,000 | $1.0000 |', '| alpha | 5,000 | $9.0000 |') + '\nUnrelated $1.0000.\n'),
            ('TIER', self.text.replace('| alpha | 5,000 |', '| alpha | 9,000 |') + '\nUnrelated 5,000.\n'),
            ('diffed ZERO', '**2** runs · $3.0000 · 2 accepted / 0 not\nProse $1.0000 $2.0000 5,000 2,500\n'),
        )
        for label, text in mutations:
            with self.subTest(arm=label):
                rows = self.check(text)
                self.assertTrue(any(st == 'FAIL' and label in what for st, what, _ in rows), rows)

    def test_labelled_header_and_stamp_forms_pass(self):
        for phrase in ('2 accepted / 0 not', '2 accepted · 0 not accepted'):
            rows = self.check(self.text.replace('2 accepted / 0 not', phrase))
            self.assertEqual([st for st, _, _ in rows], ['PASS'] * 5, rows)

    def test_task_headings_scope_same_model_rows(self):
        runs = [dict(label='alpha', task='baseline', outcome='PASS', cost_list_usd=1.0),
                dict(label='alpha', task='orgsync', outcome='PASS', cost_list_usd=2.0)]
        text = ('**2** runs · $3.0000 · 2 accepted / 0 not\n'
                '### EASY — `duration`\n| model | TIER | E[$/accepted] |\n|---|---|---|\n| alpha | 1,000 | $1.0000 |\n'
                '### INTEGRATION — `orgsync`\n| model | TIER | E[$/accepted] |\n|---|---|---|\n| alpha | 2,500 | $2.0000 |\n')
        self.assertEqual([st for st, _, _ in self.check(text, runs)], ['PASS'] * 5)
        rows = self.check(text.replace('$1.0000', 'SWAP').replace('$2.0000', '$1.0000').replace('SWAP', '$2.0000'), runs)
        self.assertTrue(any(st == 'FAIL' for st, _, _ in rows), rows)


class LateAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        for name in ('run.sh', 'preflight.sh', 'preflight_check.py', 'edition.sh', 'run_audit.py', 'score.py', 'token_evidence.py',
                     'prices.yaml', 'acceptance_orgsync.py'):
            shutil.copy(ROOT/name, self.base/name)
        for directory in ('prompts', 'editions/2026-10'):
            shutil.copytree(ROOT/directory, self.base/directory,
                            ignore=shutil.ignore_patterns('claude-code', 'codex', 'opencode', 'muse'))
        self.manifest_path = self.base/'editions/2026-10/MANIFEST.json'
        self.manifest = json.loads(self.manifest_path.read_text())
        shutil.copy(ROOT/'editions/2026-10/opencode/glm-5.3-flash/orgsync/rep1/prices.snapshot.yaml', self.base/'prices.yaml')
        self.sha = hashlib.sha256((self.base/'prices.yaml').read_bytes()).hexdigest()

    def invoke(self, tool='opencode', model='glm-5.3-flash', **extra):
        env = fixture_env()
        env.update(EDITION='2026-10', DRY_RUN='1', ALLOW_EXPENSIVE='1')
        env.update(extra)
        return subprocess.run(['bash', str(self.base/'run.sh'), tool, model, 'orgsync', '1'],
                              env=env, capture_output=True, text=True)

    def test_preflight_probe_set_is_only_flash(self):
        self.assertEqual(probe_models(self.base), {'opencode': 'glm-5.3-flash'})

    def test_preflight_missing_flash_receipt_refused(self):
        env = fixture_env()
        write_preflight_record(self.base, env)
        path = self.base/'.preflight/pass.json'
        record = json.loads(path.read_text())
        run_root = self.base/'probe'
        for tool in ('opencode', 'muse'):
            receipt = run_root/tool/'rep1/preflight-pass.json'
            receipt.parent.mkdir(parents=True)
            receipt.write_text(json.dumps(record['tools']['opencode']))
        with patch.dict(os.environ, env, clear=True):
            save_record(self.base, run_root)
            record = json.loads(path.read_text())
            self.assertEqual(set(record['tools']), {'opencode'})
            self.assertEqual(record['tools']['opencode']['model'], 'glm-5.3-flash')
            require_fresh(self.base)
            del record['tools']['opencode']
            path.write_text(json.dumps(record))
            with self.assertRaises((ValueError, KeyError)):
                require_fresh(self.base)

    def test_preflight_without_late_admissions_requires_all_four(self):
        self.manifest.pop('late_admissions')
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.assertEqual(probe_models(self.base), {
            'claude-code': 'claude-haiku-5-5', 'codex': 'gpt-6-luna',
            'opencode': 'glm-5.3', 'muse': 'muse-spark-1.3'})
        env = fixture_env()
        write_preflight_record(self.base, env)
        with patch.dict(os.environ, env, clear=True):
            require_fresh(self.base)
            path = self.base/'.preflight/pass.json'
            record = json.loads(path.read_text())
            del record['tools']['muse']
            path.write_text(json.dumps(record))
            with self.assertRaises((ValueError, KeyError)):
                require_fresh(self.base)

    def test_preflight_dry_run_names_only_flash(self):
        # An empty operator home and poison CLIs make this safe even if the
        # dry-run execution guard regresses; no real CLI or auth is available.
        home = self.base/'operator-home'; home.mkdir()
        bin_dir = self.base/'bin'; bin_dir.mkdir()
        for tool in ('claude', 'codex', 'opencode', 'muse'):
            stub = bin_dir/tool
            stub.write_text('#!/bin/sh\necho PAID_CALL_ATTEMPT >&2\nexit 99\n')
            stub.chmod(0o755)
        env = fixture_env()
        env.update(EDITION='2026-10', DRY_RUN='1', HOME=str(home),
                   PATH=str(bin_dir)+os.pathsep+os.environ['PATH'])
        p = subprocess.run(['bash', str(self.base/'preflight.sh')], env=env,
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
        commands = [line for line in p.stdout.splitlines() if line.startswith('command:')]
        self.assertEqual(len(commands), 1, p.stdout)
        self.assertTrue(commands[0].startswith('command: opencode run '), commands)
        self.assertIn('zai-coding-plan/glm-5.3-flash', commands[0])
        self.assertIn('prices_version=13', p.stdout)
        self.assertNotIn('PAID_CALL_ATTEMPT', p.stderr)
        self.assertFalse((self.base/'.preflight/pass.json').exists())
        self.assertFalse((self.base/'.tool-config').exists())

    def test_preflight_assert_rejects_pair_outside_admission(self):
        with self.assertRaisesRegex(ValueError, 'not a required preflight pair'):
            assert_probe('opencode', 'glm-5.3', self.base/'probe', 0, 'OK', root=self.base)

    def test_preflight_off_roster_duplicate_and_unexpected_receipts_refused(self):
        original = json.loads(json.dumps(self.manifest))
        self.manifest['late_admissions'][0]['tool'] = 'codex'
        self.manifest_path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, 'off-roster preflight pair'):
            probe_models(self.base)
        self.manifest = json.loads(json.dumps(original))
        self.manifest['late_admissions'].append(dict(self.manifest['late_admissions'][0]))
        self.manifest_path.write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ValueError, 'duplicate late-admission preflight tool'):
            probe_models(self.base)
        self.manifest_path.write_text(json.dumps(original))
        env = fixture_env(); write_preflight_record(self.base, env)
        path = self.base/'.preflight/pass.json'; record = json.loads(path.read_text())
        record['tools']['codex'] = dict(record['tools']['opencode'])
        path.write_text(json.dumps(record))
        with patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(ValueError, 'unexpected preflight tool receipt'):
            require_fresh(self.base)

    def test_duplicate_late_pin_refused(self):
        self.manifest['late_admissions'].append(dict(self.manifest['late_admissions'][0]))
        self.manifest_path.write_text(json.dumps(self.manifest))
        p = self.invoke()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('FATAL: duplicate late-admission pin', p.stderr)
        self.assertNotIn('command:', p.stdout)

    def test_original_roster_late_pin_refused(self):
        original = self.base/'editions/2026-10/opencode/glm-5.3/orgsync/rep1/stamp.json'
        original.parent.mkdir(parents=True)
        original.write_text(json.dumps(dict(prices_yaml_sha256=RUN_TABLE_SHA)))
        self.manifest['late_admissions'][0]['model'] = 'glm-5.3'
        self.manifest_path.write_text(json.dumps(self.manifest))
        p = self.invoke(model='glm-5.3')
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('FATAL: late-admission pin names an original-roster model', p.stderr)
        self.assertNotIn('command:', p.stdout)

    def test_protocol_price_notes_and_flash_byte_identity(self):
        # Pins facts, not wording: the report stamps the protocol's current
        # version, §9 has a dated row for the late-admission ruling, and every
        # harness file the report names as byte-identical is byte-identical.
        protocol = (ROOT/'PROTOCOL.md').read_text()
        report = (ROOT/'editions/2026-10/REPORT.md').read_text()
        version = re.search(r'^\*\*Version (\d+\.\d+)', protocol, re.M)[1]
        self.assertIn(f'| protocol | v{version} |', report)
        row = next(line for line in protocol.splitlines() if line.startswith(f'| {version} |'))
        self.assertIn('R-2026-10-13-1', row)
        self.assertRegex(row, r'\| \d{4}-\d{2}-\d{2} \|')
        paragraph = report.split('Git comparison of `66bd8ec` and reviewed head `1ad7ea8`:',1)[1].split('\n\n',1)[0]
        names = re.findall(r'`([\w./]+\.(?:sh|py))`', paragraph.split('are\nbyte-identical',1)[0])
        self.assertGreaterEqual(len(names), 10)
        for name in names:
            a = subprocess.check_output(['git','show',f'66bd8ec:{name}'],cwd=ROOT)
            b = subprocess.check_output(['git','show',f'1ad7ea8:{name}'],cwd=ROOT)
            self.assertEqual(a,b,name)

    def test_flash_admitted_with_v13_pin(self):
        entry = self.manifest['late_admissions'][0]
        self.assertEqual(entry['prices_yaml_version'], 13)
        self.assertEqual(entry['prices_yaml_sha256'], self.sha)
        self.assertEqual(entry['ruling_id'], 'R-2026-10-13-1')
        self.assertEqual(entry['reps'], 3)
        p = self.invoke()
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
        self.assertIn(f'prices={self.sha} prices_version=13', p.stdout)
        self.assertIn('zai-coding-plan/glm-5.3-flash', p.stdout)
        self.assertIn('gate=allow-expensive-override', p.stdout)
        self.assertFalse((self.base/'.tool-config').exists())
        refused = self.invoke(ALLOW_EXPENSIVE='0')
        self.assertEqual(refused.returncode, 3, refused.stderr)
        self.assertIn('anchors require logged ALLOW_EXPENSIVE=1', refused.stderr)
        probe = self.invoke(PREFLIGHT='1', PREFLIGHT_RUN_ROOT=str(self.base/'probe'))
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertIn('gate=auth-capture-loopback-preflight', probe.stdout)

    def test_other_closed_model_still_refused(self):
        p = self.invoke('claude-code', 'claude-haiku-5-5')
        self.assertEqual(p.returncode, 1, p.stdout+p.stderr)
        self.assertIn('FATAL: pinned prices hash mismatch', p.stderr)
        self.assertNotIn('command:', p.stdout)
        paid = self.invoke('claude-code', 'claude-haiku-5-5', DRY_RUN='0', GATE_CHECK='1')
        self.assertEqual(paid.returncode, 1, paid.stdout+paid.stderr)
        self.assertIn('FATAL: pinned prices hash mismatch', paid.stderr)
        self.assertNotIn('PREFLIGHT', paid.stderr)

    def test_off_roster_late_admission_refused(self):
        # Supply a valid pin and price row for this off-roster tool/model pair:
        # only the roster guard, rather than another downstream error, rejects it.
        self.manifest['late_admissions'][0]['tool'] = 'codex'
        self.manifest_path.write_text(json.dumps(self.manifest))
        p = self.invoke('codex', 'glm-5.3-flash')
        self.assertNotEqual(p.returncode, 0, p.stdout+p.stderr)
        self.assertIn('FATAL: off-roster model codex/glm-5.3-flash', p.stderr)
        self.assertNotIn('command:', p.stdout)

    def test_late_admission_bad_sha_refused(self):
        self.manifest['late_admissions'][0]['prices_yaml_sha256'] = '0'*64
        self.manifest_path.write_text(json.dumps(self.manifest))
        p = self.invoke()
        self.assertEqual(p.returncode, 1, p.stdout+p.stderr)
        self.assertIn('FATAL: pinned prices hash mismatch', p.stderr)
        self.assertNotIn('command:', p.stdout)

    def test_late_admission_bad_version_refused(self):
        self.manifest['late_admissions'][0]['prices_yaml_version'] = 12
        self.manifest_path.write_text(json.dumps(self.manifest))
        p = self.invoke()
        self.assertNotEqual(p.returncode, 0, p.stdout+p.stderr)
        self.assertIn('FATAL: pinned prices version mismatch', p.stderr)

    def test_bundle_verifier_accepts_each_run_pin_and_rejects_tampering(self):
        for version, source in ((11, ROOT/'evidence/prices'/f'{RUN_TABLE_SHA}.yaml'),
                                (13, self.base/'prices.yaml')):
            d = self.base/f'codex/fixture/baseline/rep{version}'; d.mkdir(parents=True)
            write_log(d/'session.jsonl', [dict(type='event_msg', payload=dict(
                type='token_count', info=dict(total_token_usage=dict(input_tokens=1000, output_tokens=0))))])
            (d/'session.jsonl.sha256').write_text(hashlib.sha256((d/'session.jsonl').read_bytes()).hexdigest())
            shutil.copy(source, d/'prices.snapshot.yaml')
            from score import price, sum_codex_jsonl
            prices, _ = load_prices(source)
            cost = price(sum_codex_jsonl(d/'session.jsonl'), prices['gpt-6-luna'], 'codex')[0]
            (d/'result.json').write_text(json.dumps(dict(tool='codex', model_key='gpt-6-luna', cost_list_usd=cost)))
            stamp = dict(judge='acceptance_orgsync.py',
                         judge_sha256=hashlib.sha256((ROOT/'acceptance_orgsync.py').read_bytes()).hexdigest(),
                         prices_yaml_version=version, prices_yaml_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
            (d/'stamp.json').write_text(json.dumps(stamp))
            r = Report(); verify_run(str(d), f'rep{version}', r)
            self.assertTrue(all(status=='PASS' for status, _, _ in r.rows), r.rows)
            stamp['prices_yaml_version'] = version + 1
            (d/'stamp.json').write_text(json.dumps(stamp))
            r = Report(); verify_run(str(d), f'rep{version}', r)
            self.assertTrue(any(status=='FAIL' and what.endswith(': stamped run price version')
                                for status, what, _ in r.rows), r.rows)
            stamp['prices_yaml_version'] = version
            stamp['prices_yaml_sha256'] = '0'*64
            (d/'stamp.json').write_text(json.dumps(stamp))
            r = Report(); verify_run(str(d), f'rep{version}', r)
            self.assertTrue(any(status=='FAIL' and what.endswith(': stamped run price pin')
                                and detail=='snapshot sha256 mismatch' for status, what, detail in r.rows), r.rows)

    def test_expected_original_and_flash_pins_reject_swaps_and_missing_sha(self):
        for tool, model, version in (('codex', 'gpt-6-luna', 11), ('opencode', 'glm-5.3-flash', 13)):
            d = self.base/f'editions/2026-10/{tool}/{model}/orgsync/rep1'
            d.mkdir(parents=True)
            for source_version in (11, 13):
                source = (ROOT/'evidence/prices'/f'{RUN_TABLE_SHA}.yaml' if source_version==11
                          else self.base/'prices.yaml')
                shutil.copy(source, d/'prices.snapshot.yaml')
                write_log(d/'session.jsonl', [dict(type='event_msg', payload=dict(
                    type='token_count', info=dict(total_token_usage=dict(input_tokens=100, output_tokens=0))))])
                result = dict(tool=tool, model_key=model, cost_list_usd=0)
                if tool == 'opencode':
                    message = oc_message(); message['modelID'] = model
                    write_log(d/'session.jsonl', [dict(type='opencode_message', id='m', session_id='s', data=message)])
                    result['tokens'] = sum_native_jsonl(d/'session.jsonl', tool)
                (d/'session.jsonl.sha256').write_text(hashlib.sha256((d/'session.jsonl').read_bytes()).hexdigest())
                # Zero published cost keeps pricing separate from the expected-pin assertion.
                (d/'result.json').write_text(json.dumps(result))
                stamp = dict(prices_yaml_version=source_version,
                             prices_yaml_sha256=hashlib.sha256(source.read_bytes()).hexdigest())
                (d/'stamp.json').write_text(json.dumps(stamp))
                r=Report();verify_run(str(d),'rep1',r)
                self.assertIn(('PASS' if source_version==version else 'FAIL',
                               f'{os.path.relpath(d)}: expected run price pin', ''),r.rows)
            stamp.pop('prices_yaml_sha256')
            (d/'stamp.json').write_text(json.dumps(stamp))
            r=Report();verify_run(str(d),'rep1',r)
            self.assertTrue(any(st=='FAIL' and what.endswith(': stamped run price pin') for st,what,_ in r.rows))
            self.assertTrue(any(st=='FAIL' and what.endswith(': expected run price pin') for st,what,_ in r.rows))
            # A missing expected pin is also a refusal, even with a valid bundle stamp.
            stamp['prices_yaml_sha256'] = hashlib.sha256((d/'prices.snapshot.yaml').read_bytes()).hexdigest()
            (d/'stamp.json').write_text(json.dumps(stamp))
            manifest = json.loads(self.manifest_path.read_text())
            target = manifest if tool=='codex' else manifest['late_admissions'][0]
            target.pop('prices_yaml_sha256')
            self.manifest_path.write_text(json.dumps(manifest))
            r=Report();verify_run(str(d),'rep1',r)
            self.assertTrue(any(st=='FAIL' and what.endswith(': expected run price pin') for st,what,_ in r.rows))
            self.manifest_path.write_text(json.dumps(self.manifest))
class EditionOneVerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name)
        source = ROOT / 'codex/gpt-5.6-luna/baseline/rep1'
        self.bundle = self.p / 'codex/gpt-5.6-luna/baseline/rep1'
        self.bundle.mkdir(parents=True)
        for name in ('result.json', 'stamp.json', 'session.jsonl',
                     'session.jsonl.sha256', 'prices.snapshot.yaml'):
            shutil.copyfile(source / name, self.bundle / name)
        shutil.copytree(source / 'submission', self.bundle / 'submission')
        self.manifest = json.loads((ROOT / 'editions/2026-07/MANIFEST.json').read_text())
        self.manifest['roster'] = {'codex': ['gpt-5.6-luna']}
        self.manifest['scored_models'] = 1
        self.manifest['tasks'] = {'baseline': dict(self.manifest['tasks']['baseline'], reps=1)}
        self.manifest_path = self.p / 'editions/2026-07/MANIFEST.json'
        self.manifest_path.parent.mkdir(parents=True)
        self.manifest_path.write_text(json.dumps(self.manifest))

    def invoke(self, *args, script=None):
        return subprocess.run([sys.executable, str(script or ROOT / 'verify.py'),
                               str(self.p), *args], capture_output=True, text=True)

    def assert_rejudge_rejects_flipped_verdict(self, script=None):
        result = self.invoke('--rejudge', script=script)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('recorded FAIL, re-judged PASS', result.stdout)

    def test_rejudge_flipped_recorded_verdict_negative_control(self):
        positive = self.invoke('--rejudge')
        self.assertEqual(positive.returncode, 0, positive.stdout + positive.stderr)
        self.assertIn('RE-JUDGE 1 bundles · PASS 1 · FAIL 0 · SKIP 0', positive.stdout)
        result_path = self.bundle / 'result.json'
        result = json.loads(result_path.read_text())
        result['outcome'] = 'FAIL'  # the sole planted corruption
        result_path.write_text(json.dumps(result))
        offline = self.invoke()
        self.assertEqual(offline.returncode, 0, offline.stdout + offline.stderr)
        self.assert_rejudge_rejects_flipped_verdict()

        # Kill the comparison in a disposable verifier copy. The SAME
        # negative-control assertion must fail, proving it is not satisfied
        # merely by invoking a judge or checking its exit code/hash.
        mutant_dir = self.p / 'mutant'
        mutant_dir.mkdir()
        for name in ('score.py', 'token_evidence.py', 'acceptance_test.py'):
            shutil.copyfile(ROOT / name, mutant_dir / name)
        source = (ROOT / 'verify.py').read_text()
        comparison = '"PASS" if verdict == recorded else "FAIL"'
        self.assertEqual(source.count(comparison), 1)
        mutant = mutant_dir / 'verify.py'
        mutant.write_text(source.replace(comparison, '"PASS"'))
        with self.assertRaises(AssertionError):
            self.assert_rejudge_rejects_flipped_verdict(script=mutant)

    def test_manifest_hash_mismatch_exits_nonzero(self):
        for field in ('prompt_sha256', 'judge_sha256', 'prices_yaml_sha256'):
            with self.subTest(field=field):
                changed = json.loads(json.dumps(self.manifest))
                owner = changed if field == 'prices_yaml_sha256' else changed['tasks']['baseline']
                owner[field] = '0' * 64
                self.manifest_path.write_text(json.dumps(changed))
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('manifest hashes equal bundle stamp', result.stdout)

    def judge_fixture(self, body, outcome='PASS'):
        judge = self.p / 'judge.py'
        judge.write_text(body)
        (self.bundle / 'stamp.json').write_text(json.dumps(dict(
            judge='judge.py', judge_sha256=hashlib.sha256(judge.read_bytes()).hexdigest())))
        (self.bundle / 'result.json').write_text(json.dumps(dict(outcome=outcome)))
        return judge

    def test_rejudge_requires_verdict_line_not_just_zero_exit(self):
        self.judge_fixture('print("everything looks fine")\n')
        report = Report(); rejudge_run(self.bundle, report, self.p)
        self.assertEqual(report.rows[0][0], 'FAIL')
        self.assertIn('verdict line', report.rows[0][2])

    def test_rejudge_missing_and_sealed_judges_skip_loudly(self):
        judge = self.judge_fixture('raise RuntimeError("sealed code executed")\n')
        (self.p / '.export-exclude').write_text('judge.py\n')
        for absent in (False, True):
            if absent: judge.unlink()
            report = Report(); rejudge_run(self.bundle, report, self.p)
            self.assertEqual(report.rows[0][0], 'SKIP')
            self.assertIn('NOT re-judged', report.rows[0][2])

    def test_rejudge_runs_on_copy_and_refuses_changed_judge(self):
        judge = self.judge_fixture('''import os
from pathlib import Path
(Path(os.environ['TARGET_DIR']) / 'duration.py').write_text('changed only in scratch')
print('ACCEPTANCE_RESULT pass=True failures=0 errors=0 tests=1')
''')
        original = (self.bundle / 'submission/duration.py').read_bytes()
        report = Report(); rejudge_run(self.bundle, report, self.p)
        self.assertEqual(report.rows[0][0], 'PASS', report.rows)
        self.assertEqual((self.bundle / 'submission/duration.py').read_bytes(), original)
        judge.write_text('raise RuntimeError("changed judge executed")\n')
        report = Report(); rejudge_run(self.bundle, report, self.p)
        self.assertEqual(report.rows[0][0], 'FAIL')
        self.assertIn('SHA-256', report.rows[0][2])

    def orgsync_bundle(self):
        source = ROOT / 'codex/gpt-5.6-luna/orgsync/rep1'
        bundle = self.p / 'codex/gpt-5.6-luna/orgsync/rep1'
        bundle.mkdir(parents=True)
        for name in ('result.json', 'stamp.json', 'config.json'):
            shutil.copyfile(source / name, bundle / name)
        for name in ('submission', 'orgsync', 'tests/existing'):
            shutil.copytree(source / name, bundle / name)
        return bundle

    def public_kit_fixture(self):
        bundle = self.orgsync_bundle()
        source = ROOT / 'codex/gpt-5.6-luna/orgsync/rep1'
        for name in ('session.jsonl', 'session.jsonl.sha256', 'prices.snapshot.yaml'):
            shutil.copyfile(source / name, bundle / name)
        for name in ('verify.py', 'edition_report.py', 'export.py', 'score.py', 'token_evidence.py', 'redact.py',
                     'aggregate.py', 'run_audit.py', 'preflight_check.py', 'control/test_harness.py',
                     'TASKS.md', 'PROTOCOL.md', 'LICENSE', 'LICENSE-DATA', '.export-exclude',
                     'editions/2026-07/REPORT.md', 'editions/2026-07/metrics.csv',
                     'acceptance_test.py', 'acceptance_orgsync.py'):
            target = self.p / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        for name in ('fixtures/orgsync', 'tasks/orgsync-skeleton'):
            shutil.copytree(ROOT / name, self.p / name)
        data = list(rows(self.p, '2026-07'))
        for row in data:
            for name in (f"evidence/prompts/{row['prompt_file_sha256']}.md",
                         f"evidence/prices/{row['prices_yaml_sha256']}.yaml"):
                target = self.p / name; target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target)
        self.manifest['tasks']['orgsync'] = dict(
            json.loads((ROOT / 'editions/2026-07/MANIFEST.json').read_text())['tasks']['orgsync'], reps=1)
        self.manifest_path.write_text(json.dumps(self.manifest))
        for task in ('baseline', 'orgsync', 'cronspec'):
            target = self.p / 'prompts' / (task + '.md'); target.parent.mkdir(exist_ok=True)
            shutil.copyfile(ROOT / 'prompts' / (task + '.md'), target)
        return data, bundle

    def test_export_kit_real_write_preserves_archives_and_redacts(self):
        from verify import ORGSYNC_ARCHIVED_FILES
        data, orgsync = self.public_kit_fixture()
        nested = self.bundle / 'submission/data'; nested.mkdir()
        for name in ('result.json', 'session.jsonl', 'session.jsonl.sha256'):
            (nested / name).write_bytes(b'' if name == 'session.jsonl' else b'archived nested bytes\n')
        out = self.p / 'publish'; out.mkdir()
        files, _ = kit_plan(self.p, data, '2026-07')
        for name in ('edition_report.py', 'editions/2026-07/metrics.csv'):
            self.assertIn(name, files)
        export_kit(self.p, out, data, '2026-07')
        for name in ('edition_report.py', 'editions/2026-07/metrics.csv'):
            self.assertEqual((out / name).read_bytes(), (self.p / name).read_bytes())
        # Run the exported derivation path, including verify.py's shared parser.
        checked = subprocess.run([sys.executable, '-c',
            "from pathlib import Path; from edition_report import load_metrics; "
            "_, metrics=load_metrics(Path('.')); assert len(metrics)==2"],
            cwd=out, capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        for row in data:
            source = self.p / row['bundle']; target = out / row['bundle']
            for path in (source / 'submission').rglob('*'):
                if path.is_file():
                    self.assertEqual(path.read_bytes(), (target / path.relative_to(source)).read_bytes())
            self.assertNotEqual((source / 'session.jsonl').read_bytes(), (target / 'session.jsonl').read_bytes())
            self.assertEqual(scan_banned(target / 'session.jsonl'), [])
            res = json.loads((target / 'result.json').read_text())
            self.assertFalse(any(isinstance(v, str) and v.startswith('/') for v in res.values()))
        receipts = [json.loads(line) for line in (out / 'EVIDENCE-MANIFEST.jsonl').read_text().splitlines()]
        self.assertEqual({r['bundle'] for r in receipts}, {r['bundle'] for r in data})
        self.assertEqual(len(receipts), len(data))
        for name in ORGSYNC_ARCHIVED_FILES:
            self.assertTrue((out / orgsync.relative_to(self.p) / name).is_file(), name)

    def test_submission_symlink_skips_judge_and_refuses_export(self):
        data, other = self.public_kit_fixture()
        (self.bundle / 'submission/foreign.py').symlink_to(other / 'orgsync/sync.py')
        with patch('verify.subprocess.run') as execute:
            report = Report(); rejudge_run(self.bundle, report, ROOT)
            execute.assert_not_called()
        self.assertEqual(report.rows[0][0], 'SKIP', report.rows)
        self.assertIn('symlink', report.rows[0][2])
        out = self.p / 'public'
        with self.assertRaisesRegex(ValueError, 'symlink'):
            export_kit(self.p, out, data, '2026-07')
        self.assertFalse(out.exists())

    def combined_kit_fixture(self):
        self.public_kit_fixture()
        edition = self.p / 'editions/2026-10'
        edition.mkdir(parents=True, exist_ok=True)
        for name in ('REPORT.md', 'MANIFEST.json', 'metrics.csv',
                     'EVIDENCE-MANIFEST.jsonl', 'CODEX-RESPONSE-AUDIT.json'):
            shutil.copyfile(ROOT / 'editions/2026-10' / name, edition / name)
        shutil.copyfile(ROOT / 'prices.yaml', self.p / 'prices.yaml')
        shutil.copytree(ROOT / 'editions/2026-10/evidence', edition / 'evidence')
        name = 'codex/gpt-6-luna/orgsync/rep1'
        bundle = edition / name
        shutil.copytree(ROOT / 'editions/2026-10' / name, bundle)
        manifest_path = edition / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['roster'] = {'codex': ['gpt-6-luna']}
        manifest['scored_models'] = 1
        manifest['tasks']['orgsync']['reps_run'] = 1
        manifest_path.write_text(json.dumps(manifest))
        return kit_rows(self.p, 'all'), bundle

    def test_combined_kit_real_build_contains_both_editions(self):
        data, october = self.combined_kit_fixture()
        out = self.p / 'publish'; out.mkdir()
        export_kit(self.p, out, data, 'all', dry_run=True)
        self.assertEqual(list(out.iterdir()), [])
        export_kit(self.p, out, data, 'all')
        july = self.bundle.relative_to(self.p)
        for name in (july / 'submission/duration.py',
                     Path('editions/2026-07') / july / 'submission/duration.py',
                     october.relative_to(self.p) / 'submission/test_orgsync.py',
                     Path('prices.yaml')):
            self.assertTrue((out / name).is_file(), str(name))
        for ed in ('2026-07', '2026-10'):
            for name in ('REPORT.md', 'MANIFEST.json', 'metrics.csv'):
                self.assertEqual((out / 'editions' / ed / name).read_bytes(),
                                 (self.p / 'editions' / ed / name).read_bytes())
        published = [json.loads(line) for line in (out / 'results.jsonl').read_text().splitlines()]
        self.assertEqual({r['edition'] for r in published}, {'2026-07', '2026-10'})
        self.assertEqual(len(published), len(data))
        self.assertTrue(all((out / r['bundle'] / 'result.json').is_file() for r in published))
        self.assertEqual([p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()
                          and scan_banned(p)], [])

    def test_combined_kit_refuses_sealed_path_inside_october_bundle(self):
        data, october = self.combined_kit_fixture()
        sealed = october / 'submission/prompts/cutover.md'
        sealed.parent.mkdir(); sealed.write_text('sealed sentinel')
        for dry_run in (True, False):
            with self.assertRaisesRegex(ValueError, 'sealed path'):
                with tempfile.TemporaryDirectory() as tmp:
                    export_kit(self.p, Path(tmp), data, 'all', dry_run=dry_run)

    def test_combined_kit_refuses_missing_boundary_and_private_bundle(self):
        data, _ = self.combined_kit_fixture()
        for name in ('publish/edition2-raw/codex/model/orgsync/rep1', '../foreign/rep1'):
            changed = [dict(row) for row in data]; changed[-1]['bundle'] = name
            with self.assertRaisesRegex(ValueError, 'private export path|invalid artifact path'):
                kit_plan(self.p, changed, 'all')
        (self.p / '.export-exclude').unlink()
        with self.assertRaisesRegex(ValueError, '.export-exclude absent'):
            kit_plan(self.p, data, 'all')

    def test_combined_kit_refuses_home_path_and_raw_nested_log_before_writing(self):
        data, october = self.combined_kit_fixture()
        leak = october / 'submission/README.md'
        original = leak.read_bytes() if leak.exists() else None
        leak.write_text('/' + 'Users/private-operator/work')
        with self.assertRaisesRegex(ValueError, 'redaction leak'):
            export_kit(self.p, self.p / 'publish', data, 'all')
        self.assertFalse((self.p / 'publish').exists())
        if original is None:
            leak.unlink()
        else:
            leak.write_bytes(original)
        (october / 'submission/session.jsonl').write_text(json.dumps(dict(type='response_item', payload=dict(role='user'))) + '\n')
        with self.assertRaisesRegex(ValueError, 'unredacted session log'):
            export_kit(self.p, self.p / 'publish', data, 'all')
        self.assertFalse((self.p / 'publish').exists())

    def test_combined_kit_october_original_receipt(self):
        data, october = self.combined_kit_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            export_kit(self.p, out, data, 'all')
            receipt = next(json.loads(line) for line in
                           (out / 'EVIDENCE-MANIFEST.jsonl').read_text().splitlines()
                           if json.loads(line)['bundle'] == october.relative_to(self.p).as_posix())
            committed = next(json.loads(line) for line in
                             (october.parents[3] / 'EVIDENCE-MANIFEST.jsonl').read_text().splitlines()
                             if json.loads(line)['bundle'] == 'codex/gpt-6-luna/orgsync/rep1')
            self.assertTrue(receipt['original_sha256'].startswith('a167ecfe'), receipt)
            self.assertTrue(receipt['redacted_sha256'].startswith('c14695e2'), receipt)
            self.assertEqual(receipt['original_bytes'], committed['original_bytes'])
            for name in ('EVIDENCE-MANIFEST.jsonl', 'CODEX-RESPONSE-AUDIT.json'):
                self.assertEqual((out / 'editions/2026-10' / name).read_bytes(),
                                 (self.p / 'editions/2026-10' / name).read_bytes())
                self.assertEqual(scan_banned(out / 'editions/2026-10' / name), [])

    def test_combined_kit_missing_original_receipt_refused(self):
        data, october = self.combined_kit_fixture()
        receipts = october.parents[3] / 'EVIDENCE-MANIFEST.jsonl'
        receipts.write_text('')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'missing original receipt'):
                export_kit(self.p, Path(tmp), data, 'all')
        receipts.unlink()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'missing original receipt'):
                export_kit(self.p, Path(tmp), data, 'all')

    def test_combined_kit_refuses_renamed_sealed_content(self):
        data, october = self.combined_kit_fixture()
        sealed = self.p / 'prompts/cutover.md'
        sealed.parent.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / 'prompts/cutover.md', sealed)
        shutil.copyfile(sealed, october / 'submission/innocent.md')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'sealed content'):
                export_kit(self.p, Path(tmp), data, 'all')

    def test_combined_kit_refuses_symlinked_bundle_directory(self):
        data, october = self.combined_kit_fixture()
        moved = october.with_name('saved'); october.rename(moved)
        october.symlink_to(moved, target_is_directory=True)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'symlink'):
                export_kit(self.p, Path(tmp), data, 'all')

    def result_value_leak(self, dry_run):
        data, october = self.combined_kit_fixture()
        result = october / 'result.json'
        res = json.loads(result.read_text()); res['diagnostic'] = 'prefix /' + 'Users/x/y suffix'
        result.write_text(json.dumps(res))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'redaction leak'):
                export_kit(self.p, Path(tmp), data, 'all', dry_run=dry_run)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_combined_kit_refuses_result_value_leak(self):
        self.result_value_leak(False)

    def test_combined_kit_dry_run_refuses_result_value_leak(self):
        self.result_value_leak(True)

    def test_combined_kit_report_price_path_and_pin_refused(self):
        data, _ = self.combined_kit_fixture()
        path = self.p / 'editions/2026-10/MANIFEST.json'
        manifest = json.loads(path.read_text())
        for field, value, message in (('path', 'LICENSE', 'only prices.yaml'),
                                      ('sha256', '0' * 64, 'sha256 mismatch'),
                                      ('version', 999, 'version mismatch')):
            changed = json.loads(json.dumps(manifest)); changed['report_rate_table'][field] = value
            path.write_text(json.dumps(changed))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                kit_plan(self.p, data, 'all')

    def test_combined_kit_refuses_casefolded_private_path(self):
        data, october = self.combined_kit_fixture()
        private = october / 'submission/Publish'; private.mkdir()
        (private / 'x.md').write_text('private sentinel')
        with self.assertRaisesRegex(ValueError, 'private export path'):
            kit_plan(self.p, data, 'all')

    def test_combined_kit_refuses_sealed_task_names(self):
        data, _ = self.combined_kit_fixture()
        for field, value in (('task', 'cutover'), ('prompt', 'foreman'),
                             ('prompt', 'prompts/cutover.md'),
                             ('bundle', 'editions/2026-10/codex/model/orgsync-v2/rep1')):
            changed = [dict(row) for row in data]; changed[-1][field] = value
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, 'sealed task'):
                kit_plan(self.p, changed, 'all')

    def test_combined_kit_refuses_unrevealed_prompt_bytes(self):
        data, october = self.combined_kit_fixture()
        payload = (ROOT / 'prompts/cutover.md').read_bytes() + b'!'
        digest = hashlib.sha256(payload).hexdigest()
        evidence = october.parents[3] / 'evidence/prompts' / (digest + '.md')
        evidence.write_bytes(payload)
        data[-1]['prompt_file_sha256'] = digest
        with self.assertRaisesRegex(ValueError, 'revealed prompt'):
            kit_plan(self.p, data, 'all')
        # A digest-named copy of a revealed prompt must also match its filename.
        data[-1]['prompt_file_sha256'] = hashlib.sha256((ROOT / 'prompts/orgsync.md').read_bytes()).hexdigest()
        (evidence.parent / (data[-1]['prompt_file_sha256'] + '.md')).write_bytes(payload)
        with self.assertRaisesRegex(ValueError, 'revealed prompt'):
            kit_plan(self.p, data, 'all')

    def test_combined_kit_consistency_refusals(self):
        data, october = self.combined_kit_fixture()
        changed = [dict(row) for row in data]; changed[-1]['edition'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'unknown bundle edition'):
            kit_plan(self.p, changed, 'all')
        manifest = october.parents[3] / 'MANIFEST.json'
        obj = json.loads(manifest.read_text()); obj['edition'] = '2026-07'
        manifest.write_text(json.dumps(obj))
        with self.assertRaisesRegex(ValueError, 'manifest edition mismatch'):
            kit_plan(self.p, data, 'all')
        shutil.rmtree(october.parents[2])
        with self.assertRaisesRegex(ValueError, 'no run bundles for 2026-10'):
            kit_rows(self.p, 'all')
        result = subprocess.run([sys.executable, str(ROOT / 'export.py'), '--edition', 'all'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--edition all requires --out', result.stderr)

    def test_combined_kit_refuses_unreadable_result_count(self):
        data, october = self.combined_kit_fixture()
        shutil.copytree(october, october.with_name('rep2'))
        manifest_path = october.parents[3] / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text()); manifest['tasks']['orgsync']['reps_run'] = 2
        manifest_path.write_text(json.dumps(manifest))
        (october / 'result.json').write_text('{broken')
        with self.assertRaisesRegex(ValueError, 'bundle count differs from manifest'):
            export_kit(self.p, self.p / 'publish', kit_rows(self.p, 'all'), 'all')

    def test_combined_kit_ordinary_log_and_session_named_code(self):
        data, october = self.combined_kit_fixture()
        (october / 'submission/own-tests.log').write_text('ordinary test output\n')
        (october / 'submission/session_store.py').write_text('session_store = {}\n')
        with tempfile.TemporaryDirectory() as tmp:
            export_kit(self.p, Path(tmp), data, 'all')
            for name in ('own-tests.log', 'session_store.py'):
                self.assertEqual((Path(tmp) / october.relative_to(self.p) / 'submission' / name).read_bytes(),
                                 (october / 'submission' / name).read_bytes())
        (october / 'submission/session.jsonl').write_text(
            json.dumps(dict(type='response_item', payload=dict(role='user'))) + '\n')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'unredacted session log'):
                export_kit(self.p, Path(tmp), data, 'all')

    def verification_kit_fixture(self):
        _, october = self.combined_kit_fixture()
        # A complete small panel can exercise the shipped verifier and report
        # derivation, including July's one repeated usage snapshot.
        shutil.rmtree(self.p / 'codex')
        july_manifest = json.loads((ROOT / 'editions/2026-07/MANIFEST.json').read_text())
        july_manifest['roster'] = {'codex': ['gpt-5.6-terra']}
        july_manifest['scored_models'] = 1
        july_manifest['tasks'] = {task: july_manifest['tasks'][task] for task in ('cronspec', 'orgsync')}
        self.manifest_path.write_text(json.dumps(july_manifest))
        for task in july_manifest['tasks']:
            for rep in (1, 2, 3):
                name = f'codex/gpt-5.6-terra/{task}/rep{rep}'
                shutil.copytree(ROOT / name, self.p / name)
                row = next(rows(self.p, '2026-07')) if task == 'cronspec' and rep == 1 else None
                if row:
                    name = f"evidence/prompts/{row['prompt_file_sha256']}.md"
                    shutil.copyfile(ROOT / name, self.p / name)
        shutil.copyfile(ROOT / 'acceptance_cronspec.py', self.p / 'acceptance_cronspec.py')
        edition = october.parents[3]
        for rep in (2, 3):
            name = f'codex/gpt-6-luna/orgsync/rep{rep}'
            shutil.copytree(ROOT / 'editions/2026-10' / name, edition / name)
        manifest_path = edition / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_text()); manifest['tasks']['orgsync']['reps_run'] = 3
        manifest_path.write_text(json.dumps(manifest))
        # Fixture-only report describes this small panel; approved repo reports
        # are never regenerated or edited.
        data = list(rows(self.p, '2026-07'))
        total = sum(row['cost_list_usd'] for row in data)
        text = f'**6 runs · ${total:.4f} total · 6 accepted / 0 not accepted**\n'
        from verify import TASK_WEIGHT
        for task in july_manifest['tasks']:
            cost = sum(row['cost_list_usd'] for row in data if row['task'] == task)
            text += (f'### `{task}`\n| model | E[$/accepted] | TIER |\n|---|---|---|\n'
                     f'| gpt-5.6-terra | ${cost / 3:.4f} | {TASK_WEIGHT[task] * 3000 / cost:,.0f} |\n')
        text += '\n## What this edition does and does not show\n'
        (self.p / 'editions/2026-07/REPORT.md').write_text(text)
        # Only generated fixture tables and its headline are needed here.
        (edition / 'REPORT.md').write_text(
            '**2026-10 · one model · one task · 3 runs · $0.0000 at list rates · 3 accepted / 0 not**\n'
            + '\n'.join(f'<!-- table:{name} -->\n\n<!-- /table:{name} -->'
                         for name in ('results', 'how-models-worked', 'per-run', 'token-totals'))
            + '\n' + '\n'.join(f'<!-- figure:{name} -->' for name in
                                   ('cost-per-accepted', 'model-calls', 'wall-time', 'tokens')))
        for root in (self.p, edition):
            generated = subprocess.run([sys.executable, str(self.p / 'edition_report.py'), str(root)],
                                       capture_output=True, text=True)
            self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
        output = tempfile.TemporaryDirectory(); self.addCleanup(output.cleanup)
        out = Path(output.name)
        export_kit(self.p, out, kit_rows(self.p, 'all'), 'all')
        return out

    def test_combined_kit_own_verifier_passes_both_editions(self):
        out = self.verification_kit_fixture()
        for args in ([], ['editions/2026-10']):
            checked = subprocess.run([sys.executable, 'verify.py', *args], cwd=out,
                                     capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertIn('FAIL 0', checked.stdout)
            self.assertRegex(checked.stdout, r'PASS [1-9][0-9]*')
            self.assertNotIn('[SKIP]', checked.stdout)

    def test_july_redirect_refuses_changed_mirror_byte(self):
        out = self.verification_kit_fixture()
        argv = [sys.executable, 'verify.py', 'editions/2026-07']
        positive = subprocess.run(argv, cwd=out, capture_output=True, text=True)
        self.assertEqual(positive.returncode, 0, positive.stdout + positive.stderr)
        path = out / 'editions/2026-07/codex/gpt-5.6-terra/cronspec/rep1/submission/cron.py'
        original = path.read_bytes()
        path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        checked = subprocess.run(argv, cwd=out, capture_output=True, text=True)
        self.assertNotEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn('[FAIL] July bundle mirror', checked.stdout)

    def test_verify_manifest_search_stops_at_root(self):
        root = self.p / 'verification'
        bundle = root / self.bundle.relative_to(self.p)
        shutil.copytree(self.bundle, bundle)
        (self.p / 'MANIFEST.json').write_text(json.dumps(dict(
            roster={'codex': ['gpt-5.6-luna']}, prices_yaml_sha256='0' * 64)))
        checked = subprocess.run([sys.executable, str(ROOT / 'verify.py'), str(root)],
                                 capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertNotIn('expected run price pin', checked.stdout)

    def test_manifest_panel_reps_and_undeclared_rep_fail(self):
        self.manifest['tasks']['baseline']['reps'] = 2
        self.manifest_path.write_text(json.dumps(self.manifest))
        for extra in (False, True):
            if extra:
                self.manifest['tasks']['baseline']['reps'] = 1
                self.manifest_path.write_text(json.dumps(self.manifest))
                other = self.bundle.with_name('rep2'); shutil.copytree(self.bundle, other)
                result = json.loads((other / 'result.json').read_text()); result['rep'] = 2
                (other / 'result.json').write_text(json.dumps(result))
            result = self.invoke()
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('[FAIL] Edition 1 manifest roster/tasks/reps equal archived panel', result.stdout)

    def test_kit_plan_omits_sealed_judge_and_refuses_injected_path(self):
        from export import refuse_sealed
        data, _ = self.public_kit_fixture()
        judge = data[0]['judge']; (self.p / '.export-exclude').write_text(judge + '\n')
        files, exclusions = kit_plan(self.p, data, '2026-07')
        self.assertNotIn(judge, files)
        with self.assertRaisesRegex(ValueError, 'sealed path'):
            refuse_sealed(files + [judge], exclusions, self.p)
        # Omit only the judge-selection guard; the final plan refusal must bite.
        from verify import is_sealed
        skipped = []
        def omit_selection_guard(name, paths, root=None):
            if name == judge and not skipped:
                skipped.append(name)
                return False
            return is_sealed(name, paths, root)
        with patch('export.is_sealed', side_effect=omit_selection_guard):
            with self.assertRaisesRegex(ValueError, 'sealed path'):
                kit_plan(self.p, data, '2026-07')

    def test_rejudge_zero_tests_pass_refused(self):
        self.judge_fixture("print('ACCEPTANCE_RESULT pass=True failures=0 errors=0 tests=0')\n")
        report = Report(); rejudge_run(self.bundle, report, self.p)
        self.assertEqual(report.rows[0][0], 'FAIL', report.rows)
        self.assertIn('zero tests', report.rows[0][2])

    def test_rejudge_verdict_exit_disagreement_refused(self):
        self.judge_fixture("import sys\nprint('ACCEPTANCE_RESULT pass=True failures=0 errors=0 tests=1')\nsys.exit(1)\n")
        report = Report(); rejudge_run(self.bundle, report, self.p)
        self.assertEqual(report.rows[0][0], 'FAIL', report.rows)
        self.assertIn('disagree', report.rows[0][2])

    def test_judge_path_escape_refused_by_verifier_and_export(self):
        data, _ = self.public_kit_fixture()
        stamp = json.loads((self.bundle / 'stamp.json').read_text()); stamp['judge'] = '../x.py'
        (self.bundle / 'stamp.json').write_text(json.dumps(stamp))
        with patch('verify.subprocess.run') as execute:
            report = Report(); rejudge_run(self.bundle, report, self.p)
            execute.assert_not_called()
        self.assertEqual(report.rows[0][0], 'FAIL', report.rows)
        self.assertIn('escapes', report.rows[0][2])
        for row in data: row['judge'] = '../x.py'
        with self.assertRaisesRegex(ValueError, 'invalid stamped judge path'):
            kit_plan(self.p, data, '2026-07')

    def test_rejudge_environment_is_allowlisted_with_scratch_home(self):
        self.judge_fixture("print('ACCEPTANCE_RESULT pass=True failures=0 errors=0 tests=1')\n")
        def judge(argv, **kwargs):
            env = kwargs['env']
            self.assertEqual(set(env), {'PATH', 'LANG', 'LC_ALL', 'TARGET_DIR', 'PYTHONDONTWRITEBYTECODE', 'HOME'})
            self.assertEqual(Path(env['HOME']), Path(kwargs['cwd']))
            self.assertNotEqual(env['HOME'], os.environ.get('HOME'))
            self.assertTrue(Path(env['HOME']).is_dir())
            return subprocess.CompletedProcess(argv, 0, 'ACCEPTANCE_RESULT pass=True failures=0 errors=0 tests=1\n', '')
        with patch.dict(os.environ, {'PYTHONPATH': 'foreign', 'TIER_PRICES': 'foreign', 'SENTINEL_SECRET': 'private'}), \
             patch('verify.subprocess.run', side_effect=judge):
            report = Report(); rejudge_run(self.bundle, report, self.p)
        self.assertEqual(report.rows[0][0], 'PASS', report.rows)

    def test_sealed_path_aliases_and_content_hash_refused(self):
        from export import refuse_sealed
        data, _ = self.public_kit_fixture()
        (self.p / 'prompts').mkdir(exist_ok=True)
        sealed = self.p / 'prompts/private.md'; sealed.write_text('sealed answer key')
        (self.p / '.export-exclude').write_text('prompts/private.md\n')
        for name in ('./prompts/private.md', 'PROMPTS/PRIVATE.MD', 'prompts/../prompts/private.md'):
            with self.assertRaisesRegex(ValueError, 'sealed path'):
                refuse_sealed([name], ['prompts/private.md'], self.p)
        data[0]['prompt_file_sha256'] = hashlib.sha256(sealed.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'sealed content'):
            kit_plan(self.p, data, '2026-07')

    def test_kit_hash_paths_require_lowercase_sha256(self):
        data, _ = self.public_kit_fixture()
        for field in ('prompt_file_sha256', 'prices_yaml_sha256'):
            original = data[0][field]
            for invalid in ('../private', './' + original, original.upper(), 'a' * 63):
                data[0][field] = invalid
                with self.assertRaisesRegex(ValueError, 'invalid ' + field):
                    kit_plan(self.p, data, '2026-07')
            data[0][field] = original

    def test_rejudge_import_failure_zero_tests_matches_error_counts(self):
        self.judge_fixture("import sys\nprint('ACCEPTANCE_RESULT pass=False failures=0 errors=1 tests=0')\nsys.exit(1)\n", outcome='FAIL')
        result = self.bundle / 'result.json'
        result.write_text(json.dumps(dict(outcome='FAIL', edge_failures='0f+1e')))
        report = Report(); rejudge_run(self.bundle, report, self.p)
        self.assertEqual(report.rows[0][0], 'PASS', report.rows)
        for counts in ('1f+0e', '0f+2e', None):
            result.write_text(json.dumps(dict(outcome='FAIL', edge_failures=counts)))
            report = Report(); rejudge_run(self.bundle, report, self.p)
            self.assertEqual(report.rows[0][0], 'FAIL', report.rows)
            self.assertIn('counts differ', report.rows[0][2])

    def test_kit_counts_only_planned_submission_directories(self):
        data, _ = self.public_kit_fixture()
        shutil.rmtree(self.bundle / 'submission'); (self.bundle / 'submission').mkdir()
        with patch('builtins.print') as announce:
            export_kit(self.p, self.p / 'publish', data, '2026-07', dry_run=True)
        self.assertIn('1 submission directories', announce.call_args[0][0])

    def test_non_edition_one_manifest_check_adds_no_skip(self):
        result = self.bundle / 'result.json'; res = json.loads(result.read_text()); res['edition'] = '2026-10'
        result.write_text(json.dumps(res)); report = Report()
        verify_manifest([result], report, self.p)
        self.assertEqual(report.rows, [])

    def test_score_prices_default_is_portable_and_overridable(self):
        for override in (None, str(self.p / 'override.yaml')):
            env = fixture_env(); env.pop('TIER_PRICES', None)
            if override: env['TIER_PRICES'] = override
            result = subprocess.run([sys.executable, '-c', 'import score; print(score.DEFAULT_PRICES)'],
                                    cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), override or str(ROOT / 'prices.yaml'))

    def test_orgsync_missing_required_archived_file_skips(self):
        bundle = self.orgsync_bundle()
        for name in ('config.json', 'tests/existing/test_http_client.py', 'orgsync/config.py'):
            with self.subTest(path=name):
                path = bundle / name
                original = path.read_bytes()
                path.unlink()
                with patch('verify.subprocess.run') as execute:
                    report = Report(); rejudge_run(bundle, report, ROOT)
                    execute.assert_not_called()
                self.assertEqual(report.rows[0][0], 'SKIP', report.rows)
                self.assertIn(name, report.rows[0][2])
                self.assertIn('NOT re-judged', report.rows[0][2])
                path.write_bytes(original)

    def test_orgsync_target_uses_same_bundle_layout_and_bytes(self):
        bundle = self.orgsync_bundle()
        expected = {p.relative_to(bundle / 'submission').as_posix(): p.read_bytes()
                    for p in (bundle / 'submission').rglob('*') if p.is_file()}
        expected.update({p.relative_to(bundle).as_posix(): p.read_bytes()
                         for name in ('orgsync', 'tests/existing')
                         for p in (bundle / name).rglob('*') if p.is_file()})
        expected['config.json'] = (bundle / 'config.json').read_bytes()
        def judge(argv, **kwargs):
            target = Path(kwargs['env']['TARGET_DIR'])
            self.assertNotEqual(target, bundle)
            actual = {p.relative_to(target).as_posix(): p.read_bytes()
                      for p in target.rglob('*') if p.is_file()}
            self.assertEqual(actual, expected)
            return subprocess.CompletedProcess(argv, 0,
                'ACCEPTANCE_RESULT pass=True failures=0 errors=0 tests=1\n', '')
        with patch('verify.subprocess.run', side_effect=judge):
            report = Report(); rejudge_run(bundle, report, ROOT)
        self.assertEqual(report.rows[0][0], 'PASS', report.rows)

    def test_public_kit_ships_submissions_and_refuses_sealed_paths(self):
        data = list(rows(ROOT, '2026-07'))
        files, exclusions = kit_plan(ROOT, data, '2026-07')
        self.assertEqual(len(data), 63)
        for name in ('edition_report.py', 'editions/2026-07/metrics.csv'):
            self.assertIn(name, files)
        self.assertEqual(sum('/submission/' in name for name in files), 105)
        self.assertTrue(all(any(name.startswith(row['bundle'] + '/submission/')
                                for name in files) for row in data))
        from export import refuse_sealed
        for name in exclusions:
            with self.assertRaisesRegex(ValueError, 'sealed path'):
                refuse_sealed(files + [name + '/fixture.py'], exclusions)
        # Contaminated output is refused before any writes.
        out = self.p / 'public'
        (out / 'prompts').mkdir(parents=True)
        (out / 'prompts/cutover.md').write_text('sentinel')
        with self.assertRaisesRegex(ValueError, 'sealed path'):
            export_kit(ROOT, out, data, '2026-07')
        self.assertEqual((out / 'prompts/cutover.md').read_text(), 'sentinel')


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        for name in ('run.sh', 'edition.sh', 'run_audit.py', 'score.py', 'token_evidence.py', 'TIER_PIN', 'acceptance_orgsync.py'):
            shutil.copy(ROOT/name, self.base/name)
        for directory in ('prompts', 'editions/2026-10', 'evidence/prices'):
            shutil.copytree(ROOT/directory, self.base/directory,
                            ignore=shutil.ignore_patterns('claude-code', 'codex', 'opencode', 'muse'))
        use_run_table(self.base)

    def test_closed_edition_refuses_dry_run(self):
        env=fixture_env();env.update(EDITION='2026-10',DRY_RUN='1',ALLOW_EXPENSIVE='1')
        p=subprocess.run(['bash',str(ROOT/'run.sh'),'codex','gpt-6.1-sol','orgsync'],env=env,capture_output=True,text=True)
        self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertIn('FATAL: pinned prices hash mismatch',p.stderr)
        self.assertNotIn('command:',p.stdout)

    def invoke(self, tool, model, **extra):
        env=fixture_env();env.update(EDITION='2026-10',DRY_RUN='1',ALLOW_EXPENSIVE='1');env.update(extra)
        return subprocess.run(['bash',str(self.base/'run.sh'),tool,model,'orgsync'],env=env,capture_output=True,text=True)

    def test_dry_runs_and_old_mapping(self):
        for tool,model in [('opencode','glm-5.3'),('muse','muse-spark-1.3')]:
            p=self.invoke(tool,model);self.assertEqual(p.returncode,0,p.stderr)
            self.assertIn('command: '+tool,p.stdout)
            self.assertNotIn('--no-session-log',p.stdout)
            self.assertNotIn('--yolo',p.stdout)
        p=self.invoke('claude-code','haiku',EDITION='2026-07')
        self.assertEqual(p.returncode,0,p.stderr);self.assertIn('claude-haiku-4-5',p.stdout)
        p=self.invoke('opencode','glm-5.3',OPENCODE_PROVIDER='ollama-cloud')
        self.assertIn('ollama-cloud/glm-5.3',p.stdout)

    def test_new_legs_with_offline_stub_clis_record_nonzero_exit(self):
        # Run the complete harness against disposable local stores. Stubs are
        # the only opencode/muse executables on this PATH; never a real model.
        with tempfile.TemporaryDirectory(prefix='harness-fixture-', dir=ROOT) as tmp:
            base=Path(tmp);bin_dir=base/'bin';bin_dir.mkdir()
            for f in ('run.sh','edition.sh','score.py','run_audit.py','token_evidence.py','prices.yaml','TIER_PIN'):
                shutil.copy(ROOT/f,base/f)
            for d in ('prompts','editions/2026-10','evidence/prices'):
                (base/d).mkdir(parents=True,exist_ok=True)
            shutil.copy(ROOT/'prompts/baseline.md',base/'prompts/baseline.md')
            shutil.copy(ROOT/'editions/2026-10/TIER_PIN',base/'editions/2026-10/TIER_PIN')
            shutil.copy(ROOT/'editions/2026-10/MANIFEST.json',base/'editions/2026-10/MANIFEST.json')
            use_run_table(base)
            shutil.copy(ROOT/'acceptance_test.py',base/'acceptance_test.py')
            stub = """#!{python}
import json, os, sqlite3, sys, time
from pathlib import Path
args=sys.argv[1:]
if args==['--version']:
 print('offline stub 1');sys.exit(0)
Path('stub-env-names.json').write_text(json.dumps(sorted(os.environ)))
assert os.environ['TERM']=='dumb' and os.environ['NO_COLOR']=='1'
settings=json.loads((Path(sys.argv[0]).parent/'stub-control.json').read_text())
time.sleep(float(settings.get('sleep',0)))
def value(flag):return args[args.index(flag)+1]
cwd=os.getcwd()
if Path(sys.argv[0]).name=='opencode':
 assert (Path(os.environ['XDG_DATA_HOME'])/'opencode/auth.json').is_file()
 with sqlite3.connect(settings['opencode_db']) as c:
  c.execute('INSERT INTO session VALUES(?,?,?,?)',('session-stub',cwd,value('--title'),None))
  m={oc}
  m['path']['cwd']=cwd
  c.execute('INSERT INTO message VALUES(?,?,?)',('message-stub','session-stub',json.dumps(m)))
else:
 assert value('--provider')=='meta' and value('--sandbox-network')=='enabled'
 assert '--trust-workspace' not in args
 sid=value('--session-id')
 p=Path(settings['muse_sessions'])/'2026/10/12'/sid/'session.jsonl'
 p.parent.mkdir(parents=True)
 meta=dict(id='meta',stream=dict(kind='session',id=sid),payload=dict(kind='metadata',record=dict(workspace_root=cwd)))
 agent={muse}
 agent['stream']['id']=sid
 p.write_text(json.dumps(meta)+'\\n'+json.dumps(agent)+'\\n')
sys.exit(7)
""".format(python=sys.executable,oc=repr(oc_message()),muse=repr(muse_event()))
            for tool in ('opencode','muse'):
                cli=bin_dir/tool;cli.write_text(stub);cli.chmod(0o755)
            db=base/'opencode.db'
            with sqlite3.connect(db) as c:
                c.executescript('CREATE TABLE session(id TEXT, directory TEXT, title TEXT, parent_id TEXT);'
                    'CREATE TABLE message(id TEXT, session_id TEXT, data TEXT);'
                    'CREATE TABLE part(id TEXT, session_id TEXT, data TEXT);')
            env=fixture_env();env.update(EDITION='2026-10',DRY_RUN='0',ALLOW_EXPENSIVE='1',
                OPENCODE_DB=str(db),MUSE_SESSIONS=str(base/'muse-store'),AGENT_TIMEOUT='10',
                PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],HOME=str(base/'operator-home'))
            env.update(polluted_session_env())
            auth=base/'operator-home/.local/share/opencode/auth.json';auth.parent.mkdir(parents=True)
            auth.write_text('{"zai-coding-plan":{"key":"offline-credential"}}')
            settings=dict(opencode_db=str(db),muse_sessions=str(base/'muse-store'))
            for tool,model in (('opencode','glm-5.3'),('muse','muse-spark-1.3')):
                (bin_dir/'stub-control.json').write_text(json.dumps(settings))
                argv=['bash',str(base/'run.sh'),tool,model,'baseline','1']
                p=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=20)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                d=base/'editions/2026-10'/tool/model/'baseline/rep1'
                r=json.loads((d/'result.json').read_text());st=json.loads((d/'stamp.json').read_text())
                self.assertEqual(r['edition'],'2026-10');self.assertEqual(st['edition'],'2026-10')
                self.assertEqual(r['agent_exit'],7,p.stdout+p.stderr+(d/'agent-output.txt').read_text());self.assertEqual(r['capture'],'OK')
                self.assertEqual(r['outcome'],'FAIL');self.assertGreater(r['cost_list_usd'],0)
                self.assertEqual(st['gate_basis'],'allow-expensive-override')
                self.assertTrue(st['allow_expensive']);self.assertEqual(st['clean_config'],tool == 'opencode')
                assert_minimal_environment(self,d,st,tool)
                self.assertFalse(list((base/'.tool-config').glob('*/codex/auth.json')))
                self.assertFalse(list((base/'.tool-config').glob('*/data/opencode/auth.json')))
                self.assertEqual(st['effort'],'not observable')
                self.assertIn('native',st['effort_source_checked'])
                if tool=='opencode':self.assertEqual(st['billing_route'],'zai-coding-plan subscription')
                self.assertTrue((d/'session.jsonl.sha256').is_file())
                again=subprocess.run(argv,env=env,capture_output=True,text=True)
                self.assertEqual(again.returncode,1);self.assertIn('fresh repo',again.stderr)
                timeout_env=env.copy();timeout_env.update(AGENT_TIMEOUT='1')
                (bin_dir/'stub-control.json').write_text(json.dumps(dict(settings,sleep=3)))
                p=subprocess.run(argv[:-1]+['2'],env=timeout_env,capture_output=True,text=True,timeout=20)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                timed=json.loads((d.parent/'rep2/result.json').read_text())
                self.assertTrue(timed['timed_out']);self.assertEqual(timed['outcome'],'FAIL')
                self.assertIsNone(timed['cost_list_usd']);self.assertNotEqual(timed['capture'],'OK')


    def test_fixed_roster_manifest_and_batch_dry_run(self):
        manifest=json.loads((ROOT/'editions/2026-10/MANIFEST.json').read_text())
        late_reps={(entry['tool'],entry['model']):entry['reps'] for entry in manifest['late_admissions']}
        scheduled=[(tool,model,rep) for tool,models in manifest['roster'].items()
                   for model in models for rep in range(1,late_reps.get((tool,model),1)+1)]
        existing=sum((ROOT/'editions/2026-10'/tool/model/f'orgsync/rep{rep}/result.json').is_file()
                     for tool,model,rep in scheduled)
        legs=len(scheduled)-existing
        env=fixture_env();env.update(EDITION='2026-10',DRY_RUN='1',ALLOW_EXPENSIVE='1')
        p=subprocess.run(['bash',str(ROOT/'batch.sh')],env=env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        summary=(f'###### BATCH COMPLETE: {legs} legs, {legs} clean exits, '
                 f'0 non-zero exits, {existing} existing results skipped, ')
        self.assertRegex(p.stdout, '(?m)^'+re.escape(summary)+r'[0-9]+m ######$')
        self.assertEqual(p.stdout.count('command:'),legs)
        for tool,models in manifest['roster'].items():
            for model in models:self.assertIn(f'2026-10/{tool}/{model}/orgsync rep1',p.stdout)
        self.assertEqual(manifest['prices_yaml_version'],11)
        self.assertEqual(manifest['prices_yaml_sha256'],RUN_TABLE_SHA)
        self.assertEqual(hashlib.sha256((ROOT/'evidence/prices'/f'{RUN_TABLE_SHA}.yaml').read_bytes()).hexdigest(),RUN_TABLE_SHA)
        pin=(ROOT/'editions/2026-10/TIER_PIN').read_text()
        self.assertIn('prices_yaml_version: 11',pin)
        self.assertIn(f'prices_yaml_sha256: {RUN_TABLE_SHA}',pin)
        report_table=manifest['report_rate_table']
        self.assertEqual(report_table['path'],'prices.yaml')
        self.assertEqual(report_table['sha256'],hashlib.sha256((ROOT/'prices.yaml').read_bytes()).hexdigest())
        self.assertEqual(report_table['version'],int(re.search(r'^version: (\d+)$',(ROOT/'prices.yaml').read_text(),re.M).group(1)))
        for task,props in manifest['tasks'].items():
            self.assertEqual(props['prompt_sha256'],hashlib.sha256((ROOT/'prompts'/f'{task}.md').read_bytes()).hexdigest())
            self.assertEqual(props['judge_sha256'],hashlib.sha256((ROOT/f'acceptance_{task}.py').read_bytes()).hexdigest())
        env['TASKS']='baseline'
        p=subprocess.run(['bash',str(ROOT/'batch.sh')],env=env,capture_output=True,text=True)
        self.assertEqual(p.returncode,2)


    def test_edition_required_and_export_required(self):
        env=fixture_env();env.pop('EDITION',None);env['DRY_RUN']='1'
        p=subprocess.run(['bash',str(ROOT/'run.sh'),'muse','muse-spark-1.3'],env=env,capture_output=True)
        self.assertNotEqual(p.returncode,0)
        p=subprocess.run([sys.executable,str(ROOT/'export.py')],capture_output=True)
        self.assertEqual(p.returncode,2)
        p=subprocess.run([sys.executable,str(ROOT/'score.py')],capture_output=True)
        self.assertEqual(p.returncode,2)

    def test_dry_run_does_not_invoke_cli(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            for tool in ('opencode','muse'):
                stub=p/tool;stub.write_text('#!/bin/sh\necho PAID_CALL_ATTEMPT >&2\nexit 99\n');stub.chmod(0o755)
                r=self.invoke(tool, 'glm-5.3' if tool=='opencode' else 'muse-spark-1.3', PATH=d+os.pathsep+os.environ['PATH'])
                self.assertEqual(r.returncode,0,r.stderr);self.assertNotIn('PAID_CALL_ATTEMPT',r.stderr)



class InterruptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='interrupt-fixture-', dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        for name in ('run.sh', 'batch.sh', 'edition.sh', 'score.py', 'run_audit.py',
                     'token_evidence.py', 'preflight_check.py', 'prices.yaml',
                     'acceptance_test.py', 'acceptance_orgsync.py'):
            shutil.copy(ROOT/name, self.base/name)
        for directory in ('bin', 'prompts', 'editions/2026-10', 'operator-home/.codex',
                          'operator-home/.local/share/opencode'):
            (self.base/directory).mkdir(parents=True, exist_ok=True)
        for name in ('TIER_PIN', 'MANIFEST.json'):
            shutil.copy(ROOT/'editions/2026-10'/name, self.base/'editions/2026-10'/name)
        use_run_table(self.base)
        for name in ('baseline.md', 'orgsync.md'):
            shutil.copy(ROOT/'prompts'/name, self.base/'prompts'/name)
        home = self.base/'operator-home'
        (home/'.codex/auth.json').write_text('{"fixture":"offline-codex"}')
        (home/'.local/share/opencode/auth.json').write_text(
            '{"zai-coding-plan":{"key":"offline-opencode"}}')
        self.env = fixture_env()
        self.env.update(EDITION='2026-10', DRY_RUN='0', ALLOW_EXPENSIVE='1',
                        HOME=str(home), PATH=str(self.base/'bin')+os.pathsep+os.environ['PATH'],
                        AGENT_TIMEOUT='60')
        # Every vendor executable is a stub, including later batch legs if the
        # regression continues the loop. The receipt is written only mid-leg.
        stub = r'''#!PYTHON
import json, os, signal, subprocess, sys, time
from pathlib import Path
if sys.argv[1:]==['--version']:print('offline interrupt stub');sys.exit(0)
base=Path(sys.argv[0]).parent.parent
child=subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
def stop(sig, frame):
 child.wait(timeout=5)
 sys.exit(128+sig)
signal.signal(signal.SIGTERM, stop)
with (base/'started.jsonl').open('a') as f:
 f.write(json.dumps(dict(pid=os.getpid(), pgid=os.getpgrp(), child=child.pid,
                         cwd=os.getcwd()))+'\n')
time.sleep(60)
'''.replace('PYTHON', sys.executable)
        for name in ('claude', 'codex', 'opencode', 'muse'):
            cli = self.base/'bin'/name; cli.write_text(stub); cli.chmod(0o755)

    def credentials(self):
        return sorted(str(p.relative_to(self.base)) for pattern in
                      ('*/codex/auth.json', '*/data/opencode/auth.json')
                      for p in (self.base/'.tool-config').glob(pattern))

    def group_alive(self, pgid):
        try:
            os.killpg(pgid, 0)
            return True
        except ProcessLookupError:
            return False

    def wait_for(self, predicate, timeout=10):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(.05)
        return False

    def starts(self):
        path = self.base/'started.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def launch(self, argv):
        output = (self.base/'runner-output.txt').open('w')
        self.addCleanup(output.close)
        p = subprocess.Popen(argv, env=self.env, stdout=output, stderr=subprocess.STDOUT,
                             start_new_session=True)
        def cleanup():
            # Always reap even when the unfixed harness leaks its agent. The
            # isolated test session also contains the runner's watchdog.
            try: os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            for receipt in self.starts():
                try: os.killpg(receipt['pgid'], signal.SIGTERM)
                except ProcessLookupError: pass
            for receipt in self.starts():
                if not self.wait_for(lambda: not self.group_alive(receipt['pgid']), 2):
                    try: os.killpg(receipt['pgid'], signal.SIGKILL)
                    except ProcessLookupError: pass
            p.wait(timeout=5)
        self.addCleanup(cleanup)
        return p

    def interrupt_leg(self, sig, tool, model, rep):
        previous = len(self.starts())
        p = self.launch(['bash', str(self.base/'run.sh'), tool, model, 'baseline', str(rep)])
        self.assertTrue(self.wait_for(lambda: len(self.starts()) > previous),
                        (self.base/'runner-output.txt').read_text())
        receipt = self.starts()[-1]
        self.assertNotEqual(receipt['pgid'], p.pid)
        # Both credentials really were copied; deletion checks cannot pass
        # merely because setup never reached the credential-copy step.
        self.assertEqual(len(self.credentials()), 2)
        os.kill(p.pid, sig)
        rc = p.wait(timeout=10)
        dead = self.wait_for(lambda: not self.group_alive(receipt['pgid']), 3)
        results = list((self.base/'editions/2026-10').rglob('result.json'))
        observed = (rc, dead, self.credentials(), [str(r.relative_to(self.base)) for r in results])
        self.assertEqual(observed, (128+sig, True, [], []),
                         f'{signal.Signals(sig).name} receipt: {observed}\n'+
                         (self.base/'runner-output.txt').read_text())

    def test_sigint_stops_agent_group_deletes_auth_and_writes_no_result(self):
        for rep, (tool, model) in enumerate((('codex', 'gpt-6-luna'), ('opencode', 'glm-5.3')), 1):
            with self.subTest(tool=tool):
                self.interrupt_leg(signal.SIGINT, tool, model, rep)

    def test_sigterm_stops_agent_group_deletes_auth_and_writes_no_result(self):
        for rep, (tool, model) in enumerate((('codex', 'gpt-6-luna'), ('opencode', 'glm-5.3')), 1):
            with self.subTest(tool=tool):
                self.interrupt_leg(signal.SIGTERM, tool, model, rep)

    def test_config_setup_failure_deletes_copied_auth(self):
        # Codex auth is copied before malformed OpenCode auth raises in CONFIG.
        (self.base/'operator-home/.local/share/opencode/auth.json').write_text('{invalid')
        p = subprocess.run(['bash', str(self.base/'run.sh'), 'codex', 'gpt-6-luna', 'baseline', '1'],
                           env=self.env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(p.returncode, 0, p.stdout+p.stderr)
        self.assertIn('JSONDecodeError', p.stderr)
        self.assertTrue(list((self.base/'.tool-config').glob('*/codex')))
        self.assertEqual(self.credentials(), [])
        self.assertEqual(self.starts(), [])
        self.assertFalse(list((self.base/'editions/2026-10').rglob('result.json')))

    def test_interrupted_leg_stops_batch_before_next_leg(self):
        write_preflight_record(self.base, self.env)
        p = self.launch(['bash', str(self.base/'batch.sh')])
        self.assertTrue(self.wait_for(lambda: bool(self.starts())),
                        (self.base/'runner-output.txt').read_text())
        # A terminal interrupt reaches batch and run, while the agent is in
        # the separate process group created by run_with_timeout's set -m.
        os.killpg(p.pid, signal.SIGINT)
        stopped = self.wait_for(lambda: p.poll() is not None or len(self.starts()) > 1)
        self.assertTrue(stopped, (self.base/'runner-output.txt').read_text())
        observed = (p.poll(), len(self.starts()))
        self.assertEqual(observed, (130, 1),
                         f'batch receipt: {observed}\n'+(self.base/'runner-output.txt').read_text())


class ReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.p = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def muse_config_fixture(self):
        home=self.p/'home';config=home/'.config/muse';config.mkdir(parents=True)
        env=fixture_env();env.update(HOME=str(home),XDG_CONFIG_HOME=str(home/'.config'))
        # Invalid auth/lock contents prove these are never parsed as settings.
        (config/'auth.json').write_text('auth must never be parsed by this guard')
        (config/'trust.json').write_text('{"trusted_workspaces":["/operator/home"]}')
        for name in ('.auth.json.lock', 'settings.json.lock', '.lock.json', 'notes.txt'):
            (config/name).write_text('not a JSON settings file')
        (config/'settings.json').write_text('{"schema_version":1,"model":"muse-spark-1.3","provider":"meta"}')
        return config, env

    def audit_environment(self, env, tool):
        return subprocess.run([sys.executable,str(ROOT/'run_audit.py'),'environment',tool],env=env,capture_output=True,text=True)

    def test_muse_settings_skips_auth_trust_locks_and_nonjson(self):
        config,env=self.muse_config_fixture()
        result=self.audit_environment(env,'muse')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_muse_settings_refuses_effort_mcp_instructions_only_for_muse(self):
        config,env=self.muse_config_fixture()
        for name, data in [('settings.json',{'reasoning_effort':'max'}),
                           ('other.json',{'mcpServers':{'private':'secret-value'}}),
                           ('instructions.json',{'instructions':'secret-value'})]:
            with self.subTest(name=name):
                p=config/name;p.write_text(json.dumps(data))
                result=self.audit_environment(env,'muse');self.assertNotEqual(result.returncode,0)
                self.assertIn('operator Muse config keys refused',result.stderr)
                self.assertNotIn('secret-value',result.stderr);self.assertNotIn('max',result.stderr)
                for tool in ('claude-code','codex','opencode'):
                    result=self.audit_environment(env,tool)
                    self.assertEqual(result.returncode,0,result.stderr)
                p.unlink()

    def test_muse_malformed_and_unreadable_real_settings_refuse(self):
        config,env=self.muse_config_fixture();settings=config/'settings.json'
        for contents in ('not JSON: secret-value', '[]'):
            settings.write_text(contents)
            result=self.audit_environment(env,'muse')
            self.assertNotEqual(result.returncode,0)
            self.assertIn('cannot be audited by key names',result.stderr)
            self.assertNotIn('secret-value',result.stderr)
        settings.write_text('{}')
        original=Path.read_text
        def unreadable(path, *args, **kwargs):
            if path == settings:raise PermissionError('secret-value')
            return original(path, *args, **kwargs)
        with patch.dict(os.environ,env,clear=True), patch.object(Path,'read_text',unreadable):
            with self.assertRaisesRegex(ValueError,'cannot be audited by key names'):
                check_environment(tool='muse')

    def test_all_formats_void_mcp_and_browser(self):
        for name in ('mcp__private__lookup', 'browser_navigate', 'browser', 'playwright.click'):
            for fmt in ('claude', 'codex', 'opencode', 'muse'):
                o = {'claude':dict(type='assistant',message=dict(content=[dict(type='tool_use',name=name)])),
                     'codex':dict(type='response_item',payload=dict(type='function_call',name=name)),
                     'opencode':dict(type='opencode_part',data=dict(type='tool',tool=name)),
                     'muse':dict(payload=dict(event=dict(kind='tool_started',tool_name=name)))}[fmt]
                path=self.p/'log';write_log(path,[o])
                self.assertEqual(scan_web_tools(path,fmt),(1,[name]),(fmt,name))

    def test_export_legacy_edition_refused_before_overwriting(self):
        d=self.p/'codex/gpt-5.6-sol/baseline/rep1';d.mkdir(parents=True)
        (d/'result.json').write_text(json.dumps(dict(tool='codex',label='gpt-5.6-sol')))
        for name in ('results.csv','results.jsonl'):(self.p/name).write_text('sentinel')
        self.assertEqual(list(rows(self.p,'2026-07'))[0]['edition'],'2026-07')
        p=subprocess.run([sys.executable,str(ROOT/'export.py'),str(self.p),'--edition','2026-10'],capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0);self.assertIn('edition mismatch',p.stderr)
        for name in ('results.csv','results.jsonl'):self.assertEqual((self.p/name).read_text(),'sentinel')

    def test_unknown_cost_is_no_score_in_every_consumer(self):
        runs=[dict(edition='2026-10',tool='codex',label='gpt-6-luna',task='baseline',outcome='PASS',cost_list_usd=1),
              dict(edition='2026-10',tool='codex',label='gpt-6-luna',task='baseline',outcome='FAIL',cost_list_usd=None)]
        stats=cell_stats(runs)
        self.assertIsNone(stats['expected_cost_per_accepted_usd'])
        self.assertIsNone(stats['mean_cost_when_passing_usd'])
        partial=cell_stats(runs+[dict(outcome='PASS',cost_list_usd=2)])
        self.assertIsNone(partial['cost_dispersion_pct'])
        report=Report();verify_cells(runs,report)
        self.assertEqual(report.rows[0][0],'FAIL');self.assertIn('NO SCORE',report.rows[0][1])
        for i,r in enumerate(runs,1):
            d=self.p/f"codex/gpt-6-luna/baseline/rep{i}";d.mkdir(parents=True);(d/'result.json').write_text(json.dumps(r))
        (self.p/'tier-scores').mkdir();(self.p/'tier-scores/baseline.json').write_text(json.dumps(dict(developers=[dict(developer='gpt-6-luna',tier=100,cost_per_point=0.01)])))
        p=subprocess.run([sys.executable,str(ROOT/'master_table.py'),str(self.p)],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        text=(self.p/'MASTER-RESULTS.md').read_text()
        row=next(l for l in text.splitlines() if l.startswith('| gpt-6-luna |'))
        self.assertIn('NO SCORE',row);self.assertNotIn('$1.0000',row);self.assertNotIn('100.0',row)
        self.assertIn("effort scales differ by vendor; each model ran at its CLI's shipped default (stamped)",text)

    def test_muse_redaction_rejects_unknown_stream_and_tool_objects(self):
        o=muse_event();o['stream'].update(text='secret',private=dict(arguments='secret'))
        o['payload']['event'].update(tool={'arguments':'secret'},tool_name=['secret'],name='browser_navigate')
        r=redact_muse('',o)
        self.assertEqual(r['stream'],dict(kind='session',id='parent'))
        self.assertNotIn('tool',r['payload']['event']);self.assertNotIn('tool_name',r['payload']['event'])
        self.assertEqual(r['payload']['event']['name'],'browser_navigate')
        self.assertNotIn('secret',json.dumps(r))

    def test_codex_filters_cwd_before_counting_and_claude_subagents_refused(self):
        marker=self.p/'marker';marker.touch();os.utime(marker,(100,100))
        for name,cwd in (('target','/RUN'),('noise','/OTHER')):
            write_log(self.p/f'rollout-{name}.jsonl',[dict(type='session_meta',payload=dict(cwd=cwd))])
        self.assertEqual(codex_logs(self.p,marker,'/RUN'),[str(self.p/'rollout-target.jsonl')])
        self.assertFalse(claude_subagents(self.p/'project'))
        (self.p/'project/session/subagents').mkdir(parents=True)
        self.assertTrue(claude_subagents(self.p/'project'))
        p=subprocess.run([sys.executable,str(ROOT/'run_audit.py'),'claude-subagents',str(self.p/'project')],capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0);self.assertIn('spend capture refused',p.stderr)

    def test_effort_observation_never_uses_documented_default(self):
        for tool,obj in (('claude-code',dict(type='request',output_config=dict(effort='medium'))),
                         ('codex',dict(type='turn_context',payload=dict(effort='high'))),
                         ('opencode',dict(type='opencode_message',data=dict(reasoningEffort='low'))),
                         ('muse',dict(payload=dict(kind='metadata',record=dict(reasoning_effort='high'))))):
            write_log(self.p/'log',[obj]);r=effort_receipt(self.p/'log',tool)
            self.assertNotEqual(r['effort'],'not observable');self.assertEqual(r['effort_source'],'observed-unsourced')
            self.assertTrue(r['effort_evidence_fields'])
            write_log(self.p/'log',[]);r=effort_receipt(self.p/'log',tool)
            self.assertEqual(r['effort'],'not observable');self.assertTrue(r['effort_source_checked'])
        write_log(self.p/'log',[dict(type='turn_context',payload=dict(effort='low')),dict(type='turn_context',payload=dict(effort='high'))])
        self.assertEqual(effort_receipt(self.p/'log','codex')['effort'],['high','low'])

    def test_sandbox_denial_voids_scorer(self):
        from score import sum_claude_jsonl
        log=self.p/'log'
        write_log(log,[dict(type='assistant',message=dict(id='x',model='claude-haiku-5-5',usage=dict(input_tokens=100,output_tokens=10))),
                       dict(type='tool_result',payload=dict(output='SandboxDenied: network access blocked'))])
        self.assertTrue(sandbox_denials(log))
        p=subprocess.run([sys.executable,str(ROOT/'score.py'),'--edition','2026-10','--format','claude','--log',str(log),'--prices',str(ROOT/'prices.yaml'),'--model-key','claude-haiku-5-5','--label','haiku'],capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0);self.assertIn('VOIDED',p.stderr);self.assertIn('sandbox denial',p.stderr)
        write_log(log,[dict(type='request',arguments=dict(text='sandbox denied')),dict(type='turn_context',payload=dict(sandbox_policy=dict(type='workspace-write')))])
        self.assertEqual(sandbox_denials(log),[])

    def test_capture_failed_partial_and_redacted_log_is_verifiable(self):
        d=self.p/'muse/muse-spark-1.3/baseline/rep1';d.mkdir(parents=True)
        broken=muse_event();broken['payload']['event']['usage']['input_tokens']=-1
        write_log(d/'session.jsonl',[broken]);redact_file(d/'session.jsonl',d/'redacted.jsonl')
        shutil.copy(d/'redacted.jsonl',d/'session.jsonl')
        (d/'session.jsonl.sha256').write_text(hashlib.sha256((d/'session.jsonl').read_bytes()).hexdigest())
        (d/'result.json').write_text(json.dumps(dict(tool='muse',capture='NATIVE_STORE_FAILED',cost_list_usd=None,outcome='FAIL')))
        (d/'stamp.json').write_text(json.dumps(dict(judge='acceptance_test.py',judge_sha256=hashlib.sha256((ROOT/'acceptance_test.py').read_bytes()).hexdigest())))
        shutil.copy(ROOT/'prices.yaml',d/'prices.snapshot.yaml')
        r=Report();verify_run(str(d),'rep1',r)
        self.assertTrue(all(st=='PASS' for st,_,_ in r.rows),r.rows)
        self.assertTrue(any('capture failure recorded' in what for _,what,_ in r.rows))

    def test_ten_model_triggers_with_declared_failure_expansion(self):
        manifest=json.loads((ROOT/'editions/2026-10/MANIFEST.json').read_text())
        self.assertEqual(manifest['tasks']['orgsync']['reps'],1)
        keys=[(t,m,'orgsync') for t,ms in manifest['roster'].items() for m in ms]
        def cells(perfect,partial):
            return {k:cell_stats([dict(outcome='PASS',cost_list_usd=1)] if i<perfect else
                      [dict(outcome='PASS' if i<perfect+partial and r==2 else 'FAIL',cost_list_usd=1) for r in range(3)])
                    for i,k in enumerate(keys)}
        self.assertEqual(saturation_verdict(cells(9,0),'orgsync',manifest)['verdict'],'SATURATED')
        self.assertEqual(saturation_verdict(cells(4,0),'orgsync',manifest)['verdict'],'OVER-HARD')
        self.assertEqual(saturation_verdict(cells(4,1),'orgsync',manifest)['verdict'],'IN-BAND')
        self.assertIn('no verdict',saturation_verdict(dict(list(cells(9,0).items())[:-1]),'orgsync',manifest)['verdict'])
        one_run={k:cell_stats([dict(outcome='PASS',cost_list_usd=1)]) for k in keys}
        self.assertEqual(saturation_verdict(one_run,'orgsync',manifest)['verdict'],'SATURATED')

    def test_prices_sources_no_openai_write_and_haiku_per_request_tier(self):
        from score import price, sum_claude_jsonl
        prices,version=load_prices(ROOT/'prices.yaml');self.assertEqual(version,13)
        self.assertEqual(prices['claude-sonnet-5-5']['cache_read_mult'],.05)
        self.assertEqual(prices['gpt-6.1-sol']['input_per_m'],2)
        self.assertEqual(prices['gpt-6.1-sol']['cache_read_mult'],.05)
        text=(ROOT/'prices.yaml').read_text();manifest=json.loads((ROOT/'editions/2026-10/MANIFEST.json').read_text())
        for models in manifest['roster'].values():
            for model in models:
                line=next(l for l in text.splitlines() if l.startswith('  '+model+':'))
                self.assertIn('source_url: "https://',line)
                self.assertIn('as_of: "'+('2026-10-13' if model=='glm-5.3-flash' else '2026-10-12')+'"',line)
                if 'provider: openai' in line:self.assertNotIn('cache_write_',line)
        write_log(self.p/'log',[dict(type='assistant',message=dict(id='x',usage=dict(input_tokens=100000,output_tokens=10))),dict(type='assistant',message=dict(id='y',usage=dict(input_tokens=100001,output_tokens=10)))])
        t=sum_claude_jsonl(self.p/'log')
        self.assertAlmostEqual(price(t,prices['claude-haiku-5-5'],'claude')[0],(100000*.1+10*.5+100001*.5+10*2.5)/1e6)

    def test_runner_spend_blocking_and_clean_default_flags(self):
        base=self.p/'runner';base.mkdir()
        for name in ('run.sh','edition.sh','run_audit.py','score.py','token_evidence.py',
                     'TIER_PIN','acceptance_test.py','acceptance_orgsync.py'):
            shutil.copy(ROOT/name,base/name)
        for directory in ('prompts','editions/2026-10','evidence/prices'):
            shutil.copytree(ROOT/directory,base/directory,
                            ignore=shutil.ignore_patterns('claude-code','codex','opencode','muse'))
        use_run_table(base)
        env=fixture_env();env.update(EDITION='2026-10',ALLOW_EXPENSIVE='1',DRY_RUN='1',HOME=str(self.p/'operator-home'))
        def run(tool,model,task="orgsync",**patch):
            return subprocess.run(['bash',str(base/'run.sh'),tool,model,task],env=dict(env,**patch),capture_output=True,text=True)
        for tool,model in (('codex','gpt-off-roster'),('opencode','glm-off-roster'),('claude-code','claude-off-roster'),('muse','muse-off-roster')):
            p=run(tool,model);self.assertNotEqual(p.returncode,0);self.assertIn('off-roster',p.stderr)
        p=run('opencode','glm-5.3',DRY_RUN='0',OPENCODE_PROVIDER='ollama-cloud',GATE_CHECK='1')
        self.assertEqual(p.returncode,2);self.assertIn('excluded',p.stderr)
        for tool,model in (('opencode','glm-5.3'),('muse','muse-spark-1.3')):
            p=run(tool,model,EDITION='2026-07',DRY_RUN='0');self.assertEqual(p.returncode,2);self.assertIn('not in Edition 1',p.stderr)
        for key,value in (('CLAUDE_EFFORT','high'),('CLAUDE_CODE_EFFORT_LEVEL','high'),('MAX_THINKING_TOKENS','10000'),('CLAUDE_CODE_EXTRA_BODY','{"output_config":{"effort":"max"}}'),('CODEX_MODEL_REASONING_EFFORT','high'),('OPENCODE_CONFIG_CONTENT','{"agent":{"build":{"reasoningEffort":"high"}}}'),('MUSE_EXPERIMENTAL_FIRST_TURN_MINIMAL_EFFORT','1')):
            p=run('codex','gpt-6-luna',**{key:value});self.assertNotEqual(p.returncode,0);self.assertIn('inherited effort',p.stderr)
        for tool,model,flags in [('claude-code','haiku',['--safe-mode',"--setting-sources ''",'--strict-mcp-config','--disallowedTools Task Agent']),('codex','gpt-6-luna',['--ignore-user-config','--ignore-rules','-c sandbox_workspace_write.network_access=true','network_policy=workspace-write']),('opencode','glm-5.3',['OPENCODE_DISABLE_PROJECT_CONFIG=1','OPENCODE_DISABLE_EXTERNAL_SKILLS=1','OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1']),('muse','muse-spark-1.3',['USER=','LOGNAME=','--no-foreign-personal-context','--disable-reminders','--disable-web-tools','--provider meta','--sandbox-network enabled','inherited trust cannot be disabled'])]:
            p=run(tool,model);self.assertEqual(p.returncode,0,p.stderr)
            for flag in flags:self.assertIn(flag,p.stdout)
            self.assertNotIn('--reasoning-effort',p.stdout)
        for tool,model in (('opencode','glm-5.3'),('muse','muse-spark-1.3')):
            p=run(tool,model,task='baseline',ALLOW_EXPENSIVE='0');self.assertEqual(p.returncode,3);self.assertIn('REFUSED',p.stderr)
            p=run(tool,model,task='baseline');self.assertIn('gate=allow-expensive-override',p.stdout)
        p=run('codex','gpt-6-luna',ALLOW_EXPENSIVE='0');self.assertEqual(p.returncode,3);self.assertIn('anchors require logged',p.stderr)

    def test_batch_anchor_pass_stops_at_one_fail_or_missing_expands_and_skips_existing(self):
        for outcome in ('PASS', 'FAIL', 'missing'):
            base=self.p/outcome;base.mkdir()
            shutil.copy(ROOT/'batch.sh',base/'batch.sh');shutil.copy(ROOT/'edition.sh',base/'edition.sh')
            shutil.copy(ROOT/'preflight_check.py',base/'preflight_check.py')
            stub=base/'run.sh'
            stub.write_text('''#!{python}
import json, sys
from pathlib import Path
base=Path(__file__).parent
tool, model, task, rep=sys.argv[1:]
with (base/'launches').open('a') as f:f.write(' '.join(sys.argv[1:])+'\\n')
if not ({outcome!r}=='missing' and rep=='1'):
 p=base/'editions/2026-10'/tool/model/task/('rep'+rep)/'result.json'
 p.parent.mkdir(parents=True,exist_ok=True)
 assert not p.exists(), 'must never overwrite existing result'
 p.write_text(json.dumps(dict(outcome={outcome!r} if rep=='1' else 'PASS')))
'''.format(python=sys.executable,outcome=outcome));stub.chmod(0o755)
            b=base/'bin';b.mkdir();(b/'sleep').write_text('#!/bin/sh\nexit 0\n');(b/'sleep').chmod(0o755)
            env=fixture_env();env.update(EDITION='2026-10',DRY_RUN='0',ALLOW_EXPENSIVE='1',PATH=str(b)+os.pathsep+os.environ['PATH'])
            write_preflight_record(base, env)
            p=subprocess.run(['bash',str(base/'batch.sh')],env=env,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            calls=(base/'launches').read_text().splitlines()
            self.assertEqual(len(calls),12 if outcome=='PASS' else 30)
            manifest=json.loads((ROOT/'editions/2026-10/MANIFEST.json').read_text())
            for tool,models in manifest['roster'].items():
                for model in models:
                    for rep in ((1,) if outcome=='PASS' and model!='glm-5.3-flash' else (1,2,3)):
                        self.assertIn(f'{tool} {model} orgsync {rep}',calls)
            existing = {p: p.read_bytes() for p in (base/'editions').rglob('result.json')}
            (base/'launches').unlink()
            rerun=subprocess.run(['bash',str(base/'batch.sh')],env=env,capture_output=True,text=True)
            self.assertEqual(rerun.returncode,0,rerun.stderr)
            calls=(base/'launches').read_text().splitlines() if (base/'launches').exists() else []
            self.assertEqual(len(calls),10 if outcome=='missing' else 0)
            for p, data in existing.items():self.assertEqual(p.read_bytes(),data)
            # Edition 2's initial scheduling cannot be overridden to N=3.
            refused=subprocess.run(['bash',str(base/'batch.sh')],env=dict(env,REPS='3'),capture_output=True,text=True)
            self.assertEqual(refused.returncode,2)

    def test_claude_and_codex_capture_receipts_with_offline_stub_clis(self):
        with tempfile.TemporaryDirectory(prefix='harness-fixture-',dir=ROOT) as tmp:
            base=Path(tmp)
            for name in ('run.sh','edition.sh','run_audit.py','score.py','token_evidence.py','prices.yaml','acceptance_test.py'):
                shutil.copy(ROOT/name,base/name)
            for directory in ('prompts','editions/2026-10','bin','operator-home/.codex','operator-home/.claude'):
                (base/directory).mkdir(parents=True,exist_ok=True)
            for name in ('TIER_PIN','MANIFEST.json'):shutil.copy(ROOT/'editions/2026-10'/name,base/'editions/2026-10'/name)
            use_run_table(base)
            shutil.copy(ROOT/'prompts/baseline.md',base/'prompts/baseline.md')
            (base/'operator-home/.codex/auth.json').write_text('{"fixture":"offline-credential"}')
            (base/'operator-home/.codex/config.toml').write_text('model_reasoning_effort = "max"\n')
            (base/'operator-home/.claude/settings.json').write_text('{"effortLevel":"max"}')
            stub='''#!{python}
import json,os,sys
from pathlib import Path
if sys.argv[1:]==['--version']:print('offline stub');sys.exit(0)
settings=json.loads((Path(sys.argv[0]).parent/'stub-control.json').read_text())
Path('stub-env-names.json').write_text(json.dumps(sorted(os.environ)))
assert os.environ['TERM']=='dumb' and os.environ['NO_COLOR']=='1'
if Path(sys.argv[0]).name=='claude':
 assert '.tool-config' not in os.environ['HOME']
 assert 'CLAUDE_CONFIG_DIR' not in os.environ and 'ANTHROPIC_API_KEY' not in os.environ
 assert '--bare' not in sys.argv and '--safe-mode' in sys.argv
 assert sys.argv[sys.argv.index('--setting-sources')+1]==''
else:
 assert '.tool-config' in os.environ['HOME']
 assert (Path(os.environ['CODEX_HOME'])/'auth.json').is_file()
 assert 'sandbox_workspace_write.network_access=true' in sys.argv
 assert not (Path(os.environ['CODEX_HOME'])/'config.toml').exists()
 assert not (Path(os.environ['CLAUDE_CONFIG_DIR'])/'settings.json').exists()
cwd=os.getcwd()
def write(p,objects):
 p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(''.join(json.dumps(o)+'\\n' for o in objects))
if Path(sys.argv[0]).name=='claude':
 project=Path(os.environ['HOME'])/'.claude/projects'/cwd.replace('/','-').replace('.','-')
 sid=sys.argv[sys.argv.index('--session-id')+1]
 write(project/(sid+'.jsonl'),[dict(type='request',output_config=dict(effort='low')),dict(type='assistant',message=dict(id='x',model='claude-haiku-5-5',usage=dict(input_tokens=100,output_tokens=10)))])
 if settings.get('STUB_SUBAGENTS')=='1':write(project/sid/'subagents/a.jsonl',[dict(type='assistant',message=dict(id='child',usage=dict(input_tokens=100,output_tokens=10)))])
else:
 root=Path(os.environ['CODEX_HOME'])/'sessions'
 event=dict(type='event_msg',payload=dict(type='token_count',info=dict(total_token_usage=dict(input_tokens=100,output_tokens=10,total_tokens=110))))
 context=dict(type='turn_context',payload=dict(effort='high'))
 if settings.get('STUB_SETTING_SOURCE')=='1':context['payload']['effort_source']='config'
 objects=[dict(type='session_meta',payload=dict(cwd=cwd)),context,event]
 if settings.get('STUB_MIXED_SOURCE')=='1':
  context['payload']['effort_source']='config'
  objects.append(dict(type='request',effort='high',effort_source='cli-default'))
 if settings.get('STUB_SANDBOX')=='1':objects.append(dict(type='tool_result',payload=dict(output='SandboxDenied: network access blocked')))
 write(root/'rollout-target.jsonl',objects)
 write(root/'rollout-noise.jsonl',[dict(type='session_meta',payload=dict(cwd='/unrelated')),event])
sys.exit(7)
'''.format(python=sys.executable)
            for name in ('claude','codex'):
                cli=base/'bin'/name;cli.write_text(stub);cli.chmod(0o755)
            env=fixture_env();env.update(EDITION='2026-10',DRY_RUN='0',ALLOW_EXPENSIVE='1',AGENT_TIMEOUT='10',HOME=str(base/'operator-home'),PATH=str(base/'bin')+os.pathsep+os.environ['PATH'])
            env.update(polluted_session_env())
            for tool,model,rep,patch in [('claude-code','haiku',1,{}),('claude-code','haiku',2,{'STUB_SUBAGENTS':'1'}),('codex','gpt-6-luna',1,{}),('codex','gpt-6-luna',2,{'STUB_SANDBOX':'1'}),('codex','gpt-6-luna',3,{'STUB_SETTING_SOURCE':'1'}),('codex','gpt-6-luna',4,{'STUB_MIXED_SOURCE':'1'})]:
                (base/'bin/stub-control.json').write_text(json.dumps(patch))
                p=subprocess.run(['bash',str(base/'run.sh'),tool,model,'baseline',str(rep)],env=env,capture_output=True,text=True,timeout=20)
                self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                d=base/f'editions/2026-10/{tool}/{model}/baseline/rep{rep}'
                r=json.loads((d/'result.json').read_text());st=json.loads((d/'stamp.json').read_text())
                self.assertEqual(st['clean_config'],tool == 'codex')
                assert_minimal_environment(self,d,st,tool)
                self.assertFalse(list((base/'.tool-config').glob('*/codex/auth.json')))
                self.assertFalse(list((base/'.tool-config').glob('*/data/opencode/auth.json')))
                for secret in ('dummy-secret','dummy-anthropic-key','dummy-meta-key'):
                    self.assertNotIn(secret,p.stdout+p.stderr)
                self.assertEqual(st['effort'],'low' if tool=='claude-code' else 'high')
                if patch.get('STUB_SUBAGENTS'):
                    self.assertEqual(r['capture'],'CLAUDE_SUBAGENTS_REFUSED');self.assertIsNone(r['cost_list_usd'])
                    self.assertIn('spend capture refused',p.stderr)
                elif patch:
                    self.assertEqual(r['outcome'],'VOID');self.assertIn('void_reason',r)
                    if patch.get('STUB_SETTING_SOURCE'):self.assertEqual(st['effort_source'],'config')
                    if patch.get('STUB_MIXED_SOURCE'):self.assertEqual(st['effort_source'],['cli-default','config'])
                else:
                    self.assertEqual(r['capture'],'OK');self.assertGreater(r['cost_list_usd'],0)
                    self.assertEqual(st['effort_source'],'observed-unsourced')
                if tool=='claude-code':
                    self.assertEqual(st['billing_route'],'claude-code-subscription')
                    self.assertIn('operator real home/default config dir',st['auth_policy'])
                    self.assertEqual(st['session_id'],st['run_id'])
                    self.assertIn('requested-UUID',st['capture_scope'])
                if tool=='codex':self.assertEqual(st['capture_scope'],'newer-than-marker+session_meta.cwd==run_dir')

class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='preflight-fixture-', dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        for name in ('preflight_http.py', 'run.sh', 'batch.sh', 'preflight.sh', 'preflight_check.py',
                     'edition.sh', 'run_audit.py', 'score.py', 'token_evidence.py', 'prices.yaml'):
            shutil.copy(ROOT/name, self.base/name)
        for directory in ('editions/2026-10', 'bin', 'operator-home/.codex',
                          'operator-home/.local/share/opencode', 'operator-home/.config/muse'):
            (self.base/directory).mkdir(parents=True, exist_ok=True)
        for name in ('TIER_PIN', 'MANIFEST.json'):
            shutil.copy(ROOT/'editions/2026-10'/name, self.base/'editions/2026-10'/name)
        use_run_table(self.base)
        # These regression fixtures exercise the original four-tool policy.
        manifest_path = self.base/'editions/2026-10/MANIFEST.json'
        manifest = json.loads(manifest_path.read_text())
        manifest.pop('late_admissions', None)
        manifest_path.write_text(json.dumps(manifest))
        # No prompts, skeleton, fixtures or judges: preflight must skip them.
        self.env = fixture_env()
        self.env.update(EDITION='2026-10', DRY_RUN='0', ALLOW_EXPENSIVE='1',
                        HOME=str(self.base/'operator-home'),
                        PATH=str(self.base/'bin')+os.pathsep+os.environ['PATH'],
                        CLAUDE_CONFIG_DIR=str(self.base/'wrong-claude-config'),
                        XDG_CONFIG_HOME=str(self.base/'wrong-xdg-config'),
                        MUSE_AUTH_PATH=str(self.base/'wrong-muse-auth.json'))
        self.env.update(polluted_session_env())
        self.env.update(USER='wrong-parent-user', LOGNAME='wrong-parent-user')
        home = self.base/'operator-home'
        (home/'.codex/auth.json').write_text('{"fixture":"codex-credential"}')
        (home/'.local/share/opencode/auth.json').write_text(json.dumps({
            'zai-coding-plan': {'key': 'opencode-credential'}, 'excluded': {'key': 'do-not-copy'}}))
        (home/'.config/muse/auth.json').write_text('{"storage":"keychain","mechanism":"oauth","fixture":"muse-credential"}')

    def install_stubs(self):
        stub = r'''#!PYTHON
import json, os, sqlite3, sys
from pathlib import Path
args=sys.argv[1:]; tool=Path(sys.argv[0]).name
if args==['--version']:print('offline stub');sys.exit(0)
base=Path(sys.argv[0]).parent.parent
settings=json.loads((base/'stub-control.json').read_text()) if (base/'stub-control.json').exists() else {}
def value(flag):return args[args.index(flag)+1]
def write(path,objects):
 path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(''.join(json.dumps(o)+'\n' for o in objects))
import re, urllib.request
url=re.search(r'http://127\.0\.0\.1:[0-9]+/ok/[a-z-]+',args[-1]).group()
assert 'with a shell command' in args[-1]
with (base/'calls.jsonl').open('a') as f:f.write(json.dumps(dict(tool=tool,args=args,env_names=sorted(os.environ)))+'\n')
mode=settings.get('mode') if settings.get('tool')==tool else None
inp,out=(0,0) if mode=='zero' else (100,10)
cwd=os.getcwd()
if tool in ('claude','muse'):
 import pwd
 assert Path(os.environ['HOME'])==base/'operator-home'
 assert os.environ['USER']==os.environ['LOGNAME']==pwd.getpwuid(os.getuid()).pw_name
 assert not set(('XDG_CONFIG_HOME','XDG_DATA_HOME','XDG_CACHE_HOME','MUSE_AUTH_PATH','CLAUDE_CONFIG_DIR','CODEX_HOME','ANTHROPIC_API_KEY','META_API_KEY')) & set(os.environ)
 assert not list((base/'.tool-config').glob('*/config/muse/auth.json'))
if mode!='no-capture':
 if tool=='claude':
  assert Path(os.environ['HOME'])==base/'operator-home'
  assert 'CLAUDE_CONFIG_DIR' not in os.environ and 'ANTHROPIC_API_KEY' not in os.environ
  assert '--bare' not in args and '--safe-mode' in args
  sid=value('--session-id');project=Path(os.environ['HOME'])/'.claude/projects'/cwd.replace('/','-').replace('.','-')
  write(project/(sid+'.jsonl'),[dict(type='assistant',sessionId=sid,message=dict(id='x',model='claude-haiku-5-5',usage=dict(input_tokens=inp,output_tokens=out)))])
  # Another session in this cwd must not contaminate the requested UUID capture.
  write(project/'unrelated.jsonl',[dict(type='assistant',message=dict(id='noise',usage=dict(input_tokens=99999,output_tokens=1)))])
  if mode=='subagents':write(project/sid/'subagents/a.jsonl',[])
 elif tool=='codex':
  root=Path(os.environ['CODEX_HOME'])
  assert json.loads((root/'auth.json').read_text())==dict(fixture='codex-credential')
  assert not (root/'config.toml').exists()
  write(root/'sessions/rollout-stub.jsonl',[dict(type='session_meta',payload=dict(cwd=cwd)),dict(type='event_msg',payload=dict(type='token_count',info=dict(total_token_usage=dict(input_tokens=inp,output_tokens=out,total_tokens=inp+out))))])
 elif tool=='opencode':
  root=Path(os.environ['XDG_DATA_HOME'])/'opencode'
  assert json.loads((root/'auth.json').read_text())=={'zai-coding-plan':{'key':'opencode-credential'}}
  assert not (Path(os.environ['XDG_CONFIG_HOME'])/'opencode/opencode.json').exists()
  with sqlite3.connect(root/'opencode.db') as c:
   c.executescript('CREATE TABLE session(id TEXT, directory TEXT, title TEXT, parent_id TEXT); CREATE TABLE message(id TEXT, session_id TEXT, data TEXT); CREATE TABLE part(id TEXT, session_id TEXT, data TEXT);')
   c.execute('INSERT INTO session VALUES(?,?,?,?)',('stub',cwd,value('--title'),None))
   m=dict(role='assistant',modelID='glm-5.3',providerID='zai-coding-plan',path=dict(cwd=cwd),time=dict(completed=1791800000000),tokens=dict(total=inp+out,input=inp,output=out,reasoning=0,cache=dict(read=0,write=0)))
   c.execute('INSERT INTO message VALUES(?,?,?)',('m','stub',json.dumps(m)))
 else:
  assert json.loads((Path(os.environ['HOME'])/'.config/muse/auth.json').read_text())==dict(storage='keychain',mechanism='oauth',fixture='muse-credential')
  assert all(flag in args for flag in ('--no-foreign-personal-context','--worktree','--approval-mode','--approval-judge','--contributor-mode','--disable-web-tools','--disable-reminders'))
  sid=value('--session-id')
  write(Path(os.environ['HOME'])/'.local/share/muse/sessions/2026/10/12'/sid/'session.jsonl',[
   dict(id='meta',stream=dict(kind='session',id=sid),payload=dict(kind='metadata',record=dict(workspace_root=cwd))),
   dict(id='a',recorded_at=1791800000000000,stream=dict(kind='session',id=sid),payload=dict(kind='run',run_id='run',event=dict(kind='model_completed',model='muse-spark-1.3',usage=dict(input_tokens=inp,output_tokens=out))))])
body='OK'
if mode not in ('blocked-loopback', 'fake-body'):
 body=urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url).read().decode()
 if mode=='missing-body':body='OK'
print('Not logged in · Please run /login' if mode=='login' else body)
sys.exit(7 if mode=='exit' else 0)
'''.replace('PYTHON', sys.executable)
        for tool in ('claude', 'codex', 'opencode', 'muse'):
            path=self.base/'bin'/tool; path.write_text(stub); path.chmod(0o755)

    def invoke(self):
        return subprocess.run(['bash', str(self.base/'preflight.sh')], env=self.env,
                              capture_output=True, text=True, timeout=25)

    def test_all_four_use_runner_auth_and_capture_then_write_record(self):
        self.install_stubs()
        config=self.base/'operator-home/.config/muse'
        (config/'trust.json').write_text('{"trusted_workspaces":["/operator/home"]}')
        (config/'.auth.json.lock').write_text('not JSON')
        (config/'settings.json').write_text('{"schema_version":1,"model":"muse-spark-1.3","provider":"meta"}')
        p=self.invoke(); self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
        record=json.loads((self.base/'.preflight/pass.json').read_text())
        self.assertEqual(set(record['tools']), set(TOOLS))
        self.assertEqual([record['tools'][t]['tokens'] for t in TOOLS], [110]*4)
        self.assertEqual(p.stdout.count('PREFLIGHT PASS'), 4)
        for receipt in record['tools'].values():
            self.assertIn('date_utc', receipt); self.assertLess(time.time()-receipt['passed_at'], 60)
        calls=[json.loads(line) for line in (self.base/'calls.jsonl').read_text().splitlines()]
        self.assertEqual([call['tool'] for call in calls], ['claude','codex','opencode','muse'])
        self.assertNotIn('dummy-meta-key', (self.base/'.preflight/pass.json').read_text()+p.stdout+p.stderr)
        self.assertFalse(list((self.base/'.preflight').glob('run.*')))
        self.assertFalse(list((self.base/'.tool-config').iterdir()))
        self.assertFalse(list((self.base/'editions/2026-10').glob('*/*/*/rep*')))
        gate=subprocess.run([sys.executable,str(self.base/'preflight_check.py'),'require','--root',str(self.base)],env=self.env,capture_output=True,text=True)
        self.assertEqual(gate.returncode,0,gate.stderr)

    def test_preflight_muse_settings_refusal_does_not_block_other_tools(self):
        self.install_stubs()
        config=self.base/'operator-home/.config/muse'
        (config/'.auth.json.lock').write_text('not JSON')
        (config/'trust.json').write_text('{"trusted_workspaces":["/operator/home"]}')
        (config/'settings.json').write_text('{"reasoning_effort":"max"}')
        p=self.invoke();self.assertEqual(p.returncode,1,p.stdout+p.stderr)
        self.assertIn('operator Muse config keys refused: reasoning_effort',p.stderr)
        self.assertEqual(p.stdout.count('PREFLIGHT PASS'),3)
        calls=[json.loads(line)['tool'] for line in (self.base/'calls.jsonl').read_text().splitlines()]
        self.assertEqual(calls,['claude','codex','opencode'])
        self.assertFalse((self.base/'.preflight/pass.json').exists())

    def test_preflight_dry_run_never_calls_cli_or_grants_passes(self):
        self.install_stubs()
        write_preflight_record(self.base, self.env)
        record = (self.base/'.preflight/pass.json').read_bytes()
        self.env['DRY_RUN'] = '1'
        p = self.invoke()
        self.assertEqual(p.returncode, 0, p.stdout+p.stderr)
        self.assertEqual(p.stdout.count('command:'), 4)
        self.assertFalse((self.base/'calls.jsonl').exists())
        self.assertEqual((self.base/'.preflight/pass.json').read_bytes(), record)

    def test_runner_preflight_rejects_login_zero_exit_missing_and_subagents(self):
        self.install_stubs()
        for mode, reason in [('login','login/auth prompt'),('zero','zero or invalid tokens'),
                             ('exit','agent exit 7'),('no-capture','capture failed'),
                             ('subagents','capture failed'), ('blocked-loopback','loopback request not observed'), ('fake-body','loopback request not observed'), ('missing-body','loopback request/body')]:
            with self.subTest(mode=mode):
                write_preflight_record(self.base,self.env)
                (self.base/'stub-control.json').write_text(json.dumps(dict(tool='claude',mode=mode)))
                p=self.invoke()
                self.assertEqual(p.returncode,1,p.stdout+p.stderr)
                self.assertIn(reason,p.stderr)
                self.assertFalse((self.base/'.preflight/pass.json').exists())
                self.assertEqual(p.stdout.count('PREFLIGHT PASS'),3)

    def test_batch_refuses_missing_stale_partial_future_and_zero_passes(self):
        # A paid batch may only invoke this harmless stub after a fresh record.
        run=self.base/'run.sh';run.write_text('#!/bin/sh\ntouch "$(dirname "$0")/launched"\nexit 0\n');run.chmod(0o755)
        for condition in ('missing','stale','partial','future','zero','malformed','changed'):
            with self.subTest(condition=condition):
                path=self.base/'.preflight/pass.json'
                if condition=='missing':path.unlink(missing_ok=True)
                else:
                    write_preflight_record(self.base,self.env,age=3601 if condition=='stale' else -60 if condition=='future' else 0)
                    d=json.loads(path.read_text())
                    if condition=='partial':del d['tools']['muse']
                    if condition=='zero':d['tools']['codex']['tokens']=0
                    if condition=='changed':d['policy_key']='old-runner'
                    path.write_text('bad JSON' if condition=='malformed' else json.dumps(d))
                p=subprocess.run(['bash',str(self.base/'batch.sh')],env=self.env,capture_output=True,text=True)
                self.assertEqual(p.returncode,4,p.stdout+p.stderr)
                self.assertIn('fresh preflight required',p.stderr)
                self.assertFalse((self.base/'launched').exists())

    def test_freshness_invalidated_by_home_provider_runner_and_all_policy_files(self):
        def require(env):
            return subprocess.run([sys.executable,str(self.base/'preflight_check.py'),'require','--root',str(self.base)],env=env,capture_output=True,text=True)
        for key, value in [('HOME',str(self.base/'different-home')),('OPENCODE_PROVIDER','different-provider')]:
            write_preflight_record(self.base,self.env)
            self.assertNotEqual(require(dict(self.env,**{key:value})).returncode,0)
        for name in POLICY_FILES:
            with self.subTest(file=name):
                write_preflight_record(self.base,self.env)
                path=self.base/name;original=path.read_bytes()
                path.write_bytes(original+b' ')
                try:self.assertNotEqual(require(self.env).returncode,0)
                finally:path.write_bytes(original)

    def test_freshness_rejects_tool_model_and_loopback_receipt_mismatches(self):
        for key, value in [('tool','muse'),('model','gpt-6-astra'),('loopback',False)]:
            write_preflight_record(self.base,self.env)
            path=self.base/'.preflight/pass.json';record=json.loads(path.read_text())
            record['tools']['codex'][key]=value;path.write_text(json.dumps(record))
            p=subprocess.run([sys.executable,str(self.base/'preflight_check.py'),'require','--root',str(self.base)],env=self.env,capture_output=True,text=True)
            self.assertNotEqual(p.returncode,0)

    def test_preflight_mode_refuses_nonfloor_models_before_call(self):
        self.install_stubs()
        for tool, model in [('claude-code','opus'),('codex','gpt-6-astra')]:
            p=subprocess.run(['bash',str(self.base/'run.sh'),tool,model,'baseline','1'],env=dict(self.env,PREFLIGHT='1',PREFLIGHT_RUN_ROOT=str(self.base/'throwaway')),capture_output=True,text=True)
            self.assertNotEqual(p.returncode,0);self.assertIn('limited to floor/sole models',p.stderr)
        self.assertFalse((self.base/'calls.jsonl').exists())

    def test_assertion_rejects_auth_markers_and_zero_usage_offline(self):
        run=self.base/'probe';run.mkdir()
        log=run/'session.jsonl'
        write_log(log,[dict(type='assistant',message=dict(id='x',usage=dict(input_tokens=100,output_tokens=10)))])
        for output in ('Not logged in · Please run /login','/login','Please authenticate','Missing API key'):
            (run/'agent-output.txt').write_text(output)
            with self.assertRaisesRegex(ValueError,'login/auth prompt'):
                assert_probe('claude-code','haiku',run,0,'OK')
        (run/'agent-output.txt').write_text('OK')
        write_log(log,[dict(type='assistant',message=dict(id='x',usage=dict(input_tokens=0,output_tokens=0)))])
        with self.assertRaisesRegex(ValueError,'zero or invalid tokens'):
            assert_probe('claude-code','haiku',run,0,'OK')


class EditionMetricsTests(unittest.TestCase):
    """Each CSV field is evidence-checked, including receipt wall time."""
    def setUp(self):
        import csv
        from verify import derive_run_metrics, metric_strings, METRIC_FIELDS
        from edition_report import generated_tables
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.prices = {'fixture': dict(input_per_m=2, output_per_m=10, cache_read_mult=0.1)}
        self.manifest = dict(metrics_file='metrics.csv', roster={'codex': ['fixture']})
        (self.root/'MANIFEST.json').write_text(json.dumps(self.manifest))
        self.runs = []
        self.metrics = []
        for rep in (1,2,3):
            d = self.root/f'codex/fixture/orgsync/rep{rep}'
            d.mkdir(parents=True)
            write_log(d/'session.jsonl', [dict(type='event_msg', payload=dict(type='token_count',
                info=dict(total_token_usage=dict(input_tokens=100, cached_input_tokens=20,
                    output_tokens=10, reasoning_output_tokens=3, total_tokens=110))))])
            res = dict(edition='2026-10', task='orgsync', tool='codex', model_key='fixture',
                       outcome='PASS', wall_seconds=10*rep,
                       tokens=dict(api_calls=None, input_uncached=80, cache_read=20,
                                   cache_write=0, output=10, reasoning=3))
            (d/'result.json').write_text(json.dumps(res))
            (d/'stamp.json').write_text('{}')
            self.runs.append({**res, '_bundle_dir': str(d)})
            self.metrics.append(derive_run_metrics(d, self.prices, self.root))
        self.fields = list(METRIC_FIELDS)
        self.rows = [metric_strings(x) for x in self.metrics]
        self.write_csv(self.rows)
        (self.root/'REPORT.md').write_text('\n\n'.join(
            f'<!-- table:{name} -->\n{table}\n<!-- /table:{name} -->'
            for name, table in generated_tables(self.metrics,self.manifest).items()) + '\n' +
            '\n'.join(f'<!-- figure:{name} -->' for name in ('cost-per-accepted','model-calls','wall-time','tokens')))

    def write_csv(self, rows):
        import csv
        with (self.root/'metrics.csv').open('w',newline='') as fh:
            w=csv.DictWriter(fh,fieldnames=self.fields)
            w.writeheader();w.writerows(rows)

    def check(self):
        from verify import verify_metrics
        r=Report();verify_metrics(self.runs,r,str(self.root),self.prices)
        return r.rows

    def test_metrics_and_all_generated_tables_match(self):
        checks=self.check()
        self.assertEqual(len(checks),9)
        self.assertTrue(all(status=='PASS' for status,_,_ in checks),checks)
        self.assertEqual(self.metrics[0]['model_calls'],1)
        self.assertEqual(self.metrics[0]['total_tokens'],110)  # reasoning is a subset

    def test_codex_distinct_responses_and_adjacent_repeat_refusal(self):
        from verify import derive_run_metrics
        d = Path(self.runs[0]['_bundle_dir'])
        last = json.loads((d/'session.jsonl').read_text())
        first = json.loads(json.dumps(last))
        first['payload']['info']['total_token_usage']['input_tokens'] = 50
        noise = [dict(type='event_msg', payload=dict(type='token_count', info=None)),
                 dict(type='event_msg', payload=dict(type='other'))]
        write_log(d/'session.jsonl', [first, *noise, last, last])
        # Repeated cumulative usage is one response, never an extra call.
        row = derive_run_metrics(d, self.prices, self.root)
        self.assertEqual(row['model_calls'], 2)
        self.assertTrue(any(st=='FAIL' and 'adjacent repeated usage snapshots' in detail
                            for st, _, detail in self.check()))
        write_log(d/'session.jsonl', [first, *noise, last])
        self.assertEqual(derive_run_metrics(d, self.prices, self.root)['model_calls'], 2)

    def test_report_main_rewrites_headlines_and_verifies_accepted_count(self):
        from edition_report import main
        from verify import verify_metrics, verify_report
        table = self.root/'prices.yaml'
        table.write_text('version: 13\nmodels:\n'
                         '  fixture: { input_per_m: 2, output_per_m: 10, cache_read_mult: 0.1 }\n')
        self.manifest['report_rate_table'] = dict(path=str(table.relative_to(ROOT)), version=13,
            sha256=hashlib.sha256(table.read_bytes()).hexdigest())
        (self.root/'MANIFEST.json').write_text(json.dumps(self.manifest))
        for run in self.runs:
            d = Path(run['_bundle_dir'])
            run['cost_list_usd'] = 0.0003
            (d/'result.json').write_text(json.dumps({k:v for k,v in run.items() if k!='_bundle_dir'}))
            (d/'stamp.json').write_text(json.dumps(dict(prices_yaml_version=7)))
            (d/'prices.snapshot.yaml').write_text('version: 7\nmodels:\n')
        path = self.root/'REPORT.md'
        path.write_text('**2026-10 · stale**\n| runs | stale |\n| total spend | stale |\n'
                        '| outcomes | stale |\n'+path.read_text())
        with patch.object(sys, 'argv', ['edition_report.py', str(self.root)]), contextlib.redirect_stdout(io.StringIO()):
            main()
        text = path.read_text()
        self.assertIn('one model · one task · 3 runs · $0.0008 at list rates · 3 accepted / 0 not', text)
        self.assertIn('| runs | **3** (1 models × 1 task × N=3) |', text)
        self.assertIn('sum of 3 six-decimal run costs, $0.000792; recorded v7 bundle costs sum to $0.000900', text)
        self.assertIn('| outcomes | 3 accepted · 0 not accepted |', text)
        r = Report()
        with contextlib.redirect_stdout(io.StringIO()):
            verify_metrics(self.runs, r, self.root, self.prices)
            verify_report(self.runs, r, self.root)
        self.assertTrue(all(st=='PASS' for st,_,_ in r.rows), r.rows)
        path.write_text(text.replace('3 accepted', '2 accepted'))
        r = Report()
        with contextlib.redirect_stdout(io.StringIO()):
            verify_report(self.runs, r, self.root)
        self.assertTrue(any(st=='FAIL' and 'accepted count' in what for st,what,_ in r.rows), r.rows)

    def test_generated_arithmetic_matches_hand_computed_three_rep_values(self):
        from edition_report import generated_tables
        metrics = [dict(row, cost_list_usd=cost, model_calls=calls)
                   for row,cost,calls in zip(self.metrics, (1.0,2.0,3.0), (2,4,9))]
        tables = generated_tables(metrics, self.manifest)
        self.assertIn('| fixture | 3/3 | 1.000000 | 2.000000 | 3.000000 | 2,500 | $2.0000 | $1.0000 – $3.0000 | 50.0% |', tables['results'])
        self.assertIn('| fixture | $2.0000 | 20 (10 – 30) | 4 (2 – 9) | 110 (110 – 110) |', tables['how-models-worked'])
        self.assertIn('| fixture | 240 | 60 | 0 | 30 | 9 | 330 |', tables['token-totals'])

    def test_missing_figure_marker_fails(self):
        p = self.root/'REPORT.md'; original = p.read_text()
        for name in ('cost-per-accepted','model-calls','wall-time','tokens'):
            p.write_text(original.replace(f'<!-- figure:{name} -->', ''))
            self.assertIn(('FAIL', f'report {name} figure marker', ''), self.check())

    def test_failed_capture_and_invalid_wall_time_refused(self):
        from verify import derive_run_metrics
        d = Path(self.runs[0]['_bundle_dir']); p = d/'result.json'; original = json.loads(p.read_text())
        p.write_text(json.dumps(dict(original, capture='FAILED')))
        with self.assertRaisesRegex(ValueError, 'failed capture'):
            derive_run_metrics(d, self.prices, self.root)
        for wall in (0, -1, float('inf')):
            p.write_text(json.dumps(dict(original, wall_seconds=wall)))
            with self.assertRaisesRegex(ValueError, 'wall_seconds must be finite and positive'):
                derive_run_metrics(d, self.prices, self.root)

    def test_claude_metrics_deduplicate_message_ids(self):
        from score import sum_claude_jsonl
        from verify import derive_run_metrics
        d = self.root/'claude-code/fixture/orgsync/rep1'; d.mkdir(parents=True)
        def message(mid, output):
            return dict(type='assistant', message=dict(id=mid, model='fixture', usage=dict(
                input_tokens=100, output_tokens=output, cache_read_input_tokens=20, cache_creation_input_tokens=0)))
        write_log(d/'session.jsonl', [message('one',5), message('one',10), message('one',10), message('two',10)])
        usage = sum_claude_jsonl(d/'session.jsonl')
        res = dict(self.runs[0], tool='claude-code', tokens=usage)
        res.pop('_bundle_dir'); (d/'result.json').write_text(json.dumps(res)); (d/'stamp.json').write_text('{}')
        row = derive_run_metrics(d, self.prices, self.root)
        self.assertEqual(row['model_calls'],2)
        self.assertEqual(row['model_calls_source'],'tokens.api_calls')
        self.assertEqual((row['input_uncached'],row['cache_read'],row['output']), (200,40,20))

    def test_opencode_and_muse_metrics_count_native_calls(self):
        from verify import derive_run_metrics
        for tool, model in (('opencode','glm-5.3'), ('muse','muse-spark-1.3')):
            d=self.root/f'{tool}/{model}/orgsync/rep1';d.mkdir(parents=True)
            objects=([dict(type='opencode_message', id=mid, session_id='s', data=oc_message()) for mid in ('one','two')]
                     if tool=='opencode' else [muse_event('one'),muse_event('two')])
            write_log(d/'session.jsonl',objects)
            usage=sum_native_jsonl(d/'session.jsonl',tool)
            res=dict(self.runs[0],tool=tool,model_key=model,tokens=usage)
            res.pop('_bundle_dir');(d/'result.json').write_text(json.dumps(res));(d/'stamp.json').write_text('{}')
            row=derive_run_metrics(d,{model:self.prices['fixture']},self.root)
            self.assertEqual(row['model_calls'],2)
            self.assertEqual(row['model_calls_source'],'tokens.api_calls')
            self.assertEqual(row['output'],120 if tool=='opencode' else 40)

    def test_generator_orders_full_bundle_strings_not_path_segments(self):
        from edition_report import load_metrics
        from verify import verify_metrics
        table=self.root/'prices.yaml'
        table.write_text('version: 13\nmodels:\n'
                         '  fixture: { input_per_m: 2, output_per_m: 10, cache_read_mult: 0.1 }\n'
                         '  fixture-flash: { input_per_m: 2, output_per_m: 10, cache_read_mult: 0.1 }\n')
        self.manifest['report_rate_table']=dict(path=str(table.relative_to(ROOT)), version=13,
            sha256=hashlib.sha256(table.read_bytes()).hexdigest())
        self.manifest['roster']['codex'].append('fixture-flash')
        for rep in (1,2,3):
            src=self.root/f'codex/fixture/orgsync/rep{rep}'
            dst=self.root/f'codex/fixture-flash/orgsync/rep{rep}'
            shutil.copytree(src,dst)
            p=dst/'result.json';res=json.loads(p.read_text());res['model_key']='fixture-flash'
            p.write_text(json.dumps(res))
            self.runs.append({**res,'_bundle_dir':str(dst)})
        (self.root/'MANIFEST.json').write_text(json.dumps(self.manifest))
        _,metrics=load_metrics(self.root)
        bundles=[x['bundle'] for x in metrics]
        self.assertEqual(bundles,sorted(bundles))
        self.assertEqual(metrics[0]['model'],'fixture-flash')  # '-' sorts before '/'
        from verify import metric_strings
        from edition_report import generated_tables
        self.write_csv([metric_strings(x) for x in metrics])
        (self.root/'REPORT.md').write_text('\n\n'.join(
            f'<!-- table:{name} -->\n{value}\n<!-- /table:{name} -->'
            for name,value in generated_tables(metrics,self.manifest).items()) + '\n' +
            '\n'.join(f'<!-- figure:{name} -->' for name in ('cost-per-accepted','model-calls','wall-time','tokens')))
        r=Report();verify_metrics(self.runs,r,str(self.root),
                                 {**self.prices,'fixture-flash':self.prices['fixture']})
        self.assertTrue(all(status=='PASS' for status,_,_ in r.rows),r.rows)

    def test_every_csv_value_mismatch_fails(self):
        for field in self.fields:
            with self.subTest(field=field):
                altered=[dict(x) for x in self.rows]
                altered[0][field]+='1'
                self.write_csv(altered)
                checks=self.check()
                self.assertTrue(any(st=='FAIL' and f'{field} mismatch' in detail
                                    for st,_,detail in checks),checks)

    def test_missing_duplicate_and_absent_csv_fail(self):
        for altered in (self.rows[:-1],self.rows+[self.rows[0]], [self.rows[0]]*3):
            self.write_csv(altered)
            self.assertTrue(any(st=='FAIL' for st,_,_ in self.check()))
        (self.root/'metrics.csv').unlink()
        self.assertTrue(any(st=='FAIL' for st,_,_ in self.check()))

    def test_csv_schema_and_report_table_mutations_fail(self):
        self.fields=self.fields[:-1];self.write_csv([{k:v for k,v in x.items() if k in self.fields} for x in self.rows])
        self.assertTrue(any(st=='FAIL' for st,_,_ in self.check()))
        from verify import METRIC_FIELDS
        self.fields=list(METRIC_FIELDS);self.write_csv(self.rows)
        p=self.root/'REPORT.md';p.write_text(p.read_text().replace('0.000264','9.000264'))
        self.assertTrue(any(st=='FAIL' for st,_,_ in self.check()))

    def test_recorded_tokens_and_calls_must_match_usage(self):
        from verify import derive_run_metrics
        d=Path(self.runs[0]['_bundle_dir']);p=d/'result.json';original=json.loads(p.read_text())
        for field in ('input_uncached','cache_read','cache_write','output','reasoning','api_calls'):
            with self.subTest(field=field):
                changed=json.loads(json.dumps(original));changed['tokens'][field]=999
                p.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):derive_run_metrics(d,self.prices,self.root)


class EditionOneMetricsTests(unittest.TestCase):
    """July uses root-relative bundles and keeps every task separate."""
    def setUp(self):
        from edition_report import load_metrics, main
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.edition = self.root/'editions/2026-07'
        self.edition.mkdir(parents=True)
        self.manifest = dict(edition='2026-07', roster={'codex': ['fixture']},
            tasks={task: dict(identity=task+'/v1', reps=3) for task in ('baseline','cronspec','orgsync')})
        (self.edition/'MANIFEST.json').write_text(json.dumps(self.manifest))
        self.original = 'Before results\n\n---\n\n## What this edition does and does not show\nAfter\n'
        (self.edition/'REPORT.md').write_text(self.original)
        self.runs = []
        for task, factor in (('baseline',1), ('cronspec',10), ('orgsync',100)):
            for rep, calls in zip((1,2,3), (2,4,9)):
                d = self.root/f'codex/fixture/{task}/rep{rep}'
                d.mkdir(parents=True)
                scale = factor*rep
                events = [dict(type='event_msg', payload=dict(type='token_count', info=dict(
                    total_token_usage=dict(input_tokens=100*scale-n, cached_input_tokens=20*scale,
                        output_tokens=10*scale, reasoning_output_tokens=3*scale,
                        total_tokens=110*scale-n)))) for n in reversed(range(calls))]
                # July's repeated cumulative snapshot is not an extra call.
                write_log(d/'session.jsonl', events+[events[-1]] if (task, rep)==('cronspec',1) else events)
                res = dict(task=task, tool='codex', model_key='fixture', outcome='FAIL' if task=='orgsync' else 'PASS',
                    wall_seconds=10*scale, cost_list_usd=round(0.000264*scale,6), tokens=dict(api_calls=None, input_uncached=80*scale,
                        cache_read=20*scale, cache_write=0, output=10*scale, reasoning=3*scale))
                (d/'result.json').write_text(json.dumps(res))  # July records no edition ID
                snapshot = d/'prices.snapshot.yaml'
                snapshot.write_text('version: 8\nmodels:\n  fixture: { input_per_m: 2, output_per_m: 10, cache_read_mult: 0.1 }\n')
                stamp = dict(prices_yaml_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                    machine='FixtureOS arm64', tool_version='cli-1' if rep==1 else 'cli-2',
                    date_utc='2026-07-01T01:02:03Z' if rep==1 else '2026-07-02T04:05:06Z')
                if (task, rep)==('baseline',3):
                    del stamp['tool_version']
                (d/'stamp.json').write_text(json.dumps(stamp))
                self.runs.append({**res, '_bundle_dir': str(d)})
        self.manifest_bytes = (self.edition/'MANIFEST.json').read_bytes()
        _, self.metrics = load_metrics(self.root)
        with patch.object(sys, 'argv', ['edition_report.py', str(self.root)]), contextlib.redirect_stdout(io.StringIO()):
            main()

    def check(self):
        from verify import verify_metrics
        r = Report()
        verify_metrics(self.runs, r, self.edition, None)
        return r.rows

    def test_july_derivation_uses_manifest_snapshots_and_token_count_records(self):
        self.assertEqual(len(self.metrics),9)
        for row in self.metrics:
            self.assertEqual(row['edition'],'2026-07')
            self.assertEqual(row['bundle'],f'codex/fixture/{row["task"]}/rep{row["rep"]}')
            self.assertEqual(row['model_calls'],(2,4,9)[row['rep']-1])
            self.assertEqual(row['model_calls_source'],'session.token_count records')
        self.assertEqual(self.metrics[0]['total_tokens'],110)
        self.assertEqual(self.metrics[0]['cost_list_usd'],0.000264)
        self.assertTrue(all(st=='PASS' for st,_,_ in self.check()), self.check())

    def test_per_task_tables_have_hand_computed_medians_ranges_and_no_pooling(self):
        from edition_report import generated_tables, model_groups
        tables = generated_tables(self.metrics,self.manifest)
        self.assertEqual(len(tables),6)
        self.assertIn('| fixture | $0.0005 | 20 (10 – 30) | 4 (2 – 9) | 220 (110 – 330) |', tables['how-models-worked:baseline'])
        self.assertIn('| fixture | $0.0053 | 200 (100 – 300) | 4 (2 – 9) | 2,200 (1,100 – 3,300) |', tables['how-models-worked:cronspec'])
        self.assertIn('| fixture | NO SCORE | 2,000 (1,000 – 3,000) | 4 (2 – 9) | 22,000 (11,000 – 33,000) |', tables['how-models-worked:orgsync'])
        self.assertIn('| fixture | 2 | PASS | 20 | 4 | 160 | 40 | 0 | 20 | 6 | 220 | 0.000528 |', tables['per-run:baseline'])
        with self.assertRaisesRegex(ValueError,'never pool tasks'):
            model_groups(self.metrics,self.manifest)

    def test_all_twelve_scoped_markers_required_exactly_once(self):
        from verify import FIGURE_NAMES
        path = self.edition/'REPORT.md'; original = path.read_text()
        markers = [f'{name}:{task}' for task in self.manifest['tasks'] for name in FIGURE_NAMES]
        self.assertEqual(sum('figure marker' in what and 'unscoped' not in what for _,what,_ in self.check()),12)
        for key in markers:
            with self.subTest(marker=key):
                marker = f'<!-- figure:{key} -->'
                self.assertEqual(original.count(marker),1)
                for changed in (original.replace(marker,''),original+'\n'+marker):
                    path.write_text(changed)
                    self.assertIn(('FAIL',f'report {key} figure marker',''),self.check())
        path.write_text(original)

    def test_unscoped_markers_refused_for_multitask_edition(self):
        from verify import FIGURE_NAMES
        path = self.edition/'REPORT.md'; original = path.read_text()
        for name in FIGURE_NAMES:
            path.write_text(original+f'\n<!-- figure:{name} -->')
            self.assertIn(('FAIL',f'report {name} has no unscoped figure marker',''),self.check())

    def test_generation_is_idempotent_and_preserves_frozen_manifest_and_other_prose(self):
        from edition_report import main
        path = self.edition/'REPORT.md'
        before = path.read_bytes(); csv_before = (self.edition/'metrics.csv').read_bytes()
        with patch.object(sys,'argv',['edition_report.py',str(self.edition)]), contextlib.redirect_stdout(io.StringIO()):
            main()
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual((self.edition/'metrics.csv').read_bytes(),csv_before)
        self.assertEqual((self.edition/'MANIFEST.json').read_bytes(),self.manifest_bytes)
        remaining = re.sub(r'<!-- section:how-models-worked -->.*?<!-- /section:how-models-worked -->\n\n---\n\n','',path.read_text(),flags=re.S)
        self.assertEqual(remaining,self.original)
        self.assertIn('| machine / OS / architecture | FixtureOS arm64 |',path.read_text())

    def test_measured_counts_note_is_preserved_and_excluded_from_section_comparison(self):
        from edition_report import main, worked_section, CHECK_COUNTS_NOTE_PATTERN
        note = re.search(CHECK_COUNTS_NOTE_PATTERN, (ROOT/'editions/2026-07/REPORT.md').read_text(), re.M)
        self.assertIsNotNone(note)
        path = self.edition/'REPORT.md'
        text = re.sub(r'<!-- section:how-models-worked -->.*?<!-- /section:how-models-worked -->',
            lambda m: worked_section(self.metrics,self.manifest,self.root,note[0]),path.read_text(),flags=re.S)
        path.write_text(text)
        self.assertTrue(all(st=='PASS' for st,_,_ in self.check()),self.check())
        before = path.read_bytes()
        with patch.object(sys,'argv',['edition_report.py',str(self.root)]), contextlib.redirect_stdout(io.StringIO()):
            main()
        self.assertEqual(path.read_bytes(),before)

    def test_recorded_environment_uses_stamp_values_and_only_missing_keys_are_unrecorded(self):
        text = (self.edition/'REPORT.md').read_text()
        cells = dict(re.findall(r'^\| ([^|]+?) \| ([^|]+?) \|$', text, re.M))
        self.assertEqual(cells['machine / OS / architecture'], 'FixtureOS arm64')
        self.assertEqual(cells['CLI versions'], 'cli-1; cli-2; not recorded')
        self.assertEqual(cells['dates (UTC)'], '2026-07-01; 2026-07-02')

    def test_missing_evidence_stops_generation_without_writing_outputs(self):
        from edition_report import main
        before = {name:(self.edition/name).read_bytes() for name in ('REPORT.md','metrics.csv')}
        def generate():
            with patch.object(sys,'argv',['edition_report.py',str(self.root)]), contextlib.redirect_stdout(io.StringIO()):
                main()
        def unchanged():
            for name, data in before.items():
                self.assertEqual((self.edition/name).read_bytes(), data, name)
        d = self.root/'codex/fixture/baseline/rep1'
        result = d/'result.json'; original = json.loads(result.read_text())
        result.write_text(json.dumps({k:v for k,v in original.items() if k!='wall_seconds'}))
        with self.assertRaisesRegex(ValueError,'codex/fixture/baseline/rep1.*wall_seconds not recorded'):
            generate()
        unchanged()
        result.write_text(json.dumps(original))
        write_log(d/'session.jsonl',[])
        with self.assertRaisesRegex(ValueError,'codex/fixture/baseline/rep1.*no token_count'):
            generate()
        unchanged()

    def test_multitask_csv_order_schema_and_table_values_are_verified(self):
        path = self.edition/'metrics.csv'; original = path.read_text()
        lines = original.splitlines(keepends=True)
        for changed, detail in (
                (''.join([lines[0],*reversed(lines[1:])]), 'codex/fixture/baseline/rep1: task mismatch'),
                (original.replace('wall_seconds','wall_time',1), 'CSV columns differ from metrics schema'),
                (''.join(lines[:-1]), 'expected 9 rows, found 8'),
                (original+lines[-1], 'expected 9 rows, found 10'),
                (lines[0]+lines[1].rstrip('\n')+',extra\n'+''.join(lines[2:]), 'CSV columns differ from metrics schema')):
            with self.subTest(detail=detail):
                path.write_text(changed)
                self.assertIn(('FAIL','report metrics.csv',detail),self.check())
        path.write_text(original)
        report = self.edition/'REPORT.md'; original_report = report.read_text()
        report.write_text(original_report.replace('20 (10 – 30)','21 (10 – 30)',1))
        self.assertIn(('FAIL','report how-models-worked:baseline table matches metrics',''),self.check())
        table = re.search(r'<!-- table:per-run:baseline -->.*?<!-- /table:per-run:baseline -->', original_report, re.S)[0]
        report.write_text(original_report+'\n'+table.replace('0.000264','9.000264'))
        self.assertIn(('FAIL','report per-run:baseline table matches metrics',''),self.check())

    def test_whole_worked_section_rejects_environment_and_method_edits(self):
        path = self.edition/'REPORT.md'; original = path.read_text()
        for changed in (original.replace('FixtureOS arm64','EditedOS',1),
                        original.replace('never used to rank','used to rank',1)):
            path.write_text(changed)
            self.assertIn(('FAIL','report how-models-worked section matches evidence',''),self.check())

    def test_second_july_adjacent_snapshot_repeat_is_refused(self):
        path = self.root/'codex/fixture/baseline/rep1/session.jsonl'
        with path.open('a') as fh:
            fh.write(path.read_text().splitlines()[-1]+'\n')
        self.assertIn(('FAIL','report metrics.csv','Codex adjacent repeated usage snapshots: expected 1 for July, found 2'),self.check())

    def test_other_multitask_edition_is_refused(self):
        from edition_report import main
        manifest = {**self.manifest, 'edition':'2026-08'}
        (self.edition/'MANIFEST.json').write_text(json.dumps(manifest))
        before = {name:(self.edition/name).read_bytes() for name in ('REPORT.md','metrics.csv')}
        with patch.object(sys,'argv',['edition_report.py',str(self.root)]), self.assertRaisesRegex(ValueError,'multi-task reports require edition 2026-07'):
            main()
        for name, data in before.items():
            self.assertEqual((self.edition/name).read_bytes(),data,name)
        self.assertIn(('FAIL','report metrics.csv','multi-task reports require edition 2026-07'),self.check())

    def test_cost_difference_disclosure_is_derived_from_run_data(self):
        from edition_report import main
        path = self.root/'codex/fixture/baseline/rep1/result.json'
        result = json.loads(path.read_text()); result['cost_list_usd']=0.000265
        path.write_text(json.dumps(result))
        with patch.object(sys,'argv',['edition_report.py',str(self.root)]), contextlib.redirect_stdout(io.StringIO()):
            main()
        text = (self.edition/'REPORT.md').read_text()
        self.assertIn('`codex/fixture/baseline/rep1`',text)
        self.assertIn('derived $0.000264',text)
        self.assertIn('recorded $0.000265',text)
        self.assertNotIn('`codex/fixture/baseline/rep2`',text)
        self.assertTrue(all(st=='PASS' for st,_,_ in self.check()),self.check())
        result['cost_list_usd']=0.000264
        path.write_text(json.dumps(result))
        with patch.object(sys,'argv',['edition_report.py',str(self.root)]), contextlib.redirect_stdout(io.StringIO()):
            main()
        self.assertNotIn('recorded $0.000265',(self.edition/'REPORT.md').read_text())

    @unittest.skipUnless((ROOT/'editions/2026-10').is_dir(), 'Edition 2 archive absent')
    def test_edition_two_regeneration_is_byte_identical(self):
        from edition_report import main
        source = ROOT/'editions/2026-10'
        target = self.root/'edition-two'; target.mkdir()
        for name in ('MANIFEST.json','REPORT.md','metrics.csv'):
            shutil.copyfile(source/name,target/name)
        for result in source.glob('*/*/*/rep*/result.json'):
            d = target/result.parent.relative_to(source); d.mkdir(parents=True)
            for name in ('result.json','stamp.json','session.jsonl','prices.snapshot.yaml'):
                shutil.copyfile(result.parent/name,d/name)
        before = {name:(target/name).read_bytes() for name in ('REPORT.md','metrics.csv')}
        with patch.object(sys,'argv',['edition_report.py',str(target)]), contextlib.redirect_stdout(io.StringIO()):
            main()
        for name, data in before.items():
            self.assertEqual((target/name).read_bytes(),data,name)


if __name__=='__main__':unittest.main()
