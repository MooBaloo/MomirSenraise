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
                             base_ref='dev', merge='c'*40, run_id='123', run_attempt='1')
        self.current = dict(number=7, state='open', head={'sha':'a'*40, 'repo':{'full_name':'owner/repo'}},
                            base={'sha':'b'*40, 'ref':'dev', 'repo':{'full_name':'owner/repo'}})
        self.current.update(mergeable=True, merge_commit_sha='c'*40)
        self.merge_ref = {'ref':'refs/pull/7/merge','object':{'type':'commit','sha':'c'*40}}
        self.merge_commit = {'sha':'c'*40,'parents':['b'*40,'a'*40]}
        self.code = dict(identity=copy.deepcopy(self.identity), status='complete', findings=[])
        self.security = dict(identity=copy.deepcopy(self.identity), exit_code=0, threshold='low', result_sha256='c'*64)
        self.jobs = dict(code='success', security='success')

    def check(self):
        return policy.validate(self.identity, self.current, self.merge_ref, self.merge_commit, self.code, self.security, self.jobs)

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

    def test_wrong_pr_number(self):
        self.current['number'] = 8
        with self.assertRaises(ValueError): self.check()

    def test_missing_unknown_conflicting_merge(self):
        for mergeable in (None, False, 'true', 1):
            self.setUp()
            self.current['mergeable'] = mergeable
            with self.subTest(mergeable=mergeable), self.assertRaises(ValueError): self.check()
        self.setUp()
        self.current['merge_commit_sha'] = None
        with self.assertRaises(ValueError): self.check()

    def test_force_updated_merge_and_wrong_pr_ref(self):
        for field,value in [('ref','refs/pull/8/merge'),('object',{'type':'commit','sha':'d'*40}),
                            ('object',{'type':'tag','sha':'c'*40}),('object',{})]:
            self.setUp()
            self.merge_ref[field] = value
            with self.subTest(field=field,value=value), self.assertRaises(ValueError): self.check()

    def test_merge_parent_binding(self):
        for parents in ([], ['b'*40], ['a'*40,'b'*40], ['d'*40,'a'*40], ['b'*40,'d'*40], ['b'*40,'a'*40,'d'*40]):
            self.merge_commit['parents'] = parents
            with self.subTest(parents=parents), self.assertRaises(ValueError): self.check()

    def test_same_head_other_base_cannot_reuse_success(self):
        # Both API PR fields and the new ref moved; the old successful evidence
        # remains internally consistent but must not satisfy this new merge.
        self.current['base']['sha'] = 'e'*40
        self.current['merge_commit_sha'] = 'f'*40
        self.merge_ref['object']['sha'] = 'f'*40
        self.merge_commit = {'sha':'f'*40,'parents':['e'*40,'a'*40]}
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

    def test_security_result_decision_fields(self):
        clean = {'manifest':{'documentType':'codex-security.scan-manifest','schemaVersion':'1.0',
                             'scan':{'id':'scan-1','status':'completed','target':{'kind':'git_diff','baseRevision':'b'*40,'headRevision':'c'*40}}},
                 'findings':{'documentType':'codex-security.findings','schemaVersion':'1.0','scanId':'scan-1','findings':[]},
                 'coverage':{'documentType':'codex-security.coverage','schemaVersion':'1.0','scanId':'scan-1','completeness':'complete'}}
        policy.validate_security_result(clean,self.identity)
        changes = [(('coverage','completeness'),v) for v in ('partial','unknown',None)]
        changes += [(('manifest','scan','status'),v) for v in ('failed','canceled','interrupted',None)]
        changes += [(('manifest','scan','target',k),'f'*40) for k in ('headRevision','baseRevision')]
        changes += [(('findings','scanId'),'another-scan'), (('coverage','scanId'),'another-scan'),
                    (('findings','findings'),None), (('manifest',),None), (('coverage','schemaVersion'),'2.0')]
        changes += [(('findings','findings'),[{'severity':{'level':v}}]) for v in ('critical','high','medium','low','unknown',None)]
        for path,value in changes:
            data = copy.deepcopy(clean)
            target = data
            for key in path[:-1]: target = target[key]
            target[path[-1]] = value
            with self.subTest(path=path,value=value), self.assertRaises(ValueError):
                policy.validate_security_result(data,self.identity)

    def test_head_cannot_be_used_as_merge_status_target(self):
        self.identity['merge'] = self.identity['head']
        with self.assertRaises(ValueError): self.check()

    def test_security_trust_requires_exact_source_identity(self):
        entry = {key:self.identity[key] for key in policy.TRUST_IDENTITY}
        policy.require_trusted_security_source(self.identity,{'trusted_snapshots':[entry]})
        for key in entry:
            changed = dict(entry)
            changed[key] = 8 if key == 'pr' else 'wrong'
            with self.subTest(key=key), self.assertRaises(ValueError):
                policy.require_trusted_security_source(self.identity,{'trusted_snapshots':[changed]})
        for value in ({}, {'trusted_snapshots':[]}, {'trusted_snapshots':'all'},
                      {'trusted_snapshots':[{'repository':'owner/repo'}]}, {'trusted_snapshots':[dict(entry,pr=True)]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                policy.require_trusted_security_source(self.identity,value)

    def test_schema_and_checker_identity_agree(self):
        schema = json.loads(Path(__file__).parents[2].joinpath('.github/codex/review-schema.json').read_text())
        self.assertEqual(set(schema['properties']['identity']['required']),set(policy.IDENTITY))
        self.assertEqual(set(schema['required']),set(self.code))


if __name__ == '__main__': unittest.main()
