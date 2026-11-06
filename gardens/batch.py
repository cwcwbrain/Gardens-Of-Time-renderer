"""Resumable, process-isolated test roster for every scene and candidate version."""
import csv
import hashlib
import importlib.metadata
import json
import platform
import re
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .download import atomic_json,read_json,sha256
from .scenes import variant_id

ROOT=Path(__file__).resolve().parents[1]
SCHEMA=1


def code_identity():
    digest=hashlib.sha256()
    files=sorted((ROOT/'gardens').glob('*.py'))+[ROOT/'gardens_renderer.py',ROOT/'render_scene.py',ROOT/'requirements.txt']
    for path in files:digest.update(path.name.encode()+b'\0'+path.read_bytes())
    versions={}
    for name in ('UnityPy','Pillow'):
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:versions[name]=None
    return {'schema':SCHEMA,'code':digest.hexdigest(),'python':sys.version,'platform':platform.system(),'packages':versions}


def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def reusable(result):
    """Never infer a pass from a checkpoint or file existence alone."""
    if not isinstance(result,dict):return False
    artifacts=result.get('artifacts',{});files=result.get('files',[])
    if not isinstance(artifacts,dict) or not artifacts or not isinstance(files,list) or len(files)!=3:return False
    try:
        paths=[Path(file).resolve() for file in files]
        if len(set(paths))!=3 or {p.name for p in paths}!={'scene_empty.png','scene_complete.png','scene_solution.png'}:return False
        if any(str(path) not in artifacts for path in paths):return False
        return all(Path(path).is_file() and sha256(path)==digest for path,digest in artifacts.items())
    except (OSError,ValueError,TypeError):return False



