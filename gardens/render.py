"""Static ordinary-scene rendering from explicit GAF tags and Item Manager data.

Unsupported features produce previews and an incomplete report, never certified
scene files. No atlas scoring, placement scanning, or fuzzy label matching.
"""
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

from .annotations import SOLUTION_LAYOUT, annotate_solution, validate_callouts, visible_anchor
from .download import atomic_json, sha256, validate_bundle
from .gaf import GAFError, decode


class RenderError(ValueError):
    pass


def parse_items(data, names, reviewed_bindings=None):
    if not isinstance(data, dict) or not data:
        raise RenderError('Item Manager must be a nonempty JSON object')
    items, animations, issues = {}, [], []
    animation_bases=[re.sub(r'\d+$','',name.casefold().replace('anim_','anim',1)) for name in names.values() if name.casefold().startswith('anim_')]
    for key, labels in data.items():
        if not isinstance(key, str) or not isinstance(labels, dict):
            issues.append(f'Unsupported Item Manager entry {key!r}'); continue
        # These are animation descriptors, not findable-object names.
        folded=key.casefold()
        if folded.startswith('anim_') or any(folded==base or folded.startswith(base+'_') for base in animation_bases):
            animations.append(key); continue
        matches = [oid for oid, name in names.items() if key.casefold() == name.casefold() or key.casefold().startswith(name.casefold()+'_')]
        binding_override=None
        if not matches and reviewed_bindings and key in reviewed_bindings:
            reviewed=reviewed_bindings[key]
            targets=[oid for oid,name in names.items() if name==reviewed['encoded_name']]
            # Never repair an ambiguous ordinary match or bind a similarly
            # named/duplicated object. The source hashes are checked upstream.
            if targets==[reviewed['object_id']]:
                matches=targets;binding_override=reviewed
        if matches:
            # A suffix-bearing name such as curtain_1 is more specific than
            # curtain for curtain_1_<scene>. Keep every identically named
            # instance, while equal-length case collisions remain ambiguous.
            longest=max(len(names[oid].casefold()) for oid in matches)
            matches=[oid for oid in matches if len(names[oid].casefold())==longest]
        if not matches or len({names[oid] for oid in matches})!=1:
            issues.append(f'Item {key}: expected an unambiguous named-part prefix, found {len(matches)}'); continue
        if any(oid in items for oid in matches):
            issues.append(f'Multiple Item Manager keys for objects {matches}'); continue
        label = labels.get('beginner') or labels.get('gold')
        if not isinstance(label, str) or not label:
            issues.append(f'Item {key}: no nonempty label'); continue
        for oid in matches:
            items[oid] = {'manager_key': key, 'label': label, 'labels': labels}
            if binding_override is not None:
                items[oid]['binding_override']={'method':'reviewed_source_alias',**binding_override}
    return items, animations, issues


def warp_sprite(tile, element, state, canvas_size):
    """GAF coordinates are top-left pixels; element pivot/scale precede matrix.

    Transform directly into the viewport to avoid enormous offscreen temporary
    sprites (the real Giza matte exceeds the stage bounds).
    """
    if state.get('effects'):
        from .effects import apply_effects
        tile,(ox,oy)=apply_effects(tile,state['effects'],with_offset=True)
        element={**element,'pivot':(element['pivot'][0]+ox,element['pivot'][1]+oy)}
    scale = element['scale']
    scale_y = element.get('scale_y',scale)
    if not all(math.isfinite(v) and v>0 for v in (scale,scale_y)): raise RenderError('Invalid element scale')
    a,b,c,d,x,y = state['matrix']
    a,b,c,d = a/scale,b/scale,c/scale_y,d/scale_y
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


def sprite_bounds(tile,element,state):
    """Unclipped encoded bounds, for diagnosing off-stage targets."""
    a,b,c,d,x,y=state['matrix'];sx=element['scale'];sy=element.get('scale_y',sx)
    px,py=element['pivot']
    points=[(x+a*(u-px)/sx+c*(v-py)/sy,y+b*(u-px)/sx+d*(v-py)/sy)
            for u,v in ((0,0),(tile.width,0),(0,tile.height),(tile.width,tile.height))]
    return [min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]


