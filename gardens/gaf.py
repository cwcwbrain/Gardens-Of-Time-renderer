"""Bounded GAF tag/timeline decoder (tested with the supplied CAG 5.9 assets).

Format reference: Cocos2d GAF GAFTags/GAFDataReader, not placement byte scanning.
Unsupported tags/effects are retained as limitations, never silently rendered.
"""
import math
import struct
import zlib
from dataclasses import dataclass, field


class GAFError(ValueError):
    pass


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise GAFError(f'Truncated GAF field at byte {self.pos}')
        value = self.data[self.pos:self.pos+n]
        self.pos += n
        return value

    def unpack(self, fmt):
        return struct.unpack('<'+fmt, self.take(struct.calcsize('<'+fmt)))

    def u8(self): return self.unpack('B')[0]
    def u16(self): return self.unpack('H')[0]
    def u32(self): return self.unpack('I')[0]
    def boolean(self):
        value = self.u8()
        if value not in (0, 1): raise GAFError('Invalid GAF boolean')
        return bool(value)
    def floats(self, n):
        values = self.unpack('f'*n)
        if not all(math.isfinite(v) for v in values): raise GAFError('Non-finite GAF float')
        return values
    def string(self): return self.take(self.u16()).decode('utf-8')
    def count(self, minimum=1):
        n = self.u32()
        if n > (len(self.data)-self.pos)//minimum: raise GAFError('Invalid GAF count')
        return n
    def end(self):
        if self.pos != len(self.data): raise GAFError(f'Unconsumed GAF bytes: {len(self.data)-self.pos}')


@dataclass
class Timeline:
    id: int
    frame_count: int
    bounds: tuple
    pivot: tuple
    linkage: str
    objects: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)
    masks: set = field(default_factory=set)
    frames: dict = field(default_factory=dict)
    actions: dict = field(default_factory=dict)
    tags: list = field(default_factory=list)

    def state_at(self, frame=1):
        if not 1 <= frame <= self.frame_count: raise GAFError('Frame outside timeline')
        state = {}
        for n in sorted(self.frames):
            if n > frame: break
            for change in self.frames[n]:
                if change['alpha'] == 0:
                    state.pop(change['object_id'], None)
                else:
                    state[change['object_id']] = change
        return state


@dataclass
class Movie:
    version: tuple
    scales: tuple
    csfs: tuple
    stage: dict = field(default_factory=dict)
    atlases: list = field(default_factory=list)
    timelines: dict = field(default_factory=dict)
    unsupported: list = field(default_factory=list)


def read_effect(r):
    typ = r.u32()
    if typ == 0:
        return {'type': typ, 'color': list(r.take(4)), 'values': r.floats(5), 'inner': r.boolean(), 'knockout': r.boolean()}
    if typ == 1: return {'type': typ, 'values': r.floats(2)}
    if typ == 2:
        return {'type': typ, 'color': list(r.take(4)), 'values': r.floats(3), 'inner': r.boolean(), 'knockout': r.boolean()}
    if typ == 6: return {'type': typ, 'values': r.floats(20)}
    raise GAFError(f'Unsupported GAF filter type {typ}')


def read_state(r):
    color, mask, effect = r.boolean(), r.boolean(), r.boolean()
    oid, depth = r.unpack('Ii')
    alpha, a, b, c, d, x, y = r.floats(7)
    state = dict(object_id=oid, depth=depth, alpha=alpha, matrix=(a,b,c,d,x,y),
                 color=r.floats(7) if color else None,
                 effects=[read_effect(r) for _ in range(r.u8())] if effect else [],
                 mask=r.u32() if mask else None)
    return state


