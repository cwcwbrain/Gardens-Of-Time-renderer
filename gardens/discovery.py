"""Discover production hidden-object catalogues from the game's loading config."""
import json
import re
import uuid
from pathlib import Path,PurePosixPath
from urllib.parse import urljoin,urlsplit

from .download import DownloadError,atomic_json,cache_name,read_json,sha256

DEFAULT_REMOTE_CONFIG='https://prod-cdn-assets.gardensoftime-cloud.net/unity6/webgl/StreamingAssets/GOT_RemoteConfig.json'


def production_catalogues(raw):
    doc=json.loads(raw)
    if not isinstance(doc,dict) or not isinstance(doc.get('assetCatalogInfo'),list):
        raise ValueError('Remote config has no assetCatalogInfo array')
    selected={};excluded=[]
    for entry in doc['assetCatalogInfo']:
        if not isinstance(entry,dict):raise ValueError('Malformed catalogue config entry')
        name=entry.get('catalogProjectName','')
        match=re.fullmatch(r'HO Scenes\s+(\d+)',name) if isinstance(name,str) else None
        if not match:
            excluded.append(name);continue
        number=int(match[1]);prod=entry.get('URLInfo_Prod')
        if not isinstance(prod,dict):raise ValueError(f'{name} has no production URLInfo_Prod')
        base,filename=prod.get('URL'),prod.get('catalogName')
        if not isinstance(base,str) or urlsplit(base).scheme!='https' or not urlsplit(base).netloc:
            raise ValueError(f'{name} has an invalid production base URL')
        if not isinstance(filename,str) or not filename:
            raise ValueError(f'{name} has no production catalogue filename')
        parsed=urlsplit(filename)
        if parsed.scheme or parsed.netloc or filename.startswith(('/', '\\')) or '\\' in filename or '..' in PurePosixPath(parsed.path).parts:
            raise ValueError(f'{name} catalogue filename is not a relative path')
        url=urljoin(base.rstrip('/')+'/',filename)
        if number in selected:raise ValueError(f'Duplicate hidden-object catalogue group {number}')
        selected[number]={'project':name,'url':url,'config_field':'URLInfo_Prod'}
    if not selected:raise ValueError('No production HO Scenes catalogues found')
    projects=[selected[n] for n in sorted(selected)]
    urls=[entry['url'] for entry in projects]
    if len(set(urls))!=len(urls):raise ValueError('Duplicate discovered production catalogue URL')
    return {'catalogues':urls,'projects':projects,'excluded_projects':excluded}


def discover(url,out,client,offline=False):
    if not isinstance(url,str) or urlsplit(url).scheme!='https' or not urlsplit(url).netloc:
        raise ValueError('Remote config URL must use HTTPS')
    folder=Path(out)/'remote_config'/cache_name(url);folder.mkdir(parents=True,exist_ok=True)
    pointer=folder/'current.json'
    def cached():
        entry=read_json(pointer)
        filename=entry.get('file') if isinstance(entry,dict) else None
        if not isinstance(filename,str) or Path(filename).name!=filename:raise ValueError('No verified cached remote config')
        path=folder/filename
        if entry.get('url')!=url or not path.is_file() or entry.get('sha256')!=sha256(path):
            raise ValueError('Cached remote config integrity check failed')
        return production_catalogues(path.read_bytes()),path,entry['sha256']
    if offline:
        info,path,digest=cached()
        return {**info,'source':'cached_remote_config','remote_config_url':url,'config_path':str(path),'config_sha256':digest}
    path=folder/('snapshot_'+uuid.uuid4().hex+'.json')
    try:
        client.fetch(url,path,lambda p:production_catalogues(p.read_bytes()))
        info=production_catalogues(path.read_bytes());digest=sha256(path)
        atomic_json(pointer,{'url':url,'file':path.name,'sha256':digest})
        return {**info,'source':'live_remote_config','remote_config_url':url,'config_path':str(path),'config_sha256':digest}
    except (ValueError,OSError,DownloadError) as exc:
        # A failed refresh must not destroy the previous validated snapshot.
        try:info,path,digest=cached()
        except (ValueError,OSError):raise exc
        return {**info,'source':'cached_remote_config_fallback','remote_config_url':url,'config_path':str(path),
                'config_sha256':digest,'warning':str(exc)}


def catalogue_sources(manifest,out,client,offline=False,remote_config=None):
    source=remote_config or manifest.get('remote_config')
    if source:
        try:return discover(source,out,client,offline)
        except (ValueError,OSError,DownloadError) as exc:
            return {'source':'manifest_fallback','remote_config_url':source,'warning':str(exc),'catalogues':manifest.get('catalogues')}
    return {'source':'manifest','catalogues':manifest.get('catalogues')}
