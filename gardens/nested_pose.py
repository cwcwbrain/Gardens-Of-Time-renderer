"""Select one coherent encoded pose using enclosed-cutout alpha coverage.

This is a generic static snapshot policy, not elapsed playback simulation. It
never modifies artwork or source states and does not recognize scene identities.
"""

from array import array
from collections import Counter,defaultdict
import math

from PIL import Image,ImageChops,ImageDraw

POSE_SELECTION_VERSION='cutout_coverage_v1'

MAX_FRAMES=1200
MAX_OBJECTS=256
MAX_STATE_ENTRIES=120000
MAX_TEXTURE_PIXELS=8388608
MAX_HOST_PIXELS=2097152
MAX_SCANNED_HOST_PIXELS=8388608
MAX_COMPONENTS=2048
MAX_COMPONENT_PIXELS=65536
MAX_REGIONS=24
MAX_ROI_PIXELS=65536
MAX_TOTAL_ROI_PIXELS=131072
MAX_WARPS=4096
MAX_CACHED_WARP_PIXELS=16777216
MAX_SCORE_PIXEL_WORK=120000000


class _Declined(ValueError):
    pass


class _Budget:
    def __init__(self):self.counts=Counter()

    def use(self,key,amount,limit):
        self.counts[key]+=amount
        if self.counts[key]>limit:raise _Declined('Resource budget exceeded: '+key)


def _inverse(matrix):
    a,b,c,d,x,y=matrix;det=a*d-b*c
    if not math.isfinite(det) or abs(det)<1e-12:raise _Declined('Singular stationary sprite transform')
    return d/det,-b/det,-c/det,a/det,(c*y-d*x)/det,(b*x-a*y)/det


def _net(element,state):
    a,b,c,d,x,y=state['matrix'];sx=element['scale'];sy=element.get('scale_y',sx)
    if not all(math.isfinite(v) and v>0 for v in (sx,sy)):raise _Declined('Unsupported atlas scale')
    a,b,c,d=a/sx,b/sx,c/sy,d/sy;px,py=element['pivot']
    return a,b,c,d,x-a*px-c*py,y-b*px-d*py


def _holes(alpha,budget):
    """Enclosed nonopaque components, including their antialiased borders.

    A bounded label array avoids storing every component's pixel coordinates in
    Python lists. Flooding a padded exterior connects all four sprite borders.
    """
    width,height=alpha.size;pixels=width*height
    if pixels>MAX_HOST_PIXELS:raise _Declined('Resource budget exceeded: host pixels')
    budget.use('scanned_host_pixels',pixels,MAX_SCANNED_HOST_PIXELS)
    opaque=alpha.point([255 if value>=254 else 0 for value in range(256)])
    padded=Image.new('L',(width+2,height+2));padded.paste(opaque,(1,1))
    ImageDraw.floodfill(padded,(0,0),128)
    inside=bytearray(padded.crop((1,1,width+1,height+1)).point([255 if value==0 else 0 for value in range(256)]).tobytes())
    labels=array('I',[0])*pixels;components=[];number=0
    for start in range(pixels):
        if inside[start]!=255:continue
        number+=1
        if number>MAX_COMPONENTS:raise _Declined('Resource budget exceeded: alpha components')
        todo=[start];inside[start]=0;labels[start]=number
        count=0;left=width;top=height;right=bottom=0
        while todo:
            point=todo.pop();x,y=point%width,point//width;count+=1
            if count>MAX_COMPONENT_PIXELS:raise _Declined('Resource budget exceeded: component pixels')
            left=min(left,x);right=max(right,x+1);top=min(top,y);bottom=max(bottom,y+1)
            neighbours=([point-1] if x else [])+([point+1] if x+1<width else [])+([point-width] if y else [])+([point+width] if y+1<height else [])
            for other in neighbours:
                if inside[other]==255:
                    inside[other]=0;labels[other]=number;todo.append(other)
        if count>=16:components.append((number,(left,top,right,bottom),count))
    result=[]
    for number,box,count in components:
        left,top,right,bottom=box;size=(right-left,bottom-top);area=size[0]*size[1]
        if area>MAX_ROI_PIXELS:raise _Declined('Resource budget exceeded: region pixels')
        budget.use('roi_pixels',area,MAX_TOTAL_ROI_PIXELS)
        budget.use('regions',1,MAX_REGIONS)
        data=bytes(255 if labels[y*width+x]==number else 0 for y in range(top,bottom) for x in range(left,right))
        result.append((box,Image.frombytes('L',size,data),count))
    return result


