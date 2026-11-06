"""Remove obsolete previews only when a saved passing version is still intact."""
import json
import re
import sqlite3
import stat
from pathlib import Path

from .checkpoint import checkpoint_error,require_checkpoint,stored_path
from .download import atomic_json,sha256

IMAGES={'scene_empty.png','scene_complete.png','scene_solution.png'}
PREVIEWS=IMAGES|{Path(name).with_suffix('.tmp').name for name in IMAGES}
METADATA={'validation_report.json','objects.json','solution_objects.json'}
REPORT='cleanup_incomplete_report.json'


class CheckpointChanged(ValueError):pass


def _plain(value):
    path=Path(value).absolute()
    for part in (path,*path.parents):
        if part.is_symlink() or (hasattr(part,'is_junction') and part.is_junction()):
            raise ValueError(f'Symlink or junction is not allowed during cleanup: {part}')
    return path.resolve()


def _recorded(value,out):
    root=stored_path(str(out));path=stored_path(value,type(root))
    if not path.is_relative_to(root):raise ValueError(f'Saved path is outside this output folder: {value}; relocate the output before cleanup')
    resolved=_plain(str(path))
    if not resolved.is_relative_to(out):raise ValueError('Saved path escapes the output folder')
    return resolved


def _signature(path):
    value=path.stat(follow_symlinks=False)
    if not stat.S_ISREG(value.st_mode):raise ValueError(f'Not a regular file: {path}')
    return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)


def _version(db):return db.execute('PRAGMA data_version').fetchone()[0]


def _object(raw,field):
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError(f'Checkpoint {field} must be an object')
    return value


def _verify_passed(plan):
    """Check retained image/label proof; input bundle caches need not remain."""
    try:from PIL import Image
    except ImportError as exc:raise ValueError('Install requirements.txt before verifying completed images for cleanup') from exc
    row=plan['row'];result=plan['result'];destination=plan['destination'];out=plan['out']
    if row['status']!='passed' or result.get('status')!='passed':raise ValueError('Scene version does not have a passed result')
    files=result.get('files');artifacts=result.get('artifacts')
    if not isinstance(files,list) or len(files)!=3 or not isinstance(artifacts,dict):raise ValueError('Passed result has no complete image proof')
    paths=[_recorded(value,out) for value in files]
    if len(set(paths))!=3 or {path.name for path in paths}!=IMAGES or any(path.parent!=destination for path in paths):
        raise ValueError('Passed PNGs are not three distinct canonical images in the saved destination')
    proofs={}
    for path in paths+[destination/name for name in sorted(METADATA)]:
        path=_plain(path);before=_signature(path);expected=artifacts.get(str(path))
        if not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{64}',expected) or sha256(path)!=expected:
            raise ValueError(f'Passed artifact has missing or changed hash proof: {path.name}')
        if path.name in IMAGES:
            with Image.open(path) as image:
                if image.format!='PNG':raise ValueError('Passed image is not a PNG')
                image.verify()
        if _signature(path)!=before:raise ValueError('Passed artifact changed during verification')
        proofs[path]=before
    validation=json.loads((destination/'validation_report.json').read_text(encoding='utf-8'))
    if not isinstance(validation,dict) or validation.get('status')!='validated_snapshot' or validation.get('issues')!=[]:
        raise ValueError('The retained validation report does not certify a complete snapshot')
    # Relocation deliberately leaves historical paths inside hashed JSON alone.
    # Operational locations come from the current checkpoint, not JSON strings.
    return proofs


