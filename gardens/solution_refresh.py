"""Refresh only passed solution annotations, retaining native images and proof."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from .batch import code_identity
from .checkpoint import require_checkpoint
from .download import atomic_json,cache_name,read_json,sha256
from .named_export import _plain,_source_images

REPORT='solution_refresh_report.json'
JOURNAL='solution_refresh_pending.json'
PUBLISH=('scene_solution.png','solution_objects.json','validation_report.json')
NATIVE=('scene_empty.png','scene_complete.png')


def _layout():
    from . import annotations
    return getattr(annotations,'SOLUTION_LAYOUT','numbered_local_pointers_v1')


def _read(path):
    try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError) as exc:raise ValueError(f'Cannot read solution metadata: {path}') from exc


def _inside(out,path):
    path=_plain(path)
    if path==out or not path.is_relative_to(out):raise ValueError('Solution refresh path is outside its saved output folder')
    return path


def _proof(out,artifacts,skip=()):
    for raw,digest in artifacts.items():
        path=_inside(out,raw)
        if path in skip:continue
        if not path.is_file() or sha256(path)!=digest:raise ValueError(f'Saved artifact hash no longer matches: {path.name}')


def _bindings(markers):
    if not isinstance(markers,list) or not markers:raise ValueError('Solution metadata has no verified object bindings')
    values=[]
    for marker in markers:
        if not isinstance(marker,dict) or marker.get('label_verified',True) is False:raise ValueError('Solution has unverified object labels')
        try:values.append((marker['object_id'],marker['manager_key'],marker['label']))
        except KeyError as exc:raise ValueError('Solution metadata lacks object binding identity') from exc
    return sorted(values)


def _inputs(out,definition,result):
    bundles=(definition.get('candidate') or {}).get('bundles',[])
    if len(bundles)!=1 or not bundles[0].get('url'):raise ValueError('Solution refresh requires one recorded scene bundle')
    bundle_url=bundles[0]['url'];manager_url=definition['scene'].get('item_manager_url')
    if not manager_url:raise ValueError('Solution refresh has no recorded Item Manager URL')
    bundle=_inside(out,out/'bundles'/cache_name(bundle_url,'.bundle'))
    manager=_inside(out,out/'item_managers'/cache_name(manager_url,'.json'))
    for path,url in ((bundle,bundle_url),(manager,manager_url)):
        expected=result.get('artifacts',{}).get(str(path));receipt=read_json(str(path)+'.verified.json')
        if not expected or receipt.get('url')!=url or receipt.get('sha256')!=expected or not path.is_file() or sha256(path)!=expected:
            raise ValueError(f'No unchanged verified cached input for solution refresh: {path.name}')
    data=_read(manager)
    if not isinstance(data,dict) or not data:raise ValueError('Cached Item Manager is empty or invalid')
    return bundle,manager,data


def _render(bundle,manager_path,stage,frame,timeout):
    job=stage/'render_job.json'
    atomic_json(job,{'bundle':str(bundle),'manager':str(manager_path),'stage':str(stage),'frame':frame})
    try:
        with (stage/'renderer.log').open('w',encoding='utf-8') as log:
            process=subprocess.run([sys.executable,'-m','gardens.solution_refresh','--render-job',str(job)],
                cwd=Path(__file__).resolve().parents[1],stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
    except subprocess.TimeoutExpired as exc:raise ValueError(f'Solution staging render timed out after {timeout} seconds') from exc
    report=_read(stage/'validation_report.json')
    if process.returncode or report.get('status')!='validated_snapshot':
        raise ValueError('Cached staging render is incomplete: '+'; '.join(report.get('issues',[])))
    return report


def _journal_path(out):return _plain(out/'batch'/JOURNAL)


def _recover(out,db):
    """Restore an interrupted file publication, or retain a fully committed one."""
    path=_journal_path(out)
    if not path.exists():return
    journal=_read(path)
    if not isinstance(journal,dict) or journal.get('schema')!=1:raise ValueError('Unrecognized solution refresh recovery journal')
    old=journal['old_row'];files=journal['files'];stage=_inside(out,out/journal['stage_relative'])
    if not stage.is_relative_to(out/'.solution_refresh_staging'):raise ValueError('Invalid solution recovery staging path')
    if stage.parent!=out/'.solution_refresh_staging' or not stage.name.startswith('solution-'):
        raise ValueError('Invalid owned solution recovery directory')
    current=db.execute('SELECT * FROM jobs WHERE scene_id=? AND variant_id=?',(old['scene_id'],old['variant_id'])).fetchone()
    if current is None:raise ValueError('Solution recovery checkpoint row is missing')
    current=dict(current);new={**old,'result':journal['new_result']}
    if current not in (old,new):raise ValueError('Solution recovery checkpoint changed; keep its staging and backup files')
    resolved=[]
    if len(files)!=len(PUBLISH) or {Path(entry['relative']).name for entry in files}!=set(PUBLISH):
        raise ValueError('Solution recovery journal does not contain exactly the published artifacts')
    for entry in files:
        target=_inside(out,out/entry['relative']);backup=_inside(out,out/entry['backup_relative'])
        staged=_inside(out,out/entry['staged_relative'])
        if backup.parent!=stage or backup.name!='before-'+target.name or staged.parent!=stage or staged.name!=target.name:
            raise ValueError('Invalid owned solution recovery artifact')
        if target.name not in PUBLISH or target.parent!=Path(json.loads(old['definition'])['destination']):raise ValueError('Invalid solution recovery target')
        actual=sha256(target) if target.is_file() else None
        if actual not in (entry['old_sha256'],entry['new_sha256']):raise ValueError('Solution recovery found an edited or missing published file')
        if not backup.is_file() or sha256(backup)!=entry['old_sha256']:raise ValueError('Solution recovery backup is missing or changed')
        resolved.append((entry,target,backup,actual))
    if current==new and all(actual==entry['new_sha256'] for entry,_,_,actual in resolved):
        path.unlink();shutil.rmtree(stage);return
    db.execute('BEGIN IMMEDIATE')
    try:
        latest=dict(db.execute('SELECT * FROM jobs WHERE scene_id=? AND variant_id=?',(old['scene_id'],old['variant_id'])).fetchone())
        if latest!=current:raise ValueError('Solution checkpoint changed during recovery')
        for entry,target,backup,actual in resolved:
            if sha256(target) not in (entry['old_sha256'],entry['new_sha256']):raise ValueError('Solution file changed during recovery')
            if actual!=entry['old_sha256']:
                temporary=stage/('restore-'+target.name);shutil.copy2(backup,temporary);os.replace(temporary,target)
        db.execute('UPDATE jobs SET result=? WHERE scene_id=? AND variant_id=?',(old['result'],old['scene_id'],old['variant_id']))
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction:db.execute('ROLLBACK')
        raise
    path.unlink();shutil.rmtree(stage)


def refresh_solutions(output,timeout=300,plan_only=False,renderer=None):
    out=_plain(output);checkpoint=require_checkpoint(out,writable=not plan_only)
    db=sqlite3.connect(checkpoint.as_uri()+('?mode=ro' if plan_only else '?mode=rw'),uri=True,isolation_level=None)
    db.row_factory=sqlite3.Row;backup=None;layout=_layout()
    report={'status':'completed','solution_layout':layout,'plan_only':bool(plan_only),
            'summary':{'passed_variants':0,'refreshed_variants':0,'reused_variants':0,'skipped_variants':0,'planned_variants':0},
            'results':[],'backup_path':None,
            'limitations':['Only solution annotations and their sidecars are amended; native scene files and historical render fingerprints are retained.']}
    try:
        if _journal_path(out).exists():
            if plan_only:raise ValueError('A pending solution refresh needs recovery before a plan can be verified')
            _recover(out,db)
        rows=list(db.execute("SELECT * FROM jobs WHERE active=1 AND status='passed' ORDER BY ordinal,variant_id"))
        report['summary']['passed_variants']=len(rows)
        print(f'Checking {len(rows)} passed versions for the current solution layout...',flush=True)
        for index,row in enumerate(rows,1):
            stage=None;entry={'scene_id':row['scene_id'],'variant_id':row['variant_id'],'issues':[]}
            report['results'].append(entry)
            try:
                definition=json.loads(row['definition']);result=json.loads(row['result'])
                _source_images(out,row);_proof(out,result.get('artifacts',{}))
                destination=_inside(out,definition['destination'])
                old_report=_read(destination/'validation_report.json');old_markers=_read(destination/'solution_objects.json')
                if old_report.get('status')!='validated_snapshot' or old_report.get('issues'):
                    raise ValueError('Existing validation metadata is not a passed reconstruction')
                if result.get('solution_layout')==layout and old_report.get('solution_layout')==layout:
                    entry['status']='reused';report['summary']['reused_variants']+=1;continue
                bundle,manager_path,manager=_inputs(out,definition,result)
                frame=old_report.get('snapshot_frame')
                if not isinstance(frame,int) or isinstance(frame,bool) or frame<1:raise ValueError('Old validated snapshot has no usable frame')
                old_bindings=_bindings(old_markers)
                if plan_only:
                    entry['status']='planned';report['summary']['planned_variants']+=1;continue
                version=db.execute('PRAGMA data_version').fetchone()[0]
                print(f'Solution layout {index}/{len(rows)}: {row["scene_id"]} variant {row["variant_id"]}',flush=True)
                staging=_plain(out/'.solution_refresh_staging');staging.mkdir(exist_ok=True)
                stage=Path(tempfile.mkdtemp(prefix='solution-',dir=staging))
                fresh=renderer(bundle,manager,stage,frame=frame) if renderer else _render(bundle,manager_path,stage,frame,timeout)
                if fresh.get('status')!='validated_snapshot' or fresh.get('issues') or fresh.get('solution_layout')!=layout:
                    raise ValueError('Staging render did not validate the requested solution layout')
                for name in NATIVE:
                    if sha256(stage/name)!=result['artifacts'].get(str(destination/name)):
                        raise ValueError('Native empty/complete reconstruction changed; solution-only update was not published')
                if _bindings(_read(stage/'solution_objects.json'))!=old_bindings:
                    raise ValueError('Verified object IDs, manager keys or labels changed; this is not a layout-only refresh')
                old_hashes={name:sha256(destination/name) for name in PUBLISH}
                amendment={'layout':layout,'original_render_fingerprint':row['fingerprint'],'snapshot_frame':frame,
                           'renderer_identity':code_identity(),'native_files_retained':{name:result['artifacts'][str(destination/name)] for name in NATIVE},
                           'prior_validation_report_sha256':old_hashes['validation_report.json'],'native_recertified':False}
                fresh={**fresh,'files':result['files'],'solution_refresh':amendment}
                atomic_json(stage/'validation_report.json',fresh)
                new_result={**result,'solution_layout':layout,'solution_refresh':amendment,'artifacts':dict(result['artifacts']),
                            'warnings':fresh.get('warnings',result.get('warnings',[])),
                            'item_manager_complete':fresh.get('item_manager_complete',result.get('item_manager_complete'))}
                files=[]
                for name in PUBLISH:
                    source=_inside(out,stage/name);target=_inside(out,destination/name)
                    if source.parent!=stage:raise ValueError('Invalid staged solution artifact')
                    saved=stage/('before-'+name);shutil.copy2(target,saved)
                    if sha256(saved)!=old_hashes[name]:raise ValueError('Existing solution metadata changed while staging')
                    digest=sha256(source);new_result['artifacts'][str(target)]=digest
                    files.append({'relative':target.relative_to(out).as_posix(),'staged_relative':source.relative_to(out).as_posix(),
                                  'backup_relative':saved.relative_to(out).as_posix(),'old_sha256':old_hashes[name],'new_sha256':digest})
                if backup is None:
                    backup=_plain(out/'batch'/('tests.pre-solution-refresh-'+uuid.uuid4().hex+'.sqlite3'))
                    with sqlite3.connect(backup) as target:
                        db.backup(target);target.execute('PRAGMA journal_mode=DELETE')
                    report['backup_path']=str(backup)
                journal={'schema':1,'old_row':dict(row),'new_result':json.dumps(new_result),
                         'stage_relative':stage.relative_to(out).as_posix(),'files':files}
                db.execute('BEGIN IMMEDIATE')
                try:
                    current=db.execute('SELECT * FROM jobs WHERE scene_id=? AND variant_id=?',(row['scene_id'],row['variant_id'])).fetchone()
                    if dict(current)!=dict(row) or db.execute('PRAGMA data_version').fetchone()[0]!=version:
                        raise ValueError('Another command changed the checkpoint during solution staging')
                    _proof(out,result.get('artifacts',{}))
                    for item in files:
                        if sha256(out/item['relative'])!=item['old_sha256']:raise ValueError('Existing solution file changed before publication')
                    atomic_json(_journal_path(out),journal)
                    for item in files:os.replace(out/item['staged_relative'],out/item['relative'])
                    db.execute('UPDATE jobs SET result=? WHERE scene_id=? AND variant_id=?',(json.dumps(new_result),row['scene_id'],row['variant_id']))
                    db.execute('COMMIT')
                except BaseException:
                    if db.in_transaction:db.execute('ROLLBACK')
                    raise
                _recover(out,db)
                stage=None;entry['status']='refreshed';report['summary']['refreshed_variants']+=1
            except KeyboardInterrupt:
                if _journal_path(out).exists():_recover(out,db)
                report['status']='interrupted';entry['status']='interrupted';break
            except (ValueError,OSError,sqlite3.Error,KeyError,TypeError) as exc:
                if _journal_path(out).exists():
                    try:_recover(out,db)
                    except (ValueError,OSError,sqlite3.Error) as recovery:
                        entry['issues'].append('Recovery pending: '+str(recovery));report['status']='partial';stage=None
                entry.update(status='skipped');entry['issues'].append(str(exc))
                report['summary']['skipped_variants']+=1;report['status']='partial'
            finally:
                if stage is not None and stage.exists() and not _journal_path(out).exists():shutil.rmtree(stage)
            if index%25==0:print(f'Solution layouts checked {index}/{len(rows)}; refreshed {report["summary"]["refreshed_variants"]}.',flush=True)
        if not plan_only:atomic_json(out/REPORT,report)
        return report
    finally:db.close()


if __name__=='__main__':
    if len(sys.argv)!=3 or sys.argv[1]!='--render-job':raise SystemExit(2)
    from .render import render_bundle
    job=_read(sys.argv[2]);result=render_bundle(job['bundle'],_read(job['manager']),job['stage'],job['frame'])
    raise SystemExit(0 if result['status']=='validated_snapshot' else 1)