def _sum(image):
    return sum(value*count for value,count in enumerate(image.histogram()))


def select_pose(movie,tid,resolve_scope):
    """Choose one whole encoded frame, or explicitly retain frame 1.

    Only leaf sprite timelines without masks, actions or effects are considered.
    The score measures transparency left inside stable sprites' enclosed holes
    that moving artwork substantially fills. It includes antialiased borders;
    it is not a pixel-painting or image-completion operation.
    """
    budget=_Budget()
    def audit(frame=1,reason='',**extra):
        return {'version':POSE_SELECTION_VERSION,'frame':frame,'reason':reason,
                'applied':frame!=1,'coordinate_space':'stationary sprite source pixels',
                'resource_usage':dict(budget.counts),**extra}
    try:
        from .nested import compose
        from .render import sprite_for,sprite_bounds,warp_sprite
        timeline=movie.timelines[tid]
        if timeline.frame_count<2:return audit(reason='Single encoded frame')
        if timeline.frame_count>MAX_FRAMES:raise _Declined('Resource budget exceeded: encoded frames')
        if len(timeline.objects)>MAX_OBJECTS:raise _Declined('Resource budget exceeded: objects')
        if getattr(movie,'unsupported',[]):return audit(reason='Unsupported movie features')
        if timeline.actions:return audit(reason='Timeline actions require playback semantics')
        if timeline.masks or any(state.get('mask') is not None for values in timeline.frames.values() for state in values):
            return audit(reason='Masked timeline retains its initial pose')
        if any(definition.get('type')!=0 for definition in timeline.objects.values()):
            return audit(reason='Nonleaf or text timeline retains its initial pose')
        identity=(0,1,0,1,0,1,0)
        if any(state.get('effects') or (state.get('color') is not None and tuple(state['color'])!=identity)
               for values in timeline.frames.values() for state in values):
            return audit(reason='Filtered or tinted timeline retains its initial pose')
        states=[];active={};presence=Counter();signatures=defaultdict(set)
        for frame in range(1,timeline.frame_count+1):
            for state in timeline.frames.get(frame,[]):
                if state['alpha']==0:active.pop(state['object_id'],None)
                else:active[state['object_id']]=state
            budget.use('state_entries',len(active),MAX_STATE_ENTRIES)
            states.append(dict(active))
            for oid,state in active.items():
                presence[oid]+=1;signatures[oid].add((state['matrix'],state['alpha']))
        initially_named={oid:state['alpha'] for oid,state in states[0].items() if oid in timeline.names}
        candidates=[index for index,state in enumerate(states)
                    if len({s['depth'] for s in state.values()})==len(state)
                    and all(oid in state and state[oid]['alpha']>=alpha-1e-6 for oid,alpha in initially_named.items())]
        if 0 not in candidates:return audit(reason='Initial pose has ambiguous draw depth')
        stable={oid for oid in timeline.objects if presence[oid]==timeline.frame_count and len(signatures[oid])==1}
        dynamic=set(timeline.objects)-stable
        if not stable or not dynamic:return audit(reason='No stationary cutout and moving fill combination')
        scope=resolve_scope(tid);sprites={}
        def sprite(oid):
            if oid not in sprites:
                definition=timeline.objects[oid];element=scope['atlas']['elements'][definition['element']]
                width,height=element['size']
                if not all(math.isfinite(v) and v>0 for v in (width,height)):raise _Declined('Unsupported sprite dimensions')
                budget.use('sprite_pixels',math.ceil(width)*math.ceil(height),MAX_TEXTURE_PIXELS)
                tile,element,_=sprite_for(scope,oid);sprites[oid]=(tile,element)
            return sprites[oid]
        regions=[]
        for oid in sorted(stable):
            host=states[0][oid]
            if host['alpha']!=1:continue
            tile,element=sprite(oid);host_alpha=tile.getchannel('A')
            for box,mask,count in _holes(host_alpha,budget):
                transform=compose((1,0,0,1,-box[0],-box[1]),_inverse(_net(element,host)))
                regions.append((oid,box,mask,count,transform,host_alpha.crop(box)))
        if not regions:return audit(reason='No enclosed stationary sprite cutouts')
        warps={};region_scores=[[] for _ in regions];moving_fill=[0.0 for _ in regions]
        def warped(oid,state,transform,size):
            tile,element=sprite(oid);matrix=compose(transform,_net(element,state))
            bounds=sprite_bounds(tile,{'scale':1,'pivot':(0,0)},dict(state,matrix=matrix))
            if bounds[0]>=size[0] or bounds[1]>=size[1] or bounds[2]<=0 or bounds[3]<=0:return None
            key=(oid,matrix,state['alpha'],size)
            if key not in warps:
                budget.use('unique_roi_warps',1,MAX_WARPS)
                budget.use('cached_warp_pixels',size[0]*size[1],MAX_CACHED_WARP_PIXELS)
                warps[key]=warp_sprite(tile,{'scale':1,'pivot':(0,0)},dict(state,matrix=matrix),size).getchannel('A')
            return warps[key]
        # Alpha coverage is evaluated in each stationary sprite's coordinate
        # system. RGB depth and all other source state remain unchanged when
        # the caller later paints the selected whole frame.
        for state in states:
            for index,(host,box,mask,count,transform,host_alpha) in enumerate(regions):
                size=mask.size;area=size[0]*size[1]
                budget.use('score_pixel_work',area*5,MAX_SCORE_PIXEL_WORK)
                transmit=ImageChops.invert(host_alpha);moving_transmit=Image.new('L',size,255)
                for oid,part in sorted(state.items(),key=lambda entry:entry[1]['depth']):
                    if oid==host:continue
                    alpha=warped(oid,part,transform,size)
                    if alpha is None:continue
                    budget.use('score_pixel_work',area*(2 if oid in dynamic else 1),MAX_SCORE_PIXEL_WORK)
                    remaining=ImageChops.invert(alpha);transmit=ImageChops.multiply(transmit,remaining)
                    if oid in dynamic:moving_transmit=ImageChops.multiply(moving_transmit,remaining)
                region_scores[index].append(_sum(ImageChops.multiply(mask,transmit)))
                moving_fill[index]=max(moving_fill[index],_sum(ImageChops.multiply(mask,ImageChops.invert(moving_transmit)))/(count*255))
        eligible=[index for index in range(len(regions)) if moving_fill[index]>=.5
                  and max(region_scores[index])-min(region_scores[index])>=255]
        if not eligible:return audit(reason='No substantially moving fill with variable cutout coverage')
        scores=[sum(region_scores[index][frame] for index in eligible) for frame in range(len(states))]
        best=min(candidates,key=lambda frame:(scores[frame],frame))
        details={'initial_deficit':scores[0],'best_deficit':scores[best],
                 'relative_improvement':(scores[0]-scores[best])/max(1,scores[0]),
                 'encoded_frames_checked':len(states),'eligible_candidate_frames':len(candidates),
                 'regions':[{'stationary_object_id':regions[index][0],'source_box':list(regions[index][1]),
                             'pixels':regions[index][3],'maximum_moving_fill':moving_fill[index]} for index in eligible]}
        if scores[0]-scores[best]<255:return audit(reason='Initial pose is already sufficient; improvement below one alpha-pixel',**details)
        return audit(best+1,'Minimum enclosed-cutout alpha deficit in one whole encoded pose',**details)
    except _Declined as error:return audit(reason=str(error))
    except (ValueError,KeyError,TypeError,OverflowError) as error:
        # Future custom geometry must not make an otherwise valid initial
        # snapshot fail merely because this advisory analysis inspected it.
        return audit(reason='Pose analysis declined unsupported source data: '+type(error).__name__+': '+str(error))
