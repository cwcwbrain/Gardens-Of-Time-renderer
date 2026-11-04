#!/usr/bin/env python3
"""Catalogue indexing, bundle downloading, and opt-in ordinary-scene snapshots."""
import argparse
import csv
import json
import re
import sys
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from gardens.catalogue import Index, parse_catalogue
from gardens.download import Downloader, DownloadError, atomic_json, cache_name, read_json, sha256, validate_bundle
from gardens.scenes import match_scenes, read_scenes, variant_id as candidate_variant_id

ROOT = Path(__file__).resolve().parent


def write_csv(path, rows, fields):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(row[key], ensure_ascii=False) if isinstance(row.get(key), (list, dict)) else row.get(key, '') for key in fields})


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalogues', type=Path, default=ROOT / 'catalogues.json')
    p.add_argument('--remote-config', help='Override the game loading-config URL for production HO catalogue discovery')
    p.add_argument('--scenes', type=Path, default=ROOT / 'data/Sheet2_chronology_fixed.csv')
    select = p.add_mutually_exclusive_group(required=True)
    select.add_argument('--all', action='store_true')
    select.add_argument('--test-all', action='store_true', help='Test every scene/version with isolated workers and resumable reports')
    select.add_argument('--scene', action='append', help='Exact scene ID; may be repeated')
    p.add_argument('--output', type=Path, default=ROOT / 'output')
    p.add_argument('--render', action='store_true', help='Render selected ordinary scenes; --all uses the resumable isolated test runner')
    p.add_argument('--test-timeout', type=float, default=300, help='Maximum seconds per variant test worker (default 300)')
    p.add_argument('--test-limit', type=int, help='Process at most this many pending scenes per test invocation')
    p.add_argument('--retry-failed', action='store_true', help='Retry non-passing checkpoints in test mode')
    p.add_argument('--plan-only', action='store_true', help='Create the full test roster without starting workers')
    p.add_argument('--frame', type=int, help='One-based snapshot frame for --render')
    p.add_argument('--index-only', action='store_true', help='Download and index catalogues; do not download bundles')
    p.add_argument('--offline', action='store_true', help='Read verified cached catalogues and bundles only')
    p.add_argument('--refresh-catalogues', action='store_true')
    p.add_argument('--timeout', type=float, default=30)
    p.add_argument('--retries', type=int, default=2)
    p.add_argument('--property', action='append', default=[], metavar='NAME=VALUE', help='Verified Unity runtime property; never guessed')
    args = p.parse_args(argv)
    if args.test_all: args.render=True
    batch_mode=args.test_all or (args.all and args.render)
    if args.render and args.index_only: p.error('--render/--test-all cannot use --index-only')
    if not batch_mode and (args.plan_only or args.retry_failed or args.test_limit is not None or args.test_timeout!=300):
        p.error('Test options require --test-all or --all --render')
    if args.test_timeout<=0 or (args.test_limit is not None and args.test_limit<=0):
        p.error('Test timeout and scene limit must be positive')
    if args.frame is not None and (not args.render or args.frame < 1):
        p.error('--frame requires --render and a positive one-based frame number')
    if args.timeout <= 0 or args.retries < 0:
        p.error('timeout must be positive and retries non-negative')
    if args.offline and args.refresh_catalogues:
        p.error('--offline cannot refresh catalogues')
    properties = {}
    for item in args.property:
        key, sep, value = item.partition('=')
        if not sep or not key:
            p.error('--property requires NAME=VALUE')
        properties[key] = value
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    scenes, duplicates = read_scenes(args.scenes)
    if args.scene:
        requested = {s.casefold() for s in args.scene}
        known = {s.scene_id.casefold() for s in scenes}
        if requested - known:
            p.error('Unknown scene IDs: ' + ', '.join(sorted(requested - known)))
        scenes = [s for s in scenes if s.scene_id.casefold() in requested]
    manifest = json.loads(args.catalogues.read_text(encoding='utf-8-sig'))
    from gardens.discovery import catalogue_sources
    client = Downloader(args.timeout,args.retries)
    discovery = catalogue_sources(manifest,out,client,args.offline,args.remote_config)
    atomic_json(out/'catalogue_discovery.json',discovery)
    if discovery.get('warning'):print('Catalogue discovery fallback: '+discovery['warning'],flush=True)
    print('Catalogue source: '+discovery['source'],flush=True)
    urls = discovery['catalogues']
    if not isinstance(urls, list) or not urls or not all(isinstance(u, str) and urlsplit(u).scheme == 'https' and urlsplit(u).netloc for u in urls):
        p.error('catalogues.json must contain a nonempty catalogues list of explicit HTTPS URLs')
    if len(set(urls)) != len(urls):
        p.error('Duplicate catalogue URLs in manifest')
    run_id = uuid.uuid4().hex
    atomic_json(out / 'summary.json', {'run_id': run_id, 'status': 'in_progress'})
    if not batch_mode: atomic_json(out / 'download_report.json', [])
    locations, catalogue_reports = [], []
    for number, url in enumerate(urls, 1):
        dest = out / 'catalogues' / cache_name(url, '.json')
        report = {'url': url, 'path': str(dest), 'status': 'failed'}
        print(f'Catalogue {number}/{len(urls)}: {url}', flush=True)
        try:
            def validate(path):
                parse_catalogue(path.read_bytes(), url, properties)
            if args.offline:
                receipt = read_json(str(dest) + '.verified.json')
                if not dest.exists() or receipt.get('url') != url or receipt.get('sha256') != sha256(dest):
                    raise DownloadError('No verified cached catalogue')
                validate(dest)
                result = {'status': 'cached', 'sha256': sha256(dest)}
            else:
                result = client.fetch(url, dest, validate, args.refresh_catalogues)
            parsed, info = parse_catalogue(dest.read_bytes(), url, properties)
            locations.extend(parsed)
            report.update(status='indexed', transfer=result['status'], sha256=result['sha256'], **info)
        except (ValueError, OSError, DownloadError) as exc:
            report['error'] = str(exc)
            print(f'  FAILED: {exc}', flush=True)
        catalogue_reports.append(report)
        atomic_json(out / 'catalogue_report.json', catalogue_reports)
    complete = all(c['status'] == 'indexed' for c in catalogue_reports)
    index = Index(locations)
    atomic_json(out / 'bundle_index.json', [loc.to_dict() for loc in locations if loc.is_bundle])
    # Persist asset keys and dependency relationships too; bundle-only index is insufficient.
    atomic_json(out / 'location_index.json', [loc.to_dict() for loc in locations])
    matches = match_scenes(scenes, index, complete)
    atomic_json(out / 'duplicate_scenes.json', duplicates)
    if batch_mode:
        from gardens.batch import run_batch
        atomic_json(out/'scene_matches.json',matches)
        return run_batch(scenes,matches,args)
    downloads = {}
    for row in matches:
        row['download_status'] = 'not_requested' if args.index_only else 'not_eligible'
        row['download_errors'] = []
        row['render_status'] = 'not_eligible' if args.render else 'not_requested'
        row['render_issues'] = []
        row['variants'] = []
        if args.index_only or row['status'] not in ('matched','ambiguous'):
            continue
        for candidate in row['candidates']:
            # Hash the full URL/options set, not enumeration order or a scene name.
            # Equivalent locations from multiple catalogues share a variant.
            variant_id = candidate_variant_id(candidate)
            scene_folder = out/'scenes'/row['scene_id']
            destination = scene_folder/('variant_'+variant_id) if len(row['candidates'])>1 else scene_folder
            variant = {'variant_id':variant_id,'roots':candidate['roots'],
                       'bundle_urls':sorted({b['url'] for b in candidate['bundles'] if b['url']}),
                       'output':str(destination),'download_status':'not_eligible','download_errors':[],
                       'render_status':'incomplete' if args.render else 'not_requested',
                       'render_issues':list(candidate['issues']),'files':[]}
            row['variants'].append(variant)
            if candidate['issues'] or not candidate['bundles']:
                if not candidate['bundles']:variant['render_issues'].append('Candidate has no bundles')
                continue
            bundles = candidate['bundles']
            success = True
            for bundle in bundles:
                url = bundle['url']
                size = bundle['options'].get('m_BundleSize') or bundle['options'].get('BundleSize')
                try:
                    if not url:raise ValueError('Bundle URL is unresolved')
                    size = int(size) if size else None
                    if size is not None and size < 0:raise ValueError('Negative catalogue bundle size')
                    validator = lambda path: validate_bundle(path,size)
                    if url not in downloads:
                        dest = out/'bundles'/cache_name(url,'.bundle')
                        if args.offline:
                            receipt=read_json(str(dest)+'.verified.json')
                            if not dest.exists() or receipt.get('url')!=url or receipt.get('sha256')!=sha256(dest):
                                raise DownloadError('No verified cached bundle')
                            validate_bundle(dest)
                            result={'status':'cached','path':str(dest),**receipt}
                        else:result=client.fetch(url,dest,validate_bundle)
                        downloads[url]=result
                    if downloads[url]['status']=='failed':raise DownloadError(downloads[url]['error'])
                    # Catalogue-specific size failures must not poison another
                    # candidate that references the same verified artifact.
                    validator(Path(downloads[url]['path']))
                except (ValueError,OSError,DownloadError) as exc:
                    if url and url not in downloads:
                        downloads[url]={'status':'failed','url':url,'error':str(exc)}
                    success=False;variant['download_errors'].append(str(exc))
                atomic_json(out/'download_report.json',list(downloads.values()))
            variant['download_status']='verified' if success else 'failed'
            if args.render and success:
                try:
                    from gardens.render import render_bundle
                    from render_scene import load_item_manager
                    if not re.fullmatch(r'[A-Za-z0-9_-]+',row['scene_id']):raise ValueError('Unsafe scene output identifier')
                    if re.search(r'(FTD|STD|FTO)\d*$',row['scene_id'],re.I):raise ValueError('FTD/STD/FTO rendering is not implemented')
                    bundle_urls=sorted({b['url'] for b in bundles})
                    if len(bundle_urls)!=1:raise ValueError('Multi-bundle scene assembly is not certified yet')
                    scene=next(s for s in scenes if s.scene_id==row['scene_id'])
                    manager=load_item_manager(scene,out,client,args.offline)
                    report=render_bundle(Path(downloads[bundle_urls[0]]['path']),manager,destination,args.frame)
                    variant.update(render_status=report['status'],render_issues=report['issues'],files=report['files'])
                except (ValueError,OSError,DownloadError) as exc:
                    variant.update(render_status='incomplete',render_issues=[str(exc)])
            print(f"{row['scene_id']} variant {variant_id}: download={variant['download_status']}, render={variant['render_status']}",flush=True)
        variants=row['variants']
        row['download_status']='verified' if variants and all(v['download_status']=='verified' for v in variants) else 'failed'
        row['download_errors']=[error for v in variants for error in v['download_errors']]
        row['render_issues']=[f"{v['variant_id']}: {error}" for v in variants for error in v['render_issues']]
        if args.render:
            valid=sum(v['render_status']=='validated_snapshot' for v in variants)
            row['render_status']='validated_snapshot' if variants and valid==len(variants) else ('partial' if valid else 'incomplete')
            if re.fullmatch(r'[A-Za-z0-9_-]+',row['scene_id']):
                atomic_json(out/'scenes'/row['scene_id']/'variants.json',
                            {'scene_id':row['scene_id'],'match_status':row['status'],'render_status':row['render_status'],'variants':variants})

    atomic_json(out / 'scene_matches.json', matches)
    write_csv(out / 'scene_report.csv', matches, ['order', 'scene_id', 'thumbnail_url', 'item_manager_url', 'status', 'issues', 'download_status', 'download_errors', 'render_status', 'render_issues', 'variants', 'candidates'])
    atomic_json(out / 'download_report.json', list(downloads.values()))
    summary = {'run_id': run_id, 'status': 'completed', 'phase': 'download-and-render' if args.render else 'catalogue-index-and-download-only', 'images_generated': 3*sum(v['render_status']=='validated_snapshot' for r in matches for v in r['variants']),
               'catalogues_expected': len(urls), 'catalogues_indexed': sum(c['status'] == 'indexed' for c in catalogue_reports),
               'complete_catalogue_coverage': complete, 'scenes': len(scenes), 'duplicates_skipped': len(duplicates),
               'matches': dict(Counter(r['status'] for r in matches)), 'downloads': dict(Counter(d['status'] for d in downloads.values())),
               'render_statuses': dict(Counter(r['render_status'] for r in matches)),
               'variants_attempted':sum(len(r['variants']) for r in matches),
               'variant_render_statuses':dict(Counter(v['render_status'] for r in matches for v in r['variants'])),
               'limitations': ['Snapshot validation checks structure and metadata; independent game pixel parity is unverified.',
                               'Unity Hash128 is not a file MD5; Unity provider CRC is retained but not yet independently checked.']}
    atomic_json(out / 'summary.json', summary)
    print(json.dumps(summary, indent=2), flush=True)
    # A partial catalogue run cannot establish that a unique match is globally unique.
    return 0 if complete and all(r['status'] in ('matched','ambiguous') and not r['issues'] and all(not c['issues'] for c in r['candidates']) and (args.index_only or r['download_status'] == 'verified') and (not args.render or r['render_status']=='validated_snapshot') for r in matches) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, DownloadError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
