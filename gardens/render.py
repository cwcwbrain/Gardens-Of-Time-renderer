"""Static ordinary-scene rendering from explicit GAF tags and Item Manager data.

Unsupported features produce previews and an incomplete report, never certified
scene files. No atlas scoring, placement scanning, or fuzzy label matching.
"""
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

from .annotations import annotate_solution, validate_callouts, visible_anchor
from .download import atomic_json, sha256, validate_bundle
from .gaf import GAFError, decode


class RenderError(ValueError):
    pass


def parse_items(data, names):
    if not isinstance(data, dict) or not data:
        raise RenderError('Item Manager must be a nonempty JSON object')
    items, animations, issues = {}, [], []
    for key, labels in data.items():
        if not isinstance(key, str) or not isinstance(labels, dict):
            issues.append(f'Unsupported Item Manager entry {key!r}'); continue
        # These are animation descriptors, not findable-object names.
        if key.startswith('anim_'):
            animations.append(key); continue
        matches = [oid for oid, name in names.items() if key == name or key.startswith(name+'_')]
        if len(matches) != 1:
            issues.append(f'Item {key}: expected one exact named-part prefix, found {len(matches)}'); continue
        oid = matches[0]
        if oid in items:
            issues.append(f'Multiple Item Manager keys for object {oid}'); continue
        label = labels.get('beginner') or labels.get('gold')
        if not isinstance(label, str) or not label:
            issues.append(f'Item {key}: no nonempty label'); continue
        items[oid] = {'manager_key': key, 'label': label, 'labels': labels}
    return items, animations, issues


def warp_sprite(tile, element, state, canvas_size):
    """GAF coordinates are top-left pixels; element pivot/scale precede matrix.

    Transform directly into the viewport to avoid enormous offscreen temporary
    sprites (the real Giza matte exceeds the stage bounds).
    """
    scale = element['scale']
    if not math.isfinite(scale) or scale <= 0: raise RenderError('Invalid element scale')
    a,b,c,d,x,y = state['matrix']
    a,b,c,d = a/scale,b/scale,c/scale,d/scale
    px,py = element['pivot']
    x -= a*px+c*py; y -= b*px+d*py
    det = a*d-b*c
    if abs(det) < 1e-12: raise RenderError('Singular GAF affine matrix')
    ia,ib,ic,id_ = d/det,-c/det,-b/det,a/det
    matrix = (ia,ib,-ia*x-ib*y,ic,id_,-ic*x-id_*y)
    tile = tile.copy()
    alpha = state['alpha']
    if not 0 <= alpha <= 1: raise RenderError('GAF alpha outside [0,1]')
    color = state.get('color')
    if color:
        ao,rm,ro,gm,go,bm,bo = color
        channels = tile.split()
        rgb = [ch.point([max(0,min(255,round(v*mul+off*255))) for v in range(256)])
               for ch,mul,off in zip(channels[:3],(rm,gm,bm),(ro,go,bo))]
        al = channels[3].point([max(0,min(255,round(v*alpha+ao*255))) for v in range(256)])
        tile = Image.merge('RGBA', (*rgb,al))
    elif alpha < 1:
        tile.putalpha(tile.getchannel('A').point([round(v*alpha) for v in range(256)]))
    return tile.transform(canvas_size, Image.Transform.AFFINE, matrix,
                          resample=Image.Resampling.BICUBIC, fillcolor=(0,0,0,0))


