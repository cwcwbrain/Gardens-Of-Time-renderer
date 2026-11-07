"""Refresh passed nested snapshots with a general selector and recoverable writes."""
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path

from PIL import Image,ImageChops

from .batch import code_identity,fingerprint
from .checkpoint import require_checkpoint
from .download import atomic_json,sha256
from .named_export import _plain,_source_images
from .nested_pose import POSE_SELECTION_VERSION
from .solution_refresh import _bindings,_inputs,_inside,_proof,_read,_render

REPORT='pose_refresh_report.json'
JOURNAL='pose_refresh_pending.json'
STAGING='.pose_refresh_staging'
PNGS=('scene_empty.png','scene_complete.png','scene_solution.png')
ARTIFACTS=PNGS+('objects.json','solution_objects.json','validation_report.json')


def _control(path):
    path=_plain(path)
    if path.exists() and (not path.is_file() or path.stat().st_nlink>1):
        raise ValueError('A pose refresh control file is not a private regular file: '+path.name)
    return path


def _journal(out):return _control(out/'batch'/JOURNAL)


def _destination(out,definition):
    destination=_inside(out,definition['destination']);root=_plain(out/'scenes')
    if destination==root or not destination.is_relative_to(root):raise ValueError('Pose refresh destination is outside output/scenes')
    return destination


def _recover(out,db):
    path=_journal(out)
    if not path.exists():return
    journal=_read(path)
    if (not isinstance(journal,dict) or journal.get('schema')!=1 or
            not isinstance(journal.get('pose_selection_version'),str) or not journal['pose_selection_version']):
        raise ValueError('Unrecognized pose refresh recovery journal')
    old=journal['old_row'];files=journal['files']
    if old.get('status')!='passed' or old.get('active')!=1:raise ValueError('Pose recovery does not identify an active passed scene')
    definition=json.loads(old['definition']);old_result=json.loads(old['result'])
    if definition.get('scene',{}).get('scene_id')!=old.get('scene_id'):raise ValueError('Pose recovery scene identities disagree')
    destination=_destination(out,definition);stage=_inside(out,out/journal['stage_relative'])
    if stage.parent!=out/STAGING or not stage.name.startswith('pose-'):raise ValueError('Invalid owned pose recovery directory')
    current=db.execute('SELECT * FROM jobs WHERE scene_id=? AND variant_id=?',(old['scene_id'],old['variant_id'])).fetchone()
    if current is None:raise ValueError('Pose recovery checkpoint row is missing')
    current=dict(current);new={**old,'result':journal['new_result']}
    if current not in (old,new):raise ValueError('Pose recovery checkpoint changed; retain its backups')
    names=[Path(entry['relative']).name for entry in files]
    if (len(names)!=len(set(names)) or not set(names).issubset(ARTIFACTS) or
            'validation_report.json' not in names or not set(names).intersection(PNGS)):
        raise ValueError('Pose recovery does not identify a valid changed artifact subset')
    resolved=[]
    for entry in files:
        target=_inside(out,out/entry['relative']);backup=_inside(out,out/entry['backup_relative'])
        staged=_inside(out,out/entry['staged_relative'])
        if target.parent!=destination or backup.parent!=stage or backup.name!='before-'+target.name or staged.parent!=stage or staged.name!=target.name:
            raise ValueError('Invalid owned pose recovery artifact')
        if old_result.get('artifacts',{}).get(str(target))!=entry['old_sha256']:
            raise ValueError('Pose recovery does not match the original artifact proof')
        actual=sha256(target) if target.is_file() else None
        if actual not in (entry['old_sha256'],entry['new_sha256']):raise ValueError('Pose recovery found an edited or missing published file')
        if not backup.is_file() or sha256(backup)!=entry['old_sha256']:raise ValueError('Pose recovery backup is missing or changed')
        resolved.append((entry,target,backup,actual))
    if current==new and all(actual==entry['new_sha256'] for entry,_,_,actual in resolved):
        path.unlink();shutil.rmtree(stage);return
    db.execute('BEGIN IMMEDIATE')
    try:
        latest=dict(db.execute('SELECT * FROM jobs WHERE scene_id=? AND variant_id=?',(old['scene_id'],old['variant_id'])).fetchone())
        if latest!=current:raise ValueError('Pose checkpoint changed during recovery')
        for entry,target,backup,_ in resolved:
            actual=sha256(target)
            if actual not in (entry['old_sha256'],entry['new_sha256']):raise ValueError('Pose file changed during recovery')
            if actual!=entry['old_sha256']:
                temporary=stage/('restore-'+target.name);shutil.copy2(backup,temporary);os.replace(temporary,target)
        db.execute('UPDATE jobs SET result=? WHERE scene_id=? AND variant_id=?',(old['result'],old['scene_id'],old['variant_id']))
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction:db.execute('ROLLBACK')
        raise
    path.unlink();shutil.rmtree(stage)


