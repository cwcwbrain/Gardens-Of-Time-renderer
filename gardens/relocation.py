"""Rebase a copied output checkpoint while retaining historical image proof."""
import json
import re
import sqlite3
import uuid
from pathlib import Path,PurePosixPath,PureWindowsPath

from .download import atomic_json,sha256

ROLES={'scene_empty.png','scene_complete.png','scene_solution.png'}


def _plain(path):
    path=Path(path).absolute()
    for part in (path,*path.parents):
        if part.is_symlink() or (hasattr(part,'is_junction') and part.is_junction()):
            raise ValueError(f'Symlink or junction is not allowed during relocation: {part}')
    return path.resolve()


def _stored_path(value,flavor=None):
    if not isinstance(value,str) or not value or '\0' in value:
        raise ValueError('Checkpoint contains an invalid local path')
    windows=bool(re.match(r'^[A-Za-z]:',value) or value.startswith(('\\\\','//')) or '\\' in value)
    kind=PureWindowsPath if windows else PurePosixPath
    if flavor is not None and kind is not flavor:
        raise ValueError(f'Checkpoint contains mixed local path styles: {value}')
    path=kind(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Checkpoint path is relative or contains a parent escape: {value}')
    return path


def _object(raw,field):
    try:value=json.loads(raw)
    except (TypeError,ValueError) as exc:raise ValueError(f'Checkpoint has invalid {field} JSON') from exc
    if not isinstance(value,dict):raise ValueError(f'Checkpoint {field} must be a JSON object')
    return value


def _rows(db,table):
    # Table names are internal constants; do not expose this helper to user SQL.
    return list(db.execute('SELECT * FROM '+table+' ORDER BY 1,2'))


def _signature(path):
    value=path.stat()
    return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns)


class _Mapper:
    def __init__(self,source,output):
        self.source,self.output=source,output
        self.windows=isinstance(source,PureWindowsPath)
        self.directories={};self.paths={source:output}

    def __call__(self,value):
        path=_stored_path(value,type(self.source))
        if path in self.paths:return self.paths[path]
        try:relative=path.relative_to(self.source)
        except ValueError as exc:raise ValueError(f'Checkpoint path lies outside the saved output root: {value}') from exc
        destination=self.output;prefix=self.source
        for name in relative.parts:
            prefix=prefix/name
            if prefix in self.paths:
                destination=self.paths[prefix];continue
            if self.windows and destination.is_dir():
                # A Windows checkpoint can be tested or moved on a case-sensitive
                # host. Resolve its components by Windows identity, failing on
                # ambiguous copies rather than selecting an arbitrary spelling.
                if destination not in self.directories:
                    choices={}
                    for child in destination.iterdir():choices.setdefault(child.name.casefold(),[]).append(child.name)
                    self.directories[destination]=choices
                found=self.directories[destination].get(name.casefold(),[])
                if len(found)>1:raise ValueError(f'Copied Windows path has case-colliding entries below {destination}')
                if found:name=found[0]
            destination=_plain(destination/name)
            if not destination.is_relative_to(self.output):
                raise ValueError(f'Relocated path escapes the copied output folder: {value}')
            self.paths[prefix]=destination
        return destination


