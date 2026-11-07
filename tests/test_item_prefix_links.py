"""Longest exact named-part links keep numbered objects and labels separate."""
import unittest

from gardens.render import parse_items


class ItemPrefixLinkTests(unittest.TestCase):
    def test_reported_numbered_umbrella_keys_link_each_label_to_its_exact_object(self):
        # This key structure reproduces the ordinary-scene report's _1 ambiguity;
        # the IDs/labels are explicit test data, not inferred image content.
        data={'umbrella_702_gro_gif_ini':{'beginner':'Umbrella'},
              'umbrella_1_702_gro_gif_ini':{'beginner':'Second Umbrella'}}
        for names in ({101:'umbrella',102:'umbrella_1',103:'umbrella_2'},
                      {103:'umbrella_2',102:'umbrella_1',101:'umbrella'}):
            with self.subTest(names=names):
                items,animations,issues=parse_items(data,names)
                self.assertEqual(issues,[]);self.assertEqual(animations,[])
                self.assertEqual(set(items),{101,102})
                self.assertEqual(items[101]['label'],'Umbrella')
                self.assertEqual(items[101]['manager_key'],'umbrella_702_gro_gif_ini')
                self.assertEqual(items[102]['label'],'Second Umbrella')
                self.assertEqual(items[102]['manager_key'],'umbrella_1_702_gro_gif_ini')
                self.assertIs(items[102]['labels'],data['umbrella_1_702_gro_gif_ini'])

    def test_exact_manager_key_has_priority_over_shorter_underscore_prefix(self):
        items,_,issues=parse_items({'gold_coin_1':{'gold':'Verified Gold Coin'}},
                                  {1:'gold',2:'gold_coin',3:'gold_coin_1',4:'gold_coin_10'})
        self.assertEqual(issues,[]);self.assertEqual(set(items),{3})
        self.assertEqual(items[3]['label'],'Verified Gold Coin')
        self.assertEqual(items[3]['manager_key'],'gold_coin_1')

    def test_numbered_object_one_does_not_claim_object_ten(self):
        items,_,issues=parse_items({'umbrella_10_scene_ini':{'beginner':'Tenth Umbrella'}},
                                  {1:'umbrella',2:'umbrella_1',3:'umbrella_10',4:'umbrella_100'})
        self.assertEqual(issues,[]);self.assertEqual(set(items),{3})
        self.assertEqual(items[3]['label'],'Tenth Umbrella')
        missing,_,issues=parse_items({'umbrella_11_scene_ini':{'beginner':'Eleventh Umbrella'}},
                                    {2:'umbrella_1',3:'umbrella_10'})
        self.assertFalse(missing);self.assertTrue(issues)

    def test_multiword_underscore_named_part_uses_the_full_encoded_name(self):
        key='walking_stick_1_463_castle_ini'
        items,_,issues=parse_items({key:{'beginner':'Decorated Walking Stick'}},
                                  {10:'walking',11:'walking_stick',12:'walking_stick_1',13:'walking_stick_1_glow'})
        self.assertEqual(issues,[]);self.assertEqual(set(items),{12})
        self.assertEqual(items[12]['manager_key'],key)
        self.assertEqual(items[12]['label'],'Decorated Walking Stick')

    def test_all_identically_named_numbered_instances_get_the_verified_label(self):
        key='umbrella_1_scene'
        items,_,issues=parse_items({key:{'beginner':'Numbered Umbrella'}},
                                  {5:'umbrella',6:'umbrella_1',7:'umbrella_1',8:'umbrella_2'})
        self.assertEqual(issues,[]);self.assertEqual(set(items),{6,7})
        self.assertEqual({item['label'] for item in items.values()},{'Numbered Umbrella'})
        self.assertEqual({item['manager_key'] for item in items.values()},{key})

    def test_casefolded_longest_name_collision_remains_ambiguous_without_base_fallback(self):
        items,_,issues=parse_items({'UMBRELLA_1_scene':{'beginner':'Umbrella'}},
                                  {1:'umbrella',2:'umbrella_1',3:'Umbrella_1'})
        self.assertFalse(items);self.assertTrue(issues)
        self.assertTrue(any('unambiguous' in issue for issue in issues))

    def test_unicode_casefold_collision_does_not_pick_the_longer_raw_spelling(self):
        items,_,issues=parse_items({'STRASSE_scene':{'beginner':'Street Sign'}},
                                  {1:'str',2:'straße',3:'STRASSE'})
        self.assertFalse(items);self.assertTrue(issues)

    def test_one_case_variant_still_links_the_longest_name_without_renaming_labels(self):
        label='The Item Manager’s exact label'
        items,_,issues=parse_items({'UMBRELLA_1_SCENE':{'beginner':label}},
                                  {11:'Umbrella',12:'Umbrella_1'})
        self.assertEqual(issues,[]);self.assertEqual(set(items),{12})
        self.assertEqual(items[12]['label'],label)

    def test_word_prefix_typos_and_synonyms_are_not_fuzzy_links(self):
        for key,names in [('umbrella_1_scene',{1:'umbrell'}),
                          ('umbella_1_scene',{1:'umbrella_1'}),
                          ('parasol_1_scene',{1:'umbrella_1'})]:
            with self.subTest(key=key,names=names):
                items,_,issues=parse_items({key:{'beginner':'Do not guess'}},names)
                self.assertFalse(items);self.assertTrue(issues)

    def test_missing_manager_link_stays_a_failure_while_valid_links_remain_available(self):
        items,_,issues=parse_items({'umbrella_1_scene':{'beginner':'Numbered Umbrella'},
                                   'missing_clock_scene':{'beginner':'Clock'}},
                                  {1:'umbrella',2:'umbrella_1',3:'tableclock'})
        self.assertEqual(set(items),{2})
        self.assertEqual(items[2]['label'],'Numbered Umbrella')
        self.assertTrue(any('missing_clock_scene' in issue for issue in issues))
        self.assertNotIn(1,items);self.assertNotIn(3,items)

    def test_longest_named_part_still_requires_an_item_manager_label(self):
        items,_,issues=parse_items({'umbrella_1_scene':{}},{1:'umbrella',2:'umbrella_1'})
        self.assertFalse(items);self.assertTrue(any('no nonempty label' in issue for issue in issues))

    def test_multiple_manager_keys_cannot_reassign_an_object_or_fall_back_to_its_base(self):
        items,_,issues=parse_items({'umbrella_1_first_scene':{'beginner':'First verified label'},
                                   'umbrella_1_second_scene':{'beginner':'Conflicting label'}},
                                  {1:'umbrella',2:'umbrella_1'})
        self.assertEqual(set(items),{2});self.assertEqual(items[2]['label'],'First verified label')
        self.assertTrue(any('Multiple Item Manager keys' in issue for issue in issues))


if __name__=='__main__':unittest.main()
