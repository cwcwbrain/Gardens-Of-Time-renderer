"""Isolated single-variant test worker. Invoked by gardens.batch, not a shell."""
import argparse
import json
import re
import sys
import traceback
from pathlib import Path

from .download import Downloader,DownloadError,atomic_json,cache_name,read_json,sha256,validate_bundle
from .scenes import Scene,scene_kind


def test_job(job):
    from .render import render_bundle
    from render_scene import load_item_manager
    scene=Scene(**job['scene']);candidate=job['candidate'];out=Path(job['output'])
    client=Downloader(job['timeout'],job['retries'])
    result={'status':'failed','issues':[],'files':[],'artifacts':{},'stage':'download','bundle_count':0}
    def cached_root(folder,url,extension):
        # Finder jobs may borrow verified main-cache inputs without copying or
        # modifying them. Ordinary batch jobs keep their original cache policy.
        for raw in job.get('read_cache_roots',[]):
            root=Path(raw).resolve();path=root/folder/cache_name(url,extension)
            receipt=read_json(str(path)+'.verified.json')
            try:
                if path.is_file() and receipt.get('url')==url and receipt.get('sha256')==sha256(path):return root
            except OSError:continue
        return None
    def keep(path):
        path=Path(path).resolve();result['artifacts'][str(path)]=sha256(path)
    try:
        paths={}
        for bundle in candidate['bundles']:
            url=bundle['url']
            if not url:raise DownloadError('Unresolved bundle URL')
            dest=out/'bundles'/cache_name(url,'.bundle')
            borrowed=cached_root('bundles',url,'.bundle')
            if borrowed is not None:
                dest=borrowed/'bundles'/cache_name(url,'.bundle')
                validate_bundle(dest)
            elif job['offline']:
                receipt=read_json(str(dest)+'.verified.json')
                if not dest.exists() or receipt.get('url')!=url or receipt.get('sha256')!=sha256(dest):
                    raise DownloadError('No verified cached bundle: '+url)
                validate_bundle(dest)
            else:client.fetch(url,dest,validate_bundle)
            size=bundle['options'].get('m_BundleSize') or bundle['options'].get('BundleSize')
            validate_bundle(dest,int(size) if size else None)
            keep(dest);paths[url]=dest
        result['bundle_count']=len(paths);result['stage']='item_manager'
        manager_root=cached_root('item_managers',scene.item_manager_url,'.json')
        manager=load_item_manager(scene,manager_root or out,client,job['offline'] or manager_root is not None)
        manager_path=(manager_root or out)/'item_managers'/cache_name(scene.item_manager_url,'.json');keep(manager_path)
        result['stage']='render'
        kind=scene_kind(scene.scene_id)
        if kind=='ordinary' and str(job.get('primary_default_mode'))=='8':kind='STD'
        if kind!='ordinary':
            result.update(status='incomplete',issues=[f'{kind} rendering is not implemented; assets and Item Manager checked'])
        elif len(paths)!=1:
            result.update(status='incomplete',issues=['Multi-bundle scene assembly is not certified; assets and Item Manager checked'])
        else:
            report=render_bundle(next(iter(paths.values())),manager,Path(job['destination']),job['frame'])
            result.update(status='passed' if report['status']=='validated_snapshot' else 'incomplete',
                          issues=report['issues'],files=report['files'],
                          hidden_objects=report['identified_hidden_objects'],named_pointers=report['named_pointers'],
                          unverified_named_pointers=report.get('unverified_named_pointers',0),
                          total_named_pointers=report.get('total_named_pointers',report['named_pointers']),
                          solution_layout=report.get('solution_layout'),annotation_issues=report.get('annotation_issues',[]),
                          warnings=report.get('warnings',[]),item_manager_complete=report.get('item_manager_complete'))
            # Pass reuse requires unchanged inputs, images, and validation/label metadata.
            if result['status']=='passed':
                for file in report['files']:keep(file)
                for name in ('validation_report.json','objects.json','solution_objects.json'):
                    keep(Path(job['destination'])/name)
        result['stage']='completed'
    except (DownloadError,OSError) as exc:
        result.update(status='failed',issues=[f'{type(exc).__name__}: {exc}'])
    except ValueError as exc:
        result.update(status='incomplete',issues=[f'{type(exc).__name__}: {exc}'])
    except Exception as exc:
        traceback.print_exc()
        result.update(status='failed',issues=[f'{type(exc).__name__}: {exc}'])
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job',required=True,type=Path);p.add_argument('--result',required=True,type=Path)
    args=p.parse_args();job=json.loads(args.job.read_text(encoding='utf-8'))
    result=test_job(job);result['fingerprint']=job['fingerprint']
    atomic_json(args.result,result)
    print(json.dumps({k:v for k,v in result.items() if k!='artifacts'}),flush=True)
    return 0 if result['status']=='passed' else (1 if result['status']=='incomplete' else 2)


if __name__=='__main__':sys.exit(main())
