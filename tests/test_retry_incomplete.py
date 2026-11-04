"""Incomplete-only retries preserve the finished run's other scene variants."""
import contextlib
import csv
import io
import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

import gardens.batch as batch
import gardens_renderer
from gardens.download import sha256
from gardens.scenes import Scene


class RetryIncompleteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name)/'output'
        self.ledger_path=self.out/'batch/tests.sqlite3'
        self.args=SimpleNamespace(output=self.out,frame=None,offline=True,timeout=2,retries=0,
                                  test_timeout=3,test_limit=None,plan_only=False,retry_failed=False,
                                  retry_incomplete=True)
        self.calls=[]
        # A passing sibling must survive retrying only the incomplete variant.
        entries=[('PartialScene','good','passed',1,1),('PartialScene','broken','incomplete',1,1),
                 ('FailedScene','failed','failed',2,1),('PendingScene','pending','pending',3,1),
                 ('RunningScene','running','running',4,1),('BlockedScene','blocked','blocked',5,1),
                 ('IncompleteLater','later','incomplete',6,1),('InactiveScene','inactive','incomplete',7,0),
                 ('PassedOnly','passed','passed',8,1)]
        ledger=batch.Ledger(self.ledger_path)
        try:
            for sid,vid,status,order,active in entries:
                destination=self.out/'scenes'/sid
                if sid=='PartialScene':destination=destination/('variant_'+vid)
                destination.mkdir(parents=True,exist_ok=True)
                (destination/'preserve-me.txt').write_text(sid+' '+vid)
                result={'status':status,'issues':[] if status=='passed' else ['old '+status],
                        'files':[],'stage':'completed','old_provenance':'original renderer'}
                if status=='passed':result.update(self.images(destination,'red'))
                old_fp='old-fingerprint-'+sid+'-'+vid
                url='https://assets.test/'+vid+'.bundle'
                definition={'scene':asdict(Scene(order,sid,'','https://assets.test/'+sid+'.json',[])),
                            'candidate':{'roots':[vid],'issues':[],'bundles':[{'url':url,'internal_id':url,'options':{}}]},
                            'destination':str(destination),'output':str(self.out),'frame':None,
                            'offline':True,'timeout':2,'retries':0,'fingerprint':old_fp}
                ledger.db.execute('INSERT OR REPLACE INTO scenes VALUES (?,?,?,?,?)',
                                  (sid,order,'ambiguous' if sid=='PartialScene' else 'matched','[]',active))
                ledger.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)',
                                  (sid,vid,order,old_fp,status,json.dumps(definition),json.dumps(result),active))
            ledger.db.commit()
        finally:ledger.close()

    def images(self,destination,color):
        destination.mkdir(parents=True,exist_ok=True)
        files=[];artifacts={}
        for name in ('scene_empty.png','scene_complete.png','scene_solution.png'):
            path=destination/name;Image.new('RGBA',(2,2),color).save(path)
            files.append(str(path));artifacts[str(path.resolve())]=sha256(path)
        return {'files':files,'artifacts':artifacts}

    def rows(self,table='jobs'):
        with sqlite3.connect(self.ledger_path) as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for r in db.execute('SELECT * FROM '+table+' ORDER BY 1,2')]

    def preserved_rows(self):
        return [r for r in self.rows() if not (r['active'] and r['status']=='incomplete')]

    def preserved_files(self):
        selected=[]
        for row in self.rows():
            if row['active'] and row['status']=='incomplete':
                selected.append(Path(json.loads(row['definition'])['destination']))
        return {str(p):(p.read_bytes(),p.stat().st_mtime_ns) for p in (self.out/'scenes').rglob('*')
                if p.is_file() and not any(p.is_relative_to(root) for root in selected)}

    def runner(self,job,*args):
        self.calls.append(job)
        return {'status':'passed','issues':[],'stage':'completed',
                **self.images(Path(job['destination']),'blue')}

    def retry(self,runner=None):
        with contextlib.redirect_stdout(io.StringIO()):
            code=batch.retry_incomplete(self.args,runner=runner or self.runner)
        return code,json.loads((self.out/'all_scene_test_summary.json').read_text())

    def test_changed_renderer_preserves_other_rows_outputs_and_partial_sibling(self):
        before=self.preserved_rows();files=self.preserved_files();scenes=self.rows('scenes')
        with patch('gardens.batch.code_identity',return_value={'code':'new renderer','packages':{'UnityPy':'test','Pillow':'test'}}):
            code,summary=self.retry()
        self.assertEqual(code,0)
        self.assertEqual([(j['scene']['scene_id'],j['candidate']['roots'][0]) for j in self.calls],
                         [('PartialScene','broken'),('IncompleteLater','later')])
        after={(r['scene_id'],r['variant_id']):r for r in self.rows()}
        for row in before:self.assertEqual(after[(row['scene_id'],row['variant_id'])],row)
        self.assertEqual(self.rows('scenes'),scenes)
        for path,metadata in files.items():
            p=Path(path);self.assertEqual((p.read_bytes(),p.stat().st_mtime_ns),metadata)
        self.assertEqual(summary['retry_scope'],'incomplete_only')
        self.assertEqual(summary['selected_variants'],2)
        self.assertEqual(summary['preserved_variants'],6)
        self.assertEqual(summary['preserved_passed_variants'],2)
        for job in self.calls:self.assertFalse(job['fingerprint'].startswith('old-fingerprint-'))

    def test_plan_only_preserves_every_checkpoint_row_and_scene_file(self):
        self.args.plan_only=True
        before=self.rows();scenes=self.rows('scenes')
        files={str(p):(p.read_bytes(),p.stat().st_mtime_ns) for p in (self.out/'scenes').rglob('*') if p.is_file()}
        code,summary=self.retry()
        self.assertEqual(code,0);self.assertEqual(self.calls,[])
        self.assertEqual(self.rows(),before);self.assertEqual(self.rows('scenes'),scenes)
        self.assertEqual(summary['status'],'planned');self.assertEqual(summary['selected_variants'],2)
        self.assertEqual(summary['attempted_variants'],0);self.assertEqual(summary['remaining_selected_variants'],2)
        for path,metadata in files.items():
            p=Path(path);self.assertEqual((p.read_bytes(),p.stat().st_mtime_ns),metadata)

    def assert_bad_destination_rejected_without_changes(self,destination,reason):
        # The invalid job is second: preflight must finish before even the first
        # eligible variant can start or aggregate reports can be replaced.
        with sqlite3.connect(self.ledger_path) as db:
            row=db.execute("SELECT definition FROM jobs WHERE scene_id='IncompleteLater'").fetchone()
            definition=json.loads(row[0]);definition['destination']=str(destination)
            db.execute("UPDATE jobs SET definition=? WHERE scene_id='IncompleteLater'",(json.dumps(definition),))
        for name in ('summary.json','all_scene_test_summary.json','test_report.csv','scene_test_report.csv'):
            (self.out/name).write_bytes(b'previous report must stay untouched')
        before=self.rows();scenes=self.rows('scenes')
        files={str(p):(p.read_bytes(),p.stat().st_mtime_ns) for p in self.out.rglob('*')
               if p.is_file() and p.parent!=self.ledger_path.parent}
        with self.assertRaisesRegex(ValueError,reason):self.retry()
        self.assertEqual(self.calls,[]);self.assertEqual(self.rows(),before);self.assertEqual(self.rows('scenes'),scenes)
        for path,metadata in files.items():
            p=Path(path);self.assertEqual((p.read_bytes(),p.stat().st_mtime_ns),metadata)
        self.assertFalse(list(self.ledger_path.parent.glob('*.job.json')))

    def test_outside_output_destination_fails_preflight_without_changes(self):
        destination=Path(self.temp.name)/'outside-output'
        self.assert_bad_destination_rejected_without_changes(destination,'outside')
        self.assertFalse(destination.exists())

    def test_overlapping_passed_destination_fails_preflight_without_changes(self):
        self.assert_bad_destination_rejected_without_changes(self.out/'scenes/PartialScene/variant_good','overlaps')

    def test_csv_keeps_historical_pass_fingerprint_and_updates_selected_provenance(self):
        before={(r['scene_id'],r['variant_id']):r for r in self.rows()}
        identity={'code':'new renderer','packages':{'UnityPy':'test','Pillow':'test'}}
        with patch('gardens.batch.code_identity',return_value=identity):code,summary=self.retry()
        self.assertEqual(code,0)
        with (self.out/'test_report.csv').open(encoding='utf-8-sig',newline='') as stream:
            rows={(r['scene_id'],r['variant_id']):r for r in csv.DictReader(stream)}
        passed=rows[('PartialScene','good')];selected=rows[('PartialScene','broken')]
        self.assertEqual(passed['validation_fingerprint'],before[('PartialScene','good')]['fingerprint'])
        self.assertEqual(passed['selected_for_retry'],'False')
        self.assertNotEqual(selected['validation_fingerprint'],before[('PartialScene','broken')]['fingerprint'])
        current=next(r for r in self.rows() if r['scene_id']=='PartialScene' and r['variant_id']=='broken')
        self.assertEqual(selected['validation_fingerprint'],current['fingerprint'])
        self.assertEqual(selected['selected_for_retry'],'True')
        self.assertEqual(summary['renderer_identity'],identity)
        self.assertTrue(any('prior passes are not revalidated' in item for item in summary['limitations']))

    def test_missing_or_corrupt_preserved_images_are_not_repaired_or_rehashed(self):
        good=self.out/'scenes/PartialScene/variant_good/scene_complete.png'
        good.write_bytes(b'leave this changed image alone')
        missing=self.out/'scenes/PassedOnly/scene_solution.png';missing.unlink()
        before=self.preserved_rows();files=self.preserved_files()
        with patch('gardens.batch.reusable',side_effect=AssertionError('must not revalidate preserved passes')):
            code,_=self.retry()
        self.assertEqual(code,0);self.assertFalse(missing.exists())
        self.assertEqual(good.read_bytes(),b'leave this changed image alone')
        after={(r['scene_id'],r['variant_id']):r for r in self.rows()}
        for row in before:self.assertEqual(after[(row['scene_id'],row['variant_id'])],row)
        for path,metadata in files.items():
            p=Path(path);self.assertEqual((p.read_bytes(),p.stat().st_mtime_ns),metadata)

    def test_limit_leaves_unprocessed_incomplete_rows_ready_for_same_command(self):
        original=next(r for r in self.rows() if r['scene_id']=='IncompleteLater')
        self.args.test_limit=1
        code,summary=self.retry()
        self.assertEqual(code,1);self.assertEqual(len(self.calls),1)
        self.assertEqual(next(r for r in self.rows() if r['scene_id']=='IncompleteLater'),original)
        self.assertEqual(summary['selected_variants'],2)
        self.args.test_limit=None;self.calls.clear()
        code,_=self.retry()
        self.assertEqual(code,0)
        self.assertEqual([j['scene']['scene_id'] for j in self.calls],['IncompleteLater'])

    def test_interruption_keeps_unprocessed_incomplete_proof_and_preserved_passes(self):
        original=next(r for r in self.rows() if r['scene_id']=='IncompleteLater')
        preserved=self.preserved_rows()
        def interrupt(job,*args):
            if self.calls:raise KeyboardInterrupt
            return self.runner(job,*args)
        code,_=self.retry(interrupt)
        self.assertEqual(code,130)
        self.assertEqual(next(r for r in self.rows() if r['scene_id']=='IncompleteLater'),original)
        after={(r['scene_id'],r['variant_id']):r for r in self.rows()}
        for row in preserved:self.assertEqual(after[(row['scene_id'],row['variant_id'])],row)
        self.calls.clear();code,_=self.retry()
        self.assertEqual(code,0);self.assertEqual([j['scene']['scene_id'] for j in self.calls],['IncompleteLater'])

    def test_zero_incomplete_is_a_noop_despite_other_pending_or_failed_jobs(self):
        with sqlite3.connect(self.ledger_path) as db:
            db.execute("UPDATE jobs SET status='failed' WHERE active=1 AND status='incomplete'")
        before=self.rows();files=self.preserved_files()
        code,summary=self.retry()
        self.assertEqual(code,0);self.assertEqual(self.calls,[])
        self.assertEqual(self.rows(),before);self.assertEqual(summary['selected_variants'],0)
        self.assertEqual(summary['preserved_variants'],8)
        for path,metadata in files.items():
            p=Path(path);self.assertEqual((p.read_bytes(),p.stat().st_mtime_ns),metadata)

    def test_missing_ledger_does_not_create_a_new_run(self):
        self.args.output=Path(self.temp.name)/'not-a-previous-run'
        with self.assertRaisesRegex(ValueError,'checkpoint|ledger|tests.sqlite3'):
            self.retry()
        self.assertFalse(self.args.output.exists());self.assertEqual(self.calls,[])

    def test_nonpassing_retry_returns_one_without_touching_other_statuses(self):
        preserved=self.preserved_rows()
        def incomplete(job,*args):
            self.calls.append(job);return {'status':'incomplete','issues':['still unresolved'],'files':[]}
        code,_=self.retry(incomplete)
        self.assertEqual(code,1);self.assertEqual(len(self.calls),2)
        after={(r['scene_id'],r['variant_id']):r for r in self.rows()}
        for row in preserved:self.assertEqual(after[(row['scene_id'],row['variant_id'])],row)

    def test_cli_dispatch_bypasses_scene_and_catalogue_inputs_and_discovery(self):
        with patch('gardens.batch.retry_incomplete',return_value=0) as retry, \
             patch('gardens_renderer.read_scenes',side_effect=AssertionError('must not read spreadsheet')), \
             patch('gardens_renderer.Downloader',side_effect=AssertionError('must not discover/download catalogues')), \
             patch('gardens.discovery.catalogue_sources',side_effect=AssertionError('must not discover catalogues')):
            code=gardens_renderer.main(['--retry-incomplete','--output',str(self.out)])
        self.assertEqual(code,0);retry.assert_called_once()

    def test_cli_retry_selector_cannot_be_combined_with_all_scene_run(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as raised:
            gardens_renderer.main(['--retry-incomplete','--test-all','--output',str(self.out)])
        self.assertEqual(raised.exception.code,2)


if __name__=='__main__':unittest.main()
