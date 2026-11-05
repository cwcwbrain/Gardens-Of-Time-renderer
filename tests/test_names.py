"""Official title joins preserve localization and never infer display names."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gardens.download import DownloadError, atomic_json, cache_name, sha256
from gardens.names import CATALOG_ASSETS_KEY, CATALOG_ASSETS_PATH, LOCALIZATION_KEYS, build_name_map, load_official_names
from gardens.scenes import Scene


PREFIX = 'https://prod-cdn-assets.gardensoftime-cloud.net/catalog/xml/en_us/text_manager_items_'
ROOT = 'https://prod-cdn-assets.gardensoftime-cloud.net/catalog/'


def naming_fixture():
    """Small source-derived examples; bonus titles intentionally disagree."""
    source = {
        'sceneVOs': [
            {'sid':'2091','name':'AncientGizaRiverfront'},
            {'sid':'study','name':'VictorianSceneRedone'},
            {'sid':'manor','name':'VictorianExteriorRedux1'},
            {'sid':'405399','name':'RideOfTheValkyries'},
            {'sid':'2887953','name':'ch748CourageandRemembranceVeteransBreakfastDiner'},
        ],
        'missionVOs': [
            {'mid':'15','name':'egypt_giza_riverfront','scene_id':'2091','parent_stid':'434','sort_order':'1'},
            {'mid':'study','name':'london_study','scene_id':'study','parent_stid':'434','sort_order':'2'},
            {'mid':'manor','name':'london_time_manor','scene_id':'manor','parent_stid':'434','sort_order':'3'},
            {'mid':'1458','name':'norsemythology2_ride_of_the_valkyries','scene_id':'405399','parent_stid':'87765','sort_order':'3'},
            {'mid':'1583','name':'norsemythology2_ride_of_the_valkyries_flashlight','scene_id':'405399','parent_stid':'94449','sort_order':'4'},
            {'mid':'9714','name':'ch748_courage_and_remembrance_veterans_breakfast_diner','scene_id':'2887953','parent_stid':'792303','sort_order':'1'},
            {'mid':'bonus','name':'victorian_manor_exterior_spinner','scene_id':'manor','parent_stid':'bonus','sort_order':'1'},
        ],
        'storyVOs': [
            {'stid':'434','name':'loc_01_babylon','disposition':'1','mission_ids':'manor,15,study','display_order':'2'},
            {'stid':'87765','name':'chapter_107','disposition':'1','mission_ids':'1458','display_order':'110'},
            {'stid':'94449','name':'chapter_117','disposition':'1','mission_ids':'1583','display_order':'120'},
            {'stid':'792303','name':'chapter_748','disposition':'1','mission_ids':'9714','display_order':'751'},
            {'stid':'bonus','name':'ss_craft_map110','disposition':'10','mission_ids':'bonus','display_order':'0'},
        ],
    }
    scene_text = {name:{'title':title} for name,title in [
        ('AncientGizaRiverfront','Ancient Giza - Riverfront'),
        ('VictorianSceneRedone','Victorian Scene'),
        ('VictorianExteriorRedux1','Victorian Manor Exterior'),
        ('RideOfTheValkyries','Ride of the Valkyries'),
        ('ch748CourageandRemembranceVeteransBreakfastDiner','Veterans Breakfast Diner')]}
    mission_text = {key:{'title':title} for key,title in [
        ('egypt_giza_riverfront','Giza Riverfront'),('london_study','Hourglass Study'),
        ('london_time_manor','Time Manor'),
        ('norsemythology2_ride_of_the_valkyries','Ride of the Valkyries'),
        ('norsemythology2_ride_of_the_valkyries_flashlight','Ride of the Valkyries Flashlight'),
        ('ch748_courage_and_remembrance_veterans_breakfast_diner','Veterans Breakfast Diner'),
        ('victorian_manor_exterior_spinner','Victorian Manor Exterior')]}
    story_text = {key:{'title':title} for key,title in [
        ('loc_01_babylon','Chapter 1 - Trouble at Time Manor'),
        ('chapter_107','Chapter 107 - May the Norse be with You'),
        ('chapter_117','Chapter 117 - Time Travelers of the Lost Ark'),
        ('chapter_748','Chapter 748 - Hometown Heroes'),
        ('ss_craft_map110','Time Mechanism Missions')]}
    return source, scene_text, mission_text, story_text


class Client:
    def __init__(self, responses): self.responses=responses;self.calls=[]

    def fetch(self,url,path,validator,refresh=False):
        self.calls.append((url,refresh))
        raw=self.responses[url]
        if isinstance(raw,Exception):raise raw
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw);validator(path)
        atomic_json(str(path)+'.verified.json',{'url':url,'sha256':sha256(path)})


class NameTests(unittest.TestCase):
    def setUp(self):
        self.tree,self.scene_text,self.mission_text,self.story_text=naming_fixture()
        self.scenes=[Scene(i+1,s['name'],'',PREFIX+s['name']+'.json',[]) for i,s in enumerate(self.tree['sceneVOs'])]

    def build(self):return build_name_map(self.scenes,self.tree,self.scene_text,self.mission_text,self.story_text)

    def test_official_chapter_titles_differ_from_scene_asset_titles(self):
        report=self.build();self.assertEqual(report['status'],'available')
        self.assertEqual(report['summary']['mapped_scenes'],5)
        expected={'AncientGizaRiverfront':('Giza Riverfront','Ancient Giza - Riverfront'),
                  'VictorianSceneRedone':('Hourglass Study','Victorian Scene'),
                  'VictorianExteriorRedux1':('Time Manor','Victorian Manor Exterior')}
        for sid,titles in expected.items():
            entry=report['mappings'][sid]
            self.assertEqual((entry['title'],entry['scene_title']),titles)
            self.assertEqual(entry['chapter_title'],'Chapter 1 - Trouble at Time Manor')
        modern=report['mappings'][self.scenes[-1].scene_id]
        self.assertEqual(modern['title'],'Veterans Breakfast Diner')
        self.assertEqual(modern['chapter_title'],'Chapter 748 - Hometown Heroes')

    def test_first_primary_replay_name_is_retained_and_later_alias_is_recorded(self):
        entry=self.build()['mappings']['RideOfTheValkyries']
        self.assertEqual(entry['title'],'Ride of the Valkyries')
        self.assertEqual(entry['chapter_number'],107)
        self.assertEqual([c['title'] for c in entry['primary_appearances']],['Ride of the Valkyries','Ride of the Valkyries Flashlight'])

    def test_membership_not_arbitrary_parent_join_and_preserved_input_order(self):
        self.scenes=list(reversed(self.scenes))
        self.tree['missionVOs'].append({'mid':'unlisted','name':'not_shown','scene_id':'2091','parent_stid':'434','sort_order':'1'})
        self.mission_text['not_shown']={'title':'This title must not replace Giza'}
        mappings=self.build()['mappings']
        self.assertEqual(list(mappings),[scene.scene_id for scene in self.scenes])
        self.assertEqual(mappings['AncientGizaRiverfront']['title'],'Giza Riverfront')
        self.assertEqual(mappings['AncientGizaRiverfront']['mission_order'],1)

    def test_missing_localization_affects_only_its_scene_and_does_not_use_later_alias(self):
        del self.mission_text['norsemythology2_ride_of_the_valkyries']
        report=self.build()
        self.assertEqual(report['status'],'partial');self.assertEqual(len(report['mappings']),4)
        self.assertNotIn('RideOfTheValkyries',report['mappings'])
        self.assertEqual(report['issues'][0]['scene_id'],'RideOfTheValkyries')

    def test_conflicting_earliest_primary_titles_are_unmapped(self):
        self.tree['storyVOs'].append({'stid':'duplicate','name':'other_chapter_1','disposition':'1','mission_ids':'duplicate','display_order':'2'})
        self.story_text['other_chapter_1']={'title':'Chapter 1 - An alternative caption'}
        self.tree['missionVOs'].append({'mid':'duplicate','name':'different_giza','scene_id':'2091','parent_stid':'duplicate','sort_order':'1'})
        self.mission_text['different_giza']={'title':'Conflicting Giza title'}
        report=self.build()
        self.assertNotIn('AncientGizaRiverfront',report['mappings'])
        self.assertEqual(len(report['mappings']),4)
        self.assertTrue(any('Ambiguous earliest' in issue for issue in report['issues'][0]['issues']))

    def test_exact_localized_punctuation_is_not_sanitized_by_name_reader(self):
        self.mission_text['egypt_giza_riverfront']['title']='What lies Within?'
        self.assertEqual(self.build()['mappings']['AncientGizaRiverfront']['title'],'What lies Within?')

    def test_unknown_scene_and_duplicate_scene_metadata_have_no_guessed_name(self):
        self.scenes.append(Scene(6,'UnknownCamelCaseScene','','',[]))
        self.tree['sceneVOs'].append({'sid':'duplicate','name':'AncientGizaRiverfront'})
        report=self.build()
        self.assertNotIn('UnknownCamelCaseScene',report['mappings'])
        self.assertNotIn('AncientGizaRiverfront',report['mappings'])
        self.assertEqual(report['summary']['unmapped_scenes'],2)

    def load(self, folder, client, offline=False, assets=None):
        inventory={'catalogAssetsVOs':[{'k':key,'v':17995}for key in LOCALIZATION_KEYS.values()]}
        def asset(out,client,config,key,path,offline=False,refresh_catalogue=True):
            if key==CATALOG_ASSETS_KEY:
                self.assertEqual(path,CATALOG_ASSETS_PATH)
                self.assertFalse(refresh_catalogue)
                return assets if assets is not None else inventory,{'source_asset':path,'sha256':'catalogasset-proof'}
            return self.tree,{'source_asset':path,'sha256':'chapter-proof'}
        with patch('gardens.names.load_metadata_asset',side_effect=asset):
            return load_official_names(folder,client,'config.json',self.scenes,offline)

    def responses(self):
        values={'scenes':self.scene_text,'missions':self.mission_text,'stories':self.story_text}
        return {ROOT+key:json.dumps(values[kind]).encode()for kind,key in LOCALIZATION_KEYS.items()}

    def test_explicit_resource_paths_fetch_online_and_verify_offline_hashes(self):
        with tempfile.TemporaryDirectory()as td:
            client=Client(self.responses());report=self.load(td,client)
            self.assertEqual(report['status'],'available');self.assertEqual(len(client.calls),3)
            self.assertTrue(all(refresh for _,refresh in client.calls))
            self.assertEqual(report['provenance']['localizations']['missions']['catalog_assets_key'],LOCALIZATION_KEYS['missions'])
            offline=Client({});cached=self.load(td,offline,True)
            self.assertEqual(cached['mappings'],report['mappings']);self.assertEqual(offline.calls,[])
            path=Path(td)/'naming/localization'/cache_name(ROOT+LOCALIZATION_KEYS['missions'],'.json')
            path.write_text('{"modified":{"title":"Forged"}}')
            corrupt=self.load(td,offline,True)
            self.assertEqual(corrupt['status'],'unavailable');self.assertEqual(corrupt['mappings'],{})

    def test_online_mutable_localization_is_refreshed_not_kept_forever(self):
        with tempfile.TemporaryDirectory()as td:
            self.load(td,Client(self.responses()))
            self.mission_text['egypt_giza_riverfront']['title']='Updated official title'
            report=self.load(td,Client(self.responses()))
            self.assertEqual(report['mappings']['AncientGizaRiverfront']['title'],'Updated official title')

    def test_missing_explicit_resource_or_transfer_failure_never_guesses_fallback(self):
        with tempfile.TemporaryDirectory()as td:
            missing=self.load(td,Client({}),assets={'catalogAssetsVOs':[]})
            self.assertEqual(missing['status'],'unavailable');self.assertEqual(missing['mappings'],{})
            responses=self.responses();responses[ROOT+LOCALIZATION_KEYS['missions']]=DownloadError('HTTP blocked')
            blocked=self.load(td,Client(responses))
            self.assertEqual(blocked['status'],'unavailable');self.assertEqual(blocked['mappings'],{})
            self.assertIn('blocked',blocked['source_issues'][0])


if __name__=='__main__':unittest.main()