def read_tags(r, movie, timeline=None):
    ended = False
    while r.pos < len(r.data):
        tag, size = r.unpack('HI')
        payload = Reader(r.take(size))
        if timeline: timeline.tags.append({'tag': tag, 'size': size})
        if tag == 0:
            payload.end(); ended = True; break
        elif tag == 9:
            fps = payload.u8(); color = list(payload.take(4)); width, height = payload.unpack('HH')
            movie.stage = dict(fps=fps, bgra=color, width=width, height=height)
        elif tag == 13:
            tid, frames = payload.unpack('II'); bounds = payload.floats(4); pivot = payload.floats(2)
            linkage = payload.string() if payload.boolean() else ''
            if tid in movie.timelines: raise GAFError('Duplicate GAF timeline')
            child = Timeline(tid, frames, bounds, pivot, linkage)
            movie.timelines[tid] = child
            read_tags(payload, movie, child)
        elif tag in (1,8,15):
            scale = payload.floats(1)[0]; atlases = {}
            for _ in range(payload.u8()):
                aid = payload.u32()
                atlases[aid] = [{'source': payload.string(), 'csf': payload.floats(1)[0]} for _ in range(payload.u8())]
            elements = {}
            for _ in range(payload.count(36 if tag==1 else (44 if tag==15 else 37))):
                pivot, origin = payload.floats(2), payload.floats(2)
                es = payload.floats(1)[0] if tag!=15 else None; size_xy = payload.floats(2)
                aid, eid = payload.unpack('II')
                grid = payload.floats(4) if tag in (8,15) and payload.boolean() else None
                sx,sy = payload.floats(2) if tag==15 else (es,es)
                rotated = payload.boolean() if tag==15 else False
                linkage = payload.string() if tag==15 else ''
                if eid in elements: raise GAFError('Duplicate atlas element')
                elements[eid] = dict(pivot=pivot, origin=origin, scale=sx, scale_y=sy, size=size_xy, atlas_id=aid, scale9=grid, rotated=rotated, linkage=linkage)
            movie.atlases.append(dict(timeline=timeline.id if timeline else None, scale=scale, sources=atlases, elements=elements))
        elif tag in (2,3,10,11):
            if timeline is None: raise GAFError('Objects outside timeline')
            for _ in range(payload.count(10 if tag in (10,11) else 8)):
                oid, eid = payload.unpack('II'); typ = payload.u16() if tag in (10,11) else 0
                if oid in timeline.objects: raise GAFError('Duplicate timeline object')
                timeline.objects[oid] = {'element': eid, 'type': typ}
                if tag in (2,11): timeline.masks.add(oid)
        elif tag == 5:
            if timeline is None: raise GAFError('Names outside timeline')
            for _ in range(payload.count(6)):
                oid = payload.u32(); name = payload.string()
                if oid in timeline.names: raise GAFError('Duplicate GAF named object')
                timeline.names[oid] = name
        elif tag in (4,12):
            if timeline is None: raise GAFError('Frames outside timeline')
            for _ in range(payload.count(6 if tag == 12 else 8)):
                frame = payload.u32()
                if not 1 <= frame <= timeline.frame_count or frame in timeline.frames:
                    raise GAFError('Invalid or duplicate GAF frame')
                changes, actions = (payload.boolean(), payload.boolean()) if tag == 12 else (True,False)
                states = [read_state(payload) for _ in range(payload.count(39))] if changes else []
                if len({s['object_id'] for s in states}) != len(states): raise GAFError('Duplicate frame state')
                if any(s['object_id'] not in timeline.objects for s in states): raise GAFError('Unknown object in frame')
                timeline.frames[frame] = states
                if actions:
                    records = []
                    for _ in range(payload.count(10)):
                        typ = payload.u32(); scope = payload.string(); arguments = Reader(payload.take(payload.u32()))
                        params = []
                        while arguments.pos < len(arguments.data): params.append(arguments.string())
                        records.append(dict(type=typ, scope=scope, params=params))
                    timeline.actions[frame] = records
        elif tag == 6:
            for _ in range(payload.count(6)):
                payload.string(); payload.unpack('HH')
        else:
            movie.unsupported.append(dict(timeline=timeline.id if timeline else None, tag=tag, size=size))
            payload.take(size)
        payload.end()
    if not ended: raise GAFError('Missing GAF end tag')
    r.end()


def decode(data):
    r = Reader(data)
    signature = r.take(4); version = (r.u8(), r.u8()); length = r.u32()
    if signature not in (b'CAG\0', b'GAF\0') or version[0] not in (4,5):
        raise GAFError(f'Unsupported GAF signature/version {signature!r} {version}')
    body = r.take(len(data)-r.pos)
    if signature == b'CAG\0':
        decompressor = zlib.decompressobj()
        try:
            raw = decompressor.decompress(body) + decompressor.flush()
        except zlib.error as exc:
            raise GAFError(f'Corrupt compressed GAF payload: {exc}') from exc
        if not decompressor.eof or decompressor.unused_data: raise GAFError('Invalid compressed GAF payload')
    else: raw = body
    # Unity's embedded GAF stores payload length; standalone exporters may
    # include the 10-byte header. Both must match exactly, never ignore it.
    if length not in (len(raw), len(raw)+10): raise GAFError('GAF uncompressed length mismatch')
    r = Reader(raw)
    scales = r.floats(r.count(4)); csfs = r.floats(r.count(4))
    movie = Movie(version, scales, csfs)
    read_tags(r, movie)
    return movie
