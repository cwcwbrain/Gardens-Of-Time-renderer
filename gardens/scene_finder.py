"""Find official scene names and recover missing images into a separate misc tree."""
import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

from .batch import code_identity,execute_job,fingerprint
from .catalogue import Index,parse_catalogue
from .chapters import _manager_convention,_validate_manager
from .discovery import catalogue_sources
from .download import Downloader,DownloadError,atomic_json,cache_name,read_json,sha256
from .named_export import _plain,_units,safe_component
from .names import load_official_names
from .scene_lookup import existing_scene_images,resolve_scene_name,suggestions
from .scenes import Scene,match_scenes,read_scenes,variant_id

INDEX='misc_scene_index.json'


def _control_path(path):
    path=_plain(path)
    if path.exists() and path.is_file() and path.stat().st_nlink>1:
        raise ValueError('A linked lookup control file cannot be safely updated: '+path.name)
    return path


def _cached_names(out,scenes):
    mappings={s.scene_id:{'scene_id':s.scene_id,'order':s.order,'title':s.scene_id,
                          'name_source':'internal_id'} for s in scenes}
    for root in (out,out/'misc/_cache'):
        try:
            path=_plain(root/'scene_names.json')
            data=read_json(path)
            if isinstance(data,dict) and isinstance(data.get('mappings'),dict):mappings.update(data['mappings'])
        except (OSError,ValueError):pass
    return mappings


def _cached_discovery(manifest,roots,client,remote):
    fallback=None
    source=remote or manifest.get('remote_config')
    for root in roots:
        # Offline discovery can create its cache directory. Only call it on an
        # existing tree when borrowing the main run's read-only cache.
        if not isinstance(source,str) or not (root/'remote_config'/cache_name(source)/'current.json').is_file():continue
        result=catalogue_sources(manifest,root,client,True,remote)
        if result.get('config_path'):return result
        fallback=result
    return fallback or {'source':'manifest','catalogues':manifest.get('catalogues',[])}


def _properties(args):
    result={}
    for value in args.property:
        key,separator,text=value.partition('=')
        if not separator or not key:raise ValueError('--property requires NAME=VALUE')
        result[key]=text
    return result


def _load_names(query,args,out,cache,scenes,manifest,client,mappings):
    for root in (cache,out):
        if not all((root/folder).is_dir() for folder in ('chapter_metadata/catalogues','chapter_metadata/bundles','naming/localization')):continue
        discovery=_cached_discovery(manifest,[root],client,args.remote_config)
        if not discovery.get('config_path'):continue
        names=load_official_names(root,client,discovery['config_path'],scenes,True,include_all_primary=True)
        if names.get('mappings'):mappings.update(names['mappings'])
        if resolve_scene_name(query,mappings):return mappings
    if args.offline:return mappings
    print('Checking current official scene names...',flush=True)
    discovery=catalogue_sources(manifest,cache,client,False,args.remote_config)
    names=load_official_names(cache,client,discovery.get('config_path'),scenes,False,include_all_primary=True)
    atomic_json(cache/'scene_names.json',names)
    if names.get('mappings'):mappings.update(names['mappings'])
    return mappings


