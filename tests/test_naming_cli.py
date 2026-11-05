"""Official-name export orchestration never rerenders preserved scene images."""
import contextlib
import io
import json
import sqlite3
import sys
import types
import unittest
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

import gardens_renderer
from gardens.scenes import Scene,variant_id
import test_update_cli as update_fixtures

candidate=update_fixtures.candidate


class NamingCLITests(unittest.TestCase):
    def setUp(self):
        self.fixture=update_fixtures.UpdateCLITests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.out=self.fixture.out

    def test_missing_checkpoint_stops_standalone_before_metadata_fetch(self):
        target=self.fixture.root/'no-previous-run'
        with patch('gardens_renderer.Downloader',side_effect=AssertionError('must not fetch')), \
             patch('gardens_renderer.read_scenes',side_effect=AssertionError('must not read scenes')), \
             contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
            gardens_renderer.main(['--export-named','--output',str(target)])
        self.assertEqual(error.exception.code,2);self.assertFalse(target.exists())

    def test_standalone_loads_metadata_then_exports_without_ho_index_or_renderer(self):
        discovery={'source':'live_remote_config','config_path':'verified-config.json','catalogues':[]}
        client=Mock();destination=self.fixture.root/'My Named Images'
        before=self.fixture.rows();images=self.fixture.passed_files()
        with ExitStack() as stack:
            stack.enter_context(patch('gardens_renderer.read_scenes',return_value=(self.fixture.scenes,[])))
            stack.enter_context(patch('gardens_renderer.Downloader',return_value=client))
            source=stack.enter_context(patch('gardens.discovery.catalogue_sources',return_value=discovery))
            named=stack.enter_context(patch('gardens_renderer.name_scene_exports',return_value=0,create=True))
            stack.enter_context(patch('gardens_renderer.parse_catalogue',side_effect=AssertionError('must not index HO catalogues')))
            stack.enter_context(patch('gardens_renderer.match_scenes',side_effect=AssertionError('must not rematch scenes')))
            stack.enter_context(patch('gardens.batch.retry_incomplete',side_effect=AssertionError('must not render')))
            stack.enter_context(patch('gardens.incremental.enroll_updates',side_effect=AssertionError('must not enroll')))
            with contextlib.redirect_stdout(io.StringIO()):
                code=gardens_renderer.main(['--export-named','--output',str(self.out),'--catalogues',str(self.fixture.manifest),
                                            '--named-output',str(destination)])
        self.assertEqual(code,0);source.assert_called_once();named.assert_called_once()
        args,got_client,got_discovery,got_scenes=named.call_args.args
        self.assertEqual(args.named_output,destination)
        self.assertIs(got_client,client);self.assertEqual(got_discovery,discovery)
        self.assertEqual(got_scenes,self.fixture.scenes)
        self.assertEqual(self.fixture.rows(),before);self.assertEqual(self.fixture.passed_files(),images)
        client.fetch.assert_not_called()

    def test_update_auto_exports_even_when_reconstruction_remains_incomplete(self):
        code,used=self.fixture.invoke(retry_return=1)
        self.assertEqual(code,1);used.naming.assert_called_once()
        self.assertEqual(used.naming.call_args.args[0].update,True)
        self.assertEqual([scene.scene_id for scene in used.naming.call_args.args[3]],
                         ['SavedPass','SavedIncomplete','ch748VerifiedNewScene'])

    def test_update_skips_export_for_plan_or_interruption(self):
        for flags,retry_code,expected in ((['--plan-only'],0,0),([],130,130)):
            with self.subTest(flags=flags,retry_code=retry_code):
                code,used=self.fixture.invoke(extra=flags,retry_return=retry_code)
                self.assertEqual(code,expected);used.naming.assert_not_called()

    def test_update_propagates_failed_export_after_successful_reconstruction(self):
        code,used=self.fixture.invoke(naming_return=1)
        self.assertEqual(code,1);used.naming.assert_called_once()

    def test_strict_incomplete_retry_does_not_export_names(self):
        with patch('gardens.batch.retry_incomplete',return_value=0) as retry, \
             patch('gardens_renderer.name_scene_exports',side_effect=AssertionError('must not name'),create=True), \
             patch('gardens_renderer.Downloader',side_effect=AssertionError('must not fetch')):
            code=gardens_renderer.main(['--retry-incomplete','--output',str(self.out)])
        self.assertEqual(code,0);retry.assert_called_once()

    def test_named_output_is_rejected_for_modes_without_naming(self):
        for selector in ('--test-all','--retry-incomplete','--all'):
            with self.subTest(selector=selector),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
                gardens_renderer.main([selector,'--output',str(self.out),'--named-output',str(self.fixture.root/'names')])
            self.assertEqual(error.exception.code,2)

    def add_ledger_scene(self,scene,active):
        definition={'scene':asdict(scene),'candidate':candidate(scene.scene_id),'destination':str(self.out/'scenes'/scene.scene_id),
                    'output':str(self.out),'frame':None,'offline':True,'timeout':30,'retries':2,'fingerprint':'old'}
        result={'status':'incomplete','files':[],'issues':['not rendered']}
        with sqlite3.connect(self.fixture.checkpoint) as db:
            db.execute('INSERT INTO scenes VALUES (?,?,?,?,?)',(scene.scene_id,scene.order,'matched','[]',active))
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)',
                       (scene.scene_id,variant_id(definition['candidate']),scene.order,'old','incomplete',json.dumps(definition),json.dumps(result),active))

    def call_helper(self,*,names_status='available',export_status='completed',destination=None):
        names=types.ModuleType('gardens.names');exports=types.ModuleType('gardens.named_export')
        mapping={'SavedPass':{'scene_name':'Official Scene','chapter_name':'Official Chapter'}}
        names.load_official_names=Mock(return_value={'status':names_status,'mappings':mapping,'issues':[],'provenance':{'source':'official data'}})
        exports.export_named=Mock(return_value={'status':export_status,'summary':{'exported':1},'issues':[],
                                               'manifest_path':'named_manifest.json','index_path':'index.html'})
        args=SimpleNamespace(output=self.out,named_output=destination,offline=True)
        client=Mock();discovery={'config_path':'verified-config.json','source':'cached_remote_config'}
        csv=[Scene(999,'savedpass','','https://wrong.test/items.json',[]),
             self.fixture.scenes[1],Scene(4,'CSVOnly','','https://assets.test/csv-items.json',[])]
        with patch.dict(sys.modules,{'gardens.names':names,'gardens.named_export':exports}),contextlib.redirect_stdout(io.StringIO()):
            code=gardens_renderer.name_scene_exports(args,client,discovery,csv)
        return code,names.load_official_names,exports.export_named

    def test_helper_combines_active_new_chapter_ledger_with_csv_and_keeps_saved_identity(self):
        self.add_ledger_scene(self.fixture.new,active=1)
        self.add_ledger_scene(Scene(5,'InactiveOnly','','https://assets.test/inactive.json',[]),active=0)
        before=self.fixture.rows();images=self.fixture.passed_files();destination=self.fixture.root/'Named'
        code,load,export=self.call_helper(destination=destination)
        self.assertEqual(code,0)
        scenes=load.call_args.args[3];lookup={scene.scene_id:scene for scene in scenes}
        self.assertEqual(set(lookup),{'SavedPass','SavedIncomplete','ch748VerifiedNewScene','CSVOnly'})
        self.assertEqual(lookup['SavedPass'].order,1)
        self.assertEqual(lookup['SavedPass'].item_manager_url,'https://assets.test/pass-items.json')
        self.assertEqual(lookup['ch748VerifiedNewScene'].order,3)
        self.assertTrue(load.call_args.kwargs.get('offline',False) or (len(load.call_args.args)>4 and load.call_args.args[4]))
        self.assertEqual(export.call_args.args[0],self.out.resolve())
        self.assertEqual(export.call_args.kwargs['destination'],destination)
        self.assertEqual(self.fixture.rows(),before);self.assertEqual(self.fixture.passed_files(),images)
        self.assertTrue((self.out/'scene_names.json').is_file());self.assertTrue((self.out/'naming_report.json').is_file())

    def test_helper_source_or_export_failure_does_not_claim_complete_named_export(self):
        for source,target,expected in (('unavailable','completed',1),('available','partial',1),('partial','completed',0)):
            with self.subTest(source=source,target=target):
                code,_,_=self.call_helper(names_status=source,export_status=target)
                self.assertEqual(code,expected)


if __name__=='__main__':unittest.main()