def _previous(out):
    path=_control(out/REPORT)
    if not path.exists():return {}
    data=_read(path)
    if not isinstance(data,dict) or data.get('schema')!=1 or not isinstance(data.get('checked_sources'),dict):
        raise ValueError('Existing pose refresh report is not a recognized analysis cache')
    return dict(data['checked_sources'])


def _save_report(out,report):
    _control(out/REPORT);_control(out/(REPORT+'.tmp'))
    atomic_json(out/REPORT,report)


def _artifact_owners(out,rows):
    """Protect exact shared file targets, while allowing nested variant folders."""
    owners={}
    for row in rows:
        definition=json.loads(row['definition']);destination=Path(definition['destination']).resolve()
        key=(row['scene_id'],row['variant_id'])
        paths={destination/name for name in ARTIFACTS}
        try:
            result=json.loads(row['result'])
            candidates=(list(result.get('files',[]))+list(result.get('artifacts',{}))) if isinstance(result,dict) else []
            for raw in candidates:
                path=Path(raw)
                if path.name in ARTIFACTS and path.is_absolute() and path.is_relative_to(out/'scenes'):
                    paths.add(path.resolve())
        except (TypeError,ValueError):pass
        for path in paths:owners.setdefault(path,set()).add(key)
    return owners


def _cache_key(row):return fingerprint([row['scene_id'],row['variant_id']])


def _remember(report,row,digest,decision):
    report['checked_sources'][_cache_key(row)]={'scene_id':row['scene_id'],'variant_id':row['variant_id'],
        'validation_report_sha256':digest,'pose_selection_version':POSE_SELECTION_VERSION,'decision':decision}


def _outputs_proof(out,destination,result):
    values={}
    for name in ARTIFACTS:
        path=_inside(out,destination/name);digest=result.get('artifacts',{}).get(str(path))
        if not digest:raise ValueError('Existing scene lacks complete recorded artifact proof: '+name)
        values[str(path)]=digest
    _proof(out,values)


def _fresh_proof(stage,fresh,old_report,old_bindings):
    if (fresh.get('status')!='validated_snapshot' or fresh.get('issues') or
            fresh.get('pose_selection_version')!=POSE_SELECTION_VERSION or fresh.get('item_manager_complete') is not True):
        raise ValueError('Staging render did not validate the current general pose selector')
    if fresh.get('scene_dimensions')!=old_report.get('scene_dimensions'):raise ValueError('Pose refresh changed the native stage dimensions')
    for key in ('snapshot_frame','root_timeline','bundle_sha256','item_manager_sha256'):
        if fresh.get(key)!=old_report.get(key):raise ValueError('Pose refresh changed the original snapshot or source identity: '+key)
    if 'root_objects' in old_report and fresh.get('root_objects')!=old_report['root_objects']:
        raise ValueError('Pose refresh changed the immutable root object count')
    bindings=_bindings(_read(stage/'solution_objects.json'))
    if bindings!=old_bindings or len(set(bindings))!=len(bindings):raise ValueError('Verified object IDs, manager keys or labels changed during pose refresh')
    if any(fresh.get(key)!=len(bindings) for key in ('identified_hidden_objects','solution_markers','findable_item_manager_entries','matched_item_manager_entries')):
        raise ValueError('Staging render did not retain every verified findable clue')
    if (not isinstance(fresh.get('files'),list) or len(fresh['files'])!=3 or
            set(fresh['files'])!={str(stage/name) for name in PNGS}):raise ValueError('Staging render has an unsafe scene PNG list')
    for name in ARTIFACTS:
        path=_plain(stage/name)
        if path.parent!=stage or not path.is_file():raise ValueError('Missing or redirected staged artifact: '+name)
        if name in PNGS:
            with Image.open(path) as image:
                if image.format!='PNG':raise ValueError('Staged image is not PNG: '+name)
                size=list(image.size);image.verify()
            if name!='scene_solution.png' and size!=old_report['scene_dimensions']:raise ValueError('Staged native image dimensions changed: '+name)


