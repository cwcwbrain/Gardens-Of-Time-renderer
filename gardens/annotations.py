"""Numbered local pointers and a separate object-name index."""
import math

from PIL import Image, ImageDraw, ImageFont

from .pointer_layout import place_numbered_pointers, validate_pointer_layout

SOLUTION_LAYOUT='numbered_local_pointers_v1'


def visible_anchor(mask,avoid=(),minimum_distance=10):
    """Nearest visible pixel to the visible bounding-box centre (never a hole)."""
    bbox=mask.getbbox()
    if bbox is None: raise ValueError('Cannot point at an invisible object')
    crop=mask.crop(bbox);w,h=crop.size
    choices=(( ((2*(i%w)-(w-1))**2+(2*(i//w)-(h-1))**2), i)
             for i,value in enumerate(crop.tobytes()) if value)
    _,index=min(choices)
    anchor=[bbox[0]+index%w,bbox[1]+index//w]
    if not avoid or all(math.dist(anchor,point)>=minimum_distance for point in avoid):return anchor
    pixels=crop.tobytes()
    alternatives=(( ((2*(i%w)-(w-1))**2+(2*(i//w)-(h-1))**2),i)
                  for i,value in enumerate(pixels) if value and
                  all(math.dist((bbox[0]+i%w,bbox[1]+i//w),point)>=minimum_distance for point in avoid))
    try:_,index=min(alternatives)
    except ValueError as exc:raise ValueError('No distinct visible pixel is available for a numbered pointer') from exc
    return [bbox[0]+index%w,bbox[1]+index//w]


def wrap_label(text,font,width):
    """Wrap without abbreviating, renaming, or discarding any label words."""
    if not text: raise ValueError('Missing label for callout')
    lines=[];line=''
    for word in text.split(' '):
        candidate=(line+' '+word) if line else word
        if font.getlength(candidate)<=width:
            line=candidate;continue
        if line:lines.append(line);line=''
        for char in word:
            if line and font.getlength(line+char)>width:
                lines.append(line);line=''
            line+=char
    if line:lines.append(line)
    return lines


def annotate_solution(scene,markers):
    """Keep native scene scale, local numbered pointers and a right-side index.

    Layout geometry uses solution-canvas pixels; `anchor` remains in original
    scene coordinates. Full Item Manager labels are retained in index order.
    """
    if any(marker.get('label_verified',True) is False for marker in markers):
        raise ValueError('Solution callouts require verified clue labels')
    font=ImageFont.load_default(size=16)
    heading_font=ImageFont.load_default(size=20)
    padding,gap,column_width,gutter,column_gap=16,4,240,24,12
    row_line,heading_height=21,36
    numbered=sorted(markers,key=lambda m:(m['label'].casefold(),m['object_id']))
    for number,marker in enumerate(numbered,1):
        marker['number']=number
        marker['solution_layout']=SOLUTION_LAYOUT
        marker['label_lines']=wrap_label(f'{number}. {marker["label"]}',font,column_width-20)
        marker['callout_height']=max(26,len(marker['label_lines'])*row_line+10)
        marker['badge_size']=[max(24,math.ceil(font.getlength(str(number)))+12),26]
    def rail_height(rows):return sum(m['callout_height'] for m in rows)+gap*max(0,len(rows)-1)
    # Contiguous columns preserve numeric reading order. Balance by wrapped
    # row height so long labels do not make one column unnecessarily tall.
    columns=[numbered]
    if len(numbered)>1 and rail_height(numbered)+heading_height>scene.height:
        split=min(range(1,len(numbered)),
                  key=lambda i:(max(rail_height(numbered[:i]),rail_height(numbered[i:])),i))
        columns=[numbered[:split],numbered[split:]]
    height=max(scene.height+2*padding,heading_height+max(map(rail_height,columns),default=0)+2*padding)
    offset=[padding,(height-scene.height)//2]
    width=scene.width+2*padding+gutter+len(columns)*column_width+(len(columns)-1)*column_gap
    scene_box=[*offset,offset[0]+scene.width,offset[1]+scene.height]
    place_numbered_pointers(numbered,scene_box)
    result=Image.new('RGBA',(width,height),(19,25,33,255))
    result.alpha_composite(scene,tuple(offset))
    draw=ImageDraw.Draw(result)
    cyan=(79,225,255,255);dark=(5,13,20,255)
    # Draw all leaders before badges. The geometric layout rejects crossings,
    # badge overlaps and leaders passing through another object's badge.
    for marker in numbered:
        route=[tuple(point) for point in marker['pointer_route']]
        draw.line(route,fill=dark,width=4)
        draw.line(route,fill=cyan,width=2)
        draw.polygon([tuple(point) for point in marker['pointer_arrow']],fill=cyan,outline=dark)
    for marker in numbered:
        box=marker['number_box']
        draw.rectangle(box,fill=(27,37,49,255),outline=cyan,width=2)
        draw.text(((box[0]+box[2])/2,(box[1]+box[3])/2),str(marker['number']),
                  font=font,fill=(255,255,255,255),anchor='mm')
        marker['callout_color']=list(cyan)
    index_left=offset[0]+scene.width+gutter
    draw.text((index_left,padding),'Object index',font=heading_font,fill=(255,255,255,255))
    for column,rows in enumerate(columns):
        left=index_left+column*(column_width+column_gap)
        top=padding+heading_height
        for marker in rows:
            h=marker['callout_height']
            box=[left,top,left+column_width,top+h]
            draw.rounded_rectangle(box,radius=3,fill=(27,37,49,255),outline=(56,77,94,255),width=1)
            for i,line in enumerate(marker['label_lines']):
                draw.text((left+10,top+5+i*row_line),line,font=font,fill=(255,255,255,255))
            marker.update(callout_box=box,callout_side='index',index_column=column)
            top+=h+gap
    return result,scene_box


def validate_callouts(markers,canvas_size,scene_box):
    issues=[];boxes=[]
    if sorted(marker.get('number',0) for marker in markers)!=list(range(1,len(markers)+1)):
        issues.append('Object index numbers are missing or duplicated')
    if len({marker['object_id'] for marker in markers})!=len(markers):
        issues.append('Object index contains duplicate object IDs')
    for marker in markers:
        oid=marker['object_id'];box=marker['callout_box'];anchor=marker['anchor']
        tip=marker['pointer_tip'];bbox=marker['bbox']
        if not marker.get('label'):issues.append(f'Callout {oid} has no label')
        if marker.get('label_verified',True) is False:issues.append(f'Callout {oid} is not a verified clue')
        if not (bbox[0]<=anchor[0]<bbox[2] and bbox[1]<=anchor[1]<bbox[3]) or marker.get('anchor_alpha',0)<16:
            issues.append(f'Callout {oid} does not target a visible object pixel')
        if tip!=[anchor[0]+scene_box[0],anchor[1]+scene_box[1]]:
            issues.append(f'Callout {oid} pointer is disconnected from its object')
        if not (0<=box[0]<box[2]<canvas_size[0] and 0<=box[1]<box[3]<canvas_size[1]):
            issues.append(f'Callout {oid} is outside the solution canvas')
        for other in boxes:
            if max(box[0],other[0])<min(box[2],other[2]) and max(box[1],other[1])<min(box[3],other[3]):
                issues.append(f'Callout {oid} overlaps another label')
        boxes.append(box)
    issues.extend(validate_pointer_layout(markers,canvas_size,scene_box))
    return issues
