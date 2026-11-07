#!/usr/bin/env python3
"""Catalogue indexing, bundle downloading, and opt-in ordinary-scene snapshots."""
import argparse
import csv
import json
import re
import sqlite3
import sys
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from gardens.catalogue import Index, parse_catalogue
from gardens.download import Downloader, DownloadError, atomic_json, cache_name, read_json, sha256, validate_bundle
from gardens.scenes import Scene, match_scenes, read_scenes, scene_kind, variant_id as candidate_variant_id

ROOT = Path(__file__).resolve().parent


def write_csv(path, rows, fields):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(row[key], ensure_ascii=False) if isinstance(row.get(key), (list, dict)) else row.get(key, '') for key in fields})


def match_scene_names(args,client,discovery,scenes):
    """Report official names for the complete roster, regardless of render status."""
    from gardens.names import load_official_names
    out=args.output.resolve();checkpoint=out/'batch/tests.sqlite3'
    merged={scene.scene_id.casefold():scene for scene in scenes}
    if checkpoint.is_file():
        try:
            with sqlite3.connect(checkpoint.as_uri()+'?mode=ro',uri=True) as db:
                db.row_factory=sqlite3.Row
                for row in db.execute('SELECT * FROM jobs WHERE active=1 ORDER BY ordinal,variant_id'):
                    data=json.loads(row['definition'])['scene']
                    merged[row['scene_id'].casefold()]=Scene(row['ordinal'],row['scene_id'],data.get('thumbnail_url',''),
                                                            data.get('item_manager_url',''),data.get('original_row',[]))
        except sqlite3.Error as exc:
            from gardens.checkpoint import checkpoint_error
            raise checkpoint_error(checkpoint,exc,'read saved scene identities') from exc
    roster=sorted(merged.values(),key=lambda s:s.order)
    names=load_official_names(out,client,discovery.get('config_path'),roster,args.offline)
    mappings=names.get('mappings',{})
    problems={entry['scene_id']:entry.get('issues',[]) for entry in names.get('issues',[]) if isinstance(entry,dict) and 'scene_id' in entry}
    rows=[]
    for scene in roster:
        mapping=mappings.get(scene.scene_id)
        matched=(isinstance(mapping,dict) and
                 all(isinstance(mapping.get(key),str) and mapping[key].strip() for key in ('title','scene_title','chapter_title')) and
                 isinstance(mapping.get('chapter_number'),int) and not isinstance(mapping.get('chapter_number'),bool) and mapping['chapter_number']>0)
        value=mapping if matched else {}
        keys=value.get('localization_keys',{})
        rows.append({'order':scene.order,'scene_id':scene.scene_id,'match_status':'matched' if matched else 'unmatched',
                     'in_game_name':value.get('title',''),'scene_title':value.get('scene_title',''),
                     'chapter_number':value.get('chapter_number',''),'chapter_title':value.get('chapter_title',''),
                     'mission_order':value.get('mission_order',''),'mission_id':value.get('mission_id',''),
                     'story_key':value.get('story_key',''),'localization_scene_key':keys.get('scene',''),
                     'localization_mission_key':keys.get('mission',''),'localization_story_key':keys.get('story',''),
                     'replay_aliases':value.get('primary_appearances',[])[1:],
                     'issues':[] if matched else (problems.get(scene.scene_id) or names.get('source_issues') or ['No verified official name match'])})
    matched=sum(row['match_status']=='matched' for row in rows)
    if matched!=len(rows) and names.get('status')=='available':names={**names,'status':'partial'}
    atomic_json(out/'scene_names.json',names)
    target=out/'scene_name_matches.csv';temporary=target.with_name(target.name+'.tmp')
    fields=['order','scene_id','match_status','in_game_name','scene_title','chapter_number','chapter_title',
            'mission_order','mission_id','story_key','localization_scene_key','localization_mission_key',
            'localization_story_key','replay_aliases','issues']
    write_csv(temporary,rows,fields)
    try:temporary.replace(target)
    except PermissionError as exc:
        raise ValueError(f'Close {target} in Excel and retry; the new lookup is saved at {temporary}. Saved scene results were not changed.') from exc
    summary={'requested_scenes':len(rows),'matched_scenes':matched,'unmatched_scenes':len(rows)-matched}
    report={'schema':1,'status':names.get('status','unavailable'),'csv_path':str(target),
            'summary':summary,'source_issues':names.get('source_issues',[]),'provenance':names.get('provenance',{})}
    atomic_json(out/'scene_name_match_report.json',report)
    print('Official scene-name matches: '+json.dumps({'status':report['status'],**summary}),flush=True)
    return names


