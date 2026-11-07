"""Deterministic short pointer placement with independently checked geometry.

Tips remain on the supplied visible anchors. The bounded search may reject an
impossible or unusually dense layout; it never moves a target or returns crossing
routes to make a scene appear complete. All geometry is in solution-canvas pixels.
"""
import math
import time

EPS=1e-7
BADGE_GAP=2.0
STROKE_CLEARANCE=6.0  # Two 4px outlines plus raster-rounding clearance.
BADGE_LINE_CLEARANCE=3.0
ARROW_LINE_CLEARANCE=4.0
ARROW_CLEARANCE=2.0
ARROW_LENGTH=7.0
ARROW_HALF_WIDTH=3.0
MIN_LENGTH=12.0
MAX_RADIUS=96.0


def _point(value):
    if not isinstance(value,(list,tuple)) or len(value)!=2:raise ValueError('Expected a two-dimensional pointer point')
    if not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in value):
        raise ValueError('Non-finite pointer coordinate')
    return tuple(float(v) for v in value)


def _box(value):
    if not isinstance(value,(list,tuple)) or len(value)!=4:raise ValueError('Expected a pointer rectangle')
    a=_point(value[:2]);b=_point(value[2:])
    if a[0]>=b[0] or a[1]>=b[1]:raise ValueError('Empty pointer rectangle')
    return (*a,*b)


def _inside(point,box):return box[0]-EPS<=point[0]<=box[2]+EPS and box[1]-EPS<=point[1]<=box[3]+EPS


def _expanded(box,pad):return (box[0]-pad,box[1]-pad,box[2]+pad,box[3]+pad)


def _boxes_overlap(a,b,gap=0):
    return not (a[2]+gap<b[0]-EPS or b[2]+gap<a[0]-EPS or a[3]+gap<b[1]-EPS or b[3]+gap<a[1]-EPS)


def _bounds(points):
    return (min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points))


def _cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _on_segment(point,a,b):
    return abs(_cross(a,b,point))<=EPS and min(a[0],b[0])-EPS<=point[0]<=max(a[0],b[0])+EPS and min(a[1],b[1])-EPS<=point[1]<=max(a[1],b[1])+EPS


def _intersects(a,b,c,d):
    ab_c,ab_d,cd_a,cd_b=_cross(a,b,c),_cross(a,b,d),_cross(c,d,a),_cross(c,d,b)
    if ((ab_c>EPS and ab_d<-EPS) or (ab_c<-EPS and ab_d>EPS)) and ((cd_a>EPS and cd_b<-EPS) or (cd_a<-EPS and cd_b>EPS)):return True
    return ((abs(ab_c)<=EPS and _on_segment(c,a,b)) or (abs(ab_d)<=EPS and _on_segment(d,a,b)) or
            (abs(cd_a)<=EPS and _on_segment(a,c,d)) or (abs(cd_b)<=EPS and _on_segment(b,c,d)))


def _point_segment_distance2(p,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1];denominator=dx*dx+dy*dy
    if denominator<=EPS:return (p[0]-a[0])**2+(p[1]-a[1])**2
    fraction=max(0,min(1,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/denominator))
    return (p[0]-a[0]-fraction*dx)**2+(p[1]-a[1]-fraction*dy)**2


def _segment_distance2(a,b,c,d):
    if _intersects(a,b,c,d):return 0.0
    return min(_point_segment_distance2(a,c,d),_point_segment_distance2(b,c,d),
               _point_segment_distance2(c,a,b),_point_segment_distance2(d,a,b))


def _segment_rect(a,b,box):
    if not _boxes_overlap(_bounds((a,b)),box):return False
    # Liang-Barsky clipping, including contact with the rectangle boundary.
    low,high=0.0,1.0;dx,dy=b[0]-a[0],b[1]-a[1]
    for p,q in ((-dx,a[0]-box[0]),(dx,box[2]-a[0]),(-dy,a[1]-box[1]),(dy,box[3]-a[1])):
        if abs(p)<=EPS:
            if q<-EPS:return False
        else:
            t=q/p
            if p<0:low=max(low,t)
            else:high=min(high,t)
            if low>high+EPS:return False
    return True


def _in_triangle(point,triangle):
    values=[_cross(triangle[i],triangle[(i+1)%3],point) for i in range(3)]
    return min(values)>=-EPS or max(values)<=EPS


