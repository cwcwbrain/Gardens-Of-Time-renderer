"""Append authoritative future chapter missions without changing corrected rows.

The production Group00 asset supplies chapter/mission/scene joins and order.
Item Manager paths are derived from the input CSV's demonstrated convention,
then accepted only after a verified nonempty label JSON response.
"""
import json
import re
from dataclasses import replace
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit

from .catalogue import Index, parse_catalogue
from .download import DownloadError, cache_name, read_json, sha256, validate_bundle
from .scenes import Scene, match_scenes, scene_kind


ASSET_KEY = 'Prod/Group00_VO_SO'
ASSET_PATH = 'Assets/Data/GameData/StaticData/Prod/Group00_VO_SO.asset'
RUNTIME_PROPERTY = '{UnityEngine.AddressableAssets.Addressables.RuntimePath}'
RUNTIME_ERROR = 'Unresolved runtime property ' + RUNTIME_PROPERTY


def _local_monoscripts(location):
    """Recognize only the explicit local WebGL MonoScripts runtime resource.

    It supplies script identities to the Unity player, not chapter values. This
    metadata-only reader never executes scripts and requires embedded type trees
    plus the exact Group00 container reference. Other dependencies still resolve
    normally and an absent/unsupported type tree still fails the read.
    """
    return (location.is_bundle and location.url is None and location.url_error == RUNTIME_ERROR
            and re.fullmatch(re.escape(RUNTIME_PROPERTY) +
                r'/WebGL/[^/]+/[0-9a-f]{32}_monoscripts_[0-9a-f]{32}\.bundle',
                location.internal_id) is not None)


def _by_id(tree, field, key):
    values = tree.get(field)
    if not isinstance(values, list):
        raise ValueError(f'Chapter metadata has no {field} array')
    lookup = {}
    for entry in values:
        if not isinstance(entry, dict) or not isinstance(entry.get(key), str) or not entry[key]:
            raise ValueError(f'Invalid {field} identifier')
        if entry[key] in lookup:
            raise ValueError(f'Duplicate {field} identifier {entry[key]}')
        lookup[entry[key]] = entry
    return lookup


def _manager_convention(scenes):
    prefixes = set()
    stripped_ftd = False
    for scene in scenes:
        url = scene.item_manager_url
        suffix = scene.scene_id + '.json'
        if scene_kind(scene.scene_id) == 'ordinary' and url.endswith(suffix):
            prefix = url[:-len(suffix)]
            if prefix.endswith('text_manager_items_'):
                prefixes.add(prefix)
        base = re.sub(r'FTD\d+$', '', scene.scene_id, flags=re.I)
        if base != scene.scene_id and url.endswith('text_manager_items_' + base + '.json'):
            stripped_ftd = True
    if len(prefixes) != 1:
        raise ValueError('No unique Item Manager URL convention in corrected scene rows')
    prefix = next(iter(prefixes))
    parsed = urlsplit(prefix)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError('Invalid corrected Item Manager URL convention')
    return prefix, stripped_ftd


def append_chapters(scenes, tree):
    """Pure metadata join. Manager URLs here are candidates, not verified URLs."""
    scene_rows = _by_id(tree, 'sceneVOs', 'sid')
    missions = _by_id(tree, 'missionVOs', 'mid')
    stories = _by_id(tree, 'storyVOs', 'stid')
    chapters = {}
    for story in stories.values():
        name = story.get('name', '')
        if not isinstance(name, str):
            raise ValueError('Invalid chapter metadata story name')
        match = re.fullmatch(r'chapter_(\d+)', name)
        if match:
            number = int(match[1])
            if number in chapters:
                raise ValueError(f'Duplicate chapter number {number}')
            chapters[number] = story
    current = max((int(m[1]) for s in scenes if (m := re.match(r'ch(\d+)', s.scene_id, re.I))), default=0)
    future = sorted(number for number in chapters if number > current)
    for expected,number in enumerate(future,current+1):
        if number!=expected:
            raise ValueError(f'Chapter metadata skips chapter {expected}; later chapters cannot be appended in verified order')
    report = {'status': 'metadata_only', 'base_max_chapter': current, 'new_chapters': future,
              'added_scenes': [], 'skipped_duplicates': [], 'manager_candidates': {}, 'errors': []}
    if not future:
        return list(scenes), report
    prefix, stripped_ftd = _manager_convention(scenes)
    extended = list(scenes)
    seen = {s.scene_id.casefold() for s in scenes}
    order = max((s.order for s in scenes), default=0)
    for number in future:
        story = chapters[number]
        raw = story.get('mission_ids')
        if not isinstance(raw, str) or not raw:
            raise ValueError(f'Chapter {number} has no ordered mission IDs')
        ids = raw.split(',')
        if any(not mid or mid.strip() != mid for mid in ids) or len(set(ids)) != len(ids):
            raise ValueError(f'Chapter {number} has invalid or duplicate mission references')
        ordered = []
        for mid in ids:
            mission = missions.get(mid)
            if not mission or mission.get('parent_stid') != story['stid']:
                raise ValueError(f'Chapter {number} has missing or mismatched mission {mid}')
            raw_order = str(mission.get('sort_order', ''))
            if re.fullmatch(r'[1-9]\d*', raw_order) is None:
                raise ValueError(f'Chapter {number} mission {mid} has no valid numeric sort_order')
            ordered.append((int(raw_order), mid, mission))
        ordered.sort(key=lambda row: row[0])
        if [entry[0] for entry in ordered] != list(range(1, len(ordered) + 1)):
            raise ValueError(f'Chapter {number} has duplicate or noncontiguous mission sort_order values')
        # mission_ids establishes membership; sort_order establishes displayed
        # order. Older production chapters demonstrably store shuffled ID lists.
        for position, mid, mission in ordered:
            source = scene_rows.get(mission.get('scene_id'))
            name = source.get('name') if source else None
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', name):
                raise ValueError(f'Chapter {number} mission {mid} has no safe scene reference')
            if name.casefold() in seen:
                report['skipped_duplicates'].append({'chapter': number, 'mission_id': mid, 'scene_id': name})
                continue
            candidates = [prefix + name + '.json']
            base = re.sub(r'FTD\d+$', '', name, flags=re.I)
            if stripped_ftd and base != name:
                candidates.append(prefix + base + '.json')
            order += 1
            extended.append(Scene(order, name, '', candidates[0], [name, '', candidates[0]]))
            seen.add(name.casefold())
            report['added_scenes'].append({'scene_id': name, 'chapter': number, 'mission_id': mid,
                                          'position': position, 'source_scene_id': source['sid']})
            report['manager_candidates'][name] = candidates
    return extended, report


