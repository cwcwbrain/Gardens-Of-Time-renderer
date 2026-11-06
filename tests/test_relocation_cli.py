"""Desktop move commands stay offline and opt in to unfinished work explicitly."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gardens_renderer
import test_update_cli as fixtures


class RelocationCLITests(unittest.TestCase):
    def test_relocation_and_preview_dispatch_before_any_source_or_catalogue_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            for extra,dry in (([],False),(['--plan-only'],True)):
                with patch('gardens.relocation.relocate_output',return_value={'status':'planned' if dry else 'relocated'}) as move, \
                     patch('gardens_renderer.read_scenes',side_effect=AssertionError('no spreadsheet needed')), \
                     patch('gardens_renderer.Downloader',side_effect=AssertionError('no network needed')), \
                     contextlib.redirect_stdout(io.StringIO()):
                    result=gardens_renderer.main(['--relocate-output','--output',folder,*extra])
                self.assertEqual(result,0)
                move.assert_called_once_with(Path(folder),dry_run=dry)

    def test_pending_requires_explicit_update_mode(self):
        for mode in ('--test-all','--retry-incomplete','--relocate-output','--export-named'):
            with self.subTest(mode=mode),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as raised:
                gardens_renderer.main([mode,'--include-pending'])
            self.assertEqual(raised.exception.code,2)

    def test_update_forwards_pending_opt_in_without_full_roster_reset(self):
        fixture=fixtures.UpdateCLITests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        code,used=fixture.invoke(extra=['--include-pending'])
        self.assertEqual(code,0)
        self.assertTrue(used.retry.call_args.args[0].include_pending)

    def test_relocation_cannot_be_used_as_a_render_command(self):
        for option in ('--render','--index-only','--retry-failed'):
            with self.subTest(option=option),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as raised:
                gardens_renderer.main(['--relocate-output',option])
            self.assertEqual(raised.exception.code,2)


if __name__=='__main__':unittest.main()