def _triangle_rect(triangle,box):
    if not _boxes_overlap(_bounds(triangle),box):return False
    corners=((box[0],box[1]),(box[2],box[1]),(box[2],box[3]),(box[0],box[3]))
    return (any(_inside(p,box) for p in triangle) or any(_in_triangle(p,triangle) for p in corners) or
            any(_segment_rect(triangle[i],triangle[(i+1)%3],box) for i in range(3)))


def _triangle_segment(triangle,start,tip,clearance):
    if not _boxes_overlap(_expanded(_bounds(triangle),clearance),_bounds((start,tip))):return False
    if _in_triangle(start,triangle) or _in_triangle(tip,triangle):return True
    return any(_segment_distance2(triangle[i],triangle[(i+1)%3],start,tip)<=clearance**2+EPS for i in range(3))


def _triangles(a,b,clearance):
    if not _boxes_overlap(_expanded(_bounds(a),clearance),_bounds(b)):return False
    if _in_triangle(a[0],b) or _in_triangle(b[0],a):return True
    return any(_segment_distance2(a[i],a[(i+1)%3],b[j],b[(j+1)%3])<=clearance**2+EPS for i in range(3) for j in range(3))


def _arrow(start,tip):
    length=math.dist(start,tip)
    if length<=EPS:raise ValueError('Zero-length local pointer')
    ux,uy=(tip[0]-start[0])/length,(tip[1]-start[1])/length
    return (tip,(tip[0]-ARROW_LENGTH*ux+ARROW_HALF_WIDTH*uy,tip[1]-ARROW_LENGTH*uy-ARROW_HALF_WIDTH*ux),
            (tip[0]-ARROW_LENGTH*ux-ARROW_HALF_WIDTH*uy,tip[1]-ARROW_LENGTH*uy+ARROW_HALF_WIDTH*ux))


def _geometry(box,start,tip,arrow):
    return {'box':box,'start':start,'tip':tip,'arrow':arrow,
            'bounds':_expanded(_bounds(((box[0],box[1]),(box[2],box[3]),start,tip,*arrow)),STROKE_CLEARANCE)}


def _conflict(a,b):
    if not _boxes_overlap(a['bounds'],b['bounds']):return None
    if _boxes_overlap(a['box'],b['box'],BADGE_GAP):return 'number badges overlap'
    if _segment_rect(a['start'],a['tip'],_expanded(b['box'],BADGE_LINE_CLEARANCE)) or _segment_rect(b['start'],b['tip'],_expanded(a['box'],BADGE_LINE_CLEARANCE)):
        return 'pointer stroke intersects another number badge'
    if _triangle_rect(a['arrow'],_expanded(b['box'],1)) or _triangle_rect(b['arrow'],_expanded(a['box'],1)):
        return 'arrowhead intersects another number badge'
    if _segment_distance2(a['start'],a['tip'],b['start'],b['tip'])<=STROKE_CLEARANCE**2+EPS:return 'pointer strokes intersect or touch'
    if (_triangle_segment(a['arrow'],b['start'],b['tip'],ARROW_LINE_CLEARANCE) or
            _triangle_segment(b['arrow'],a['start'],a['tip'],ARROW_LINE_CLEARANCE)):return 'arrowhead intersects another pointer stroke'
    if _triangles(a['arrow'],b['arrow'],ARROW_CLEARANCE):return 'arrowheads intersect or touch'
    return None


def _edge_start(box,tip):
    cx,cy=(box[0]+box[2])/2,(box[1]+box[3])/2
    dx,dy=tip[0]-cx,tip[1]-cy
    factors=[(box[2]-box[0])/2/abs(dx) if abs(dx)>EPS else float('inf'),
             (box[3]-box[1])/2/abs(dy) if abs(dy)>EPS else float('inf')]
    factor=min(factors)
    return (cx+factor*dx,cy+factor*dy)


def _within_geometry(candidate,scene):
    box=candidate['box']
    return (_inside((box[0],box[1]),scene) and _inside((box[2],box[3]),scene) and
            all(_inside(p,scene) for p in candidate['arrow']))