def _index(args,out,cache,manifest,client,refresh=False):
    discovery=(catalogue_sources(manifest,cache,client,args.offline,args.remote_config) if refresh
               else _cached_discovery(manifest,[cache,out],client,args.remote_config))
    urls=discovery.get('catalogues')
    if (not isinstance(urls,list) or not urls or
            not all(isinstance(url,str) and urlsplit(url).scheme=='https' and urlsplit(url).netloc for url in urls) or
            len(set(urls))!=len(urls)):
        raise ValueError('No complete catalogue URL list for scene lookup')
    locations=[];reports=[]
    properties=_properties(args)
    for number,url in enumerate(urls,1):
        path=None
        if not refresh or args.offline:
            for root in (cache,out):
                candidate=_plain(root/'catalogues'/cache_name(url,'.json'))
                receipt=read_json(str(candidate)+'.verified.json')
                if candidate.is_file() and receipt.get('url')==url and receipt.get('sha256')==sha256(candidate):
                    path=candidate;break
        try:
            if path is None:
                if args.offline:raise DownloadError('No verified cached catalogue: '+url)
                path=cache/'catalogues'/cache_name(url,'.json')
                print(f'Lookup catalogue {number}/{len(urls)}: {url}',flush=True)
                client.fetch(url,path,lambda p:parse_catalogue(p.read_bytes(),url,properties),refresh=refresh)
            parsed,info=parse_catalogue(path.read_bytes(),url,properties)
            locations.extend(parsed);reports.append({'url':url,'status':'indexed','sha256':sha256(path),**info})
        except (ValueError,OSError,DownloadError) as exc:
            reports.append({'url':url,'status':'failed','error':str(exc)})
    complete=len(reports)==len(urls) and all(row['status']=='indexed' for row in reports)
    atomic_json(cache/'finder_catalogue_report.json',{'source':discovery.get('source'),'complete':complete,'catalogues':reports})
    return Index(locations),complete


def _verified_manager(args,out,cache,scene,mapping,scenes,client):
    if scene.item_manager_url:return scene
    prefix,strip_ftd=_manager_convention(scenes)
    sid=scene.scene_id;urls=[prefix+sid+'.json']
    base=re.sub(r'FTD\d+$','',sid,flags=re.I)
    if strip_ftd and base!=sid:urls.append(prefix+base+'.json')
    errors=[]
    for url in urls:
        for root in (cache,out):
            path=_plain(root/'item_managers'/cache_name(url,'.json'));receipt=read_json(str(path)+'.verified.json')
            if path.is_file() and receipt.get('url')==url and receipt.get('sha256')==sha256(path):
                _validate_manager(path)
                return Scene(scene.order,sid,'',url,[sid,'',url])
        if not args.offline:
            try:
                client.fetch(url,cache/'item_managers'/cache_name(url,'.json'),_validate_manager)
                return Scene(scene.order,sid,'',url,[sid,'',url])
            except (ValueError,OSError,DownloadError) as exc:errors.append(str(exc))
    raise DownloadError('No verified selected-scene Item Manager: '+'; '.join(errors or urls))


@contextmanager
def _finder_lock(misc):
    misc.mkdir(parents=True,exist_ok=True)
    path=_control_path(misc/'.lookup_lock.sqlite3')
    for suffix in ('-journal','-wal','-shm'):_control_path(str(path)+suffix)
    db=sqlite3.connect(path,timeout=30,isolation_level=None)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS finder_lock (id INTEGER PRIMARY KEY)')
        db.execute('BEGIN IMMEDIATE')
        yield
        db.execute('COMMIT')
    finally:
        if db.in_transaction:db.execute('ROLLBACK')
        db.close()


def _read_index(misc):
    for name in (INDEX,INDEX+'.tmp','misc_scene_index.csv','misc_scene_index.csv.tmp','last_lookup.json','last_lookup.json.tmp'):
        path=_control_path(misc/name)
        if path.exists() and not path.is_file():raise ValueError('Misc lookup control path is not a regular file: '+name)
    path=_plain(misc/INDEX)
    if not path.exists():return {'schema':1,'variants':[]}
    value=read_json(path)
    if not isinstance(value,dict) or value.get('schema')!=1 or not isinstance(value.get('variants'),list):
        raise ValueError('Existing misc index is unrecognized; it was left unchanged')
    if any(not isinstance(entry,dict) or not isinstance(entry.get('scene_id'),str) or
           not isinstance(entry.get('variant_id'),str) or not isinstance(entry.get('result'),dict)
           for entry in value['variants']):
        raise ValueError('Existing misc index has malformed entries; it was left unchanged')
    return value


