"""Exact official-name lookup and read-only discovery of verified scene PNGs.

Missing bundles are not image proof. Untracked legacy PNGs without historical
hashes are deliberately not eligible for automatic reuse.
"""
import difflib
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path, PurePosixPath

from PIL import Image

from .download import sha256
from .named_export import ROLES, _plain, _source_images


def normalize_name(value):
    if not isinstance(value,str):return ''
    value=unicodedata.normalize('NFKC',value).casefold()
    value=value.translate(str.maketrans({'\u2018':"'",'\u2019':"'",'\u02bc':"'"}))
    return ' '.join(value.split())


def _mappings(mappings):
    if not isinstance(mappings,dict):return []
    result=[]
    for sid,value in mappings.items():
        if not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',sid) or not isinstance(value,dict):continue
        if value.get('scene_id',sid)!=sid:continue
        result.append({**value,'scene_id':sid})
    return sorted(result,key=_order)


def _order(value):
    order=value.get('order')
    return (order if isinstance(order,int) and not isinstance(order,bool) and order>0 else 10**12,
            value['scene_id'].casefold(),value['scene_id'])


def _aliases(value):
    names=[value.get(key) for key in ('title','scene_title','scene_id')]
    appearances=value.get('primary_appearances',[])
    if isinstance(appearances,list):names.extend(a.get('title') for a in appearances if isinstance(a,dict))
    return {normalized for name in names if (normalized:=normalize_name(name))}


def resolve_scene_name(query,mappings):
    """Return every distinct exact ID match; replay titles never add duplicates."""
    normalized=normalize_name(query)
    if not normalized:return []
    return [value for value in _mappings(mappings) if normalized in _aliases(value)]


def suggestions(query,mappings,limit=5):
    """Display-only near matches. These must never authorize a scene download."""
    normalized=normalize_name(query)
    if not normalized or limit<=0:return []
    scored=[]
    for value in _mappings(mappings):
        score=max((difflib.SequenceMatcher(None,normalized,name).ratio() for name in _aliases(value)),default=0)
        if score>=.45:scored.append((score,value))
    scored.sort(key=lambda entry:(-entry[0],_order(entry[1])))
    return [value for _,value in scored[:limit]]


def _json(path):
    path=_plain(path)
    value=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(value,dict):raise ValueError('Expected a keyed scene proof')
    return value


def _png(path,digest):
    if not isinstance(digest,str) or not re.fullmatch(r'[0-9a-f]{64}',digest):return False
    if not path.is_file() or sha256(path)!=digest:return False
    with Image.open(path) as image:
        if image.format!='PNG':return False
        image.verify()
    return True


def _record(sid,vid,directory,files,source):
    return {'scene_id':sid,'variant_id':vid,'directory':str(directory),
            'files':[str(file) for file in files],'source':source}


