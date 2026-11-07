"""Exact scene-name lookup and image-proof reuse without touching main output."""
import json
import sqlite3
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from gardens.download import sha256
from gardens.scene_lookup import existing_scene_images,normalize_name,resolve_scene_name,suggestions


class NameLookupTests(unittest.TestCase):
    def setUp(self):
        self.mappings={
            'AncientGizaRiverfront':{'scene_id':'AncientGizaRiverfront','order':1,'title':'Giza Riverfront',
                'scene_title':'Ancient Giza - Riverfront','chapter_title':'Chapter 1 - Trouble at Time Manor',
                'primary_appearances':[{'title':'Giza Riverfront'},{'title':'Return to Giza'}]},
            'ClockTower':{'scene_id':'ClockTower','order':2,'title':'Clock Tower','scene_title':'Inside the Clock Tower'},
            'TeaRoom':{'scene_id':'TeaRoom','order':3,'title':'Hatter’s Tea Room','scene_title':'A Mad Tea Party'},
        }

    def test_case_whitespace_compatibility_and_apostrophes_normalize(self):
        self.assertEqual(normalize_name('  ＧＩＺＡ\u00a0 Riverfront \n'),'giza riverfront')
        self.assertEqual(resolve_scene_name(' GIZA   RIVERFRONT ',self.mappings)[0]['scene_id'],'AncientGizaRiverfront')
        self.assertEqual(resolve_scene_name("Hatter's Tea Room",self.mappings)[0]['scene_id'],'TeaRoom')
        self.assertEqual(normalize_name(None),'')

    def test_exact_id_full_caption_and_replay_titles_return_one_scene(self):
        for query in ['AncientGizaRiverfront','Ancient Giza - Riverfront','Return to Giza','Giza Riverfront']:
            with self.subTest(query=query):
                rows=resolve_scene_name(query,self.mappings)
                self.assertEqual([row['scene_id'] for row in rows],['AncientGizaRiverfront'])
        self.assertEqual(self.mappings['AncientGizaRiverfront']['title'],'Giza Riverfront')

    def test_distinct_scene_ids_with_same_title_stay_ambiguous(self):
        self.mappings['LaterGiza']={'scene_id':'LaterGiza','order':7,'title':'Giza Riverfront',
                                   'primary_appearances':[{'title':'Giza Riverfront'}]}
        self.assertEqual([m['scene_id'] for m in resolve_scene_name('Giza Riverfront',self.mappings)],
                         ['AncientGizaRiverfront','LaterGiza'])

    def test_no_substring_punctuation_stripping_chapter_or_fuzzy_auto_selection(self):
        for query in ['Giza','GizaRiverfront','Ancient Giza Riverfront','Chapter 1 - Trouble at Time Manor','Giza Rivefront','']:
            self.assertEqual(resolve_scene_name(query,self.mappings),[],query)
        self.assertEqual(suggestions('Giza Rivefront',self.mappings)[0]['scene_id'],'AncientGizaRiverfront')
        self.assertEqual(suggestions('',self.mappings),[])
        self.assertLessEqual(len(suggestions('Tower',self.mappings,limit=1)),1)

    def test_malformed_or_conflicting_name_mapping_is_not_resolved(self):
        self.assertEqual(resolve_scene_name('Tea Room',{'TeaRoom':{'scene_id':'DifferentScene','title':'Tea Room'}}),[])
        self.assertEqual(resolve_scene_name('Tea Room',{'../TeaRoom':{'title':'Tea Room'}}),[])
        self.assertEqual(resolve_scene_name('Giza',None),[])


class ExistingImageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name)/'output';self.out.mkdir()
        self.sid='AncientGizaRiverfront';self.vid='abc123'

    def images(self,directory,named=False):
        directory.mkdir(parents=True,exist_ok=True);files=[];artifacts={}
        for role in ('empty','complete','solution'):
            name=('Giza Riverfront - '+role.title()+'.png') if named else ('scene_'+role+'.png')
            path=directory/name;Image.new('RGBA',(3,2),(15,40,80,255)).save(path)
            files.append(str(path));artifacts[str(path.resolve())]=sha256(path)
        return files,artifacts

    def batch(self,status='passed'):
        directory=self.out/'scenes'/self.sid/('variant_'+self.vid)
        files,artifacts=self.images(directory)
        checkpoint=self.out/'batch/tests.sqlite3';checkpoint.parent.mkdir()
        # Missing non-image inputs must not hide already verified rendered PNGs.
        artifacts[str(self.out/'bundles/deleted.bundle')]='0'*64
        result={'status':status,'issues':[],'files':files,'artifacts':artifacts}
        definition={'scene':{'scene_id':self.sid},'destination':str(directory),'output':str(self.out)}
        with sqlite3.connect(checkpoint) as db:
            db.execute('CREATE TABLE jobs (scene_id TEXT,variant_id TEXT,status TEXT,definition TEXT,result TEXT,active INTEGER)')
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,1)',(self.sid,self.vid,status,json.dumps(definition),json.dumps(result)))
        return directory,result,checkpoint

    def named(self,custom=False):
        root=Path(self.temp.name)/'Custom Named Scenes' if custom else self.out/'named_scenes'
        directory=root/'Chapter 001'/('variant_'+self.vid)
        files,artifacts=self.images(directory,named=True);records=[];owned={}
        for role,raw in zip(('empty','complete','solution'),files):
            path=Path(raw);relative=path.relative_to(root).as_posix();digest=artifacts[raw]
            records.append({'scene_id':self.sid,'variant_id':self.vid,'role':role,'relative_path':relative,
                            'source_sha256':digest,'destination_sha256':digest,
                            'source':str(self.out/'scenes/deleted'/('scene_'+role+'.png'))})
            owned[relative]=digest
        manifest={'schema':2,'files':records,'owned_files':owned}
        path=root/'named_export_manifest.json';path.write_text(json.dumps(manifest))
        if custom:(self.out/'naming_report.json').write_text(json.dumps({'export':{'destination':str(root)}}))
        return directory,manifest,path

    def misc(self):
        root=self.out/'misc';directory=root/'Giza-Riverfront'/('variant_'+self.vid)
        files,artifacts=self.images(directory)
        artifacts[str(root/'cache/deleted.bundle')]='0'*64
        result={'status':'passed','issues':[],'files':files,'artifacts':artifacts}
        manifest={'schema':1,'variants':[{'scene_id':self.sid,'variant_id':self.vid,'directory':str(directory),'result':result}]}
        path=root/'misc_scene_index.json';path.write_text(json.dumps(manifest))
        return directory,manifest,path

    def snapshot(self):
        return {str(p):(p.read_bytes(),p.stat().st_mtime_ns) for p in self.out.rglob('*') if p.is_file()}

    def test_passed_batch_three_png_proof_is_reused_without_input_cache(self):
        directory,_,_=self.batch();before=self.snapshot()
        found=existing_scene_images(self.out,self.sid)
        self.assertEqual(len(found),1);self.assertEqual(found[0]['source'],'batch')
        self.assertEqual(found[0]['directory'],str(directory));self.assertEqual(len(found[0]['files']),3)
        self.assertEqual(self.snapshot(),before)

    def test_corrupt_missing_or_nonpassing_batch_pngs_are_not_available(self):
        directory,_,checkpoint=self.batch()
        image=directory/'scene_complete.png';original=image.read_bytes();image.write_bytes(b'changed')
        self.assertEqual(existing_scene_images(self.out,self.sid),[])
        image.write_bytes(original)
        with sqlite3.connect(checkpoint) as db:db.execute("UPDATE jobs SET status='incomplete'")
        self.assertEqual(existing_scene_images(self.out,self.sid),[])
        with sqlite3.connect(checkpoint) as db:db.execute("UPDATE jobs SET status='passed'")
        image.unlink();self.assertEqual(existing_scene_images(self.out,self.sid),[])

    def test_bad_database_is_skipped_without_creation_or_repair(self):
        checkpoint=self.out/'batch/tests.sqlite3';checkpoint.parent.mkdir();checkpoint.write_bytes(b'not sqlite')
        before=self.snapshot();self.assertEqual(existing_scene_images(self.out,self.sid),[])
        self.assertEqual(self.snapshot(),before)
        absent=Path(self.temp.name)/'absent'
        self.assertEqual(existing_scene_images(absent,self.sid),[]);self.assertFalse(absent.exists())

    def test_named_copies_remain_available_after_raw_images_are_deleted(self):
        directory,_,_=self.named();before=self.snapshot()
        found=existing_scene_images(self.out,self.sid)
        self.assertEqual([(r['source'],r['directory']) for r in found],[('named',str(directory))])
        self.assertEqual(self.snapshot(),before)

    def test_custom_named_directory_is_found_via_recorded_export_destination(self):
        directory,_,_=self.named(custom=True)
        found=existing_scene_images(self.out,self.sid)
        self.assertEqual(found[0]['directory'],str(directory));self.assertEqual(found[0]['source'],'named')

    def test_named_copy_requires_owned_hashes_and_all_three_roles(self):
        _,manifest,path=self.named()
        first=manifest['files'][0]['relative_path'];manifest['owned_files'][first]='0'*64
        path.write_text(json.dumps(manifest));self.assertEqual(existing_scene_images(self.out,self.sid),[])
        manifest['owned_files'][first]=manifest['files'][0]['destination_sha256']
        manifest['files'].pop();path.write_text(json.dumps(manifest))
        self.assertEqual(existing_scene_images(self.out,self.sid),[])

    def test_named_relative_path_escape_and_duplicate_image_roles_are_rejected(self):
        _,manifest,path=self.named();original=json.loads(json.dumps(manifest))
        manifest['files'][0]['relative_path']='../outside.png';path.write_text(json.dumps(manifest))
        self.assertEqual(existing_scene_images(self.out,self.sid),[])
        manifest=original;manifest['files'][2]['relative_path']=manifest['files'][0]['relative_path']
        path.write_text(json.dumps(manifest));self.assertEqual(existing_scene_images(self.out,self.sid),[])

    def test_hash_matching_non_png_is_not_reused(self):
        _,manifest,path=self.named();record=manifest['files'][0]
        image=path.parent/record['relative_path'];image.write_bytes(b'not png')
        record['source_sha256']=record['destination_sha256']=sha256(image)
        manifest['owned_files'][record['relative_path']]=sha256(image);path.write_text(json.dumps(manifest))
        self.assertEqual(existing_scene_images(self.out,self.sid),[])

    def test_misc_receipt_reuses_only_complete_verified_images(self):
        directory,_,_=self.misc();before=self.snapshot()
        found=existing_scene_images(self.out,self.sid)
        self.assertEqual([(r['source'],r['directory']) for r in found],[('misc',str(directory))])
        self.assertEqual(self.snapshot(),before)
        (directory/'scene_solution.png').write_bytes(b'changed')
        self.assertEqual(existing_scene_images(self.out,self.sid),[])

    def test_misc_must_be_passed_and_stay_inside_declared_misc_directory(self):
        _,manifest,path=self.misc();entry=manifest['variants'][0]
        entry['result']['status']='incomplete';path.write_text(json.dumps(manifest))
        self.assertEqual(existing_scene_images(self.out,self.sid),[])
        entry['result']['status']='passed';entry['directory']=str(Path(self.temp.name))
        path.write_text(json.dumps(manifest));self.assertEqual(existing_scene_images(self.out,self.sid),[])

    def test_legacy_unhashed_images_and_cached_bundles_are_not_image_proof(self):
        directory=self.out/'scenes'/self.sid;self.images(directory)
        (directory/'validation_report.json').write_text(json.dumps({'status':'validated_snapshot','issues':[]}))
        cache=self.out/'bundles';cache.mkdir();(cache/'scene.bundle').write_bytes(b'cached bytes')
        self.assertEqual(existing_scene_images(self.out,self.sid),[])

    def test_direct_render_receipt_reuses_images_without_a_batch_checkpoint(self):
        directory=self.out/'scenes'/self.sid;files,proof=self.images(directory)
        report=directory/'validation_report.json';report.write_text('{"status":"validated_snapshot","issues":[]}')
        receipt={'schema':1,'scene_id':self.sid,'variant_id':self.vid,'status':'passed',
                 'images':{Path(raw).name:proof[raw] for raw in files},'validation_report_sha256':sha256(report)}
        (directory/'scene_images_receipt.json').write_text(json.dumps(receipt))
        found=existing_scene_images(self.out,self.sid)
        self.assertEqual([(r['source'],r['directory']) for r in found],[('direct',str(directory))])
        report.write_text('{"status":"incomplete","issues":[]}')
        self.assertEqual(existing_scene_images(self.out,self.sid),[])

    def test_incremental_new_version_namespace_is_an_existing_scene(self):
        old,result,checkpoint=self.batch()
        directory=self.out/'scenes/_new_versions'/self.sid/('variant_'+self.vid)
        directory.parent.mkdir(parents=True);old.rename(directory)
        files={Path(raw).name:result['artifacts'][raw] for raw in result['files']}
        result['files']=[str(directory/name) for name in files]
        result['artifacts'].update({str(directory/name):digest for name,digest in files.items()})
        with sqlite3.connect(checkpoint) as db:
            definition=json.loads(db.execute('SELECT definition FROM jobs').fetchone()[0]);definition['destination']=str(directory)
            db.execute('UPDATE jobs SET definition=?,result=?',(json.dumps(definition),json.dumps(result)))
        found=existing_scene_images(self.out,self.sid)
        self.assertEqual(len(found),1);self.assertEqual(found[0]['directory'],str(directory))

    def test_portable_misc_receipts_survive_a_copied_output_root(self):
        directory,manifest,path=self.misc();entry=manifest['variants'][0]
        entry['relative_directory']=directory.relative_to(path.parent).as_posix()
        entry['image_receipts']={Path(raw).name:entry['result']['artifacts'][raw] for raw in entry['result']['files']}
        path.write_text(json.dumps(manifest))
        copied=Path(self.temp.name)/'copied-output';shutil.copytree(self.out,copied)
        found=existing_scene_images(copied,self.sid)
        self.assertEqual(len(found),1)
        self.assertTrue(Path(found[0]['directory']).is_relative_to(copied/'misc'))

    def test_source_locations_remain_distinct_but_no_wrong_scene_is_returned(self):
        self.batch();self.named();self.misc()
        self.assertEqual([r['source'] for r in existing_scene_images(self.out,self.sid)],['batch','named','misc'])
        self.assertEqual(existing_scene_images(self.out,'DifferentScene'),[])
        self.assertEqual(existing_scene_images(self.out,'../'+self.sid),[])

    def test_symlinked_scene_directories_are_not_automatically_reused(self):
        directory,_,_=self.batch();outside=Path(self.temp.name)/'outside';directory.rename(outside)
        try:directory.symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('Symlinks not available')
        self.assertEqual(existing_scene_images(self.out,self.sid),[])


if __name__=='__main__':unittest.main()
