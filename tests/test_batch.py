"""Batch orchestration tests use explicit fake outcomes, not fake scene passes."""
import contextlib
import csv
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from gardens.batch import Ledger,execute_job,reusable,run_batch
from gardens.download import atomic_json,sha256
from gardens.scenes import Scene


def candidate(name):
    url='https://assets.test/'+name+'.bundle'
    return {'roots':[name],'issues':[],'bundles':[{'url':url,'internal_id':url,'options':{}}]}


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name)
        self.args=SimpleNamespace(output=self.out,frame=None,offline=True,timeout=2,retries=0,
                                  test_timeout=3,test_limit=None,plan_only=False,retry_failed=False)
        self.scenes=[Scene(1,'SceneA','','',[]),Scene(2,'SceneBFTD1','','',[]),Scene(3,'Missing','','',[])]
        self.matches=[{'scene_id':'SceneA','order':1,'status':'ambiguous','issues':[],'candidates':[candidate('a'),candidate('b')]},
                      {'scene_id':'SceneBFTD1','order':2,'status':'matched','issues':[],'candidates':[candidate('c')]},
                      {'scene_id':'Missing','order':3,'status':'missing','issues':[],'candidates':[]}]
        self.calls=[]

    def runner(self,job,*args):
        self.calls.append((job['scene']['scene_id'],job['candidate']['roots'][0]))
        if job['scene']['scene_id']=='SceneBFTD1':return {'status':'incomplete','issues':['FTD unsupported'],'files':[]}
        dest=Path(job['destination']);dest.mkdir(parents=True,exist_ok=True)
        files=[];artifacts={}
        for name in ('scene_empty.png','scene_complete.png','scene_solution.png'):
            p=dest/name;Image.new('RGBA',(2,2),'red').save(p);files.append(str(p));artifacts[str(p.resolve())]=sha256(p)
        return {'status':'passed','issues':[],'files':files,'artifacts':artifacts}

    def run_batch(self,runner=None):
        with contextlib.redirect_stdout(io.StringIO()):
            code=run_batch(self.scenes,self.matches,self.args,runner or self.runner)
        return code,json.loads((self.out/'all_scene_test_summary.json').read_text())

    def test_limit_tests_all_versions_and_reports_every_scene_then_resumes(self):
        self.args.test_limit=1
        code,r=self.run_batch()
        self.assertEqual(code,1);self.assertEqual(r['status'],'paused')
        self.assertEqual(r['scenes'],3);self.assertEqual(r['variants'],4)
        self.assertEqual(r['variant_results'],{'blocked':1,'passed':2,'pending':1})
        self.assertEqual(len(self.calls),2)
        with (self.out/'scene_test_report.csv').open(encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f))
        self.assertEqual([r['scene_id'] for r in rows],['SceneA','SceneBFTD1','Missing'])
        self.assertEqual(rows[1]['status'],'pending');self.assertEqual(rows[2]['status'],'blocked')
        self.args.test_limit=None;self.calls.clear()
        code,r=self.run_batch()
        self.assertEqual(code,1);self.assertEqual(r['status'],'completed');self.assertTrue(r['coverage_complete'])
        self.assertEqual(self.calls,[('SceneBFTD1','c')])
        self.calls.clear();self.run_batch();self.assertEqual(self.calls,[])
        self.args.retry_failed=True;self.run_batch();self.assertEqual(self.calls,[('SceneBFTD1','c')])

    def test_interruption_keeps_completed_checkpoint(self):
        def interrupt(job,*args):
            if len(self.calls):raise KeyboardInterrupt
            return self.runner(job,*args)
        code,r=self.run_batch(interrupt)
        self.assertEqual(code,130);self.assertEqual(r['status'],'interrupted')
        self.assertEqual(r['variant_results']['passed'],1)
        self.calls.clear();_,r=self.run_batch()
        self.assertEqual(len(self.calls),2)
        self.assertEqual(r['variant_results'],{'blocked':1,'incomplete':1,'passed':2})

    def test_report_keeps_unverified_pointers_separate_and_reads_older_results(self):
        def runner(job,*args):
            result={'status':'incomplete','issues':['Unclassified named visuals'],'files':[],
                    'hidden_objects':61,'named_pointers':61}
            if job['candidate']['roots'][0]=='a':
                result.update(unverified_named_pointers=2,total_named_pointers=63)
            return result
        _,report=self.run_batch(runner)
        self.assertNotIn('passed',report['variant_results'])
        with (self.out/'test_report.csv').open(encoding='utf-8-sig',newline='') as f:
            rows=[row for row in csv.DictReader(f) if row['scene_id']=='SceneA']
        current=next(row for row in rows if '/a.bundle' in row['bundle_urls'])
        legacy=next(row for row in rows if '/b.bundle' in row['bundle_urls'])
        self.assertEqual(current['status'],'incomplete')
        self.assertEqual((current['hidden_objects'],current['named_pointers'],
                          current['unverified_named_pointers'],current['total_named_pointers']),('61','61','2','63'))
        self.assertEqual(legacy['unverified_named_pointers'],'')
        self.assertEqual(legacy['total_named_pointers'],'61')

    def test_plan_only_creates_full_roster_without_workers(self):
        self.args.plan_only=True
        code,r=self.run_batch()
        self.assertEqual(code,0);self.assertEqual(self.calls,[])
        self.assertEqual(r['status'],'planned');self.assertEqual(r['variants'],4)
        self.assertEqual(r['variant_results'],{'blocked':1,'pending':3})

    def test_corrupt_output_and_changed_code_invalidate_passes(self):
        self.run_batch();self.calls.clear()
        p=next((self.out/'scenes/SceneA').glob('variant_*/scene_complete.png'));p.write_bytes(b'changed')
        self.run_batch();self.assertEqual(len(self.calls),1)
        self.calls.clear()
        with patch('gardens.batch.code_identity',return_value={'code':'updated','packages':{'UnityPy':'test','Pillow':'test'}}):
            self.run_batch()
        self.assertEqual(len(self.calls),3)

    def test_pass_requires_every_image_to_have_a_verified_hash(self):
        p=self.out/'input';p.write_bytes(b'data')
        self.assertFalse(reusable({'files':['missing1','missing2','missing3'],'artifacts':{str(p):sha256(p)}}))

    def test_locked_csv_does_not_stop_workers_or_checkpoints(self):
        replace=Path.replace
        def locked(path,target):
            if Path(target).name=='test_report.csv':raise PermissionError('Windows file in use')
            return replace(path,target)
        with patch.object(Path,'replace',locked):
            code,r=self.run_batch()
        self.assertEqual(len(self.calls),3)
        self.assertTrue(r['coverage_complete'])
        self.assertTrue(r['report_warnings'])
        self.assertTrue((self.out/'test_report.csv.tmp').exists())
        self.calls.clear();self.run_batch()
        self.assertEqual(self.calls,[])
        self.assertTrue((self.out/'test_report.csv').exists())

    def test_zero_scene_run_is_not_a_pass(self):
        with self.assertRaisesRegex(ValueError,'no unique scenes'):
            run_batch([],[],self.args)

    def test_pass_cannot_reuse_a_duplicate_image_list(self):
        p=self.out/'scene_complete.png';p.write_bytes(b'data')
        self.assertFalse(reusable({'files':[str(p)]*3,'artifacts':{str(p):sha256(p)}}))

    def test_timeout_and_crash_are_failed_results(self):
        job={'fingerprint':'new'}
        with patch('gardens.batch.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',.01)):
            r=execute_job(job,self.out/'job.json',self.out/'result.json',self.out/'log.txt',.01)
        self.assertEqual(r['status'],'failed');self.assertIn('timed out',r['issues'][0])
        with patch('gardens.batch.subprocess.run',return_value=SimpleNamespace(returncode=9)):
            r=execute_job(job,self.out/'job.json',self.out/'result.json',self.out/'log.txt',3)
        self.assertEqual(r['status'],'failed');self.assertIn('exited 9',r['issues'][0])

    def test_stale_worker_result_is_not_accepted(self):
        def stale(*args,**kwargs):
            atomic_json(self.out/'result.json',{'status':'passed','fingerprint':'old','files':[]})
            return SimpleNamespace(returncode=0)
        with patch('gardens.batch.subprocess.run',side_effect=stale):
            r=execute_job({'fingerprint':'new'},self.out/'job.json',self.out/'result.json',self.out/'log.txt',3)
        self.assertEqual(r['status'],'failed')


if __name__=='__main__':unittest.main()
