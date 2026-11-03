#!/usr/bin/env python3
"""Experimental scene-independent GAF first-frame reconstruction and asset audit.
Usage: py renderer.py "Giza Riverfront ZIP.zip" --output output
Requires Pillow. The placement scanner is heuristic, not a complete GAF parser.
"""
import argparse, csv, io, json, math, re, struct, zipfile, zlib
from collections import Counter
from pathlib import Path
from PIL import Image, ImageStat


def load_source(path):
    """Read exported assets or extract original UnityFS bundle(s), including inside a ZIP."""
    import tempfile
    path=Path(path)
    load_source.last_bundle_audit = None
    if path.is_file() and path.suffix.lower()=='.zip':
        with zipfile.ZipFile(path) as z:
            members=[n for n in z.namelist() if not n.endswith('/') and not n.startswith('__MACOSX/')]
            bundle_names=[n for n in members if n.lower().endswith(('.bundle','.unity3d','.assetbundle'))]
            if bundle_names:
                from bundle_loader import extract_bundle
                with tempfile.TemporaryDirectory(prefix='gaf_bundle_') as td:
                    extracted=[]
                    for idx,n in enumerate(bundle_names):
                        # Flat staging avoids unsafe ZIP paths and preserves multiple bundles.
                        dest=Path(td)/f'{idx:04d}_{Path(n).name}'
                        dest.write_bytes(z.read(n));extracted.append(dest)
                    files,audit=extract_bundle(Path(td))
                    audit['zip_bundle_members']=bundle_names
                    load_source.last_bundle_audit=audit
                    return files
            files={n:z.read(n) for n in members if n.lower().endswith(('.json','.png'))}
            if files:return files
            raise ValueError('ZIP has no Unity .bundle files or exported .json/.png assets.')
    if path.is_dir():
        bundles=[p for p in path.rglob('*') if p.is_file() and p.suffix.lower() in ('.bundle','.unity3d','.assetbundle')]
        if bundles:
            from bundle_loader import extract_bundle
            files,audit=extract_bundle(path)
            load_source.last_bundle_audit=audit
            return files
        exported={str(p.relative_to(path)):p.read_bytes() for p in path.rglob('*') if p.suffix.lower() in ('.json','.png')}
        if exported:return exported
        raise ValueError('Directory has no bundles or exported assets.')
    if path.is_file() and path.suffix.lower() in ('.bundle','.unity3d','.assetbundle'):
        from bundle_loader import extract_bundle
        files,audit=extract_bundle(path)
        load_source.last_bundle_audit=audit
        return files
    raise ValueError('Provide a ZIP containing original Unity bundles, a bundle file, or exported assets.')


def parse_assets(files):
    objs=[];assets=[]; managers={};bad=[]
    for name,raw in files.items():
        if not name.lower().endswith('.json'):continue
        try: data=json.loads(raw)
        except Exception as e:bad.append({'file':name,'error':str(e)});continue
        if not isinstance(data,dict):continue
        if isinstance(data.get('m_AssetData'),list):assets.append((name,data))
        if isinstance(data.get('m_Data'),dict) and 'm_AtlasInitialElementID' in data['m_Data']:
            objs.append((name,data['m_Data']))
        if any(x in name.lower() for x in ('gafobjectsmanager','gafsortingmanager','gafmovieclip')):
            managers[Path(name).stem]=data
    atlases=[]
    for name,raw in files.items():
        if name.lower().endswith('.png'):
            try: atlases.append((name,Image.open(io.BytesIO(raw)).convert('RGBA')))
            except Exception as e:bad.append({'file':name,'error':str(e)})
    return objs,assets,managers,atlases,bad


def unpack_gaf(data):
    raw=bytes(data['m_AssetData'])
    if raw[:3]!=b'CAG':raise ValueError('Unsupported GAF wrapper: missing CAG signature')
    # CAG wrapper in this export has a 10-byte prefix and zlib payload.
    if raw[10:12]!=b'\x78\xda' and raw[10]!=0x78:raise ValueError('Unsupported compressed payload')
    return zlib.decompress(raw[10:])


def candidate(data,pos,ids):
    if pos+39>len(data):return None
    oid,depth,alpha,a,b,c,d,x,y=struct.unpack_from('<II7f',data,pos)
    if oid not in ids or depth>10000 or not all(math.isfinite(v) for v in (alpha,a,b,c,d,x,y)):
        return None
    if not (-0.01<=alpha<=1.1 and -100000<x<100000 and -100000<y<100000):return None
    if not (abs(a)<100 and abs(b)<100 and abs(c)<100 and abs(d)<100):return None
    if abs(a*d-b*c)<1e-12:return None
    return dict(object_id=oid,depth=depth,alpha=alpha,a=a,b=b,c=c,d=d,x=x,y=y,flags=data[pos+36:pos+39].hex(),byte_offset=pos)


