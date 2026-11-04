import json
import tempfile
import unittest
from pathlib import Path

from gardens.discovery import catalogue_sources,discover,production_catalogues
from gardens.download import DownloadError

FIXTURE=Path(__file__).parent/'fixtures/GOT_RemoteConfig_HO.json'
URL='https://cdn.test/config.json'


class Client:
    def __init__(self,raw=None,error=None):self.raw=raw;self.error=error;self.calls=0
    def fetch(self,url,path,validator):
        self.calls+=1
        if self.error:raise self.error
        path.write_bytes(self.raw);validator(path)
        return {'status':'downloaded'}


class DiscoveryTests(unittest.TestCase):
    def test_real_config_production_only_and_new_catalogue13(self):
        raw=json.loads(FIXTURE.read_text())
        extra={'catalogProjectName':'ISO 01','URLInfo_Prod':{'URL':'https://unrelated.test/','catalogName':'iso.json'}}
        raw['assetCatalogInfo'].append(extra)
        info=production_catalogues(json.dumps(raw))
        self.assertEqual(len(info['catalogues']),13)
        self.assertTrue(info['catalogues'][-1].endswith('catalog_13_p_2026.11.02.10.44.21.json'))
        self.assertTrue(all('/ho/' in url and 'prod-cdn-assets.' in url for url in info['catalogues']))
        self.assertEqual(info['excluded_projects'],['ISO 01'])

    def test_accepts_future_explicit_group_without_guessing(self):
        raw=json.loads(FIXTURE.read_text());entry=json.loads(json.dumps(raw['assetCatalogInfo'][0]))
        entry['catalogProjectName']='HO Scenes 14';entry['URLInfo_Prod']['catalogName']='explicit_new_name.json'
        raw['assetCatalogInfo'].append(entry)
        self.assertEqual(production_catalogues(json.dumps(raw))['catalogues'][-1],'https://prod-cdn-assets.gardensoftime-cloud.net/ho/explicit_new_name.json')

    def test_missing_prod_duplicate_groups_and_traversal_fail_closed(self):
        for change in ('missing','duplicate','traversal'):
            raw=json.loads(FIXTURE.read_text())
            if change=='missing':del raw['assetCatalogInfo'][0]['URLInfo_Prod']
            elif change=='duplicate':raw['assetCatalogInfo'].append(raw['assetCatalogInfo'][0])
            else:raw['assetCatalogInfo'][0]['URLInfo_Prod']['catalogName']='../elsewhere.json'
            with self.subTest(change=change),self.assertRaises(ValueError):production_catalogues(json.dumps(raw))

    def test_snapshot_offline_and_failed_refresh_keep_last_verified_config(self):
        with tempfile.TemporaryDirectory() as td:
            client=Client(FIXTURE.read_bytes());a=discover(URL,td,client)
            b=discover(URL,td,client,offline=True)
            self.assertEqual(client.calls,1);self.assertEqual(a['catalogues'],b['catalogues'])
            c=discover(URL,td,Client(error=DownloadError('unavailable')))
            self.assertEqual(c['source'],'cached_remote_config_fallback')
            self.assertEqual(c['config_sha256'],a['config_sha256'])
            Path(a['config_path']).write_bytes(b'changed')
            with self.assertRaises(ValueError):discover(URL,td,client,offline=True)

    def test_custom_manifest_has_no_implicit_network_and_fallback_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            client=Client(error=DownloadError('blocked'))
            manifest={'catalogues':['https://cdn.test/catalogue.json']}
            self.assertEqual(catalogue_sources(manifest,td,client)['source'],'manifest')
            self.assertEqual(client.calls,0)
            info=catalogue_sources(manifest,td,client,remote_config=URL)
            self.assertEqual(info['source'],'manifest_fallback');self.assertIn('blocked',info['warning'])
            self.assertEqual(info['catalogues'],manifest['catalogues'])


if __name__=='__main__':unittest.main()
