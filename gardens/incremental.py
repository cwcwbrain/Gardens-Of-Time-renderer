"""Append verified new scene/version matches without replacing saved results."""
import json
import re
from dataclasses import asdict
from pathlib import Path

from .batch import Ledger,fingerprint
from .checkpoint import require_checkpoint
from .scenes import variant_id

VERSION_NAMESPACE='_new_versions'


def enroll_updates(out,scenes,matches,*,complete):
    """Queue new matches and reserve authoritative chronology for missing assets.

    The caller supplies verified scene identity/order. Previously saved rows stay
    unchanged, except update-created blocked placeholders can be retired once a
    real match becomes available. Partial catalogue coverage never changes rows.
    """
    out=Path(out).resolve();checkpoint=out/'batch/tests.sqlite3'
    if not checkpoint.is_file():
        raise ValueError(f'No existing test checkpoint at {checkpoint}; no scenes were changed')
    stats={'added_scenes':0,'added_variants':0,'discovery_complete':bool(complete),
           'ignored_inactive':0,'discovered_blocked':0,'retired_placeholders':0,'blocked_update_variants':0}
    if not complete:return stats
    require_checkpoint(out)
    ledger=Ledger(checkpoint)
    try:
        saved_scenes=list(ledger.db.execute('SELECT * FROM scenes'))
        saved_jobs=list(ledger.db.execute('SELECT * FROM jobs'))
        known={};keys=set();maximum=0;placeholders={}
        for row in saved_scenes:
            folded=row['id'].casefold()
            if folded in known:
                raise ValueError(f"Checkpoint contains case-colliding scene IDs: {row['id']}")
            known[folded]=dict(row);maximum=max(maximum,row['ordinal'])
        for row in saved_jobs:
            folded=row['scene_id'].casefold()
            if folded not in known or known[folded]['id']!=row['scene_id']:
                raise ValueError(f"Checkpoint has inconsistent scene identity: {row['scene_id']}")
            keys.add((folded,row['variant_id']));maximum=max(maximum,row['ordinal'])
            if (row['variant_id']=='blocked' and row['status']=='blocked' and row['active'] and
                    json.loads(row['definition']).get('discovered_by_update') is True):
                placeholders[folded]=row['scene_id']
        if VERSION_NAMESPACE.casefold() in known:
            raise ValueError(f'Reserved update namespace collides with saved scene {VERSION_NAMESPACE}')
        namespace=out/'scenes'/VERSION_NAMESPACE
        if namespace.exists() and not namespace.is_dir():
            raise ValueError(f'Reserved update namespace is not a directory: {namespace}')
        matched={}
        for row in matches:
            folded=row['scene_id'].casefold()
            if folded in matched:
                raise ValueError(f"Duplicate scene match rows: {row['scene_id']}")
            matched[folded]=row
        scene_inserts=[];job_inserts=[];retire=[];original_ids=set(known)
        for scene in sorted(scenes,key=lambda value:value.order):
            folded=scene.scene_id.casefold();row=matched.get(folded)
            if not row:continue
            if folded in known and not known[folded]['active']:
                stats['ignored_inactive']+=1;continue
            if not re.fullmatch(r'[A-Za-z0-9_-]+',scene.scene_id):
                raise ValueError(f'Unsafe scene output identifier: {scene.scene_id}')
            if folded==VERSION_NAMESPACE.casefold():
                raise ValueError(f'Reserved update namespace collides with scene {scene.scene_id}')
            valid={}
            if row['status'] in ('matched','ambiguous') and not row['issues']:
                for item in row['candidates']:
                    if item['issues'] or not item['bundles']:continue
                    if not all(isinstance(b.get('url'),str) and b['url'] for b in item['bundles']):continue
                    valid.setdefault(variant_id(item),item)
            is_new=folded not in known
            if is_new:
                maximum+=1
                saved={'id':scene.scene_id,'ordinal':maximum,'active':1}
                known[folded]=saved
                scene_inserts.append((saved['id'],maximum,row['status'],json.dumps(row['issues']),1))
            saved=known[folded];sid=saved['id'];ordinal=saved['ordinal']
            queue=list(valid.items())
            if not valid and is_new:queue=[('blocked',None)]
            for vid,item in queue:
                if (folded,vid) in keys:continue
                if folded in original_ids and item is not None:
                    destination=namespace/sid/('variant_'+vid)
                else:
                    destination=out/'scenes'/sid
                    if item is not None and len(valid)>1:destination=destination/('variant_'+vid)
                destination=destination.resolve()
                if not destination.is_relative_to(out/'scenes'):
                    raise ValueError(f'Update destination is outside scene output: {destination}')
                if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
                    raise ValueError(f'New update destination already contains untracked outputs: {destination}')
                metadata=asdict(scene);metadata.update(scene_id=sid,order=ordinal)
                definition={'scene':metadata,'candidate':item,'destination':str(destination),
                            'output':str(out),'frame':None,'offline':False,'timeout':30,'retries':2,
                            'discovered_by_update':True}
                fp=fingerprint({'queued_for_update':True,'definition':definition});definition['fingerprint']=fp
                if item is None:
                    issues=list(row['issues'])
                    for choice in row['candidates']:issues.extend(choice['issues'])
                    issues.append('No eligible catalogue match: '+row['status'])
                    result={'status':'blocked','issues':sorted(set(issues)),'files':[]}
                    stats['discovered_blocked']+=1
                else:
                    result={'status':'incomplete','issues':['New scene/version queued by update'],'files':[]}
                    stats['added_variants']+=1
                job_inserts.append((sid,vid,ordinal,fp,result['status'],json.dumps(definition),json.dumps(result),1))
                keys.add((folded,vid))
            if valid and folded in placeholders and sid not in retire:retire.append(sid)
        # Validate the entire proposed enrolment before this single transaction.
        # INSERT (not REPLACE) also fails closed if another run races this merge.
        with ledger.db:
            ledger.db.executemany('INSERT INTO scenes VALUES (?,?,?,?,?)',scene_inserts)
            ledger.db.executemany('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)',job_inserts)
            ledger.db.executemany("UPDATE jobs SET active=0 WHERE scene_id=? AND variant_id='blocked' AND status='blocked'",[(sid,) for sid in retire])
        stats.update(added_scenes=len(scene_inserts),retired_placeholders=len(retire),
                     blocked_update_variants=len(placeholders)-len(retire)+stats['discovered_blocked'])
        return stats
    finally:ledger.close()
