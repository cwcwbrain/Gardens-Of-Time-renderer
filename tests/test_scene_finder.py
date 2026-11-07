"""Finder orchestration uses explicit fake worker images, never fake game proof."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from PIL import Image

import gardens_renderer
from gardens.download import sha256
from gardens.scene_finder import find_scene,run_finder,_save_index,_cached_discovery,_load_names,_destination,open_result_folder
from gardens.named_export import _units


class SceneFinderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.out=self.root/'output';self.out.mkdir()
        self.csv=self.root/'scenes.csv'
        self.csv.write_text('SceneA,https://assets.test/a.jpg,https://assets.test/text_manager_items_SceneA.json\n')
        self.manifest=self.root/'catalogues.json';self.manifest.write_text('{"catalogues":["https://assets.test/catalogue.json"]}')
        self.mapping={'SceneA':{'scene_id':'SceneA','title':'Test Garden','scene_title':'Garden Caption','chapter_number':1,'order':1}}
        (self.out/'scene_names.json').write_text(json.dumps({'status':'available','mappings':self.mapping}))
        self.args=SimpleNamespace(output=self.out,scenes=self.csv,catalogues=self.manifest,find_scene='Test Garden',
             offline=True,remote_config=None,property=[],refresh_catalogues=False,timeout=2,retries=0,test_timeout=3,frame=None,open_folder=False)
        self.candidates=[{'roots':[letter],'issues':[],'bundles':[{'url':'https://assets.test/'+letter+'.bundle','internal_id':letter,'options':{}}]} for letter in ('a','b')]
        self.calls=[]

    def images(self,directory):
        directory.mkdir(parents=True,exist_ok=True);files=[];artifacts={}
        for role in ('empty','complete','solution'):
            path=directory/f'scene_{role}.png';Image.new('RGBA',(8,8),'cyan').save(path)
            files.append(str(path));artifacts[str(path)]=sha256(path)
        return files,artifacts

    def worker(self,job,*unused):
        self.calls.append(job)
        files,artifacts=self.images(Path(job['destination']))
        return {'status':'passed','issues':[],'files':files,'artifacts':artifacts}

    def invoke(self,worker=None,candidates=None,**kwargs):
        match={'status':'ambiguous','issues':[],'candidates':self.candidates if candidates is None else candidates}
        with patch('gardens.scene_finder._index',return_value=(object(),True)), \
             patch('gardens.scene_finder.match_scenes',return_value=[match]),contextlib.redirect_stdout(io.StringIO()):
            return find_scene('Test Garden',self.args,client=Mock(),runner=worker or self.worker,**kwargs)

    def test_all_variants_render_into_misc_and_repeat_reuses_without_other_fetches(self):
        original=(self.out/'scene_names.json').read_bytes()
        report=self.invoke()
        self.assertEqual(report['status'],'rendered');self.assertEqual(len(report['locations']),2)
        self.assertEqual(len(self.calls),2)
        for job in self.calls:
            self.assertTrue(Path(job['destination']).is_relative_to(self.out/'misc'))
            self.assertEqual(job['read_cache_roots'],[str(self.out)])
            self.assertEqual(Path(job['output']),self.out/'misc/_cache')
        self.assertEqual((self.out/'scene_names.json').read_bytes(),original)
        self.assertFalse((self.out/'batch').exists());self.assertTrue((self.out/'misc/misc_scene_index.csv').exists())
        with patch('gardens.scene_finder._index',side_effect=AssertionError('must not fetch catalogues')):
            again=find_scene('garden caption',self.args,client=Mock(),runner=Mock(side_effect=AssertionError('must not render')))
        self.assertEqual(again['status'],'found')

    def test_existing_named_or_batch_result_does_not_create_misc_or_fetch_metadata(self):
        locations=[{'scene_id':'SceneA','variant_id':'a','directory':str(self.out/'named_scenes/Existing'),'files':[],'source':'named'}]
        with patch('gardens.scene_finder.existing_scene_images',return_value=locations), \
             patch('gardens.scene_finder._index',side_effect=AssertionError('no downloads')):
            result=find_scene('test garden',self.args,client=Mock())
        self.assertEqual(result['status'],'found');self.assertEqual(result['locations'],locations)
        self.assertFalse((self.out/'misc').exists())

    def test_ambiguous_name_never_downloads_or_selects_first(self):
        self.mapping['SceneB']={**self.mapping['SceneA'],'scene_id':'SceneB','chapter_number':2}
        (self.out/'scene_names.json').write_text(json.dumps({'mappings':self.mapping}))
        with patch('gardens.scene_finder._index',side_effect=AssertionError('no downloads')):
            result=find_scene('Test Garden',self.args,client=Mock())
        self.assertEqual(result['status'],'ambiguous');self.assertEqual(len(result['candidates']),2)
        self.assertFalse((self.out/'misc').exists())

    def test_unknown_name_suggestions_do_not_render_a_near_match(self):
        with patch('gardens.scene_finder._load_names',side_effect=lambda query,args,out,cache,scenes,manifest,client,mappings:mappings), \
             patch('gardens.scene_finder._index',side_effect=AssertionError('no bundles')):
            result=find_scene('Test Garde',self.args,client=Mock())
        self.assertEqual(result['status'],'not_found');self.assertEqual(result['suggestions'][0]['scene_id'],'SceneA')

    def test_incomplete_attempt_is_not_claimed_complete_and_unchanged_repeat_is_cheap(self):
        proof=self.out/'input-proof';proof.write_bytes(b'explicit synthetic source')
        def incomplete(job,*unused):
            self.calls.append(job)
            return {'status':'incomplete','issues':['unsupported test feature'],'files':[],'artifacts':{str(proof):sha256(proof)}}
        first=self.invoke(incomplete,candidates=self.candidates[:1])
        second=self.invoke(incomplete,candidates=self.candidates[:1])
        self.assertEqual(first['status'],'incomplete');self.assertEqual(second['status'],'incomplete')
        self.assertEqual(len(self.calls),1);self.assertEqual(second['locations'],[])

    def test_failed_attempt_can_retry_without_overwriting_its_directory(self):
        def fail(job,*unused):
            self.calls.append(job)
            return {'status':'failed','issues':['test network failure'],'files':[],'artifacts':{}}
        first=self.invoke(fail,candidates=self.candidates[:1]);old=Path(first['attempts'][0]['directory'])
        (old/'keep.txt').write_text('user note')
        second=self.invoke(candidates=self.candidates[:1])
        self.assertEqual(second['status'],'rendered');self.assertNotEqual(second['locations'][0]['directory'],str(old))
        self.assertEqual((old/'keep.txt').read_text(),'user note')

    def test_missing_or_corrupt_worker_pngs_are_not_a_success(self):
        report=self.invoke(lambda *unused:{'status':'passed','issues':[],'files':[],'artifacts':{}},candidates=self.candidates[:1])
        self.assertEqual(report['status'],'incomplete');self.assertEqual(report['locations'],[])
        self.assertEqual(report['attempts'][0]['result']['status'],'failed')

    def test_blocked_variant_does_not_hide_another_usable_scene_version(self):
        broken={**self.candidates[1],'issues':['Missing dependency']}
        report=self.invoke(candidates=[self.candidates[0],broken])
        self.assertEqual(report['status'],'partial');self.assertEqual(len(self.calls),1)
        self.assertEqual(len(report['locations']),1)
        self.assertEqual([e['result']['status'] for e in report['attempts']],['passed','blocked'])

    def test_edited_misc_csv_is_preserved_while_json_index_advances(self):
        self.invoke(candidates=self.candidates[:1])
        path=self.out/'misc/misc_scene_index.csv';path.write_bytes(b'user edited index')
        existing=self.out/'misc/misc_scene_index.json'
        data=json.loads(existing.read_text());Path(data['variants'][0]['result']['files'][0]).write_bytes(b'changed scene PNG')
        self.invoke(candidates=self.candidates[:1])
        self.assertEqual(path.read_bytes(),b'user edited index')
        self.assertEqual(len(json.loads(existing.read_text())['variants']),2)

    def test_interactive_prompt_accepts_name_and_quit(self):
        self.args.find_scene=''
        with patch('builtins.input',side_effect=['Test Garden','q']), \
             patch('gardens.scene_finder.find_scene',return_value={'status':'found','locations':[]}) as finder, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_finder(self.args),0)
        finder.assert_called_once_with('Test Garden',self.args)

    def test_cli_dispatch_and_invalid_combinations(self):
        with patch('gardens.scene_finder.run_finder',return_value=0) as finder:
            self.assertEqual(gardens_renderer.main(['--find-scene','Test Garden','--open','--output',str(self.out)]),0)
        self.assertEqual(finder.call_args.args[0].find_scene,'Test Garden')
        self.assertTrue(finder.call_args.args[0].open_folder)
        for flags in (['--find-scene','Test Garden','--render'],['--all','--open'],['--find-scene','--refresh-solutions']):
            with self.subTest(flags=flags),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                gardens_renderer.main(flags)

    def test_online_missing_images_refresh_the_catalogue_snapshot(self):
        self.args.offline=False
        match={'status':'matched','issues':[],'candidates':self.candidates[:1]}
        with patch('gardens.scene_finder._index',return_value=(object(),True)) as indexed, \
             patch('gardens.scene_finder.match_scenes',return_value=[match]),contextlib.redirect_stdout(io.StringIO()):
            result=find_scene('Test Garden',self.args,client=Mock(),runner=self.worker)
        self.assertEqual(result['status'],'rendered');self.assertIs(indexed.call_args.args[-1],True)

    def test_nonempty_worker_issues_cannot_be_reported_as_passed(self):
        def bad(job,*unused):
            result=self.worker(job);result['issues']=['Missing required clue'];return result
        result=self.invoke(bad,candidates=self.candidates[:1])
        self.assertEqual(result['status'],'incomplete');self.assertEqual(result['locations'],[])

    def test_index_temp_symlinks_cannot_overwrite_main_files(self):
        misc=self.out/'misc';misc.mkdir();protected=self.out/'main-scene.png';protected.write_bytes(b'user data')
        for name in ('misc_scene_index.csv.tmp','misc_scene_index.json.tmp'):
            path=misc/name
            try:path.symlink_to(protected)
            except (OSError,NotImplementedError):self.skipTest('Symlink creation is unavailable')
            with self.assertRaises(ValueError):_save_index(misc,{'schema':1,'variants':[]})
            self.assertEqual(protected.read_bytes(),b'user data');path.unlink()

    def test_index_temp_hardlinks_cannot_overwrite_main_files(self):
        misc=self.out/'misc';misc.mkdir();protected=self.out/'main-scene.png';protected.write_bytes(b'user data')
        target=misc/'misc_scene_index.csv.tmp'
        try:os.link(protected,target)
        except (OSError,NotImplementedError):self.skipTest('Hardlink creation is unavailable')
        with self.assertRaisesRegex(ValueError,'linked lookup'):_save_index(misc,{'schema':1,'variants':[]})
        self.assertEqual(protected.read_bytes(),b'user data')

    def test_malformed_index_entries_are_preserved_before_any_download(self):
        misc=self.out/'misc';misc.mkdir();path=misc/'misc_scene_index.json'
        path.write_text('{"schema":1,"variants":["user note"]}');before=path.read_bytes()
        with patch('gardens.scene_finder._index',side_effect=AssertionError('no download')),self.assertRaisesRegex(ValueError,'malformed'):
            find_scene('Test Garden',self.args,client=Mock())
        self.assertEqual(path.read_bytes(),before)

    def test_uncached_config_override_does_not_create_main_directories(self):
        (self.out/'remote_config').mkdir()
        manifest={'remote_config':'https://assets.test/old-config','catalogues':['https://assets.test/catalogue.json']}
        with patch('gardens.scene_finder.catalogue_sources',side_effect=AssertionError('no mkdir in main')):
            result=_cached_discovery(manifest,[self.out],Mock(),'https://assets.test/new-config')
        self.assertEqual(result['source'],'manifest');self.assertEqual(list((self.out/'remote_config').iterdir()),[])

    def test_incomplete_borrowed_metadata_cache_stays_read_only(self):
        (self.out/'chapter_metadata').mkdir();(self.out/'naming/localization').mkdir(parents=True)
        before=sorted(str(p) for p in self.out.rglob('*'))
        with patch('gardens.scene_finder.load_official_names',side_effect=AssertionError('no missing-directory creation')):
            _load_names('Unlisted',self.args,self.out,self.out/'misc/_cache',[],{},Mock(),{})
        self.assertEqual(sorted(str(p) for p in self.out.rglob('*')),before)

    def test_incomplete_previews_can_open_without_claiming_complete(self):
        def partial(job,*unused):
            files,artifacts=self.images(Path(job['destination'])/'incomplete')
            return {'status':'incomplete','issues':['Unsupported test effect'],'files':files,'artifacts':artifacts}
        report=self.invoke(partial,candidates=self.candidates[:1]);self.assertEqual(report['status'],'incomplete')
        expected=str(Path(report['attempts'][0]['directory'])/'incomplete')
        with patch('gardens.scene_finder.sys.platform','win32'),patch('gardens.scene_finder.os.startfile',create=True) as opened, \
             contextlib.redirect_stdout(io.StringIO()):
            open_result_folder(report)
        opened.assert_called_once_with(expected)

    def test_deep_retry_path_reserves_space_for_incomplete_images(self):
        padding=150-_units(str(self.out/'misc'))-1
        if padding<1:self.skipTest('Temporary base path is already unusually long')
        misc=self.out/('d'*padding)/'misc';misc.mkdir(parents=True)
        first=_destination(misc,'A Very Long Official Scene Name '*10,'SceneA','a'*16)
        second=_destination(misc,'A Very Long Official Scene Name '*10,'SceneA','a'*16)
        self.assertNotEqual(first,second)
        self.assertLessEqual(_units(str(second/'incomplete/scene_solution.png')),249)


if __name__=='__main__':unittest.main()