def scan_first_frame(data,ids):
    """Find longest depth-ordered run of 39-byte placements, allowing 4-byte extensions.
    Not a replacement for parsing GAF tags or subsequent frame deltas.
    """
    # Restrict seeds to actual known IDs, depth zero, and plausible alpha/matrix.
    best=[];best_start=None
    for pos in range(len(data)-39):
        r=candidate(data,pos,ids)
        if r is None or r['depth']!=0:continue
        run=[r];cur=pos+39;seen={r['object_id']}
        while len(run)<max(len(ids)+20,100) and cur+39<=len(data):
            found=None
            for extension in (0,4,8,12):
                p=cur+extension
                q=candidate(data,p,ids)
                if q and q['depth']==run[-1]['depth']+1 and q['object_id'] not in seen:
                    found=(q,p);break
            if not found:break
            q,p=found;run.append(q);seen.add(q['object_id']);cur=p+39
        if len(run)>len(best):best,best_start=run,pos
    if len(best)<8:raise ValueError('No sufficiently long placement run found. This GAF version may require a new decoder.')
    return best,best_start


def classify(rec):
    name=str(rec.get('m_Name','')).lower()
    if name.startswith('bg_') or name in ('background','backdrop'):return 'background'
    if 'timecap' in name or 'present' in name:return 'special_gameplay'
    if 'mask' in name or int(rec.get('m_BehaviourType',0))==2:return 'mask_or_stencil'
    if name.startswith('anim_') or int(rec.get('m_BehaviourType',0))==1:return 'animation'
    if re.match(r'^\d+_\d+$',name):return 'unclassified_overlay'
    return 'unclassified' # NOT automatically a findable hidden object


def choose_atlas(rect,atlases):
    x,y,w,h=rect
    choices=[]
    for name,img in atlases:
        if x<0 or y<0 or x+w>img.width or y+h>img.height:continue
        tile=img.crop((x,y,x+w,y+h))
        alpha=tile.getchannel('A')
        bbox=alpha.getbbox()
        if not bbox:score=0
        else:
            # Prefer actual nontransparent pixels, not merely a fitting rectangle.
            sample=tile.resize((min(96,w),min(96,h)))
            stats=ImageStat.Stat(sample).mean
            # Reject fully black opaque placeholder atlas regions.
            score=stats[3]*(stats[0]+stats[1]+stats[2])/765
        choices.append((score,name,tile))
    if not choices:return None,None,'no_atlas_fits'
    choices.sort(reverse=True,key=lambda t:t[0]);score,name,tile=choices[0]
    if score==0:return None,None,'all_candidate_regions_transparent'
    return name,tile,'ambiguous_atlas' if len(choices)>1 and choices[1][0]>=score*.95 else 'ok'