def _fetch(url, path, client, validator, offline, refresh=False):
    if offline:
        receipt = read_json(str(path) + '.verified.json')
        if not path.is_file() or receipt.get('url') != url or receipt.get('sha256') != sha256(path):
            raise DownloadError('No verified chapter metadata cache for ' + url)
        validator(path)
    elif refresh:
        client.fetch(url, path, validator, refresh=True)
    else:
        client.fetch(url, path, validator)


def _main_catalogue(config_path):
    if not isinstance(config_path,(str,Path)):
        raise ValueError('No verified remote-config snapshot is available for chapter discovery')
    config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    entries = config.get('assetCatalogInfo', []) if isinstance(config, dict) else []
    if not isinstance(entries, list):
        raise ValueError('Invalid MainProject catalogue configuration')
    projects = [e for e in entries if isinstance(e, dict) and e.get('catalogProjectName') == 'MainProject']
    if len(projects) != 1:
        raise ValueError('No unique MainProject production metadata catalogue')
    prod = projects[0].get('URLInfo_Prod', {})
    if not isinstance(prod, dict):
        raise ValueError('Invalid MainProject production configuration')
    base, filename = prod.get('URL'), prod.get('catalogName')
    if not isinstance(base, str) or urlsplit(base).scheme != 'https' or not urlsplit(base).netloc:
        raise ValueError('Invalid MainProject production URL')
    if not isinstance(filename, str) or not filename:
        raise ValueError('Missing MainProject production catalogue filename')
    parsed = urlsplit(filename)
    if parsed.scheme or parsed.netloc or filename.startswith(('/', '\\')) or '\\' in filename or '..' in PurePosixPath(parsed.path).parts:
        raise ValueError('Invalid MainProject production catalogue filename')
    return urljoin(base.rstrip('/') + '/', filename)


def _read_tree(paths,asset_path=ASSET_PATH):
    import UnityPy
    trees = []
    for path in paths:
        try:
            env = UnityPy.load(path.read_bytes())
            # UnityPy ContainerHelper supports membership/subscript access but
            # does not implement dict.get(). Use its public mapping operations.
            if asset_path in env.container:
                pointer = env.container[asset_path]
                trees.append(pointer.read_typetree())
        except Exception as exc:
            raise ValueError(f'Cannot read production metadata asset: {exc}') from exc
    if len(trees) != 1 or trees[0].get('m_Name') != PurePosixPath(asset_path).stem:
        raise ValueError(f'No unique production metadata asset {asset_path}')
    return trees[0]


def _validate_manager(path):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or not data:
        raise ValueError('Item Manager is not a nonempty JSON object')
    if not all(isinstance(key, str) and key and isinstance(labels, dict) for key, labels in data.items()):
        raise ValueError('Item Manager has invalid label records')
    if not any(isinstance(labels.get(key), str) and labels[key] for labels in data.values() for key in ('beginner', 'gold')):
        raise ValueError('Item Manager contains no verified object labels')


