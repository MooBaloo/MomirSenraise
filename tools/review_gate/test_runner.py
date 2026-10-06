import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

with patch.dict(os.environ, {'GITHUB_REPOSITORY':'owner/repo'}):
    spec = importlib.util.spec_from_file_location('runner', Path(__file__).with_name('runner.py'))
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.identity = dict(repository='owner/repo', pr=7, head='a'*40, base='b'*40,
                             base_ref='dev', merge='c'*40, run_id='123', run_attempt='1')
        self.pr = dict(number=7, state='open', head={'sha':'a'*40,'repo':{'full_name':'owner/repo'}},
                       base={'sha':'b'*40,'ref':'dev','repo':{'full_name':'owner/repo'}})
        self.pr.update(mergeable=True, merge_commit_sha='c'*40)
        self.ref = {'ref':'refs/pull/7/merge','object':{'type':'commit','sha':'c'*40}}
        self.commit = {'sha':'c'*40,'parents':[{'sha':'b'*40},{'sha':'a'*40}]}
        self.env = dict(IDENTITY=json.dumps(self.identity),GITHUB_SHA='d'*40,GITHUB_RUN_ID='123',
                        CODE_JOB='success',SECURITY_JOB='success',
                        CODE_RESULT=json.dumps(dict(identity=self.identity,status='complete',findings=[])),
                        SECURITY_RESULT=json.dumps(dict(identity=self.identity,exit_code=0,threshold='low',result_sha256='e'*64)))

    def publish(self, expected_exit):
        calls = []
        def api(path, body=None):
            if body is not None:
                calls.append((path,body))
                return {}
            if path.startswith('/pulls/'): return self.pr
            if path.startswith('/git/ref/'): return self.ref
            if path.startswith('/git/commits/'): return self.commit
            return {'commit':{'sha':'d'*40}}
        with patch.dict(os.environ,self.env), patch.object(runner,'api',side_effect=api), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exit:
            runner.publish()
        self.assertEqual(exit.exception.code,expected_exit)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][0],'/statuses/'+'c'*40)
        self.assertEqual(calls[0][1]['state'],'success' if expected_exit==0 else 'failure')

    def test_success_publishes_test_merge_never_head(self): self.publish(0)

    def test_malformed_model_output_publishes_failure(self):
        self.env['CODE_RESULT'] = 'not JSON'
        self.publish(1)

    def test_skipped_scanner_publishes_failure(self):
        self.env['SECURITY_JOB'] = 'skipped'
        self.publish(1)

    def test_base_movement_publishes_failure(self):
        self.pr['base']['sha'] = 'f'*40
        self.publish(1)

    def test_api_failure_never_publishes_success(self):
        with patch.dict(os.environ,self.env), patch.object(runner,'api',side_effect=OSError('network unavailable')) as api, self.assertRaises(OSError):
            runner.publish()
        self.assertEqual(api.call_count,1)

    def test_missing_merge_ref_never_publishes_success(self):
        from urllib.error import HTTPError
        with patch.dict(os.environ,self.env), patch.object(runner,'api',side_effect=HTTPError('url',404,'Not found',{},None)) as api, self.assertRaises(HTTPError):
            runner.publish()
        self.assertEqual(api.call_count,1)
        self.assertEqual(api.call_args.args[0],'/git/ref/pull/7/merge')

    def test_force_updated_merge_publishes_failure_only_on_old_merge(self):
        self.ref['object']['sha'] = 'f'*40
        self.commit['sha'] = 'f'*40
        self.publish(1)

    def test_wrong_pr_ref_publishes_failure(self):
        self.ref['ref'] = 'refs/pull/8/merge'
        self.publish(1)

    def test_head_force_push_publishes_failure(self):
        self.pr['head']['sha'] = 'f'*40
        self.publish(1)

    def test_scanner_wrong_checkout_never_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(self.env,RUNNER_TEMP=directory,GITHUB_WORKSPACE=directory)
            with patch.dict(os.environ,env), patch.object(runner.subprocess,'check_output',return_value='a'*40), patch.object(runner.subprocess,'run') as scan, self.assertRaises(ValueError):
                runner.security()
            scan.assert_not_called()

    def test_dispatch_other_branch_rejected_before_api(self):
        with patch.dict(os.environ,{'GITHUB_REF':'refs/heads/dev'}), patch.object(runner,'api') as api, self.assertRaises(ValueError):
            runner.prepare()
        api.assert_not_called()

    def test_prepare_snapshots_merge_and_rejects_intervening_force_push(self):
        for changed in (False, True):
            reads = []
            writes = []
            def api(path, body=None):
                if body is not None:
                    writes.append((path,body))
                    return {}
                reads.append(path)
                if path == '/branches/main': return {'commit':{'sha':'d'*40}}
                if path.startswith('/git/ref/'): return self.ref
                if path.startswith('/git/commits/'): return self.commit
                if changed and reads.count('/pulls/7') == 2:
                    import copy
                    pr = copy.deepcopy(self.pr)
                    pr['head']['sha'] = 'f'*40
                    return pr
                return self.pr
            env = dict(self.env,GITHUB_REF='refs/heads/main',PR_NUMBER='7',GITHUB_RUN_ATTEMPT='1')
            with self.subTest(changed=changed), patch.dict(os.environ,env), patch.object(runner,'api',side_effect=api), patch.object(runner,'output') as output:
                if changed:
                    with self.assertRaises(ValueError): runner.prepare()
                    output.assert_not_called()
                    self.assertEqual(writes,[])
                else:
                    runner.prepare()
                    self.assertEqual(output.call_args.args,('identity',self.identity))
                    self.assertEqual(writes[0][0],'/statuses/'+'c'*40)
                    self.assertEqual(writes[0][1]['state'],'pending')

    def test_scanner_exit_and_result_contract(self):
        clean = {'manifest':{'documentType':'codex-security.scan-manifest','schemaVersion':'1.0',
                              'scan':{'id':'scan-1','status':'completed','target':{'kind':'git_diff','baseRevision':'b'*40,'headRevision':'c'*40}}},
                 'findings':{'documentType':'codex-security.findings','schemaVersion':'1.0','scanId':'scan-1','findings':[]},
                 'coverage':{'documentType':'codex-security.coverage','schemaVersion':'1.0','scanId':'scan-1','completeness':'complete'}}
        cases = [(0,clean,0),
                 (1,{'findings':['blocking']},1), (2,{'coverage':'partial'},2),
                 (0,{},None), (0,'malformed',None)]
        for code,payload,expected in cases:
            with self.subTest(code=code,payload=payload), tempfile.TemporaryDirectory() as directory:
                def process(*args,stdout,**kwargs):
                    stdout.write((payload if isinstance(payload,str) else json.dumps(payload)).encode())
                    return SimpleNamespace(returncode=code)
                env = dict(self.env,RUNNER_TEMP=directory,GITHUB_WORKSPACE=directory)
                with patch.dict(os.environ,env), patch.object(runner.subprocess,'check_output',return_value='c'*40), patch.object(runner.subprocess,'run',side_effect=process) as scan, patch.object(runner,'output') as output:
                    with self.assertRaises(SystemExit if expected is not None else ValueError) as error:
                        runner.security()
                    if expected is None:
                        output.assert_not_called()
                    else:
                        self.assertEqual(error.exception.code,expected)
                        receipt = output.call_args.args[1]
                        self.assertEqual(receipt['exit_code'],code)
                        self.assertEqual(receipt['identity'],self.identity)
                    command = scan.call_args.args[0]
                    trusted_prompt = Path(runner.__file__).parents[2] / '.github/codex/security-prompt.txt'
                    self.assertEqual(command[command.index('--scan-prompt-file')+1],str(trusted_prompt))
                    self.assertEqual(command[command.index('--diff')+1],self.identity['base'])
                    self.assertEqual(command[command.index('--head')+1],self.identity['merge'])
                    self.assertIn('--fail-on-severity',command)
                    self.assertEqual(command[command.index('--fail-on-severity')+1],'low')


if __name__ == '__main__': unittest.main()
