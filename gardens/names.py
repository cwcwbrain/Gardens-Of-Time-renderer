"""Official chapter-list display names, joined through production metadata.

Names remain literal localization strings. Filesystem escaping, collision
handling and image copying belong to the exporter, not this metadata reader.
"""
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin

from .chapters import ASSET_KEY, ASSET_PATH, _fetch, _manager_convention, load_metadata_asset
from .download import DownloadError, cache_name, sha256
from .scenes import Scene


CATALOG_ASSETS_KEY = 'Prod/catalogAssets_en_us'
CATALOG_ASSETS_PATH = 'Assets/Data/GameData/CatalogAssets/Prod/catalogAssets_en_us.asset'
LOCALIZATION_KEYS = {
    'scenes': 'xml/en_us/text_manager_scenes.json',
    'missions': 'xml/en_us/text_manager_missions.json',
    'stories': 'xml/en_us/text_manager_stories.json',
}


def _title(data, key):
    record = data.get(key) if isinstance(data, dict) and isinstance(key, str) else None
    value = record.get('title') if isinstance(record, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def _records(tree, field, key, issues):
    values = tree.get(field)
    if not isinstance(values, list):
        raise ValueError(f'Official naming metadata has no {field} array')
    records = {}
    duplicates = set()
    for value in values:
        identity = value.get(key) if isinstance(value, dict) else None
        if not isinstance(identity, str) or not identity:
            issues.append(f'Invalid {field} record identifier')
            continue
        if identity in records or identity in duplicates:
            records.pop(identity, None)
            duplicates.add(identity)
            issues.append(f'Duplicate {field} identifier {identity}')
        else:
            records[identity] = value
    return records


def primary_roster(base_scenes, tree):
    """Extend a read-only naming roster with all verified primary scene IDs.

    This is for one-off lookup, not incremental chapter enrollment: chapter
    numbers and mission orders need not be contiguous. No Item Manager URL is
    inferred or fetched for appended metadata-only rows.
    """
    source_issues = []
    scene_rows = _records(tree, 'sceneVOs', 'sid', source_issues)
    missions = _records(tree, 'missionVOs', 'mid', source_issues)
    stories = _records(tree, 'storyVOs', 'stid', source_issues)
    name_counts = defaultdict(int)
    for source in scene_rows.values():
        name = source.get('name')
        if isinstance(name, str):
            name_counts[name.casefold()] += 1
    report = {'status': 'available', 'base_scene_count': len(base_scenes),
              'added_scene_count': 0, 'added_scene_ids': [], 'primary_scene_count': 0,
              'skipped_replays': [], 'issues': [], 'source_issues': source_issues}
    by_display_order = defaultdict(list)
    for story in stories.values():
        if str(story.get('disposition')) != '1':
            continue
        raw_order = str(story.get('display_order', ''))
        if re.fullmatch(r'[1-9]\d*', raw_order) is None:
            report['issues'].append({'story_id': story['stid'], 'issue': 'Invalid primary chapter display order'})
            continue
        by_display_order[int(raw_order)].append(story)
    extended = list(base_scenes)
    seen = {s.scene_id.casefold() for s in base_scenes}
    production_seen = set()
    next_order = max((s.order for s in base_scenes), default=0)
    for display_order in sorted(by_display_order):
        group = by_display_order[display_order]
        if len(group) != 1:
            report['issues'].append({'display_order': display_order, 'issue': 'Ambiguous primary chapter display order'})
            continue
        story = group[0]
        membership = story.get('mission_ids')
        ids = membership.split(',') if isinstance(membership, str) and membership else []
        if not ids or len(set(ids)) != len(ids) or any(not mid or mid.strip() != mid for mid in ids):
            report['issues'].append({'story_id': story['stid'], 'issue': 'Invalid primary mission membership'})
            continue
        ordered = []
        problems = []
        for mid in ids:
            mission = missions.get(mid)
            if mission is None or mission.get('parent_stid') != story['stid']:
                problems.append(f'Missing or mismatched primary mission {mid}')
                continue
            raw_order = str(mission.get('sort_order', ''))
            if re.fullmatch(r'[1-9]\d*', raw_order) is None:
                problems.append(f'Invalid sort_order for primary mission {mid}')
                continue
            source_id = mission.get('scene_id')
            source = scene_rows.get(source_id) if isinstance(source_id, str) else None
            name = source.get('name') if source else None
            if not isinstance(name, str) or re.fullmatch(r'[A-Za-z0-9_-]+', name) is None:
                problems.append(f'Missing or unsafe scene reference for primary mission {mid}')
                continue
            if name_counts[name.casefold()] != 1:
                problems.append(f'Ambiguous scene metadata name {name}')
                continue
            ordered.append((int(raw_order), name))
        if len({value[0] for value in ordered}) != len(ordered):
            problems.append('Ambiguous primary mission sort_order values')
        if problems:
            report['issues'].append({'story_id': story['stid'], 'issues': sorted(set(problems))})
            continue
        for mission_order, name in sorted(ordered):
            folded = name.casefold()
            if folded in production_seen:
                report['skipped_replays'].append({'scene_id': name, 'story_id': story['stid'], 'mission_order': mission_order})
                continue
            production_seen.add(folded)
            if folded in seen:
                continue
            next_order += 1
            extended.append(Scene(next_order, name, '', '', [name, '', '']))
            seen.add(folded)
            report['added_scene_ids'].append(name)
    report.update(added_scene_count=len(extended)-len(base_scenes), primary_scene_count=len(production_seen))
    if report['issues'] or source_issues:
        report['status'] = 'partial'
    return extended, report


def build_name_map(scenes, tree, scene_text, mission_text, story_text):
    """Map exact scene IDs to their first primary chapter's localized title.

    The chapter's mission_ids establish membership and each mission's numeric
    sort_order establishes displayed order. Bonus/challenge titles never replace
    the primary chapter name. Missing or ambiguous rows are reported separately.
    """
    source_issues = []
    scene_records = _records(tree, 'sceneVOs', 'sid', source_issues)
    missions = _records(tree, 'missionVOs', 'mid', source_issues)
    stories = _records(tree, 'storyVOs', 'stid', source_issues)
    by_name = defaultdict(list)
    for source in scene_records.values():
        name = source.get('name')
        if isinstance(name, str):
            by_name[name].append(source)
    contexts = defaultdict(list)
    name_problems = defaultdict(list)
    for story in stories.values():
        if str(story.get('disposition')) != '1':
            continue
        raw_ids = story.get('mission_ids')
        if not isinstance(raw_ids, str) or not raw_ids:
            source_issues.append(f'Primary story {story["stid"]} has no mission membership')
            continue
        ids = raw_ids.split(',')
        if len(set(ids)) != len(ids) or any(not mid or mid.strip() != mid for mid in ids):
            source_issues.append(f'Primary story {story["stid"]} has ambiguous mission membership')
            for mid in ids:
                mission = missions.get(mid, {})
                source = scene_records.get(mission.get('scene_id'), {})
                name_problems[source.get('name')].append('Ambiguous primary chapter mission membership')
            continue
        chapter_title = _title(story_text, story.get('name'))
        chapter_match = re.match(r'^Chapter\s+(\d+)\b', chapter_title or '')
        display_order = str(story.get('display_order', ''))
        for mid in ids:
            mission = missions.get(mid)
            if mission is None:
                source_issues.append(f'Primary story {story["stid"]} references missing mission {mid}')
                continue
            source = scene_records.get(mission.get('scene_id'))
            if source is None:
                source_issues.append(f'Mission {mid} references missing scene metadata')
                continue
            name = source.get('name')
            problem = None
            if not chapter_match:
                problem = 'No verified numbered primary chapter title'
            elif re.fullmatch(r'[1-9]\d*', display_order) is None:
                problem = 'Invalid primary chapter display order'
            elif mission.get('parent_stid') != story['stid']:
                problem = 'Mission parent disagrees with primary chapter membership'
            mission_order = str(mission.get('sort_order', ''))
            if re.fullmatch(r'[1-9]\d*', mission_order) is None:
                problem = 'Invalid primary mission sort order'
            if problem:
                name_problems[name].append(problem)
                continue
            contexts[name].append({
                'source_scene_id': source['sid'], 'chapter_number': int(chapter_match[1]),
                'chapter_title': chapter_title, 'chapter_display_order': int(display_order),
                'mission_order': int(mission_order), 'mission_key': mission.get('name'),
                'mission_id': mid, 'story_key': story.get('name'), 'story_id': story['stid'],
                'title': _title(mission_text, mission.get('name')),
                'primary_default_mode': mission.get('default_mode'),
                'scene_gameplay_mode': source.get('gameplay_mode'),
            })
    mappings = {}
    issues = []
    replays = 0
    for scene in scenes:
        sid = scene.scene_id
        problems = list(name_problems.get(sid, []))
        if len(by_name.get(sid, [])) != 1:
            problems.append('No unique exact scene identifier in official metadata')
        appearance = sorted(contexts.get(sid, []), key=lambda value: (value['chapter_number'], value['mission_order']))
        if not appearance:
            problems.append('No verified primary chapter mission for scene')
        first = appearance[0] if appearance else None
        if first:
            earliest = [c for c in appearance if (c['chapter_number'], c['mission_order']) == (first['chapter_number'], first['mission_order'])]
            if len(earliest) != 1:
                problems.append('Ambiguous earliest primary chapter mission/title')
            if not first['title']:
                problems.append('Missing official chapter-list title localization')
        official_scene_title = _title(scene_text, sid)
        if not official_scene_title:
            problems.append('Missing official scene-title localization')
        if problems:
            issues.append({'scene_id': sid, 'issues': sorted(set(problems))})
            continue
        mappings[sid] = {
            'scene_id': sid, 'order': scene.order, 'title': first['title'],
            'scene_title': official_scene_title, 'chapter_number': first['chapter_number'],
            'chapter_title': first['chapter_title'], 'chapter_display_order': first['chapter_display_order'],
            'mission_order': first['mission_order'], 'mission_key': first['mission_key'],
            'story_key': first['story_key'], 'mission_id': first['mission_id'],
            'primary_default_mode': first['primary_default_mode'],
            'scene_gameplay_mode': first['scene_gameplay_mode'],
            'source_scene_id': first['source_scene_id'], 'primary_appearances': appearance,
            'localization_keys': {'scene': sid, 'mission': first['mission_key'], 'story': first['story_key']},
        }
        replays += len(appearance) > 1
    return {
        'status': 'partial' if issues or source_issues else 'available', 'mappings': mappings,
        'issues': issues, 'source_issues': sorted(set(source_issues)),
        'summary': {'requested_scenes': len(scenes), 'mapped_scenes': len(mappings),
                    'unmapped_scenes': len(issues), 'scenes_with_replay_aliases': replays},
        'policy': 'Official English chapter-list title from first primary chapter; explicit mission membership and numeric mission sort order; corrected roster order retained.',
    }


def _validate_localization(path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or not data or not all(isinstance(key, str) for key in data):
        raise ValueError('Official localization must be a nonempty keyed JSON object')
    if not any(_title(data, key) for key in data):
        raise ValueError('Official localization contains no verified titles')


def load_official_names(out, client, config_path, scenes, offline=False, include_all_primary=False):
    """Load verified production naming resources; never rename or render files."""
    provenance = {}
    roster_summary = None
    try:
        tree, source = load_metadata_asset(out, client, config_path, ASSET_KEY, ASSET_PATH, offline)
        provenance['chapter_metadata'] = source
        if include_all_primary:
            scenes, roster_summary = primary_roster(scenes, tree)
        assets, source = load_metadata_asset(out, client, config_path, CATALOG_ASSETS_KEY,
                                             CATALOG_ASSETS_PATH, offline, refresh_catalogue=False)
        provenance['catalog_assets'] = source
        entries = assets.get('catalogAssetsVOs')
        if not isinstance(entries, list):
            raise ValueError('No production CatalogAssets path inventory')
        prefix, _ = _manager_convention(scenes)
        marker = 'xml/en_us/text_manager_items_'
        if not prefix.endswith(marker):
            raise ValueError('No verified English catalog root in corrected Item Manager URLs')
        root = prefix[:-len(marker)]
        provenance['catalog_root'] = {'url': root, 'derived_from': 'Corrected CSV Item Manager URL convention'}
        localizations = {}
        provenance['localizations'] = {}
        for kind, key in LOCALIZATION_KEYS.items():
            matches = [entry for entry in entries if isinstance(entry, dict) and entry.get('k') == key]
            if len(matches) != 1:
                raise ValueError(f'No unique explicit production localization resource {key}')
            url = urljoin(root, key)
            path = Path(out)/'naming/localization'/cache_name(url, '.json')
            path.parent.mkdir(parents=True, exist_ok=True)
            _fetch(url, path, client, _validate_localization, offline, refresh=True)
            localizations[kind] = json.loads(path.read_text(encoding='utf-8-sig'))
            provenance['localizations'][kind] = {'catalog_assets_key': key, 'url': url,
                'path': str(path), 'sha256': sha256(path),
                'source': 'verified_cache' if offline else 'live_localization'}
        report = build_name_map(scenes, tree, localizations['scenes'], localizations['missions'], localizations['stories'])
        report['provenance'] = provenance
        if roster_summary is not None:
            report['roster_summary'] = roster_summary
            if roster_summary['status'] != 'available':
                report['status'] = 'partial'
        return report
    except (ValueError, OSError, DownloadError) as exc:
        report = {'status': 'unavailable', 'mappings': {}, 'issues': [], 'source_issues': [str(exc)],
                  'summary': {'requested_scenes': len(scenes), 'mapped_scenes': 0, 'unmapped_scenes': len(scenes)},
                  'provenance': provenance}
        if roster_summary is not None:
            report['roster_summary'] = roster_summary
        return report
