"""Incremental CLI orchestration with explicit fake worker outcomes, no network."""
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from PIL import Image

import gardens.batch as batch
import gardens.incremental as incremental
import gardens_renderer
from gardens.download import DownloadError,sha256
from gardens.scenes import Scene,variant_id


def candidate(name):
    url='https://assets.test/'+name+'.bundle'
    return {'roots':[name],'issues':[],
            'bundles':[{'url':url,'internal_id':url,'options':{}}]}


def match(scene,candidates):
    return {**asdict(scene),'status':'ambiguous' if len(candidates)>1 else 'matched',
            'issues':[],'candidates':candidates}


class CatalogueClient:
    def __init__(self,failed=()):self.calls=[];self.failed=set(failed)
    def fetch(self,url,destination,validator,refresh=False):
        self.calls.append((url,refresh))
        if url in self.failed:raise DownloadError('catalogue unavailable')
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(b'explicit fake packed catalogue')
        validator(destination)
        return {'status':'downloaded','sha256':sha256(destination)}


class UpdateCLITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.out=self.root/'output'
        self.checkpoint=self.out/'batch/tests.sqlite3'
        self.scenes=[Scene(1,'SavedPass','','https://assets.test/pass-items.json',[]),
                     Scene(2,'SavedIncomplete','','https://assets.test/incomplete-items.json',[])]
        self.new=Scene(3,'ch748VerifiedNewScene','','https://assets.test/new-items.json',[])
        self.original=candidate('old-pass');self.incomplete=candidate('old-incomplete')
        self.new_version=candidate('new-pass-version');self.new_candidate=candidate('new-scene')
        self.original_matches=[match(self.scenes[0],[self.original]),match(self.scenes[1],[self.incomplete])]
        self.updated_matches=[match(self.scenes[0],[self.original,self.new_version]),
                              match(self.scenes[1],[self.incomplete]),match(self.new,[self.new_candidate])]
        ledger=batch.Ledger(self.checkpoint)
        args=SimpleNamespace(output=self.out,frame=None,offline=False,timeout=30,retries=2,
                             test_timeout=300,retry_failed=False)
        try:
            ledger.prepare(self.scenes,self.original_matches,args,{'code':'old'})
            passed=self.images(self.out/'scenes/SavedPass','red')
            ledger.set_result('SavedPass',variant_id(self.original),{'status':'passed','issues':[],**passed})
            ledger.set_result('SavedIncomplete',variant_id(self.incomplete),{'status':'incomplete','issues':['old limitation'],'files':[]})
        finally:ledger.close()
        self.urls=['https://assets.test/catalogue-one.json']
        self.manifest=self.root/'catalogues.json';self.manifest.write_text(json.dumps({'remote_config':'https://assets.test/config.json','catalogues':self.urls}))
        self.chapter_report={'status':'discovered','new_chapters':[748],'unverified_scenes':[]}
        self.worker_calls=[]
        (self.out/'scene_matches.json').write_bytes(b'original matching proof must be retained')

    def images(self,destination,color):
        destination.mkdir(parents=True,exist_ok=True)
        files=[];artifacts={}
        for name in ('scene_empty.png','scene_complete.png','scene_solution.png'):
            path=destination/name;Image.new('RGBA',(2,2),color).save(path)
            files.append(str(path));artifacts[str(path.resolve())]=sha256(path)
        return {'files':files,'artifacts':artifacts}

    def rows(self):
        with sqlite3.connect(self.checkpoint) as db:
            db.row_factory=sqlite3.Row
            return {(r['scene_id'],r['variant_id']):dict(r) for r in db.execute('SELECT * FROM jobs')}

    def passed_files(self):
        return {str(path):(path.read_bytes(),path.stat().st_mtime_ns)
                for path in (self.out/'scenes/SavedPass').glob('scene_*.png')}

    def worker(self,job,*unused):
        self.worker_calls.append(job)
        return {'status':'passed','issues':[],**self.images(Path(job['destination']),'blue')}

    def invoke(self,*,client=None,report=None,enrollment=None,real_batch=False,extra=()):
        client=client or CatalogueClient();report=self.chapter_report if report is None else report
        discovery={'source':'live_remote_config','catalogues':self.urls,'config_path':str(self.root/'verified-config.json')}
        chapter_module=types.ModuleType('gardens.chapters')
        chapter_module.discover_chapters=Mock(return_value=([*self.scenes,self.new],report))
        matches=json.loads(json.dumps(self.updated_matches))
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules,{'gardens.chapters':chapter_module}))
            stack.enter_context(patch('gardens_renderer.read_scenes',return_value=(self.scenes,[])))
            stack.enter_context(patch('gardens_renderer.Downloader',return_value=client))
            source=stack.enter_context(patch('gardens.discovery.catalogue_sources',return_value=discovery))
            stack.enter_context(patch('gardens_renderer.parse_catalogue',return_value=([],{'format':'test'})))
            stack.enter_context(patch('gardens_renderer.Index',return_value=SimpleNamespace(locations={})))
            matching=stack.enter_context(patch('gardens_renderer.match_scenes',return_value=matches))
            prepare=stack.enter_context(patch.object(batch.Ledger,'prepare',side_effect=AssertionError('must not reset the saved roster')))
            if real_batch:
                enroll=stack.enter_context(patch('gardens.incremental.enroll_updates',wraps=incremental.enroll_updates))
                retry=stack.enter_context(patch('gardens.batch.retry_incomplete',wraps=batch.retry_incomplete))
                stack.enter_context(patch('gardens.batch.execute_job',side_effect=self.worker))
                stack.enter_context(patch('gardens.batch.code_identity',return_value={'code':'updated renderer','packages':{'UnityPy':'test','Pillow':'test'}}))
            else:
                enrolled={'added_scenes':1,'added_variants':2,'discovery_complete':True} if enrollment is None else enrollment
                enroll=stack.enter_context(patch('gardens.incremental.enroll_updates',return_value=enrolled))
                retry=stack.enter_context(patch('gardens.batch.retry_incomplete',return_value=0))
            with contextlib.redirect_stdout(io.StringIO()):
                code=gardens_renderer.main(['--update','--output',str(self.out),'--catalogues',str(self.manifest),*extra])
        return code,SimpleNamespace(client=client,source=source,chapters=chapter_module.discover_chapters,
                                    matching=matching,enroll=enroll,retry=retry,prepare=prepare,matches=matches)

    def test_missing_checkpoint_fails_before_discovery_and_creates_no_output(self):
        target=self.root/'missing-run'
        with patch('gardens_renderer.Downloader',side_effect=AssertionError('must not discover')), \
             patch('gardens_renderer.read_scenes',side_effect=AssertionError('must not read scenes')), \
             contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
            gardens_renderer.main(['--update','--output',str(target)])
        self.assertEqual(error.exception.code,2);self.assertFalse(target.exists())

    def test_update_refreshes_catalogues_and_uses_extended_chapter_order(self):
        before=self.rows();images=self.passed_files()
        code,used=self.invoke()
        self.assertEqual(code,0)
        self.assertEqual(used.client.calls,[(self.urls[0],True)])
        self.assertEqual(used.chapters.call_args.args[0],self.scenes)
        self.assertEqual(used.chapters.call_args.args[4],str(self.root/'verified-config.json'))
        self.assertEqual([scene.scene_id for scene in used.matching.call_args.args[0]],
                         ['SavedPass','SavedIncomplete','ch748VerifiedNewScene'])
        self.assertTrue(used.enroll.call_args.kwargs['complete'])
        self.assertEqual(used.retry.call_args.kwargs['update_metadata']['catalogue_source'],'live_remote_config')
        used.prepare.assert_not_called()
        self.assertEqual(self.rows(),before);self.assertEqual(self.passed_files(),images)
        self.assertEqual((self.out/'scene_matches.json').read_bytes(),b'original matching proof must be retained')
        self.assertTrue((self.out/'update_scene_matches.json').exists())

    def test_unverified_manager_cannot_be_enrolled_as_eligible_match(self):
        report={**self.chapter_report,'unverified_scenes':[{'scene_id':self.new.scene_id,'issues':['Item Manager unavailable']}]}
        code,used=self.invoke(report=report)
        self.assertEqual(code,1)
        rows=used.enroll.call_args.args[2];new=next(row for row in rows if row['scene_id']==self.new.scene_id)
        self.assertEqual(new['status'],'unresolved');self.assertIn('Item Manager unavailable',new['issues'])
        self.assertEqual(next(row for row in rows if row['scene_id']=='SavedPass')['status'],'ambiguous')

    def test_persisted_blocked_update_still_returns_one_without_new_enrollment(self):
        enrollment={'added_scenes':0,'added_variants':0,'discovery_complete':True,'blocked_update_variants':1}
        for extra,expected in (((),1),(('--plan-only',),0)):
            with self.subTest(extra=extra):
                code,used=self.invoke(enrollment=enrollment,extra=extra)
                self.assertEqual(code,expected);used.retry.assert_called_once()
                self.assertEqual(used.retry.return_value,0)
                self.assertEqual(used.retry.call_args.kwargs['update_metadata']['blocked_update_variants'],1)

    def test_plan_only_can_report_unverified_metadata_without_claiming_render_success(self):
        report={**self.chapter_report,'unverified_scenes':[{'scene_id':self.new.scene_id,'issues':['Item Manager unavailable']}]}
        code,used=self.invoke(report=report,extra=['--plan-only'])
        self.assertEqual(code,0)
        self.assertTrue(used.retry.call_args.args[0].plan_only)
        self.assertEqual(used.retry.call_args.kwargs['update_metadata']['chapter_discovery']['unverified_scenes'],report['unverified_scenes'])

    def test_partial_catalogue_failure_retries_saved_work_without_enrolling_new_coverage(self):
        self.urls.append('https://assets.test/catalogue-two.json')
        before=self.rows();images=self.passed_files()
        code,used=self.invoke(client=CatalogueClient(failed=[self.urls[-1]]))
        self.assertEqual(code,1);used.chapters.assert_not_called()
        self.assertFalse(used.enroll.call_args.kwargs['complete']);used.retry.assert_called_once()
        self.assertEqual(used.retry.call_args.kwargs['update_metadata']['chapter_discovery']['status'],'unavailable')
        self.assertEqual(self.rows(),before);self.assertEqual(self.passed_files(),images)

    def test_unavailable_chapter_metadata_cannot_claim_successful_update(self):
        code,used=self.invoke(report={'status':'unavailable','unverified_scenes':[],'issues':['source unavailable']})
        self.assertEqual(code,1);used.retry.assert_called_once()

    def test_real_merge_and_retry_preserve_passed_root_and_only_execute_incomplete_or_new(self):
        passed_key=('SavedPass',variant_id(self.original));before=self.rows()[passed_key];images=self.passed_files()
        code,used=self.invoke(real_batch=True)
        self.assertEqual(code,0)
        actual={(job['scene']['scene_id'],variant_id(job['candidate'])) for job in self.worker_calls}
        self.assertEqual(actual,{('SavedIncomplete',variant_id(self.incomplete)),
                                 ('SavedPass',variant_id(self.new_version)),(self.new.scene_id,variant_id(self.new_candidate))})
        self.assertEqual(self.rows()[passed_key],before);self.assertEqual(self.passed_files(),images)
        summary=json.loads((self.out/'all_scene_test_summary.json').read_text())
        self.assertEqual(summary['retry_scope'],'incomplete_and_new')
        self.assertEqual(summary['preserved_passed_variants'],1)
        self.assertEqual(summary['attempted_variants'],3)
        self.assertEqual(summary['update_discovery']['added_scenes'],1)
        self.assertEqual(summary['update_discovery']['added_variants'],2)
        used.prepare.assert_not_called()
        # An identical update discovers no additional jobs and starts no workers.
        self.worker_calls.clear();current=self.rows();code,_=self.invoke(real_batch=True)
        self.assertEqual(code,0);self.assertEqual(self.worker_calls,[]);self.assertEqual(self.rows(),current)

    def test_update_plan_registers_only_new_jobs_and_runs_no_workers(self):
        passed_key=('SavedPass',variant_id(self.original));before=self.rows()[passed_key];images=self.passed_files()
        code,_=self.invoke(real_batch=True,extra=['--plan-only'])
        self.assertEqual(code,0);self.assertEqual(self.worker_calls,[])
        self.assertEqual(self.rows()[passed_key],before);self.assertEqual(self.passed_files(),images)
        summary=json.loads((self.out/'all_scene_test_summary.json').read_text())
        self.assertEqual(summary['status'],'planned');self.assertEqual(summary['selected_variants'],3)

    def test_update_is_a_distinct_selector_and_rejects_retry_failed(self):
        for flags in (['--update','--retry-incomplete'],['--update','--retry-failed']):
            with self.subTest(flags=flags),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
                gardens_renderer.main([*flags,'--output',str(self.out)])
            self.assertEqual(error.exception.code,2)


if __name__=='__main__':unittest.main()
