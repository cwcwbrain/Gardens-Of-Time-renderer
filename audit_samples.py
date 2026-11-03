#!/usr/bin/env python3
"""Reuse the supplied extractor for a repeatable real-bundle readiness audit."""
import argparse
import json
import sys
from pathlib import Path

from gardens.download import atomic_json, sha256, validate_bundle

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'legacy'))
from bundle_loader import extract_bundle
from renderer import parse_assets, scan_first_frame, unpack_gaf


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('bundles', nargs='+', type=Path)
    p.add_argument('--output', type=Path, default=ROOT / 'output/sample_audit.json')
    args = p.parse_args()
    reports = []
    for bundle in args.bundles:
        row = {'bundle': str(bundle), 'status': 'failed', 'reconstruction_status': 'incomplete'}
        try:
            validate_bundle(bundle)
            files, audit = extract_bundle(bundle)
            objects, assets, managers, atlases, errors = parse_assets(files)
            row.update(sha256=sha256(bundle), extraction=audit, gaf_objects=len(objects),
                       gaf_assets=len(assets), managers=list(managers),
                       atlases=[{'name': name, 'dimensions': list(im.size)} for name, im in atlases], errors=errors)
            if not assets or not atlases or not objects:
                raise ValueError('Required GAF assets, object definitions or atlases missing')
            ids = {int(obj['m_ID']) for _, obj in objects}
            placements, offset = scan_first_frame(unpack_gaf(assets[0][1]), ids)
            placed = {r['object_id'] for r in placements}
            row.update(status='extraction_verified', heuristic_placements=len(placements),
                       placement_offset=offset, unplaced_object_ids=sorted(ids-placed),
                       limitations=['First-frame scanner is heuristic; full timelines are not decoded.',
                                    'Atlas references and Item Manager identification still require validation.',
                                    'No final scene images certified.'])
        except Exception as exc:
            row['error'] = str(exc)
        reports.append(row)
        atomic_json(args.output, reports)
    print(json.dumps(reports, indent=2))
    return 0 if all(r['status'] == 'extraction_verified' for r in reports) else 1


if __name__ == '__main__':
    sys.exit(main())