def _delete_entry(db,initial_version,entry,plan):
    """Hold a short SQLite write reservation while checking and removing files."""
    db.execute('BEGIN IMMEDIATE')
    try:
        saved=db.execute('SELECT * FROM jobs WHERE scene_id=? AND variant_id=?',
                         (plan['row']['scene_id'],plan['row']['variant_id'])).fetchone()
        if _version(db)!=initial_version or saved is None or dict(saved)!=plan['row']:
            raise CheckpointChanged('Checkpoint changed during cleanup; stop renderer commands and scan again')
        proofs=_verify_passed(plan)
        # Check the entire preview set before the first deletion of this version.
        for file in plan['files']:
            path=_plain(file['path'])
            if _signature(path)!=file['signature'] or sha256(path)!=file['sha256']:
                raise ValueError('An incomplete preview changed after the cleanup scan')
        for file in plan['files']:
            if any(_signature(_plain(path))!=signature for path,signature in proofs.items()):
                raise ValueError('A completed artifact changed before cleanup finished')
            path=_plain(file['path'])
            if _signature(path)!=file['signature']:raise ValueError('An incomplete preview changed before deletion')
            path.unlink()
            file['record']['status']='deleted'
        preview=_plain(plan['preview'])
        try:
            preview.rmdir();entry['removed_empty_directory']=True
        except OSError:
            # Unknown files/subdirectories are deliberately retained.
            entry['removed_empty_directory']=False
        entry['status']='deleted'
    finally:
        if db.in_transaction:db.execute('ROLLBACK')


