"""Append-only update enrolment; declared historical outcomes are not rerendered."""
import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from gardens.batch import Ledger
from gardens.incremental import enroll_updates
from gardens.scenes import Scene,variant_id


def candidate(name):
    url='https://assets.test/'+name+'.bundle'
    return {'roots':[name],'issues':[],
            'bundles':[{'url':url,'internal_id':url,'options':{}}]}


def match(scene,*candidates,status=None,issues=None):
    return {'scene_id':scene.scene_id,'order':scene.order,
            'status':status or ('ambiguous' if len(candidates)>1 else 'matched'),
            'issues':issues or [],'candidates':list(candidates)}


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name)/'output';self.checkpoint=self.out/'batch/tests.sqlite3'
        ledger=Ledger(self.checkpoint)
        try:
            for ordinal,(sid,status,active) in enumerate([
                ('PassedScene','passed',1),('IncompleteScene','incomplete',1),
                ('FailedScene','failed',1),('BlockedScene','blocked',1),
                ('PendingScene','pending',1),('RunningScene','running',1),
                ('InactiveScene','incomplete',0)],1):
                scene=self.scene(ordinal,sid);item=candidate(sid+'old');vid=variant_id(item)
                destination=self.out/'scenes'/sid;destination.mkdir(parents=True)
                image=destination/'scene_complete.png';image.write_bytes(('historical '+sid).encode())
                definition={'scene':asdict(scene),'candidate':item,'destination':str(destination),
                            'output':str(self.out),'frame':None,'offline':True,'timeout':1,'retries':0,
                            'fingerprint':'historical-'+sid}
                result={'status':status,'issues':['historical result'],'files':[str(image)]}
                ledger.db.execute('INSERT INTO scenes VALUES (?,?,?,?,?)',(sid,ordinal,'matched','[]',active))
                ledger.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)',
                                  (sid,vid,ordinal,'historical-'+sid,status,json.dumps(definition),json.dumps(result),active))
            ledger.db.commit()
        finally:ledger.close()

    def scene(self,order,sid):
        return Scene(order,sid,'https://assets.test/'+sid+'.jpg',
                     'https://assets.test/items_'+sid+'.json',[sid,'verified-thumbnail','verified-item-manager'])

    def rows(self,table):
        with sqlite3.connect(self.checkpoint) as db:
            db.row_factory=sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY 1,2')]

    def preserved_files(self):
        return {str(path):(path.read_bytes(),path.stat().st_mtime_ns)
                for path in (self.out/'scenes').rglob('*') if path.is_file()}

    def assert_original_rows(self,scenes,jobs,files):
        after_scenes={row['id']:row for row in self.rows('scenes')}
        after_jobs={(row['scene_id'],row['variant_id']):row for row in self.rows('jobs')}
        for row in scenes:self.assertEqual(after_scenes[row['id']],row)
        for row in jobs:self.assertEqual(after_jobs[(row['scene_id'],row['variant_id'])],row)
        self.assertEqual(self.preserved_files(),files)

    def test_appends_new_chapter_and_existing_scene_version_preserving_every_prior_row(self):
        scenes_before=self.rows('scenes');jobs_before=self.rows('jobs');files=self.preserved_files()
        old=self.scene(1,'PassedScene');new=self.scene(8,'Chapter748SceneOne')
        matches=[match(old,candidate('PassedSceneold'),candidate('PassedScenenew')),
                 match(new,candidate('Chapter748SceneOne'))]
        stats=enroll_updates(self.out,[old,new],matches,complete=True)
        self.assertEqual(stats['added_scenes'],1);self.assertEqual(stats['added_variants'],2)
        self.assertTrue(stats['discovery_complete'])
        self.assert_original_rows(scenes_before,jobs_before,files)
        new_jobs=[row for row in self.rows('jobs') if row not in jobs_before]
        self.assertEqual({row['status'] for row in new_jobs},{'incomplete'})
        for row in new_jobs:
            definition=json.loads(row['definition']);result=json.loads(row['result'])
            self.assertTrue(definition['discovered_by_update'])
            self.assertEqual(result['issues'],['New scene/version queued by update'])
            self.assertEqual(result['files'],[])
            destination=Path(definition['destination'])
            if row['scene_id']=='PassedScene':
                self.assertEqual(destination,self.out/'scenes/_new_versions/PassedScene'/('variant_'+row['variant_id']))
                self.assertEqual(row['ordinal'],1)
            else:
                self.assertEqual(destination,self.out/'scenes/Chapter748SceneOne')
                self.assertEqual(row['ordinal'],8)

    def test_repeat_discovery_is_idempotent(self):
        scene=self.scene(8,'NewChapter');matches=[match(scene,candidate('new-a'),candidate('new-b'))]
        enroll_updates(self.out,[scene],matches,complete=True)
        scenes_before=self.rows('scenes');jobs_before=self.rows('jobs');files=self.preserved_files()
        stats=enroll_updates(self.out,[scene],matches,complete=True)
        self.assertEqual(stats['added_scenes'],0);self.assertEqual(stats['added_variants'],0)
        self.assertEqual(self.rows('scenes'),scenes_before);self.assertEqual(self.rows('jobs'),jobs_before)
        self.assertEqual(self.preserved_files(),files)
        for row in jobs_before:
            if row['scene_id']=='NewChapter':
                self.assertEqual(Path(json.loads(row['definition'])['destination']),
                                 self.out/'scenes/NewChapter'/('variant_'+row['variant_id']))

    def test_partial_catalogue_coverage_makes_no_writes(self):
        scene=self.scene(8,'NewChapter');before=self.checkpoint.read_bytes()
        scenes_before=self.rows('scenes');jobs_before=self.rows('jobs');files=self.preserved_files()
        stats=enroll_updates(self.out,[scene],[match(scene,candidate('new'))],complete=False)
        self.assertFalse(stats['discovery_complete']);self.assertEqual(stats['added_variants'],0)
        self.assertEqual(self.checkpoint.read_bytes(),before)
        self.assertEqual(self.rows('scenes'),scenes_before);self.assertEqual(self.rows('jobs'),jobs_before)
        self.assertEqual(self.preserved_files(),files)

    def test_casefolded_scene_identity_keeps_saved_spelling_and_ordinal(self):
        scene=self.scene(999,'passedscene');item=candidate('additional')
        stats=enroll_updates(self.out,[scene],[match(scene,item)],complete=True)
        self.assertEqual(stats['added_scenes'],0);self.assertEqual(stats['added_variants'],1)
        row=next(row for row in self.rows('jobs') if row['variant_id']==variant_id(item))
        self.assertEqual(row['scene_id'],'PassedScene');self.assertEqual(row['ordinal'],1)
        self.assertEqual(json.loads(row['definition'])['scene']['scene_id'],'PassedScene')
        self.assertEqual(json.loads(row['definition'])['scene']['order'],1)

    def test_same_candidate_for_existing_nonpassing_statuses_is_untouched(self):
        before=self.rows('jobs');scene_rows=self.rows('scenes');files=self.preserved_files()
        scenes=[self.scene(row['ordinal'],row['id']) for row in scene_rows]
        matches=[match(scene,candidate(scene.scene_id+'old')) for scene in scenes]
        stats=enroll_updates(self.out,scenes,matches,complete=True)
        self.assertEqual(stats['added_variants'],0)
        self.assertEqual(self.rows('jobs'),before);self.assertEqual(self.rows('scenes'),scene_rows)
        self.assertEqual(self.preserved_files(),files)

    def test_inactive_scene_gets_no_new_versions(self):
        scene=self.scene(7,'InactiveScene');before=self.rows('jobs')
        stats=enroll_updates(self.out,[scene],[match(scene,candidate('inactive-new'))],complete=True)
        self.assertEqual(stats['added_variants'],0);self.assertEqual(stats['ignored_inactive'],1)
        self.assertEqual(self.rows('jobs'),before)

    def test_unresolved_candidates_reserve_order_as_nonrenderable_placeholders(self):
        scenes=[self.scene(8,'Missing'),self.scene(9,'Provisional'),self.scene(10,'BadDependencies'),self.scene(11,'NoURL')]
        bad=candidate('bad');bad['issues']=['missing dependency']
        no_url=candidate('none');no_url['bundles'][0]['url']=None
        matches=[match(scenes[0],status='missing'),match(scenes[1],candidate('p'),status='provisional'),
                 match(scenes[2],bad),match(scenes[3],no_url)]
        before=self.rows('jobs');scene_rows=self.rows('scenes');files=self.preserved_files()
        stats=enroll_updates(self.out,scenes,matches,complete=True)
        self.assertEqual(stats['added_scenes'],4);self.assertEqual(stats['added_variants'],0)
        self.assertEqual(stats['discovered_blocked'],4);self.assertEqual(stats['blocked_update_variants'],4)
        self.assert_original_rows(scene_rows,before,files)
        for scene in scenes:
            row=next(row for row in self.rows('jobs') if row['scene_id']==scene.scene_id)
            self.assertEqual(row['ordinal'],scene.order)
            self.assertEqual(row['variant_id'],'blocked');self.assertEqual(row['status'],'blocked')
            self.assertIsNone(json.loads(row['definition'])['candidate'])
            self.assertEqual(json.loads(row['result'])['files'],[])

    def test_missing_middle_mission_keeps_reserved_order_when_assets_arrive(self):
        first=self.scene(8,'Chapter748First');middle=self.scene(9,'Chapter748Delayed');last=self.scene(10,'Chapter748Last')
        first_item=candidate('chapter-first');middle_item=candidate('chapter-delayed');last_item=candidate('chapter-last')
        initial=[match(first,first_item),match(middle,status='missing'),match(last,last_item)]
        stats=enroll_updates(self.out,[first,middle,last],initial,complete=True)
        self.assertEqual(stats['added_variants'],2);self.assertEqual(stats['discovered_blocked'],1)
        self.assertEqual(stats['blocked_update_variants'],1)
        before_scenes=self.rows('scenes');before_jobs=self.rows('jobs');files=self.preserved_files()
        stats=enroll_updates(self.out,[first,middle,last],
                             [match(first,first_item),match(middle,middle_item),match(last,last_item)],complete=True)
        self.assertEqual(stats['added_scenes'],0);self.assertEqual(stats['added_variants'],1)
        self.assertEqual(stats['retired_placeholders'],1);self.assertEqual(stats['blocked_update_variants'],0)
        self.assertEqual(self.rows('scenes'),before_scenes);self.assertEqual(self.preserved_files(),files)
        current={(row['scene_id'],row['variant_id']):row for row in self.rows('jobs')}
        for row in before_jobs:
            expected=dict(row)
            if row['scene_id']==middle.scene_id:expected['active']=0
            self.assertEqual(current[(row['scene_id'],row['variant_id'])],expected)
        delayed=current[(middle.scene_id,variant_id(middle_item))]
        self.assertEqual(delayed['ordinal'],9);self.assertEqual(delayed['status'],'incomplete')
        after_scenes=self.rows('scenes');after_jobs=self.rows('jobs')
        stats=enroll_updates(self.out,[first,middle,last],
                             [match(first,first_item),match(middle,middle_item),match(last,last_item)],complete=True)
        self.assertEqual(stats['added_variants'],0);self.assertEqual(stats['retired_placeholders'],0)
        self.assertEqual(stats['blocked_update_variants'],0)
        self.assertEqual(self.rows('scenes'),after_scenes);self.assertEqual(self.rows('jobs'),after_jobs)

    def test_legacy_blocked_placeholder_is_never_retired(self):
        scene=self.scene(8,'LegacyBlocked');ledger=Ledger(self.checkpoint)
        try:
            definition={'scene':asdict(scene),'candidate':None,'destination':str(self.out/'scenes/LegacyBlocked'),
                        'output':str(self.out),'frame':None,'offline':True,'timeout':1,'retries':0}
            result={'status':'blocked','issues':['old blocked match'],'files':[]}
            ledger.db.execute('INSERT INTO scenes VALUES (?,?,?,?,?)',('LegacyBlocked',8,'missing','[]',1))
            ledger.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)',
                              ('LegacyBlocked','blocked',8,'old-blocked-proof','blocked',json.dumps(definition),json.dumps(result),1))
            ledger.db.commit()
        finally:ledger.close()
        before_scenes=self.rows('scenes');before_jobs=self.rows('jobs');files=self.preserved_files()
        stats=enroll_updates(self.out,[scene],[match(scene,candidate('now-found'))],complete=True)
        self.assertEqual(stats['added_variants'],1);self.assertEqual(stats['retired_placeholders'],0)
        self.assertEqual(stats['blocked_update_variants'],0)
        self.assert_original_rows(before_scenes,before_jobs,files)

    def test_reserved_namespace_collision_fails_before_any_enrolment(self):
        first=self.scene(8,'NewChapter');reserved=self.scene(9,'_NEW_VERSIONS')
        before=self.rows('jobs');scene_rows=self.rows('scenes')
        with self.assertRaisesRegex(ValueError,'Reserved update namespace'):
            enroll_updates(self.out,[first,reserved],[match(first,candidate('new')),match(reserved,candidate('reserved'))],complete=True)
        self.assertEqual(self.rows('jobs'),before);self.assertEqual(self.rows('scenes'),scene_rows)

    def test_missing_checkpoint_does_not_create_a_run(self):
        missing=Path(self.temp.name)/'missing-output';scene=self.scene(1,'NewChapter')
        with self.assertRaisesRegex(ValueError,'checkpoint'):
            enroll_updates(missing,[scene],[match(scene,candidate('new'))],complete=True)
        self.assertFalse(missing.exists())


if __name__=='__main__':unittest.main()
