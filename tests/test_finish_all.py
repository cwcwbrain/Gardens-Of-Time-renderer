"""Unattended finish runs cleanup after partial results and protects aborts."""
import contextlib
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch

import gardens_renderer
from gardens.finish import ES_CONTINUOUS,ES_SYSTEM_REQUIRED,finish_all,keep_awake
import test_cleanup_incomplete as fixtures


class FinishAllTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.CleanupIncompleteTests();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.out=self.fixture.out

    def invoke(self,update_code=0,cleanup_status='completed',*,update=None,cleanup=None,plan_only=False):
        update=update if update is not None else Mock(return_value=update_code)
        cleanup=cleanup if cleanup is not None else Mock(return_value={'status':cleanup_status,'summary':{'deleted_files':3}})
        with contextlib.redirect_stdout(io.StringIO()):
            code=finish_all(self.out,update,cleanup,plan_only=plan_only)
        report=json.loads((self.out/'finish_all_report.json').read_text())
        return code,report,update,cleanup

    def test_complete_update_then_cleanup_reports_success(self):
        code,report,update,cleanup=self.invoke()
        self.assertEqual(code,0);self.assertEqual(report['status'],'completed')
        update.assert_called_once_with();cleanup.assert_called_once_with(self.out.resolve(),delete=True)
        self.assertEqual([step['name'] for step in report['steps']],['update','cleanup'])

    def test_expected_incomplete_update_still_runs_cleanup_and_reports_partial(self):
        code,report,_,cleanup=self.invoke(update_code=1)
        self.assertEqual(code,1);self.assertEqual(report['status'],'partial')
        cleanup.assert_called_once();self.assertEqual(report['steps'][0]['status'],'partial')
        self.assertEqual(report['steps'][1]['status'],'completed')

    def test_fatal_or_interrupted_update_never_deletes_previews(self):
        for update_code in (2,130):
            with self.subTest(code=update_code):
                code,report,_,cleanup=self.invoke(update_code=update_code)
                self.assertEqual(code,update_code);cleanup.assert_not_called()
                self.assertEqual(report['steps'][1]['status'],'skipped')

    def test_update_exceptions_and_keyboard_interrupt_skip_cleanup(self):
        for error,expected in ((ValueError('broken checkpoint'),2),(KeyboardInterrupt(),130)):
            with self.subTest(error=type(error).__name__):
                code,report,_,cleanup=self.invoke(update=Mock(side_effect=error))
                self.assertEqual(code,expected);cleanup.assert_not_called()
                self.assertEqual(report['exit_code'],expected)

    def test_plan_only_never_deletes_any_preview(self):
        stale=self.fixture.stage('PassedOnly','passed');before=self.fixture.file_snapshot(stale)
        code,report,update,cleanup=self.invoke(plan_only=True)
        self.assertEqual(code,0);self.assertEqual(report['status'],'planned')
        update.assert_called_once();cleanup.assert_not_called();self.fixture.assert_files(before)

    def test_cleanup_partial_interruption_and_error_remain_visible(self):
        for status,expected in (('partial',1),('interrupted',130)):
            with self.subTest(status=status):
                code,report,_,_=self.invoke(cleanup_status=status)
                self.assertEqual(code,expected);self.assertEqual(report['status'],status)
        code,report,_,_=self.invoke(cleanup=Mock(side_effect=OSError('storage unavailable')))
        self.assertEqual(code,2);self.assertEqual(report['steps'][1]['status'],'failed')

    def test_actual_cleanup_after_partial_update_preserves_completed_and_incomplete_versions(self):
        stale=self.fixture.stage('PassedOnly','passed')
        incomplete=self.fixture.stage('PartialScene','broken')
        original=self.fixture.originals();kept=self.fixture.file_snapshot(incomplete)
        with contextlib.redirect_stdout(io.StringIO()):
            code=finish_all(self.out,lambda:1)
        report=json.loads((self.out/'finish_all_report.json').read_text())
        self.assertEqual(code,1);self.assertEqual(report['steps'][1]['summary']['deleted_files'],3)
        self.assertTrue(all(not path.exists() for path in stale))
        self.fixture.assert_files(kept);self.fixture.assert_originals(original)
        with contextlib.redirect_stdout(io.StringIO()):finish_all(self.out,lambda:1)
        self.fixture.assert_files(kept);self.fixture.assert_originals(original)

    def test_cli_forwards_update_pending_and_custom_options_once_without_full_test_mode(self):
        original=gardens_renderer.main
        with patch('gardens_renderer.main',return_value=1) as update, \
             patch('gardens.cleanup.cleanup_incomplete',return_value={'status':'completed','summary':{}}) as cleanup, \
             contextlib.redirect_stdout(io.StringIO()):
            code=original(['--finish-all','--output',str(self.out),'--offline','--timeout','11'])
        self.assertEqual(code,1);update.assert_called_once();cleanup.assert_called_once()
        forwarded=update.call_args.args[0]
        self.assertNotIn('--finish-all',forwarded);self.assertNotIn('--test-all',forwarded)
        self.assertEqual(forwarded.count('--update'),1);self.assertEqual(forwarded.count('--include-pending'),1)
        self.assertIn('--offline',forwarded);self.assertEqual(forwarded[forwarded.index('--timeout')+1],'11')

    def test_cli_plan_only_forwards_preview_and_skips_cleanup(self):
        original=gardens_renderer.main
        with patch('gardens_renderer.main',return_value=0) as update, \
             patch('gardens.cleanup.cleanup_incomplete',side_effect=AssertionError('plan must not delete')), \
             contextlib.redirect_stdout(io.StringIO()):
            code=original(['--finish-all','--output',str(self.out),'--plan-only','--include-pending'])
        self.assertEqual(code,0)
        self.assertEqual(update.call_args.args[0].count('--include-pending'),1)
        self.assertIn('--plan-only',update.call_args.args[0])

    def test_windows_keep_awake_restores_the_previous_state_even_on_exception(self):
        api=Mock(side_effect=[ES_CONTINUOUS,ES_CONTINUOUS|ES_SYSTEM_REQUIRED])
        windows=SimpleNamespace(kernel32=SimpleNamespace(SetThreadExecutionState=api))
        with patch('gardens.finish.sys.platform','win32'),patch('gardens.finish.ctypes.windll',windows,create=True):
            with self.assertRaises(ValueError):
                with keep_awake() as state:
                    self.assertTrue(state['active']);raise ValueError('stop')
        self.assertEqual(api.call_args_list[0].args,(ES_CONTINUOUS|ES_SYSTEM_REQUIRED,))
        self.assertEqual(api.call_args_list[1].args,(ES_CONTINUOUS,))
        self.assertFalse(state['active']);self.assertTrue(state['restored'])

    def test_windows_keep_awake_failure_is_reported_without_masking_work(self):
        api=Mock(return_value=0);windows=SimpleNamespace(kernel32=SimpleNamespace(SetThreadExecutionState=api))
        with patch('gardens.finish.sys.platform','win32'),patch('gardens.finish.ctypes.windll',windows,create=True), \
             contextlib.redirect_stdout(io.StringIO()):
            with keep_awake() as state:self.assertFalse(state['active'])
        self.assertIn('did not accept',state['warning']);self.assertEqual(api.call_count,1)


if __name__=='__main__':unittest.main()
