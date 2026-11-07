"""Future name lookup enumerates primary metadata without bulk Manager fetches."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gardens.download import atomic_json, sha256
from gardens.names import CATALOG_ASSETS_KEY, LOCALIZATION_KEYS, build_name_map, load_official_names, primary_roster
from gardens.scenes import Scene


PREFIX = 'https://prod-cdn-assets.gardensoftime-cloud.net/catalog/xml/en_us/text_manager_items_'
ROOT = 'https://prod-cdn-assets.gardensoftime-cloud.net/catalog/'
FIXTURE = Path(__file__).parent/'fixtures/chapter747_748.json'


class Client:
    def __init__(self, responses): self.responses=responses;self.calls=[]

    def fetch(self,url,path,validator,refresh=False):
        if 'text_manager_items_' in url:
            raise AssertionError('Name enumeration must not fetch Item Managers')
        self.calls.append(url)
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(self.responses[url]);validator(path)
        atomic_json(str(path)+'.verified.json',{'url':url,'sha256':sha256(path)})


class FinderPrimaryRosterTests(unittest.TestCase):
    def setUp(self):
        self.tree=json.loads(FIXTURE.read_text())
        self.base=[Scene(i+1,row['name'],'original thumbnail',PREFIX+row['name']+'.json',['original row',str(i)])
                   for i,row in enumerate(self.tree['sceneVOs'][:6])]
        self.future=[row['name']for row in self.tree['sceneVOs'][6:]]

    def test_real_future_primary_roster_keeps_base_rows_and_uses_blank_urls(self):
        extended,report=primary_roster(self.base,self.tree)
        self.assertEqual(report['status'],'available')
        self.assertEqual(report['base_scene_count'],6)
        self.assertEqual(report['primary_scene_count'],12)
        self.assertEqual(report['added_scene_count'],6)
        self.assertEqual([s.scene_id for s in extended[6:]],self.future)
        self.assertTrue(all(a is b for a,b in zip(extended,self.base)))
        self.assertTrue(all(not s.item_manager_url and not s.thumbnail_url for s in extended[6:]))
        self.assertEqual([s.order for s in extended],list(range(1,13)))

    def test_finder_has_no_chapter_or_mission_contiguity_requirement(self):
        self.tree['storyVOs'][1]['name']='chapter_750'
        self.tree['storyVOs'][1]['display_order']='753'
        self.tree['storyVOs'][1]['mission_ids']=','.join(reversed(self.tree['storyVOs'][1]['mission_ids'].split(',')))
        for row in self.tree['missionVOs'][6:]:row['sort_order']=str(int(row['sort_order'])+10)
        extended,report=primary_roster(self.base,self.tree)
        self.assertEqual(report['status'],'available')
        self.assertEqual([s.scene_id for s in extended[6:]],self.future)

    def test_orphan_and_bonus_only_scene_metadata_is_not_enrolled(self):
        self.tree['sceneVOs'] += [{'sid':'orphan','name':'OrphanScene'}, {'sid':'bonus','name':'BonusOnlyScene'}]
        self.tree['missionVOs'].append({'mid':'bonus','scene_id':'bonus','parent_stid':'bonus','name':'bonus','sort_order':'1'})
        self.tree['storyVOs'].append({'stid':'bonus','name':'bonus','disposition':'10','mission_ids':'bonus','display_order':'0'})
        extended,report=primary_roster(self.base,self.tree)
        self.assertEqual(len(extended),12)
        self.assertEqual(report['primary_scene_count'],12)
        self.assertNotIn('BonusOnlyScene',[s.scene_id for s in extended])
        self.assertNotIn('OrphanScene',[s.scene_id for s in extended])

    def test_replay_dedup_and_existing_future_row_preserve_original_identity(self):
        existing=Scene(50,self.future[3],'custom thumbnail','https://custom.test/keep-exactly.json',['untouched'])
        base=self.base+[existing]
        self.tree['missionVOs'][6]['scene_id']=self.tree['sceneVOs'][0]['sid']
        extended,report=primary_roster(base,self.tree)
        self.assertEqual(extended[:7],base)
        self.assertIs(extended[6],existing)
        self.assertEqual(report['added_scene_count'],4)
        self.assertEqual([s.order for s in extended[7:]],[51,52,53,54])
        self.assertEqual(len({s.scene_id.casefold()for s in extended}),len(extended))
        self.assertEqual(report['skipped_replays'][0]['scene_id'],self.base[0].scene_id)

    def test_malformed_or_ambiguous_new_primary_records_are_not_guessed(self):
        for kind in ('missing_mission','wrong_parent','missing_scene','unsafe_name','duplicate_order','duplicate_display','duplicate_scene_id','case_collision'):
            tree=copy.deepcopy(self.tree)
            if kind=='missing_mission':tree['storyVOs'][1]['mission_ids']+='missing'
            elif kind=='wrong_parent':tree['missionVOs'][6]['parent_stid']='wrong'
            elif kind=='missing_scene':tree['missionVOs'][6]['scene_id']='missing'
            elif kind=='unsafe_name':tree['sceneVOs'][6]['name']='../unsafe'
            elif kind=='duplicate_order':tree['missionVOs'][6]['sort_order']='2'
            elif kind=='duplicate_display':tree['storyVOs'].append({**tree['storyVOs'][1],'stid':'other'})
            elif kind=='duplicate_scene_id':tree['sceneVOs'].append(tree['sceneVOs'][6])
            else:tree['sceneVOs'].append({'sid':'case-collision','name':self.future[0].upper()})
            with self.subTest(kind=kind):
                extended,report=primary_roster(self.base,tree)
                self.assertEqual(report['status'],'partial')
                self.assertEqual(extended,self.base)
                self.assertEqual(report['added_scene_count'],0)
                self.assertTrue(report['issues']or report['source_issues'])

    def texts(self):
        scene_text={row['name']:{'title':'Scene title '+row['name']}for row in self.tree['sceneVOs']}
        mission_text={row['name']:{'title':'Mission title '+row['name']}for row in self.tree['missionVOs']}
        story_text={row['name']:{'title':'Chapter '+str(i+747)+' - Official chapter'}for i,row in enumerate(self.tree['storyVOs'])}
        return scene_text,mission_text,story_text

    def load(self,out,client,include=False,offline=False):
        calls=[]
        inventory={'catalogAssetsVOs':[{'k':key,'v':1}for key in LOCALIZATION_KEYS.values()]}
        def metadata(out,client,config,key,path,offline=False,refresh_catalogue=True):
            calls.append(key)
            return (inventory if key==CATALOG_ASSETS_KEY else self.tree),{'source_asset':path}
        with patch('gardens.names.load_metadata_asset',side_effect=metadata):
            report=load_official_names(out,client,'config.json',self.base,offline,include_all_primary=include)
        return report,calls

    def test_optional_load_discovers_future_names_with_single_group00_fetch_and_no_managers(self):
        scene_text,mission_text,story_text=self.texts()
        values=dict(zip(('scenes','missions','stories'),(scene_text,mission_text,story_text)))
        responses={ROOT+key:json.dumps(values[kind]).encode()for kind,key in LOCALIZATION_KEYS.items()}
        with tempfile.TemporaryDirectory()as td:
            default,default_calls=self.load(td,Client(responses))
            self.assertEqual(default['summary']['mapped_scenes'],6)
            self.assertNotIn('roster_summary',default)
            client=Client(responses)
            report,calls=self.load(td,client,True)
            self.assertEqual(report['summary']['mapped_scenes'],12)
            self.assertEqual(report['roster_summary']['added_scene_count'],6)
            self.assertEqual(calls.count('Prod/Group00_VO_SO'),1)
            self.assertEqual(len(calls),2)
            self.assertEqual(len(client.calls),3)
            self.assertIn(self.future[0],report['mappings'])
            cached,cached_calls=self.load(td,Client({}),True,True)
            self.assertEqual(cached['mappings'],report['mappings'])

    def test_primary_mission_mode_remains_distinct_from_scene_mode(self):
        self.tree['sceneVOs'][0]['gameplay_mode']='9'
        self.tree['missionVOs'][0]['default_mode']='1'
        entry=build_name_map(self.base,self.tree,*self.texts())['mappings'][self.base[0].scene_id]
        self.assertEqual(entry['primary_default_mode'],'1')
        self.assertEqual(entry['scene_gameplay_mode'],'9')


if __name__=='__main__':unittest.main()