def _save_index(misc,index):
    for name in (INDEX,INDEX+'.tmp','misc_scene_index.csv','misc_scene_index.csv.tmp'):_control_path(misc/name)
    stream=io.StringIO(newline='')
    fields=['scene_id','title','chapter_number','variant_id','status','directory','empty','complete','solution','issues']
    writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
    for entry in index['variants']:
        result=entry.get('result',{});files={Path(raw).name:raw for raw in result.get('files',[])}
        writer.writerow({**{k:entry.get(k,'') for k in fields[:4]},'status':result.get('status',''),
                         'directory':entry.get('directory',''),**{role:files.get('scene_'+role+'.png','') for role in ('empty','complete','solution')},
                         'issues':json.dumps(result.get('issues',[]),ensure_ascii=False)})
    payload=stream.getvalue().encode('utf-8-sig');target=_plain(misc/'misc_scene_index.csv')
    if not target.exists() or index.get('csv_sha256')==sha256(target):
        temporary=_control_path(target.with_name(target.name+'.tmp'));temporary.write_bytes(payload)
        try:
            temporary.replace(target);index['csv_sha256']=hashlib.sha256(payload).hexdigest()
        except PermissionError:index['csv_warning']='Close misc_scene_index.csv in Excel to refresh it; the JSON index is current.'
    else:index['csv_warning']='Edited or untracked misc_scene_index.csv was preserved; the JSON index is current.'
    atomic_json(misc/INDEX,index)


def _destination(misc,title,sid,vid):
    room=249-_units(str(misc))-len('/variant_'+vid+'_run_12345678/incomplete/scene_solution.png')-2
    if room<12:raise ValueError('The output path is too long for a safe misc scene directory')
    suffix=hashlib.sha256(sid.encode()).hexdigest()[:10]
    component=(safe_component(title,min(60,room-12))+'--'+suffix) if room>=25 else suffix
    parent=_plain(misc/component);parent.mkdir(exist_ok=True)
    target=_plain(parent/('variant_'+vid))
    while target.exists():target=_plain(parent/('variant_'+vid+'_run_'+uuid.uuid4().hex[:8]))
    target.mkdir()
    return target


def _reusable_attempt(index,sid,vid,request):
    for entry in reversed(index['variants']):
        if entry.get('scene_id')!=sid or entry.get('variant_id')!=vid or entry.get('request_fingerprint')!=request:continue
        result=entry.get('result',{})
        if result.get('status')!='incomplete' or not result.get('artifacts'):continue
        try:
            if all(Path(path).is_file() and sha256(path)==digest for path,digest in result['artifacts'].items()):return entry
        except OSError:pass
    return None


def _valid_images(directory,result):
    files=result.get('files',[]);artifacts=result.get('artifacts',{})
    if result.get('issues') or len(files)!=3 or not isinstance(artifacts,dict):return False
    expected={'scene_empty.png','scene_complete.png','scene_solution.png'}
    found=set()
    try:
        for raw in files:
            path=_plain(raw)
            if path.parent!=directory or path.name not in expected or path.name in found:return False
            if artifacts.get(str(path))!=sha256(path):return False
            with Image.open(path) as image:
                if image.format!='PNG':return False
                image.verify()
            found.add(path.name)
    except (OSError,ValueError,TypeError):return False
    return found==expected


