"""Verified named copies preserve original scene files and checkpoint proof."""
import csv
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from gardens.batch import Ledger
from gardens.download import sha256
from gardens.named_export import export_named,safe_component


class NamedExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name)/'output';self.dbpath=self.out/'batch/tests.sqlite3'
        ledger=Ledger(self.dbpath);ledger.close()
        self.mappings={}
        self.add_job('SceneA','a'*16)

    def add_job(self,sid,vid,status='passed',order=1,title='River Front',chapter_title='Chapter 1: Beginnings',active=1):
        dest=self.out/'scenes'/sid/('variant_'+vid);dest.mkdir(parents=True,exist_ok=True)
        files=[];artifacts={str(self.out/'bundles/missing.bundle'):'0'*64}
        for name,color in [('scene_empty.png','red'),('scene_complete.png','blue'),('scene_solution.png','yellow')]:
            path=dest/name;Image.new('RGBA',(3,2),color).save(path)
            files.append(str(path));artifacts[str(path.resolve())]=sha256(path)
        result={'status':status,'files':files,'artifacts':artifacts,'issues':[]}
        definition={'destination':str(dest),'scene':{'scene_id':sid}}
        with sqlite3.connect(self.dbpath) as db:
            db.execute('INSERT OR REPLACE INTO scenes VALUES (?,?,?,?,?)',(sid,order,'matched','[]',active))
            db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,?)',
                       (sid,vid,order,'proof-'+sid+'-'+vid,status,json.dumps(definition),json.dumps(result),active))
        self.mappings[sid]={'title':title,'chapter_title':chapter_title,'chapter_number':1,'order':order,
                            'mission_order':order,'scene_title':'Original '+title}
        return dest

    def snapshot(self):
        paths=[self.dbpath,*[p for p in (self.out/'scenes').rglob('*') if p.is_file()]]
        return {str(p):(p.read_bytes(),p.stat().st_mtime_ns) for p in paths}

    def assert_snapshot(self,snapshot):
        for name,before in snapshot.items():
            path=Path(name);self.assertEqual((path.read_bytes(),path.stat().st_mtime_ns),before)

    def export(self,**kwargs):return export_named(self.out,self.mappings,provenance={'source':'official name fixture'},**kwargs)

    def test_verified_copy_chapter_grouping_roles_and_originals_unchanged(self):
        before=self.snapshot();report=self.export()
        self.assertEqual(report['status'],'completed');self.assertEqual(report['summary']['written_pngs'],3)
        self.assertEqual(report['summary']['exported_variants'],1)
        entry=report['exports'][0]
        self.assertEqual(entry['official_title'],'River Front');self.assertEqual(entry['official_chapter_title'],'Chapter 1: Beginnings')
        self.assertEqual(entry['source_proof_fingerprint'],'proof-SceneA-'+'a'*16)
        self.assertEqual(entry['grouping']['chapter'],'Chapter 001 - Beginnings')
        self.assertEqual(entry['grouping']['scene'],'000001 - River Front')
        self.assertEqual(entry['grouping']['variant'],'variant_'+'a'*16)
        self.assertEqual({f['role'] for f in entry['files']},{'empty','complete','solution'})
        for file in entry['files']:
            self.assertEqual(sha256(file['source']),sha256(file['destination']))
            self.assertFalse(os.path.samefile(file['source'],file['destination']))
            self.assertTrue(Path(file['destination']).name.startswith('River Front - '))
        self.assert_snapshot(before)
        manifest=json.loads(Path(report['manifest_path']).read_text())
        self.assertEqual(manifest['provenance'],{'source':'official name fixture'})
        with Path(report['index_path']).open(encoding='utf-8-sig',newline='') as stream:
            self.assertEqual(len(list(csv.DictReader(stream))),3)
        # The source bundle and validation JSON need not remain for verified copies.
        self.assertFalse((self.out/'bundles/missing.bundle').exists())

    def test_idempotent_rerun_preserves_copy_mtimes(self):
        first=self.export();files={f['destination']:(Path(f['destination']).read_bytes(),Path(f['destination']).stat().st_mtime_ns) for f in first['files']}
        second=self.export()
        self.assertEqual(second['status'],'completed');self.assertEqual(second['summary']['written_pngs'],0)
        self.assertEqual(second['summary']['reused_variants'],1)
        self.assert_snapshot(files)

    def test_editing_copy_does_not_change_source_and_is_not_overwritten(self):
        report=self.export();record=report['files'][0];source=Path(record['source']);copy_path=Path(record['destination'])
        before=(source.read_bytes(),source.stat().st_mtime_ns);copy_path.write_bytes(b'user edited named copy')
        again=self.export()
        self.assertEqual(again['status'],'partial');self.assertEqual(again['summary']['skipped_variants'],1)
        self.assertEqual(copy_path.read_bytes(),b'user edited named copy')
        self.assertEqual((source.read_bytes(),source.stat().st_mtime_ns),before)
        self.assertTrue(any('conflict' in issue for issue in again['exports'][0]['issues']))

    def test_untracked_nonmatching_file_is_a_conflict_not_an_overwrite(self):
        report=self.export();path=Path(report['files'][0]['destination'])
        Path(report['manifest_path']).unlink();path.write_bytes(b'not owned by export')
        before=self.snapshot();again=self.export()
        self.assertEqual(again['status'],'partial');self.assertEqual(path.read_bytes(),b'not owned by export')
        self.assert_snapshot(before)

    def test_owned_unmodified_copy_can_follow_changed_validated_source(self):
        first=self.export();record=first['files'][0];source=Path(record['source']);target=Path(record['destination'])
        Image.new('RGBA',(3,2),'green').save(source)
        with sqlite3.connect(self.dbpath) as db:
            saved=json.loads(db.execute("SELECT result FROM jobs WHERE scene_id='SceneA'").fetchone()[0])
            saved['artifacts'][str(source)]=sha256(source)
            db.execute("UPDATE jobs SET result=?,fingerprint='new-proof' WHERE scene_id='SceneA'",(json.dumps(saved),))
        report=self.export()
        self.assertEqual(report['status'],'completed');self.assertEqual(report['summary']['written_pngs'],1)
        self.assertEqual(sha256(source),sha256(target))

    def test_variants_and_duplicate_titles_use_separate_stable_paths(self):
        self.add_job('SceneA','b'*16)
        self.add_job('SceneB','c'*16,order=1,title='River Front')
        self.add_job('SceneC','d'*16,order=1,title='river front')
        report=self.export()
        self.assertEqual(report['summary']['exported_variants'],4)
        directories={str(Path(e['files'][0]['destination']).parent).casefold() for e in report['exports']}
        self.assertEqual(len(directories),4)
        a=[e for e in report['exports'] if e['scene_id']=='SceneA']
        self.assertEqual(a[0]['grouping']['scene'],a[1]['grouping']['scene'])
        self.assertNotEqual(a[0]['grouping']['variant'],a[1]['grouping']['variant'])
        paths={e['scene_id']:e['grouping']['scene'] for e in report['exports']}
        self.assertNotEqual(paths['SceneA'].casefold(),paths['SceneB'].casefold())
        self.assertNotEqual(paths['SceneA'].casefold(),paths['SceneC'].casefold())
        self.assertEqual(self.export()['summary']['written_pngs'],0)

    def test_missing_mapping_and_hash_failure_skip_without_creating_fake_names(self):
        self.add_job('SceneB','b'*16,order=2)
        self.mappings.pop('SceneB')
        source=self.out/'scenes/SceneA'/('variant_'+'a'*16)/'scene_complete.png';source.write_bytes(b'changed source')
        report=self.export()
        self.assertEqual(report['status'],'partial');self.assertEqual(report['summary']['skipped_variants'],2)
        self.assertEqual(report['summary']['written_pngs'],0)
        self.assertEqual(list((self.out/'named_scenes').rglob('*.png')),[])
        reasons=[issue for e in report['exports'] for issue in e['issues']]
        self.assertTrue(any('mapping' in issue for issue in reasons));self.assertTrue(any('hash' in issue for issue in reasons))

    def test_incomplete_failed_and_inactive_jobs_are_excluded(self):
        self.add_job('NotComplete','b'*16,status='incomplete',order=2)
        self.add_job('Failed','c'*16,status='failed',order=3)
        self.add_job('Inactive','d'*16,active=0,order=4)
        report=self.export()
        self.assertEqual(report['summary']['passed_variants'],1)
        self.assertEqual([e['scene_id'] for e in report['exports']],['SceneA'])

    def test_invalid_png_with_matching_hash_is_still_rejected(self):
        source=self.out/'scenes/SceneA'/('variant_'+'a'*16)/'scene_empty.png';source.write_bytes(b'not a PNG')
        with sqlite3.connect(self.dbpath) as db:
            result=json.loads(db.execute('SELECT result FROM jobs').fetchone()[0]);result['artifacts'][str(source)]=sha256(source)
            db.execute('UPDATE jobs SET result=?',(json.dumps(result),))
        report=self.export();self.assertEqual(report['status'],'partial');self.assertEqual(report['summary']['written_pngs'],0)

    def test_windows_unsafe_names_and_original_unsanitized_manifest(self):
        title='CON: river/<front> | "?* . '
        self.mappings['SceneA']['title']=title
        report=self.export();self.assertEqual(report['status'],'completed')
        self.assertEqual(report['exports'][0]['official_title'],title)
        for file in report['files']:
            for part in Path(file['relative_path']).parts:
                self.assertFalse(any(c in part for c in '<>:"/\\|?*'))
                self.assertEqual(part,part.rstrip('. '))
        self.assertEqual(safe_component('CON'),'_CON');self.assertEqual(safe_component('lpt1.txt'),'_lpt1.txt')
        self.assertEqual(safe_component('COM¹'),'_COM¹')

    def test_deep_root_and_long_emoji_titles_fit_windows_utf16_budget(self):
        self.out=Path(self.temp.name)/('long-directory-'*5)/'output'
        self.dbpath=self.out/'batch/tests.sqlite3';ledger=Ledger(self.dbpath);ledger.close();self.mappings={}
        title='Ancient 🏛️ Gardens 🌳 '*15
        self.add_job('Deep','a'*16,title=title,chapter_title='Chapter 1: '+'A Very Long Official Chapter '*8)
        report=self.export();self.assertEqual(report['status'],'completed',report['issues'])
        self.assertEqual(report['exports'][0]['official_title'],title)
        for file in report['files']:
            self.assertLess(len(file['destination'].encode('utf-16-le'))//2,250)
            self.assertLessEqual(len(Path(file['destination']).name.encode('utf-16-le'))//2,105)

    def test_protected_destinations_are_rejected_without_touching_originals(self):
        before=self.snapshot()
        for destination in (self.out,self.out/'scenes',self.out/'scenes/export',self.out/'batch/export',self.out/'bundles/export',self.out/'naming/export',self.out/'chapter_metadata/export',self.out/'remote_config/export',self.out.parent):
            with self.subTest(destination=destination),self.assertRaisesRegex(ValueError,'protected'):
                self.export(destination=destination)
        self.assert_snapshot(before)

    def test_export_root_or_named_file_symlinks_do_not_escape(self):
        outside=Path(self.temp.name)/'elsewhere';outside.mkdir()
        link=self.out/'named_scenes'
        try:link.symlink_to(outside,target_is_directory=True)
        except (OSError,NotImplementedError):self.skipTest('Symlinks unavailable')
        with self.assertRaisesRegex(ValueError,'Symlink'):self.export()
        self.assertEqual(list(outside.iterdir()),[]);link.unlink()
        report=self.export();target=Path(report['files'][0]['destination']);target.unlink()
        escaped=outside/'edited.png';escaped.write_bytes(b'keep me');target.symlink_to(escaped)
        report=self.export();self.assertEqual(report['status'],'partial');self.assertEqual(escaped.read_bytes(),b'keep me')

    def test_source_outside_scene_destination_is_not_exported(self):
        outside=self.out/'outside.png';Image.new('RGBA',(3,2),'red').save(outside)
        with sqlite3.connect(self.dbpath) as db:
            result=json.loads(db.execute('SELECT result FROM jobs').fetchone()[0]);result['files'][0]=str(outside)
            result['artifacts'][str(outside)]=sha256(outside)
            db.execute('UPDATE jobs SET result=?',(json.dumps(result),))
        report=self.export();self.assertEqual(report['status'],'partial');self.assertEqual(report['summary']['written_pngs'],0)

    def test_nested_incomplete_png_cannot_masquerade_as_a_passed_source(self):
        import shutil
        source=self.out/'scenes/SceneA'/('variant_'+'a'*16)/'scene_empty.png'
        nested=source.parent/'incomplete'/source.name;nested.parent.mkdir();shutil.copyfile(source,nested)
        with sqlite3.connect(self.dbpath) as db:
            result=json.loads(db.execute('SELECT result FROM jobs').fetchone()[0]);result['files'][0]=str(nested)
            result['artifacts'][str(nested)]=sha256(nested)
            db.execute('UPDATE jobs SET result=?',(json.dumps(result),))
        report=self.export();self.assertEqual(report['status'],'partial');self.assertEqual(report['summary']['written_pngs'],0)

    def test_job_status_alone_does_not_replace_a_passed_result_proof(self):
        with sqlite3.connect(self.dbpath) as db:
            result=json.loads(db.execute('SELECT result FROM jobs').fetchone()[0]);result['status']='incomplete'
            db.execute('UPDATE jobs SET result=?',(json.dumps(result),))
        report=self.export();self.assertEqual(report['status'],'partial');self.assertEqual(report['summary']['written_pngs'],0)

    def test_new_official_names_keep_old_exports_and_lookup_entries(self):
        first=self.export();old=[Path(f['destination']) for f in first['files']]
        self.mappings['SceneA']['title']='Official Renamed River'
        second=self.export()
        self.assertEqual(second['status'],'completed');self.assertEqual(len(second['files']),6)
        self.assertTrue(all(path.exists() for path in old))


if __name__=='__main__':unittest.main()
