"""Opt-in update resumption selects unfinished jobs without invalidating passes."""
import contextlib
import io
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

import gardens.batch as batch
import test_retry_incomplete as retry_fixtures


class ResumePendingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=retry_fixtures.RetryIncompleteTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.args=self.fixture.args;self.args.include_pending=True
        self.out=self.fixture.out

    def rows(self):
        return {(row['scene_id'],row['variant_id']):row for row in self.fixture.rows()}

    def preserved(self,states):
        return {key:row for key,row in self.rows().items() if not (row['active'] and row['status'] in states)}

    def files_for(self,rows):
        files={}
        for row in rows.values():
            folder=Path(json.loads(row['definition'])['destination'])
            for path in folder.rglob('*'):
                if path.is_file():files[str(path)]=(path.read_bytes(),path.stat().st_mtime_ns)
        return files

    def assert_preserved(self,rows,files):
        current=self.rows()
        for key,row in rows.items():self.assertEqual(current[key],row)
        for filename,metadata in files.items():
            path=Path(filename);self.assertEqual((path.read_bytes(),path.stat().st_mtime_ns),metadata)

    def resume(self,runner=None,update_metadata=None):
        metadata={'added_scenes':0,'added_variants':0,'discovery_complete':True} if update_metadata is None else update_metadata
        with contextlib.redirect_stdout(io.StringIO()):
            code=batch.retry_incomplete(self.args,runner=runner or self.fixture.runner,update_metadata=metadata)
        summary=json.loads((self.out/'all_scene_test_summary.json').read_text())
        return code,summary

    def test_default_update_still_excludes_pending_and_running(self):
        del self.args.include_pending
        before=self.preserved({'incomplete'});files=self.files_for(before)
        code,summary=self.resume()
        self.assertEqual(code,0)
        self.assertEqual([(job['scene']['scene_id'],job['candidate']['roots'][0]) for job in self.fixture.calls],
                         [('PartialScene','broken'),('IncompleteLater','later')])
        self.assertEqual(summary['retry_scope'],'incomplete_and_new')
        self.assertEqual(summary['selected_statuses'],{'incomplete':2})
        self.assert_preserved(before,files)

    def test_opt_in_includes_pending_and_running_across_code_update_but_preserves_passes(self):
        original=self.rows();before=self.preserved({'incomplete','pending','running'});files=self.files_for(before)
        with patch('gardens.batch.code_identity',return_value={'code':'new desktop renderer','packages':{'UnityPy':'test','Pillow':'test'}}):
            code,summary=self.resume()
        self.assertEqual(code,0)
        self.assertEqual([(job['scene']['scene_id'],job['candidate']['roots'][0]) for job in self.fixture.calls],
                         [('PartialScene','broken'),('PendingScene','pending'),('RunningScene','running'),('IncompleteLater','later')])
        self.assertEqual(summary['retry_scope'],'incomplete_pending_and_new')
        self.assertEqual(summary['selected_statuses'],{'incomplete':2,'pending':1,'running':1})
        self.assertEqual(summary['selected_variants'],4);self.assertEqual(summary['preserved_passed_variants'],2)
        self.assertEqual(summary['preserved_variants'],4);self.assertEqual(summary['attempted_variants'],4)
        self.assert_preserved(before,files)
        current=self.rows()
        for job in self.fixture.calls:
            key=(job['scene']['scene_id'],job['candidate']['roots'][0])
            self.assertEqual(current[key]['status'],'passed')
            self.assertNotEqual(current[key]['fingerprint'],original[key]['fingerprint'])

    def test_limit_keeps_unprocessed_pending_or_running_proof_until_same_flag_resumes(self):
        original=self.rows();self.args.test_limit=2
        code,summary=self.resume()
        self.assertEqual(code,1);self.assertEqual(summary['status'],'paused')
        self.assertEqual(summary['attempted_variants'],2);self.assertEqual(summary['remaining_selected_variants'],2)
        current=self.rows()
        for key in [('RunningScene','running'),('IncompleteLater','later')]:self.assertEqual(current[key],original[key])
        self.assertEqual(current[('RunningScene','running')]['status'],'running')
        self.args.test_limit=None;self.fixture.calls.clear()
        code,summary=self.resume()
        self.assertEqual(code,0)
        self.assertEqual([job['scene']['scene_id'] for job in self.fixture.calls],['RunningScene','IncompleteLater'])
        self.assertEqual(summary['selected_statuses'],{'incomplete':1,'running':1})

    def test_ctrl_c_keeps_original_unattempted_pending_running_rows_and_passed_files(self):
        original=self.rows();preserved=self.preserved({'incomplete','pending','running'});files=self.files_for(preserved)
        def interrupt(job,*args):
            if self.fixture.calls:raise KeyboardInterrupt
            return self.fixture.runner(job,*args)
        code,summary=self.resume(interrupt)
        self.assertEqual(code,130);self.assertEqual(summary['status'],'interrupted')
        self.assertEqual(summary['attempted_variants'],1);self.assertEqual(summary['remaining_selected_variants'],3)
        current=self.rows()
        for key in [('PendingScene','pending'),('RunningScene','running'),('IncompleteLater','later')]:
            self.assertEqual(current[key],original[key])
        self.assert_preserved(preserved,files)
        self.fixture.calls.clear();code,_=self.resume()
        self.assertEqual(code,0)
        self.assertEqual([job['scene']['scene_id'] for job in self.fixture.calls],['PendingScene','RunningScene','IncompleteLater'])

    def test_plan_reports_exact_status_selection_without_mutating_any_row_or_scene_file(self):
        self.args.plan_only=True;before=self.rows();files=self.files_for(before)
        code,summary=self.resume()
        self.assertEqual(code,0);self.assertEqual(self.fixture.calls,[])
        self.assertEqual(summary['status'],'planned');self.assertEqual(summary['selected_variants'],4)
        self.assertEqual(summary['selected_statuses'],{'incomplete':2,'pending':1,'running':1})
        self.assertEqual(summary['attempted_variants'],0);self.assertEqual(summary['remaining_selected_variants'],4)
        self.assert_preserved(before,files)

    def test_failed_and_blocked_results_are_not_enrolled_by_include_pending(self):
        before=self.preserved({'incomplete','pending','running'});files=self.files_for(before)
        def fail_running(job,*args):
            if job['scene']['scene_id']=='RunningScene':
                self.fixture.calls.append(job);return {'status':'failed','issues':['worker stopped'],'files':[]}
            return self.fixture.runner(job,*args)
        code,_=self.resume(fail_running)
        self.assertEqual(code,1);self.assert_preserved(before,files)
        failed=self.rows()[('RunningScene','running')]
        self.fixture.calls.clear();code,summary=self.resume()
        self.assertEqual(code,0);self.assertEqual(self.fixture.calls,[])
        self.assertEqual(summary['selected_variants'],0);self.assertEqual(summary['selected_statuses'],{})
        self.assertEqual(self.rows()[('RunningScene','running')],failed)

    def test_inactive_pending_or_running_jobs_remain_untouched(self):
        with sqlite3.connect(self.fixture.ledger_path) as db:
            db.execute("UPDATE jobs SET status='running' WHERE scene_id='InactiveScene'")
        original=self.rows()[('InactiveScene','inactive')]
        code,_=self.resume()
        self.assertEqual(code,0);self.assertEqual(self.rows()[('InactiveScene','inactive')],original)
        self.assertNotIn('InactiveScene',[job['scene']['scene_id'] for job in self.fixture.calls])

    def test_scene_limit_includes_every_selected_version_but_not_passed_sibling(self):
        with sqlite3.connect(self.fixture.ledger_path) as db:
            row=db.execute("SELECT * FROM jobs WHERE scene_id='PartialScene' AND variant_id='broken'").fetchone()
            definition=json.loads(row[5]);definition['destination']=str(self.out/'scenes/PartialScene/variant_pending_sibling')
            definition['candidate']['roots']=['pending_sibling']
            definition['fingerprint']='old pending sibling'
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)',
                       ('PartialScene','pending_sibling',1,'old pending sibling','pending',json.dumps(definition),
                        json.dumps({'status':'pending','files':[],'issues':[]}),1))
        preserved=self.preserved({'incomplete','pending','running'});files=self.files_for(preserved)
        self.args.test_limit=1;code,summary=self.resume()
        self.assertEqual(code,1)
        self.assertEqual([(job['scene']['scene_id'],job['candidate']['roots'][0]) for job in self.fixture.calls],
                         [('PartialScene','broken'),('PartialScene','pending_sibling')])
        self.assertEqual(summary['attempted_variants'],2);self.assertEqual(summary['remaining_selected_variants'],3)
        self.assert_preserved(preserved,files)


if __name__=='__main__':unittest.main()
