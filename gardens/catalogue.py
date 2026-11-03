"""Strict Unity Addressables JSON catalogue decoder.

Packed layout follows Unity Addressables 1.19.19 ContentCatalogData.CreateLocator
and SerializationUtilities.ReadObjectFromByteArray. Unknown layouts fail closed.
No catalogue filenames, runtime properties, or download URLs are guessed.
"""
import base64
import json
import re
import struct
from dataclasses import asdict, dataclass, field
from urllib.parse import urljoin, urlsplit


class CatalogueError(ValueError):
    pass


class Reader:
    def __init__(self, data, offset=0):
        self.data, self.pos = data, offset

    def take(self, n):
        if n < 0 or self.pos < 0 or self.pos + n > len(self.data):
            raise CatalogueError('Truncated or invalid packed catalogue offset')
        out = self.data[self.pos:self.pos+n]
        self.pos += n
        return out

    def integer(self):
        return struct.unpack('<i', self.take(4))[0]

    def count(self, minimum_bytes=1):
        n = self.integer()
        if n < 0 or n > (len(self.data) - self.pos) // minimum_bytes:
            raise CatalogueError('Invalid packed catalogue count')
        return n


def read_object(data, offset):
    r = Reader(data, offset)
    typ = r.take(1)[0]
    if typ in (0, 1):
        return r.take(r.integer()).decode('ascii' if typ == 0 else 'utf-16-le')
    if typ in (2, 3, 4):
        fmt = {2: '<H', 3: '<I', 4: '<i'}[typ]
        return struct.unpack(fmt, r.take(struct.calcsize(fmt)))[0]
    if typ == 5:
        return {'$unity_hash128': r.take(r.take(1)[0]).decode('ascii')}
    if typ == 7:
        assembly = r.take(r.take(1)[0]).decode('ascii')
        class_name = r.take(r.take(1)[0]).decode('ascii')
        value = json.loads(r.take(r.integer()).decode('utf-16-le'))
        return {'assembly': assembly, 'class': class_name, 'value': value}
    raise CatalogueError(f'Unsupported packed object type {typ}')


def token(key):
    # Unity keys of different primitive types must not collide.
    return json.dumps([type(key).__name__, key], sort_keys=True)


@dataclass
class Location:
    id: str
    catalogue: str
    internal_id: str
    provider: str
    primary_key: object
    keys: list
    dependencies: list
    options: dict = field(default_factory=dict)
    resource_type: object = None
    url: str | None = None
    url_error: str = ''

    @property
    def is_bundle(self):
        return ('AssetBundleProvider' in self.provider or
                urlsplit(self.internal_id).path.lower().endswith(('.bundle', '.unity3d', '.assetbundle')))

    def to_dict(self):
        return {**asdict(self), 'is_bundle': self.is_bundle}


def resolve_url(internal_id, catalogue_url, properties):
    def replace(match):
        name = match.group(1)
        if name not in properties:
            raise CatalogueError(f'Unresolved runtime property {{{name}}}')
        return properties[name]
    value = re.sub(r'\{([^{}]+)\}', replace, internal_id)
    if '{' in value or '}' in value:
        raise CatalogueError('Malformed runtime property')
    parsed = urlsplit(value)
    if parsed.scheme and parsed.scheme not in ('http', 'https'):
        raise CatalogueError(f'Non-HTTP asset location: {value}')
    if not parsed.scheme and (value.startswith('/') or re.match(r'^[A-Za-z]:', value)):
        raise CatalogueError(f'Local asset location needs an explicit runtime mapping: {value}')
    resolved = urljoin(catalogue_url, value)
    if urlsplit(resolved).scheme not in ('http', 'https') or not urlsplit(resolved).netloc:
        raise CatalogueError(f'Unresolvable asset URL: {value}')
    return resolved


