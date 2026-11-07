"""Readable named callouts with arrow tips on actual visible object pixels."""
import math

from PIL import Image, ImageDraw, ImageFont


def visible_anchor(mask):
    """Nearest visible pixel to the visible bounding-box centre (never a hole)."""
    bbox=mask.getbbox()
    if bbox is None: raise ValueError('Cannot point at an invisible object')
    crop=mask.crop(bbox);w,h=crop.size
    choices=(( ((2*(i%w)-(w-1))**2+(2*(i//w)-(h-1))**2), i)
             for i,value in enumerate(crop.tobytes()) if value)
    _,index=min(choices)
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
    """Keep scene pixels at native scale; put nonoverlapping labels in side rails.

    Arrow/box coordinates are recorded in solution-canvas pixels; `anchor` stays
    in original scene coordinates. The full label is retained even when its
    display text wraps. Only verified clue labels receive callouts.
    """
    if any(marker.get('label_verified',True) is False for marker in markers):
        raise ValueError('Solution callouts require verified clue labels')
    font=ImageFont.load_default(size=16)
    padding,gap,rail,gutter=16,4,240,20
    row_line=21
    sides={'left':[],'right':[]}
    numbered=sorted(markers,key=lambda m:(m['label'].casefold(),m['object_id']))
    for number,marker in enumerate(numbered,1):
        marker['number']=number
        marker['label_lines']=wrap_label(f'{number}. {marker["label"]}',font,rail-20)
        marker['callout_height']=max(26,len(marker['label_lines'])*row_line+10)
        sides['left' if marker['anchor'][0]<scene.width/2 else 'right'].append(marker)
    def rail_height(rows):return sum(m['callout_height'] for m in rows)+gap*max(0,len(rows)-1)
    height=max(scene.height+2*padding,max(map(rail_height,sides.values()),default=0)+2*padding)
    offset=[padding+rail+gutter,(height-scene.height)//2]
    width=scene.width+2*(padding+rail+gutter)
    result=Image.new('RGBA',(width,height),(19,25,33,255))
    result.alpha_composite(scene,tuple(offset))
    draw=ImageDraw.Draw(result)
    cyan=(79,225,255,255);dark=(5,13,20,255)
    for side,rows in sides.items():
        rows.sort(key=lambda m:(m['anchor'][1],m['anchor'][0],m['object_id']))
        tops=[];minimum=padding
        for marker in rows:
            h=marker['callout_height']
            top=max(minimum,marker['anchor'][1]+offset[1]-h//2)
            tops.append(top);minimum=top+h+gap
        maximum=height-padding
        for i in range(len(rows)-1,-1,-1):
            tops[i]=min(tops[i],maximum-rows[i]['callout_height'])
            maximum=tops[i]-gap
        for marker,top in zip(rows,tops):
            color=cyan
            h=marker['callout_height']
            left=padding if side=='left' else width-padding-rail
            box=[left,top,left+rail,top+h]
            start=[box[2] if side=='left' else box[0],top+h//2]
            tip=[marker['anchor'][0]+offset[0],marker['anchor'][1]+offset[1]]
            # A straight leader has a single unambiguous arrowhead at the object.
            draw.line([tuple(start),tuple(tip)],fill=dark,width=4)
            draw.line([tuple(start),tuple(tip)],fill=color,width=2)
            dx,dy=tip[0]-start[0],tip[1]-start[1];length=math.hypot(dx,dy)
            if length==0:raise ValueError('Degenerate callout pointer')
            ux,uy=dx/length,dy/length
            arrow=[tuple(tip),(tip[0]-8*ux+4*uy,tip[1]-8*uy-4*ux),
                   (tip[0]-8*ux-4*uy,tip[1]-8*uy+4*ux)]
            draw.polygon(arrow,fill=color,outline=dark)
            draw.rounded_rectangle(box,radius=4,fill=(27,37,49,255),outline=color,width=1)
            for i,line in enumerate(marker['label_lines']):
                draw.text((left+10,top+5+i*row_line),line,font=font,fill=(255,255,255,255))
            marker.update(callout_box=box,pointer_start=start,pointer_tip=tip,callout_side=side,callout_color=list(color))
    return result,[*offset,offset[0]+scene.width,offset[1]+scene.height]


def validate_callouts(markers,canvas_size,scene_box):
    issues=[];boxes=[]
    for marker in markers:
        oid=marker['object_id'];box=marker['callout_box'];anchor=marker['anchor']
        tip=marker['pointer_tip'];bbox=marker['bbox']
        if not marker.get('label'):issues.append(f'Callout {oid} has no label')
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
    return issues
