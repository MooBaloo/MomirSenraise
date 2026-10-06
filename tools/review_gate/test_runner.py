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
                             base_ref='dev', run_id='123', run_attempt='1')
        self.pr = dict(number=7, state='open', head={'sha':'a'*40,'repo':{'full_name':'owner/repo'}},
                       base={'sha':'b'*40,'ref':'dev','repo':{'full_name':'owner/repo'}})
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
            return self.pr if path.startswith('/pulls/') else {'commit':{'sha':'d'*40}}
        with patch.dict(os.environ,self.env), patch.object(runner,'api',side_effect=api), contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as exit:
            runner.publish()
        self.assertEqual(exit.exception.code,expected_exit)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][0],'/statuses/'+'a'*40)
        self.assertEqual(calls[0][1]['state'],'success' if expected_exit==0 else 'failure')

    def test_success_publishes_reviewed_head(self): self.publish(0)

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

    def test_dispatch_other_branch_rejected_before_api(self):
        with patch.dict(os.environ,{'GITHUB_REF':'refs/heads/dev'}), patch.object(runner,'api') as api, self.assertRaises(ValueError):
            runner.prepare()
        api.assert_not_called()

    def test_scanner_exit_and_result_contract(self):
        cases = [(0,{'manifest':{},'findings':[],'coverage':{}},0),
                 (1,{'findings':['blocking']},1), (2,{'coverage':'partial'},2),
                 (0,{},None), (0,'malformed',None)]
        for code,payload,expected in cases:
            with self.subTest(code=code,payload=payload), tempfile.TemporaryDirectory() as directory:
                def process(*args,stdout,**kwargs):
                    stdout.write((payload if isinstance(payload,str) else json.dumps(payload)).encode())
                    return SimpleNamespace(returncode=code)
                env = dict(self.env,RUNNER_TEMP=directory,GITHUB_WORKSPACE=directory)
                with patch.dict(os.environ,env), patch.object(runner.subprocess,'check_output',return_value='b'*40), patch.object(runner.subprocess,'run',side_effect=process) as scan, patch.object(runner,'output') as output:
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
                    self.assertIn('--fail-on-severity',command)
                    self.assertEqual(command[command.index('--fail-on-severity')+1],'low')


if __name__ == '__main__': unittest.main()
