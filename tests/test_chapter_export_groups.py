"""Hundred/ten/chapter grouping preserves verified images and raw run proof."""
import csv
import json
import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from gardens.batch import Ledger
from gardens.download import sha256
import test_named_export as export_fixtures


class ChapterExportGroupTests(unittest.TestCase):
    def setUp(self):
        self.fixture=export_fixtures.NamedExportTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def chapter_job(self,number,order,*,sid=None,vid=None,title=None,chapter_title=None):
        sid=sid or ('Chapter'+str(number)+'Scene')
        vid=vid or f'{number:016x}'
        self.fixture.add_job(sid,vid,order=order,title=title or f'Scene in Chapter {number}',
                             chapter_title=chapter_title or f'Chapter {number}: Title {number}')
        self.fixture.mappings[sid]['chapter_number']=number
        self.fixture.mappings[sid]['mission_order']=1
        return sid,vid

    def prepare_boundaries(self):
        numbers=[1,10,11,100,101,463,748,1000,1001]
        expected={}
        for order,number in enumerate(numbers,1):
            sid,vid=self.chapter_job(number,order,sid='SceneA' if number==1 else None,
                                     vid='a'*16 if number==1 else None)
            expected[sid]=(number,order,vid)
        return expected

    def assert_triplets(self,report):
        for entry in report['exports']:
            if entry['status']=='skipped':continue
            self.assertEqual(len(entry['files']),3)
            self.assertEqual({record['role'] for record in entry['files']},{'empty','complete','solution'})
            for record in entry['files']:
                self.assertEqual(sha256(record['source']),sha256(record['destination']))
                self.assertEqual(record['source_sha256'],record['destination_sha256'])
                self.assertFalse(os.path.samefile(record['source'],record['destination']))

    def test_boundary_chapters_use_correct_hundred_ten_and_individual_chapter_folders(self):
        expected=self.prepare_boundaries();before=self.fixture.snapshot()
        report=self.fixture.export()
        self.assertEqual(report['status'],'completed',report['issues'])
        self.assertEqual(report['summary']['exported_variants'],9)
        self.assertEqual(report['summary']['written_pngs'],27)
        self.assertEqual([entry['order'] for entry in report['exports']],list(range(1,10)))
        explicit={1:('Chapters 001-100','Chapters 001-010'),10:('Chapters 001-100','Chapters 001-010'),
                  11:('Chapters 001-100','Chapters 011-020'),100:('Chapters 001-100','Chapters 091-100'),
                  101:('Chapters 101-200','Chapters 101-110'),463:('Chapters 401-500','Chapters 461-470'),
                  748:('Chapters 701-800','Chapters 741-750'),1000:('Chapters 901-1000','Chapters 991-1000'),
                  1001:('Chapters 1001-1100','Chapters 1001-1010')}
        for entry in report['exports']:
            number,order,vid=expected[entry['scene_id']]
            hundred,ten=explicit[number]
            for record in entry['files']:
                parts=Path(record['relative_path']).parts
                self.assertEqual(parts[:2],(hundred,ten))
                self.assertEqual(parts[2],f'Chapter {number:03d} - Title {number}')
                self.assertTrue(parts[3].startswith(f'{order:06d}'))
                self.assertEqual(parts[4],'variant_'+vid)
                self.assertEqual(len(parts),6)
                self.assertEqual(record['order'],order)
                self.assertEqual(record['source_proof_fingerprint'],entry['source_proof_fingerprint'])
            self.assertEqual(entry['grouping']['hundred'],hundred);self.assertEqual(entry['grouping']['ten'],ten)
        self.assert_triplets(report);self.fixture.assert_snapshot(before)

    def test_multiple_variants_retain_corrected_scene_order_and_all_three_roles(self):
        self.chapter_job(463,7,sid='SceneA',vid='a'*16,title='Official River')
        self.chapter_job(463,7,sid='SceneA',vid='b'*16,title='Official River')
        self.chapter_job(463,8,sid='SceneB',vid='c'*16,title='Official River')
        before=self.fixture.snapshot();report=self.fixture.export()
        self.assertEqual(report['status'],'completed',report['issues'])
        self.assertEqual(report['summary']['exported_variants'],3)
        entries=report['exports'];self.assertEqual([entry['order'] for entry in entries],[7,7,8])
        a=[entry for entry in entries if entry['scene_id']=='SceneA']
        self.assertEqual(a[0]['grouping']['scene'],a[1]['grouping']['scene'])
        self.assertNotEqual(a[0]['grouping']['variant'],a[1]['grouping']['variant'])
        directories={str(Path(entry['files'][0]['destination']).parent).casefold() for entry in entries}
        self.assertEqual(len(directories),3)
        self.assert_triplets(report);self.fixture.assert_snapshot(before)

    def test_missing_chapter_or_mapping_is_unexported_without_invented_groups(self):
        self.chapter_job(10,2,sid='MissingChapter',vid='b'*16)
        self.fixture.mappings['MissingChapter']['chapter_number']=None
        self.chapter_job(11,3,sid='MissingMapping',vid='c'*16)
        self.fixture.mappings.pop('MissingMapping')
        before=self.fixture.snapshot();report=self.fixture.export()
        self.assertEqual(report['status'],'partial')
        self.assertEqual(report['summary']['exported_variants'],1)
        self.assertEqual(report['summary']['skipped_variants'],2)
        failed={entry['scene_id']:entry for entry in report['exports'] if entry['status']=='skipped'}
        self.assertEqual(set(failed),{'MissingChapter','MissingMapping'})
        self.assertTrue(all(not entry['files'] for entry in failed.values()))
        root=Path(report['destination'])
        self.assertFalse(any('Unmapped' in path.parts for path in root.rglob('*')))
        self.assertEqual(len(list(root.rglob('*.png'))),3)
        self.fixture.assert_snapshot(before)

    def test_deep_windows_path_budget_still_fits_ranges_variants_and_full_title_metadata(self):
        fixture=self.fixture
        fixture.out=Path(fixture.temp.name)/('long-directory-'*5)/'output'
        fixture.dbpath=fixture.out/'batch/tests.sqlite3';ledger=Ledger(fixture.dbpath);ledger.close();fixture.mappings={}
        title='Ancient 🏛️ Gardens 🌳 '*15
        chapter_title='Chapter 748: '+'A Very Long Official Chapter 🌳 '*8
        self.chapter_job(748,4482,sid='Deep',vid='d'*16,title=title,chapter_title=chapter_title)
        self.chapter_job(748,4482,sid='Deep',vid='e'*16,title=title,chapter_title=chapter_title)
        before=fixture.snapshot();report=fixture.export()
        self.assertEqual(report['status'],'completed',report['issues'])
        self.assertEqual(report['summary']['exported_variants'],2)
        for entry in report['exports']:
            self.assertEqual(entry['official_title'],title)
            self.assertEqual(entry['official_chapter_title'],chapter_title)
            for record in entry['files']:
                self.assertEqual(Path(record['relative_path']).parts[:2],('Chapters 701-800','Chapters 741-750'))
                self.assertLess(len(record['destination'].encode('utf-16-le'))//2,250)
                self.assertLessEqual(len(Path(record['destination']).name.encode('utf-16-le'))//2,105)
        self.assert_triplets(report);fixture.assert_snapshot(before)

    def test_grouped_rerun_reuses_files_without_rewriting_raw_results_or_pngs(self):
        self.prepare_boundaries();raw=self.fixture.snapshot();first=self.fixture.export()
        copies={record['destination']:(Path(record['destination']).read_bytes(),Path(record['destination']).stat().st_mtime_ns)
                for record in first['files']}
        with sqlite3.connect(self.fixture.dbpath) as db:
            rows=list(db.execute('SELECT * FROM jobs ORDER BY ordinal,scene_id,variant_id'))
        second=self.fixture.export()
        self.assertEqual(second['status'],'completed');self.assertEqual(second['summary']['written_pngs'],0)
        self.assertEqual(second['summary']['reused_variants'],9)
        self.fixture.assert_snapshot(copies);self.fixture.assert_snapshot(raw)
        with sqlite3.connect(self.fixture.dbpath) as db:
            self.assertEqual(list(db.execute('SELECT * FROM jobs ORDER BY ordinal,scene_id,variant_id')),rows)

    def legacy_export(self):
        self.chapter_job(463,1,sid='SceneA',vid='a'*16,title='Official River')
        first=self.fixture.export();self.assertEqual(first['status'],'completed',first['issues'])
        root=Path(first['destination']);manifest_path=Path(first['manifest_path'])
        manifest=json.loads(manifest_path.read_text());moved={};records={};legacy_files=[]
        for record in manifest['files']:
            current=Path(record['destination']);parts=Path(record['relative_path']).parts
            self.assertEqual(len(parts),6)
            relative=Path(*parts[2:]);legacy=root/relative;legacy.parent.mkdir(parents=True,exist_ok=True)
            current.replace(legacy);old=record['relative_path'];new=relative.as_posix()
            copied={**record,'destination':str(legacy),'relative_path':new,
                    'grouping':{key:value for key,value in record['grouping'].items() if key not in ('hundred','ten')}}
            moved[old]=new;records[new]=copied;legacy_files.append(legacy)
        manifest['files']=list(records.values())
        manifest['owned_files']={moved[key]:digest for key,digest in manifest['owned_files'].items()}
        for entry in manifest['exports']:
            entry['grouping']={key:value for key,value in entry['grouping'].items() if key not in ('hundred','ten')}
            entry['files']=[records[moved[record['relative_path']]] for record in entry['files']]
        index_path=Path(first['index_path'])
        with index_path.open(encoding='utf-8-sig',newline='') as stream:fields=csv.DictReader(stream).fieldnames
        with index_path.open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(manifest['files'])
        manifest['index_sha256']=sha256(index_path)
        manifest['schema']=1;manifest.pop('layout',None)
        manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
        return root,legacy_files,manifest

    def test_owned_verified_legacy_exports_are_moved_to_groups_without_duplicate_png_copies(self):
        root,legacy,old_manifest=self.legacy_export();raw=self.fixture.snapshot()
        inodes={path.name:path.stat().st_ino for path in legacy}
        old_paths={record['relative_path'] for record in old_manifest['files']}
        with patch('gardens.named_export._copy',side_effect=AssertionError('verified legacy files should move, not copy')):
            report=self.fixture.export()
        self.assertEqual(report['status'],'completed',report['issues'])
        self.assertEqual(len(list(root.rglob('*.png'))),3)
        self.assertTrue(all(not path.exists() for path in legacy))
        for record in report['files']:
            path=Path(record['destination']);self.assertEqual(path.stat().st_ino,inodes[path.name])
            self.assertEqual(Path(record['relative_path']).parts[:2],('Chapters 401-500','Chapters 461-470'))
            self.assertEqual(sha256(path),record['source_sha256'])
        self.assertTrue(old_paths.isdisjoint(report['owned_files']))
        self.assertTrue(old_paths.isdisjoint(record['relative_path'] for record in report['files']))
        self.assertEqual(report['schema'],2)
        self.assertFalse((root/'.named_layout_migration.json').exists())
        self.fixture.assert_snapshot(raw)

    def test_completed_legacy_move_removes_only_the_old_empty_parent_folders(self):
        root,legacy,_=self.legacy_export();raw=self.fixture.snapshot()
        variant=legacy[0].parent;old_folders=[variant,variant.parent,variant.parent.parent]
        self.assertTrue(all(path.is_dir() for path in old_folders))
        report=self.fixture.export()
        self.assertEqual(report['status'],'completed',report['issues'])
        self.assertTrue(all(not path.exists() for path in old_folders))
        self.assertTrue(root.is_dir());self.assertEqual(len(list(root.rglob('*.png'))),3)
        self.fixture.assert_snapshot(raw)

    def test_legacy_parent_folder_with_user_notes_is_retained_without_editing_notes(self):
        root,legacy,_=self.legacy_export();raw=self.fixture.snapshot()
        variant=legacy[0].parent;old_folders=[variant,variant.parent,variant.parent.parent]
        notes=variant/'My Notes.txt';notes.write_bytes(b'Please keep my scene notes exactly as written.')
        preserved={str(notes):(notes.read_bytes(),notes.stat().st_mtime_ns)}
        report=self.fixture.export()
        self.assertEqual(report['status'],'completed',report['issues'])
        self.assertTrue(all(path.is_dir() for path in old_folders))
        self.assertTrue(all(not path.exists() for path in legacy))
        self.assertEqual(len(list(root.rglob('*.png'))),3)
        self.fixture.assert_snapshot(preserved);self.fixture.assert_snapshot(raw)

    def test_edited_legacy_export_is_not_moved_or_overwritten(self):
        root,legacy,_=self.legacy_export();edited=legacy[1];edited.write_bytes(b'user changed this old named copy')
        before=(edited.read_bytes(),edited.stat().st_mtime_ns,edited.stat().st_ino);raw=self.fixture.snapshot()
        named={str(path):(path.read_bytes(),path.stat().st_mtime_ns) for path in legacy}
        report=self.fixture.export()
        self.assertEqual(report['status'],'partial');self.assertEqual(report['summary']['skipped_variants'],1)
        self.assertEqual(report['summary']['moved_pngs'],0);self.assertEqual(report['summary']['written_pngs'],0)
        self.assertEqual((edited.read_bytes(),edited.stat().st_mtime_ns,edited.stat().st_ino),before)
        self.assertTrue(edited.exists())
        self.assertTrue(report['exports'])
        self.fixture.assert_snapshot(named);self.assertEqual(len(list(root.rglob('*.png'))),3)
        self.fixture.assert_snapshot(raw)

    def test_edited_lookup_index_prevents_any_legacy_move_or_copy(self):
        root,legacy,manifest=self.legacy_export();raw=self.fixture.snapshot()
        index=Path(manifest['index_path']);index.write_bytes(b'user edited this CSV index')
        preserved={str(path):(path.read_bytes(),path.stat().st_mtime_ns)
                   for path in [*legacy,index,Path(manifest['manifest_path'])]}
        report=self.fixture.export()
        self.assertEqual(report['status'],'partial')
        self.assertEqual(report['summary']['moved_pngs'],0);self.assertEqual(report['summary']['written_pngs'],0)
        self.assertTrue(any('index' in str(issue).casefold() for issue in report['issues']))
        self.fixture.assert_snapshot(preserved);self.fixture.assert_snapshot(raw)
        self.assertEqual(len(list(root.rglob('*.png'))),3)

    def test_legacy_move_recovers_after_interrupt_between_png_renames(self):
        root,legacy,_=self.legacy_export();raw=self.fixture.snapshot()
        inodes={path.name:path.stat().st_ino for path in legacy};moved=[];replace=os.replace
        def stop_after_first_png(source,destination):
            png=Path(source).suffix.casefold()=='.png' and Path(destination).suffix.casefold()=='.png'
            if png and moved:raise KeyboardInterrupt
            result=replace(source,destination)
            if png:moved.append(str(destination))
            return result
        with patch('gardens.named_export._copy',side_effect=AssertionError('migration must not duplicate PNG data')):
            with patch('gardens.named_export.os.replace',side_effect=stop_after_first_png):
                with self.assertRaises(KeyboardInterrupt):self.fixture.export()
            self.assertEqual(len(moved),1);self.assertEqual(len(list(root.rglob('*.png'))),3)
            self.fixture.assert_snapshot(raw)
            report=self.fixture.export()
        self.assertEqual(report['status'],'completed',report['issues'])
        self.assertEqual(len(list(root.rglob('*.png'))),3)
        self.assertTrue(all(not path.exists() for path in legacy))
        for record in report['files']:
            path=Path(record['destination']);self.assertEqual(path.stat().st_ino,inodes[path.name])
            self.assertEqual(sha256(path),record['source_sha256'])
        self.assertFalse((root/'.named_layout_migration.json').exists())
        self.fixture.assert_snapshot(raw)

    def test_legacy_move_recovers_after_csv_publish_before_manifest_publish(self):
        import gardens.named_export as exporter
        root,legacy,_=self.legacy_export();raw=self.fixture.snapshot()
        inodes={path.name:path.stat().st_ino for path in legacy};published=[];atomic=exporter._atomic_bytes
        def stop_before_manifest(path,data):
            if Path(path).name==exporter.MANIFEST:
                self.assertTrue(published,'The injected interruption must follow CSV publication')
                raise KeyboardInterrupt
            result=atomic(path,data)
            if Path(path).name==exporter.INDEX:published.append(str(path))
            return result
        with patch('gardens.named_export._copy',side_effect=AssertionError('migration must not duplicate PNG data')):
            with patch('gardens.named_export._atomic_bytes',side_effect=stop_before_manifest):
                with self.assertRaises(KeyboardInterrupt):self.fixture.export()
            self.assertEqual(len(published),1);self.assertEqual(len(list(root.rglob('*.png'))),3)
            self.fixture.assert_snapshot(raw)
            report=self.fixture.export()
        self.assertEqual(report['status'],'completed',report['issues'])
        self.assertEqual(len(list(root.rglob('*.png'))),3)
        for record in report['files']:
            path=Path(record['destination']);self.assertEqual(path.stat().st_ino,inodes[path.name])
            self.assertEqual(sha256(path),record['source_sha256'])
        self.assertFalse((root/'.named_layout_migration.json').exists())
        self.fixture.assert_snapshot(raw)

    def test_index_edited_during_moves_is_preserved_and_journal_can_resume(self):
        from gardens.named_migration import LegacyMigration
        root,legacy,manifest=self.legacy_export();raw=self.fixture.snapshot()
        index=Path(manifest['index_path']);original_index=index.read_bytes()
        edited=b'user changed the CSV while images were being reorganized'
        apply=LegacyMigration.apply;changed=[]
        def edit_after_first_move(migration,move):
            result=apply(migration,move)
            if not changed:index.write_bytes(edited);changed.append(True)
            return result
        with patch.object(LegacyMigration,'apply',edit_after_first_move):
            partial=self.fixture.export()
        self.assertEqual(partial['status'],'partial')
        self.assertEqual(index.read_bytes(),edited)
        self.assertTrue((root/'.named_layout_migration.json').exists())
        self.assertTrue(all(not path.exists() for path in legacy))
        self.assertEqual(len(list(root.rglob('*.png'))),3)
        self.fixture.assert_snapshot(raw)
        # Restore the previously owned CSV to resolve the user's edit, then
        # recover using the journal without copying any PNG data again.
        index.write_bytes(original_index)
        with patch('gardens.named_export._copy',side_effect=AssertionError('recovery must reuse moved files')):
            completed=self.fixture.export()
        self.assertEqual(completed['status'],'completed',completed['issues'])
        self.assertFalse((root/'.named_layout_migration.json').exists())
        self.assertEqual(len(list(root.rglob('*.png'))),3)
        self.fixture.assert_snapshot(raw)


if __name__=='__main__':unittest.main()
