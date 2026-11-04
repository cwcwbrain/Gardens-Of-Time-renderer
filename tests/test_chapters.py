"""Real chapter metadata joins, with mocked transfer/Unity decode boundaries."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from gardens.catalogue import Index, Location
from gardens.chapters import ASSET_KEY, ASSET_PATH, RUNTIME_ERROR, RUNTIME_PROPERTY, _read_tree, append_chapters, discover_chapters
from gardens.download import DownloadError, atomic_json, cache_name, sha256
from gardens.scenes import Scene


FIXTURE = Path(__file__).parent/'fixtures/chapter747_748.json'
PREFIX = 'https://prod-cdn-assets.gardensoftime-cloud.net/catalog/xml/en_us/text_manager_items_'


def location(identity, primary, internal, dependencies=(), bundle=False):
    return Location(identity, 'https://cdn.test/main/catalogue.json', internal,
                    'AssetBundleProvider' if bundle else 'BundledAssetProvider', primary,
                    [primary], list(dependencies), {}, url=internal if bundle else None)


class Client:
    def __init__(self, responses):
        self.responses, self.calls, self.refreshes = responses, [], []

    def fetch(self, url, path, validator, refresh=False):
        self.calls.append(url)
        self.refreshes.append((url, refresh))
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        validator(path)
        atomic_json(str(path)+'.verified.json', {'url': url, 'sha256': sha256(path)})


class ChapterTests(unittest.TestCase):
    def setUp(self):
        self.tree = json.loads(FIXTURE.read_text())
        original = [s for s in self.tree['sceneVOs'] if s['name'].startswith('ch747')]
        self.scenes = [Scene(i+1, s['name'], 'original-thumb', PREFIX+s['name']+'.json', ['original', s['name']])
                       for i, s in enumerate(original)]
        self.future = [s['name'] for s in self.tree['sceneVOs'] if s['name'].startswith('ch748')]

    def test_real_authoritative_chapter_order_and_original_rows_preserved(self):
        extended, report = append_chapters(self.scenes, self.tree)
        self.assertEqual(extended[:6], self.scenes)
        self.assertTrue(all(a is b for a, b in zip(extended[:6], self.scenes)))
        self.assertEqual([s.scene_id for s in extended[6:]], self.future)
        self.assertEqual([s.order for s in extended[6:]], list(range(7, 13)))
        self.assertEqual(report['base_max_chapter'], 747)
        self.assertEqual(report['new_chapters'], [748])
        self.assertEqual([e['mission_id'] for e in report['added_scenes']], ['9714','9715','9716','9717','9718','9719'])
        self.assertEqual([e['position'] for e in report['added_scenes']], [1,2,3,4,5,6])
        self.assertEqual(extended[6].thumbnail_url, '')
        self.assertEqual(report['status'], 'metadata_only')

    def test_replay_names_are_deduplicated_without_reordering_csv(self):
        # A future chapter can replay a scene from an earlier mission.
        self.tree['missionVOs'][6]['scene_id'] = self.tree['sceneVOs'][0]['sid']
        extended, report = append_chapters(self.scenes, self.tree)
        self.assertEqual(extended[:6], self.scenes)
        self.assertEqual(len(extended), 11)
        self.assertEqual(report['skipped_duplicates'][0]['scene_id'], self.scenes[0].scene_id)
        self.assertEqual(report['added_scenes'][0]['position'], 2)

    def test_malformed_ids_references_and_order_fail_closed(self):
        for kind in ('scene_duplicate','mission_duplicate','missing_scene','wrong_story','wrong_order','missing_mission','chapter_duplicate','chapter_gap'):
            tree = copy.deepcopy(self.tree)
            if kind == 'scene_duplicate': tree['sceneVOs'].append(tree['sceneVOs'][0])
            elif kind == 'mission_duplicate': tree['missionVOs'].append(tree['missionVOs'][0])
            elif kind == 'missing_scene': tree['missionVOs'][6]['scene_id'] = 'missing'
            elif kind == 'wrong_story': tree['missionVOs'][6]['parent_stid'] = 'wrong'
            elif kind == 'wrong_order': tree['missionVOs'][6]['sort_order'] = '6'
            elif kind == 'missing_mission': tree['storyVOs'][1]['mission_ids'] = 'absent'
            elif kind == 'chapter_gap': tree['storyVOs'][1]['name'] = 'chapter_749'
            else:
                tree['storyVOs'].append({**tree['storyVOs'][1], 'stid':'different'})
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                append_chapters(self.scenes, tree)

    def test_no_unobserved_item_manager_origin_or_labels_are_invented(self):
        self.scenes[0].item_manager_url = 'https://other.test/text_manager_items_'+self.scenes[0].scene_id+'.json'
        with self.assertRaisesRegex(ValueError, 'unique Item Manager'):
            append_chapters(self.scenes, self.tree)

    def test_missing_remote_config_preserves_original_scenes_with_reported_error(self):
        with tempfile.TemporaryDirectory() as folder:
            scenes,report=discover_chapters(self.scenes,Index([]),folder,Client({}),None)
        self.assertEqual(scenes,self.scenes)
        self.assertEqual(report['status'],'unavailable')
        self.assertIn('remote-config snapshot',report['errors'][0])

    def setup_discovery(self, folder):
        config = folder/'config.json'
        config.write_text(json.dumps({'assetCatalogInfo': [{
            'catalogProjectName':'MainProject',
            'URLInfo_Prod':{'URL':'https://cdn.test/main/','catalogName':'catalogue.json'}}]}))
        main = [location('group', ASSET_KEY, ASSET_PATH, ['meta.bundle']),
                location('meta', 'meta.bundle', 'https://cdn.test/main/meta.bundle', bundle=True)]
        ho = []
        for i, name in enumerate(self.future):
            ho += [location('scene'+str(i), name, 'Assets/RemoteResource/HO/'+name+'.prefab', ['scene'+str(i)+'.bundle']),
                   location('bundle'+str(i), 'scene'+str(i)+'.bundle', 'https://cdn.test/ho/scene'+str(i)+'.bundle', bundle=True)]
        responses = {'https://cdn.test/main/catalogue.json':b'catalogue bytes',
                     'https://cdn.test/main/meta.bundle':b'validated bundle placeholder'}
        responses.update({PREFIX+name+'.json':b'{"verified_object":{"beginner":"Verified label"}}' for name in self.future})
        return config, main, ho, responses

    def discover(self, folder, config, main, ho, client, offline=False):
        with patch('gardens.chapters.parse_catalogue', return_value=(main, {})), \
             patch('gardens.chapters.validate_bundle'), \
             patch('gardens.chapters._read_tree', return_value=self.tree):
            return discover_chapters(self.scenes, Index(ho), folder, client, config, offline)

    def test_metadata_and_managers_verified_then_reused_offline(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            config, main, ho, responses = self.setup_discovery(folder)
            client = Client(responses)
            extended, report = self.discover(folder, config, main, ho, client)
            self.assertEqual(report['status'], 'available')
            self.assertFalse(report['unverified_scenes'])
            self.assertEqual(len(report['verified_item_managers']), 6)
            self.assertIn(('https://cdn.test/main/catalogue.json', True), client.refreshes)
            self.assertEqual(report['metadata_source'], 'live_main_catalogue')
            self.assertTrue(all(s.item_manager_url for s in extended))
            offline = Client({})
            cached, cached_report = self.discover(folder, config, main, ho, offline, True)
            self.assertEqual(cached, extended)
            self.assertEqual(cached_report['status'], 'available')
            self.assertEqual(cached_report['metadata_source'], 'verified_cache')
            self.assertEqual(offline.calls, [])

    def test_unavailable_manager_keeps_its_chronological_slot_but_no_usable_url(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            config, main, ho, responses = self.setup_discovery(folder)
            responses[PREFIX+self.future[0]+'.json'] = DownloadError('HTTP 404')
            extended, report = self.discover(folder, config, main, ho, Client(responses))
            self.assertEqual(len(extended), 12)
            self.assertEqual(extended[6].scene_id, self.future[0])
            self.assertEqual(extended[6].item_manager_url, '')
            self.assertEqual(extended[7].order, 8)
            self.assertEqual(report['unverified_scenes'][0]['scene_id'], self.future[0])
            self.assertEqual(len(report['verified_item_managers']), 5)

    def test_missing_scene_bundle_is_reported_without_manager_request(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            config, main, ho, responses = self.setup_discovery(folder)
            del responses[PREFIX+self.future[0]+'.json']
            client = Client(responses)
            extended, report = self.discover(folder, config, main, ho[2:], client)
            self.assertEqual(extended[6].item_manager_url, '')
            self.assertEqual(report['missing_assets'][0]['scene_id'], self.future[0])
            self.assertNotIn(PREFIX+self.future[0]+'.json', client.calls)

    def test_bad_metadata_falls_back_to_original_scenes_without_partial_append(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            config, main, ho, responses = self.setup_discovery(folder)
            self.tree['missionVOs'][7]['scene_id'] = 'missing'
            extended, report = self.discover(folder, config, main, ho, Client(responses))
            self.assertEqual(extended, self.scenes)
            self.assertEqual(report['status'], 'unavailable')
            self.assertTrue(report['errors'])

    def test_exact_unity_container_reference_selects_the_production_asset(self):
        pointer = SimpleNamespace(read_typetree=lambda: self.tree)
        class ContainerHelper:
            # Match UnityPy's mapping surface: there is deliberately no .get.
            def __init__(self, values): self.values = values
            def __contains__(self, key): return key in self.values
            def __getitem__(self, key): return self.values[key]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td)/'meta.bundle';path.write_bytes(b'bundle fixture boundary')
            with patch('UnityPy.load', return_value=SimpleNamespace(container=ContainerHelper({ASSET_PATH:pointer}))):
                self.assertIs(_read_tree([path]), self.tree)
            # A matching object name under a different asset path is insufficient.
            with patch('UnityPy.load', return_value=SimpleNamespace(container=ContainerHelper({'PreProd/Group00_VO_SO.asset':pointer}))):
                with self.assertRaisesRegex(ValueError, 'unique production'):
                    _read_tree([path])

    def test_corrupt_cached_manager_is_not_accepted_offline(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            config, main, ho, responses = self.setup_discovery(folder)
            self.discover(folder, config, main, ho, Client(responses))
            path = folder/'item_managers'/cache_name(PREFIX+self.future[0]+'.json', '.json')
            path.write_bytes(b'{}')
            offline = Client({})
            extended, report = self.discover(folder, config, main, ho, offline, True)
            self.assertEqual(offline.calls, [])
            self.assertEqual(extended[6].item_manager_url, '')
            self.assertEqual(extended[7].order, 8)
            self.assertEqual(report['unverified_scenes'][0]['scene_id'], self.future[0])

    def script_dependency(self, main):
        name = 'a'*32 + '_monoscripts_' + 'b'*32 + '.bundle'
        script = location('local-script', name, RUNTIME_PROPERTY+'/WebGL/6000.4.1f1/'+name, bundle=True)
        script.url = None;script.url_error = RUNTIME_ERROR
        main[1].dependencies.append(name);main.append(script)
        return script

    def test_explicit_local_monoscripts_dependency_is_reported_and_not_downloaded(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            config, main, ho, responses = self.setup_discovery(folder)
            script = self.script_dependency(main)
            client = Client(responses)
            extended, report = self.discover(folder, config, main, ho, client)
            self.assertEqual(report['status'], 'available')
            self.assertEqual(len(extended), 12)
            self.assertEqual(report['omitted_runtime_script_bundles'][0]['internal_id'], script.internal_id)
            self.assertNotIn(script.internal_id, client.calls)
            self.assertEqual(len(report['source_bundles']), 1)

    def test_other_unresolved_bundle_with_identical_error_is_not_silenced(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            config, main, ho, responses = self.setup_discovery(folder)
            self.script_dependency(main)
            other = location('unresolved', 'other.bundle', RUNTIME_PROPERTY+'/WebGL/6000.4.1f1/other.bundle', bundle=True)
            other.url = None;other.url_error = RUNTIME_ERROR
            main[1].dependencies.append('other.bundle');main.append(other)
            extended, report = self.discover(folder, config, main, ho, Client(responses))
            self.assertEqual(extended, self.scenes)
            self.assertEqual(report['status'], 'unavailable')
            self.assertIn(RUNTIME_ERROR, report['errors'][0])

    def test_missing_dependency_under_local_scripts_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            config, main, ho, responses = self.setup_discovery(folder)
            script = self.script_dependency(main)
            script.dependencies.append('missing.bundle')
            extended, report = self.discover(folder, config, main, ho, Client(responses))
            self.assertEqual(extended, self.scenes)
            self.assertEqual(report['status'], 'unavailable')
            self.assertIn('Missing dependency', report['errors'][0])


if __name__ == '__main__':
    unittest.main()