def parse_catalogue(raw, source, properties=None):
    try:
        doc = json.loads(raw)
        required = ('m_InternalIds', 'm_ProviderIds', 'm_KeyDataString',
                    'm_BucketDataString', 'm_EntryDataString', 'm_ExtraDataString')
        if not isinstance(doc, dict) or not all(k in doc for k in required):
            raise CatalogueError('Unsupported catalogue format; expected packed Addressables JSON')
        decode = lambda name: base64.b64decode(doc[name], validate=True)
        bucket_data = Reader(decode('m_BucketDataString'))
        keys_data = decode('m_KeyDataString')
        entries_data = Reader(decode('m_EntryDataString'))
        extra = decode('m_ExtraDataString')
        bucket_count = bucket_data.count(8)
        buckets, keys = [], []
        for _ in range(bucket_count):
            offset = bucket_data.integer()
            count = bucket_data.count(4)
            buckets.append([bucket_data.integer() for _ in range(count)])
            keys.append(read_object(keys_data, offset))
        if Reader(keys_data).integer() != bucket_count or bucket_data.pos != len(bucket_data.data):
            raise CatalogueError('Key/bucket count or length mismatch')
        n = entries_data.count(28)
        if len(entries_data.data) != 4 + n * 28:
            raise CatalogueError('Unexpected entry size/version')
        entry_keys = [[] for _ in range(n)]
        for key, indices in zip(keys, buckets):
            for idx in indices:
                if not 0 <= idx < n:
                    raise CatalogueError('Bucket references missing entry')
                entry_keys[idx].append(key)
        def at(values, idx):
            if not 0 <= idx < len(values):
                raise CatalogueError(f'Catalogue index out of bounds: {idx}')
            return values[idx]
        locations = []
        for i in range(n):
            internal, provider, dep, _dep_hash, extra_idx, primary, resource = [entries_data.integer() for _ in range(7)]
            if dep < -1 or extra_idx < -1:
                raise CatalogueError('Invalid negative entry index')
            value = at(doc['m_InternalIds'], internal)
            prefixes = doc.get('m_InternalIdPrefixes') or []
            if prefixes and '#' in value:
                prefix, tail = value.rsplit('#', 1)
                if prefix.isdecimal():
                    value = at(prefixes, int(prefix)) + tail
            options = read_object(extra, extra_idx) if extra_idx >= 0 else {}
            if options:
                options = options.get('value', options) if isinstance(options, dict) else {}
            if not isinstance(options, dict):
                raise CatalogueError('Provider options must be an object')
            if not isinstance(value, str) or not isinstance(at(doc['m_ProviderIds'], provider), str):
                raise CatalogueError('Internal IDs and provider IDs must be strings')
            deps = [] if dep < 0 else [at(keys, dep)]
            loc = Location(f'{source}#{i}', source, value, at(doc['m_ProviderIds'], provider),
                           at(keys, primary), entry_keys[i], deps, options,
                           at(doc['m_resourceTypes'], resource) if 'm_resourceTypes' in doc else resource)
            if loc.is_bundle:
                try:
                    loc.url = resolve_url(value, source, properties or {})
                except CatalogueError as exc:
                    loc.url_error = str(exc)
            locations.append(loc)
        return locations, {'format': 'unity-addressables-packed-json', 'entries': n,
                           'keys': len(keys), 'bundles': sum(x.is_bundle for x in locations)}
    except (KeyError, TypeError, IndexError, struct.error, UnicodeError, ValueError) as exc:
        if isinstance(exc, CatalogueError):
            raise
        raise CatalogueError(f'Invalid packed catalogue: {exc}') from exc


class Index:
    def __init__(self, locations):
        self.locations = {loc.id: loc for loc in locations}
        self.by_catalogue_key = {}
        for loc in locations:
            for key in loc.keys:
                self.by_catalogue_key.setdefault((loc.catalogue, token(key)), []).append(loc.id)

    def bundle_closure(self, roots):
        visiting, done, bundles, issues = set(), set(), {}, []
        def visit(loc_id):
            if loc_id in visiting:
                issues.append(f'Dependency cycle at {loc_id}')
                return
            if loc_id in done:
                return
            visiting.add(loc_id)
            loc = self.locations[loc_id]
            if loc.is_bundle:
                bundles[loc.id] = loc
                if loc.url_error:
                    issues.append(loc.url_error)
            for key in loc.dependencies:
                matches = self.by_catalogue_key.get((loc.catalogue, token(key)), [])
                if not matches:
                    issues.append(f'Missing dependency {key!r} in {loc.catalogue}')
                for match in matches:
                    visit(match)
            visiting.remove(loc_id)
            done.add(loc_id)
        for root in roots:
            visit(root)
        return list(bundles.values()), sorted(set(issues))