def affine_sprite(tile,r):
    a,b,c,d=(r[k] for k in ('a','b','c','d'))
    w,h=tile.size;det=a*d-b*c
    if abs(det)<1e-12:return None,None
    corners=[(a*u+c*v,b*u+d*v) for u,v in ((0,0),(w,0),(0,h),(w,h))]
    minx=math.floor(min(v[0] for v in corners));maxx=math.ceil(max(v[0] for v in corners))
    miny=math.floor(min(v[1] for v in corners));maxy=math.ceil(max(v[1] for v in corners))
    width,height=maxx-minx,maxy-miny
    if not 0<width<=6000 or not 0<height<=6000:return None,None
    ia,ib,ic,id_=d/det,-c/det,-b/det,a/det
    warped=tile.transform((width,height),Image.Transform.AFFINE,(ia,ib,ia*minx+ib*miny,ic,id_,ic*minx+id_*miny),resample=Image.Resampling.BICUBIC)
    if r['alpha']<.999:
        alpha=warped.getchannel('A').point(lambda v:int(v*max(0,r['alpha'])))
        warped.putalpha(alpha)
    return warped,(round(r['x'])+minx,round(r['y'])+miny)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source',help='Scene ZIP or extracted directory')
    ap.add_argument('--output',default='reconstruction_output')
    ap.add_argument('--include-special',action='store_true',help='Include timecaps and other gameplay sprites')
    ap.add_argument('--include-overlays',action='store_true',help='Include masks and unclassified overlays (may obscure scene)')
    ap.add_argument('--width',type=int,default=None);ap.add_argument('--height',type=int,default=None)
    args=ap.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    files=load_source(args.source);objs,assets,managers,atlases,bad=parse_assets(files)
    byid={int(o['m_ID']):o for _,o in objs};dupes=len(objs)-len(byid)
    if not assets:raise ValueError('No JSON with m_AssetData found')
    if not atlases:raise ValueError('No PNG atlases found')
    decoded=unpack_gaf(assets[0][1]);placements,start=scan_first_frame(decoded,set(byid))
    # Use a background sprite to determine dimensions when possible.
    bgs=[o for o in byid.values() if classify(o)=='background']
    if bgs:
        rect=bgs[0]['m_AtlasTextureRect'];default_size=(int(rect['width']),int(rect['height']))
    else:default_size=(1280,760)
    size=(args.width or default_size[0],args.height or default_size[1])
    canvas=Image.new('RGBA',size,(0,0,0,0));rows=[]
    for r in sorted(placements,key=lambda p:p['depth']):
        oid=r['object_id'];obj=byid.get(oid);row={**r,'name':obj.get('m_Name') if obj else '', 'category':classify(obj) if obj else 'missing_metadata','atlas':'','status':''}
        if obj is None:row['status']='missing_object_metadata';rows.append(row);continue
        category=row['category']
        if category=='special_gameplay' and not args.include_special:row['status']='excluded_special';rows.append(row);continue
        if category in ('mask_or_stencil','unclassified_overlay') and not args.include_overlays:row['status']='excluded_overlay';rows.append(row);continue
        rect=obj.get('m_AtlasTextureRect')
        if not rect:row['status']='missing_atlas_rectangle';rows.append(row);continue
        recttuple=tuple(int(rect[k]) for k in ('x','y','width','height'))
        if recttuple[2]<=0 or recttuple[3]<=0:row['status']='invalid_rectangle';rows.append(row);continue
        name,tile,status=choose_atlas(recttuple,atlases);row['atlas']=name or ''
        if tile is None:row['status']=status;rows.append(row);continue
        warped,position=affine_sprite(tile,r)
        if warped is None:row['status']='invalid_affine';rows.append(row);continue
        layer=Image.new('RGBA',size,(0,0,0,0));layer.paste(warped,position)
        canvas=Image.alpha_composite(canvas,layer)
        row['status']='rendered' if status=='ok' else 'rendered_atlas_ambiguous';rows.append(row)
    canvas.save(out/'scene.png')
    with (out/'placements.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    object_rows=[]
    for oid,obj in sorted(byid.items()):
        object_rows.append({'object_id':oid,'name':obj.get('m_Name'),'atlas_element':obj.get('m_AtlasInitialElementID'),'category':classify(obj),'placed_in_first_frame':any(r['object_id']==oid for r in placements),'behavior_type':obj.get('m_BehaviourType')})
    with (out/'object_catalogue.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(object_rows[0]));writer.writeheader();writer.writerows(object_rows)
    report={'source':str(args.source),'gaf_asset':assets[0][0],'asset_json_count':len(assets),'gaf_object_files':len(objs),'unique_gaf_object_ids':len(byid),'duplicate_ids':dupes,'gaf_transform_files':sum('gaftransform' in n.lower() and n.endswith('.json') for n in files),'manager_files':list(managers),'atlases':[{'name':n,'size':im.size} for n,im in atlases],'decoded_gaf_bytes':len(decoded),'placement_run_start_offset':start,'placement_count':len(placements),'placement_detection':'heuristic first-frame run; not full timeline decoder','render_size':size,'categories':dict(Counter(classify(o) for o in byid.values())),'statuses':dict(Counter(r['status'] for r in rows)),'not_in_first_frame':[{'id':oid,'name':o.get('m_Name')} for oid,o in sorted(byid.items()) if oid not in {r['object_id'] for r in placements}],'errors':bad,'limitations':['Findable-object status is not established by these GAF records alone.','Only one inferred first-frame placement run is decoded; later frames, masks, color effects and gameplay visibility are not faithfully implemented.','Atlas choice is heuristic if the same rectangle contains content in multiple atlases.']}
    (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print('Scene:',out/'scene.png');print('Objects:',len(objs),'placements:',len(placements));print('Statuses:',report['statuses']);print('Report:',out/'report.json')

if __name__=='__main__':main()
