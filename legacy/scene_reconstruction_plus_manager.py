#!/usr/bin/env python3
"""Non-destructive Gardens of Time scene builder with animation discovery.

Background duplicate handling delegates to build_scenes_auto.py, which writes
its decision report. Animation records outside the decoded first frame are
inspected and exported as sprite previews, not blindly placed on the scene.
Use --overlay-animation ID to add a *manually verified* candidate placement.
This is not a full GAF timeline decoder.
"""
import argparse, csv, json, subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw
import renderer_ftd_test as base

ROOT=Path(__file__).resolve().parent

def scan_candidates(raw, ids, first_ids):
    """Find plausible 39-byte placement records beyond the first frame.
    These are unverified candidates, not authoritative timeline events.
    """
    result={}
    for pos in range(0,max(0,len(raw)-39)):
        rec=base.candidate(raw,pos,ids)
        if rec is None or rec['object_id'] in first_ids:continue
        if rec['depth']>500 or not (0 <= rec['alpha'] <= 1):continue
        if not (-100 <= rec['x'] <= 3000 and -100 <= rec['y'] <= 1800):continue
        if not (0.02 <= abs(rec['a']*rec['d']-rec['b']*rec['c']) <= 20):continue
        result.setdefault(rec['object_id'],[]).append(rec)
    return result

def extract_sprite(obj, atlases, placement):
    rect=obj.get('m_AtlasTextureRect') or {}
    coords=tuple(int(rect[k]) for k in ('x','y','width','height'))
    _,tile,reason=base.choose_atlas_for_object(coords,atlases,obj)
    if tile is None:raise ValueError(reason)
    return base.affine_sprite(tile,placement)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('bundle')
    ap.add_argument('--output',default='ScenePlus')
    ap.add_argument('--overlay-animation',type=int,action='append',default=[],metavar='OBJECT_ID',help='Opt-in candidate placement; verify preview first')
    ap.add_argument('--candidate-index',type=int,default=0,help='Candidate index for selected animation (default 0)')
    ap.add_argument('--skip-build',action='store_true')
    ap.add_argument('--offline',action='store_true')
    args=ap.parse_args()
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    if not args.skip_build:
        builder=ROOT/'build_scenes_auto.py'
        if not builder.exists():ap.error('Missing build_scenes_auto.py beside this script')
        cmd=[sys.executable,str(builder),args.bundle,'--output',str(out), '--builder-script',str(ROOT/'build_three_scenes_ftd_manager.py')]
        if args.offline:cmd+=['--offline']
        subprocess.run(cmd,check=True)
    objects,assets,_,atlases,errors=base.parse_assets(base.load_source(args.bundle))
    if not assets or not atlases:raise RuntimeError('No readable GAF asset or atlas')
    byid={int(o['m_ID']):o for _,o in objects}
    raw=base.unpack_gaf(assets[0][1]);first,_=base.scan_first_frame(raw,set(byid))
    first_ids={p['object_id'] for p in first}
    candidates=scan_candidates(raw,set(byid),first_ids)
    # Also expose unplaced animation-class objects without any candidate transform.
    animations={oid:o for oid,o in byid.items() if base.classify(o)=='animation' or any(k in str(o.get('m_Name','')).lower() for k in ('helicopter','rotor','propeller'))}
    watch={**animations,**{oid:byid[oid] for oid in candidates if oid in byid}}
    preview=out/'animation_previews';preview.mkdir(exist_ok=True)
    report=[]
    for oid,obj in sorted(watch.items()):
        entries=candidates.get(oid,[])
        row={'object_id':oid,'name':obj.get('m_Name',''),'category':base.classify(obj),
             'in_first_frame':oid in first_ids,'candidate_count':len(entries),
             'candidate_offsets':[p['byte_offset'] for p in entries[:20]],'preview':'','error':''}
        # A neutral transform can preview the atlas sprite even without a timeline placement.
        neutral={'a':1.,'b':0.,'c':0.,'d':1.,'x':0.,'y':0.,'alpha':1.}
        try:
            sprite,_=extract_sprite(obj,atlases,neutral)
            if sprite is not None:
                sprite.thumbnail((500,400))
                path=preview/f'object_{oid}.png';sprite.save(path);row['preview']=str(path.relative_to(out))
        except Exception as e:row['error']=str(e)
        report.append(row)
    (out/'animation_audit.json').write_text(json.dumps({'note':'Candidates are unverified binary matches, not decoded GAF animation frames. No missing object is automatically positioned.', 'first_frame_ids':len(first_ids),'animation_objects':len(animations),'unplaced_candidates':len(candidates),'objects':report,'source_errors':errors},indent=2),encoding='utf-8')
    with (out/'animation_audit.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=['object_id','name','category','in_first_frame','candidate_count','candidate_offsets','preview','error']);writer.writeheader();writer.writerows(report)
    for oid in args.overlay_animation:
        if oid not in byid:raise ValueError(f'Unknown animation ID: {oid}')
        entries=candidates.get(oid,[])
        if not entries:raise ValueError(f'No candidate placement for {oid}; cannot position automatically')
        if args.candidate_index >= len(entries):raise ValueError(f'Candidate index out of range for {oid}')
        sprite,pos=extract_sprite(byid[oid],atlases,entries[args.candidate_index])
        if sprite is None:raise ValueError(f'Cannot render animation {oid}')
        for filename in ('scene_complete.png','scene_solution.png'):
            path=out/filename
            if not path.exists():raise FileNotFoundError(path)
            im=Image.open(path).convert('RGBA');layer=Image.new('RGBA',im.size)
            layer.paste(sprite,pos);Image.alpha_composite(im,layer).save(out/(path.stem+f'_animation_{oid}.png'))
    # Explicitly report output validity rather than silently accepting identical FTD images.
    from PIL import ImageChops
    paths=[out/name for name in ('scene_empty.png','scene_complete.png','scene_solution.png')]
    if all(p.exists() for p in paths):
        ims=[Image.open(p).convert('RGBA') for p in paths]
        identical=all(ims[0].size==im.size and ImageChops.difference(ims[0],im).getbbox() is None for im in ims[1:])
        report_path=out/'validation_report.json'
        existing=json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
        ftd=bool(existing.get('ftd_half_mode')) or 'ftd' in Path(args.bundle).stem.lower()
        existing['three_outputs_identical']=identical
        existing['pipeline_status']='FAILED: all three FTD outputs identical' if ftd and identical else ('WARNING: outputs identical' if identical else 'outputs differ')
        report_path.write_text(json.dumps(existing,indent=2),encoding='utf-8')
        print('VALIDATION:',existing['pipeline_status'],flush=True)
    # Produce the all-atlas visual catalogue automatically in the same folder.
    try:
        from build_object_catalogue import build_catalogue
        build_catalogue(args.bundle, out)
    except Exception as exc:
        (out/'catalogue_error.txt').write_text(str(exc),encoding='utf-8')
        print('WARNING: catalogue generation failed:',exc,flush=True)
    print('Animation audit:',out/'animation_audit.json')
    print('Preview folder:',preview)
    print('Note: Full animated-object placement requires a real GAF timeline decoder.')
if __name__=='__main__':main()