def _select_root_clip(root_timelines,mono,asset_id,gameobjects,transforms,prefab_root_ids):
    """Resolve library-timeline ambiguity from an active serialized prefab root.

    Only explicit local references establish ownership. Timeline numbers,
    linkage names and object counts do not choose the scene root.
    """
    def local(pointer,path_id):
        return isinstance(pointer,dict) and pointer.get('m_FileID')==0 and pointer.get('m_PathID')==path_id
    candidates=[]
    for pid,clip in mono.items():
        if (clip.get('m_TimelineID') not in root_timelines or not local(clip.get('m_GAFAsset'),asset_id) or
                not local(clip.get('m_Parent'),0) or clip.get('m_Enabled')!=1 or clip.get('m_IsActive')!=1):
            continue
        go_pointer=clip.get('m_GameObject');go_id=go_pointer.get('m_PathID') if isinstance(go_pointer,dict) else None
        if go_id not in prefab_root_ids or not local(go_pointer,go_id):continue
        go=gameobjects.get(go_id)
        if not isinstance(go,dict) or go.get('m_IsActive')!=1:continue
        components={entry['component'].get('m_PathID') for entry in go.get('m_Component',[])
                    if isinstance(entry,dict) and isinstance(entry.get('component'),dict) and entry['component'].get('m_FileID')==0}
        if pid not in components:continue
        parents=[(tid,t) for tid,t in transforms.items() if local(t.get('m_GameObject'),go_id)]
        if len(parents)!=1:continue
        transform_id,transform=parents[0]
        if transform_id not in components or not local(transform.get('m_Father'),0):continue
        candidates.append((pid,clip))
    if len(candidates)!=1:raise RenderError('Cannot establish a unique active prefab GAF root clip')
    return candidates[0]


