"""Out-of-atlas sampling uses only explicitly serialized Unity Clamp modes."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from gardens.render import RenderError,atlas_crop,sprite_for


class TextureWrapTests(unittest.TestCase):
    def atlas(self,modes=(1,1)):
        image=Image.new('RGBA',(4,3))
        for y in range(image.height):
            for x in range(image.width):image.putpixel((x,y),(x*50+11,y*70+13,(x+y)*25+7,x*25+y*13+37))
        image.putpixel((0,0),(7,9,11,0))
        if modes is not None:image.info['unity_wrap_modes']=modes
        return image

    def assert_clamp_pixels(self,image,rect):
        before=(image.size,image.tobytes(),copy.deepcopy(image.info));tile=atlas_crop(image,rect)
        x,y,w,h=rect;self.assertEqual(tile.size,(w,h));self.assertEqual(tile.mode,'RGBA')
        for v in range(h):
            for u in range(w):
                point=(min(image.width-1,max(0,x+u)),min(image.height-1,max(0,y+v)))
                self.assertEqual(tile.getpixel((u,v)),image.getpixel(point))
        self.assertEqual((image.size,image.tobytes(),image.info),before)
        return tile

    def test_five_rows_below_texture_use_actual_bottom_border_rgba(self):
        image=self.atlas();tile=self.assert_clamp_pixels(image,(1,1,2,7))
        for y in range(2,7):
            self.assertEqual(tile.getpixel((0,y)),image.getpixel((1,2)))
            self.assertEqual(tile.getpixel((1,y)),image.getpixel((2,2)))

    def test_top_left_right_and_all_corner_overruns_repeat_only_source_edge_pixels(self):
        for rect in ((1,-2,2,4),(-2,0,4,2),(2,0,5,2),(-1,-1,6,5)):
            with self.subTest(rect=rect):self.assert_clamp_pixels(self.atlas(),rect)

    def test_fully_outside_rectangles_clamp_to_corresponding_edge_or_corner(self):
        for rect in ((7,8,3,2),(-8,-6,3,2),(-9,1,2,2),(8,0,2,2),(10**12,10**12,2,2)):
            with self.subTest(rect=rect):self.assert_clamp_pixels(self.atlas(),rect)

    def test_only_the_overflowing_axis_requires_explicit_clamp(self):
        for modes,rect in (((1,0),(-1,1,3,1)),((1,None),(-1,1,3,1)),
                           ((0,1),(1,-1,1,3)),((None,1),(1,-1,1,3))):
            with self.subTest(modes=modes,rect=rect):self.assert_clamp_pixels(self.atlas(modes),rect)
        for modes,rect in (((1,0),(1,-1,1,3)),((0,1),(-1,1,3,1)),
                           ((1,2),(-1,-1,3,3)),((2,1),(-1,-1,3,3))):
            with self.subTest(modes=modes,rect=rect),self.assertRaises(RenderError):atlas_crop(self.atlas(modes),rect)

    def test_absent_repeat_mirror_or_malformed_modes_never_enable_outside_sampling(self):
        for modes in (None,(0,0),(2,2),(3,3),(None,None),(1,),('Clamp','Clamp')):
            with self.subTest(modes=modes),self.assertRaisesRegex(RenderError,'outside texture'):
                atlas_crop(self.atlas(modes),(-1,-1,3,3))

    def test_inside_crop_is_unchanged_without_any_wrap_metadata(self):
        for modes in (None,(0,0),(2,2)):
            with self.subTest(modes=modes):
                image=self.atlas(modes);before=(image.size,image.tobytes(),copy.deepcopy(image.info))
                tile=atlas_crop(image,(1,1,2,2))
                self.assertEqual(tile.tobytes(),image.crop((1,1,3,3)).tobytes())
                tile.putpixel((0,0),(0,0,0,0))
                self.assertEqual((image.size,image.tobytes(),image.info),before)

    def test_nonintegral_and_nonpositive_rectangles_fail_instead_of_padding_or_rounding(self):
        for rect in ((0.5,0,2,2),(0,0.5,2,2),(0,0,1.5,2),(0,0,0,2),(0,0,2,0),(0,0,-1,2)):
            with self.subTest(rect=rect),self.assertRaises(RenderError):atlas_crop(self.atlas(),rect)

    def test_unsafe_outside_dimensions_are_rejected_before_allocating_a_tile(self):
        image=self.atlas()
        for rect in ((0,0,8193,1),(0,0,1,8193),(0,0,100000,100000)):
            with self.subTest(rect=rect),patch('gardens.render.Image.new',side_effect=AssertionError('must reject before allocation')):
                with self.assertRaisesRegex(RenderError,'Unsafe atlas crop dimensions'):atlas_crop(image,rect)

    def test_sprite_crop_clamps_in_native_atlas_axes_before_counter_rotating(self):
        image=self.atlas();before=(image.size,image.tobytes(),copy.deepcopy(image.info))
        element={'atlas_id':7,'origin':(-1,1),'size':(3,2),'pivot':(0,0),'scale':1,'scale9':None,'rotated':True}
        timeline=SimpleNamespace(objects={12:{'element':34,'type':0}})
        scope={'timeline':timeline,'atlas':{'elements':{34:element}},'textures':{7:('explicit-atlas',image)},'metadata':{}}
        tile,actual,fields=sprite_for(scope,12)
        self.assertEqual(tile.size,(2,3));self.assertIs(actual,element)
        for y in range(3):
            for x in range(2):
                # Counter-clockwise rotation maps to native (width-1-y,x).
                native=(max(0,-1+2-y),1+x)
                self.assertEqual(tile.getpixel((x,y)),image.getpixel(native))
        self.assertEqual(fields['crop'],[-1,1,3,2]);self.assertEqual(fields['texture'],'explicit-atlas')
        self.assertEqual((image.size,image.tobytes(),image.info),before)


if __name__=='__main__':unittest.main()