def load_scene(bundle):
    import UnityPy
    validate_bundle(bundle)
    env = UnityPy.load(Path(bundle).read_bytes())
    textures, mono = {}, {}
    for obj in env.objects:
        if obj.type.name == 'Texture2D':
            data = obj.read()
            textures[obj.path_id] = (data.m_Name, data.image.convert('RGBA'))
        elif obj.type.name == 'MonoBehaviour':
            mono[obj.path_id] = obj.read_typetree()
    assets = [(pid,t) for pid,t in mono.items() if 'm_AssetData' in t]
    if len(assets) != 1: raise RenderError(f'Expected one GAF asset, found {len(assets)}')
    asset_id, tree = assets[0]
    movie = decode(bytes(tree['m_AssetData']))
    referenced = {d['element'] for timeline in movie.timelines.values() for d in timeline.objects.values() if d['type']==2}
    if not referenced.issubset(movie.timelines): raise RenderError('Missing nested GAF timeline')
    root_timelines = set(movie.timelines)-referenced
    if len(root_timelines)!=1: raise RenderError('No unique unreferenced GAF root timeline')
    root_timeline = next(iter(root_timelines))
    roots = [(pid,t) for pid,t in mono.items() if t.get('m_TimelineID')==root_timeline and
             t.get('m_GAFAsset',{}).get('m_PathID')==asset_id]
    if len(roots) != 1: raise RenderError('Cannot establish a unique root GAF clip')
    root_id, root = roots[0]
    tl = movie.timelines[root['m_TimelineID']]
    # Use the root clip's explicitly serialized scale and content-scale factor.
    settings = root.get('m_Settings', {})
    scale, csf = settings.get('m_Scale'), settings.get('m_CSF')
    if scale != 1 or csf != 1 or settings.get('m_FlipX') or settings.get('m_PixelsPerUnit') != 1:
        raise RenderError('Root clip requires unsupported scale, CSF, flip or pixel-unit conversion')
    if settings.get('m_PivotOffset') != {'x':0.0,'y':0.0}:
        raise RenderError('Nonzero root clip pivot offset is unsupported')
    atlases = [a for a in movie.atlases if a['timeline']==tl.id and abs(a['scale']-scale)<1e-6]
    if not atlases:
        atlases = [a for a in movie.atlases if a['timeline'] is None and abs(a['scale']-scale)<1e-6]
    if len(atlases) != 1: raise RenderError('No unique atlas definition for root clip scale')
    atlas = atlases[0]
    resources = [t for t in mono.values() if t.get('m_Asset',{}).get('m_PathID')==asset_id and isinstance(t.get('m_Data'),list)
                 and t.get('m_Scale')==scale and t.get('m_CSF')==csf]
    if len(resources) != 1: raise RenderError('No unique GAF shared-resource table')
    source_textures = {}
    for aid, choices in atlas['sources'].items():
        sources = [s for s in choices if abs(s['csf']-csf)<1e-6]
        if len(sources) != 1: raise RenderError(f'No unique source for atlas ID {aid}')
        name = Path(sources[0]['source']).stem
        matches = [r for r in resources[0]['m_Data'] if r['m_SearchName']==name]
        if len(matches)!=1: raise RenderError(f'No exact texture resource for {name}')
        pointer = matches[0]['m_SharedTexture']
        if pointer['m_FileID']!=0 or pointer['m_PathID'] not in textures: raise RenderError(f'Missing texture {name}')
        actual_name, image = textures[pointer['m_PathID']]
        if actual_name!=name: raise RenderError('Texture name/reference disagreement')
        source_textures[aid] = (name,image)
    metadata = {}
    for t in mono.values():
        d = t.get('m_Data')
        if isinstance(d,dict) and d.get('m_Clip',{}).get('m_PathID')==root_id and 'm_ID' in d:
            if d['m_ID'] in metadata: raise RenderError('Duplicate Unity object ID within root clip')
            metadata[d['m_ID']] = d
    return movie, tl, atlas, source_textures, metadata