def find_scene(query,args,client=None,runner=None):
    """Return verified existing images, or render all missing-scene variants.

    The main roster/checkpoint, scene files and reports are never rewritten.
    New caches, result receipts and images belong only to output/misc.
    """
    out=_plain(args.output);misc=out/'misc';cache=misc/'_cache'
    scenes,_=read_scenes(args.scenes)
    mappings=_cached_names(out,scenes)
    selected=resolve_scene_name(query,mappings)
    if len(selected)>1:return {'status':'ambiguous','query':query,'candidates':selected}
    if selected:
        existing=existing_scene_images(out,selected[0]['scene_id'])
        if existing:return {'status':'found','query':query,'scene':selected[0],'locations':existing}
    client=client or Downloader(args.timeout,args.retries)
    manifest=json.loads(Path(args.catalogues).read_text(encoding='utf-8-sig'))
    if not isinstance(manifest,dict):raise ValueError('Catalogue manifest must be a JSON object')
    misc=_plain(misc);cache=_plain(cache)
    with _finder_lock(misc):
        saved=_read_index(misc)
        if cache.exists():
            for path in cache.rglob('*'):_control_path(path)
        if not selected:
            mappings=_load_names(query,args,out,cache,scenes,manifest,client,mappings)
            selected=resolve_scene_name(query,mappings)
        if not selected:return {'status':'not_found','query':query,'suggestions':suggestions(query,mappings)}
        if len(selected)>1:return {'status':'ambiguous','query':query,'candidates':selected}
        mapping=selected[0];sid=mapping['scene_id']
        existing=existing_scene_images(out,sid)
        if existing:return {'status':'found','query':query,'scene':mapping,'locations':existing}
        scene=next((s for s in scenes if s.scene_id==sid),Scene(mapping.get('order',1),sid,'','',[sid,'','']))
        index,complete=_index(args,out,cache,manifest,client,args.refresh_catalogues or not args.offline)
        match=match_scenes([scene],index,complete)[0]
        eligible=[c for c in match['candidates'] if not c['issues'] and c['bundles']]
        if match['status'] not in ('matched','ambiguous') or match['issues'] or not eligible:
            report={'status':'unavailable','query':query,'scene':mapping,'match':match}
            atomic_json(misc/'last_lookup.json',report);return report
        scene=_verified_manager(args,out,cache,scene,mapping,scenes,client)
        attempts=[]
        for candidate in match['candidates']:
            vid=variant_id(candidate)
            request=fingerprint({'scene':asdict(scene),'candidate':candidate,'frame':args.frame,'renderer':code_identity()})
            if candidate['issues'] or not candidate['bundles']:
                entry={'scene_id':sid,'title':mapping.get('title') or sid,'chapter_number':mapping.get('chapter_number'),
                       'variant_id':vid,'directory':'','request_fingerprint':request,
                       'result':{'status':'blocked','issues':candidate['issues'] or ['Candidate has no resolved bundle'],
                                 'files':[],'artifacts':{}}}
                saved['variants'].append(entry);_save_index(misc,saved);attempts.append(entry);continue
            prior=_reusable_attempt(saved,sid,vid,request)
            if prior is not None:attempts.append(prior);continue
            destination=_destination(misc,mapping.get('title') or sid,sid,vid)
            job={'scene':asdict(scene),'candidate':candidate,'destination':str(destination),'output':str(cache),
                 'read_cache_roots':[str(out)],'primary_default_mode':mapping.get('primary_default_mode'),
                 'frame':args.frame,'offline':args.offline,'timeout':args.timeout,'retries':args.retries}
            job['fingerprint']=fingerprint(job)
            print(f'Finding images for {mapping.get("title",sid)} variant {vid}...',flush=True)
            result=(runner or execute_job)(job,destination/'job.json',destination/'worker_result.json',destination/'worker.log',args.test_timeout)
            if result.get('status')=='passed' and not _valid_images(destination,result):
                result={**result,'status':'failed','issues':['Finder did not receive three verified scene PNGs']}
            entry={'scene_id':sid,'title':mapping.get('title') or sid,'chapter_number':mapping.get('chapter_number'),
                   'variant_id':vid,'directory':str(destination),'relative_directory':destination.relative_to(misc).as_posix(),
                   'request_fingerprint':request,'result':result}
            if result.get('status')=='passed':
                entry['image_receipts']={Path(raw).name:result['artifacts'][str(Path(raw).resolve())] for raw in result['files']}
            saved['variants'].append(entry);_save_index(misc,saved);attempts.append(entry)
        locations=existing_scene_images(out,sid)
        passed=sum(e['result'].get('status')=='passed' for e in attempts)
        report={'status':'rendered' if passed==len(attempts) else ('partial' if passed else 'incomplete'),
                'query':query,'scene':mapping,'locations':locations,'attempts':attempts,'misc_index':str(misc/INDEX)}
        atomic_json(misc/'last_lookup.json',report)
        return report