def _specs(markers,scene_box):
    scene=_box(scene_box);viewport=(scene[0],scene[1],scene[2]-1,scene[3]-1)
    if viewport[2]<=viewport[0] or viewport[3]<=viewport[1]:raise ValueError('Scene is too small for numbered pointers')
    specs=[];numbers=set();ids=set()
    for marker in markers:
        oid=marker['object_id'];number=marker['number']
        if not isinstance(number,int) or isinstance(number,bool) or number<1 or number in numbers:raise ValueError('Pointer numbers must be unique positive integers')
        if oid in ids:raise ValueError(f'Duplicate pointer object ID {oid}')
        numbers.add(number);ids.add(oid)
        anchor=_point(marker['anchor']);tip=(anchor[0]+scene[0],anchor[1]+scene[1])
        if not _inside(tip,viewport):raise ValueError(f'Object {oid} anchor is outside the scene')
        width,height=_point(marker['badge_size'])
        if width<=0 or height<=0:raise ValueError(f'Object {oid} has an invalid badge size')
        specs.append({'marker':marker,'oid':oid,'number':number,'tip':tip,'size':(math.ceil(width),math.ceil(height))})
    for i,a in enumerate(specs):
        for b in specs[i+1:]:
            if math.dist(a['tip'],b['tip'])<=STROKE_CLEARANCE+EPS:
                raise ValueError(f'Cannot place nonintersecting pointers for objects {a["oid"]} and {b["oid"]}: coincident or too-close visible anchors')
    return specs,scene,viewport


def _candidates(spec,specs,viewport):
    tip=spec['tip'];width,height=spec['size'];seen=set();result=[]
    others=[s for s in specs if s is not spec]
    nearest=min(others,key=lambda s:(math.dist(tip,s['tip']),s['number'],s['oid'])) if others else None
    away=math.atan2(tip[1]-nearest['tip'][1],tip[0]-nearest['tip'][0]) if nearest else 0
    angles=sorted(range(16),key=lambda n:(abs(math.atan2(math.sin(n*math.pi/8-away),math.cos(n*math.pi/8-away))),n))
    for radius in (32,40,48,56,64,80,96):
        for n in angles:
            angle=n*math.pi/8;cx=tip[0]+radius*math.cos(angle);cy=tip[1]+radius*math.sin(angle)
            left=round(cx-width/2);top=round(cy-height/2);box=(left,top,left+width,top+height)
            if box in seen:continue
            seen.add(box)
            if any(_inside(other['tip'],_expanded(box,BADGE_LINE_CLEARANCE)) for other in others):continue
            if _inside(tip,box):continue
            start=_edge_start(box,tip);length=math.dist(start,tip)
            if length<MIN_LENGTH-EPS or length>MAX_RADIUS+EPS:continue
            candidate=_geometry(box,start,tip,_arrow(start,tip))
            if _within_geometry(candidate,viewport) and not _triangle_rect(candidate['arrow'],_expanded(box,1)):
                result.append(candidate)
    return result


def _public(candidate):
    start=list(candidate['start']);tip=list(candidate['tip'])
    return {'number_box':list(candidate['box']),'pointer_start':start,'pointer_tip':tip,
            'pointer_route':[start,tip],'pointer_arrow':[list(p) for p in candidate['arrow']],
            'pointer_length':math.dist(candidate['start'],candidate['tip'])}


def place_numbered_pointers(markers,scene_box):
    """Mutate/return markers with checked local badges; leave inputs on failure.

    Typical scenes use greedy placement. A deterministic bounded backtracking
    search handles denser arrangements, with a 1.4-second fallback budget.
    """
    specs,scene,viewport=_specs(markers,scene_box)
    if not specs:return markers
    choices=[_candidates(s,specs,viewport) for s in specs]
    missing=[s['oid'] for s,c in zip(specs,choices) if not c]
    if missing:raise ValueError(f'No local numbered-pointer space for objects {missing}')
    distance=lambda i:min((math.dist(specs[i]['tip'],s['tip']) for j,s in enumerate(specs) if j!=i),default=float('inf'))
    orders=[sorted(range(len(specs)),key=lambda i:(len(choices[i]),distance(i),specs[i]['number'])),
            sorted(range(len(specs)),key=lambda i:(distance(i),specs[i]['number'])),
            sorted(range(len(specs)),key=lambda i:(len(choices[i]),distance(i),-specs[i]['number']))]
    answer=None
    for order in orders:
        assigned={}
        for i in order:
            selected=next((candidate for candidate in choices[i] if all(_conflict(candidate,other) is None for other in assigned.values())),None)
            if selected is None:break
            assigned[i]=selected
        if len(assigned)==len(specs):answer=assigned;break
    if answer is None:
        deadline=time.monotonic()+1.4;states=[0]
        def search(domains,assigned):
            if not domains:return assigned
            if states[0]>=2500 or time.monotonic()>deadline:return None
            i=min(domains,key=lambda j:(len(domains[j]),specs[j]['number']))
            for candidate in domains[i]:
                states[0]+=1
                if states[0]>=2500 or time.monotonic()>deadline:return None
                rest={};possible=True
                for j,values in domains.items():
                    if j==i:continue
                    allowed=[value for value in values if _conflict(candidate,value) is None]
                    if not allowed:possible=False;break
                    rest[j]=allowed
                if possible:
                    result=search(rest,{**assigned,i:candidate})
                    if result is not None:return result
            return None
        answer=search({i:values for i,values in enumerate(choices)},{})
    if answer is None:raise ValueError('No nonintersecting local pointer layout found for objects '+', '.join(str(s['oid']) for s in specs))
    proposed=[{**s['marker'],**_public(answer[i])} for i,s in enumerate(specs)]
    issues=validate_pointer_layout(proposed,(math.ceil(scene[2])+1,math.ceil(scene[3])+1),scene)
    if issues:raise ValueError('Invalid numbered-pointer layout: '+'; '.join(issues))
    for i,spec in enumerate(specs):spec['marker'].update(_public(answer[i]))
    return markers