def render_bundle(bundle, item_manager, output, frame=None):
    out = Path(output); out.mkdir(parents=True, exist_ok=True)
    movie, tl, atlas, textures, metadata = load_scene(bundle)
    stage = movie.stage
    if not stage or not 0 < stage['width'] <= 8192 or not 0 < stage['height'] <= 8192:
        raise RenderError('Missing or unsafe GAF stage dimensions')
    size = stage['width'],stage['height']
    items, animation_keys, issues = parse_items(item_manager, tl.names)
    if tl.actions: issues.append('Timeline actions are decoded but not interpreted')
    if movie.unsupported: issues.append(f'Unsupported GAF tags: {movie.unsupported}')
    if set(tl.objects)!=set(metadata): issues.append('Unity/GAF root object sets differ')
    for oid,name in tl.names.items():
        if oid not in items and not name.startswith(('bg_','anim_')) and name != 'timecap':
            issues.append(f'Unclassified named object {name} ({oid})')
    if not items: issues.append('No hidden objects identified')
    if frame is None:
        # Prefer a coherent frame with the most identified/named objects,
        # then the most objects at their own maximum encoded opacity. A
        # deliberately translucent sprite is not a missing object.
        required = {oid for oid,name in tl.names.items() if name!='timecap'}
        maximum = {oid:0.0 for oid in required}
        for changes in tl.frames.values():
            for change in changes:
                if change['object_id'] in maximum:
                    maximum[change['object_id']] = max(maximum[change['object_id']],change['alpha'])
        active = {}; best = (-1,-1,-1); frame = 1
        for n in range(1,tl.frame_count+1):
            for change in tl.frames.get(n,[]):
                if change['alpha']==0: active.pop(change['object_id'],None)
                else: active[change['object_id']]=change
            score = (sum(oid in active for oid in items),
                     sum(oid in active for oid in required),
                     sum(oid in active and active[oid]['alpha']>=maximum[oid]-1e-6 for oid in required))
            if score>best: best,frame=score,n
    state = tl.state_at(frame)
    excluded = [oid for oid,name in tl.names.items() if name=='timecap']
    inactive = sorted(set(tl.objects)-set(state))
    rows, drawables = [], []
    for oid,definition in tl.objects.items():
        record = {'object_id': oid, 'name': tl.names.get(oid, metadata.get(oid,{}).get('m_Name','')),
                  'element_id': definition['element'], 'kind': 'hidden_object' if oid in items else 'decoration',
                  'status': 'inactive_at_snapshot' if oid not in state else 'pending'}
        if oid in items: record.update(items[oid])
        if oid in excluded: record.update(status='excluded_gameplay',kind='gameplay')
        elif oid in state:
            s = state[oid]
            record.update(depth=s['depth'],matrix=s['matrix'],alpha=s['alpha'],mask=s['mask'])
            try:
                if definition['type']!=0: raise RenderError('Nested timeline/text rendering is not certified yet')
                if s['effects']: raise RenderError('GAF filters are not supported yet')
                eid = definition['element']; element = atlas['elements'].get(eid)
                if element is None: raise RenderError(f'Missing atlas element {eid}')
                if element['scale9']: raise RenderError('Scale9 geometry is not supported')
                md = metadata.get(oid,{})
                if md.get('m_UseCustomAtlasTextureRect') or md.get('m_AtlasCustomElementID',-1)!=-1:
                    raise RenderError('Custom Unity sprite rectangle/element is unsupported')
                if md.get('m_Offset',{'x':0,'y':0})!={'x':0.0,'y':0.0} or md.get('m_MeshSizeMultiplier',{'x':1,'y':1})!={'x':1.0,'y':1.0}:
                    raise RenderError('Custom Unity sprite offset/mesh scale is unsupported')
                if md and md.get('m_AtlasInitialElementID')!=eid: raise RenderError('Unity/GAF atlas element disagreement')
                name, image = textures[element['atlas_id']]
                x,y = element['origin']; w,h = element['size']
                if any(abs(v-round(v))>1e-5 for v in (x,y,w,h)): raise RenderError('Non-integral atlas crop')
                x,y,w,h = map(round,(x,y,w,h))
                if min(x,y)<0 or min(w,h)<=0 or x+w>image.width or y+h>image.height: raise RenderError('Atlas crop outside texture')
                tile = image.crop((x,y,x+w,y+h))
                record.update(atlas_id=element['atlas_id'],texture=name,crop=[x,y,w,h],pivot=element['pivot'],element_scale=element['scale'])
                if oid in tl.masks:
                    record.update(kind='mask',status='mask_only')
                else:
                    record['status']='rendered'
                drawables.append((oid,s,tile,element))
            except (ValueError,KeyError) as exc:
                record.update(status='unsupported',error=str(exc)); issues.append(f'Object {oid}: {exc}')
        rows.append(record)
    rowmap={r['object_id']:r for r in rows}
    for oid in items:
        if oid not in state: issues.append(f'Required hidden object {oid} is invisible at frame {frame}')
    masks={}
    for oid,s,tile,element in drawables:
        if oid in tl.masks:
            if s['mask'] is not None: issues.append(f'Nested masks unsupported for {oid}')
            masks[oid]=warp_sprite(tile,element,s,size).getchannel('A')
    def layer_for(entry):
        oid,s,tile,element=entry
        layer=warp_sprite(tile,element,s,size)
        if s['mask'] is not None:
            mask=masks.get(s['mask'])
            if mask is None: raise RenderError(f'Missing mask {s["mask"]} for {oid}')
            layer.putalpha(ImageChops.multiply(layer.getchannel('A'),mask))
        return layer
    # Same background reference at different depths can be an occluder, not a duplicate.
    # Flag identical background draws for review instead of deleting real art by guess.
    background_signatures=Counter()
    for entry in drawables:
        oid,s,tile,element=entry
        if tl.names.get(oid,'').startswith('bg_'):
            background_signatures[(tl.objects[oid]['element'],s['matrix'],s['alpha'],s['mask'])]+=1
    duplicate_backgrounds=sum(n-1 for n in background_signatures.values() if n>1)
    if duplicate_backgrounds: issues.append('Repeated identical background placements need review')
    ordered=sorted((e for e in drawables if e[0] not in tl.masks),key=lambda e:e[1]['depth'])
    if len({e[1]['depth'] for e in ordered})!=len(ordered): issues.append('Ambiguous duplicate draw depth')
    b,g,r,a = stage['bgra']
    # The GAF stage explicitly supplies its clear color; retain it under
    # antialiased texture-edge pixels rather than inventing an opaque fill.
    empty=Image.new('RGBA',size,(r,g,b,a)); complete=Image.new('RGBA',size,(r,g,b,a))
    for entry in ordered:
        try: layer=layer_for(entry)
        except RenderError as exc:
            issues.append(str(exc));rowmap[entry[0]]['status']='unsupported';continue
        complete=Image.alpha_composite(complete,layer)
        if entry[0] not in items: empty=Image.alpha_composite(empty,layer)
    if not background_signatures: issues.append('No named background placement was found')
    transmittance=Image.new('L',size,255); highlights=Image.new('L',size)
    markers=[]
    for entry in reversed(ordered):
        oid=entry[0]
        if rowmap[oid]['status']!='rendered':continue
        layer=layer_for(entry); alpha=layer.getchannel('A')
        if oid in items:
            visible=ImageChops.multiply(alpha,transmittance)
            threshold=visible.point([255 if v>=16 else 0 for v in range(256)])
            bbox=threshold.getbbox()
            if not bbox: issues.append(f'Hidden object {oid} has no visible pixels')
            else:
                rowmap[oid]['visible_bbox']=list(bbox)
                rowmap[oid]['visible_pixels']=threshold.histogram()[255]
                anchor=visible_anchor(threshold)
                markers.append({'object_id':oid,**items[oid],'bbox':list(bbox),'visible_pixels':threshold.histogram()[255],
                                'anchor':anchor,'anchor_alpha':visible.getpixel(tuple(anchor))})
                contour=ImageChops.subtract(threshold.filter(ImageFilter.MaxFilter(5)),threshold)
                glow=ImageChops.lighter(contour.point(lambda v:round(v*.9)),threshold.point(lambda v:round(v*.25)))
                highlights=ImageChops.lighter(highlights,glow)
        transmittance=ImageChops.multiply(transmittance,ImageChops.invert(alpha))
    yellow=Image.new('RGBA',size,(255,225,0,0));yellow.putalpha(highlights)
    solution=Image.alpha_composite(complete,yellow)
    if len(markers)!=len(items): issues.append('Solution marker count differs from identified hidden objects')
    if len(items)!=len([k for k in item_manager if not k.startswith('anim_')]): issues.append('Not all Item Manager hidden objects were resolved')
    highlights_differ = ImageChops.difference(complete,solution).convert('RGB').getbbox() is not None
    solution,solution_scene_box=annotate_solution(solution,markers)
    issues.extend(validate_callouts(markers,solution.size,solution_scene_box))
    coverage={}
    for name,image in [('empty',empty),('complete',complete),('solution',solution)]:
        extrema=image.getchannel('A').getextrema();coverage[name]={'alpha_extrema':list(extrema),'bbox':image.getbbox()}
        if image.getbbox() is None or all(lo==hi for lo,hi in image.convert('RGB').getextrema()):
            issues.append(f'{name} image is unexpectedly blank or uniform')
        if extrema[0]!=255: issues.append(f'{name} stage contains uncovered or translucent pixels')
    if ImageChops.difference(empty,complete).convert('RGB').getbbox() is None: issues.append('Empty and complete images are identical')
    if not highlights_differ: issues.append('Complete and solution images are identical')
    issues=sorted(set(issues))
    valid=not issues
    destination=out if valid else out/'incomplete'
    destination.mkdir(parents=True,exist_ok=True)
    files=[]
    for name,image in [('scene_empty.png',empty),('scene_complete.png',complete),('scene_solution.png',solution)]:
        path=destination/name;tmp=path.with_suffix('.tmp');image.save(tmp,format='PNG');tmp.replace(path);files.append(str(path))
    report={'status':'validated_snapshot' if valid else 'incomplete','scene_dimensions':list(size),
            'solution_dimensions':list(solution.size),'solution_scene_box':solution_scene_box,'named_pointers':len(markers),
            'gaf_version':list(movie.version),'root_timeline':tl.id,'decoded_frames':len(tl.frames),'timeline_frame_count':tl.frame_count,
            'snapshot_frame':frame,'snapshot_policy':'one decoded frame maximizing hidden/named object visibility and encoded opacity; no union of animation cells',
            'bundle_sha256':sha256(bundle),'item_manager_sha256':hashlib.sha256(json.dumps(item_manager,sort_keys=True).encode()).hexdigest(),
            'root_objects':len(tl.objects),'identified_hidden_objects':len(items),'solution_markers':len(markers),
            'inactive_at_snapshot':inactive,'excluded_gameplay_objects':excluded,'animation_metadata_keys':animation_keys,
            'atlases':[{'id':i,'texture':n,'dimensions':list(im.size)} for i,(n,im) in textures.items()],
            'duplicate_backgrounds':duplicate_backgrounds,'coverage':coverage,'issues':issues,'files':files,
            'validation_scope':'Structural, metadata, coverage and visible-object checks; pixel parity with the game is not independently verified.'}
    atomic_json(out/'validation_report.json',report);atomic_json(out/'objects.json',rows);atomic_json(out/'solution_objects.json',sorted(markers,key=lambda x:x['object_id']))
    return report