def load_scene(bundle, with_context=False):
    import UnityPy
    validate_bundle(bundle)
    env = UnityPy.load(Path(bundle).read_bytes())
    textures, mono = {}, {}
    for obj in env.objects:
        if obj.type.name == 'Texture2D':
            data = obj.read()
            image=data.image.convert('RGBA')
            texture_settings=getattr(data,'m_TextureSettings',None)
            image.info['unity_wrap_modes']=(getattr(texture_settings,'m_WrapU',None),
                                            getattr(texture_settings,'m_WrapV',None))
            textures[obj.path_id] = (data.m_Name, image)
        elif obj.type.name == 'MonoBehaviour':
            mono[obj.path_id] = obj.read_typetree()
    assets = [(pid,t) for pid,t in mono.items() if 'm_AssetData' in t]
    if len(assets) != 1: raise RenderError(f'Expected one GAF asset, found {len(assets)}')
    asset_id, tree = assets[0]
    movie = decode(bytes(tree['m_AssetData']))
    referenced = {d['element'] for timeline in movie.timelines.values() for d in timeline.objects.values() if d['type']==2}
    if not referenced.issubset(movie.timelines): raise RenderError('Missing nested GAF timeline')
    root_timelines = set(movie.timelines)-referenced
    if not root_timelines: raise RenderError('No unique unreferenced GAF root timeline')
    if len(root_timelines)==1:
        root_timeline = next(iter(root_timelines))
        roots = [(pid,t) for pid,t in mono.items() if t.get('m_TimelineID')==root_timeline and
                 t.get('m_GAFAsset',{}).get('m_PathID')==asset_id]
        if len(roots) != 1: raise RenderError('Cannot establish a unique root GAF clip')
        root_id, root = roots[0]
    else:
        # A GAF can export unused library timelines alongside the scene. Resolve
        # only this ambiguity from the prefab's active, parentless clip.
        gameobjects,transforms,prefab_roots={},{},set()
        for obj in env.objects:
            if obj.type.name=='GameObject':gameobjects[obj.path_id]=obj.read_typetree()
            elif obj.type.name in ('Transform','RectTransform'):transforms[obj.path_id]=obj.read_typetree()
            elif obj.type.name=='AssetBundle':
                for name,entry in obj.read_typetree().get('m_Container',[]):
                    pointer=entry.get('asset',{})
                    if name.casefold().endswith('.prefab') and pointer.get('m_FileID')==0:
                        prefab_roots.add(pointer.get('m_PathID'))
        root_id,root=_select_root_clip(root_timelines,mono,asset_id,gameobjects,transforms,prefab_roots)
    tl = movie.timelines[root['m_TimelineID']]
    # Use the root clip's explicitly serialized scale and content-scale factor.
    settings = root.get('m_Settings', {})
    scale, csf = settings.get('m_Scale'), settings.get('m_CSF')
    if scale != 1 or csf != 1 or settings.get('m_FlipX') or settings.get('m_PixelsPerUnit') != 1:
        raise RenderError('Root clip requires unsupported scale, CSF, flip or pixel-unit conversion')
    if (settings.get('m_AnimationColor')!={'r':1.0,'g':1.0,'b':1.0,'a':1.0} or
            settings.get('m_AnimationColorOffset')!={'x':0.0,'y':0.0,'z':0.0,'w':0.0}):
        raise RenderError('Custom root Unity clip colour is unsupported')
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
    def resolve_scope(tid):
        if tid==tl.id:
            return {'timeline':tl,'atlas':atlas,'textures':source_textures,'metadata':metadata,'clip':root}
        clips=[(pid,t) for pid,t in mono.items() if t.get('m_TimelineID')==tid and t.get('m_GAFAsset',{}).get('m_PathID')==asset_id]
        if len(clips)>1:
            signatures=[]
            for pid,instance in clips:
                objects=[]
                for value in mono.values():
                    data=value.get('m_Data')
                    if isinstance(data,dict) and data.get('m_Clip',{}).get('m_PathID')==pid and 'm_ID' in data:
                        objects.append({k:v for k,v in data.items() if k not in ('m_Clip','m_Manager')})
                signatures.append(json.dumps([instance.get('m_Settings'),instance.get('m_InternalFrameNumber'),sorted(objects,key=lambda d:d['m_ID'])],sort_keys=True))
            if len(set(signatures))!=1: raise RenderError(f'Conflicting Unity instances for nested timeline {tid}')
        # Referenced GAF timelines can be instantiated at runtime without a
        # serialized Unity component. Their encoded atlas/objects remain authoritative.
        runtime_template=not clips
        clip_id,clip=clips[0] if clips else (None,{'m_Settings':root['m_Settings'],'m_InternalFrameNumber':1})
        settings=clip.get('m_Settings',{})
        if (settings.get('m_Scale')!=1 or settings.get('m_CSF')!=1 or settings.get('m_FlipX') or
                settings.get('m_PixelsPerUnit')!=1 or settings.get('m_PivotOffset')!={'x':0.0,'y':0.0}):
            raise RenderError(f'Unsupported nested clip geometry for timeline {tid}')
        if (settings.get('m_AnimationColor')!={'r':1.0,'g':1.0,'b':1.0,'a':1.0} or
                settings.get('m_AnimationColorOffset')!={'x':0.0,'y':0.0,'z':0.0,'w':0.0}):
            raise RenderError(f'Custom nested clip color is unsupported for timeline {tid}')
        if clip.get('m_InternalFrameNumber')!=1:
            raise RenderError(f'Nested timeline {tid} does not start at encoded frame 1')
        definitions=[a for a in movie.atlases if a['timeline']==tid and a['scale']==1]
        if not definitions: definitions=[a for a in movie.atlases if a['timeline'] is None and a['scale']==1]
        if not definitions and all(d['type']==2 for d in movie.timelines[tid].objects.values()):
            definitions=[{'elements':{},'sources':{}}]
        if len(definitions)!=1: raise RenderError(f'No unique nested atlas definition for timeline {tid}')
        child_atlas=definitions[0];child_textures={}
        for aid,sources in child_atlas['sources'].items():
            choices=[x for x in sources if x['csf']==1]
            if len(choices)!=1: raise RenderError('Ambiguous nested atlas source')
            name=Path(choices[0]['source']).stem
            matches=[r for r in resources[0]['m_Data'] if r['m_SearchName']==name]
            if len(matches)!=1: raise RenderError(f'Missing nested texture resource {name}')
            pointer=matches[0]['m_SharedTexture']
            if pointer['m_FileID']!=0 or pointer['m_PathID'] not in textures: raise RenderError(f'Missing nested texture {name}')
            actual,image=textures[pointer['m_PathID']]
            if actual!=name: raise RenderError('Nested texture reference/name disagreement')
            child_textures[aid]=(actual,image)
        child_metadata={}
        for tree in mono.values():
            d=tree.get('m_Data')
            if clip_id is not None and isinstance(d,dict) and d.get('m_Clip',{}).get('m_PathID')==clip_id and 'm_ID' in d:
                if d['m_ID'] in child_metadata: raise RenderError('Duplicate nested Unity object ID')
                child_metadata[d['m_ID']]=d
        if not runtime_template and set(child_metadata)!=set(movie.timelines[tid].objects): raise RenderError('Nested Unity/GAF object sets differ')
        return {'timeline':movie.timelines[tid],'atlas':child_atlas,'textures':child_textures,'metadata':child_metadata,'clip':clip,'scope_source':'gaf_runtime_template' if runtime_template else 'serialized_unity','equivalent_clip_instances':len(clips)}
    result=(movie,tl,atlas,source_textures,metadata)
    return (*result,resolve_scope) if with_context else result


