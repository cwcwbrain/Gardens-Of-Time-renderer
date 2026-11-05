"""Copy verified scene snapshots into folders using authoritative game names.

The original scene tree and read-only checkpoint remain the source of truth.
Exports are independent copies; their manifest records both naming and image proof.
"""
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import unicodedata
from pathlib import Path

from PIL import Image

from .download import sha256

ROLES={'scene_empty.png':('empty','Empty'),'scene_complete.png':('complete','Complete'),
       'scene_solution.png':('solution','Solution')}
MANIFEST='named_export_manifest.json'
INDEX='named_scene_index.csv'
SCHEMA=1


def _units(text):return len(text.encode('utf-16-le'))//2


def _truncate(text,limit):
    result=[];size=0
    for char in text:
        width=2 if ord(char)>0xffff else 1
        if size+width>limit:break
        result.append(char);size+=width
    return ''.join(result)


def _digest(text):return hashlib.sha256(text.encode('utf-8',errors='replace')).hexdigest()[:10]


def safe_component(text,limit=90):
    """Windows-safe component with stable truncation, including UTF-16 limits."""
    value=unicodedata.normalize('NFC',str(text))
    value=''.join('_' if char in '<>:"/\\|?*' or unicodedata.category(char) in ('Cc','Cs') else char for char in value)
    value=value.strip().rstrip('. ') or '_'
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?',value,re.I):value='_'+value
    if _units(value)>limit:value=_truncate(value,max(1,limit-11)).rstrip('. ')+'~'+_digest(str(text))
    return value


def _plain(path):
    """Reject symlink/junction redirection before resolving a controlled path."""
    path=Path(path).absolute()
    for part in (path,*path.parents):
        if part.is_symlink() or (hasattr(part,'is_junction') and part.is_junction()):
            raise ValueError(f'Symlink or junction path is not allowed for named export: {part}')
    return path.resolve()


def _overlap(a,b):return a==b or a.is_relative_to(b) or b.is_relative_to(a)


def _atomic_bytes(path,data):
    path=_plain(path);path.parent.mkdir(parents=True,exist_ok=True)
    handle=tempfile.NamedTemporaryFile(prefix='.named-export-',dir=path.parent,delete=False)
    temporary=Path(handle.name)
    try:
        with handle:handle.write(data)
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def _copy(source,destination,digest):
    destination=_plain(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    handle=tempfile.NamedTemporaryFile(prefix='.named-export-',dir=destination.parent,delete=False)
    temporary=Path(handle.name);handle.close()
    try:
        shutil.copyfile(source,temporary)
        if sha256(temporary)!=digest:raise ValueError('Source changed while copying')
        os.replace(temporary,destination)
    finally:temporary.unlink(missing_ok=True)


def _source_images(out,row):
    definition=json.loads(row['definition']);result=json.loads(row['result'])
    if row['status']!='passed' or result.get('status')!='passed':raise ValueError('Stored source proof is not a passed result')
    root=_plain(out/'scenes');destination=_plain(definition['destination'])
    if destination==root or not destination.is_relative_to(root):raise ValueError('Source destination lies outside output/scenes')
    files=result.get('files');artifacts=result.get('artifacts',{})
    if not isinstance(files,list) or len(files)!=3 or not isinstance(artifacts,dict):raise ValueError('Passed result has no complete PNG proof')
    images={}
    for raw in files:
        path=_plain(raw)
        if path.parent!=destination or path.name not in ROLES or path.name in images:
            raise ValueError('Passed PNG list has an unsafe, duplicate or noncanonical source')
        expected=artifacts.get(str(path))
        if not isinstance(expected,str) or not re.fullmatch(r'[0-9a-f]{64}',expected) or not path.is_file() or sha256(path)!=expected:
            raise ValueError(f'Passed PNG hash proof does not match: {path.name}')
        with Image.open(path) as image:
            if image.format!='PNG':raise ValueError(f'Passed image is not PNG: {path.name}')
            image.verify()
        images[path.name]=(path,expected)
    if set(images)!=set(ROLES):raise ValueError('Passed result is missing a canonical scene PNG')
    return images


def _mapping(value,row):
    if not isinstance(value,dict):raise ValueError('No verified official name mapping for scene')
    for field in ('title','chapter_title'):
        if not isinstance(value.get(field),str) or not value[field].strip():raise ValueError(f'Missing official {field}')
    chapter=value.get('chapter_number');order=value.get('order',row['ordinal'])
    if not isinstance(chapter,int) or isinstance(chapter,bool) or chapter<1:raise ValueError('Missing valid official chapter number')
    if not isinstance(order,int) or isinstance(order,bool) or order<1:raise ValueError('Missing valid corrected scene order')
    return {**value,'order':order}


def _chapter_title(mapping):
    number=mapping['chapter_number'];title=mapping['chapter_title']
    title=re.sub(r'^\s*Chapter\s+0*'+str(number)+r'\b\s*(?:[-:–—]\s*)?','',title,flags=re.I).strip()
    return f'Chapter {number:03d}'+(' - '+title if title else '')


def _previous(root):
    path=_plain(root/MANIFEST)
    if not path.exists():return {'files':[],'owned_files':{}}
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict) or value.get('schema')!=SCHEMA or not isinstance(value.get('owned_files'),dict) or not isinstance(value.get('files'),list):
        raise ValueError('Existing named export manifest is not a recognized ownership manifest')
    return value


