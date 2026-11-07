"""Generic coherent-pose selection using source alpha, without scene exceptions."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from gardens.gaf import Movie,Timeline,decode
from gardens.nested_pose import POSE_SELECTION_VERSION,select_pose

ROOT=Path(__file__).resolve().parents[1]
FIXTURES=Path(__file__).parent/'fixtures'


def state(oid,matrix=(1,0,0,1,0,0),depth=0,alpha=1):
    return {'object_id':oid,'matrix':matrix,'depth':depth,'alpha':alpha,'mask':None,'color':None,'effects':[]}


def synthetic(host_id=905,patch_id=37,tid=611):
    timeline=Timeline(tid,3,(0,0,40,20),(0,0),'')
    timeline.objects={host_id:{'element':1,'type':0},patch_id:{'element':2,'type':0}}
    timeline.frames={1:[state(host_id),state(patch_id,(.75,0,0,.75,5,5),depth=1)],
                     2:[state(patch_id,(1,0,0,1,4,4),depth=1)],
                     3:[state(patch_id,(1.25,0,0,1.25,3,3),depth=1)]}
    texture=Image.new('RGBA',(40,20));texture.paste((90,80,70,255),(0,0,16,16));texture.paste((0,0,0,0),(4,4,12,12))
    texture.paste((90,80,70,255),(20,0,28,8))
    elements={i:{'atlas_id':1,'origin':xy,'size':size,'pivot':(0,0),'scale':1,'scale_y':1,'scale9':None}
              for i,xy,size in ((1,(0,0),(16,16)),(2,(20,0),(8,8)))}
    scope={'timeline':timeline,'atlas':{'elements':elements},'textures':{1:('synthetic',texture)},
           'metadata':{host_id:{'m_AtlasInitialElementID':1},patch_id:{'m_AtlasInitialElementID':2}}}
    movie=Movie((5,9),(1,),(1,),timelines={tid:timeline})
    return movie,timeline,scope,host_id,patch_id


class GenericNestedPoseTests(unittest.TestCase):
    def test_generic_frame_is_chosen_without_names_ids_or_source_hashes(self):
        for host,part,tid in ((905,37,611),(4,8001,23)):
            with self.subTest(host=host,part=part,tid=tid):
                movie,timeline,scope,_,_=synthetic(host,part,tid)
                before=copy.deepcopy(timeline.__dict__);pixels=scope['textures'][1][1].tobytes()
                result=select_pose(movie,tid,lambda n:scope)
                self.assertEqual(result['version'],POSE_SELECTION_VERSION)
                self.assertEqual(result['frame'],2)
                self.assertTrue(result['applied'])
                self.assertGreater(result['initial_deficit'],result['best_deficit'])
                self.assertEqual(result['best_deficit'],0)
                self.assertEqual(timeline.__dict__,before)
                self.assertEqual(scope['textures'][1][1].tobytes(),pixels)

    def test_initially_named_parts_cannot_disappear_or_become_less_opaque(self):
        for replacement in (0,.5):
            with self.subTest(alpha=replacement):
                movie,timeline,scope,host,part=synthetic()
                timeline.objects[721]={'element':2,'type':0};scope['metadata'][721]={'m_AtlasInitialElementID':2}
                timeline.names[721]='A retained named part'
                timeline.frames[1].append(state(721,(1,0,0,1,30,10),depth=2))
                timeline.frames[2].append(state(721,(1,0,0,1,30,10),depth=2,alpha=replacement))
                timeline.frames[3]=[state(part,(1,0,0,1,4,4),depth=1,alpha=.9),state(721,(1,0,0,1,30,10),depth=2)]
                result=select_pose(movie,timeline.id,lambda n:scope)
                self.assertEqual(result['frame'],3)
                self.assertEqual(result['eligible_candidate_frames'],2)

    def test_a_future_duplicate_draw_depth_is_not_a_candidate(self):
        movie,timeline,scope,_,part=synthetic()
        timeline.frames[2][0]['depth']=0
        timeline.frames[3]=[state(part,(1,0,0,1,4,4),depth=1,alpha=.9)]
        result=select_pose(movie,timeline.id,lambda n:scope)
        self.assertEqual(result['frame'],3)

    def test_masks_actions_effects_and_nonleaf_features_keep_the_initial_pose(self):
        for feature in ('mask','mask_reference','actions','effect','color','nested','text'):
            with self.subTest(feature=feature):
                movie,timeline,scope,host,part=synthetic()
                if feature=='mask':timeline.masks.add(host)
                elif feature=='mask_reference':timeline.frames[2][0]['mask']=host
                elif feature=='actions':timeline.actions={3:[{'type':0,'scope':'','params':[]}]}
                elif feature=='effect':timeline.frames[2][0]['effects']=[{'type':1,'values':(2,2)}]
                elif feature=='color':timeline.frames[2][0]['color']=(0,1,.1,1,0,1,0)
                else:timeline.objects[part]['type']=2 if feature=='nested' else 1
                result=select_pose(movie,timeline.id,lambda n:scope)
                self.assertEqual(result['frame'],1);self.assertFalse(result['applied'])

    def test_non_enclosed_transparency_and_small_incidental_fill_do_not_select(self):
        for feature in ('open','small_fill'):
            with self.subTest(feature=feature):
                movie,timeline,scope,_,part=synthetic();texture=scope['textures'][1][1]
                if feature=='open':texture.paste((0,0,0,0),(0,4,8,5))
                else:
                    scope['atlas']['elements'][2]['size']=(2,2)
                    timeline.frames[1][1]['matrix']=(1,0,0,1,5,5)
                result=select_pose(movie,timeline.id,lambda n:scope)
                self.assertEqual(result['frame'],1)

    def test_budget_exhaustion_never_returns_a_partial_search_result(self):
        for limit in ('MAX_FRAMES','MAX_STATE_ENTRIES','MAX_TEXTURE_PIXELS','MAX_HOST_PIXELS',
                      'MAX_SCANNED_HOST_PIXELS','MAX_COMPONENT_PIXELS','MAX_ROI_PIXELS',
                      'MAX_TOTAL_ROI_PIXELS','MAX_CACHED_WARP_PIXELS','MAX_SCORE_PIXEL_WORK'):
            with self.subTest(limit=limit):
                movie,timeline,scope,_,_=synthetic()
                with patch('gardens.nested_pose.'+limit,1):
                    result=select_pose(movie,timeline.id,lambda n:scope)
                self.assertEqual(result['frame'],1)
                self.assertFalse(result['applied'])
                self.assertIn('Resource budget exceeded',result['reason'])

    def test_future_custom_geometry_declines_analysis_without_changing_the_source(self):
        movie,timeline,scope,_,_=synthetic();timeline.objects[788]={'element':2,'type':0}
        scope['metadata'][788]={'m_AtlasInitialElementID':2,'m_UseCustomAtlasTextureRect':1}
        timeline.frames[2].append(state(788,(1,0,0,1,6,6),depth=2))
        original=copy.deepcopy(timeline.__dict__)
        result=select_pose(movie,timeline.id,lambda n:scope)
        self.assertEqual(result['frame'],1)
        self.assertIn('declined unsupported source data',result['reason'])
        self.assertEqual(timeline.__dict__,original)

    def test_equal_or_sufficient_initial_coverage_keeps_frame_one(self):
        movie,timeline,scope,_,part=synthetic()
        timeline.frames[1][1]['matrix']=(1,0,0,1,4,4)
        result=select_pose(movie,timeline.id,lambda n:scope)
        self.assertEqual(result['frame'],1);self.assertFalse(result['applied'])


class SourceNestedPoseTests(unittest.TestCase):
    def test_real_highland_cutout_selects_a_coherent_pose_from_alpha_only(self):
        movie=decode((FIXTURES/'HighlandGames.gaf').read_bytes());timeline=movie.timelines[97]
        atlas=next(a for a in movie.atlases if all(d['element'] in a['elements'] for d in timeline.objects.values()))
        metadata={int(k):v for k,v in json.loads((FIXTURES/'HighlandGames_static.json').read_text())['child_metadata'].items()}
        alpha=Image.open(FIXTURES/'HighlandGames_pose_alpha.png').convert('L')
        texture=Image.new('RGBA',alpha.size);texture.putalpha(alpha)
        scope={'timeline':timeline,'atlas':atlas,'textures':{1:('HighlandGames',texture)},'metadata':metadata}
        before=copy.deepcopy(timeline.__dict__);result=select_pose(movie,97,lambda tid:scope)
        self.assertEqual(result['frame'],20)
        self.assertEqual(result['encoded_frames_checked'],160)
        self.assertEqual(result['initial_deficit'],6915)
        self.assertEqual(result['best_deficit'],22)
        self.assertGreater(result['relative_improvement'],.99)
        self.assertEqual(result['regions'][0]['stationary_object_id'],190)
        self.assertEqual(timeline.__dict__,before)

    def test_existing_real_giza_and_victorian_poses_remain_initial(self):
        from gardens.render import load_scene
        for bundle in sorted((ROOT/'samples').glob('*.bundle')):
            movie,root,atlas,textures,metadata,scope=load_scene(bundle,True)
            for tid in movie.timelines:
                with self.subTest(bundle=bundle.name,timeline=tid):
                    self.assertEqual(select_pose(movie,tid,scope)['frame'],1)


if __name__=='__main__':unittest.main()