def is_background(name):
    return name.casefold().startswith('bg_') or re.fullmatch(r'bg\d+(?:_\d+)*',name,re.I) is not None or name.casefold() in ('bg','background','backdrop')


def check_snapshot_actions(timeline,frame):
    for n,actions in timeline.actions.items():
        if n>frame:continue
        for action in actions:
            # Stop affects future playback only, not the first displayed pose.
            if not (frame==1 and action=={'type':0,'scope':'','params':[]}):
                raise RenderError(f'Unsupported action in timeline {timeline.id}, frame {n}: {action}')


def atlas_crop(image,rect):
    """Sample an atlas rectangle using explicit serialized Clamp edge modes.

    GAF rectangles may legitimately extend beyond a Unity texture. UVs outside
    an axis with TextureWrapMode.Clamp (1) sample the actual border texels. Do
    not infer that mode when settings are absent or use transparent padding.
    """
    x,y,w,h=rect
    if not all(isinstance(v,int) for v in rect):raise RenderError('Non-integral atlas crop')
    if min(w,h)<=0:raise RenderError('Atlas crop outside texture')
    outside_u=x<0 or x+w>image.width;outside_v=y<0 or y+h>image.height
    if not outside_u and not outside_v:return image.crop((x,y,x+w,y+h))
    modes=image.info.get('unity_wrap_modes')
    if not isinstance(modes,(tuple,list)) or len(modes)!=2:modes=(None,None)
    if (outside_u and modes[0]!=1) or (outside_v and modes[1]!=1):
        raise RenderError('Atlas crop outside texture')
    if w>8192 or h>8192 or w*h>8192**2:raise RenderError('Unsafe atlas crop dimensions')
    def segments(start,length,extent):
        result=[]
        if start<0:
            count=min(length,-start)
            result.append((0,count,0,1))
        left=max(0,start);right=min(extent,start+length)
        if right>left:result.append((left-start,right-left,left,right))
        if start+length>extent:
            offset=max(0,extent-start)
            result.append((offset,length-offset,extent-1,extent))
        return result
    tile=Image.new(image.mode,(w,h))
    for dx,dw,sx,ex in segments(x,w,image.width):
        for dy,dh,sy,ey in segments(y,h,image.height):
            piece=image.crop((sx,sy,ex,ey))
            if piece.size!=(dw,dh):piece=piece.resize((dw,dh),Image.Resampling.NEAREST)
            tile.paste(piece,(dx,dy))
    tile.info.update(image.info)
    return tile


