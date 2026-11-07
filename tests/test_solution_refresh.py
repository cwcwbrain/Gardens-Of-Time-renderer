"""Layout-only refresh preserves native images and historical scene proof.

The injected renderer tests orchestration with declared synthetic snapshots;
real source bundle/manager bytes are retained as cached-input proof.
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

from PIL import Image

from gardens.batch import Ledger
from gardens.download import cache_name,sha256
from gardens.solution_refresh import refresh_solutions

ROOT=Path(__file__).resolve().parents[1]
LAYOUT='numbered_local_pointers_v1'


class SolutionRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name)/'output';self.checkpoint=self.out/'batch/tests.sqlite3'
        ledger=Ledger(self.checkpoint);ledger.close()
        self.bundle_url='https://assets.test/scene.bundle'
        self.manager_url='https://assets.test/items.json'
        self.bundle=self.out/'bundles'/cache_name(self.bundle_url,'.bundle')
        self.manager=self.out/'item_managers'/cache_name(self.manager_url,'.json')
        for path,source in [(self.bundle,next((ROOT/'samples').glob('ancient*.bundle'))),
                            (self.manager,ROOT/'tests/fixtures/AncientGizaRiverfront_items.json')]:
            path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,path)
            Path(str(path)+'.verified.json').write_text(json.dumps({'url':self.bundle_url if path==self.bundle else self.manager_url,'sha256':sha256(path)}))
        self.manager_data=json.loads(self.manager.read_text());self.calls=[]
        self.add_job('PassedScene',1,'passed',1)
        self.add_job('InactivePassed',2,'passed',0)
        for order,status in enumerate(('incomplete','failed','pending','running','blocked'),3):self.add_job(status.title(),order,status,1)
        self.dest=self.out/'scenes/PassedScene'

    def marker(self):
        return {'object_id':143,'manager_key':'banana_anc_giz_riv','label':'Banana',
                'labels':self.manager_data['banana_anc_giz_riv'],'label_verified':True,
                'bbox':[5,5,15,15],'anchor':[10,10],'anchor_alpha':255,'number':1,
                'pointer_start':[0,0],'pointer_tip':[10,10],'callout_box':[0,0,20,20]}

    def add_job(self,sid,order,status,active):
        dest=self.out/'scenes'/sid;dest.mkdir(parents=True,exist_ok=True)
        files=[];artifacts={}
        for name,color in [('scene_empty.png','red'),('scene_complete.png','blue'),('scene_solution.png','yellow')]:
            path=dest/name;Image.new('RGBA',(20,20),color).save(path)
            files.append(str(path));artifacts[str(path)]=sha256(path)
        objects=[{'object_id':143,'name':'banana','kind':'hidden_object','status':'rendered'}]
        report={'status':'validated_snapshot','issues':[],'files':files,'snapshot_frame':7,
                'scene_dimensions':[20,20],'solution_dimensions':[20,20],'identified_hidden_objects':1,
                'named_pointers':1,'solution_markers':1,'bundle_sha256':sha256(self.bundle),
                'item_manager_sha256':hashlib.sha256(json.dumps(self.manager_data,sort_keys=True).encode()).hexdigest()}
        for name,data in [('validation_report.json',report),('solution_objects.json',[self.marker()]),('objects.json',objects)]:
            path=dest/name;path.write_text(json.dumps(data));artifacts[str(path)]=sha256(path)
        for path in (self.bundle,self.manager):artifacts[str(path)]=sha256(path)
        fp='historical-proof-'+sid
        definition={'scene':{'order':order,'scene_id':sid,'thumbnail_url':'','item_manager_url':self.manager_url,'original_row':[]},
                    'candidate':{'roots':[sid],'issues':[],'bundles':[{'url':self.bundle_url,'internal_id':self.bundle_url,'options':{}}]},
                    'destination':str(dest),'output':str(self.out),'frame':None,'offline':True,'timeout':30,'retries':0,'fingerprint':fp}
        result={'status':status,'issues':[],'files':files if status=='passed' else [],'artifacts':artifacts if status=='passed' else {},
                'fingerprint':fp,'hidden_objects':1,'named_pointers':1}
        with sqlite3.connect(self.checkpoint) as db:
            db.execute('INSERT INTO scenes VALUES (?,?,?,?,?)',(sid,order,'matched','[]',active))
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)',(sid,'variant-'+sid,order,fp,status,json.dumps(definition),json.dumps(result),active))
        return dest

    def rows(self):
        with sqlite3.connect(self.checkpoint) as db:
            db.row_factory=sqlite3.Row
            return {row['scene_id']:dict(row) for row in db.execute('SELECT * FROM jobs')}

    def snapshot(self,paths=None):
        if paths is None:paths=[path for path in (self.out/'scenes').rglob('*') if path.is_file()]
        return {str(path):(path.read_bytes(),path.stat().st_mtime_ns) for path in paths}

    def assert_snapshot(self,values):
        for raw,expected in values.items():
            path=Path(raw);self.assertEqual((path.read_bytes(),path.stat().st_mtime_ns),expected)

    def render(self,bundle,manager,stage,frame=None):
        self.calls.append((Path(bundle),manager,Path(stage),frame))
        self.assertEqual(Path(bundle),self.bundle);self.assertEqual(manager,self.manager_data)
        self.assertNotEqual(Path(stage),self.dest);self.assertEqual(frame,7)
        stage=Path(stage);stage.mkdir(parents=True,exist_ok=True)
        for name in ('scene_empty.png','scene_complete.png'):shutil.copyfile(self.dest/name,stage/name)
        Image.new('RGBA',(40,30),'cyan').save(stage/'scene_solution.png')
        markers=[{**self.marker(),'pointer_start':[9,9],'pointer_tip':[10,10],'callout_box':[1,1,8,8]}]
        report={'status':'validated_snapshot','issues':[],'solution_layout':LAYOUT,'snapshot_frame':7,
                'scene_dimensions':[20,20],'solution_dimensions':[40,30],
                'files':[str(stage/name) for name in ('scene_empty.png','scene_complete.png','scene_solution.png')],
                'identified_hidden_objects':1,'named_pointers':1,'solution_markers':1,'bundle_sha256':sha256(self.bundle),
                'item_manager_sha256':hashlib.sha256(json.dumps(manager,sort_keys=True).encode()).hexdigest()}
        (stage/'solution_objects.json').write_text(json.dumps(markers))
        (stage/'validation_report.json').write_text(json.dumps(report))
        shutil.copyfile(self.dest/'objects.json',stage/'objects.json')
        return report

    def refresh(self,renderer=None,**kwargs):
        with contextlib.redirect_stdout(io.StringIO()), \
             patch('gardens.download.Downloader.fetch',side_effect=AssertionError('solution refresh must not download')):
            return refresh_solutions(self.out,renderer=renderer or self.render,**kwargs)

    def test_only_active_passed_solution_and_its_sidecars_are_amended(self):
        before=self.rows()
        preserved=[path for path in (self.out/'scenes').rglob('*') if path.is_file() and
                   (path.parent!=self.dest or path.name in ('scene_empty.png','scene_complete.png','objects.json'))]
        native=self.snapshot(preserved);old_solution=sha256(self.dest/'scene_solution.png')
        report=self.refresh()
        self.assertEqual(report['status'],'completed',report)
        self.assertEqual(len(self.calls),1);self.assert_snapshot(native)
        after=self.rows()
        for sid,row in before.items():
            if sid!='PassedScene':self.assertEqual(after[sid],row)
        for field in before['PassedScene']:
            if field!='result':self.assertEqual(after['PassedScene'][field],before['PassedScene'][field])
        result=json.loads(after['PassedScene']['result']);original=json.loads(before['PassedScene']['result'])
        self.assertEqual(result['files'],original['files'])
        self.assertEqual(result['fingerprint'],original['fingerprint'])
        published={str(self.dest/name) for name in ('scene_solution.png','solution_objects.json','validation_report.json')}
        for path,digest in original['artifacts'].items():
            if path not in published:self.assertEqual(result['artifacts'][path],digest)
        for path in published:self.assertEqual(result['artifacts'][path],sha256(path))
        self.assertNotEqual(sha256(self.dest/'scene_solution.png'),old_solution)
        validation=json.loads((self.dest/'validation_report.json').read_text())
        self.assertEqual(validation['solution_layout'],LAYOUT)
        self.assertEqual(validation['files'],original['files'])
        self.assertTrue(all(Path(path).is_relative_to(self.out) for path in result['artifacts']))

    def test_plan_only_starts_no_renderer_and_changes_no_rows_or_scene_files(self):
        before=self.rows();files=self.snapshot()
        report=self.refresh(plan_only=True)
        self.assertEqual(self.calls,[]);self.assertEqual(self.rows(),before);self.assert_snapshot(files)
        self.assertIn(report['status'],('planned','completed'))

    def test_current_layout_is_idempotent_without_another_renderer_call(self):
        self.refresh();before=self.rows();files=self.snapshot();self.calls.clear()
        report=self.refresh()
        self.assertEqual(report['status'],'completed');self.assertEqual(self.calls,[])
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_changed_native_stage_image_skips_without_degrading_the_pass(self):
        before=self.rows();files=self.snapshot()
        def changed(*args,**kwargs):
            report=self.render(*args,**kwargs)
            Image.new('RGBA',(20,20),'green').save(Path(args[2])/'scene_complete.png')
            return report
        report=self.refresh(changed)
        self.assertEqual(report['status'],'partial');self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_changed_object_binding_or_label_cannot_be_published_as_layout_only(self):
        before=self.rows();files=self.snapshot()
        def changed(*args,**kwargs):
            report=self.render(*args,**kwargs);path=Path(args[2])/'solution_objects.json'
            markers=json.loads(path.read_text());markers[0]['label']='Invented label';path.write_text(json.dumps(markers))
            return report
        report=self.refresh(changed)
        self.assertEqual(report['status'],'partial');self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_missing_or_edited_cached_input_prevents_rendering(self):
        self.bundle.write_bytes(b'user edited cached bundle');before=self.rows();files=self.snapshot()
        report=self.refresh()
        self.assertEqual(report['status'],'partial');self.assertEqual(self.calls,[])
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_missing_verified_receipt_prevents_rendering(self):
        Path(str(self.manager)+'.verified.json').unlink();before=self.rows();files=self.snapshot()
        report=self.refresh()
        self.assertEqual(report['status'],'partial');self.assertEqual(self.calls,[])
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_edited_existing_native_or_solution_image_is_preserved(self):
        (self.dest/'scene_solution.png').write_bytes(b'user edited existing solution')
        before=self.rows();files=self.snapshot();report=self.refresh()
        self.assertEqual(report['status'],'partial');self.assertEqual(self.calls,[])
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_recorded_json_artifact_hash_mismatch_is_not_silently_accepted(self):
        path=self.dest/'validation_report.json';data=json.loads(path.read_text());data['snapshot_frame']=8;path.write_text(json.dumps(data))
        before=self.rows();files=self.snapshot();report=self.refresh()
        self.assertEqual(report['status'],'partial');self.assertEqual(self.calls,[])
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_legacy_pass_without_recorded_json_hashes_can_refresh_with_matching_native_and_input_proof(self):
        with sqlite3.connect(self.checkpoint) as db:
            raw=db.execute("SELECT result FROM jobs WHERE scene_id='PassedScene'").fetchone()[0];value=json.loads(raw)
            for name in ('validation_report.json','objects.json','solution_objects.json'):value['artifacts'].pop(str(self.dest/name))
            db.execute("UPDATE jobs SET result=? WHERE scene_id='PassedScene'",(json.dumps(value),))
        native=self.snapshot([self.dest/'scene_empty.png',self.dest/'scene_complete.png',self.dest/'objects.json'])
        report=self.refresh()
        self.assertEqual(report['status'],'completed',report);self.assertEqual(len(self.calls),1);self.assert_snapshot(native)

    def test_other_writer_during_staging_prevents_publish(self):
        before=self.rows();files=self.snapshot()
        def concurrent(*args,**kwargs):
            report=self.render(*args,**kwargs)
            with sqlite3.connect(self.checkpoint) as db:db.execute("UPDATE scenes SET issues='[\"another command\"]' WHERE id='InactivePassed'")
            return report
        report=self.refresh(concurrent)
        self.assertEqual(report['status'],'partial');self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_native_edit_during_staging_is_not_overwritten_or_published_over(self):
        before=self.rows();solution=self.snapshot([self.dest/'scene_solution.png',self.dest/'solution_objects.json',self.dest/'validation_report.json'])
        path=self.dest/'scene_complete.png'
        def edited(*args,**kwargs):
            report=self.render(*args,**kwargs);path.write_bytes(b'user edited native image during refresh');return report
        report=self.refresh(edited)
        self.assertEqual(report['status'],'partial');self.assertEqual(self.rows(),before);self.assert_snapshot(solution)
        self.assertEqual(path.read_bytes(),b'user edited native image during refresh')

    def test_cached_input_edit_during_staging_prevents_publish(self):
        before=self.rows();files=self.snapshot()
        def edited(*args,**kwargs):
            report=self.render(*args,**kwargs);self.manager.write_bytes(b'changed cached manager during refresh');return report
        report=self.refresh(edited)
        self.assertEqual(report['status'],'partial');self.assertEqual(self.rows(),before);self.assert_snapshot(files)
        self.assertEqual(self.manager.read_bytes(),b'changed cached manager during refresh')

    def test_renderer_interrupt_preserves_old_pass_and_all_scene_artifacts(self):
        before=self.rows();files=self.snapshot()
        def interrupted(*args,**kwargs):raise KeyboardInterrupt
        report=self.refresh(interrupted)
        self.assertEqual(report['status'],'interrupted');self.assertEqual(self.rows(),before);self.assert_snapshot(files)

    def test_interrupt_during_solution_publish_restores_old_pass_then_same_command_succeeds(self):
        before=self.rows();files=self.snapshot();replace=os.replace;stopped=[]
        def interrupt_after_solution(source,destination):
            result=replace(source,destination)
            if Path(destination)==self.dest/'scene_solution.png' and not stopped:
                stopped.append(True);raise KeyboardInterrupt
            return result
        with patch('gardens.solution_refresh.os.replace',side_effect=interrupt_after_solution):report=self.refresh()
        self.assertEqual(stopped,[True]);self.assertEqual(report['status'],'interrupted')
        self.assertEqual(self.rows(),before);self.assert_snapshot(files)
        report=self.refresh()
        self.assertEqual(report['status'],'completed',report)
        result=json.loads(self.rows()['PassedScene']['result'])
        for path,digest in result['artifacts'].items():self.assertEqual(sha256(path),digest)

    def test_abrupt_process_exit_is_recovered_before_next_refresh(self):
        native=self.snapshot([self.dest/'scene_empty.png',self.dest/'scene_complete.png',self.dest/'objects.json'])
        script=r'''
import importlib.util,json,os,sys
from pathlib import Path
from gardens.download import cache_name
spec=importlib.util.spec_from_file_location('refresh_fixtures','tests/test_solution_refresh.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
fixture=module.SolutionRefreshTests()
fixture.out=Path(sys.argv[1]);fixture.checkpoint=fixture.out/'batch/tests.sqlite3'
fixture.bundle_url='https://assets.test/scene.bundle';fixture.manager_url='https://assets.test/items.json'
fixture.bundle=fixture.out/'bundles'/cache_name(fixture.bundle_url,'.bundle')
fixture.manager=fixture.out/'item_managers'/cache_name(fixture.manager_url,'.json')
fixture.manager_data=json.loads(fixture.manager.read_text());fixture.calls=[];fixture.dest=fixture.out/'scenes/PassedScene'
replace=os.replace
def terminate(source,destination):
 result=replace(source,destination)
 if Path(destination)==fixture.dest/'scene_solution.png':os._exit(23)
 return result
os.replace=terminate
module.refresh_solutions(fixture.out,renderer=fixture.render)
'''
        process=subprocess.run([sys.executable,'-c',script,str(self.out)],cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(process.returncode,23,process.stdout+process.stderr)
        self.assert_snapshot(native)
        report=self.refresh()
        self.assertEqual(report['status'],'completed',report);self.assert_snapshot(native)
        result=json.loads(self.rows()['PassedScene']['result'])
        self.assertEqual(result['status'],'passed')
        for path,digest in result['artifacts'].items():self.assertEqual(sha256(path),digest)


if __name__=='__main__':unittest.main()