def _destination_state(source,target,digest,previous,relative):
    target=_plain(target)
    if not target.exists():return 'write'
    if not target.is_file():raise ValueError('Named destination is not a regular file')
    actual=sha256(target);owned=previous.get(relative)
    linked=os.path.samefile(source,target)
    if actual==digest and not linked:return 'reuse'
    if owned!=actual:raise ValueError('Named destination conflicts with an untracked or edited file')
    return 'write'


def export_named(out,mappings,provenance=None,destination=None):
    """Export active passed variants, never altering their source or checkpoint.

    summary.exported_variants includes successful new and reused variants;
    summary.reused_variants is its subset requiring no PNG writes.
    """
    if not isinstance(mappings,dict):raise ValueError('Official scene mappings must be keyed by exact scene ID')
    out=_plain(out);root=_plain(destination if destination is not None else out/'named_scenes')
    if root.exists() and not root.is_dir():raise ValueError('Named destination is not a directory')
    protected=[out/name for name in ('scenes','batch','bundles','item_managers','catalogues','.cache','naming','chapter_metadata','remote_config')]
    if any(_overlap(root,_plain(path)) for path in protected):raise ValueError('Named destination overlaps protected scene, checkpoint or cache output')
    checkpoint=_plain(out/'batch/tests.sqlite3')
    if not checkpoint.is_file():raise ValueError(f'No existing scene checkpoint at {checkpoint}')
    with sqlite3.connect(checkpoint.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        rows=list(db.execute("SELECT * FROM jobs WHERE active=1 AND status='passed' ORDER BY ordinal,scene_id,variant_id"))
    previous=_previous(root);ownership=dict(previous['owned_files'])
    tracked={entry['relative_path']:entry for entry in previous['files'] if isinstance(entry,dict) and isinstance(entry.get('relative_path'),str)}
    summary={'passed_variants':len(rows),'exported_variants':0,'reused_variants':0,'skipped_variants':0,'written_pngs':0}
    report={'schema':SCHEMA,'status':'completed','source_output':str(out),'destination':str(root),
            'provenance':provenance,'summary':summary,'exports':[],'issues':[],
            'manifest_path':str(root/MANIFEST),'index_path':str(root/INDEX)}
    plans=[]
    for row in rows:
        entry={'scene_id':row['scene_id'],'variant_id':row['variant_id'],'source_proof_fingerprint':row['fingerprint'],'issues':[],'files':[]}
        report['exports'].append(entry)
        try:
            mapping=_mapping(mappings.get(row['scene_id']),row)
            variant='variant_'+row['variant_id']
            if not re.fullmatch(r'variant_[A-Za-z0-9_-]+',variant) or _units(variant)>90:raise ValueError('Unsafe variant identifier')
            # Reserve the complete variant ID and most space for the named PNG.
            # Deep Windows download folders use order-only scene folders rather
            # than repeating a long title twice and exceeding MAX_PATH.
            room=249-_units(str(root))-_units(variant)-4
            if room<66:raise ValueError('Named destination is too long; choose a shorter export directory')
            compact=room<130
            chapter_limit=22 if compact else min(65,max(26,room//4))
            scene_limit=17 if compact else min(75,max(26,room//4))
            chapter_name=_chapter_title(mapping)
            if compact:
                prefix=f"Chapter {mapping['chapter_number']:03d}"
                subtitle=chapter_name[len(prefix):].removeprefix(' - ')
                chapter_name=f"{mapping['chapter_number']:03d}"+(' - '+safe_component(subtitle,chapter_limit-6) if subtitle else '')
            chapter=safe_component(chapter_name,chapter_limit)
            scene=safe_component(f"{mapping['order']:06d}" if compact else f"{mapping['order']:06d} - {mapping['title']}",scene_limit)
            entry.update(official_title=mapping['title'],official_chapter_title=mapping['chapter_title'],
                         chapter_number=mapping['chapter_number'],order=mapping['order'],
                         mission_order=mapping.get('mission_order'),scene_title=mapping.get('scene_title'))
            plans.append({'row':row,'entry':entry,'mapping':mapping,'chapter':chapter,'scene':scene,'variant':variant,'chapter_limit':chapter_limit,'scene_limit':scene_limit,'room':room})
        except (ValueError,TypeError,KeyError) as exc:entry['issues'].append(str(exc))
    # Sanitizing names can merge different official strings. Disambiguate using
    # stable source identities rather than filesystem enumeration order.
    chapters={};scenes={}
    for plan in plans:
        m=plan['mapping'];chapters.setdefault(plan['chapter'].casefold(),set()).add((m['chapter_number'],m['chapter_title']))
        scenes.setdefault((plan['chapter'].casefold(),plan['scene'].casefold()),set()).add(plan['row']['scene_id'])
    targets={}
    for plan in plans:
        row,entry,m=plan['row'],plan['entry'],plan['mapping']
        original_chapter=plan['chapter']
        if len(chapters[plan['chapter'].casefold()])>1:
            plan['chapter']=safe_component(plan['chapter']+'~'+_digest(m['chapter_title']),plan['chapter_limit'])
        key=(original_chapter.casefold(),plan['scene'].casefold())
        if len(scenes.get(key,()))>1:plan['scene']=safe_component(plan['scene']+'~'+_digest(row['scene_id']),plan['scene_limit'])
        directory=root/plan['chapter']/plan['scene']/plan['variant']
        target_key=str(directory).casefold()
        if target_key in targets and targets[target_key]!=(row['scene_id'],row['variant_id']):
            entry['issues'].append('Named folder collision between different saved variants')
        targets[target_key]=(row['scene_id'],row['variant_id'])
        entry['grouping']={'chapter':plan['chapter'],'scene':plan['scene'],'variant':plan['variant']}
        try:
            if entry['issues']:continue
            images=_source_images(out,row);actions=[]
            for canonical,(role,label) in ROLES.items():
                source,digest=images[canonical]
                title_limit=min(90,plan['room']-_units(plan['chapter'])-_units(plan['scene'])-len(' - Complete.png'))
                if title_limit<12:raise ValueError('Named title has insufficient Windows path space; choose a shorter directory')
                title=safe_component(m['title'],title_limit)
                target=directory/(title+' - '+label+'.png');relative=target.relative_to(root).as_posix()
                if _units(str(target))>=250:raise ValueError('Named PNG path exceeds the Windows path budget')
                state=_destination_state(source,target,digest,ownership,relative)
                record={'scene_id':row['scene_id'],'variant_id':row['variant_id'],'official_title':m['title'],
                        'official_chapter_title':m['chapter_title'],'chapter_number':m['chapter_number'],'order':m['order'],
                        'role':role,'alias':m['title']+' - '+label+'.png','source':str(source),'destination':str(target),
                        'relative_path':relative,'source_sha256':digest,'destination_sha256':digest,
                        'source_proof_fingerprint':row['fingerprint'],'grouping':entry['grouping']}
                actions.append((source,target,digest,state,record))
            # All three hashes and destination ownership checks finish first.
            for source,target,digest,state,record in actions:
                if state=='write':_copy(source,target,digest);summary['written_pngs']+=1
                ownership[record['relative_path']]=digest;tracked[record['relative_path']]=record;entry['files'].append(record)
            entry['status']='reused' if all(action[3]=='reuse' for action in actions) else 'exported'
            summary['exported_variants']+=1
            if entry['status']=='reused':summary['reused_variants']+=1
        except (ValueError,TypeError,KeyError,OSError,SyntaxError) as exc:entry['issues'].append(str(exc))
    for entry in report['exports']:
        if entry['issues']:
            entry['status']='skipped';summary['skipped_variants']+=1
            report['issues'].append({'scene_id':entry['scene_id'],'variant_id':entry['variant_id'],'issues':entry['issues']})
    report['files']=[tracked[key] for key in sorted(tracked)];report['owned_files']=ownership
    stream=io.StringIO(newline='');fields=['scene_id','variant_id','official_title','official_chapter_title','chapter_number','order','role','relative_path','source','source_sha256','destination','destination_sha256','source_proof_fingerprint']
    writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(report['files'])
    index_data=stream.getvalue().encode('utf-8-sig');index=_plain(root/INDEX)
    if index.exists() and (not index.is_file() or (sha256(index)!=hashlib.sha256(index_data).hexdigest() and sha256(index)!=previous.get('index_sha256'))):
        report['issues'].append({'issues':['Named CSV index conflicts with an untracked or edited file']})
        report['index_sha256']=previous.get('index_sha256')
    else:
        if not index.exists() or sha256(index)!=hashlib.sha256(index_data).hexdigest():_atomic_bytes(index,index_data)
        report['index_sha256']=hashlib.sha256(index_data).hexdigest()
    if report['issues']:report['status']='partial'
    _atomic_bytes(root/MANIFEST,(json.dumps(report,indent=2,ensure_ascii=True)+'\n').encode('utf-8'))
    return report