class Ledger:
    def __init__(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS scenes (id TEXT PRIMARY KEY, ordinal INTEGER, match_status TEXT, issues TEXT, active INTEGER)')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (scene_id TEXT, variant_id TEXT, ordinal INTEGER, fingerprint TEXT, status TEXT, definition TEXT, result TEXT, active INTEGER, PRIMARY KEY(scene_id,variant_id))')
        self.db.execute('CREATE INDEX IF NOT EXISTS active_job_status ON jobs(active,status,ordinal,variant_id)')
        self.db.execute('CREATE INDEX IF NOT EXISTS active_scene_status ON jobs(active,scene_id,status)')
        self.db.commit()

    def close(self):self.db.close()

    def prepare(self,scenes,matches,args,identity):
        lookup={s.scene_id:s for s in scenes};self.db.execute('UPDATE scenes SET active=0');self.db.execute('UPDATE jobs SET active=0')
        for row in matches:
            sid=row['scene_id'];scene=lookup[sid]
            self.db.execute('INSERT OR REPLACE INTO scenes VALUES (?,?,?,?,1)',(sid,row['order'],row['status'],json.dumps(row['issues'])))
            candidates=row['candidates'] if row['status'] in ('matched','ambiguous') else []
            if not candidates:candidates=[None]
            for candidate in candidates:
                vid=variant_id(candidate) if candidate else 'blocked'
                destination=Path(args.output).resolve()/'scenes'/sid
                if len(candidates)>1:destination=destination/('variant_'+vid)
                issues=list(row['issues'])
                if candidate:issues+=candidate['issues']
                else:
                    issues.extend(error for c in row['candidates'] for error in c['issues'])
                    issues.append('No eligible catalogue match: '+row['status'])
                if not re.fullmatch(r'[A-Za-z0-9_-]+',sid):issues.append('Unsafe scene output identifier')
                if candidate and (not candidate['bundles'] or any(not b['url'] for b in candidate['bundles'])):
                    issues.append('No complete, resolved bundle set')
                definition={'scene':asdict(scene),'candidate':candidate,'destination':str(destination),
                            'output':str(Path(args.output).resolve()),'frame':args.frame,
                            'offline':args.offline,'timeout':args.timeout,'retries':args.retries}
                fp=fingerprint({'identity':identity,'definition':definition,'test_timeout':args.test_timeout})
                definition['fingerprint']=fp
                prior=self.db.execute('SELECT * FROM jobs WHERE scene_id=? AND variant_id=?',(sid,vid)).fetchone()
                status='pending';result={}
                if issues:
                    status='blocked';result={'status':'blocked','issues':sorted(set(issues)),'files':[]}
                elif prior and prior['fingerprint']==fp and prior['status'] not in ('pending','running'):
                    old=json.loads(prior['result'])
                    if prior['status']=='passed' and reusable(old):status,result='passed',old
                    elif prior['status']!='passed' and not args.retry_failed:status,result=prior['status'],old
                self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?,?,?,?,?,?,1)',
                                (sid,vid,row['order'],fp,status,json.dumps(definition),json.dumps(result)))
        self.db.commit()

    def set_result(self,scene_id,vid,result):
        self.db.execute('UPDATE jobs SET status=?,result=? WHERE scene_id=? AND variant_id=?',
                        (result['status'],json.dumps(result),scene_id,vid));self.db.commit()

    def counts(self):
        return {row['status']:row['n'] for row in self.db.execute('SELECT status,count(*) n FROM jobs WHERE active=1 GROUP BY status')}

    def export(self,out,run_id,state,retry=None):
        out=Path(out);counts=self.counts();scene_counts={};report_warnings=[]
        def publish_csv(name):
            try:(out/(name+'.tmp')).replace(out/name)
            except PermissionError:
                message=f'{name} is locked; close it to refresh. SQLite checkpoint and {name}.tmp are current.'
                report_warnings.append(message);print(message,flush=True)
        with (out/'test_report.csv.tmp').open('w',encoding='utf-8-sig',newline='') as f:
            fields=['order','scene_id','variant_id','status','stage','issues','hidden_objects','named_pointers','bundle_count','bundle_urls','output','log','elapsed_seconds','validation_fingerprint','selected_for_retry']
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
            for job in self.db.execute('SELECT * FROM jobs WHERE active=1 ORDER BY ordinal,variant_id'):
                result=json.loads(job['result']);definition=json.loads(job['definition'])
                writer.writerow({'order':job['ordinal'],'scene_id':job['scene_id'],'variant_id':job['variant_id'],
                                 'status':job['status'],'stage':result.get('stage',''),'issues':json.dumps(result.get('issues',[]),ensure_ascii=False),
                                 'hidden_objects':result.get('hidden_objects',''),'named_pointers':result.get('named_pointers',''),
                                 'bundle_count':result.get('bundle_count',''),
                                 'bundle_urls':json.dumps([b['url'] for b in (definition.get('candidate') or {}).get('bundles',[])],ensure_ascii=False),
                                 'output':definition['destination'],
                                 'log':result.get('log',''),'elapsed_seconds':result.get('elapsed_seconds',''),
                                 'validation_fingerprint':job['fingerprint'],
                                 'selected_for_retry':(job['scene_id'],job['variant_id']) in retry['keys'] if retry else ''})
        publish_csv('test_report.csv')
        with (out/'scene_test_report.csv.tmp').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['order','scene_id','match_status','status','variants','passed','incomplete','failed','blocked','pending','running'])
            writer.writeheader()
            for scene in self.db.execute('SELECT * FROM scenes WHERE active=1 ORDER BY ordinal'):
                statuses={r['status']:r['n'] for r in self.db.execute('SELECT status,count(*) n FROM jobs WHERE active=1 AND scene_id=? GROUP BY status',(scene['id'],))}
                if statuses.get('running') or statuses.get('pending'):status='pending'
                elif set(statuses)=={'passed'}:status='passed'
                elif statuses.get('passed'):status='partial'
                elif statuses.get('failed'):status='failed'
                elif statuses.get('incomplete'):status='incomplete'
                else:status='blocked'
                scene_counts[status]=scene_counts.get(status,0)+1
                writer.writerow({'order':scene['ordinal'],'scene_id':scene['id'],'match_status':scene['match_status'],
                                 'status':status,'variants':sum(statuses.values()),**statuses})
        publish_csv('scene_test_report.csv')
        summary={'run_id':run_id,'phase':'all-scene-tests','status':state,'scenes':sum(scene_counts.values()),
                 'scene_results':scene_counts,'variant_results':counts,'variants':sum(counts.values()),
                 'finished_variants':sum(n for status,n in counts.items() if status not in ('pending','running')),
                 'coverage_complete':not counts.get('pending') and not counts.get('running'),
                 'all_passed':bool(counts) and set(counts)=={'passed'},
                 'worker_outcomes':sum(counts.get(s,0) for s in ('passed','incomplete','failed')),
                 'report_warnings':report_warnings,
                 'limitations':['Passed means structural/metadata/visibility snapshot checks; game pixel parity is unverified.',
                                'Unsupported FTD/STD/FTO and geometry/effects remain explicitly incomplete.']}
        if retry:
            summary.update({k:v for k,v in retry.items() if k!='keys'})
            summary['limitations'].append('Selective retry preserves all unselected results and their original validation fingerprints; prior passes are not revalidated by this build.')
        atomic_json(out/'all_scene_test_summary.json',summary);atomic_json(out/'summary.json',summary)
        return summary