def name_scene_exports(args,client,discovery,scenes):
    """Export readable copies without changing original paths or checkpoints."""
    from gardens.named_export import export_named
    out=args.output.resolve()
    names=match_scene_names(args,client,discovery,scenes)
    exported=export_named(out,names.get('mappings',{}),provenance=names.get('provenance',{}),destination=args.named_output)
    report={'names_status':names['status'],'mapping_summary':names.get('summary',{}),
            'source_issues':names.get('source_issues',[]),'export':exported}
    atomic_json(out/'naming_report.json',report)
    print('Official-name export: '+json.dumps({'status':exported['status'],**exported.get('summary',{})}),flush=True)
    return 0 if names['status']!='unavailable' and exported['status']=='completed' else 1


def main(argv=None):
    arguments=list(sys.argv[1:] if argv is None else argv)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--catalogues', type=Path, default=ROOT / 'catalogues.json')
    p.add_argument('--remote-config', help='Override the game loading-config URL for production HO catalogue discovery')
    p.add_argument('--scenes', type=Path, default=ROOT / 'data/Sheet2_chronology_fixed.csv')
    select = p.add_mutually_exclusive_group(required=True)
    select.add_argument('--all', action='store_true')
    select.add_argument('--test-all', action='store_true', help='Test every scene/version with isolated workers and resumable reports')
    select.add_argument('--retry-incomplete', action='store_true', help='Retry only incomplete versions from the existing output checkpoint; preserve all other results')
    select.add_argument('--update', action='store_true', help='Discover new chapters/versions and retry incomplete ones while preserving prior passing outputs')
    select.add_argument('--finish-all', action='store_true', help='Update/resume, match official names, organize exports and delete verified obsolete previews; keep Windows awake during the run')
    select.add_argument('--export-named', action='store_true', help='Export passing images with official chapter-list names, without rendering scenes')
    select.add_argument('--match-names', action='store_true', help='Match every roster scene to official in-game titles; write a CSV without rendering or copying images')
    select.add_argument('--relocate-output', action='store_true', help='Rebase a copied output checkpoint to this folder after moving computers; verify existing artifacts first')
    select.add_argument('--scene', action='append', help='Exact scene ID; may be repeated')
    select.add_argument('--find-scene', nargs='?', const='', metavar='NAME', help='Find an in-game name or scene ID; reuse saved images or recover missing images into output/misc. Omit NAME for a prompt')
    p.add_argument('--output', type=Path, default=ROOT / 'output')
    p.add_argument('--open', dest='open_folder', action='store_true', help='With --find-scene, open the folder containing verified images')
    p.add_argument('--named-output', type=Path, help='Destination for official-name copies (default: output/named_scenes); used by --update or --export-named')
    p.add_argument('--render', action='store_true', help='Render selected ordinary scenes; --all uses the resumable isolated test runner')
    p.add_argument('--test-timeout', type=float, default=300, help='Maximum seconds per variant test worker (default 300)')
    p.add_argument('--test-limit', type=int, help='Process at most this many selected scenes per test invocation')
    p.add_argument('--retry-failed', action='store_true', help='Retry non-passing checkpoints in test mode')
    p.add_argument('--include-pending', action='store_true', help='With --update, also resume saved pending/running jobs after an interrupted run or move')
    p.add_argument('--refresh-solutions', action='store_true', help='With --update or --finish-all, upgrade saved passing solutions to the current pointer layout while preserving empty and complete images')
    p.add_argument('--refresh-poses', action='store_true', help='With --update, review saved passing nested animations and replace only images changed by the general pose selector; included in --finish-all')
    p.add_argument('--plan-only', action='store_true', help='Export the test or retry plan without starting workers')
    p.add_argument('--frame', type=int, help='One-based snapshot frame for --render')
    p.add_argument('--index-only', action='store_true', help='Download and index catalogues; do not download bundles')
    p.add_argument('--offline', action='store_true', help='Read verified cached catalogues and bundles only')
    p.add_argument('--refresh-catalogues', action='store_true')
    p.add_argument('--timeout', type=float, default=30)
    p.add_argument('--retries', type=int, default=2)
    p.add_argument('--property', action='append', default=[], metavar='NAME=VALUE', help='Verified Unity runtime property; never guessed')
    args = p.parse_args(arguments)
    if args.find_scene is not None:
        if (args.render or args.index_only or args.plan_only or args.retry_failed or args.include_pending or
                args.refresh_solutions or args.refresh_poses or args.named_output is not None or args.test_limit is not None):
            p.error('--find-scene handles only the requested name; do not combine it with batch, render or export options')
        if args.timeout<=0 or args.retries<0 or args.test_timeout<=0 or (args.frame is not None and args.frame<1):
            p.error('Scene lookup timeout/frame must be positive and retries non-negative')
        if args.offline and args.refresh_catalogues:p.error('--offline cannot refresh catalogues')
        from gardens.scene_finder import run_finder
        return run_finder(args)
    if args.open_folder:p.error('--open requires --find-scene')
    if args.finish_all:
        args.update=True
        args.include_pending=True
        args.refresh_poses=True
    if args.include_pending and not args.update:
        p.error('--include-pending requires --update')
    if args.refresh_solutions and not args.update:
        p.error('--refresh-solutions requires --update or --finish-all')
    if args.refresh_poses and not args.update:
        p.error('--refresh-poses requires --update or --finish-all')
    if args.relocate_output:
        if args.render or args.index_only or args.retry_failed or args.named_output or args.frame is not None or args.test_limit is not None:
            p.error('--relocate-output verifies and rebases saved paths; use --output and optionally --plan-only')
        from gardens.relocation import relocate_output
        report=relocate_output(args.output,dry_run=args.plan_only)
        print(json.dumps(report,indent=2),flush=True)
        return 0
    if args.named_output is not None and not (args.update or args.export_named):
        p.error('--named-output requires --update or --export-named')
    if args.export_named and (args.render or args.index_only):
        p.error('--export-named copies validated images; do not combine it with --render or --index-only')
    if args.match_names and (args.render or args.index_only or args.retry_failed):
        p.error('--match-names reads official names only; do not combine it with render, index or retry options')
    if args.test_all or args.retry_incomplete or args.update: args.render=True
    batch_mode=args.test_all or args.retry_incomplete or args.update or (args.all and args.render)
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
    if args.retry_incomplete:
        if args.retry_failed or args.refresh_catalogues or args.remote_config or args.property:
            p.error('--retry-incomplete uses the saved roster; do not combine it with --retry-failed or catalogue overrides')
        from gardens.checkpoint import require_checkpoint
        require_checkpoint(args.output)
        from gardens.batch import retry_incomplete
        return retry_incomplete(args)
    if args.update or args.export_named:
        if args.retry_failed:p.error('--update preserves previous failed results; do not combine it with --retry-failed')
        if not (args.output.resolve()/'batch/tests.sqlite3').is_file():
            p.error('--update/--export-named requires the same --output folder and batch/tests.sqlite3 from the previous run')
        from gardens.checkpoint import require_checkpoint
        require_checkpoint(args.output,writable=args.update)
    if args.match_names and (args.output.resolve()/'batch/tests.sqlite3').exists():
        from gardens.checkpoint import require_checkpoint
        require_checkpoint(args.output,writable=False)
    properties = {}
    for item in args.property:
        key, sep, value = item.partition('=')
        if not sep or not key:
            p.error('--property requires NAME=VALUE')
        properties[key] = value
    out = args.output.resolve()
    if args.finish_all:
        from gardens.finish import finish_all
        forwarded=['--update' if value=='--finish-all' else value for value in arguments]
        if '--include-pending' not in forwarded:forwarded.append('--include-pending')
        if '--refresh-poses' not in forwarded:forwarded.append('--refresh-poses')
        return finish_all(out,lambda:main(forwarded),plan_only=args.plan_only)
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
    if args.export_named:
        return name_scene_exports(args,client,discovery,scenes)
    if args.match_names:
        names=match_scene_names(args,client,discovery,scenes)
        return 0 if names.get('status')=='available' else 1
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
                result = client.fetch(url, dest, validate, args.refresh_catalogues or args.update)
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
    if args.update and complete:
        from gardens.chapters import discover_chapters
        scenes,chapter_report=discover_chapters(scenes,index,out,client,discovery.get('config_path'),args.offline)
    elif args.update:
        chapter_report={'status':'unavailable','issues':['Catalogue coverage is incomplete; no new chapters or versions enrolled'],'unverified_scenes':[]}
    matches = match_scenes(scenes, index, complete)
    if args.update:
        from gardens.incremental import enroll_updates
        from gardens.batch import retry_incomplete
        # Missing metadata can reserve a chronological position but must never
        # become an eligible scene worker or a successful reconstruction.
        unverified={entry['scene_id']:entry for entry in chapter_report.get('unverified_scenes',[])}
        for row in matches:
            if row['scene_id'] in unverified:
                row['issues'].extend(unverified[row['scene_id']].get('issues',['Item Manager metadata is not verified']))
                row['status']='unresolved'
        atomic_json(out/'chapter_discovery.json',chapter_report)
        atomic_json(out/'update_scene_matches.json',matches)
        enrollment=enroll_updates(out,scenes,matches,complete=complete)
        update={**enrollment,'chapter_discovery':chapter_report,'catalogue_source':discovery['source']}
        atomic_json(out/'update_discovery.json',update)
        code=retry_incomplete(args,update_metadata=update)
        if args.refresh_poses and code not in (2,130):
            from gardens.pose_refresh import refresh_poses
            refreshed=refresh_poses(out,timeout=args.test_timeout,plan_only=args.plan_only)
            if not args.plan_only:
                if refreshed['status']=='interrupted':code=130
                elif refreshed['status']!='completed':code=code or 1
        if not args.plan_only and code not in (2,130) and args.refresh_solutions:
            from gardens.solution_refresh import refresh_solutions
            refreshed=refresh_solutions(out,timeout=args.test_timeout)
            if refreshed['status']=='interrupted':code=130
            elif refreshed['status']!='completed':code=code or 1
        if not args.plan_only and code!=130:
            naming_code=name_scene_exports(args,client,discovery,scenes)
            if not code:code=naming_code
        discovery_ok=(complete and chapter_report.get('status')!='unavailable'
                      and not chapter_report.get('unverified_scenes') and not enrollment.get('blocked_update_variants'))
        return code if args.plan_only or discovery_ok else (code or 1)
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
                    if scene_kind(row['scene_id'])!='ordinary':raise ValueError('FTD/STD/FTO rendering is not implemented')
                    bundle_urls=sorted({b['url'] for b in bundles})
                    if len(bundle_urls)!=1:raise ValueError('Multi-bundle scene assembly is not certified yet')
                    scene=next(s for s in scenes if s.scene_id==row['scene_id'])
                    manager=load_item_manager(scene,out,client,args.offline)
                    report=render_bundle(Path(downloads[bundle_urls[0]]['path']),manager,destination,args.frame)
                    variant.update(render_status=report['status'],render_issues=report['issues'],files=report['files'])
                    if report['status']=='validated_snapshot':
                        atomic_json(destination/'scene_images_receipt.json',
                                    {'schema':1,'scene_id':row['scene_id'],'variant_id':variant_id,'status':'passed',
                                     'images':{Path(file).name:sha256(file) for file in report['files']},
                                     'validation_report_sha256':sha256(destination/'validation_report.json')})
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
