"""Solution upgrades happen after workers and before named exports."""
import contextlib
import io
import unittest
from unittest.mock import patch

import gardens_renderer
import gardens.batch
import test_update_cli as fixtures


class SolutionRefreshCLITests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.UpdateCLITests();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_refresh_runs_after_workers_and_before_the_single_named_export(self):
        def refresh(out,timeout):
            self.assertTrue(gardens.batch.retry_incomplete.called)
            self.assertFalse(gardens_renderer.name_scene_exports.called)
            self.assertEqual(out,self.fixture.out.resolve())
            self.assertEqual(timeout,300)
            return {'status':'completed','summary':{'refreshed':1}}
        with patch('gardens.solution_refresh.refresh_solutions',side_effect=refresh) as used:
            code,workflow=self.fixture.invoke(extra=['--refresh-solutions'])
        self.assertEqual(code,0);used.assert_called_once();workflow.naming.assert_called_once()

    def test_partial_refresh_still_exports_valid_named_results_and_returns_partial(self):
        with patch('gardens.solution_refresh.refresh_solutions',return_value={'status':'partial'}) as used:
            code,workflow=self.fixture.invoke(extra=['--refresh-solutions'])
        self.assertEqual(code,1);used.assert_called_once();workflow.naming.assert_called_once()

    def test_interrupted_refresh_stops_before_named_export(self):
        with patch('gardens.solution_refresh.refresh_solutions',return_value={'status':'interrupted'}):
            code,workflow=self.fixture.invoke(extra=['--refresh-solutions'])
        self.assertEqual(code,130);workflow.naming.assert_not_called()

    def test_plan_interrupted_worker_and_absent_flag_do_not_refresh(self):
        for flags,worker_code in ((['--refresh-solutions','--plan-only'],0),
                                  (['--refresh-solutions'],130),([],0)):
            with self.subTest(flags=flags,worker_code=worker_code), \
                 patch('gardens.solution_refresh.refresh_solutions',side_effect=AssertionError('must not refresh')) as used:
                self.fixture.invoke(extra=flags,retry_return=worker_code)
                used.assert_not_called()

    def test_refresh_flag_requires_update_or_finish_all(self):
        for selector in ('--all','--test-all','--retry-incomplete','--export-named'):
            with self.subTest(selector=selector),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
                gardens_renderer.main([selector,'--refresh-solutions','--output',str(self.fixture.out)])
            self.assertEqual(error.exception.code,2)

    def test_finish_all_forwards_refresh_flag_to_update(self):
        with patch('gardens.finish.finish_all',return_value=0) as finish:
            code=gardens_renderer.main(['--finish-all','--refresh-solutions','--output',str(self.fixture.out)])
        self.assertEqual(code,0)
        nested=finish.call_args.args[1]
        with patch('gardens_renderer.main',return_value=0) as update:
            nested()
        forwarded=update.call_args.args[0]
        self.assertIn('--update',forwarded);self.assertIn('--refresh-solutions',forwarded)
        self.assertIn('--include-pending',forwarded);self.assertNotIn('--finish-all',forwarded)


if __name__=='__main__':unittest.main()
