"""Variant routing uses synthetic catalogues with the real uploaded Giza bundle."""
import contextlib
import base64
import struct
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import gardens_renderer
from gardens.download import atomic_json,cache_name,sha256
from gardens.scenes import read_scenes
from test_pipeline import ROOT,SAMPLE,packed


class VariantTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.out=Path(self.temp.name)/'output';self.manifest=Path(self.temp.name)/'catalogues.json'
        self.sources=['https://catalogue.test/a.json','https://catalogue.test/b.json']
        self.urls=['https://assets.test/version-a.bundle','https://assets.test/version-b.bundle']
        self.manifest.write_text(json.dumps({'catalogues':self.sources}))
        for source,url in zip(self.sources,self.urls):
            self.seed(self.out/'catalogues'/cache_name(source,'.json'),source,packed(url))
            self.seed(self.out/'bundles'/cache_name(url,'.bundle'),url,SAMPLE.read_bytes())
        scene=read_scenes(ROOT/'data/Sheet2_chronology_fixed.csv')[0][0]
        self.seed(self.out/'item_managers'/cache_name(scene.item_manager_url,'.json'),scene.item_manager_url,
                  (ROOT/'tests/fixtures/AncientGizaRiverfront_items.json').read_bytes())
        self.args=['--scene','AncientGizaRiverfront','--offline','--catalogues',str(self.manifest),'--output',str(self.out)]

    def seed(self,path,url,data):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        atomic_json(str(path)+'.verified.json',{'url':url,'sha256':sha256(path)})

    def run_cli(self,render=False):
        with contextlib.redirect_stdout(io.StringIO()):
            code=gardens_renderer.main(self.args+(['--render'] if render else []))
        return code,json.loads((self.out/'scene_matches.json').read_text())[0],json.loads((self.out/'summary.json').read_text())

    def declared_render(self,destination):
        """An explicit synthetic renderer result for orchestration-only tests."""
        destination.mkdir(parents=True,exist_ok=True);files=[]
        for role in ('empty','complete','solution'):
            path=destination/('scene_'+role+'.png');Image.new('RGBA',(2,2),'cyan').save(path);files.append(str(path))
        report={'status':'validated_snapshot','issues':[],'files':files}
        atomic_json(destination/'validation_report.json',report)
        return report

    def test_all_variants_render_separately_and_ids_survive_manifest_order(self):
        code,row,summary=self.run_cli(render=True)
        self.assertEqual(code,0)
        self.assertEqual(row['status'],'ambiguous')
        self.assertEqual(row['render_status'],'validated_snapshot')
        self.assertEqual(summary['images_generated'],6)
        self.assertEqual(summary['variants_attempted'],2)
        folders={v['variant_id']:v['output'] for v in row['variants']}
        self.assertEqual(len(set(folders.values())),2)
        self.assertEqual(len(json.loads((self.out/'scenes/AncientGizaRiverfront/variants.json').read_text())['variants']),2)
        for variant in row['variants']:
            for name in ('scene_empty.png','scene_complete.png','scene_solution.png'):
                self.assertTrue((Path(variant['output'])/name).exists())
            self.assertEqual(json.loads((Path(variant['output'])/'validation_report.json').read_text())['named_pointers'],68)
            receipt=json.loads((Path(variant['output'])/'scene_images_receipt.json').read_text())
            self.assertEqual(receipt['scene_id'],'AncientGizaRiverfront');self.assertEqual(receipt['variant_id'],variant['variant_id'])
            self.assertEqual(len(receipt['images']),3)
        from gardens.scene_lookup import existing_scene_images
        self.assertEqual(len(existing_scene_images(self.out,'AncientGizaRiverfront')),2)
        self.manifest.write_text(json.dumps({'catalogues':list(reversed(self.sources))}))
        code,reordered,_=self.run_cli()
        self.assertEqual(code,0)
        self.assertEqual({v['variant_id']:v['output'] for v in reordered['variants']},folders)
        # A missing version is a failure, but does not stop the other version.
        (self.out/'bundles'/cache_name(self.urls[0],'.bundle')).unlink()
        code,failed,_=self.run_cli()
        self.assertEqual(code,1)
        self.assertEqual(sorted(v['download_status'] for v in failed['variants']),['failed','verified'])

    def test_bad_options_on_shared_url_do_not_block_valid_version(self):
        good=packed(self.urls[0]);bad=json.loads(good)
        opts=json.dumps({'m_BundleSize':SAMPLE.stat().st_size+1,'m_Crc':0}).encode('utf-16-le')
        bad['m_ExtraDataString']=base64.b64encode(b'\x07\x01A\x01C'+struct.pack('<i',len(opts))+opts).decode()
        self.seed(self.out/'catalogues'/cache_name(self.sources[0],'.json'),self.sources[0],json.dumps(bad).encode())
        self.seed(self.out/'catalogues'/cache_name(self.sources[1],'.json'),self.sources[1],good)
        with patch('gardens.render.render_bundle',side_effect=lambda bundle,manager,destination,frame:self.declared_render(destination)) as renderer:
            code,row,summary=self.run_cli(render=True)
        self.assertEqual(code,1)
        self.assertEqual(renderer.call_count,1)
        self.assertEqual(sorted(v['download_status'] for v in row['variants']),['failed','verified'])
        self.assertEqual(row['render_status'],'partial')
        self.assertEqual(summary['downloads'],{'cached':1})

    def test_one_incomplete_render_keeps_successful_sibling_and_returns_failure(self):
        def render(bundle,manager,destination,frame):
            if bundle.name==cache_name(self.urls[0],'.bundle'):
                return {'status':'incomplete','issues':['Unsupported test feature'],'files':[]}
            return self.declared_render(destination)
        with patch('gardens.render.render_bundle',side_effect=render) as renderer:
            code,row,summary=self.run_cli(render=True)
        self.assertEqual(renderer.call_count,2)
        self.assertEqual(code,1)
        self.assertEqual(row['render_status'],'partial')
        self.assertEqual(summary['images_generated'],3)
        self.assertEqual(summary['variant_render_statuses'],{'incomplete':1,'validated_snapshot':1})
        self.assertTrue(any('Unsupported test feature' in error for error in row['render_issues']))


if __name__=='__main__':unittest.main()