def load_metadata_asset(out,client,config_path,asset_key,asset_path,offline=False,refresh_catalogue=True):
    """Load one exact production value asset without executing Unity scripts."""
    base=Path(out)
    catalogue_url = _main_catalogue(config_path)
    catalogue_path = base/'chapter_metadata/catalogues'/cache_name(catalogue_url, '.json')
    catalogue_path.parent.mkdir(parents=True, exist_ok=True)
    _fetch(catalogue_url, catalogue_path, client,
           lambda p: parse_catalogue(p.read_bytes(), catalogue_url, {}), offline, refresh=refresh_catalogue)
    locations, _ = parse_catalogue(catalogue_path.read_bytes(), catalogue_url, {})
    roots = [loc for loc in locations if loc.primary_key == asset_key and loc.internal_id == asset_path]
    if len(roots) != 1:
        raise ValueError(f'No unique explicit production metadata catalogue asset {asset_key}')
    local_scripts = {loc.id: loc for loc in locations if _local_monoscripts(loc)}
    # Clear the known local script's own URL issue before computing closure,
    # rather than subtracting a deduplicated error string: an unrelated
    # unresolved bundle may have the identical runtime-property message.
    value_locations = [replace(loc, url_error='') if loc.id in local_scripts else loc for loc in locations]
    bundles, issues = Index(value_locations).bundle_closure([roots[0].id])
    omitted = [local_scripts[b.id] for b in bundles if b.id in local_scripts]
    bundles = [b for b in bundles if b.id not in local_scripts]
    omitted_report = [
        {'location_id': b.id, 'internal_id': b.internal_id,
         'reason': 'Metadata value decoding uses the embedded type tree and executes no Unity scripts'}
        for b in omitted]
    if issues or not bundles or any(not b.url for b in bundles):
        raise ValueError('Unresolved production chapter metadata bundles: ' + '; '.join(issues))
    paths = []
    for bundle in bundles:
        path = base/'chapter_metadata/bundles'/cache_name(bundle.url, '.bundle')
        path.parent.mkdir(parents=True, exist_ok=True)
        size = bundle.options.get('m_BundleSize') or bundle.options.get('BundleSize')
        _fetch(bundle.url, path, client, lambda p: validate_bundle(p, int(size) if size else None), offline)
        paths.append(path)
    tree = _read_tree(paths) if asset_path==ASSET_PATH else _read_tree(paths,asset_path)
    provenance={'source_catalogue':catalogue_url,'metadata_source':'verified_cache' if offline else 'live_main_catalogue',
                'source_catalogue_sha256':sha256(catalogue_path),'source_config_sha256':sha256(config_path),
                'source_asset':asset_path,'source_bundles':[{'url':b.url,'sha256':sha256(p)} for b,p in zip(bundles,paths)],
                'omitted_runtime_script_bundles':omitted_report}
    return tree,provenance


def discover_chapters(scenes, index, out, client, config_path, offline=False):
    """Read exact production metadata and verify new scene Manager resources.

    All metadata-valid scene positions are returned, including unavailable ones.
    Unverified manager URLs are cleared and reported, so callers can reserve
    chronological placeholders without scheduling a speculative download.
    """
    report = {'status': 'unavailable', 'errors': [], 'added_scenes': [], 'new_chapters': [],
              'unverified_scenes': [], 'missing_assets': [], 'verified_item_managers': []}
    base = Path(out)
    try:
        tree,provenance=load_metadata_asset(out,client,config_path,ASSET_KEY,ASSET_PATH,offline)
        extended, parsed = append_chapters(scenes, tree)
        report.update(parsed,**provenance,status='available')
        added = {entry['scene_id'] for entry in report['added_scenes']}
        eligible = {row['scene_id']: row for row in match_scenes([s for s in extended if s.scene_id in added], index, True)}
        result = []
        for scene in extended:
            if scene.scene_id not in added:
                result.append(scene)
                continue
            row = eligible[scene.scene_id]
            if row['status'] not in ('matched', 'ambiguous') or row['issues'] or any(c['issues'] for c in row['candidates']):
                issues = ['No complete eligible scene bundle match in available catalogues']
                report['missing_assets'].append({'scene_id': scene.scene_id, 'status': row['status'], 'issues': issues})
                report['unverified_scenes'].append({'scene_id': scene.scene_id, 'issues': issues})
                result.append(replace(scene, item_manager_url='', original_row=[scene.scene_id, '', '']))
                continue
            failures = []
            accepted = None
            for url in report['manager_candidates'][scene.scene_id]:
                path = base/'item_managers'/cache_name(url, '.json')
                path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    _fetch(url, path, client, _validate_manager, offline)
                    accepted = url
                    report['verified_item_managers'].append({'scene_id': scene.scene_id, 'url': url,
                        'sha256': sha256(path), 'provenance': 'CSV-derived path convention, verified HTTP/cache content and object label JSON'})
                    break
                except (ValueError, OSError, DownloadError) as exc:
                    failures.append(str(exc))
            if accepted is None:
                report['unverified_scenes'].append({'scene_id': scene.scene_id, 'issues': failures})
            result.append(replace(scene, item_manager_url=accepted or '', original_row=[scene.scene_id, '', accepted or '']))
        return result, report
    except (ValueError, OSError, DownloadError) as exc:
        report.update(status='unavailable', errors=[str(exc)])
        return list(scenes), report
