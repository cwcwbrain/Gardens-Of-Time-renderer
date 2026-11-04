"""Initial-pose GAF nested timeline composition in native scene coordinates."""
from PIL import Image, ImageChops
from .effects import supported


def compose(parent,child):
    a,b,c,d,x,y=parent;e,f,g,h,u,v=child
    return (a*e+c*f,b*e+d*f,a*g+c*h,b*g+d*h,a*u+c*v+x,b*u+d*v+y)


def cascade_color(parent,child):
    """GAF cascades channel multipliers by product and offsets by sum.

    Matches Cocos GAFObject._applyStateSuper; alpha multipliers travel separately.
    """
    p=parent or (0,1,0,1,0,1,0);c=child or (0,1,0,1,0,1,0)
    return (p[0]+c[0],p[1]*c[1],p[2]+c[2],p[3]*c[3],p[4]+c[4],p[5]*c[5],p[6]+c[6])


class NestedPainter:
    def __init__(self,movie,resolve_scope,size):
        self.movie,self.resolve_scope,self.size=movie,resolve_scope,size
        self.audit=[];self.scopes={}

    def paint(self,tid,matrix=(1,0,0,1,0,0),alpha=1,masks=(),path=(),stack=(),color=None,effects=()):
        from .render import RenderError, check_snapshot_actions, sprite_for, warp_sprite
        if not supported(effects):raise RenderError('Unsupported inherited GAF filter')
        if tid in stack or len(stack)>=16:raise RenderError('Nested timeline cycle/depth limit')
        if tid not in self.movie.timelines:raise RenderError(f'Missing timeline {tid}')
        if tid not in self.scopes:self.scopes[tid]=self.resolve_scope(tid)
        scope=self.scopes[tid];timeline=scope['timeline'];state=timeline.state_at(1)
        check_snapshot_actions(timeline,1)
        key=list(path)
        if not any(x['instance_path']==key for x in self.audit):
            self.audit.append({'instance_path':key,'timeline_id':tid,'frame':1,'active_objects':len(state),
                               'matrix':list(matrix),'alpha':alpha,'atlas_ids':sorted(scope['textures']),
                               'scope_source':scope.get('scope_source','serialized_unity'),
                               'equivalent_clip_instances':scope.get('equivalent_clip_instances',1),
                               'inherited_effects':list(effects)})
        mask_layers={}
        for oid in timeline.masks:
            if oid not in state:continue
            s=state[oid]
            if not supported(s['effects']):raise RenderError('Unsupported mask spatial effects')
            tile,element,_=sprite_for(scope,oid)
            world={**s,'matrix':compose(matrix,s['matrix']),'effects':list(s['effects'])+list(effects)}
            mask_layers[oid]=warp_sprite(tile,element,world,self.size).getchannel('A')
        def resolve_mask(oid,stack=()):
            if oid in stack:raise RenderError('Nested mask relationship cycle')
            if oid not in mask_layers:raise RenderError(f'Missing nested mask {oid}')
            parent=state[oid]['mask']
            return mask_layers[oid] if parent is None else ImageChops.multiply(mask_layers[oid],resolve_mask(parent,stack+(oid,)))
        mask_layers={oid:resolve_mask(oid) for oid in mask_layers}
        image=Image.new('RGBA',self.size)
        objects=[(oid,s) for oid,s in state.items() if oid not in timeline.masks]
        if len({s['depth'] for oid,s in objects})!=len(objects):raise RenderError('Ambiguous nested depth')
        for oid,s in sorted(objects,key=lambda pair:pair[1]['depth']):
            definition=timeline.objects[oid];active_masks=list(masks)
            if not supported(s['effects']):raise RenderError(f'Unsupported nested spatial/timeline effect on object {oid}')
            if s['mask'] is not None:
                if s['mask'] not in mask_layers:raise RenderError(f'Missing nested mask {s["mask"]}')
                active_masks.append(mask_layers[s['mask']])
            world_matrix=compose(matrix,s['matrix'])
            if definition['type']==2:
                layer=self.paint(definition['element'],world_matrix,alpha*s['alpha'],active_masks,path+ (oid,),stack+(tid,),cascade_color(color,s['color']),tuple(s['effects'])+tuple(effects))
            elif definition['type']==0:
                tile,element,_=sprite_for(scope,oid)
                world={**s,'matrix':world_matrix,'alpha':alpha*s['alpha'],'color':cascade_color(color,s['color']),'effects':list(s['effects'])+list(effects)}
                layer=warp_sprite(tile,element,world,self.size)
                for mask in active_masks:
                    layer.putalpha(ImageChops.multiply(layer.getchannel('A'),mask))
            else:raise RenderError('Nested text rendering is unsupported')
            image=Image.alpha_composite(image,layer)
        return image
