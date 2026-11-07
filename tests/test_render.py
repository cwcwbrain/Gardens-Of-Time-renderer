"""Real GAF/atlas/Item Manager regressions plus small geometric invariants."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import UnityPy
from PIL import Image, ImageChops

from gardens.annotations import annotate_solution, validate_callouts, visible_anchor
from gardens.gaf import GAFError, decode
from gardens.render import RenderError, check_snapshot_actions, is_background, parse_items, render_bundle, warp_sprite
from gardens.nested import NestedPainter, compose
from gardens.gaf import Movie, Timeline

ROOT=Path(__file__).resolve().parents[1]
GIZA=next((ROOT/'samples').glob('ancient*.bundle'))
VICTORIAN=next((ROOT/'samples').glob('victorian*.bundle'))
ITEMS=ROOT/'tests/fixtures/AncientGizaRiverfront_items.json'


def movie_at(path):
    env=UnityPy.load(path.read_bytes())
    trees=[o.read_typetree() for o in env.objects if o.type.name=='MonoBehaviour']
    return decode(bytes(next(t for t in trees if 'm_AssetData' in t)['m_AssetData']))


class GAFTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.movie=movie_at(GIZA)

    def test_real_stage_and_full_timeline(self):
        m=self.movie;tl=m.timelines[0]
        self.assertEqual(m.version,(5,9))
        self.assertEqual((m.stage['width'],m.stage['height']),(1280,758))
        self.assertEqual(len(tl.frames),448)
        self.assertEqual(len(tl.objects),84)
        self.assertEqual(tl.masks,{153})
        self.assertEqual(m.unsupported,[])

    def test_delta_visibility_and_alternating_cells(self):
        tl=self.movie.timelines[0]
        self.assertIn(154,tl.state_at(1))
        self.assertNotIn(162,tl.state_at(1))
        self.assertNotIn(154,tl.state_at(5))
        self.assertIn(162,tl.state_at(5))
        self.assertNotIn(164,tl.state_at(69))
        self.assertAlmostEqual(tl.state_at(70)[164]['alpha'],.66,places=5)
        self.assertEqual(tl.state_at(80)[164]['alpha'],1)
        self.assertEqual(tl.state_at(5)[162]['mask'],153)

    def test_exact_atlas_reference_and_pivot(self):
        atlas=self.movie.atlases[0]
        self.assertEqual(atlas['elements'][1]['origin'],(3.,3.))
        self.assertEqual(atlas['elements'][1]['size'],(1280.,758.))
        self.assertEqual(atlas['elements'][77]['atlas_id'],2)
        self.assertAlmostEqual(atlas['elements'][77]['scale'],.5851703286)
        self.assertEqual(atlas['sources'][2][0]['source'],'AncientGizaRiverfront_2.png')

    def test_nested_victorian_timelines_are_decoded(self):
        m=movie_at(VICTORIAN)
        self.assertEqual(len(m.timelines),9)
        self.assertEqual(sum(len(t.objects) for t in m.timelines.values()),98)
        self.assertEqual(m.timelines[13].state_at(1)[183]['mask'],182)
        self.assertIn(192,m.timelines[246].state_at(3))
        self.assertNotIn(192,m.timelines[246].state_at(1))

    def test_truncated_bad_version_and_frame_fail_closed(self):
        env=UnityPy.load(GIZA.read_bytes())
        raw=next(bytes(t['m_AssetData']) for o in env.objects if o.type.name=='MonoBehaviour' and 'm_AssetData' in (t:=o.read_typetree()))
        for broken in [raw[:-1],raw[:4]+b'\x09'+raw[5:],raw[:6]+b'\0'*4+raw[10:]]:
            with self.subTest(),self.assertRaises((GAFError,ValueError)):
                decode(broken)
        with self.assertRaises(GAFError): self.movie.timelines[0].state_at(449)

    def test_item_manager_exact_binding(self):
        data=json.loads(ITEMS.read_text())
        items,animations,issues=parse_items(data,self.movie.timelines[0].names)
        self.assertEqual(len(items),68)
        self.assertEqual(len(animations),5)
        self.assertEqual(issues,[])
        self.assertEqual(items[143]['label'],'Banana')
        bad,_,issues=parse_items({'ban_anc_giz_riv':{'beginner':'Wrong'}},self.movie.timelines[0].names)
        self.assertFalse(bad);self.assertTrue(issues)

    def test_affine_uses_texture_pivot_and_scale(self):
        tile=Image.new('RGBA',(2,2),(255,0,0,255))
        element={'pivot':(1,1),'scale':.5}
        state={'matrix':(1,0,0,1,4,4),'alpha':1,'color':None}
        image=warp_sprite(tile,element,state,(8,8))
        self.assertEqual(image.getbbox(),(2,2,6,6))
        self.assertEqual(image.getpixel((3,3)),(255,0,0,255))
        self.assertEqual(image.getpixel((7,7))[3],0)


class NestedTests(unittest.TestCase):
    def test_transform_composition(self):
        # Child point (1,2) -> (7,4); parent rotates and translates -> (6,27).
        matrix=compose((0,1,-1,0,10,20),(2,0,0,3,5,-2))
        a,b,c,d,x,y=matrix
        self.assertEqual((a+c*2+x,b+d*2+y),(6,27))

    def make_painter(self):
        from types import SimpleNamespace
        def state(oid,matrix,depth=0,alpha=1,mask=None):
            return {'object_id':oid,'matrix':matrix,'depth':depth,'alpha':alpha,'mask':mask,'effects':[],'color':None}
        a=Timeline(10,1,(0,0,8,8),(0,0),'')
        b=Timeline(20,1,(0,0,2,2),(0,0),'')
        a.objects={1:{'element':1,'type':0},3:{'element':3,'type':0},4:{'element':20,'type':2}}
        a.masks={3};b.objects={5:{'element':5,'type':0}}
        a.frames={1:[state(1,(1,0,0,1,0,0),mask=3),state(3,(2,0,0,4,0,0)),state(4,(1,0,0,1,3,0),depth=1,alpha=.5)]}
        b.frames={1:[state(5,(1,0,0,1,0,0))]}
        texture=Image.new('RGBA',(8,4))
        texture.paste((255,0,0,255),(0,0,4,4));texture.paste((255,255,255,255),(4,0,5,1));texture.paste((0,0,255,255),(6,0,8,2))
        elements={i:{'atlas_id':1,'origin':xy,'size':size,'pivot':(0,0),'scale':1,'scale9':None}
                  for i,xy,size in [(1,(0,0),(4,4)),(3,(4,0),(1,1)),(5,(6,0),(2,2))]}
        def scope(tid):
            tl={10:a,20:b}[tid]
            return {'timeline':tl,'atlas':{'elements':elements},'textures':{1:('test',texture)},
                    'metadata':{oid:{'m_AtlasInitialElementID':d['element']} for oid,d in tl.objects.items()}}
        return NestedPainter(SimpleNamespace(timelines={10:a,20:b}),scope,(8,8)),a,b

    def test_masks_alpha_and_grandchild_transform(self):
        painter,a,b=self.make_painter()
        result=painter.paint(10,(1,0,0,1,1,1),.5)
        self.assertEqual(result.getpixel((1,1)),(255,0,0,128))
        self.assertEqual(result.getpixel((3,1))[3],0)
        self.assertEqual(result.getpixel((4,1)),(0,0,255,64))
        self.assertEqual(result.getpixel((0,0))[3],0)
        mask=Image.new('L',(8,8),128)
        result=painter.paint(10,(1,0,0,1,1,1),.5,[mask])
        self.assertEqual(result.getpixel((1,1))[3],64)
        self.assertEqual(result.getpixel((4,1))[3],32)

    def test_parent_filters_cascade_to_descendant_sprites(self):
        painter,a,b=self.make_painter()
        matrix=(0,0,1,0,0, 0,1,0,0,0, 1,0,0,0,0, 0,0,0,1,0)
        result=painter.paint(10,effects=({'type':6,'values':matrix},))
        self.assertEqual(result.getpixel((0,0)),(0,0,255,255))
        # Grandchild blue becomes red too, with the encoded parent alpha.
        self.assertEqual(result.getpixel((3,0)),(255,0,0,128))

    def test_chained_masks_intersect_and_cycles_fail(self):
        painter,a,b=self.make_painter()
        a.objects[6]={'element':3,'type':0};a.masks.add(6)
        state=dict(a.frames[1][1],object_id=6,matrix=(1,0,0,4,0,0))
        a.frames[1].append(state);a.frames[1][1]['mask']=6
        result=painter.paint(10)
        self.assertEqual(result.getpixel((0,0))[3],255)
        self.assertEqual(result.getpixel((1,0))[3],0)
        state['mask']=3
        with self.assertRaisesRegex(RenderError,'cycle'):painter.paint(10)

    def test_cycles_and_unsupported_actions_fail(self):
        painter,a,b=self.make_painter()
        b.objects[5]={'element':10,'type':2}
        with self.assertRaisesRegex(RenderError,'cycle'):painter.paint(10)
        a.actions={1:[{'type':3,'scope':'','params':['2']}]}
        with self.assertRaisesRegex(RenderError,'Unsupported action'):check_snapshot_actions(a,1)
        a.actions={1:[{'type':0,'scope':'','params':[]}]}
        check_snapshot_actions(a,1)
        with self.assertRaises(RenderError):check_snapshot_actions(a,2)

    def test_case_variants_remain_exact_and_collisions_stay_unresolved(self):
        items,_,issues=parse_items({'SchoolUniform_scene':{'beginner':'School Uniform'}},{1:'schooluniform'})
        self.assertEqual(issues,[]);self.assertEqual(items[1]['manager_key'],'SchoolUniform_scene')
        items,_,issues=parse_items({'HAT_scene':{'beginner':'Hat'}},{1:'hat',2:'Hat'})
        self.assertFalse(items);self.assertTrue(issues)
        self.assertTrue(is_background('bg01'));self.assertTrue(is_background('BG_02'))
        self.assertTrue(is_background('bg_background'));self.assertFalse(is_background('bag_object'))


class AnnotationTests(unittest.TestCase):
    def test_anchor_is_not_a_transparent_bbox_centre(self):
        mask=Image.new('L',(9,9))
        mask.putpixel((1,1),255);mask.putpixel((7,7),255)
        anchor=visible_anchor(mask)
        self.assertEqual(mask.getpixel(tuple(anchor)),255)
        self.assertNotEqual(anchor,[4,4])

    def test_very_long_labels_wrap_without_renaming_and_do_not_overlap(self):
        markers=[{'object_id':i,'label':'A Very Long Item Manager Label '*4,'anchor':[4,4],
                  'anchor_alpha':255,'bbox':[3,3,6,6]} for i in range(6)]
        image,box=annotate_solution(Image.new('RGBA',(16,16),'white'),markers)
        self.assertEqual(validate_callouts(markers,image.size,box),[])
        self.assertTrue(all(len(m['label_lines'])>1 for m in markers))
        self.assertTrue(all(m['label']=='A Very Long Item Manager Label '*4 for m in markers))

    def test_unverified_callouts_are_distinct_without_renumbering_verified_clues(self):
        verified=[{'object_id':i,'label':label,'anchor':[4+i,4],
                   'anchor_alpha':255,'bbox':[3,3,8,6]}
                  for i,label in enumerate(('Banana','Zebra'),1)]
        annotate_solution(Image.new('RGBA',(16,16),'white'),verified)
        numbers={m['object_id']:m['number'] for m in verified}
        unknown={'object_id':3,'label':'aardvark [unverified]','label_verified':False,
                 'anchor':[6,4],'anchor_alpha':255,'bbox':[3,3,8,6]}
        markers=verified+[unknown]
        image,box=annotate_solution(Image.new('RGBA',(16,16),'white'),markers)
        self.assertEqual(validate_callouts(markers,image.size,box),[])
        self.assertEqual({m['object_id']:m['number'] for m in verified},numbers)
        self.assertEqual(unknown['number'],3)
        self.assertEqual(unknown['callout_color'],[255,165,70,255])
        self.assertTrue(all(m['callout_color']==[79,225,255,255] for m in verified))
        self.assertEqual(image.getpixel(tuple(unknown['callout_box'][:2])),(19,25,33,255))
        x,y,_,_=unknown['callout_box']
        self.assertEqual(image.getpixel((x+8,y)),tuple(unknown['callout_color']))


class RealRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.out=Path(cls.temp.name)/'giza'
        cls.data=json.loads(ITEMS.read_text())
        source=Path(cls.temp.name)/'giza.bundle'
        shutil.copyfile(GIZA,source)
        cls.report=render_bundle(source,cls.data,cls.out)
        # Windows must be able to release the source immediately as well.
        source.unlink()

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_real_three_outputs_and_visible_object_coverage(self):
        r=self.report
        self.assertEqual(r['status'],'validated_snapshot',r['issues'])
        self.assertEqual(r['snapshot_frame'],80)
        self.assertEqual(r['decoded_frames'],448)
        self.assertEqual(r['identified_hidden_objects'],68)
        self.assertEqual(r['solution_markers'],68)
        self.assertEqual(r['unverified_named_pointers'],0)
        self.assertEqual(r['total_named_pointers'],68)
        self.assertEqual(len(r['atlases']),2)
        self.assertEqual(r['duplicate_backgrounds'],0)
        for name in ('scene_empty.png','scene_complete.png','scene_solution.png'):
            with Image.open(self.out/name) as im:
                self.assertEqual(im.size,tuple(r['solution_dimensions']) if name=='scene_solution.png' else (1280,758))
                self.assertEqual(im.getchannel('A').getextrema(),(255,255))

    def test_empty_removes_objects_solution_marks_visible_targets(self):
        with Image.open(self.out/'scene_empty.png') as a,Image.open(self.out/'scene_complete.png') as b,Image.open(self.out/'scene_solution.png') as c:
            self.assertIsNotNone(ImageChops.difference(a,b).convert('RGB').getbbox())
            self.assertIsNotNone(ImageChops.difference(b,c.crop(self.report['solution_scene_box'])).convert('RGB').getbbox())
            self.assertEqual(a.getpixel((700,20)),b.getpixel((700,20)))
        markers=json.loads((self.out/'solution_objects.json').read_text())
        self.assertEqual(len({m['object_id'] for m in markers}),68)
        self.assertTrue(all(m['visible_pixels']>0 and m['label'] for m in markers))

    def test_named_pointers_match_item_manager_and_have_separate_label_boxes(self):
        markers=json.loads((self.out/'solution_objects.json').read_text())
        self.assertEqual(self.report['named_pointers'],68)
        self.assertEqual(validate_callouts(markers,self.report['solution_dimensions'],self.report['solution_scene_box']),[])
        for marker in markers:
            self.assertEqual(marker['label'],self.data[marker['manager_key']]['beginner'])
            self.assertGreaterEqual(marker['anchor_alpha'],16)
            self.assertEqual(marker['pointer_tip'],[marker['anchor'][0]+self.report['solution_scene_box'][0],
                                                   marker['anchor'][1]+self.report['solution_scene_box'][1]])
        self.assertEqual({m['number'] for m in markers},set(range(1,69)))

    def test_explicit_animation_snapshot_and_exclusions(self):
        self.assertEqual(self.report['inactive_at_snapshot'],[154,163])
        self.assertEqual(self.report['excluded_gameplay_objects'],[156,157,158,159,160])
        rows=json.loads((self.out/'objects.json').read_text());byid={r['object_id']:r for r in rows}
        self.assertEqual(byid[153]['status'],'mask_only')
        self.assertEqual(byid[164]['status'],'rendered')
        self.assertEqual(byid[161]['atlas_id'],2)
        self.assertEqual(byid[143]['manager_key'],'banana_anc_giz_riv')

    def test_nested_initial_pose_is_rendered(self):
        data=json.loads((ROOT/'tests/fixtures/VictorianExteriorRedux1_items.json').read_text())
        out=Path(self.temp.name)/'victorian'
        r=render_bundle(VICTORIAN,data,out)
        self.assertEqual(r['status'],'validated_snapshot',r['issues'])
        self.assertTrue((out/'scene_complete.png').exists())
        self.assertEqual(len(r['atlases']),2)
        self.assertEqual(r['named_pointers'],69)
        self.assertEqual(len(r['nested_snapshot_instances']),8)
        self.assertEqual({x['timeline_id'] for x in r['nested_snapshot_instances']},{6,13,19,25,28,246,251,18})
        self.assertTrue(all(x['frame']==1 for x in r['nested_snapshot_instances']))
        bird=next(x for x in r['nested_snapshot_instances'] if x['timeline_id']==18)
        self.assertEqual(bird['instance_path'],[103,184])
        self.assertAlmostEqual(bird['matrix'][4],723.1000061035156)

    def test_nested_failure_is_not_certified(self):
        from unittest.mock import patch
        data=json.loads((ROOT/'tests/fixtures/VictorianExteriorRedux1_items.json').read_text())
        out=Path(self.temp.name)/'victorian_unsupported'
        with patch('gardens.nested.NestedPainter.paint',side_effect=RenderError('Unsupported nested effect')):
            r=render_bundle(VICTORIAN,data,out)
        self.assertEqual(r['status'],'incomplete')
        self.assertIn('Unsupported nested effect',r['issues'])
        self.assertFalse((out/'scene_complete.png').exists())

    def test_unknown_named_visual_remains_a_blocker(self):
        data=dict(self.data);data.pop('banana_anc_giz_riv')
        out=Path(self.temp.name)/'unknown'
        out.mkdir();(out/'scene_complete.png').write_bytes(b'old output')
        r=render_bundle(GIZA,data,out)
        self.assertEqual(r['status'],'incomplete')
        self.assertTrue(any('banana' in issue and 'Unclassified' in issue for issue in r['issues']))
        self.assertFalse((out/'scene_complete.png').exists())
        self.assertTrue((out/'incomplete/scene_complete.png').exists())
        self.assertEqual(r['identified_hidden_objects'],67)
        self.assertEqual(r['named_pointers'],67)
        self.assertEqual(r['solution_markers'],67)
        self.assertEqual(r['unverified_named_pointers'],1)
        self.assertEqual(r['total_named_pointers'],68)
        markers=json.loads((out/'solution_objects.json').read_text())
        self.assertEqual(len(markers),68)
        banana=next(m for m in markers if m['object_id']==143)
        self.assertEqual(banana['label'],'banana [unverified]')
        self.assertEqual(banana['encoded_name'],'banana')
        self.assertEqual(banana['label_source'],'encoded_name')
        self.assertIs(banana['label_verified'],False)
        self.assertIsNone(banana['manager_key'])
        self.assertEqual(banana['labels'],{})
        self.assertEqual(banana['callout_color'],[255,165,70,255])
        self.assertGreaterEqual(banana['anchor_alpha'],16)
        self.assertEqual(validate_callouts(markers,r['solution_dimensions'],r['solution_scene_box']),[])
        for marker in markers:
            if marker['object_id']==143:continue
            self.assertIs(marker['label_verified'],True)
            self.assertEqual(marker['label_source'],'item_manager')
            self.assertEqual(marker['label'],data[marker['manager_key']]['beginner'])
        rows=json.loads((out/'objects.json').read_text())
        self.assertEqual(next(row for row in rows if row['object_id']==143)['kind'],'unverified_named_object')

    def test_offstage_unverified_object_does_not_get_an_invented_pointer(self):
        from unittest.mock import patch
        from gardens.render import load_scene
        data=dict(self.data);data.pop('banana_anc_giz_riv')
        scene=load_scene(GIZA,with_context=True)
        # Retain the real sprite and encoded name, but place it off stage in
        # every decoded state to exercise the visibility gate for unknowns.
        for changes in scene[1].frames.values():
            for change in changes:
                if change['object_id']==143:
                    a,b,c,d,_,_=change['matrix']
                    change['matrix']=(a,b,c,d,-10000,-10000)
        out=Path(self.temp.name)/'unknown_offstage'
        with patch('gardens.render.load_scene',return_value=scene):
            r=render_bundle(GIZA,data,out)
        self.assertEqual(r['status'],'incomplete')
        self.assertIn(143,r['unresolved_named_objects'])
        self.assertEqual(r['unverified_named_pointers'],0)
        self.assertEqual(r['total_named_pointers'],67)
        markers=json.loads((out/'solution_objects.json').read_text())
        self.assertNotIn(143,{m['object_id'] for m in markers})

    def test_missing_item_does_not_publish_final_images(self):
        data={**self.data,'nonexistent_object_anc_giz_riv':{'beginner':'Unrecoverable'}}
        destination=Path(self.temp.name)/'incomplete'
        r=render_bundle(GIZA,data,destination)
        self.assertEqual(r['status'],'incomplete')
        self.assertTrue(r['issues'])
        self.assertFalse((destination/'scene_complete.png').exists())
        self.assertTrue((destination/'incomplete/scene_complete.png').exists())


if __name__=='__main__':unittest.main()
