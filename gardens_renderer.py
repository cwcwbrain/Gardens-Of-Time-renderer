#!/usr/bin/env python3
"""Phase 1: catalogue indexing, exact matching, validated bundle downloads.

This CLI deliberately does not claim to reconstruct images yet.
"""
import argparse
import csv
import json
import sys
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from gardens.catalogue import Index, parse_catalogue
from gardens.download import Downloader, DownloadError, atomic_json, cache_name, read_json, sha256, validate_bundle
from gardens.scenes import match_scenes, read_scenes

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
    p.add_argument('--scenes', type=Path, default=ROOT / 'data/Sheet2_chronology_fixed.csv')
    select = p.add_mutually_exclusive_group(required=True)
    select.add_argument('--all', action='store_true')
    select.add_argument('--scene', action='append', help='Exact scene ID; may be repeated')
    p.add_argument('--output', type=Path, default=ROOT / 'output')
    p.add_argument('--index-only', action='store_true', help='Download and index catalogues; do not download bundles')
    p.add_argument('--offline', action='store_true', help='Read verified cached catalogues and bundles only')
    p.add_argument('--refresh-catalogues', action='store_true')
    p.add_argument('--timeout', type=float, default=30)
    p.add_argument('--retries', type=int, default=2)
    p.add_argument('--property', action='append', default=[], metavar='NAME=VALUE', help='Verified Unity runtime property; never guessed')
    args = p.parse_args(argv)
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
    urls = manifest.get('catalogues')
    if not isinstance(urls, list) or not urls or not all(isinstance(u, str) and urlsplit(u).scheme == 'https' and urlsplit(u).netloc for u in urls):
        p.error('catalogues.json must contain a nonempty catalogues list of explicit HTTPS URLs')
    if len(set(urls)) != len(urls):
        p.error('Duplicate catalogue URLs in manifest')
    client = Downloader(args.timeout, args.retries)
    run_id = uuid.uuid4().hex
    atomic_json(out / 'summary.json', {'run_id': run_id, 'status': 'in_progress'})
    atomic_json(out / 'download_report.json', [])
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
    downloads = {}
    for row in matches:
        row['download_status'] = 'not_requested' if args.index_only else 'not_eligible'
        row['download_errors'] = []
        if args.index_only or row['status'] != 'matched':
            continue
        # Only a unique, resolved candidate is eligible. Never choose among variants.
        bundles = row['candidates'][0]['bundles']
        success = True
        for bundle in bundles:
            url = bundle['url']
            if url not in downloads:
                dest = out / 'bundles' / cache_name(url, '.bundle')
                size = bundle['options'].get('m_BundleSize') or bundle['options'].get('BundleSize')
                try:
                    size = int(size) if size else None
                    if size is not None and size < 0:
                        raise ValueError('Negative catalogue bundle size')
                    validator = lambda path: validate_bundle(path, size)
                    if args.offline:
                        receipt = read_json(str(dest) + '.verified.json')
                        if not dest.exists() or receipt.get('url') != url or receipt.get('sha256') != sha256(dest):
                            raise DownloadError('No verified cached bundle')
                        validator(dest)
                        result = {'status': 'cached', 'path': str(dest), **receipt}
                    else:
                        result = client.fetch(url, dest, validator)
                    downloads[url] = result
                except (ValueError, OSError, DownloadError) as exc:
                    downloads[url] = {'status': 'failed', 'url': url, 'error': str(exc)}
                atomic_json(out / 'download_report.json', list(downloads.values()))
            if downloads[url]['status'] == 'failed':
                success = False
                row['download_errors'].append(downloads[url]['error'])
        row['download_status'] = 'verified' if success else 'failed'
    atomic_json(out / 'scene_matches.json', matches)
    write_csv(out / 'scene_report.csv', matches, ['order', 'scene_id', 'thumbnail_url', 'item_manager_url', 'status', 'issues', 'download_status', 'download_errors', 'candidates'])
    atomic_json(out / 'download_report.json', list(downloads.values()))
    summary = {'run_id': run_id, 'status': 'completed', 'phase': 'catalogue-index-and-download-only', 'images_generated': 0,
               'catalogues_expected': len(urls), 'catalogues_indexed': sum(c['status'] == 'indexed' for c in catalogue_reports),
               'complete_catalogue_coverage': complete, 'scenes': len(scenes), 'duplicates_skipped': len(duplicates),
               'matches': dict(Counter(r['status'] for r in matches)), 'downloads': dict(Counter(d['status'] for d in downloads.values())),
               'limitations': ['No image reconstruction is certified by this stage.',
                               'Unity Hash128 is not a file MD5; Unity provider CRC is retained but not yet independently checked.']}
    atomic_json(out / 'summary.json', summary)
    print(json.dumps(summary, indent=2), flush=True)
    # A partial catalogue run cannot establish that a unique match is globally unique.
    return 0 if complete and all(r['status'] == 'matched' and (args.index_only or r['download_status'] == 'verified') for r in matches) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, DownloadError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