def _batch(out,sid):
    checkpoint=_plain(out/'batch/tests.sqlite3')
    if not checkpoint.is_file():return []
    records=[]
    with sqlite3.connect(checkpoint.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        rows=db.execute("SELECT * FROM jobs WHERE active=1 AND status='passed' AND scene_id=? ORDER BY variant_id",(sid,))
        for row in rows:
            try:
                definition=json.loads(row['definition']);result=json.loads(row['result']);vid=row['variant_id']
                if definition.get('scene',{}).get('scene_id')!=sid or result.get('issues'):continue
                if not isinstance(vid,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',vid):continue
                directory=_plain(definition['destination']);root=out/'scenes'/sid
                if directory not in (root,root/('variant_'+vid),out/'scenes/_new_versions'/sid/('variant_'+vid)):continue
                images=_source_images(out,row)
                records.append(_record(sid,vid,directory,[images[name][0] for name in ROLES],'batch'))
            except (OSError,ValueError,TypeError,KeyError,SyntaxError):continue
    return records


def _relative(root,raw):
    if not isinstance(raw,str) or not raw or any(c in raw for c in ('\\','\0',':')):raise ValueError('Unsafe named image path')
    relative=PurePosixPath(raw)
    if relative.is_absolute() or '..' in relative.parts or relative==PurePosixPath('.'):raise ValueError('Unsafe named image path')
    path=_plain(root.joinpath(*relative.parts))
    if path==root or not path.is_relative_to(root):raise ValueError('Named image escapes its export directory')
    return path


def _direct(out,sid):
    root=_plain(out/'scenes'/sid)
    if not root.is_dir():return []
    records=[]
    for directory in (root,*sorted(root.glob('variant_*'))):
        try:
            directory=_plain(directory);receipt=_json(directory/'scene_images_receipt.json')
            vid=receipt.get('variant_id')
            if (receipt.get('schema')!=1 or receipt.get('scene_id')!=sid or receipt.get('status')!='passed' or
                    not isinstance(vid,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',vid) or
                    directory not in (root,root/('variant_'+vid))):continue
            validation=_plain(directory/'validation_report.json')
            if not validation.is_file() or sha256(validation)!=receipt.get('validation_report_sha256'):continue
            report=_json(validation);proof=receipt.get('images')
            if report.get('status')!='validated_snapshot' or report.get('issues') or not isinstance(proof,dict) or set(proof)!=set(ROLES):continue
            if all(_png(_plain(directory/name),proof[name]) for name in ROLES):
                records.append(_record(sid,vid,directory,[directory/name for name in ROLES],'direct'))
        except (OSError,ValueError,TypeError,KeyError,SyntaxError):continue
    return records


def _named(root,sid):
    root=_plain(root);manifest=_json(root/'named_export_manifest.json')
    if manifest.get('schema') not in (1,2) or not isinstance(manifest.get('files'),list):return []
    owned=manifest.get('owned_files')
    if not isinstance(owned,dict):return []
    groups=defaultdict(list)
    for entry in manifest['files']:
        if not isinstance(entry,dict) or entry.get('scene_id')!=sid:continue
        vid=entry.get('variant_id')
        if not isinstance(vid,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',vid):continue
        try:path=_relative(root,entry.get('relative_path'))
        except (OSError,ValueError,TypeError):continue
        groups[(vid,path.parent)].append((entry,path))
    records=[]
    expected_roles={role for role,_ in ROLES.values()}
    for (vid,directory),entries in sorted(groups.items(),key=lambda item:(item[0][0],str(item[0][1]))):
        try:
            if (len(entries)!=3 or len({path for _,path in entries})!=3 or
                    {entry.get('role') for entry,_ in entries}!=expected_roles):continue
            files={}
            for entry,path in entries:
                digest=entry.get('destination_sha256')
                if path.suffix.casefold()!='.png' or owned.get(entry['relative_path'])!=digest:break
                if entry.get('source_sha256',digest)!=digest or not _png(path,digest):break
                files[entry['role']]=path
            if set(files)==expected_roles:
                records.append(_record(sid,vid,directory,[files[role] for role,_ in ROLES.values()],'named'))
        except (OSError,ValueError,TypeError,KeyError,SyntaxError):continue
    return records


def _misc(out,sid):
    root=_plain(out/'misc');manifest=_json(root/'misc_scene_index.json')
    if manifest.get('schema')!=1 or not isinstance(manifest.get('variants'),list):return []
    records=[]
    for entry in manifest['variants']:
        if not isinstance(entry,dict) or entry.get('scene_id')!=sid:continue
        try:
            vid=entry.get('variant_id');result=entry.get('result')
            if not isinstance(vid,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',vid):continue
            if not isinstance(result,dict) or result.get('status')!='passed' or result.get('issues'):continue
            if isinstance(entry.get('relative_directory'),str) and isinstance(entry.get('image_receipts'),dict):
                directory=_relative(root,entry['relative_directory']);receipts=entry['image_receipts']
                if set(receipts)==set(ROLES) and all(_png(_plain(directory/name),receipts[name]) for name in ROLES):
                    records.append(_record(sid,vid,directory,[directory/name for name in ROLES],'misc'))
                continue
            raw=entry['directory']
            if not isinstance(raw,str) or not raw:continue
            directory=_plain(Path(raw) if Path(raw).is_absolute() else root/raw)
            if directory==root or not directory.is_relative_to(root):continue
            files=result.get('files');artifacts=result.get('artifacts')
            if not isinstance(files,list) or len(files)!=3 or not isinstance(artifacts,dict):continue
            verified={}
            for raw in files:
                path=_plain(raw)
                if path.parent!=directory or path.name not in ROLES or path.name in verified:break
                if not _png(path,artifacts.get(str(path))):break
                verified[path.name]=path
            if set(verified)==set(ROLES):
                records.append(_record(sid,vid,directory,[verified[name] for name in ROLES],'misc'))
        except (OSError,ValueError,TypeError,KeyError,SyntaxError):continue
    return records


def existing_scene_images(out,sid):
    """Find complete hash-proven snapshots without modifying files or progress."""
    if not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',sid):return []
    try:out=_plain(out)
    except (OSError,ValueError,TypeError):return []
    records=[]
    try:records.extend(_batch(out,sid))
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):pass
    try:records.extend(_direct(out,sid))
    except (OSError,ValueError,TypeError,KeyError):pass
    roots=[out/'named_scenes']
    try:
        custom=_json(out/'naming_report.json').get('export',{}).get('destination')
        if isinstance(custom,str) and Path(custom).is_absolute():roots.append(Path(custom))
    except (OSError,ValueError,TypeError,AttributeError):pass
    seen=set()
    for root in roots:
        try:
            root=_plain(root)
            if root in seen:continue
            seen.add(root);records.extend(_named(root,sid))
        except (OSError,ValueError,TypeError,KeyError):pass
    try:records.extend(_misc(out,sid))
    except (OSError,ValueError,TypeError,KeyError):pass
    unique={}
    for record in records:unique.setdefault((record['variant_id'],record['directory']),record)
    return list(unique.values())
