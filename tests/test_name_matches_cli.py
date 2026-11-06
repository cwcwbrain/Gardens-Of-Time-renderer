"""Official name matching covers every roster row without rendering or exports."""
import contextlib
import csv
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

import gardens_renderer
from gardens.scenes import Scene,variant_id
import test_update_cli as update_fixtures


class NameMatchesCLITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.out=self.root/'fresh-output'
        self.manifest=self.root/'catalogues.json'
        self.manifest.write_text(json.dumps({'remote_config':'https://official.test/GOT_RemoteConfig.json','catalogues':[]}))
        self.scenes=[Scene(1,'AncientGizaRiverfront','','https://assets.test/giza-items.json',[]),
                     Scene(2,'VictorianExteriorRedux1','','https://assets.test/victorian-items.json',[]),
                     Scene(3,'ch748RawIdentifierWithoutATitle','','https://assets.test/new-items.json',[])]
        self.discovery={'source':'live_remote_config','config_path':str(self.root/'verified-config.json'),'catalogues':[]}
        self.client=Mock()

    def mapping(self,scene,title,scene_title,chapter=1,mission_order=1,replays=()):
        first={'chapter_number':chapter,'mission_order':mission_order,'title':title}
        return {'scene_id':scene.scene_id,'order':scene.order,'title':title,'scene_title':scene_title,
                'chapter_number':chapter,'chapter_title':f'Chapter {chapter}: Official chapter heading',
                'mission_order':mission_order,'mission_id':str(100+scene.order),'story_key':'chapter_'+str(chapter),
                'mission_key':'mission_'+str(scene.order),'source_scene_id':str(900+scene.order),
                'localization_keys':{'scene':scene.scene_id,'mission':'mission_'+str(scene.order),'story':'chapter_'+str(chapter)},
                'primary_appearances':[first,*replays]}

    def report(self,*,status='available',omit=()):
        mappings={}
        for scene in self.scenes:
            if scene.scene_id not in omit:
                mappings[scene.scene_id]=self.mapping(scene,'Official list title '+str(scene.order),'Official scene title '+str(scene.order))
        issues=[{'scene_id':sid,'issues':['Missing official chapter-list title localization']} for sid in omit]
        return {'status':status,'mappings':mappings,'issues':issues,'source_issues':[],
                'provenance':{'source':'explicit official localization fixture'},
                'summary':{'requested_scenes':len(self.scenes),'mapped_scenes':len(mappings),'unmapped_scenes':len(issues)}}

    def invoke(self,report,*,extra=()):
        with ExitStack() as stack:
            stack.enter_context(patch('gardens_renderer.read_scenes',return_value=(self.scenes,[])))
            stack.enter_context(patch('gardens_renderer.Downloader',return_value=self.client))
            source=stack.enter_context(patch('gardens.discovery.catalogue_sources',return_value=self.discovery))
            names=stack.enter_context(patch('gardens.names.load_official_names',return_value=report))
            stack.enter_context(patch('gardens_renderer.parse_catalogue',side_effect=AssertionError('name matching must not index HO catalogues')))
            stack.enter_context(patch('gardens_renderer.Index',side_effect=AssertionError('name matching must not build a HO index')))
            stack.enter_context(patch('gardens_renderer.match_scenes',side_effect=AssertionError('name matching must not match AssetBundles')))
            stack.enter_context(patch('gardens.render.render_bundle',side_effect=AssertionError('name matching must not render scenes')))
            stack.enter_context(patch('gardens.named_export.export_named',side_effect=AssertionError('name matching must not export images')))
            stack.enter_context(patch('gardens.batch.retry_incomplete',side_effect=AssertionError('name matching must not start workers')))
            stack.enter_context(patch('gardens.incremental.enroll_updates',side_effect=AssertionError('name matching must not enroll variants')))
            with contextlib.redirect_stdout(io.StringIO()):
                code=gardens_renderer.main(['--match-names','--output',str(self.out),'--catalogues',str(self.manifest),*extra])
        with (self.out/'scene_name_matches.csv').open(encoding='utf-8-sig',newline='') as stream:
            rows=list(csv.DictReader(stream))
        return code,rows,names,source

    def test_fresh_output_matches_every_csv_scene_in_corrected_order_without_checkpoint_or_images(self):
        source=self.report()
        code,rows,load,discovery=self.invoke(source)
        self.assertEqual(code,0);load.assert_called_once();discovery.assert_called_once()
        self.assertEqual([row['scene_id'] for row in rows],[scene.scene_id for scene in self.scenes])
        self.assertEqual([int(row['order']) for row in rows],[1,2,3])
        self.assertEqual({row['match_status'] for row in rows},{'matched'})
        self.assertEqual(json.loads((self.out/'scene_names.json').read_text()),source)
        self.assertTrue((self.out/'scene_name_match_report.json').is_file())
        self.assertFalse((self.out/'batch').exists());self.assertFalse((self.out/'scenes').exists())
        self.assertFalse((self.out/'named_scenes').exists());self.assertEqual(list(self.out.rglob('*.png')),[])
        self.client.fetch.assert_not_called()
        self.assertEqual(load.call_args.args[2],self.discovery['config_path'])
        self.assertEqual([scene.scene_id for scene in load.call_args.args[3]],[scene.scene_id for scene in self.scenes])

    def test_csv_retains_exact_chapter_list_name_distinct_scene_title_localization_and_replay_aliases(self):
        source=self.report();sid=self.scenes[0].scene_id
        alias={'chapter_number':44,'mission_order':6,'title':'Official replay title'}
        source['mappings'][sid]=self.mapping(self.scenes[0],'Riverfront, Egypt: "First Visit"','A Different Scene Title',replays=[alias])
        code,rows,_,_=self.invoke(source)
        self.assertEqual(code,0);row=rows[0]
        self.assertEqual(row['in_game_name'],'Riverfront, Egypt: "First Visit"')
        self.assertEqual(row['scene_title'],'A Different Scene Title')
        self.assertNotEqual(row['in_game_name'],sid);self.assertNotEqual(row['in_game_name'],row['scene_title'])
        self.assertEqual(row['chapter_number'],'1');self.assertEqual(row['chapter_title'],'Chapter 1: Official chapter heading')
        self.assertEqual(row['mission_id'],'101');self.assertEqual(row['story_key'],'chapter_1')
        self.assertEqual(row['localization_scene_key'],sid)
        self.assertEqual(row['localization_mission_key'],'mission_1');self.assertEqual(row['localization_story_key'],'chapter_1')
        self.assertEqual(json.loads(row['replay_aliases']),[alias]);self.assertEqual(json.loads(row['issues']),[])

    def test_unmatched_row_remains_visible_with_empty_display_names_and_nonzero_exit(self):
        sid=self.scenes[-1].scene_id;source=self.report(status='partial',omit=[sid])
        code,rows,_,_=self.invoke(source)
        self.assertEqual(code,1);self.assertEqual(len(rows),3)
        row=rows[-1];self.assertEqual(row['scene_id'],sid);self.assertEqual(row['match_status'],'unmatched')
        for field in ('in_game_name','scene_title','chapter_number','chapter_title'):
            self.assertEqual(row[field],'')
        self.assertEqual(json.loads(row['issues']),['Missing official chapter-list title localization'])
        self.assertEqual(json.loads((self.out/'scene_names.json').read_text())['issues'],source['issues'])

    def test_partial_source_with_all_rows_mapped_still_does_not_claim_complete_match(self):
        source=self.report(status='partial');source['source_issues']=['Duplicate primary story in source metadata']
        code,rows,_,_=self.invoke(source)
        self.assertEqual(code,1);self.assertTrue(all(row['match_status']=='matched' for row in rows))
        self.assertEqual(json.loads((self.out/'scene_names.json').read_text())['source_issues'],source['source_issues'])

    def test_source_available_flag_is_downgraded_when_an_official_mapping_is_missing(self):
        source=self.report(omit=[self.scenes[-1].scene_id])
        code,rows,_,_=self.invoke(source)
        self.assertEqual(code,1);self.assertEqual(rows[-1]['match_status'],'unmatched')
        self.assertEqual(rows[-1]['in_game_name'],'')
        self.assertEqual(json.loads((self.out/'scene_names.json').read_text())['status'],'partial')
        self.assertEqual(json.loads((self.out/'scene_name_match_report.json').read_text())['summary']['unmatched_scenes'],1)

    def test_excel_locked_lookup_keeps_old_csv_and_saves_complete_new_temporary_lookup(self):
        self.out.mkdir();target=self.out/'scene_name_matches.csv';target.write_bytes(b'previous lookup must stay intact')
        source_image=self.out/'scenes/existing.png';source_image.parent.mkdir();source_image.write_bytes(b'original scene bytes')
        before=(target.read_bytes(),target.stat().st_mtime_ns)
        image_before=(source_image.read_bytes(),source_image.stat().st_mtime_ns)
        replace=Path.replace
        def locked(path,destination):
            if Path(destination)==target:raise PermissionError('Excel has the CSV open')
            return replace(path,destination)
        with patch.object(Path,'replace',locked),self.assertRaisesRegex(ValueError,'Excel'):
            self.invoke(self.report())
        self.assertEqual((target.read_bytes(),target.stat().st_mtime_ns),before)
        self.assertEqual((source_image.read_bytes(),source_image.stat().st_mtime_ns),image_before)
        with (self.out/'scene_name_matches.csv.tmp').open(encoding='utf-8-sig',newline='') as stream:
            rows=list(csv.DictReader(stream))
        self.assertEqual([row['scene_id'] for row in rows],[scene.scene_id for scene in self.scenes])
        self.assertEqual({row['match_status'] for row in rows},{'matched'})
        self.assertFalse((self.out/'batch').exists())

    def test_unavailable_source_writes_all_roster_rows_as_unmatched_without_guessing_names(self):
        source={'status':'unavailable','mappings':{},'issues':[],'source_issues':['Official names unavailable'],
                'summary':{'requested_scenes':3,'mapped_scenes':0,'unmapped_scenes':3},'provenance':{}}
        code,rows,_,_=self.invoke(source)
        self.assertEqual(code,1);self.assertEqual(len(rows),3)
        self.assertTrue(all(row['match_status']=='unmatched' and row['in_game_name']=='' for row in rows))
        self.assertEqual(list(self.out.rglob('*.png')),[])

    def add_ledger_scene(self,fixture,scene,active):
        value=update_fixtures.candidate(scene.scene_id)
        definition={'scene':asdict(scene),'candidate':value,'destination':str(fixture.out/'scenes'/scene.scene_id),
                    'output':str(fixture.out),'frame':None,'offline':True,'timeout':30,'retries':2}
        result={'status':'incomplete','files':[],'issues':['not rendered']}
        with sqlite3.connect(fixture.checkpoint) as db:
            db.execute('INSERT INTO scenes VALUES (?,?,?,?,?)',(scene.scene_id,scene.order,'matched','[]',active))
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)',
                       (scene.scene_id,variant_id(value),scene.order,'historical','incomplete',json.dumps(definition),json.dumps(result),active))

    def test_existing_checkpoint_adds_new_chapter_ids_and_preserves_saved_order_rows_and_images(self):
        fixture=update_fixtures.UpdateCLITests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.out=fixture.out;self.manifest=fixture.manifest
        self.add_ledger_scene(fixture,fixture.new,active=1)
        self.add_ledger_scene(fixture,Scene(9,'InactiveOnly','','https://assets.test/inactive.json',[]),active=0)
        self.scenes=[Scene(999,'savedpass','','https://wrong.test/items.json',[]),fixture.scenes[1],
                     Scene(4,'CSVOnly','','https://assets.test/csv-items.json',[])]
        source={'status':'available','mappings':{},'issues':[],'source_issues':[],'provenance':{'source':'official fixture'}}
        requested=[fixture.scenes[0],fixture.scenes[1],fixture.new,self.scenes[-1]]
        for scene in requested:source['mappings'][scene.scene_id]=self.mapping(scene,'Official List '+str(scene.order),'Scene Title '+str(scene.order))
        before=fixture.rows();images=fixture.passed_files()
        for filename in ('summary.json','test_report.csv'):(self.out/filename).write_bytes(b'existing test results must not change')
        code,rows,load,_=self.invoke(source)
        self.assertEqual(code,0)
        self.assertEqual([row['scene_id'] for row in rows],['SavedPass','SavedIncomplete','ch748VerifiedNewScene','CSVOnly'])
        self.assertEqual([int(row['order']) for row in rows],[1,2,3,4])
        self.assertEqual([scene.scene_id for scene in load.call_args.args[3]],[scene.scene_id for scene in requested])
        self.assertEqual(fixture.rows(),before);self.assertEqual(fixture.passed_files(),images)
        for filename in ('summary.json','test_report.csv'):
            self.assertEqual((self.out/filename).read_bytes(),b'existing test results must not change')
        self.assertNotIn('InactiveOnly',[row['scene_id'] for row in rows])

    def test_metadata_only_selector_rejects_render_export_and_test_options(self):
        for flags in (['--render'],['--index-only'],['--test-all'],['--named-output','somewhere'],['--include-pending']):
            with self.subTest(flags=flags),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
                gardens_renderer.main(['--match-names','--output',str(self.out),*flags])
            self.assertEqual(error.exception.code,2)

    def test_checkpoint_read_error_is_actionable_and_does_not_change_saved_work(self):
        fixture=update_fixtures.UpdateCLITests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        before=fixture.rows();images=fixture.passed_files()
        args=SimpleNamespace(output=fixture.out,offline=True)
        with patch('gardens_renderer.sqlite3.connect',side_effect=sqlite3.OperationalError('disk I/O error')), \
             patch('gardens.names.load_official_names',side_effect=AssertionError('must not fetch metadata after read failure')):
            with self.assertRaisesRegex(ValueError,'read saved scene identities') as error:
                gardens_renderer.match_scene_names(args,self.client,self.discovery,fixture.scenes)
        self.assertIn('OneDrive',str(error.exception));self.assertIn('do not delete the checkpoint',str(error.exception))
        self.assertEqual(fixture.rows(),before);self.assertEqual(fixture.passed_files(),images)


if __name__=='__main__':unittest.main()
