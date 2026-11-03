#!/usr/bin/env python3
"""Render one explicit ordinary-scene bundle; unsupported results remain incomplete."""
import argparse
import json
import sys
from pathlib import Path

from gardens.download import Downloader, DownloadError, cache_name, read_json, sha256
from gardens.render import RenderError, render_bundle
from gardens.scenes import read_scenes

ROOT=Path(__file__).resolve().parent


def load_item_manager(scene, output, client, offline=False):
    url=scene.item_manager_url
    path=Path(output)/'item_managers'/cache_name(url,'.json')
    def validate(p):
        value=json.loads(p.read_text(encoding='utf-8-sig'))
        if not isinstance(value,dict) or not value: raise RenderError('Invalid or empty Item Manager JSON')
    if offline:
        receipt=read_json(str(path)+'.verified.json')
        if not path.exists() or receipt.get('url')!=url or receipt.get('sha256')!=sha256(path):
            raise DownloadError('No verified cached Item Manager; run once online or supply --item-manager')
        validate(path)
    else:
        client.fetch(url,path,validate)
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scene',required=True)
    p.add_argument('--bundle',required=True,type=Path)
    p.add_argument('--scenes',type=Path,default=ROOT/'data/Sheet2_chronology_fixed.csv')
    p.add_argument('--output',type=Path,default=ROOT/'output/rendered')
    p.add_argument('--item-manager',type=Path,help='Explicit local Item Manager JSON; otherwise fetched from CSV URL')
    p.add_argument('--offline',action='store_true')
    p.add_argument('--frame',type=int,help='Exact one-based GAF frame; default picks one coherent snapshot with maximum object visibility')
    args=p.parse_args(argv)
    scenes,_=read_scenes(args.scenes)
    matches=[s for s in scenes if s.scene_id.casefold()==args.scene.casefold()]
    if len(matches)!=1: p.error('Scene must identify one row in the corrected CSV')
    scene=matches[0]
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]+',scene.scene_id):p.error('Unsafe scene output identifier')
    if re.search(r'(FTD|STD|FTO)\d*$',scene.scene_id,re.I):p.error('This renderer currently supports ordinary scenes only')
    data=json.loads(args.item_manager.read_text(encoding='utf-8-sig')) if args.item_manager else load_item_manager(scene,args.output,Downloader(),args.offline)
    result=render_bundle(args.bundle,data,args.output/scene.scene_id,args.frame)
    print(json.dumps(result,indent=2))
    return 0 if result['status']=='validated_snapshot' else 1


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError,DownloadError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);sys.exit(2)
