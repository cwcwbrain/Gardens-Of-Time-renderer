#!/usr/bin/env python3
"""Clean obsolete incomplete previews from verified completed scene versions."""
import argparse
import json
import sys
from pathlib import Path

from gardens.cleanup import cleanup_incomplete


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'output')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--delete',action='store_true',help='Delete the listed obsolete previews after verifying finished scene files')
    mode.add_argument('--dry-run',action='store_true',help='Preview only (the default)')
    args=parser.parse_args(argv)
    report=cleanup_incomplete(args.output,delete=args.delete)
    print(json.dumps({'status':report['status'],**report['summary'],'report':report['report_path']},indent=2),flush=True)
    if not args.delete:print('Preview only. Add --delete to remove eligible incomplete previews.',flush=True)
    return 130 if report['status']=='interrupted' else (1 if report['status']=='partial' else 0)


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);sys.exit(2)
