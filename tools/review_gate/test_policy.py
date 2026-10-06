import copy
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('policy', Path(__file__).with_name('policy.py'))
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.identity = dict(repository='owner/repo', pr=7, head='a'*40, base='b'*40,
                             base_ref='dev', run_id='123', run_attempt='1')
        self.current = dict(number=7, state='open', head={'sha':'a'*40, 'repo':{'full_name':'owner/repo'}},
                            base={'sha':'b'*40, 'ref':'dev', 'repo':{'full_name':'owner/repo'}})
        self.code = dict(identity=copy.deepcopy(self.identity), status='complete', findings=[])
        self.security = dict(identity=copy.deepcopy(self.identity), exit_code=0, threshold='low', result_sha256='c'*64)
        self.jobs = dict(code='success', security='success')

    def check(self):
        return policy.validate(self.identity, self.current, self.code, self.security, self.jobs)

    def test_clean_all_target_branches(self):
        for branch in ('main', 'dev', 'feature/anything', 'release/1'):
            self.setUp()
            self.identity['base_ref'] = self.current['base']['ref'] = branch
            self.code['identity']['base_ref'] = self.security['identity']['base_ref'] = branch
            self.assertTrue(self.check())

    def test_blocking_priorities(self):
        for priority in range(3):
            with self.subTest(priority=priority), self.assertRaises(ValueError):
                self.code['findings'] = [dict(priority=priority,title='Regression',path='app/file',line=1)]
                self.check()

    def test_low_priority_is_reportable(self):
        self.code['findings'] = [dict(priority=3,title='Minor',path='app/file',line=1)]
        self.assertTrue(self.check())

    def test_malformed_findings(self):
        for value in (None, {}, [None], [{'priority':True}], [dict(priority=1,title='x',path='x',line=True)]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.code['findings'] = value
                self.check()

    def test_no_skipped_cancelled_failed_or_missing_job(self):
        for job in ('code','security'):
            for state in ('skipped','cancelled','failure','',None):
                self.setUp()
                self.jobs[job] = state
                with self.subTest(job=job,state=state), self.assertRaises(ValueError): self.check()

    def test_security_findings_incomplete_error_and_interrupt(self):
        for exit_code in (1,2,130,143,-9,None,False,'0'):
            self.security['exit_code'] = exit_code
            with self.subTest(exit_code=exit_code), self.assertRaises(ValueError): self.check()

    def test_security_threshold_cannot_weaken(self):
        for threshold in ('high','medium','',None):
            self.security['threshold'] = threshold
            with self.assertRaises(ValueError): self.check()

    def test_stale_identity_every_field_both_reviewers(self):
        for reviewer in ('code','security'):
            for key in self.identity:
                self.setUp()
                getattr(self,reviewer)['identity'][key] = 'wrong'
                with self.subTest(reviewer=reviewer,key=key), self.assertRaises(ValueError): self.check()

    def test_head_base_and_target_movement(self):
        for side,key in (('head','sha'),('base','sha'),('base','ref')):
            self.setUp()
            self.current[side][key] = 'changed'
            with self.assertRaises(ValueError): self.check()

    def test_closed_fork_and_wrong_destination(self):
        for side in ('head','base'):
            self.setUp()
            self.current[side]['repo']['full_name'] = 'other/repo'
            with self.assertRaises(ValueError): self.check()
        self.setUp()
        self.current['state'] = 'closed'
        with self.assertRaises(ValueError): self.check()

    def test_incomplete_code(self):
        for status in ('incomplete','skipped','error','clean',None):
            self.code['status'] = status
            with self.assertRaises(ValueError): self.check()

    def test_missing_receipt_and_digest(self):
        self.security.pop('result_sha256')
        with self.assertRaises(ValueError): self.check()

    def test_invalid_json_fails_closed(self):
        for raw in ('', '{} garbage', 'null', '[]', '{"a":1,"a":2}', '{"a":NaN}', 'x'*65537):
            with self.subTest(raw=raw[:30]), self.assertRaises(ValueError): policy.parse(raw)

    def test_schema_and_checker_identity_agree(self):
        schema = json.loads(Path(__file__).parents[2].joinpath('.github/codex/review-schema.json').read_text())
        self.assertEqual(set(schema['properties']['identity']['required']),set(policy.IDENTITY))
        self.assertEqual(set(schema['required']),set(self.code))


if __name__ == '__main__': unittest.main()
