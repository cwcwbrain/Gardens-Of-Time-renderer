"""Ambiguous exported timelines need explicit active prefab ownership evidence."""
import copy
import unittest

from gardens.render import RenderError,_select_root_clip


def pointer(path_id,file_id=0):return {'m_FileID':file_id,'m_PathID':path_id}


class RootClipSelectionTests(unittest.TestCase):
    def scene(self):
        # Timeline 0 is an unused library export. The actual prefab clip is 10.
        root={'m_TimelineID':10,'m_GAFAsset':pointer(900),'m_Parent':pointer(0),
              'm_Enabled':1,'m_IsActive':1,'m_GameObject':pointer(100)}
        library={'m_TimelineID':0,'m_GAFAsset':pointer(900),'m_Parent':pointer(777),
                 'm_Enabled':1,'m_IsActive':1,'m_GameObject':pointer(200)}
        mono={11:root,22:library,900:{'m_Name':'Embedded GAF asset','m_AssetData':[1,2,3]}}
        gameobjects={100:{'m_Name':'Scene prefab','m_IsActive':1,'m_Component':[{'component':pointer(11)},{'component':pointer(101)}]},
                     200:{'m_Name':'Library object','m_IsActive':1,'m_Component':[{'component':pointer(22)},{'component':pointer(201)}]}}
        transforms={101:{'m_GameObject':pointer(100),'m_Father':pointer(0)},
                    201:{'m_GameObject':pointer(200),'m_Father':pointer(0)}}
        return {0,10},mono,900,gameobjects,transforms,{100}

    def test_selects_unique_parentless_active_prefab_clip_instead_of_timeline_zero(self):
        args=self.scene();pid,clip=_select_root_clip(*args)
        self.assertEqual(pid,11);self.assertIs(clip,args[1][11]);self.assertEqual(clip['m_TimelineID'],10)
        self.assertNotEqual(clip['m_TimelineID'],0)

    def test_missing_wrong_or_remote_asset_pointer_never_falls_back_to_timeline_zero(self):
        for value in (None,pointer(901),pointer(900,file_id=1)):
            with self.subTest(asset=value):
                args=self.scene();args[1][11]['m_GAFAsset']=value
                with self.assertRaises(RenderError):_select_root_clip(*args)

    def test_clip_must_be_enabled_active_and_explicitly_parentless(self):
        changes=[('m_Enabled',0),('m_Enabled',None),('m_IsActive',0),('m_IsActive',None),
                 ('m_Parent',None),('m_Parent',0),('m_Parent',pointer(99)),('m_Parent',pointer(0,file_id=1))]
        for field,value in changes:
            with self.subTest(field=field,value=value):
                args=self.scene();args[1][11][field]=value
                with self.assertRaises(RenderError):_select_root_clip(*args)

    def test_gameobject_must_be_local_active_and_an_actual_prefab_root(self):
        for change in ('remote_go','missing_go','inactive_go','missing_active_flag','no_prefab_root','wrong_prefab_root'):
            with self.subTest(change=change):
                roots,mono,asset,objects,transforms,prefabs=self.scene()
                if change=='remote_go':mono[11]['m_GameObject']=pointer(100,file_id=1)
                elif change=='missing_go':objects.pop(100)
                elif change=='inactive_go':objects[100]['m_IsActive']=0
                elif change=='missing_active_flag':objects[100].pop('m_IsActive')
                elif change=='no_prefab_root':prefabs.clear()
                else:prefabs.clear();prefabs.add(200)
                with self.assertRaises(RenderError):_select_root_clip(roots,mono,asset,objects,transforms,prefabs)

    def test_prefab_component_table_must_own_both_clip_and_transform_locally(self):
        for change in ('missing_clip','remote_clip','missing_transform','remote_transform','missing_components'):
            with self.subTest(change=change):
                args=self.scene();go=args[3][100]
                if change=='missing_clip':go['m_Component']=[{'component':pointer(101)}]
                elif change=='remote_clip':go['m_Component'][0]['component']['m_FileID']=1
                elif change=='missing_transform':go['m_Component']=[{'component':pointer(11)}]
                elif change=='remote_transform':go['m_Component'][1]['component']['m_FileID']=1
                else:go.pop('m_Component')
                with self.assertRaises(RenderError):_select_root_clip(*args)

    def test_exactly_one_locally_owned_transform_with_explicit_null_father_is_required(self):
        for change in ('missing','duplicate','wrong_go','remote_go','nonzero_father','remote_null_father','missing_father'):
            with self.subTest(change=change):
                args=self.scene();transforms=args[4]
                if change=='missing':transforms.pop(101)
                elif change=='duplicate':transforms[102]=copy.deepcopy(transforms[101])
                elif change=='wrong_go':transforms[101]['m_GameObject']=pointer(200)
                elif change=='remote_go':transforms[101]['m_GameObject']=pointer(100,file_id=1)
                elif change=='nonzero_father':transforms[101]['m_Father']=pointer(201)
                elif change=='remote_null_father':transforms[101]['m_Father']=pointer(0,file_id=1)
                else:transforms[101].pop('m_Father')
                with self.assertRaises(RenderError):_select_root_clip(*args)

    def test_two_eligible_prefab_roots_remain_ambiguous(self):
        roots,mono,asset,objects,transforms,prefabs=self.scene()
        mono[22]['m_Parent']=pointer(0);prefabs.add(200)
        with self.assertRaises(RenderError):_select_root_clip(roots,mono,asset,objects,transforms,prefabs)

    def test_two_active_clips_on_one_prefab_root_remain_ambiguous(self):
        args=self.scene();duplicate=copy.deepcopy(args[1][11]);args[1][12]=duplicate
        args[3][100]['m_Component'].append({'component':pointer(12)})
        with self.assertRaises(RenderError):_select_root_clip(*args)

    def test_clip_timeline_must_be_in_the_encoded_unreferenced_root_set(self):
        args=list(self.scene());args[0]={0,99}
        with self.assertRaises(RenderError):_select_root_clip(*args)


if __name__=='__main__':unittest.main()
