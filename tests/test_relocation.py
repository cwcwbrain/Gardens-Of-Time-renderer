"""Copied checkpoint relocation preserves real artifact bytes and old proof."""
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path,PureWindowsPath
from unittest.mock import patch

from PIL import Image

from gardens.batch import Ledger
from gardens.download import sha256
from gardens.relocation import relocate_output

ROOT=Path(__file__).resolve().parents[1]


class RelocationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        base=Path(self.temp.name)
        self.old=base/'laptop/project/output';self.out=base/'desktop/project/output'
        self.checkpoint=self.out/'batch/tests.sqlite3'
        ledger=Ledger(self.old/'batch/tests.sqlite3')
        shared=[]
        for name,source in [('bundles/giza.bundle',next((ROOT/'samples').glob('ancient*.bundle'))),
                            ('item_managers/giza.json',ROOT/'tests/fixtures/AncientGizaRiverfront_items.json')]:
            destination=self.old/name;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,destination);shared.append(destination)
        try:
            states=[('PassedScene','passed',1),('PassedSibling','passed',1),('Pending','pending',1),
                    ('Running','running',1),('Incomplete','incomplete',1),('Failed','failed',1),
                    ('Blocked','blocked',1),('InactivePassed','passed',0),('InactivePending','pending',0)]
            for order,(sid,status,active) in enumerate(states,1):
                folder=self.old/'scenes'/sid;folder.mkdir(parents=True)
                files=[];artifacts={}
                if status=='passed':
                    for index,name in enumerate(['scene_empty.png','scene_complete.png','scene_solution.png']):
                        path=folder/name;Image.new('RGBA',(3,2),(index*60,20,90,255)).save(path);files.append(str(path))
                    for name in ('validation_report.json','objects.json','solution_objects.json'):
                        (folder/name).write_text(json.dumps({'historical_output':str(self.old),'files':files,
                                                           'url':'https://assets.test/'+sid+'.json'}))
                    artifacts={str(path):sha256(path) for path in [*map(Path,files),*folder.glob('*.json'),*shared]}
                fingerprint='original-renderer-proof-'+sid
                definition={'scene':{'scene_id':sid,'order':order,'item_manager_url':'https://assets.test/'+sid+'.json',
                                     'original_row':[str(self.old),'https://assets.test/original']},
                            'candidate':{'url':'https://assets.test/asset?historical_root='+str(self.old)},
                            'destination':str(folder),'output':str(self.old),'frame':None,'offline':False,
                            'timeout':30,'retries':2,'fingerprint':fingerprint}
                log=self.old/'batch'/(sid+'.log');log.write_bytes(b'historical worker log')
                result={'status':status,'issues':['Source reference '+str(self.old)],'files':files,
                        'artifacts':artifacts,'log':str(log),'fingerprint':fingerprint,
                        'text_metadata':{'output':str(self.old)}}
                ledger.db.execute('INSERT INTO scenes VALUES (?,?,?,?,?)',(sid,order,'matched','[]',active))
                ledger.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)',
                                  (sid,'version-'+sid,order,fingerprint,status,json.dumps(definition),json.dumps(result),active))
            ledger.db.commit()
        finally:ledger.close()
        self.out.parent.mkdir(parents=True)
        shutil.copytree(self.old,self.out)

    def rows(self,table='jobs',checkpoint=None):
        with sqlite3.connect(checkpoint or self.checkpoint) as db:
            db.row_factory=sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY 1,2')]

    def files(self,root=None):
        root=root or self.out
        return {path.relative_to(root).as_posix():(path.read_bytes(),path.stat().st_mtime_ns)
                for path in root.rglob('*') if path.is_file() and path.parent.name!='batch'}

    def update(self,sid,field,transform):
        with sqlite3.connect(self.checkpoint) as db:
            raw=db.execute('SELECT '+field+' FROM jobs WHERE scene_id=?',(sid,)).fetchone()[0]
            value=json.loads(raw);transform(value)
            db.execute('UPDATE jobs SET '+field+'=? WHERE scene_id=?',(json.dumps(value),sid))

    def foreign_root(self,root):
        def remap(raw):return str(root.joinpath(*Path(raw).relative_to(self.old).parts))
        with sqlite3.connect(self.checkpoint) as db:
            for row in db.execute('SELECT scene_id,definition,result FROM jobs').fetchall():
                sid,raw_definition,raw_result=row
                definition=json.loads(raw_definition);result=json.loads(raw_result)
                definition['output']=str(root);definition['destination']=remap(definition['destination'])
                result['files']=[remap(path) for path in result['files']]
                result['artifacts']={remap(path):digest for path,digest in result['artifacts'].items()}
                result['log']=remap(result['log'])
                db.execute('UPDATE jobs SET definition=?,result=? WHERE scene_id=?',
                           (json.dumps(definition),json.dumps(result),sid))

    def assert_no_relocation(self,pattern):
        before=self.rows();scenes=self.rows('scenes');files=self.files()
        with self.assertRaisesRegex(ValueError,pattern):relocate_output(self.out)
        self.assertEqual(self.rows(),before);self.assertEqual(self.rows('scenes'),scenes)
        self.assertEqual(self.files(),files)
        self.assertFalse(list((self.out/'batch').glob('tests.pre-relocation-*.sqlite3')))
        self.assertFalse((self.out/'batch/relocation_report.json').exists())

    def test_posix_copy_rebases_only_operational_paths_and_preserves_every_artifact(self):
        before=self.rows();scenes=self.rows('scenes');files=self.files();source_files=self.files(self.old)
        old_checkpoint=self.old/'batch/tests.sqlite3'
        old_db=(old_checkpoint.read_bytes(),old_checkpoint.stat().st_mtime_ns)
        expected={path for row in before if row['status']=='passed' for path in json.loads(row['result'])['artifacts']}
        report=relocate_output(self.out)
        self.assertEqual(report['status'],'relocated');self.assertEqual(report['changed_jobs'],len(before))
        self.assertEqual(report['source_roots'],[str(self.old)])
        self.assertEqual(report['new_root'],str(self.out));self.assertEqual(report['verified_files'],len(expected))
        self.assertEqual(report['verified_bytes'],sum(Path(path).stat().st_size for path in expected))
        self.assertTrue(report['historical_fingerprints_preserved']);self.assertFalse(report['passed_results_recertified'])
        self.assertEqual(self.files(),files);self.assertEqual(self.files(self.old),source_files)
        self.assertEqual((old_checkpoint.read_bytes(),old_checkpoint.stat().st_mtime_ns),old_db)
        self.assertEqual(self.rows('scenes'),scenes)
        for old,new in zip(before,self.rows()):
            for key in old:
                if key not in ('definition','result'):self.assertEqual(old[key],new[key])
            old_def=json.loads(old['definition']);new_def=json.loads(new['definition'])
            old_result=json.loads(old['result']);new_result=json.loads(new['result'])
            self.assertEqual(new_def['output'],str(self.out))
            self.assertEqual(new_def['destination'],str(self.out/'scenes'/new['scene_id']))
            for key in old_def:
                if key not in ('output','destination'):self.assertEqual(old_def[key],new_def[key])
            self.assertEqual(old_result['issues'],new_result['issues'])
            self.assertEqual(old_result['text_metadata'],new_result['text_metadata'])
            self.assertEqual(old_result['fingerprint'],new_result['fingerprint'])
            self.assertEqual(sorted(old_result['artifacts'].values()),sorted(new_result['artifacts'].values()))
            self.assertTrue(all(Path(path).is_relative_to(self.out) for path in new_result['files']))
            self.assertTrue(all(Path(path).is_relative_to(self.out) for path in new_result['artifacts']))
            self.assertEqual(new_result['log'],str(self.out/'batch'/(new['scene_id']+'.log')))

    def test_backup_is_complete_and_keeps_original_checkpoint_rows(self):
        before=self.rows();scenes=self.rows('scenes')
        report=relocate_output(self.out);backup=Path(report['backup_path'])
        self.assertEqual(backup.parent,self.out/'batch');self.assertTrue(backup.is_file())
        self.assertEqual(self.rows(checkpoint=backup),before)
        self.assertEqual(self.rows('scenes',backup),scenes)
        self.assertFalse(Path(str(backup)+'-wal').exists())
        self.assertFalse(Path(str(backup)+'-shm').exists())

    def test_backup_includes_committed_wal_pages(self):
        writer=sqlite3.connect(self.checkpoint,isolation_level=None)
        self.addCleanup(writer.close)
        writer.execute('PRAGMA journal_mode=WAL');writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("UPDATE jobs SET ordinal=ordinal+100 WHERE scene_id='Pending'")
        self.assertTrue(Path(str(self.checkpoint)+'-wal').is_file())
        before=self.rows();scenes=self.rows('scenes')
        report=relocate_output(self.out)
        self.assertEqual(self.rows(checkpoint=Path(report['backup_path'])),before)
        self.assertEqual(self.rows('scenes',Path(report['backup_path'])),scenes)
        self.assertEqual(next(row for row in self.rows() if row['scene_id']=='Pending')['ordinal'],103)

    def test_concurrent_checkpoint_change_aborts_before_rebasing_any_rows(self):
        real_connect=sqlite3.connect;before=self.rows();changed={}
        def connect(database,*args,**kwargs):
            connection=real_connect(database,*args,**kwargs)
            if str(database).startswith(self.checkpoint.as_uri()):
                class Connection:
                    @property
                    def row_factory(self):return connection.row_factory
                    @row_factory.setter
                    def row_factory(self,value):connection.row_factory=value
                    def execute(self,*args,**kwargs):return connection.execute(*args,**kwargs)
                    def executemany(self,*args,**kwargs):return connection.executemany(*args,**kwargs)
                    def close(self):connection.close()
                    def backup(self,target):
                        with real_connect(self_checkpoint) as other:
                            raw=other.execute("SELECT result FROM jobs WHERE scene_id='Pending'").fetchone()[0]
                            result=json.loads(raw);result['issues'].append('another writer committed')
                            changed['result']=json.dumps(result)
                            other.execute("UPDATE jobs SET result=? WHERE scene_id='Pending'",(changed['result'],))
                        connection.backup(target)
                self_checkpoint=self.checkpoint
                return Connection()
            return connection
        with patch('gardens.relocation.sqlite3.connect',side_effect=connect):
            with self.assertRaisesRegex(ValueError,'Checkpoint changed during relocation'):relocate_output(self.out)
        for old,new in zip(before,self.rows()):
            expected=dict(old)
            if old['scene_id']=='Pending':expected['result']=changed['result']
            self.assertEqual(new,expected)
        self.assertFalse((self.out/'batch/relocation_report.json').exists())

    def test_windows_paths_map_without_accessing_the_recorded_laptop_root(self):
        root=PureWindowsPath(r'C:\Users\Laptop\Downloads\Gardens\output');self.foreign_root(root)
        self.update('PassedScene','definition',lambda value:value.update(output=str(root).upper(),destination=str(root/'SCENES/PASSEDSCENE')))
        self.update('PassedScene','result',lambda value:value.update(files=[path.upper() for path in value['files']],
                                                                  artifacts={path.upper():digest for path,digest in value['artifacts'].items()}))
        files=self.files();shutil.rmtree(self.old)
        report=relocate_output(self.out)
        self.assertEqual(report['status'],'relocated');self.assertEqual(self.files(),files)
        for row in self.rows():self.assertEqual(json.loads(row['definition'])['output'],str(self.out))

    def test_unc_paths_map_into_the_copied_output(self):
        self.foreign_root(PureWindowsPath(r'\\laptop\shared\Gardens\output'))
        files=self.files();report=relocate_output(self.out)
        self.assertEqual(report['status'],'relocated');self.assertEqual(self.files(),files)

    def test_source_folder_does_not_need_to_exist(self):
        shutil.rmtree(self.old)
        self.assertEqual(relocate_output(self.out)['status'],'relocated')

    def test_hash_reads_are_deduplicated_across_passed_rows(self):
        real_sha=sha256;calls=[]
        def digest(path):
            calls.append(Path(path));self.assertTrue(Path(path).is_relative_to(self.out))
            return real_sha(path)
        with patch('gardens.relocation.sha256',side_effect=digest):report=relocate_output(self.out)
        self.assertEqual(len(calls),len(set(calls)))
        self.assertEqual(len(calls),report['verified_files'])

    def test_dry_run_and_second_invocation_write_no_checkpoint_or_artifact_data(self):
        before=self.rows();scenes=self.rows('scenes');files=self.files()
        all_files={path.relative_to(self.out).as_posix():path.read_bytes() for path in self.out.rglob('*') if path.is_file()}
        report=relocate_output(self.out,dry_run=True)
        self.assertEqual(report['status'],'planned');self.assertIsNone(report['backup_path'])
        self.assertEqual(self.rows(),before);self.assertEqual(self.rows('scenes'),scenes);self.assertEqual(self.files(),files)
        self.assertEqual({path.relative_to(self.out).as_posix():path.read_bytes() for path in self.out.rglob('*') if path.is_file()},all_files)
        report=relocate_output(self.out);after=self.rows();backup=report['backup_path']
        second=relocate_output(self.out)
        self.assertEqual(second['status'],'unchanged');self.assertEqual(second['changed_jobs'],0)
        self.assertIsNone(second['backup_path']);self.assertEqual(self.rows(),after);self.assertEqual(self.files(),files)
        self.assertEqual([str(path) for path in (self.out/'batch').glob('tests.pre-relocation-*.sqlite3')],[backup])

    def test_outside_root_and_prefix_boundary_are_rejected(self):
        self.update('Pending','result',lambda value:value.update(log=str(self.old)+'-other/batch/worker.log'))
        self.assert_no_relocation('outside')

    def test_parent_escape_is_rejected(self):
        self.update('Pending','definition',lambda value:value.update(destination=str(self.old/'scenes/../outside')))
        self.assert_no_relocation('parent escape')

    def test_scene_destination_outside_scene_tree_is_rejected(self):
        self.update('Pending','definition',lambda value:value.update(destination=str(self.old/'batch/not-a-scene')))
        self.assert_no_relocation('outside output/scenes')

    def test_failed_sqlite_quick_check_stops_before_artifact_reads_or_backup(self):
        real_connect=sqlite3.connect
        class DamagedIndex(sqlite3.Connection):
            def execute(self,sql,*args,**kwargs):
                if sql=='PRAGMA quick_check':return [('row 1 missing from index active_job_status',)]
                return super().execute(sql,*args,**kwargs)
        def connect(database,*args,**kwargs):return real_connect(database,*args,**kwargs,factory=DamagedIndex)
        with patch('gardens.relocation.sqlite3.connect',side_effect=connect), \
             patch('gardens.relocation.sha256',side_effect=AssertionError('corrupt checkpoint must fail before hashing')):
            self.assert_no_relocation('failed SQLite quick_check')

    def test_mixed_source_roots_are_rejected(self):
        self.update('Pending','definition',lambda value:value.update(output=str(self.old.parent/'another-output')))
        self.assert_no_relocation('mixed output roots')

    def test_missing_copied_artifact_fails_without_mutation(self):
        (self.out/'item_managers/giza.json').unlink()
        self.assert_no_relocation('artifact is missing')

    def test_corrupted_copied_json_or_bundle_fails_without_mutation(self):
        (self.out/'scenes/PassedScene/objects.json').write_bytes(b'changed copied JSON')
        self.assert_no_relocation('SHA-256 mismatch')

    def test_inconsistent_pass_image_list_is_rejected(self):
        self.update('PassedScene','result',lambda value:value.update(files=value['files'][:2]))
        self.assert_no_relocation('complete image')

    def test_symlink_escape_is_rejected_before_verification_or_mutation(self):
        path=self.out/'scenes/PassedScene/scene_complete.png';path.unlink()
        try:path.symlink_to(self.old/'scenes/PassedScene/scene_complete.png')
        except OSError as exc:self.skipTest(str(exc))
        before=self.rows()
        with self.assertRaisesRegex(ValueError,'Symlink or junction'):relocate_output(self.out)
        self.assertEqual(self.rows(),before)

    def test_missing_empty_or_unrelated_checkpoint_does_not_create_a_run(self):
        missing=Path(self.temp.name)/'not-a-copy'
        with self.assertRaisesRegex(ValueError,'checkpoint'):relocate_output(missing)
        self.assertFalse(missing.exists())
        empty=Path(self.temp.name)/'empty-output';(empty/'batch').mkdir(parents=True)
        file=empty/'batch/tests.sqlite3';file.touch()
        with self.assertRaisesRegex(ValueError,'checkpoint'):relocate_output(empty)
        self.assertEqual(file.read_bytes(),b'')
        ledger=Ledger(empty/'batch/tests.sqlite3');ledger.close()
        before=file.read_bytes()
        with self.assertRaisesRegex(ValueError,'no scene jobs'):relocate_output(empty)
        self.assertEqual(file.read_bytes(),before)


if __name__=='__main__':unittest.main()