def validate_pointer_layout(markers,canvas_size,scene_box):
    """Rebuild public geometry and check every badge, stroke and arrowhead pair."""
    issues=[];candidates=[]
    try:
        scene=_box(scene_box);viewport=(scene[0],scene[1],scene[2]-1,scene[3]-1)
        width,height=_point(canvas_size)
        if scene[0]<0 or scene[1]<0 or scene[2]>width or scene[3]>height:issues.append('Scene box lies outside the solution canvas')
    except (ValueError,TypeError,KeyError) as exc:return [str(exc)]
    numbers=set();ids=set()
    for marker in markers:
        oid=marker.get('object_id','?')
        try:
            number=marker['number']
            if not isinstance(number,int) or isinstance(number,bool) or number<1 or number in numbers or oid in ids:raise ValueError('Duplicate or invalid object/number identity')
            numbers.add(number);ids.add(oid)
            box=_box(marker['number_box']);start=_point(marker['pointer_start']);tip=_point(marker['pointer_tip'])
            route=marker['pointer_route'];arrow=tuple(_point(p) for p in marker['pointer_arrow'])
            if len(arrow)!=3 or len(route)!=2 or _point(route[0])!=start or _point(route[1])!=tip:raise ValueError('Malformed pointer route or arrowhead')
            anchor=_point(marker['anchor']);expected=(anchor[0]+scene[0],anchor[1]+scene[1])
            if math.dist(tip,expected)>EPS:raise ValueError('Pointer tip moved away from the actual visible anchor')
            if not _inside(tip,viewport):raise ValueError('Pointer target lies outside the scene')
            length=math.dist(start,tip)
            if not MIN_LENGTH-EPS<=length<=MAX_RADIUS+EPS:raise ValueError('Pointer route is not short and local')
            boundary=(abs(start[0]-box[0])<=EPS or abs(start[0]-box[2])<=EPS or abs(start[1]-box[1])<=EPS or abs(start[1]-box[3])<=EPS)
            if not _inside(start,box) or not boundary or _inside(tip,box):raise ValueError('Pointer does not start at its number badge edge')
            expected_arrow=_arrow(start,tip)
            if any(math.dist(p,q)>EPS for p,q in zip(arrow,expected_arrow)):raise ValueError('Arrowhead does not follow its pointer route')
            if 'pointer_length' in marker and abs(marker['pointer_length']-length)>EPS:raise ValueError('Incorrect recorded pointer length')
            if 'badge_size' in marker:
                bw,bh=_point(marker['badge_size'])
                if abs(box[2]-box[0]-math.ceil(bw))>EPS or abs(box[3]-box[1]-math.ceil(bh))>EPS:raise ValueError('Number badge dimensions changed')
            candidate=_geometry(box,start,tip,arrow)
            if not _within_geometry(candidate,viewport):raise ValueError('Number badge or arrowhead lies outside the scene')
            if _triangle_rect(arrow,_expanded(box,1)):raise ValueError('Arrowhead overlaps its own number badge')
            candidates.append((oid,candidate))
        except (ValueError,TypeError,KeyError,OverflowError) as exc:issues.append(f'Object {oid}: {exc}')
    for i,(oid,a) in enumerate(candidates):
        for other,b in candidates[i+1:]:
            conflict=_conflict(a,b)
            if conflict:issues.append(f'Objects {oid} and {other}: {conflict}')
    return issues