def execute_job(job,job_path,result_path,log_path,timeout):
    atomic_json(job_path,job);result_path.unlink(missing_ok=True)
    started=time.monotonic()
    try:
        with log_path.open('w',encoding='utf-8') as log:
            process=subprocess.run([sys.executable,'-m','gardens.batch_worker','--job',str(job_path),'--result',str(result_path)],
                                   cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
        result=read_json(result_path)
        expected={'passed':0,'incomplete':1,'failed':2}
        if (not isinstance(result,dict) or result.get('fingerprint')!=job['fingerprint'] or result.get('status') not in expected or
                process.returncode!=expected.get(result.get('status'))):
            result={'status':'failed','issues':[f'Worker exited {process.returncode} without a matching valid result'],'files':[]}
        elif result['status']=='passed' and not reusable(result):
            result={'status':'failed','issues':['Worker claimed a pass but its artifact hashes or image list did not validate'],'files':[]}
    except subprocess.TimeoutExpired:
        result={'status':'failed','issues':[f'Worker timed out after {timeout} seconds'],'files':[]}
    except OSError as exc:
        result={'status':'failed','issues':[f'Cannot start worker: {exc}'],'files':[]}
    result.update(log=str(log_path),elapsed_seconds=round(time.monotonic()-started,3))
    return result


def retry_incomplete(args,runner=None,update_metadata=None):
    """Retry incomplete and optionally unfinished saved variants, without resets.

    Unattempted/in-flight jobs keep their original checkpoint until a new
    outcome is committed. A limit, interrupt or abrupt shutdown can therefore
    resume with the same command. Pending/running jobs require explicit opt-in.
    """
    out=Path(args.output).resolve();work=out/'batch';checkpoint=work/'tests.sqlite3'
    if not checkpoint.is_file():
        raise ValueError(f'No existing test checkpoint at {checkpoint}. Use the same --output folder as the finished run; no scenes were changed.')
    ledger=Ledger(checkpoint);runner=runner or execute_job;run_id=uuid.uuid4().hex
    try:
        rows=list(ledger.db.execute('SELECT * FROM jobs WHERE active=1 ORDER BY ordinal,variant_id'))
        include_pending=getattr(args,'include_pending',False)
        selected_states={'incomplete','pending','running'} if include_pending else {'incomplete'}
        selected=[row for row in rows if row['status'] in selected_states]
        keys={(row['scene_id'],row['variant_id']) for row in selected}
        identity=code_identity()
        if selected and not args.plan_only and any(v is None for v in identity['packages'].values()):
            raise ValueError('Install requirements.txt before running scene workers')
        selected_statuses={status:sum(row['status']==status for row in selected)
                           for status in ('incomplete','pending','running') if any(row['status']==status for row in selected)}
        retry={'keys':keys,'retry_scope':'incomplete_and_pending' if include_pending else 'incomplete_only',
               'selected_variants':len(selected),'selected_statuses':selected_statuses,
               'preserved_variants':len(rows)-len(selected),
               'preserved_passed_variants':sum(row['status']=='passed' for row in rows),
               'attempted_variants':0,'remaining_selected_variants':len(selected),
               'renderer_identity':identity}
        if update_metadata is not None:
            retry.update(retry_scope='incomplete_pending_and_new' if include_pending else 'incomplete_and_new',
                         update_discovery=update_metadata)
        # Preflight the whole selection before workers or report writes. A saved
        # destination must not redirect a retry over a preserved scene/version.
        preserved=set()
        for row in ledger.db.execute('SELECT * FROM jobs'):
            if (row['scene_id'],row['variant_id']) in keys:continue
            result=json.loads(row['result'])
            preserved.update(Path(file).resolve() for file in result.get('files',[]))
            if row['status']=='passed':
                preserved.add(Path(json.loads(row['definition'])['destination']).resolve())
        preserved_ancestors={parent for path in preserved for parent in path.parents}
        jobs=[];scene_root=out/'scenes'
        for row in selected:
            definition=json.loads(row['definition']);definition.pop('fingerprint',None)
            destination=Path(definition['destination']).resolve()
            if destination==scene_root or not destination.is_relative_to(scene_root):
                raise ValueError(f"Saved destination for {row['scene_id']} is outside {scene_root}; no scenes were changed")
            if destination in preserved_ancestors or any(parent in preserved for parent in (destination,*destination.parents)):
                raise ValueError(f"Saved destination for {row['scene_id']} overlaps preserved outputs; no scenes were changed")
            definition.update(output=str(out),offline=args.offline,timeout=args.timeout,retries=args.retries)
            if args.frame is not None:definition['frame']=args.frame
            fp=fingerprint({'identity':identity,'definition':definition,'test_timeout':args.test_timeout})
            definition['fingerprint']=fp;jobs.append((row,definition))
        label='incomplete/pending/running' if include_pending else 'saved incomplete'
        if update_metadata is not None:label+='/new'
        print(f"Retrying only {len(selected)} {label} variants; preserving {retry['preserved_passed_variants']} passed variants and all other results.",flush=True)
        ledger.export(out,run_id,'planned' if args.plan_only else 'in_progress',retry)
        outcomes=[];active_scene=None;started_scenes=0
        try:
            if not args.plan_only:
                for row,definition in jobs:
                    if row['scene_id']!=active_scene:
                        if args.test_limit is not None and started_scenes>=args.test_limit:break
                        active_scene=row['scene_id'];started_scenes+=1
                    key=hashlib.sha256((row['scene_id']+'\0'+row['variant_id']).encode()).hexdigest()[:24]
                    print(f"Process saved {row['status']} scene {row['ordinal']} {row['scene_id']} variant {row['variant_id']}",flush=True)
                    result=runner(definition,work/(key+'.job.json'),work/(key+'.result.json'),work/(key+'.log'),args.test_timeout)
                    # Only this selected row changes; every preserved row keeps
                    # its original fingerprint, definition and result verbatim.
                    ledger.db.execute('UPDATE jobs SET fingerprint=?,definition=?,status=?,result=? WHERE scene_id=? AND variant_id=?',
                                      (definition['fingerprint'],json.dumps(definition),result['status'],json.dumps(result),row['scene_id'],row['variant_id']))
                    ledger.db.commit();outcomes.append(result['status'])
                    retry['attempted_variants']=len(outcomes);retry['remaining_selected_variants']=len(selected)-len(outcomes)
                    print(f"  {result['status']}: {'; '.join(result.get('issues',[])) or 'snapshot checks passed'}",flush=True)
                    if len(outcomes)%10==0:ledger.export(out,run_id,'in_progress',retry)
            state='planned' if args.plan_only else ('paused' if retry['remaining_selected_variants'] else 'completed')
            summary=ledger.export(out,run_id,state,retry)
            print(json.dumps(summary,indent=2),flush=True)
            if args.plan_only:return 0
            return 0 if not retry['remaining_selected_variants'] and all(status=='passed' for status in outcomes) else 1
        except KeyboardInterrupt:
            ledger.export(out,run_id,'interrupted',retry)
            resume='--update --include-pending' if include_pending else ('--update' if update_metadata is not None else '--retry-incomplete')
            print(f'Interrupted. Untested variants retain their original checkpoints; repeat {resume} to resume them.',flush=True)
            return 130
    finally:ledger.close()


def run_batch(scenes,matches,args,runner=None):
    if not scenes:raise ValueError('The scene CSV contains no unique scenes to test')
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    work=out/'batch';work.mkdir(exist_ok=True)
    ledger=Ledger(work/'tests.sqlite3');run_id=uuid.uuid4().hex
    identity=code_identity();runner=runner or execute_job
    if not args.plan_only and any(v is None for v in identity['packages'].values()):
        ledger.close();raise ValueError('Install requirements.txt before running scene workers')
    try:
        ledger.prepare(scenes,matches,args,identity)
        ledger.export(out,run_id,'planned' if args.plan_only else 'in_progress')
        if not args.plan_only:
            active_scene=None;started_scenes=0;completed=0
            while True:
                row=ledger.db.execute("SELECT * FROM jobs WHERE active=1 AND status='pending' ORDER BY ordinal,variant_id LIMIT 1").fetchone()
                if row is None:break
                if row['scene_id']!=active_scene:
                    if args.test_limit is not None and started_scenes>=args.test_limit:break
                    active_scene=row['scene_id'];started_scenes+=1
                definition=json.loads(row['definition'])
                key=hashlib.sha256((row['scene_id']+'\0'+row['variant_id']).encode()).hexdigest()[:24]
                ledger.set_result(row['scene_id'],row['variant_id'],{'status':'running','issues':[],'files':[]})
                print(f"Test scene {row['ordinal']}/{len(scenes)} {row['scene_id']} variant {row['variant_id']}",flush=True)
                result=runner(definition,work/(key+'.job.json'),work/(key+'.result.json'),work/(key+'.log'),args.test_timeout)
                ledger.set_result(row['scene_id'],row['variant_id'],result);completed+=1
                print(f"  {result['status']}: {'; '.join(result.get('issues',[])) or 'snapshot checks passed'}",flush=True)
                if completed%10==0:ledger.export(out,run_id,'in_progress')
        counts=ledger.counts()
        state='planned' if args.plan_only else ('paused' if counts.get('pending') or counts.get('running') else 'completed')
        summary=ledger.export(out,run_id,state)
        print(json.dumps(summary,indent=2),flush=True)
        if args.plan_only:return 0
        return 0 if summary['coverage_complete'] and set(counts)<= {'passed'} else 1
    except KeyboardInterrupt:
        ledger.export(out,run_id,'interrupted')
        print('Interrupted. Checkpoints saved; repeat the command to resume.',flush=True)
        return 130
    finally:ledger.close()
