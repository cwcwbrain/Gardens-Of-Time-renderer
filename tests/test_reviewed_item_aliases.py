"""Reviewed source exceptions cannot become general spelling corrections."""
import hashlib
import json
import unittest
from pathlib import Path

from gardens.item_aliases import reviewed_item_bindings
from gardens.render import parse_items

FIXTURES=Path(__file__).parent/'fixtures'
BUNDLE='1ff326c9906b657ba4e5aaa6a29ae5536b62a4eb79e799ee8eda2260018e926d'
MANAGER='435145a7522e2426e686c9b619ea06f26bc069b831b4f5c5bc41a9a00fa6f5a4'
KEY='colourfulball_509_eas_mal_ini'


class ReviewedItemAliasTests(unittest.TestCase):
    def setUp(self):
        self.manager=json.loads((FIXTURES/'EasterFestiveMall_items.json').read_text())
        self.names={int(k):v for k,v in json.loads((FIXTURES/'EasterFestiveMall_names.json').read_text()).items()}
        self.bindings=reviewed_item_bindings(BUNDLE,MANAGER)

    def test_actual_reported_sources_resolve_all_59_labels_without_renaming(self):
        self.assertEqual(hashlib.sha256(json.dumps(self.manager,sort_keys=True).encode()).hexdigest(),MANAGER)
        original_names=dict(self.names)
        items,animations,issues=parse_items(self.manager,self.names,self.bindings)
        self.assertEqual(issues,[]);self.assertEqual(animations,[])
        self.assertEqual(len(items),59)
        self.assertEqual(items[96]['manager_key'],KEY)
        self.assertEqual(items[96]['label'],'Beach Ball')
        self.assertEqual(self.names,original_names)
        proof=items[96]['binding_override']
        self.assertEqual(proof['method'],'reviewed_source_alias')
        self.assertEqual(proof['bundle_sha256'],BUNDLE)
        self.assertEqual(proof['item_manager_sha256'],MANAGER)

    def test_no_exception_for_a_different_bundle_or_changed_manager(self):
        for bundle,manager in (('0'*64,MANAGER),(BUNDLE,'0'*64)):
            with self.subTest(bundle=bundle,manager=manager):
                bindings=reviewed_item_bindings(bundle,manager)
                self.assertEqual(bindings,{})
                items,_,issues=parse_items(self.manager,self.names,bindings)
                self.assertEqual(len(items),58);self.assertNotIn(96,items)
                self.assertTrue(any(KEY in issue for issue in issues))

    def test_an_exact_binding_has_priority_and_needs_no_override(self):
        self.names[96]='colourfulball'
        items,_,issues=parse_items(self.manager,self.names,self.bindings)
        self.assertEqual(issues,[]);self.assertEqual(len(items),59)
        self.assertNotIn('binding_override',items[96])

    def test_alias_does_not_rescue_ambiguous_normal_candidates(self):
        self.names.update({300:'colourfulball',301:'Colourfulball'})
        items,_,issues=parse_items(self.manager,self.names,self.bindings)
        self.assertNotIn(96,items);self.assertNotIn(300,items);self.assertNotIn(301,items)
        self.assertTrue(any(KEY in issue and 'found 2' in issue for issue in issues))

    def test_alias_requires_the_reviewed_id_name_and_unique_instance(self):
        for changed in ({**self.names,96:'colourfullball_1'},
                        {**{k:v for k,v in self.names.items() if k!=96},999:'colourfullball'},
                        {**self.names,999:'colourfullball'}):
            with self.subTest(names=changed):
                items,_,issues=parse_items(self.manager,changed,self.bindings)
                self.assertTrue(any(KEY in issue for issue in issues))
                self.assertNotIn(96,items);self.assertNotIn(999,items)

    def test_other_chapters_and_spelling_errors_are_not_inferred(self):
        for key in ('colourfulball_510_eas_mal_ini','colourfulbal_509_eas_mal_ini'):
            with self.subTest(key=key):
                items,_,issues=parse_items({key:{'beginner':'Do not guess'}},{96:'colourfullball'},self.bindings)
                self.assertEqual(items,{});self.assertTrue(issues)

    def test_missing_label_and_duplicate_manager_assignment_still_fail(self):
        data={KEY:{'beginner':'','gold':''}}
        items,_,issues=parse_items(data,self.names,self.bindings)
        self.assertNotIn(96,items);self.assertTrue(any('no nonempty label' in issue for issue in issues))
        for data in ({KEY:self.manager[KEY],'colourfullball_other':{'beginner':'Conflict'}},
                     {'colourfullball_other':{'beginner':'Conflict'},KEY:self.manager[KEY]}):
            with self.subTest(data=data):
                items,_,issues=parse_items(data,self.names,self.bindings)
                self.assertEqual(len(items),1)
                self.assertTrue(any('Multiple Item Manager keys' in issue for issue in issues))


if __name__=='__main__':unittest.main()