def cleanup_incomplete(output,delete=False):
    """Scan by default; --delete removes only allowlisted obsolete preview files.

    Stop other rendering commands before using this utility. No saved scene/job
    records are changed. Each deletion is guarded by fresh checkpoint and image
    proof; an interrupted run can repeat the same command for remaining files.
    """
    out=_plain(output);checkpoint=_plain(require_checkpoint(out,writable=False))
    report_path=_plain(out/REPORT);_plain(report_path.with_name(REPORT+'.tmp'))
    db=None
    try:
        db=sqlite3.connect(checkpoint.as_uri()+('?mode=rw' if delete else '?mode=ro'),uri=True,timeout=1,isolation_level=None)
        # An actual read/write connection is needed for the short writer
        # reservation; mode=ro accepts BEGIN IMMEDIATE without blocking writers.
        # When supported, suppress SQLite's optional close-time WAL checkpoint
        # so this utility also leaves physical database bytes unchanged.
        if delete and hasattr(db,'setconfig') and hasattr(sqlite3,'SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE'):
            db.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE,True)
        db.row_factory=sqlite3.Row
        initial_version=_version(db)
        db.execute('BEGIN')
        rows=[dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY ordinal,scene_id,variant_id')]
        db.execute('ROLLBACK')
        if _version(db)!=initial_version:raise CheckpointChanged('Checkpoint changed while reading saved jobs; stop the renderer before cleanup')
        decoded=[];protected=set();destinations={}
        for row in rows:
            definition=_object(row['definition'],'definition');result=_object(row['result'],'result')
            if stored_path(definition.get('output'))!=stored_path(str(out)):
                raise ValueError('Saved output root differs from this folder; relocate output before cleanup')
            destination=_recorded(definition.get('destination'),out)
            if destination==out/'scenes' or not destination.is_relative_to(out/'scenes'):
                raise ValueError('Saved scene destination is outside output/scenes')
            key=(row['scene_id'],row['variant_id']);destinations.setdefault(destination,[]).append(key)
            files=result.get('files',[]);artifacts=result.get('artifacts',{})
            if not isinstance(files,list) or not isinstance(artifacts,dict):raise ValueError('Malformed saved artifact paths; cleanup cannot establish file ownership')
            for value in [*files,*artifacts,*([result['log']] if result.get('log') else [])]:
                protected.add(_recorded(value,out))
            decoded.append({'row':row,'result':result,'destination':destination,'out':out})
        if report_path in protected or report_path.with_name(REPORT+'.tmp') in protected:
            raise ValueError('Cleanup report path overlaps a saved artifact')
        passed=[plan for plan in decoded if plan['row']['active'] and plan['row']['status']=='passed']
        summary={'passed_variants':len(passed),'eligible_variants':0,'candidate_files':0,'candidate_bytes':0,
                 'deleted_files':0,'deleted_bytes':0,'skipped_variants':0,'retained_files':0}
        report={'schema':1,'status':'planned','delete':bool(delete),'output':str(out),
                'report_path':str(report_path),'summary':summary,'entries':[],
                'scope':'Only canonical preview PNGs and matching renderer .tmp files directly inside a verified passed version/incomplete folder.'}
        plans=[]
        print(f'Checking {len(passed)} passed versions for obsolete incomplete previews...',flush=True)
        for number,plan in enumerate(passed,1):
            row=plan['row'];destination=plan['destination'];preview=destination/'incomplete'
            if not preview.exists() and not preview.is_symlink():continue
            entry={'scene_id':row['scene_id'],'variant_id':row['variant_id'],'destination':str(destination),
                   'incomplete_directory':str(preview),'status':'skipped','files':[],'retained':[],'issues':[]}
            report['entries'].append(entry)
            try:
                preview=_plain(preview)
                if not preview.is_dir():raise ValueError('Incomplete preview path is not a directory')
                if len(destinations[destination])!=1:raise ValueError('More than one saved version owns this destination')
                if any(path==preview or path.is_relative_to(preview) for path in destinations):
                    raise ValueError('Another saved version owns a destination inside this incomplete folder')
                files=[]
                for path in sorted(preview.iterdir()):
                    if path.name not in PREVIEWS:
                        entry['retained'].append({'path':str(path),'reason':'Not a known renderer preview filename'});continue
                    path=_plain(path)
                    if path in protected:
                        entry['retained'].append({'path':str(path),'reason':'Referenced by a saved scene result'});continue
                    signature=_signature(path)
                    if path.stat(follow_symlinks=False).st_nlink>1:
                        entry['retained'].append({'path':str(path),'reason':'File has additional hard links'});continue
                    digest=sha256(path)
                    if _signature(path)!=signature:raise ValueError('Preview changed during cleanup scan')
                    record={'path':str(path),'bytes':signature[2],'sha256':digest,'status':'planned'}
                    entry['files'].append(record)
                    files.append({'path':path,'signature':signature,'sha256':digest,'record':record})
                if not files and entry['retained']:
                    entry['status']='nothing_to_delete';continue
                _verify_passed(plan)
                plan.update(preview=preview,files=files,entry=entry)
                plans.append(plan);entry['status']='planned'
                summary['eligible_variants']+=1
            except (ValueError,OSError,SyntaxError) as exc:
                entry['issues'].append(str(exc))
                for file in entry['files']:file['status']='retained'
            if number%100==0:print(f'Checked {number}/{len(passed)} passed versions.',flush=True)
        summary['candidate_files']=sum(len(plan['files']) for plan in plans)
        summary['candidate_bytes']=sum(file['signature'][2] for plan in plans for file in plan['files'])
        # Persist the exact deletion plan before touching a preview file.
        atomic_json(report_path,report)
        if delete:
            report['status']='completed'
            try:
                for index,plan in enumerate(plans):
                    try:_delete_entry(db,initial_version,plan['entry'],plan)
                    except CheckpointChanged as exc:
                        for pending in plans[index:]:
                            pending['entry']['status']='skipped';pending['entry']['issues'].append(str(exc))
                            for file in pending['entry']['files']:
                                if file['status']!='deleted':file['status']='retained'
                        break
                    except (ValueError,OSError,sqlite3.Error,SyntaxError) as exc:
                        plan['entry']['status']='skipped';plan['entry']['issues'].append(str(exc))
                        for file in plan['entry']['files']:
                            if file['status']!='deleted':file['status']='retained'
            except KeyboardInterrupt:report['status']='interrupted'
        summary['deleted_files']=sum(file['status']=='deleted' for entry in report['entries'] for file in entry['files'])
        summary['deleted_bytes']=sum(file['bytes'] for entry in report['entries'] for file in entry['files'] if file['status']=='deleted')
        summary['skipped_variants']=sum(entry['status']=='skipped' for entry in report['entries'])
        summary['retained_files']=sum(len(entry['retained']) for entry in report['entries'])
        if summary['skipped_variants'] and report['status']!='interrupted':report['status']='partial'
        atomic_json(report_path,report)
        return report
    except sqlite3.Error as exc:
        raise checkpoint_error(checkpoint,exc,'inspect incomplete previews') from exc
    finally:
        if db is not None:
            if db.in_transaction:db.execute('ROLLBACK')
            db.close()