def relocate_output(output,dry_run=False):
    """Verify and rebase only the copied checkpoint's operational path fields.

    Never accesses the old source root. PNGs, JSON artifacts, digests, statuses
    and fingerprints remain historical proof; relocation changes no image bytes.
    """
    out=_plain(output);checkpoint=_plain(out/'batch/tests.sqlite3')
    if not checkpoint.is_file():raise ValueError(f'No copied test checkpoint at {checkpoint}')
    for suffix in ('-wal','-shm'):_plain(str(checkpoint)+suffix)
    mode='ro' if dry_run else 'rw'
    db=None
    try:
        db=sqlite3.connect(checkpoint.as_uri()+'?mode='+mode,uri=True,isolation_level=None)
        db.row_factory=sqlite3.Row
        checked=[row[0] for row in db.execute('PRAGMA quick_check')]
        if checked!=['ok']:
            raise ValueError('Copied checkpoint failed SQLite quick_check: '+'; '.join(map(str,checked)))
        original=_rows(db,'jobs');original_scenes=_rows(db,'scenes')
        if not original:raise ValueError('Copied checkpoint has no scene jobs from which to infer its output root')
        required={'scene_id','variant_id','ordinal','fingerprint','status','definition','result','active'}
        if not required.issubset(original[0].keys()):raise ValueError('Copied checkpoint has an unsupported jobs schema')
        decoded=[];roots={}
        for row in original:
            definition=_object(row['definition'],'definition');result=_object(row['result'],'result')
            root=_stored_path(definition.get('output'))
            roots.setdefault((type(root),root),root)
            decoded.append((row,definition,result))
        if len(roots)!=1:raise ValueError('Copied checkpoint has mixed output roots; no rows were relocated')
        source=next(iter(roots.values()));mapper=_Mapper(source,out)
        changed=[];expected={};passed_results=0
        for row,definition,result in decoded:
            new_definition=dict(definition);new_result=dict(result)
            new_definition['output']=str(mapper(definition['output']))
            destination=mapper(definition.get('destination'));new_definition['destination']=str(destination)
            scene_root=_plain(out/'scenes')
            if destination==scene_root or not destination.is_relative_to(scene_root):
                raise ValueError(f'Relocated scene destination is outside output/scenes: {destination}')
            if 'files' in result:
                if not isinstance(result['files'],list):raise ValueError('Checkpoint image files must be a list')
                new_result['files']=[str(mapper(path)) for path in result['files']]
            if 'artifacts' in result:
                if not isinstance(result['artifacts'],dict):raise ValueError('Checkpoint artifacts must be an object')
                artifacts={}
                for raw,digest in result['artifacts'].items():
                    mapped=str(mapper(raw))
                    if mapped in artifacts:raise ValueError('Relocated artifact paths collide')
                    artifacts[mapped]=digest
                new_result['artifacts']=artifacts
            if result.get('log') not in (None,''):new_result['log']=str(mapper(result['log']))
            if row['status']=='passed':
                passed_results+=1
                if result.get('status')!='passed':raise ValueError('Passed checkpoint has an inconsistent result status')
                files=new_result.get('files');artifacts=new_result.get('artifacts')
                if not isinstance(files,list) or len(files)!=3 or not isinstance(artifacts,dict) or not artifacts:
                    raise ValueError('Passed checkpoint lacks complete image and artifact proof')
                paths=[Path(path) for path in files]
                if len(set(paths))!=3 or {path.name for path in paths}!=ROLES or any(path.parent!=destination for path in paths):
                    raise ValueError('Passed checkpoint has duplicate or noncanonical scene image paths')
                if any(str(path) not in artifacts for path in paths):raise ValueError('Passed image is missing its original artifact hash')
                for raw,digest in artifacts.items():
                    if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest):
                        raise ValueError('Passed checkpoint has an invalid artifact SHA-256')
                    path=Path(raw)
                    if path in expected and expected[path]!=digest:
                        raise ValueError(f'Passed results disagree on the copied hash for {path}')
                    expected[path]=digest
            if new_definition!=definition or new_result!=result:
                changed.append((json.dumps(new_definition) if new_definition!=definition else row['definition'],
                                json.dumps(new_result) if new_result!=result else row['result'],row['scene_id'],row['variant_id']))
        signatures={};verified_bytes=0
        print(f'Verifying {len(expected)} copied artifacts for {passed_results} historical passed scene versions...',flush=True)
        for index,(path,digest) in enumerate(expected.items(),1):
            path=_plain(path)
            if not path.is_file():raise ValueError(f'Copied passed artifact is missing: {path}')
            before=_signature(path)
            if sha256(path)!=digest:raise ValueError(f'Copied passed artifact SHA-256 mismatch: {path}')
            if _signature(path)!=before:raise ValueError(f'Copied artifact changed during verification: {path}')
            signatures[path]=before;verified_bytes+=before[2]
            if index%100==0 or index==len(expected):
                print(f'Verified {index}/{len(expected)} copied artifacts ({verified_bytes/(1024**3):.2f} GiB).',flush=True)
        report={'status':'planned' if dry_run else ('relocated' if changed else 'unchanged'),
                'new_root':str(out),'source_roots':[str(source)],'changed_jobs':len(changed),
                'jobs':len(original),'passed_results':passed_results,'verified_files':len(expected),
                'verified_bytes':verified_bytes,'backup_path':None,
                'historical_fingerprints_preserved':True,'passed_results_recertified':False,
                'limitations':['Relocation verifies copied artifact integrity and rebases operational checkpoint paths; it does not recertify scene reconstruction.',
                               'Absolute paths inside copied JSON artifacts remain historical provenance so their original hashes and bytes stay unchanged.']}
        if dry_run or not changed:return report
        report_path=_plain(out/'batch/relocation_report.json')
        _plain(report_path.with_name(report_path.name+'.tmp'))
        if report_path.exists() and not report_path.is_file():
            raise ValueError('Relocation report destination is not a regular file')
        # The backup is made only in the desktop copy and includes any committed
        # WAL pages. Never opens or writes the original laptop's recorded paths.
        backup=_plain(out/'batch'/('tests.pre-relocation-'+uuid.uuid4().hex+'.sqlite3'))
        with sqlite3.connect(backup) as target:
            db.backup(target)
            target.execute('PRAGMA journal_mode=DELETE')
        report['backup_path']=str(backup)
        db.execute('BEGIN IMMEDIATE')
        try:
            if [tuple(row) for row in _rows(db,'jobs')]!=[tuple(row) for row in original] or [tuple(row) for row in _rows(db,'scenes')]!=[tuple(row) for row in original_scenes]:
                raise ValueError('Checkpoint changed during relocation; stop every run before retrying')
            for path,signature in signatures.items():
                if _plain(path)!=path or _signature(path)!=signature:
                    raise ValueError(f'Copied artifact changed before relocation commit: {path}')
            db.executemany('UPDATE jobs SET definition=?,result=? WHERE scene_id=? AND variant_id=?',changed)
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK');raise
        atomic_json(report_path,report)
        return report
    except sqlite3.Error as exc:
        raise ValueError(f'Cannot relocate copied checkpoint: {exc}') from exc
    finally:
        if db is not None:db.close()
