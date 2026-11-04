"""CPU implementation of the GAF Unity colour/blur/outer-glow path.

Reference: StarlingGAFPlayer GAFFilter's Unity programs: separable box taps in
atlas texels, Adjust Color, then Tint/object alpha. No Gaussian substitution.
Inner/knockout, drop shadows and timeline-level filters remain unsupported.
"""
import math
from PIL import Image,ImageChops,ImageMath


def supported(effects):
    return all(e['type']==6 or e['type']==1 or
               (e['type']==2 and not e['inner'] and not e['knockout']) for e in effects)


def color_matrix(tile,matrix):
    if len(matrix)!=20:raise ValueError('Invalid GAF colour matrix')
    channels=[ch.convert('F') for ch in tile.split()]
    result=[]
    for row in range(4):
        values=matrix[row*5:row*5+5]
        out=ImageMath.lambda_eval(lambda args: args['r']*values[0]+args['g']*values[1]+args['b']*values[2]+args['a']*values[3]+values[4],
                                  r=channels[0],g=channels[1],b=channels[2],a=channels[3])
        result.append(out.convert('L'))
    return Image.merge('RGBA',result)


def box_pass(channel,width,horizontal):
    if not math.isfinite(width) or not 0<=width<=256:raise ValueError('Unsafe GAF blur width')
    if width==0:return channel
    # Unity chooses 8-64 taps by width; the reference compatibility shader uses
    # 32 uniformly spaced bilinear taps. Accumulate in float, quantize per pass.
    source=channel.convert('F');total=Image.new('F',source.size)
    for i in range(32):
        offset=width*(i/31-.5)
        matrix=(1,0,offset if horizontal else 0,0,1,0 if horizontal else offset)
        sample=source.transform(source.size,Image.Transform.AFFINE,matrix,Image.Resampling.BILINEAR,fillcolor=0)
        total=ImageMath.lambda_eval(lambda a:a['total']+a['sample']/32,total=total,sample=sample)
    return total.convert('L')


def blur(channel,x,y):return box_pass(box_pass(channel,x,True),y,False)


def apply_effects(tile,effects,with_offset=False):
    if not supported(effects):raise ValueError('Unsupported GAF spatial filter combination')
    # Unity retains the last spatial effect and last Adjust Color, rather than
    # composing every intermediate filter record as a separate render pass.
    spatial=None;matrix=None;matrix_after=False
    for effect in effects:
        if effect['type']==6:matrix=effect['values'];matrix_after=spatial is not None
        else:spatial=effect
    offset=(0,0)
    if spatial:
        x,y=spatial['values'][:2]
        if not all(math.isfinite(v) and 0<=v<=256 for v in (x,y)):raise ValueError('Unsafe GAF blur width')
        px,py=math.ceil(x/2)+2,math.ceil(y/2)+2;offset=(px,py)
        padded=Image.new('RGBA',(tile.width+2*px,tile.height+2*py));padded.paste(tile,(px,py));tile=padded
        if spatial['type']==1:
            # Filter premultiplied channels so transparent RGB never bleeds.
            channels=tile.convert('RGBa').split()
            tile=Image.merge('RGBa',[blur(ch,x,y) for ch in channels]).convert('RGBA')
            if matrix:tile=color_matrix(tile,matrix)
        else:
            alpha=blur(tile.getchannel('A'),x,y)
            strength=spatial['values'][2]
            if not math.isfinite(strength) or not 0<=strength<=256:raise ValueError('Unsafe GAF glow strength')
            b,g,r,a=spatial['color'];color=Image.new('RGBA',(1,1),(r,g,b,a))
            if matrix and matrix_after:color=color_matrix(color,matrix)
            r,g,b,a=color.getpixel((0,0))
            alpha=alpha.point([round(min(255,v*strength)*a/255) for v in range(256)])
            if matrix:tile=color_matrix(tile,matrix)
            source_alpha=tile.getchannel('A')
            rgb=Image.composite(tile.convert('RGB'),Image.new('RGB',tile.size,(r,g,b)),source_alpha)
            tile=rgb.convert('RGBA');tile.putalpha(ImageChops.screen(source_alpha,alpha))
    elif matrix:tile=color_matrix(tile,matrix)
    return (tile,offset) if with_offset else tile