def _changed_images(stage,destination):
    changed=[]
    for name in PNGS:
        if sha256(stage/name)==sha256(destination/name):continue
        with Image.open(stage/name) as fresh,Image.open(destination/name) as original:
            if fresh.size!=original.size or ImageChops.difference(fresh.convert('RGBA'),original.convert('RGBA')).getbbox(alpha_only=False):
                changed.append(name)
    return changed


def refresh_poses(output,timeout=300,plan_only=False,renderer=None):
    """Audit passed nested scenes; publish only snapshots whose pixels change.

    Source-scene identities are never special-cased. Unchanged analysis is cached
    outside scene folders by verified validation-report hash and selector version.
    """
    out=_plain(output);_control(out/'batch/tests.sqlite3')
    checkpoint=require_checkpoint(out,writable=not plan_only)
    for path in (out/REPORT,out/(REPORT+'.tmp'),out/'batch'/JOURNAL,out/'batch'/(JOURNAL+'.tmp')):_control(path)
    checked=_previous(out)
    db=sqlite3.connect(checkpoint.as_uri()+('?mode=ro' if plan_only else '?mode=rw'),uri=True,isolation_level=None)
    db.row_factory=sqlite3.Row;backup=None
    report={'schema':1,'status':'in_progress','run_state':'running','pose_selection_version':POSE_SELECTION_VERSION,'plan_only':bool(plan_only),
        'summary':{'passed_variants':0,'processed_variants':0,'nested_variants':0,'refreshed_variants':0,'unchanged_variants':0,
                   'reused_variants':0,'non_nested_variants':0,'skipped_variants':0,'planned_variants':0},
        'results':[],'checked_sources':checked,'changed_scene_ids':[],'backup_path':None,
        'limitations':['Only structurally validated source-encoded nested poses are considered. Cached analysis reuse does not rerender or recertify unavailable inputs. Other passed scenes and unchanged image files are preserved.']}
    try:
        if _journal(out).exists():
            if plan_only:raise ValueError('A pending pose refresh needs recovery before a read-only plan')
            _recover(out,db)
        rows=list(db.execute("SELECT * FROM jobs WHERE active=1 AND status='passed' ORDER BY ordinal,variant_id"))
        report['summary']['passed_variants']=len(rows)
        owners=_artifact_owners(out,rows)
        for row in rows:
            stage=None;entry={'scene_id':row['scene_id'],'variant_id':row['variant_id'],'issues':[]}
            report['results'].append(entry)
            try:
                definition=json.loads(row['definition']);result=json.loads(row['result'])
                if not isinstance(result,dict):raise ValueError('Saved result metadata is not an object')
                if definition.get('scene',{}).get('scene_id')!=row['scene_id']:raise ValueError('Saved scene identities disagree')
                destination=_destination(out,definition);validation_path=_plain(destination/'validation_report.json')
                digest=sha256(validation_path)
                if result.get('artifacts',{}).get(str(validation_path))!=digest:raise ValueError('Existing validation metadata lacks an unchanged recorded proof')
                old_report=_read(validation_path)
                if (result.get('status')!='passed' or result.get('issues') or old_report.get('status')!='validated_snapshot' or old_report.get('issues')):
                    raise ValueError('Existing validation metadata is not a passed reconstruction')
                if not old_report.get('nested_snapshot_instances'):
                    entry['status']='non_nested';report['summary']['non_nested_variants']+=1;continue
                report['summary']['nested_variants']+=1
                _source_images(out,row);_outputs_proof(out,destination,result)
                cached=checked.get(_cache_key(row),{})
                if (old_report.get('pose_selection_version')==POSE_SELECTION_VERSION or
                        (isinstance(cached,dict) and cached.get('validation_report_sha256')==digest and
                         cached.get('pose_selection_version')==POSE_SELECTION_VERSION and
                         cached.get('decision') in ('checked_unchanged','refreshed','already_current'))):
                    entry['status']='reused';report['summary']['reused_variants']+=1
                    _remember(report,row,digest,'already_current' if old_report.get('pose_selection_version')==POSE_SELECTION_VERSION else 'checked_unchanged')
                    continue
                _proof(out,result['artifacts']);bundle,manager_path,manager=_inputs(out,definition,result)
                if sha256(bundle)!=old_report.get('bundle_sha256'):raise ValueError('Verified cached bundle differs from the original validation identity')
                manager_hash=hashlib.sha256(json.dumps(manager,sort_keys=True).encode()).hexdigest()
                if manager_hash!=old_report.get('item_manager_sha256'):raise ValueError('Verified cached Item Manager differs from the original validation identity')
                frame=old_report.get('snapshot_frame')
                if not isinstance(frame,int) or isinstance(frame,bool) or frame<1:raise ValueError('Old validated snapshot has no usable frame')
                old_bindings=_bindings(_read(destination/'solution_objects.json'))
                if len(set(old_bindings))!=len(old_bindings):raise ValueError('Existing scene has duplicate verified clue bindings')
                if plan_only:
                    entry['status']='planned';report['summary']['planned_variants']+=1;continue
                version=db.execute('PRAGMA data_version').fetchone()[0]
                print(f'Pose refresh: {row["scene_id"]} variant {row["variant_id"]}',flush=True)
                staging=_plain(out/STAGING);staging.mkdir(exist_ok=True)
                stage=Path(tempfile.mkdtemp(prefix='pose-',dir=staging))
                fresh=renderer(bundle,manager,stage,frame=frame) if renderer else _render(bundle,manager_path,stage,frame,timeout)
                _fresh_proof(stage,fresh,old_report,old_bindings)
                changed_images=_changed_images(stage,destination)
                _proof(out,result['artifacts'])
                if not changed_images:
                    entry['status']='checked_unchanged';report['summary']['unchanged_variants']+=1
                    _remember(report,row,digest,'checked_unchanged');continue
                old_hashes={name:sha256(destination/name) for name in ARTIFACTS}
                amendment={'pose_selection_version':POSE_SELECTION_VERSION,'original_render_fingerprint':row['fingerprint'],
                    'snapshot_frame':frame,'renderer_identity':code_identity(),'native_recertified':True,
                    'prior_validation_report_sha256':digest,'changed_images':changed_images,
                    'retained_image_sha256':{name:old_hashes[name] for name in PNGS if name not in changed_images}}
                fresh={**fresh,'files':result['files'],'pose_refresh':amendment}
                atomic_json(stage/'validation_report.json',fresh)
                publish=[name for name in ARTIFACTS if (name in changed_images or
                    (name not in PNGS and sha256(stage/name)!=old_hashes[name]))]
                key=(row['scene_id'],row['variant_id'])
                if any(owners.get(destination/name,set())-{key} for name in publish):
                    raise ValueError('Pose refresh target is also owned by another active passed scene version')
                new_result={**result,'status':'passed','issues':[],'artifacts':dict(result['artifacts']),'pose_refresh':amendment,
                    'pose_selection_version':POSE_SELECTION_VERSION,'solution_layout':fresh.get('solution_layout'),
                    'warnings':fresh.get('warnings',[]),'item_manager_complete':True,'hidden_objects':fresh['identified_hidden_objects'],
                    'named_pointers':fresh.get('named_pointers',fresh['solution_markers']),
                    'unverified_named_pointers':fresh.get('unverified_named_pointers',0),
                    'total_named_pointers':fresh.get('total_named_pointers',fresh['solution_markers']),
                    'annotation_issues':fresh.get('annotation_issues',[])}
                files=[]
                for name in publish:
                    source=_inside(out,stage/name);target=_inside(out,destination/name);saved=stage/('before-'+name)
                    shutil.copy2(target,saved)
                    if sha256(saved)!=old_hashes[name]:raise ValueError('Existing scene changed while staging: '+name)
                    new_digest=sha256(source);new_result['artifacts'][str(target)]=new_digest
                    files.append({'relative':target.relative_to(out).as_posix(),'staged_relative':source.relative_to(out).as_posix(),
                        'backup_relative':saved.relative_to(out).as_posix(),'old_sha256':old_hashes[name],'new_sha256':new_digest})
                if backup is None:
                    backup=_control(out/'batch'/('tests.pre-pose-refresh-'+uuid.uuid4().hex+'.sqlite3'))
                    with sqlite3.connect(backup) as target:db.backup(target);target.execute('PRAGMA journal_mode=DELETE')
                    report['backup_path']=str(backup)
                journal={'schema':1,'pose_selection_version':POSE_SELECTION_VERSION,'old_row':dict(row),
                    'new_result':json.dumps(new_result),'stage_relative':stage.relative_to(out).as_posix(),'files':files}
                db.execute('BEGIN IMMEDIATE')
                try:
                    current=db.execute('SELECT * FROM jobs WHERE scene_id=? AND variant_id=?',(row['scene_id'],row['variant_id'])).fetchone()
                    if dict(current)!=dict(row) or db.execute('PRAGMA data_version').fetchone()[0]!=version:
                        raise ValueError('Another command changed the checkpoint during pose staging')
                    _proof(out,result['artifacts'])
                    atomic_json(_journal(out),journal)
                    for item in files:os.replace(out/item['staged_relative'],out/item['relative'])
                    db.execute('UPDATE jobs SET result=? WHERE scene_id=? AND variant_id=?',(json.dumps(new_result),row['scene_id'],row['variant_id']))
                    db.execute('COMMIT')
                except BaseException:
                    if db.in_transaction:db.execute('ROLLBACK')
                    raise
                _recover(out,db);stage=None
                entry.update(status='refreshed',changed_images=changed_images,published_artifacts=publish)
                report['summary']['refreshed_variants']+=1;report['changed_scene_ids'].append(row['scene_id'])
                _remember(report,row,sha256(validation_path),'refreshed')
            except KeyboardInterrupt:
                if _journal(out).exists():_recover(out,db)
                report['status']='interrupted';entry['status']='interrupted';break
            except (ValueError,OSError,sqlite3.Error,KeyError,TypeError) as exc:
                if _journal(out).exists():
                    try:_recover(out,db)
                    except (ValueError,OSError,sqlite3.Error) as recovery:
                        entry['issues'].append('Recovery pending: '+str(recovery));stage=None
                entry['status']='skipped';entry['issues'].append(str(exc))
                report['summary']['skipped_variants']+=1;report['status']='partial'
            finally:
                if stage is not None and stage.exists() and not _journal(out).exists():shutil.rmtree(stage)
                report['summary']['processed_variants']+=1
                if (not plan_only and (entry.get('status') in ('checked_unchanged','refreshed') or
                        report['summary']['processed_variants']%10==0)):
                    _save_report(out,report)
        report['changed_scene_ids']=sorted(set(report['changed_scene_ids']))
        if report['status']=='in_progress':report['status']='completed'
        report['run_state']='interrupted' if report['status']=='interrupted' else 'completed'
        if not plan_only:_save_report(out,report)
        return report
    finally:db.close()