def sprite_for(scope,oid):
    definition=scope['timeline'].objects[oid];eid=definition['element']
    if definition['type']!=0: raise RenderError('Expected an atlas sprite')
    element=scope['atlas']['elements'].get(eid)
    if element is None: raise RenderError(f'Missing atlas element {eid}')
    if element['scale9']: raise RenderError('Scale9 geometry is not supported')
    md=scope['metadata'].get(oid,{})
    if md.get('m_UseCustomAtlasTextureRect') or md.get('m_AtlasCustomElementID',-1)!=-1:
        raise RenderError('Custom Unity sprite rectangle/element is unsupported')
    if md.get('m_Offset',{'x':0,'y':0})!={'x':0.0,'y':0.0} or md.get('m_MeshSizeMultiplier',{'x':1,'y':1})!={'x':1.0,'y':1.0}:
        raise RenderError('Custom Unity sprite offset/mesh scale is unsupported')
    if md and md.get('m_AtlasInitialElementID')!=eid: raise RenderError('Unity/GAF atlas element disagreement')
    name,image=scope['textures'][element['atlas_id']]
    x,y=element['origin'];w,h=element['size']
    if any(abs(v-round(v))>1e-5 for v in (x,y,w,h)): raise RenderError('Non-integral atlas crop')
    x,y,w,h=map(round,(x,y,w,h))
    tile=atlas_crop(image,(x,y,w,h))
    if element.get('rotated'): tile=tile.transpose(Image.Transpose.ROTATE_90)
    sampling={}
    if x<0 or y<0 or x+w>image.width or y+h>image.height:
        sampling={'atlas_sampling':'serialized_clamp_edge','texture_wrap_modes':list(image.info['unity_wrap_modes'])}
    return tile,element,dict(atlas_id=element['atlas_id'],texture=name,crop=[x,y,w,h],pivot=element['pivot'],element_scale=element['scale'],**sampling)



