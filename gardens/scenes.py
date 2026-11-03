"""Chronology-preserving scene input and conservative catalogue matching."""
import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit


@dataclass
class Scene:
    order: int
    scene_id: str
    thumbnail_url: str
    item_manager_url: str
    original_row: list


def read_scenes(path):
    scenes, duplicates, seen = [], [], {}
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = csv.reader(f)
        for line, row in enumerate(rows, 1):
            if not row or not any(row):
                continue
            if line == 1 and row[0].strip().casefold() in ('scene_id', 'scene', 'scene_identifier'):
                continue
            if len(row) != 3 or not row[0].strip():
                raise ValueError(f'Invalid scene CSV row {line}: expected identifier and two URLs')
            scene_id = row[0].strip()
            key = scene_id.casefold()
            if key in seen:
                duplicates.append({'line': line, 'scene_id': scene_id, 'first_order': seen[key], 'original_row': row})
                continue
            seen[key] = len(scenes) + 1
            scenes.append(Scene(len(scenes) + 1, scene_id, row[1], row[2], row))
    return scenes, duplicates


def identifiers(value):
    if not isinstance(value, str):
        return set()
    # Keep punctuation: normalizing it away can merge distinct scene IDs.
    path = unquote(urlsplit(value.replace('\\', '/')).path)
    parts = [x.casefold() for x in PurePosixPath(path).parts if x not in ('/', '.', '..')]
    result = set(parts)
    if parts:
        stem = PurePosixPath(parts[-1]).stem
        result.add(stem)
        match = re.fullmatch(r'(.+)_assets_all_[0-9a-f]{32}(?:\.(?:bundle|unity3d|assetbundle))?', parts[-1])
        if match:
            result.add(match[1])
    result.add(value.casefold())
    return result


def match_scenes(scenes, index, complete_catalogues):
    lookup = {}
    for loc in index.locations.values():
        values = [loc.internal_id, loc.primary_key, *loc.keys]
        for key in set().union(*(identifiers(v) for v in values)):
            lookup.setdefault(key, set()).add(loc.id)
    results = []
    for scene in scenes:
        roots = sorted(lookup.get(scene.scene_id.casefold(), []))
        candidates = {}
        issues = []
        for root in roots:
            bundles, errors = index.bundle_closure([root])
            if not bundles:
                issues.extend(errors or [f'No bundle reachable from {root}'])
                continue
            # Equivalent locations across catalogues do not create fake ambiguity.
            signature = tuple(sorted(set((b.url or b.internal_id, json.dumps(b.options, sort_keys=True)) for b in bundles)))
            candidate = candidates.setdefault(signature, {'roots': [], 'bundles': {}, 'issues': []})
            candidate['roots'].append(root)
            candidate['bundles'].update({b.id: b for b in bundles})
            candidate['issues'].extend(errors)
        if not complete_catalogues:
            status = 'provisional' if candidates else 'catalogues_incomplete'
        elif len(candidates) > 1:
            status = 'ambiguous'
        elif len(candidates) == 1:
            status = 'unresolved' if any(c['issues'] for c in candidates.values()) or issues else 'matched'
        else:
            status = 'missing'
        if len(candidates) > 1 and not complete_catalogues:
            issues.append('Multiple candidates found in available catalogues')
        results.append({**asdict(scene), 'status': status, 'issues': sorted(set(issues)),
                        'candidates': [{'roots': c['roots'],
                                        'bundles': [b.to_dict() for b in c['bundles'].values()],
                                        'issues': sorted(set(c['issues']))} for c in candidates.values()]})
    return results
