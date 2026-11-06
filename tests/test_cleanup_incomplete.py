"""Delete only stale renderer previews backed by unchanged passing PNG proof."""
import contextlib
import io
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from gardens.download import sha256
import test_retry_incomplete as retry_fixtures

CANONICAL=('scene_empty.png','scene_complete.png','scene_solution.png')


class CleanupIncompleteTests(unittest.TestCase):
    def setUp(self):
        self.fixture=retry_fixtures.RetryIncompleteTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.out=self.fixture.out
        # Normal cleanup fixtures represent a stopped run. A separate case can
        # restore a running marker when checking active-run refusal.
        with self.connection() as db:
            db.execute("UPDATE jobs SET status='pending' WHERE status='running'")
            for sid,vid,raw in db.execute("SELECT scene_id,variant_id,result FROM jobs WHERE status='passed'").fetchall():
                result=json.loads(raw);folder=Path(result['files'][0]).parent
                records={'validation_report.json':{'status':'validated_snapshot','issues':[],'files':result['files']},
                         'objects.json':[],'solution_objects.json':[]}
                for filename,value in records.items():
                    path=folder/filename;path.write_text(json.dumps(value))
                    result['artifacts'][str(path)]=sha256(path)
                db.execute('UPDATE jobs SET result=? WHERE scene_id=? AND variant_id=?',(json.dumps(result),sid,vid))

    @contextlib.contextmanager
    def connection(self):
        db=sqlite3.connect(self.fixture.ledger_path)
        try:
            with db:yield db
        finally:db.close()

    def rows(self,table='jobs'):
        db=sqlite3.connect(self.fixture.ledger_path.as_uri()+'?mode=ro',uri=True)
        try:
            db.row_factory=sqlite3.Row
            return [dict(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY 1,2')]
        finally:db.close()

    def cleanup(self,delete=False):
        from gardens.cleanup import cleanup_incomplete
        with contextlib.redirect_stdout(io.StringIO()):
            return cleanup_incomplete(self.out,delete=delete)

    def row(self,sid,vid):
        return next(row for row in self.rows() if row['scene_id']==sid and row['variant_id']==vid)

    def folder(self,sid,vid):return Path(json.loads(self.row(sid,vid)['definition'])['destination'])/'incomplete'

    def stage(self,sid,vid):
        folder=self.folder(sid,vid);folder.mkdir(parents=True,exist_ok=True)
        for name,color in zip(CANONICAL,('green','magenta','cyan')):
            Image.new('RGBA',(4,3),color).save(folder/name)
        return [folder/name for name in CANONICAL]

    def file_snapshot(self,paths):
        return {str(path):(path.read_bytes(),path.stat().st_mtime_ns) for path in paths}

    def assert_files(self,snapshot):
        for filename,metadata in snapshot.items():
            path=Path(filename);self.assertEqual((path.read_bytes(),path.stat().st_mtime_ns),metadata)

    def originals(self):
        rows=self.rows();scenes=self.rows('scenes')
        files=[path for path in (self.out/'scenes').rglob('*')
               if path.is_file() and 'incomplete' not in path.relative_to(self.out/'scenes').parts]
        files.append(self.fixture.ledger_path)
        return rows,scenes,self.file_snapshot(files)

    def assert_originals(self,snapshot):
        rows,scenes,files=snapshot
        self.assertEqual(self.rows(),rows);self.assertEqual(self.rows('scenes'),scenes)
        self.assert_files(files)

    def change_result(self,sid,vid,change):
        with self.connection() as db:
            row=db.execute('SELECT result FROM jobs WHERE scene_id=? AND variant_id=?',(sid,vid)).fetchone()
            result=json.loads(row[0]);change(result)
            db.execute('UPDATE jobs SET result=? WHERE scene_id=? AND variant_id=?',(json.dumps(result),sid,vid))

    def test_default_dry_run_keeps_all_previews_original_images_and_checkpoints(self):
        stale=[*self.stage('PassedOnly','passed'),*self.stage('PartialScene','good')]
        previews=self.file_snapshot(stale);original=self.originals()
        report=self.cleanup()
        self.assertEqual(report['status'],'planned');self.assertEqual(report['summary']['candidate_files'],6)
        self.assertEqual(report['summary']['deleted_files'],0)
        self.assertEqual(report['summary']['candidate_bytes'],sum(path.stat().st_size for path in stale))
        self.assert_files(previews);self.assert_originals(original)
        self.assertTrue(all(path.exists() for path in stale))

    def test_delete_removes_three_stale_pngs_only_from_each_verified_passed_variant(self):
        passed=[*self.stage('PassedOnly','passed'),*self.stage('PartialScene','good')]
        others=[*self.stage('PartialScene','broken'),*self.stage('FailedScene','failed'),
                *self.stage('PendingScene','pending'),*self.stage('InactiveScene','inactive')]
        unselected=self.file_snapshot(others);original=self.originals()
        bytes_to_delete=sum(path.stat().st_size for path in passed)
        report=self.cleanup(delete=True)
        self.assertEqual(report['status'],'completed');self.assertEqual(report['summary']['deleted_files'],6)
        self.assertEqual(report['summary']['deleted_bytes'],bytes_to_delete)
        self.assertTrue(all(not path.exists() for path in passed))
        self.assert_files(unselected);self.assert_originals(original)
        self.assertTrue((self.out/'scenes/PassedOnly').is_dir())
        self.assertTrue((self.out/'scenes/PartialScene/variant_good').is_dir())

    def test_repeating_deletion_is_an_idempotent_noop(self):
        stale=self.stage('PassedOnly','passed');original=self.originals()
        self.cleanup(delete=True);after=self.originals()
        report=self.cleanup(delete=True)
        self.assertEqual(report['summary']['candidate_files'],0);self.assertEqual(report['summary']['deleted_files'],0)
        self.assertTrue(all(not path.exists() for path in stale));self.assert_originals(original)
        self.assertEqual(after[0],self.rows());self.assert_files(after[2])

    def test_corrupt_or_missing_passing_image_proof_prevents_preview_deletion(self):
        stale=[*self.stage('PassedOnly','passed'),*self.stage('PartialScene','good')]
        (self.out/'scenes/PassedOnly/scene_complete.png').write_bytes(b'corrupt complete image')
        (self.out/'scenes/PartialScene/variant_good/scene_solution.png').unlink()
        previews=self.file_snapshot(stale);original=self.originals()
        self.cleanup(delete=True)
        self.assert_files(previews);self.assert_originals(original)

    def test_passed_job_with_nonpassing_or_duplicate_role_result_cannot_authorize_deletion(self):
        stale=[*self.stage('PassedOnly','passed'),*self.stage('PartialScene','good')]
        self.change_result('PassedOnly','passed',lambda result:result.update(status='incomplete'))
        self.change_result('PartialScene','good',lambda result:result['files'].__setitem__(1,result['files'][0]))
        previews=self.file_snapshot(stale);original=self.originals()
        self.cleanup(delete=True)
        self.assert_files(previews);self.assert_originals(original)

    def test_missing_or_changed_source_sidecars_prevent_preview_deletion(self):
        stale=[*self.stage('PassedOnly','passed'),*self.stage('PartialScene','good')]
        (self.out/'scenes/PassedOnly/objects.json').unlink()
        (self.out/'scenes/PartialScene/variant_good/solution_objects.json').write_text('["changed labels"]')
        previews=self.file_snapshot(stale);original=self.originals()
        report=self.cleanup(delete=True)
        self.assertEqual(report['summary']['deleted_files'],0)
        self.assert_files(previews);self.assert_originals(original)

    def test_matching_hashes_do_not_authorize_cleanup_when_validation_report_is_incomplete(self):
        stale=self.stage('PassedOnly','passed')
        report_path=self.out/'scenes/PassedOnly/validation_report.json'
        report_path.write_text(json.dumps({'status':'incomplete','issues':['missing object'],'files':[]}))
        self.change_result('PassedOnly','passed',lambda result:result['artifacts'].update({str(report_path):sha256(report_path)}))
        previews=self.file_snapshot(stale);original=self.originals()
        report=self.cleanup(delete=True)
        self.assertEqual(report['summary']['deleted_files'],0)
        self.assert_files(previews);self.assert_originals(original)

    def test_non_png_passing_file_with_matching_hash_is_not_valid_cleanup_proof(self):
        stale=self.stage('PassedOnly','passed');source=self.out/'scenes/PassedOnly/scene_empty.png'
        source.write_bytes(b'not an actual PNG even though its recorded digest agrees')
        self.change_result('PassedOnly','passed',lambda result:result['artifacts'].update({str(source):sha256(source)}))
        previews=self.file_snapshot(stale);original=self.originals()
        report=self.cleanup(delete=True)
        self.assertEqual(report['summary']['deleted_files'],0)
        self.assert_files(previews);self.assert_originals(original)

    def test_only_direct_renderer_temporary_filenames_are_removed(self):
        stale=self.stage('PassedOnly','passed');folder=stale[0].parent
        temporary=[folder/(Path(name).stem+'.tmp') for name in CANONICAL]
        for path in temporary:path.write_bytes(b'interrupted renderer temporary file')
        unknown=folder/'my-notes.tmp';unknown.write_bytes(b'keep this user temporary file')
        nested=folder/'other/scene_empty.tmp';nested.parent.mkdir();nested.write_bytes(b'keep nested files')
        kept=self.file_snapshot([unknown,nested]);original=self.originals()
        report=self.cleanup(delete=True)
        self.assertEqual(report['summary']['deleted_files'],6)
        self.assertTrue(all(not path.exists() for path in [*stale,*temporary]))
        self.assert_files(kept);self.assert_originals(original)

    def test_unknown_files_and_nested_subdirectories_are_retained(self):
        stale=self.stage('PassedOnly','passed');folder=stale[0].parent
        notes=folder/'My Notes.txt';notes.write_bytes(b'keep user notes')
        extra=folder/'extra.png';Image.new('RGBA',(2,2),'orange').save(extra)
        nested=folder/'do-not-touch/scene_complete.png';nested.parent.mkdir();Image.new('RGBA',(2,2),'red').save(nested)
        kept=self.file_snapshot([notes,extra,nested]);original=self.originals()
        self.cleanup(delete=True)
        self.assertTrue(all(not path.exists() for path in stale))
        self.assert_files(kept);self.assertTrue(folder.is_dir());self.assert_originals(original)

    def test_incomplete_directory_symlink_cannot_delete_any_outside_files(self):
        outside=Path(self.fixture.temp.name)/'outside-previews';outside.mkdir()
        for name in CANONICAL:Image.new('RGBA',(3,2),'red').save(outside/name)
        link=self.folder('PassedOnly','passed')
        try:link.symlink_to(outside,target_is_directory=True)
        except (OSError,NotImplementedError):self.skipTest('Symlinks unavailable')
        kept=self.file_snapshot(list(outside.iterdir()));original=self.originals()
        self.cleanup(delete=True)
        self.assertTrue(link.is_symlink());self.assert_files(kept);self.assert_originals(original)

    def test_symlinked_preview_is_not_unlinked_and_its_target_stays_unchanged(self):
        stale=self.stage('PassedOnly','passed');linked=stale[0];linked.unlink()
        outside=Path(self.fixture.temp.name)/'outside.png';Image.new('RGBA',(3,2),'yellow').save(outside)
        try:linked.symlink_to(outside)
        except (OSError,NotImplementedError):self.skipTest('Symlinks unavailable')
        kept=self.file_snapshot([outside]);original=self.originals()
        self.cleanup(delete=True)
        self.assertTrue(linked.is_symlink());self.assert_files(kept);self.assert_originals(original)

    def test_saved_pass_destination_outside_scene_tree_does_not_authorize_cleanup(self):
        # A second valid passed variant is disabled to isolate the invalid proof.
        with self.connection() as db:
            db.execute("UPDATE jobs SET active=0 WHERE scene_id='PartialScene' AND variant_id='good'")
            row=db.execute("SELECT definition FROM jobs WHERE scene_id='PassedOnly'").fetchone()
            definition=json.loads(row[0]);definition['destination']=str(self.out.parent/'outside-destination')
            db.execute("UPDATE jobs SET definition=? WHERE scene_id='PassedOnly'",(json.dumps(definition),))
        outside=Path(definition['destination'])/'incomplete';outside.mkdir(parents=True)
        for name in CANONICAL:Image.new('RGBA',(3,2),'purple').save(outside/name)
        kept=self.file_snapshot(list(outside.iterdir()));original=self.originals()
        try:self.cleanup(delete=True)
        except ValueError:pass
        self.assert_files(kept);self.assert_originals(original)

    def test_external_checkpoint_change_between_plan_and_delete_refuses_all_deletions(self):
        import gardens.cleanup as cleanup
        stale=[*self.stage('PassedOnly','passed'),*self.stage('PartialScene','good')]
        previews=self.file_snapshot(stale);original=self.originals();apply=cleanup._delete_entry;changed=[]
        def change_checkpoint(db,initial_version,entry,plan):
            if not changed:
                with self.connection() as other:
                    other.execute("UPDATE jobs SET status='incomplete' WHERE scene_id='PassedOnly'")
                changed.append(True)
            return apply(db,initial_version,entry,plan)
        with patch('gardens.cleanup._delete_entry',side_effect=change_checkpoint):report=self.cleanup(delete=True)
        self.assertEqual(report['status'],'partial');self.assertEqual(report['summary']['deleted_files'],0)
        self.assert_files(previews)
        self.assert_files({name:value for name,value in original[2].items() if name!=str(self.fixture.ledger_path)})
        expected=[{**row,'status':'incomplete'} if row['scene_id']=='PassedOnly' else row for row in original[0]]
        self.assertEqual(self.rows(),expected)

    def test_changed_passing_source_after_planning_cannot_authorize_deletion(self):
        import gardens.cleanup as cleanup
        stale=self.stage('PassedOnly','passed');previews=self.file_snapshot(stale)
        original=self.originals();source=self.out/'scenes/PassedOnly/scene_complete.png'
        apply=cleanup._delete_entry;changed=[]
        def change_source(db,initial_version,entry,plan):
            if not changed:
                source.write_bytes(b'a different renderer changed the passing image')
                changed.append(True)
            return apply(db,initial_version,entry,plan)
        with patch('gardens.cleanup._delete_entry',side_effect=change_source):report=self.cleanup(delete=True)
        self.assertEqual(report['status'],'partial');self.assertEqual(report['summary']['deleted_files'],0)
        self.assert_files(previews);self.assertEqual(source.read_bytes(),b'a different renderer changed the passing image')
        self.assertEqual(self.rows(),original[0]);self.assertEqual(self.rows('scenes'),original[1])

    def test_delete_reserves_the_real_sqlite_writer_slot_during_fresh_proof_check(self):
        import gardens.cleanup as cleanup
        stale=self.stage('PassedOnly','passed');original=self.originals()
        apply=cleanup._delete_entry;verify=cleanup._verify_passed;applying=[];attempts=[]
        def reserved_delete(db,initial_version,entry,plan):
            applying.append(True)
            try:return apply(db,initial_version,entry,plan)
            finally:applying.pop()
        def verify_while_competing_writer_tries(plan):
            if applying:
                other=sqlite3.connect(self.fixture.ledger_path,timeout=0,isolation_level=None)
                try:
                    with self.assertRaises(sqlite3.OperationalError) as error:
                        other.execute("UPDATE jobs SET fingerprint=fingerprint WHERE scene_id='PassedOnly'")
                    message=str(error.exception).casefold()
                    self.assertTrue('locked' in message or 'busy' in message,message)
                    attempts.append(message)
                finally:
                    if other.in_transaction:other.execute('ROLLBACK')
                    other.close()
            return verify(plan)
        with patch('gardens.cleanup._delete_entry',side_effect=reserved_delete), \
             patch('gardens.cleanup._verify_passed',side_effect=verify_while_competing_writer_tries):
            report=self.cleanup(delete=True)
        self.assertTrue(attempts,'A competing writer must be checked inside the deletion transaction')
        self.assertEqual(report['status'],'completed');self.assertEqual(report['summary']['deleted_files'],3)
        self.assertTrue(all(not path.exists() for path in stale));self.assert_originals(original)

    def test_interrupted_deletion_can_resume_without_changing_completed_images_or_rows(self):
        stale=self.stage('PassedOnly','passed');original=self.originals();removed=[];unlink=Path.unlink
        def stop_after_one(path,*args,**kwargs):
            if path in stale and removed:raise KeyboardInterrupt
            result=unlink(path,*args,**kwargs)
            if path in stale:removed.append(path)
            return result
        with patch.object(Path,'unlink',stop_after_one):report=self.cleanup(delete=True)
        self.assertEqual(report['status'],'interrupted');self.assertEqual(len(removed),1)
        self.assertEqual(sum(path.exists() for path in stale),2);self.assert_originals(original)
        resumed=self.cleanup(delete=True)
        self.assertEqual(resumed['status'],'completed');self.assertEqual(resumed['summary']['deleted_files'],2)
        self.assertTrue(all(not path.exists() for path in stale));self.assert_originals(original)


if __name__=='__main__':unittest.main()
