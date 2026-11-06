"""Check an existing run before downloads or changes to its saved roster."""
import json
import re
import sqlite3
from pathlib import Path, PurePosixPath, PureWindowsPath


def stored_path(value, flavor=None):
    if not isinstance(value, str) or not value or '\0' in value:
        raise ValueError('Checkpoint contains an invalid local path')
    windows = bool(re.match(r'^[A-Za-z]:', value) or value.startswith(('\\\\', '//')) or '\\' in value)
    kind = PureWindowsPath if windows else PurePosixPath
    if flavor is not None and kind is not flavor:
        raise ValueError(f'Checkpoint contains mixed local path styles: {value}')
    path = kind(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Checkpoint path is relative or contains a parent escape: {value}')
    return path


def checkpoint_error(checkpoint, error, action='open saved progress'):
    """Explain a storage failure without implying a damaged database was repaired."""
    code = getattr(error, 'sqlite_errorname', None)
    detail = f'{error}' + (f' ({code})' if code else '')
    return ValueError(
        f'Cannot {action} at {checkpoint}: {detail}. '
        'Stop other renderer commands and check free disk space and write access. '
        'If this folder is in OneDrive, make all files available on this device, then copy '
        'the entire project to a fully local folder outside OneDrive, such as C:\\GardensOfTime. '
        'Keep the original copy and the whole output folder, including tests.sqlite3 and any '
        'tests.sqlite3-wal/tests.sqlite3-shm files; do not delete the checkpoint. '
        'Run --relocate-output successfully in the local copy before --update. '
        'If it still fails there, stop and keep both copies for inspection; do not overwrite either.'
    )


def require_checkpoint(output, writable=True):
    """Validate saved roots and SQLite access without enrolling or changing jobs.

    A zero-row UPDATE checks write permission under a short transaction. No
    records, schema, journal settings or artifact files are changed. This is a
    readiness check, not a guarantee against a later storage failure.
    """
    out = Path(output).resolve()
    checkpoint = out / 'batch/tests.sqlite3'
    if not checkpoint.is_file():
        raise ValueError(f'No existing test checkpoint at {checkpoint}; use the previous output folder.')
    db = None
    try:
        mode = 'rw' if writable else 'ro'
        db = sqlite3.connect(checkpoint.as_uri() + '?mode=' + mode, uri=True,
                             timeout=1, isolation_level=None)
        db.execute('BEGIN')
        checked = [row[0] for row in db.execute('PRAGMA quick_check')]
        if checked != ['ok']:
            raise checkpoint_error(checkpoint, 'SQLite quick_check failed: ' + '; '.join(map(str, checked)))
        required = {
            'jobs': {'scene_id', 'variant_id', 'ordinal', 'fingerprint', 'status', 'definition', 'result', 'active'},
            'scenes': {'id', 'ordinal', 'match_status', 'issues', 'active'},
        }
        for table, columns in required.items():
            found = {row[1] for row in db.execute('PRAGMA table_info(' + table + ')')}
            if not columns.issubset(found):
                raise ValueError(f'Unsupported or incomplete checkpoint schema at {checkpoint}; keep the original run and recopy its output folder.')
        current = stored_path(str(out))
        count = 0
        for sid, raw in db.execute('SELECT scene_id, definition FROM jobs'):
            count += 1
            try:
                definition = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f'Invalid checkpoint definition for {sid}; keep the original run and recopy its output folder.') from exc
            if not isinstance(definition, dict):
                raise ValueError(f'Invalid checkpoint definition for {sid}')
            if stored_path(definition.get('output')) != current:
                raise ValueError(
                    f'Checkpoint still points to another output folder: {definition.get("output")}. '
                    f'Run --relocate-output --output "{out}" and wait for success before resuming. '
                    'No scene jobs were changed by this check.'
                )
            destination = stored_path(definition.get('destination'), type(current))
            scene_root = current / 'scenes'
            local_destination = Path(str(destination)).resolve()
            if (destination == scene_root or not destination.is_relative_to(scene_root)
                    or not local_destination.is_relative_to(out / 'scenes')):
                raise ValueError(f'Saved destination for {sid} is outside {out / "scenes"}; no scenes were changed.')
        if not count:
            raise ValueError(f'Checkpoint has no saved scene jobs at {checkpoint}; use the output folder from the previous run.')
        db.execute('ROLLBACK')
        if writable:
            db.execute('BEGIN IMMEDIATE')
            db.execute('UPDATE jobs SET fingerprint=fingerprint WHERE 0')
            db.execute('ROLLBACK')
        return checkpoint
    except sqlite3.Error as exc:
        raise checkpoint_error(checkpoint, exc) from exc
    finally:
        if db is not None:
            db.close()
