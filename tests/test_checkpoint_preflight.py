"""Checkpoint I/O and relocation failures stop before discovery or scene writes."""
import contextlib
import io
import json
import shutil
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

import gardens_renderer
import test_retry_incomplete as retry_fixtures


class CheckpointPreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture=retry_fixtures.RetryIncompleteTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.out=self.fixture.out

    def require(self,out=None,**kwargs):
        from gardens.checkpoint import require_checkpoint
        return require_checkpoint(self.out if out is None else out,**kwargs)

    def snapshot(self,root):
        return {str(path.relative_to(root)):(path.read_bytes(),path.stat().st_mtime_ns)
                for path in root.rglob('*') if path.is_file()}

    def test_current_valid_checkpoint_is_allowed_without_changing_rows_or_scene_files(self):
        before=self.fixture.rows();scenes=self.fixture.rows('scenes')
        files=self.snapshot(self.out/'scenes')
        result=self.require()
        self.assertEqual(result,self.fixture.ledger_path.resolve())
        self.assertEqual(self.fixture.rows(),before);self.assertEqual(self.fixture.rows('scenes'),scenes)
        self.assertEqual(self.snapshot(self.out/'scenes'),files)

    def test_readonly_preflight_allows_the_same_saved_root_without_mutation(self):
        before=self.fixture.rows();files=self.snapshot(self.out/'scenes')
        self.assertEqual(self.require(writable=False),self.fixture.ledger_path.resolve())
        self.assertEqual(self.fixture.rows(),before);self.assertEqual(self.snapshot(self.out/'scenes'),files)

    def test_copied_output_requires_relocation_and_keeps_original_and_copy_untouched(self):
        moved=self.out.parent/'desktop-copy';shutil.copytree(self.out,moved)
        before=self.snapshot(moved);original=self.snapshot(self.out)
        with self.assertRaisesRegex(ValueError,'--relocate-output'):
            self.require(moved)
        self.assertEqual(self.snapshot(moved),before);self.assertEqual(self.snapshot(self.out),original)

    def test_missing_checkpoint_does_not_create_an_empty_ledger_or_output_folder(self):
        missing=self.out.parent/'missing-run'
        with self.assertRaisesRegex(ValueError,'checkpoint|tests.sqlite3'):
            self.require(missing)
        self.assertFalse(missing.exists())

    def test_sqlite_disk_io_error_is_actionable_and_preserves_checkpoint(self):
        before=self.fixture.ledger_path.read_bytes();files=self.snapshot(self.out/'scenes')
        with patch('gardens.checkpoint.sqlite3.connect',side_effect=sqlite3.OperationalError('disk I/O error')):
            with self.assertRaises(ValueError) as error:self.require()
        message=str(error.exception).casefold()
        self.assertIn('onedrive',message);self.assertIn('checkpoint',message)
        self.assertTrue(any(word in message for word in ('preserv','keep','do not delete','don’t delete','don\'t delete')),message)
        self.assertEqual(self.fixture.ledger_path.read_bytes(),before);self.assertEqual(self.snapshot(self.out/'scenes'),files)

    def test_writable_probe_rejects_a_readonly_database_connection_without_changes(self):
        before=self.fixture.rows();files=self.snapshot(self.out/'scenes')
        readonly=sqlite3.connect(self.fixture.ledger_path.as_uri()+'?mode=ro',uri=True,isolation_level=None)
        with patch('gardens.checkpoint.sqlite3.connect',return_value=readonly):
            with self.assertRaises(ValueError) as error:self.require(writable=True)
        message=str(error.exception).casefold()
        self.assertTrue('readonly' in message or 'read-only' in message,message)
        self.assertIn('write access',message)
        self.assertEqual(self.fixture.rows(),before);self.assertEqual(self.snapshot(self.out/'scenes'),files)

    def test_ledger_initialization_io_error_closes_connection_and_uses_storage_guidance(self):
        from gardens.batch import Ledger
        before=self.fixture.rows();files=self.snapshot(self.out/'scenes')
        connection=Mock();connection.execute.side_effect=sqlite3.OperationalError('disk I/O error')
        with patch('gardens.batch.sqlite3.connect',return_value=connection):
            with self.assertRaises(ValueError) as error:Ledger(self.fixture.ledger_path)
        connection.close.assert_called_once()
        self.assertIn('disk I/O error',str(error.exception));self.assertIn('OneDrive',str(error.exception))
        self.assertIn('do not delete the checkpoint',str(error.exception))
        self.assertEqual(self.fixture.rows(),before);self.assertEqual(self.snapshot(self.out/'scenes'),files)

    def test_relocation_io_failure_preserves_copy_and_reports_actionable_storage_error(self):
        from gardens.relocation import relocate_output
        moved=self.out.parent/'relocation-copy';shutil.copytree(self.out,moved)
        before=self.snapshot(moved)
        with patch('gardens.relocation.sqlite3.connect',side_effect=sqlite3.OperationalError('disk I/O error')):
            with self.assertRaises(ValueError) as error:relocate_output(moved)
        message=str(error.exception)
        self.assertIn('relocate copied checkpoint',message);self.assertIn('OneDrive',message)
        self.assertIn('do not delete the checkpoint',message)
        self.assertEqual(self.snapshot(moved),before)

    def test_locked_database_is_reported_without_mutating_progress(self):
        before=self.fixture.rows();files=self.snapshot(self.out/'scenes')
        lock=sqlite3.connect(self.fixture.ledger_path,timeout=0,isolation_level=None)
        lock.execute('BEGIN IMMEDIATE')
        try:
            with self.assertRaises(ValueError) as error:self.require()
            message=str(error.exception).casefold()
            self.assertTrue('locked' in message or 'busy' in message,message)
            self.assertIn('stop other renderer commands',message)
        finally:
            lock.execute('ROLLBACK');lock.close()
        self.assertEqual(self.fixture.rows(),before);self.assertEqual(self.snapshot(self.out/'scenes'),files)

    def test_invalid_sqlite_bytes_are_reported_without_replacing_the_file(self):
        broken=self.out.parent/'broken-run';folder=broken/'batch';folder.mkdir(parents=True)
        checkpoint=folder/'tests.sqlite3';checkpoint.write_bytes(b'not a SQLite database; keep these bytes')
        before=self.snapshot(broken)
        with self.assertRaisesRegex(ValueError,'checkpoint|SQLite|database'):
            self.require(broken)
        self.assertEqual(self.snapshot(broken),before)

    def test_wrong_schema_is_rejected_without_creating_missing_tables(self):
        broken=self.out.parent/'wrong-schema';folder=broken/'batch';folder.mkdir(parents=True)
        checkpoint=folder/'tests.sqlite3'
        with sqlite3.connect(checkpoint) as db:db.execute('CREATE TABLE unrelated (value TEXT)')
        before=self.snapshot(broken)
        with self.assertRaisesRegex(ValueError,'schema|checkpoint|table'):
            self.require(broken)
        self.assertEqual(self.snapshot(broken),before)
        with sqlite3.connect(checkpoint) as db:
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables,{'unrelated'})

    def test_relative_saved_output_is_rejected_without_rewriting_definition(self):
        with sqlite3.connect(self.fixture.ledger_path) as db:
            row=db.execute("SELECT definition FROM jobs WHERE scene_id='PassedOnly'").fetchone()
            definition=json.loads(row[0]);definition['output']='relative/output'
            db.execute("UPDATE jobs SET definition=? WHERE scene_id='PassedOnly'",(json.dumps(definition),))
        before=self.fixture.rows();files=self.snapshot(self.out/'scenes')
        with self.assertRaisesRegex(ValueError,'relative|absolute|relocat|saved|root'):
            self.require()
        self.assertEqual(self.fixture.rows(),before);self.assertEqual(self.snapshot(self.out/'scenes'),files)

    def test_saved_destination_outside_scene_root_is_rejected_without_changes(self):
        with sqlite3.connect(self.fixture.ledger_path) as db:
            row=db.execute("SELECT definition FROM jobs WHERE scene_id='IncompleteLater'").fetchone()
            definition=json.loads(row[0]);definition['destination']=str(self.out.parent/'outside-scenes')
            db.execute("UPDATE jobs SET definition=? WHERE scene_id='IncompleteLater'",(json.dumps(definition),))
        before=self.fixture.rows();files=self.snapshot(self.out/'scenes')
        with self.assertRaisesRegex(ValueError,'destination|outside|scene'):
            self.require()
        self.assertEqual(self.fixture.rows(),before);self.assertEqual(self.snapshot(self.out/'scenes'),files)
        self.assertFalse((self.out.parent/'outside-scenes').exists())

    def test_inactive_job_with_old_root_is_not_hidden_from_preflight(self):
        old=self.out.parent/'old-machine-output'
        with sqlite3.connect(self.fixture.ledger_path) as db:
            row=db.execute("SELECT definition,active FROM jobs WHERE scene_id='InactiveScene'").fetchone()
            self.assertEqual(row[1],0)
            definition=json.loads(row[0]);definition['output']=str(old);definition['destination']=str(old/'scenes/InactiveScene')
            db.execute("UPDATE jobs SET definition=? WHERE scene_id='InactiveScene'",(json.dumps(definition),))
        before=self.fixture.rows();files=self.snapshot(self.out/'scenes')
        with self.assertRaisesRegex(ValueError,'--relocate-output'):self.require()
        self.assertEqual(self.fixture.rows(),before);self.assertEqual(self.snapshot(self.out/'scenes'),files)
        self.assertFalse(old.exists())

    def test_direct_enrollment_cannot_mix_a_new_root_into_an_unrelocated_copy(self):
        from dataclasses import asdict
        from gardens.incremental import enroll_updates
        from gardens.scenes import Scene
        moved=self.out.parent/'enroll-copy';shutil.copytree(self.out,moved)
        scene=Scene(100,'BrandNewChapterScene','','https://assets.test/new-items.json',[])
        url='https://assets.test/new.bundle'
        candidate={'roots':['new-root'],'issues':[],'bundles':[{'url':url,'internal_id':url,'options':{}}]}
        matches=[{**asdict(scene),'status':'matched','issues':[],'candidates':[candidate]}]
        before=self.snapshot(moved)
        with self.assertRaisesRegex(ValueError,'--relocate-output'):
            enroll_updates(moved,[scene],matches,complete=True)
        self.assertEqual(self.snapshot(moved),before)
        with sqlite3.connect(moved/'batch/tests.sqlite3') as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE scene_id='BrandNewChapterScene'").fetchone()[0],0)

    def test_cli_rejects_moved_checkpoint_before_downloads_enrollment_or_rendering(self):
        moved=self.out.parent/'moved';shutil.copytree(self.out,moved)
        before=self.snapshot(moved)
        for selector in ('--update','--export-named','--retry-incomplete'):
            with self.subTest(selector=selector),contextlib.redirect_stderr(io.StringIO()), \
                 patch('gardens_renderer.Downloader',side_effect=AssertionError('must not start downloads')), \
                 patch('gardens_renderer.read_scenes',side_effect=AssertionError('must not read scene CSV')), \
                 patch('gardens.discovery.catalogue_sources',side_effect=AssertionError('must not discover catalogues')), \
                 patch('gardens.batch.retry_incomplete',side_effect=AssertionError('must not schedule workers')):
                try:gardens_renderer.main([selector,'--output',str(moved)])
                except ValueError as error:self.assertIn('--relocate-output',str(error))
                except SystemExit as error:self.assertEqual(error.code,2)
                else:self.fail('Moved checkpoint was accepted without relocation')
        self.assertEqual(self.snapshot(moved),before)


if __name__=='__main__':unittest.main()
