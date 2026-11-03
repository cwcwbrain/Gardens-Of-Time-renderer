"""Real GAF/atlas/Item Manager regressions plus small geometric invariants."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import UnityPy
from PIL import Image, ImageChops

from gardens.gaf import GAFError, decode
from gardens.render import parse_items, render_bundle, warp_sprite

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
        self.assertEqual(len(r['atlases']),2)
        self.assertEqual(r['duplicate_backgrounds'],0)
        for name in ('scene_empty.png','scene_complete.png','scene_solution.png'):
            with Image.open(self.out/name) as im:
                self.assertEqual(im.size,(1280,758))
                self.assertEqual(im.getchannel('A').getextrema(),(255,255))

    def test_empty_removes_objects_solution_marks_visible_targets(self):
        with Image.open(self.out/'scene_empty.png') as a,Image.open(self.out/'scene_complete.png') as b,Image.open(self.out/'scene_solution.png') as c:
            self.assertIsNotNone(ImageChops.difference(a,b).convert('RGB').getbbox())
            self.assertIsNotNone(ImageChops.difference(b,c).convert('RGB').getbbox())
            self.assertEqual(a.getpixel((700,20)),b.getpixel((700,20)))
            self.assertEqual(b.getpixel((700,20)),c.getpixel((700,20)))
        markers=json.loads((self.out/'solution_objects.json').read_text())
        self.assertEqual(len({m['object_id'] for m in markers}),68)
        self.assertTrue(all(m['visible_pixels']>0 and m['label'] for m in markers))

    def test_explicit_animation_snapshot_and_exclusions(self):
        self.assertEqual(self.report['inactive_at_snapshot'],[154,163])
        self.assertEqual(self.report['excluded_gameplay_objects'],[156,157,158,159,160])
        rows=json.loads((self.out/'objects.json').read_text());byid={r['object_id']:r for r in rows}
        self.assertEqual(byid[153]['status'],'mask_only')
        self.assertEqual(byid[164]['status'],'rendered')
        self.assertEqual(byid[161]['atlas_id'],2)
        self.assertEqual(byid[143]['manager_key'],'banana_anc_giz_riv')

    def test_nested_scene_stays_incomplete(self):
        data=json.loads((ROOT/'tests/fixtures/VictorianExteriorRedux1_items.json').read_text())
        out=Path(self.temp.name)/'victorian'
        r=render_bundle(VICTORIAN,data,out)
        self.assertEqual(r['status'],'incomplete')
        self.assertTrue(any('Nested timeline' in issue for issue in r['issues']))
        self.assertFalse((out/'scene_complete.png').exists())
        self.assertEqual(len(r['atlases']),2)

    def test_missing_item_does_not_publish_final_images(self):
        data={**self.data,'nonexistent_object_anc_giz_riv':{'beginner':'Unrecoverable'}}
        destination=Path(self.temp.name)/'incomplete'
        r=render_bundle(GIZA,data,destination)
        self.assertEqual(r['status'],'incomplete')
        self.assertTrue(r['issues'])
        self.assertFalse((destination/'scene_complete.png').exists())
        self.assertTrue((destination/'incomplete/scene_complete.png').exists())


if __name__=='__main__':unittest.main()