def render_bundle(bundle, item_manager, output, frame=None):
    out = Path(output); out.mkdir(parents=True, exist_ok=True)
    # Invalidate old final names before loading: corrupt inputs can fail early.
    for name in ('scene_empty.png','scene_complete.png','scene_solution.png'):
        (out/name).unlink(missing_ok=True)
    movie, tl, atlas, textures, metadata, resolve_scope = load_scene(bundle, with_context=True)
    stage = movie.stage
    if not stage or not 0 < stage['width'] <= 8192 or not 0 < stage['height'] <= 8192:
        raise RenderError('Missing or unsafe GAF stage dimensions')
    size = stage['width'],stage['height']
    from .item_aliases import reviewed_item_bindings
    bundle_digest=sha256(bundle)
    manager_digest=hashlib.sha256(json.dumps(item_manager,sort_keys=True).encode()).hexdigest()
    items, animation_keys, issues = parse_items(item_manager, tl.names,reviewed_item_bindings(bundle_digest,manager_digest))
    item_binding_issues=list(issues)
    if movie.unsupported: issues.append(f'Unsupported GAF tags: {movie.unsupported}')
    if set(tl.objects)!=set(metadata): issues.append('Unity/GAF root object sets differ')
    unresolved_names={oid:name for oid,name in tl.names.items()
                      if oid not in items and not is_background(name) and not name.casefold().startswith('anim_') and name.casefold()!='timecap'}
    warnings=[f'Extra named object {oid}: {name}; not listed in Item Manager'
              for oid,name in unresolved_names.items()]
    if not items: issues.append('No hidden objects identified')
    has_nested=any(d['type']==2 for d in tl.objects.values())
    if frame is None and has_nested: frame=1
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
    try: check_snapshot_actions(tl,frame)
    except RenderError as exc: issues.append(str(exc))
    if has_nested and frame!=1: issues.append('Nested snapshots currently require root frame 1; elapsed playback is not simulated')
    state = tl.state_at(frame)
    excluded = [oid for oid,name in tl.names.items() if name=='timecap']
    inactive = sorted(set(tl.objects)-set(state))
    rows, drawables = [], []
    for oid,definition in tl.objects.items():
        record = {'object_id': oid, 'name': tl.names.get(oid, metadata.get(oid,{}).get('m_Name','')),
                  'element_id': definition['element'], 'kind': 'hidden_object' if oid in items else ('unverified_named_object' if oid in unresolved_names else 'decoration'),
                  'status': 'inactive_at_snapshot' if oid not in state else 'pending'}
        if oid in items: record.update(items[oid])
        if oid in excluded: record.update(status='excluded_gameplay',kind='gameplay')
        elif oid in state:
            s = state[oid]
            record.update(depth=s['depth'],matrix=s['matrix'],alpha=s['alpha'],mask=s['mask'])
            try:
                from .effects import supported
                if s['effects'] and not supported(s['effects']):
                    raise RenderError('Unsupported GAF spatial-filter combination')
                if definition['type']==2:
                    if oid in tl.masks: raise RenderError('Nested timeline used as a mask is unsupported')
                    record.update(status='rendered_nested',nested_timeline=definition['element'])
                    drawables.append((oid,s,None,None));rows.append(record);continue
                if definition['type']!=0: raise RenderError('Text rendering is unsupported')
                tile,element,fields=sprite_for(resolve_scope(tl.id),oid)
                record.update(fields,encoded_bounds=sprite_bounds(tile,element,s))
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
            masks[oid]=warp_sprite(tile,element,s,size).getchannel('A')
    mask_states={oid:s for oid,s,_,_ in drawables if oid in tl.masks}
    def resolve_mask(oid,stack=()):
        if oid in stack: raise RenderError('Mask relationship cycle')
        if oid not in masks: raise RenderError(f'Missing mask {oid}')
        parent=mask_states[oid]['mask']
        return masks[oid] if parent is None else ImageChops.multiply(masks[oid],resolve_mask(parent,stack+(oid,)))
    try: masks={oid:resolve_mask(oid) for oid in masks}
    except RenderError as exc: issues.append(str(exc))
    from .nested import NestedPainter
    nested=NestedPainter(movie,resolve_scope,size)
    def layer_for(entry):
        oid,s,tile,element=entry
        if tile is None:
            mask=masks.get(s['mask']) if s['mask'] is not None else None
            if s['mask'] is not None and mask is None: raise RenderError(f'Missing parent mask for {oid}')
            if frame!=1: raise RenderError('Nested playback beyond initial frame is unsupported')
            return nested.paint(tl.objects[oid]['element'],s['matrix'],s['alpha'],[mask] if mask else [],path=(oid,),color=s['color'],effects=s['effects'])
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
        if is_background(tl.names.get(oid,'')):
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
        except (RenderError,KeyError) as exc:
            issues.append(str(exc));rowmap[entry[0]]['status']='unsupported';continue
        complete=Image.alpha_composite(complete,layer)
        # Both verified clues and unverified named objects
        # belong in the complete/solution views, not in the empty background.
        if entry[0] not in items and entry[0] not in unresolved_names:
            empty=Image.alpha_composite(empty,layer)
    if not background_signatures: issues.append('No named background placement was found')
    transmittance=Image.new('L',size,255); highlights=Image.new('L',size)
    markers=[];used_anchors=[]
    for entry in reversed(ordered):
        oid=entry[0]
        if rowmap[oid]['status'] not in ('rendered','rendered_nested'):continue
        layer=layer_for(entry); alpha=layer.getchannel('A')
        if oid in items or oid in unresolved_names:
            visible=ImageChops.multiply(alpha,transmittance)
            threshold=visible.point([255 if v>=16 else 0 for v in range(256)])
            bbox=threshold.getbbox()
            if not bbox and oid in items:
                bounds=rowmap[oid].get('encoded_bounds')
                if bounds and (bounds[2]<=0 or bounds[3]<=0 or bounds[0]>=size[0] or bounds[1]>=size[1]):
                    issues.append(f'Hidden object {oid} lies outside encoded stage: {bounds}')
                else: issues.append(f'Hidden object {oid} has no visible pixels')
            elif bbox:
                rowmap[oid]['visible_bbox']=list(bbox)
                rowmap[oid]['visible_pixels']=threshold.histogram()[255]
                if oid in items:
                    anchor=visible_anchor(threshold,avoid=used_anchors)
                    used_anchors.append(anchor)
                    markers.append({'object_id':oid,**items[oid],'label_source':'item_manager','label_verified':True,
                                    'bbox':list(bbox),'visible_pixels':threshold.histogram()[255],
                                    'anchor':anchor,'anchor_alpha':visible.getpixel(tuple(anchor))})
                    contour=ImageChops.subtract(threshold.filter(ImageFilter.MaxFilter(5)),threshold)
                    glow=ImageChops.lighter(contour.point(lambda v:round(v*.9)),threshold.point(lambda v:round(v*.25)))
                    highlights=ImageChops.lighter(highlights,glow)
        transmittance=ImageChops.multiply(transmittance,ImageChops.invert(alpha))
    yellow=Image.new('RGBA',size,(255,225,0,0));yellow.putalpha(highlights)
    solution=Image.alpha_composite(complete,yellow)
    if len(markers)!=len(items): issues.append('Solution marker count differs from identified hidden objects')
    if len({item['manager_key'] for item in items.values()})+len(animation_keys)!=len(item_manager): issues.append('Not all Item Manager hidden objects were resolved')
    manager_complete=(bool(items) and not item_binding_issues and len(markers)==len(items)
                      and len({item['manager_key'] for item in items.values()})+len(animation_keys)==len(item_manager))
    highlights_differ = ImageChops.difference(complete,solution).convert('RGB').getbbox() is not None
    solution,solution_scene_box=annotate_solution(solution,markers)
    annotation_issues=validate_callouts(markers,solution.size,solution_scene_box)
    issues.extend(annotation_issues)
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
            'solution_dimensions':list(solution.size),'solution_scene_box':solution_scene_box,'named_pointers':len(markers),'nested_snapshot_instances':nested.audit,
            'unverified_named_pointers':0,'total_named_pointers':len(markers),
            'visible_unverified_object_ids':sorted(oid for oid in unresolved_names if rowmap.get(oid,{}).get('visible_pixels',0)>0),
            'solution_layout':SOLUTION_LAYOUT,'annotation_issues':annotation_issues,
            'anchor_policy':'Actual visible pixels after occlusion; neighbouring clue targets use distinct visible pixels at least 10 pixels apart',
            'solution_policy':'Highlight verified clues with nonintersecting short numbered pointers and a side index; retain unverified objects as unmarked original art',
            'nested_policy':'Encoded first frame per child; no elapsed-time playback simulation',
            'filter_backend':'CPU Unity-style 32-tap separable box blur/outer glow; colour matrix before tint; GPU pixel parity not independently verified',
            'gaf_version':list(movie.version),'root_timeline':tl.id,'decoded_frames':len(tl.frames),'timeline_frame_count':tl.frame_count,
            'snapshot_frame':frame,'snapshot_policy':('Encoded initial frame throughout the nested tree' if has_nested else
                'one decoded frame maximizing hidden/named object visibility and encoded opacity; no union of animation cells'),
            'bundle_sha256':bundle_digest,'item_manager_sha256':manager_digest,
            'item_binding_overrides':[item['binding_override'] for item in items.values() if 'binding_override' in item],
            'root_objects':len(tl.objects),'identified_hidden_objects':len(items),'solution_markers':len(markers),
            'item_manager_complete':manager_complete,'warnings':warnings,
            'findable_item_manager_entries':len(item_manager)-len(animation_keys),
            'matched_item_manager_entries':len({item['manager_key'] for item in items.values()}),
            'empty_excluded_object_ids':sorted(set(items)|set(unresolved_names)),
            'empty_policy':'Omit verified Item Manager clues and unverified named objects; retain background and other scene decoration',
            'unresolved_named_objects':unresolved_names,'extra_named_objects':unresolved_names,
            'target_scope':'Every findable Item Manager entry must be matched and visible; extra named visuals are unmarked, nonblocking warnings',
            'inactive_at_snapshot':inactive,'excluded_gameplay_objects':excluded,'animation_metadata_keys':animation_keys,
            'atlases':[{'id':i,'texture':n,'dimensions':list(im.size)} for i,(n,im) in textures.items()],
            'duplicate_backgrounds':duplicate_backgrounds,'coverage':coverage,'issues':issues,'files':files,
            'validation_scope':'Structural, metadata, coverage and visible-object checks; pixel parity with the game is not independently verified.'}
    atomic_json(out/'validation_report.json',report);atomic_json(out/'objects.json',rows);atomic_json(out/'solution_objects.json',sorted(markers,key=lambda x:x['object_id']))
    return report