def _print_report(report):
    status=report['status']
    if report.get('scene'):
        scene=report['scene'];print(f'{scene.get("title",scene["scene_id"])} [{scene["scene_id"]}]')
    if status=='ambiguous':
        print('That name matches more than one scene. Search again using one of these IDs:')
        for value in report['candidates']:
            print(f'  {value["scene_id"]} — {value.get("title","")} (Chapter {value.get("chapter_number","?")})')
    elif status=='not_found':
        print('No exact scene-name match was found.')
        for value in report.get('suggestions',[]):print(f'  Suggested: {value.get("title",value["scene_id"])} — {value["scene_id"]}')
    else:
        print('Scene lookup: '+status)
        for value in report.get('locations',[]):print(f'  {value["directory"]}')
        for attempt in report.get('attempts',[]):
            if attempt['result'].get('status')!='passed':
                print('  '+attempt['variant_id']+': '+'; '.join(attempt['result'].get('issues',[])))
                if attempt.get('directory'):print('  Attempt folder: '+attempt['directory'])
                preview=_preview_folder(attempt)
                if preview is not None:print('  Incomplete preview folder: '+str(preview))
        if status=='unavailable':
            print('  Catalogue match: '+report['match']['status'])
            problems=report['match'].get('issues',[])+[issue for c in report['match'].get('candidates',[]) for issue in c['issues']]
            for issue in dict.fromkeys(problems):print('  '+issue)


def _preview_folder(attempt):
    try:
        if attempt.get('result',{}).get('status')!='incomplete':return None
        root=_plain(attempt['directory'])
        files=[_plain(raw) for raw in attempt['result'].get('files',[])]
        if len(files)==3 and {p.name for p in files}=={'scene_empty.png','scene_complete.png','scene_solution.png'}:
            if all(p.parent==root/'incomplete' and p.is_file() for p in files):return root/'incomplete'
    except (ValueError,OSError,KeyError,TypeError):pass
    return None


def open_result_folder(report):
    locations=report.get('locations',[])
    if locations:
        selected=min(locations,key=lambda r:({'named':0,'misc':1,'batch':2}.get(r['source'],3),r['directory']))
        path=selected['directory']
    else:
        preview=next((p for attempt in report.get('attempts',[]) if (p:=_preview_folder(attempt)) is not None),None)
        if preview is None:return
        print('Opening incomplete previews; this scene has not passed validation.')
        path=str(preview)
    if sys.platform=='win32':os.startfile(path)
    else:subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open',path],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def run_finder(args):
    interactive=args.find_scene=='';last_code=0
    while True:
        if interactive:
            try:query=input('Scene name (q to quit): ').strip()
            except EOFError:return last_code
            except KeyboardInterrupt:return 130
            if query.casefold() in ('q','quit','exit'):return last_code
            if not query:continue
        else:query=args.find_scene
        try:report=find_scene(query,args)
        except KeyboardInterrupt:return 130
        except (ValueError,OSError,DownloadError,sqlite3.Error) as exc:
            print('Scene lookup failed: '+str(exc));last_code=2
            if interactive:continue
            return last_code
        _print_report(report)
        last_code=0 if report['status'] in ('found','rendered') else 1
        if args.open_folder:
            try:open_result_folder(report)
            except OSError as exc:print('Could not open the folder: '+str(exc))
        if not interactive:return last_code
