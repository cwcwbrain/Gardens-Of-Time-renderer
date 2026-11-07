"""Generic pose publication/cache/recovery with declared synthetic snapshots.

The injected renderer isolates filesystem/checkpoint behavior from the selector's
separate real-source rendering tests. Cached input bytes themselves are real.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image,PngImagePlugin

from gardens.batch import Ledger
from gardens.download import cache_name,sha256
from gardens.pose_refresh import refresh_poses

ROOT=Path(__file__).resolve().parents[1]


class PoseRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name)/'output';self.checkpoint=self.out/'batch/tests.sqlite3'
        ledger=Ledger(self.checkpoint);ledger.close()
        self.bundle_url='https://assets.test/scene.bundle';self.manager_url='https://assets.test/items.json'
        self.bundle=self.out/'bundles'/cache_name(self.bundle_url,'.bundle')
        self.manager=self.out/'item_managers'/cache_name(self.manager_url,'.json')
        for path,source,url in ((self.bundle,next((ROOT/'samples').glob('ancient*.bundle')),self.bundle_url),
                                (self.manager,ROOT/'tests/fixtures/AncientGizaRiverfront_items.json',self.manager_url)):
            path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,path)
            Path(str(path)+'.verified.json').write_text(json.dumps({'url':url,'sha256':sha256(path)}))
        self.manager_data=json.loads(self.manager.read_text());self.calls=[];self.render_mode='changed'
        self.dest=self.add_job('AnimatedScene',1,'passed',1)
        self.add_job('BackgroundScene',2,'passed',1,nested=False)
        self.add_job('InactiveAnimation',3,'passed',0)
        for order,status in enumerate(('incomplete','failed','pending','running','blocked'),4):self.add_job(status.title(),order,status,1)

    def marker(self):
        return {'object_id':143,'manager_key':'banana_anc_giz_riv','label':'Banana',
                'label_verified':True,'labels':self.manager_data['banana_anc_giz_riv']}

    def report(self,files,nested=True):
        return {'status':'validated_snapshot','issues':[],'files':files,'snapshot_frame':7,'root_timeline':0,'root_objects':3,
                'scene_dimensions':[20,20],'solution_dimensions':[20,20],'identified_hidden_objects':1,
                'named_pointers':1,'solution_markers':1,'findable_item_manager_entries':1,
                'matched_item_manager_entries':1,'item_manager_complete':True,'bundle_sha256':sha256(self.bundle),
                'nested_snapshot_instances':[{'instance_path':[77],'timeline_id':10,'frame':1}] if nested else [],
                'item_manager_sha256':hashlib.sha256(json.dumps(self.manager_data,sort_keys=True).encode()).hexdigest()}

    def add_job(self,sid,order,status,active,nested=True):
        dest=self.out/'scenes'/sid;dest.mkdir(parents=True,exist_ok=True);files=[];artifacts={}
        for name,color in [('scene_empty.png','red'),('scene_complete.png','blue'),('scene_solution.png','yellow')]:
            path=dest/name;Image.new('RGBA',(20,20),color).save(path);files.append(str(path));artifacts[str(path)]=sha256(path)
        for name,data in [('validation_report.json',self.report(files,nested)),('solution_objects.json',[self.marker()]),
                          ('objects.json',[{'object_id':143,'name':'banana','kind':'hidden_object'}])]:
            path=dest/name;path.write_text(json.dumps(data));artifacts[str(path)]=sha256(path)
        for path in (self.bundle,self.manager):artifacts[str(path)]=sha256(path)
        fp='historical-proof-'+sid
        definition={'scene':{'order':order,'scene_id':sid,'thumbnail_url':'','item_manager_url':self.manager_url,'original_row':[]},
                    'candidate':{'bundles':[{'url':self.bundle_url,'internal_id':self.bundle_url,'options':{}}]},
                    'destination':str(dest),'output':str(self.out),'frame':None,'offline':True,'timeout':30,'retries':0,'fingerprint':fp}
        result={'status':status,'issues':[],'files':files if status=='passed' else [],'artifacts':artifacts if status=='passed' else {},
                'fingerprint':fp,'hidden_objects':1,'named_pointers':1}
        with contextlib.closing(sqlite3.connect(self.checkpoint)) as db,db:
            db.execute('INSERT INTO scenes VALUES (?,?,?,?,?)',(sid,order,'matched','[]',active))
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)',(sid,'main',order,fp,status,json.dumps(definition),json.dumps(result),active))
        return dest

    def rows(self):
        with contextlib.closing(sqlite3.connect(self.checkpoint)) as db:
            db.row_factory=sqlite3.Row
            return {row['scene_id']:dict(row) for row in db.execute('SELECT * FROM jobs')}

    def snapshot(self):
        return {str(p):(p.read_bytes(),p.stat().st_mtime_ns) for p in (self.out/'scenes').rglob('*') if p.is_file()}

    def assert_snapshot(self,values):
        for raw,expected in values.items():
            p=Path(raw);self.assertEqual((p.read_bytes(),p.stat().st_mtime_ns),expected)

    def render(self,bundle,manager,stage,frame=None):
        from gardens.pose_refresh import POSE_SELECTION_VERSION
        self.calls.append(str(stage));self.assertEqual(Path(bundle),self.bundle)
        self.assertEqual(manager,self.manager_data);self.assertEqual(frame,7)
        stage=Path(stage);self.assertNotEqual(stage,self.dest);files=[]
        for name,color in [('scene_empty.png','green'),('scene_complete.png','cyan'),('scene_solution.png','orange')]:
            path=stage/name;files.append(str(path))
            if self.render_mode=='unchanged' or (self.render_mode=='solution_only' and name!='scene_solution.png'):
                shutil.copyfile(self.dest/name,path)
            elif self.render_mode=='encoding_only':
                metadata=PngImagePlugin.PngInfo();metadata.add_text('test','same pixels, different encoding')
                with Image.open(self.dest/name) as image:image.save(path,pnginfo=metadata,compress_level=0)
            else:Image.new('RGBA',(20,20),color).save(path)
        report={**self.report(files),'pose_selection_version':POSE_SELECTION_VERSION,
                'nested_snapshot_instances':[{'instance_path':[77],'timeline_id':10,'frame':12}],
                'solution_layout':'numbered_local_pointers_v1'}
        for name in ('objects.json','solution_objects.json'):shutil.copyfile(self.dest/name,stage/name)
        (stage/'validation_report.json').write_text(json.dumps(report))
        return report

    def refresh(self,renderer=None,**kwargs):
        with contextlib.redirect_stdout(io.StringIO()),patch('gardens.download.Downloader.fetch',side_effect=AssertionError('pose refresh must not download')):
            return refresh_poses(self.out,renderer=renderer or self.render,**kwargs)

    def test_changed_snapshots_publish_only_changed_artifacts_and_preserve_other_rows(self):
        before=self.rows();files=self.snapshot();old=json.loads(before['AnimatedScene']['result'])
        report=self.refresh();self.assertEqual(report['status'],'completed',report)
        self.assertEqual(report['changed_scene_ids'],['AnimatedScene']);self.assertEqual(len(self.calls),1)
        self.assertEqual(report['summary']['non_nested_variants'],1)
        after=self.rows()
        for sid,row in before.items():
            if sid!='AnimatedScene':self.assertEqual(after[sid],row)
            else:
                for field in row:
                    if field!='result':self.assertEqual(after[sid][field],row[field])
        changed={'scene_empty.png','scene_complete.png','scene_solution.png','validation_report.json'}
        self.assertEqual(set(report['results'][0]['published_artifacts']),changed)
        self.assert_snapshot({p:v for p,v in files.items() if Path(p).parent!=self.dest or Path(p).name not in changed})
        result=json.loads(after['AnimatedScene']['result']);self.assertEqual(result['files'],old['files'])
        self.assertTrue(result['pose_refresh']['native_recertified']);self.assertEqual(result['fingerprint'],old['fingerprint'])
        for path,digest in result['artifacts'].items():self.assertEqual(sha256(path),digest)

    def test_arbitrary_nested_scene_names_are_candidates_without_any_source_registry(self):
        self.add_job('AnotherNestedScene',20,'passed',1)
        report=self.refresh();self.assertEqual(report['status'],'completed',report)
        self.assertEqual(set(report['changed_scene_ids']),{'AnimatedScene','AnotherNestedScene'})
        self.assertEqual(len(self.calls),2)

    def test_shared_active_passed_file_targets_are_not_overwritten(self):
        self.add_job('AliasedAnimation',20,'passed',1)
        with contextlib.closing(sqlite3.connect(self.checkpoint)) as db,db:
            original=db.execute("SELECT result FROM jobs WHERE scene_id='AnimatedScene'").fetchone()[0]
            raw=db.execute("SELECT definition FROM jobs WHERE scene_id='AliasedAnimation'").fetchone()[0]
            definition=json.loads(raw);definition['destination']=str(self.dest)
            db.execute("UPDATE jobs SET definition=?,result=? WHERE scene_id='AliasedAnimation'",(json.dumps(definition),original))
        before=self.rows();files=self.snapshot();report=self.refresh()
        self.assertEqual(report['status'],'partial');self.assertEqual(report['changed_scene_ids'],[])
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)
        issues=[issue for entry in report['results'] for issue in entry['issues']]
        self.assertTrue(any('also owned' in issue for issue in issues))

    def test_unchanged_pixels_do_not_rewrite_any_scene_file_or_checkpoint_row(self):
        self.render_mode='unchanged';before=self.rows();files=self.snapshot()
        report=self.refresh();self.assertEqual(report['status'],'completed',report)
        self.assertEqual(report['summary']['unchanged_variants'],1);self.assertEqual(report['changed_scene_ids'],[])
        self.assertEqual(self.rows(),before);self.assert_snapshot(files);self.assertIsNone(report['backup_path'])

    def test_png_encoding_changes_without_pixel_changes_do_not_publish(self):
        self.render_mode='encoding_only';before=self.rows();files=self.snapshot()
        report=self.refresh();self.assertEqual(report['status'],'completed',report)
        self.assertEqual(report['summary']['unchanged_variants'],1);self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_unchanged_analysis_cache_skips_future_rendering_even_without_input_caches(self):
        self.render_mode='unchanged';self.refresh()
        for path in (self.bundle,self.manager):path.unlink();Path(str(path)+'.verified.json').unlink()
        before=self.rows();files=self.snapshot();self.calls.clear()
        report=self.refresh();self.assertEqual(report['status'],'completed',report)
        self.assertEqual(report['summary']['reused_variants'],1);self.assertEqual(self.calls,[])
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_selector_version_change_invalidates_the_unchanged_analysis_cache(self):
        self.render_mode='unchanged';self.refresh();self.calls.clear();before=self.rows();files=self.snapshot()
        with patch('gardens.pose_refresh.POSE_SELECTION_VERSION','next_general_version'):report=self.refresh()
        self.assertEqual(report['status'],'completed');self.assertEqual(len(self.calls),1)
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_current_selector_marker_reuses_images_after_caches_are_removed(self):
        self.refresh()
        for path in (self.bundle,self.manager):path.unlink();Path(str(path)+'.verified.json').unlink()
        before=self.rows();files=self.snapshot();self.calls.clear();report=self.refresh()
        self.assertEqual(report['status'],'completed',report);self.assertEqual(self.calls,[])
        self.assertEqual(report['summary']['reused_variants'],1);self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_non_nested_scene_images_and_inputs_are_not_loaded(self):
        self.refresh();other=self.out/'scenes/BackgroundScene'
        for path in other.iterdir():
            if path.name!='validation_report.json':path.unlink()
        for path in (self.bundle,self.manager):path.unlink();Path(str(path)+'.verified.json').unlink()
        before=self.rows();self.calls.clear();report=self.refresh()
        self.assertEqual(report['status'],'completed',report);self.assertEqual(self.calls,[])
        self.assertEqual(report['summary']['non_nested_variants'],1);self.assertEqual(self.rows(),before)

    def test_plan_only_changes_no_checkpoint_scene_files_or_reports(self):
        before=self.rows();files=self.snapshot();db_bytes=self.checkpoint.read_bytes()
        report=self.refresh(plan_only=True);self.assertEqual(report['summary']['planned_variants'],1)
        self.assertEqual(self.calls,[]);self.assertEqual(self.checkpoint.read_bytes(),db_bytes)
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)
        self.assertFalse((self.out/'pose_refresh_report.json').exists());self.assertFalse((self.out/'.pose_refresh_staging').exists())

    def test_solution_only_change_retains_native_png_bytes_and_mtimes(self):
        self.render_mode='solution_only';before=self.snapshot();report=self.refresh()
        self.assertEqual(report['status'],'completed',report)
        self.assertEqual(report['results'][0]['published_artifacts'],['scene_solution.png','validation_report.json'])
        self.assert_snapshot({p:v for p,v in before.items() if Path(p).name not in ('scene_solution.png','validation_report.json') or Path(p).parent!=self.dest})

    def test_missing_input_receipt_or_edited_input_skips_and_preserves_old_pass(self):
        Path(str(self.manager)+'.verified.json').unlink();before=self.rows();files=self.snapshot()
        report=self.refresh();self.assertEqual(report['status'],'partial');self.assertEqual(self.calls,[])
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_bundle_identity_must_match_original_validation_even_with_a_new_receipt(self):
        self.bundle.write_bytes(b'changed input');digest=sha256(self.bundle)
        Path(str(self.bundle)+'.verified.json').write_text(json.dumps({'url':self.bundle_url,'sha256':digest}))
        with contextlib.closing(sqlite3.connect(self.checkpoint)) as db,db:
            raw=db.execute("SELECT result FROM jobs WHERE scene_id='AnimatedScene'").fetchone()[0]
            result=json.loads(raw);result['artifacts'][str(self.bundle)]=digest
            db.execute("UPDATE jobs SET result=? WHERE scene_id='AnimatedScene'",(json.dumps(result),))
        before=self.rows();files=self.snapshot();report=self.refresh()
        self.assertEqual(report['status'],'partial');self.assertEqual(self.calls,[])
        self.assertTrue(any('bundle differs' in x for x in report['results'][0]['issues']))
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_edited_metadata_does_not_use_an_earlier_analysis_cache(self):
        self.render_mode='unchanged';self.refresh();self.calls.clear()
        path=self.dest/'validation_report.json';data=json.loads(path.read_text());data['snapshot_frame']=8;path.write_text(json.dumps(data))
        before=self.rows();files=self.snapshot();report=self.refresh()
        self.assertEqual(report['status'],'partial');self.assertEqual(self.calls,[])
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_fresh_incomplete_or_changed_binding_counts_frame_and_version_are_preserved(self):
        for mode in ('incomplete','label','counts','frame','root','objects','version'):
            with self.subTest(mode=mode):
                before=self.rows();files=self.snapshot()
                def changed(*args,**kwargs):
                    report=self.render(*args,**kwargs)
                    if mode=='incomplete':report.update(status='incomplete',issues=['Missing clue'])
                    elif mode=='label':(Path(args[2])/'solution_objects.json').write_text(json.dumps([{**self.marker(),'label':'Guessed'}]))
                    elif mode=='counts':report['solution_markers']=0
                    elif mode=='frame':report['snapshot_frame']=8
                    elif mode=='root':report['root_timeline']=10
                    elif mode=='objects':report['root_objects']=999
                    else:report['pose_selection_version']='different_version'
                    return report
                report=self.refresh(changed);self.assertEqual(report['status'],'partial')
                self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_concurrent_database_change_prevents_publication(self):
        before=self.rows();files=self.snapshot()
        def concurrent(*args,**kwargs):
            report=self.render(*args,**kwargs)
            with contextlib.closing(sqlite3.connect(self.checkpoint)) as db,db:db.execute("UPDATE scenes SET issues='[\"writer\"]' WHERE id='BackgroundScene'")
            return report
        report=self.refresh(concurrent);self.assertEqual(report['status'],'partial')
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_interrupt_during_subset_publication_restores_all_previous_artifacts(self):
        self.render_mode='solution_only';before=self.rows();files=self.snapshot();replace=os.replace;stopped=[]
        def interrupt(source,destination):
            value=replace(source,destination)
            if Path(destination)==self.dest/'scene_solution.png' and not stopped:
                stopped.append(True);raise KeyboardInterrupt
            return value
        with patch('gardens.pose_refresh.os.replace',side_effect=interrupt):report=self.refresh()
        self.assertEqual(report['status'],'interrupted');self.assertEqual(stopped,[True])
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)
        self.assertEqual(self.refresh()['status'],'completed')

    def test_abrupt_exit_is_recovered_before_the_next_audit(self):
        script=r'''
import importlib.util,json,os,sys
from pathlib import Path
from gardens.download import cache_name
spec=importlib.util.spec_from_file_location('pose_fixtures','tests/test_pose_refresh.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
fixture=module.PoseRefreshTests()
fixture.out=Path(sys.argv[1]);fixture.checkpoint=fixture.out/'batch/tests.sqlite3'
fixture.bundle_url='https://assets.test/scene.bundle';fixture.manager_url='https://assets.test/items.json'
fixture.bundle=fixture.out/'bundles'/cache_name(fixture.bundle_url,'.bundle')
fixture.manager=fixture.out/'item_managers'/cache_name(fixture.manager_url,'.json')
fixture.manager_data=json.loads(fixture.manager.read_text());fixture.calls=[];fixture.render_mode='solution_only';fixture.dest=fixture.out/'scenes/AnimatedScene'
replace=os.replace
def terminate(source,destination):
 value=replace(source,destination)
 if Path(destination)==fixture.dest/'scene_solution.png':os._exit(23)
 return value
os.replace=terminate
fixture.refresh()
'''
        before=self.rows();files=self.snapshot()
        process=subprocess.run([sys.executable,'-c',script,str(self.out)],cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(process.returncode,23,process.stdout+process.stderr)
        self.assertTrue((self.out/'batch/pose_refresh_pending.json').exists())
        with self.assertRaisesRegex(ValueError,'pending pose refresh'):self.refresh(plan_only=True)
        self.assertEqual(self.rows(),before)
        report=self.refresh();self.assertEqual(report['status'],'completed',report)
        self.assert_snapshot({p:v for p,v in files.items() if Path(p).parent!=self.dest})
        result=json.loads(self.rows()['AnimatedScene']['result'])
        for path,digest in result['artifacts'].items():self.assertEqual(sha256(path),digest)

    def test_completed_unchanged_analysis_survives_an_abrupt_later_renderer_exit(self):
        self.add_job('AnotherNestedScene',20,'passed',1)
        script=r'''
import importlib.util,json,os,sys
from pathlib import Path
from gardens.download import cache_name
spec=importlib.util.spec_from_file_location('pose_fixtures','tests/test_pose_refresh.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
fixture=module.PoseRefreshTests()
fixture.out=Path(sys.argv[1]);fixture.checkpoint=fixture.out/'batch/tests.sqlite3'
fixture.bundle_url='https://assets.test/scene.bundle';fixture.manager_url='https://assets.test/items.json'
fixture.bundle=fixture.out/'bundles'/cache_name(fixture.bundle_url,'.bundle')
fixture.manager=fixture.out/'item_managers'/cache_name(fixture.manager_url,'.json')
fixture.manager_data=json.loads(fixture.manager.read_text());fixture.calls=[];fixture.render_mode='unchanged';fixture.dest=fixture.out/'scenes/AnimatedScene'
def stop_on_second(*args,**kwargs):
 if fixture.calls:os._exit(23)
 return fixture.render(*args,**kwargs)
fixture.refresh(renderer=stop_on_second)
'''
        before=self.rows();files=self.snapshot()
        process=subprocess.run([sys.executable,'-c',script,str(self.out)],cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(process.returncode,23,process.stdout+process.stderr)
        saved=json.loads((self.out/'pose_refresh_report.json').read_text())
        self.assertEqual(saved['run_state'],'running');self.assertEqual(saved['status'],'in_progress')
        self.assertEqual(len(saved['checked_sources']),1)
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)
        self.render_mode='unchanged';self.calls.clear();report=self.refresh()
        self.assertEqual(report['status'],'completed',report);self.assertEqual(report['run_state'],'completed')
        self.assertEqual(report['summary']['reused_variants'],1);self.assertEqual(len(self.calls),1)
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_redirected_or_hardlinked_controls_are_rejected_before_writes(self):
        external=Path(self.temp.name)/'outside.txt';external.write_text('keep')
        tmp=self.out/'pose_refresh_report.json.tmp';tmp.symlink_to(external)
        with self.assertRaises(ValueError):self.refresh()
        self.assertEqual(external.read_text(),'keep');tmp.unlink()
        linked=Path(self.temp.name)/'linked.sqlite3';os.link(self.checkpoint,linked);before=self.checkpoint.read_bytes()
        with self.assertRaisesRegex(ValueError,'private regular file'):self.refresh()
        self.assertEqual(self.checkpoint.read_bytes(),before);self.assertEqual(linked.read_bytes(),before)


if __name__=='__main__':unittest.main()
