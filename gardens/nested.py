"""Initial-pose GAF nested timeline composition in native scene coordinates."""
from PIL import Image, ImageChops


def compose(parent,child):
    a,b,c,d,x,y=parent;e,f,g,h,u,v=child
    return (a*e+c*f,b*e+d*f,a*g+c*h,b*g+d*h,a*u+c*v+x,b*u+d*v+y)


class NestedPainter:
    def __init__(self,movie,resolve_scope,size):
        self.movie,self.resolve_scope,self.size=movie,resolve_scope,size
        self.audit=[];self.scopes={}

    def paint(self,tid,matrix=(1,0,0,1,0,0),alpha=1,masks=(),path=(),stack=()):
        from .render import RenderError, check_snapshot_actions, sprite_for, warp_sprite
        if tid in stack or len(stack)>=16:raise RenderError('Nested timeline cycle/depth limit')
        if tid not in self.movie.timelines:raise RenderError(f'Missing timeline {tid}')
        if tid not in self.scopes:self.scopes[tid]=self.resolve_scope(tid)
        scope=self.scopes[tid];timeline=scope['timeline'];state=timeline.state_at(1)
        check_snapshot_actions(timeline,1)
        key=list(path)
        if not any(x['instance_path']==key for x in self.audit):
            self.audit.append({'instance_path':key,'timeline_id':tid,'frame':1,'active_objects':len(state),
                               'matrix':list(matrix),'alpha':alpha,'atlas_ids':sorted(scope['textures'])})
        mask_layers={}
        for oid in timeline.masks:
            if oid not in state:continue
            s=state[oid]
            if s['mask'] is not None or s['effects']:raise RenderError('Unsupported mask effects/relationships')
            tile,element,_=sprite_for(scope,oid)
            world={**s,'matrix':compose(matrix,s['matrix'])}
            mask_layers[oid]=warp_sprite(tile,element,world,self.size).getchannel('A')
        image=Image.new('RGBA',self.size)
        objects=[(oid,s) for oid,s in state.items() if oid not in timeline.masks]
        if len({s['depth'] for oid,s in objects})!=len(objects):raise RenderError('Ambiguous nested depth')
        for oid,s in sorted(objects,key=lambda pair:pair[1]['depth']):
            definition=timeline.objects[oid];active_masks=list(masks)
            if s['effects']:raise RenderError(f'Unsupported nested effect on object {oid}')
            if s['mask'] is not None:
                if s['mask'] not in mask_layers:raise RenderError(f'Missing nested mask {s["mask"]}')
                active_masks.append(mask_layers[s['mask']])
            world_matrix=compose(matrix,s['matrix'])
            if definition['type']==2:
                if s['color']:raise RenderError('Nested parent color transforms are unsupported')
                layer=self.paint(definition['element'],world_matrix,alpha*s['alpha'],active_masks,path+ (oid,),stack+(tid,))
            elif definition['type']==0:
                tile,element,_=sprite_for(scope,oid)
                world={**s,'matrix':world_matrix,'alpha':alpha*s['alpha']}
                layer=warp_sprite(tile,element,world,self.size)
                for mask in active_masks:
                    layer.putalpha(ImageChops.multiply(layer.getchannel('A'),mask))
            else:raise RenderError('Nested text rendering is unsupported')
            image=Image.alpha_composite(image,layer)
        return image
