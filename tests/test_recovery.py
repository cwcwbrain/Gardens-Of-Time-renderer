"""Regressions for ordinary-scene failures from the chapter 82 report."""
import unittest
from pathlib import Path
from PIL import Image
from gardens.gaf import decode
from gardens.render import parse_items,warp_sprite
from gardens.nested import cascade_color
from gardens.scenes import scene_kind

class RecoveryTests(unittest.TestCase):
    def test_real_v3_atlas_consumed_and_scoped(self):
        m=decode((Path(__file__).parent/'fixtures/YucatanCenote.gaf').read_bytes())
        self.assertEqual(m.unsupported,[])
        root=next(a for a in m.atlases if a['timeline']==0)
        self.assertGreater(len(root['elements']),60)
        for e in root['elements'].values():
            self.assertGreater(e['scale'],0);self.assertGreater(e['scale_y'],0)
            self.assertIn(e['atlas_id'],root['sources'])
        with self.assertRaises(ValueError):decode((Path(__file__).parent/'fixtures/YucatanCenote.gaf').read_bytes()[:-1])

    def test_anisotropic_atlas_scale(self):
        image=warp_sprite(Image.new('RGBA',(4,4),'red'),{'pivot':(0,0),'scale':2,'scale_y':1},
                          {'matrix':(1,0,0,1,0,0),'alpha':1,'color':None},(8,8))
        self.assertEqual(image.getbbox(),(0,0,2,4))

    def test_animation_descriptors_preserve_real_findable_names(self):
        items,animations,issues=parse_items({'Anim_Light_scene':{},'animprojectorbeam_gra_the_ini':{},
                                            'animal_scene':{'beginner':'Animal'}},
                                           {1:'anim_light01',2:'anim_projectorbeam01',3:'animal'})
        self.assertEqual(issues,[]);self.assertEqual(set(items),{3});self.assertEqual(len(animations),2)

    def test_color_cascade_products_and_additive_offsets(self):
        result=cascade_color((.1,.5,.2,.8,.3,1,.4),(.2,.4,.1,.5,.2,.9,.3))
        for actual,expected in zip(result,(.3,.2,.3,.4,.5,.9,.7)):self.assertAlmostEqual(actual,expected)
        self.assertEqual(cascade_color(None,None),(0,1,0,1,0,1,0))

    def test_modes_do_not_misclassify_rooftop(self):
        for name in ['SceneFTD','SceneFTD1Redux','SceneFTD1Redux2']:
            self.assertEqual(scene_kind(name),'FTD')
        self.assertEqual(scene_kind('SceneSTD1'),'STD');self.assertEqual(scene_kind('SceneFTO'),'FTO')
        self.assertEqual(scene_kind('RooftopBandit'),'ordinary')

    def test_colour_matrix_uses_channels_offsets_and_alpha(self):
        from gardens.effects import apply_effects
        image=Image.new('RGBA',(2,1),(20,40,60,128))
        matrix=(0,1,0,0,5, 1,0,0,0,-10, 0,0,2,0,0, 0,0,0,.5,0)
        out=apply_effects(image,[{'type':6,'values':matrix}])
        self.assertEqual(out.getpixel((0,0)),(45,10,120,64))
        self.assertEqual(image.getpixel((0,0)),(20,40,60,128))
        with self.assertRaisesRegex(ValueError,'spatial filter'):
            apply_effects(image,[{'type':2,'values':(3,3,1),'inner':True,'knockout':False}])

    def test_encoded_bounds_do_not_move_offstage_objects(self):
        from gardens.render import sprite_bounds
        tile=Image.new('RGBA',(20,10),'red')
        state={'matrix':(1,0,0,1,1422,391),'alpha':1,'color':None}
        self.assertEqual(sprite_bounds(tile,{'pivot':(2,3),'scale':1},state),[1420,388,1440,398])
        self.assertIsNone(warp_sprite(tile,{'pivot':(2,3),'scale':1},state,(1280,758)).getbbox())

    def test_rotated_atlas_tile_restores_orientation(self):
        from gardens.gaf import Timeline
        from gardens.render import sprite_for
        image=Image.new('RGBA',(2,3))
        for y in range(3):
            for x in range(2):image.putpixel((x,y),(x*90,y*90,0,255))
        timeline=Timeline(0,1,(0,0,3,2),(0,0),'');timeline.objects={1:{'element':4,'type':0}}
        element={'atlas_id':2,'origin':(0,0),'size':(2,3),'pivot':(0,0),'scale':1,'scale9':None,'rotated':True}
        scope={'timeline':timeline,'atlas':{'elements':{4:element}},'textures':{2:('test',image)},'metadata':{}}
        tile,_,_=sprite_for(scope,1)
        self.assertEqual(tile.size,(3,2));self.assertEqual(tile.getpixel((0,0)),image.getpixel((1,0)))

    def test_identically_named_instances_all_get_the_verified_label(self):
        items,_,issues=parse_items({'chessman_yuc_cen_ini':{'beginner':'Chessman'}},{159:'chessman',160:'chessman'})
        self.assertEqual(issues,[]);self.assertEqual(set(items),{159,160})
        self.assertEqual({item['label'] for item in items.values()},{'Chessman'})
        items,_,issues=parse_items({'hat_scene':{'beginner':'Hat'}},{1:'hat',2:'Hat'})
        self.assertTrue(issues);self.assertFalse(items)

    def test_blur_preserves_colour_without_transparent_rgb_bleed(self):
        from gardens.effects import apply_effects
        image=Image.new('RGBA',(7,7),(0,255,0,0));image.putpixel((3,3),(255,0,0,255))
        result,(x,y)=apply_effects(image,[{'type':1,'values':(4,0)}],with_offset=True)
        self.assertEqual(result.size,(15,11));self.assertEqual((x,y),(4,2))
        self.assertGreater(result.getpixel((x+2,y+3))[3],0)
        self.assertEqual(result.getpixel((x+2,y+3))[:3],(255,0,0))
        self.assertEqual(result.getpixel((x+3,y+2))[3],0)
        self.assertEqual(result.getpixel((x+2,y+3)),result.getpixel((x+4,y+3)))

    def test_outer_glow_retains_opaque_object_and_expands_alpha(self):
        from gardens.effects import apply_effects
        image=Image.new('RGBA',(1,1),(255,0,0,255))
        effect={'type':2,'values':(4,4,1),'color':[255,0,0,255],'inner':False,'knockout':False}
        result,(x,y)=apply_effects(image,[effect],with_offset=True)
        self.assertEqual(result.getpixel((x,y)),(255,0,0,255))
        self.assertGreater(result.getpixel((x+1,y))[3],0)
        self.assertEqual(result.getpixel((x+1,y))[:3],(0,0,255))

    def test_filtered_object_keeps_its_encoded_position(self):
        image=Image.new('RGBA',(1,1),(255,0,0,255))
        state={'matrix':(1,0,0,1,10,10),'alpha':.5,'color':None,'effects':[{'type':1,'values':(4,0)}]}
        result=warp_sprite(image,{'pivot':(0,0),'scale':1},state,(21,21))
        alpha=result.getchannel('A');bbox=alpha.getbbox()
        self.assertEqual((bbox[0]+bbox[2]-1)/2,10)
        self.assertEqual((bbox[1]+bbox[3]-1)/2,10)
        self.assertLessEqual(alpha.getextrema()[1],128)

    def test_adjust_colour_precedes_tint_and_object_alpha(self):
        matrix=(2,0,0,0,0, 0,1,0,0,0, 0,0,1,0,0, 0,0,0,1,0)
        state={'matrix':(1,0,0,1,0,0),'alpha':.5,'color':(0,.5,10/255,1,0,1,0),
               'effects':[{'type':6,'values':matrix}]}
        image=warp_sprite(Image.new('RGBA',(1,1),(20,40,60,255)),{'pivot':(0,0),'scale':1},state,(1,1))
        pixel=image.getpixel((0,0))
        # Pillow's premultiplied affine sampler can quantize RGB by one byte.
        self.assertTrue(all(abs(a-b)<=1 for a,b in zip(pixel[:3],(30,40,60))))
        self.assertEqual(pixel[3],128)

    def test_early_load_failure_cannot_leave_stale_certified_images(self):
        import tempfile
        from unittest.mock import patch
        from gardens.render import render_bundle,RenderError
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)
            for name in ('scene_empty.png','scene_complete.png','scene_solution.png'):(out/name).write_bytes(b'old')
            with patch('gardens.render.load_scene',side_effect=RenderError('missing GAF')):
                with self.assertRaisesRegex(RenderError,'missing GAF'):render_bundle('unused',{},out)
            self.assertFalse(list(out.glob('scene_*.png')))
