"""Fixtures are synthetic Addressables records; bundle payloads are user samples.
These tests do not certify the live CDN catalogue format or global matches.
"""
import base64
import io
import json
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from gardens.catalogue import CatalogueError, Index, Location, parse_catalogue, read_object
from gardens.download import Downloader, DownloadError, atomic_json, sha256, validate_bundle
from gardens.scenes import Scene, match_scenes, read_scenes

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = next((ROOT / 'samples').glob('ancient*.bundle'))


def packed(url, scene='AncientGizaRiverfront', dependency='bundle-key', broken_dep=False):
    keys = [scene, dependency]
    key_data = bytearray(struct.pack('<i', len(keys)))
    offsets = []
    for k in keys:
        b = k.encode('ascii'); offsets.append(len(key_data))
        key_data.extend(b'\x00' + struct.pack('<i', len(b)) + b)
    buckets = struct.pack('<i', 2) + b''.join(struct.pack('<iii', offset, 1, i) for i, offset in enumerate(offsets))
    if broken_dep:
        buckets = struct.pack('<i', 2) + struct.pack('<iii', offsets[0], 1, 0) + struct.pack('<ii', offsets[1], 0)
    options = json.dumps({'m_BundleSize': SAMPLE.stat().st_size, 'm_Crc': 0}).encode('utf-16-le')
    extra = b'\x07\x01A\x01C' + struct.pack('<i', len(options)) + options
    entries = struct.pack('<i', 2)
    entries += struct.pack('<7i', 0, 0, 1, 0, -1, 0, 0)
    entries += struct.pack('<7i', 1, 1, -1, 0, 0, 1, 0)
    b64 = lambda b: base64.b64encode(b).decode()
    return json.dumps({'m_InternalIds': [scene + '.prefab', url],
                       'm_ProviderIds': ['BundledAssetProvider', 'AssetBundleProvider'],
                       'm_KeyDataString': b64(key_data), 'm_BucketDataString': b64(buckets),
                       'm_EntryDataString': b64(entries), 'm_ExtraDataString': b64(extra)}) .encode()


class CatalogueTests(unittest.TestCase):
    def test_real_csv_first_scene_and_order(self):
        scenes, dupes = read_scenes(ROOT / 'data/Sheet2_chronology_fixed.csv')
        self.assertEqual(len(scenes), 4481)
        self.assertEqual(scenes[0].scene_id, 'AncientGizaRiverfront')
        self.assertEqual(scenes[1].scene_id, 'VictorianExteriorRedux1')
        self.assertEqual(scenes[-1].order, 4481)
        self.assertEqual(dupes, [])

    def test_replays_preserve_first_and_original_row(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / 'scenes.csv'
            p.write_text('scene_id,thumbnail_url,item_manager_url\nA,t,m\nB,u,n\na,replay,new\n')
            scenes, dupes = read_scenes(p)
            self.assertEqual([s.scene_id for s in scenes], ['A', 'B'])
            self.assertEqual(dupes[0]['original_row'], ['a', 'replay', 'new'])

    def test_packed_dependency_and_options(self):
        locations, info = parse_catalogue(packed('https://cdn.test/a.bundle'), 'https://cdn.test/catalog.json')
        bundles, issues = Index(locations).bundle_closure([locations[0].id])
        self.assertEqual([b.url for b in bundles], ['https://cdn.test/a.bundle'])
        self.assertEqual(bundles[0].options['m_BundleSize'], SAMPLE.stat().st_size)
        self.assertEqual(info['bundles'], 1)
        self.assertEqual(issues, [])

    def test_prefix_runtime_mapping(self):
        doc = json.loads(packed('0#sample.bundle'))
        doc['m_InternalIdPrefixes'] = ['{RemoteRoot}/']
        locs, _ = parse_catalogue(json.dumps(doc), 'https://cdn.test/catalog.json', {'RemoteRoot': 'https://assets.test/ho'})
        self.assertEqual(locs[1].url, 'https://assets.test/ho/sample.bundle')
        locs, _ = parse_catalogue(json.dumps(doc), 'https://cdn.test/catalog.json')
        self.assertIsNone(locs[1].url)
        self.assertIn('Unresolved runtime property', locs[1].url_error)

    def test_fail_closed_unknown_and_truncated(self):
        for raw in [b'{}', b'not json', packed('https://cdn.test/a.bundle')[:-1]]:
            with self.subTest(raw=raw[:10]), self.assertRaises(CatalogueError):
                parse_catalogue(raw, 'https://cdn.test/catalog.json')
        doc = json.loads(packed('https://cdn.test/a.bundle'))
        doc['m_EntryDataString'] = base64.b64encode(struct.pack('<i', 99999999)).decode()
        with self.assertRaises(CatalogueError):
            parse_catalogue(json.dumps(doc), 'https://cdn.test/catalog.json')

    def test_missing_dependency_not_success(self):
        locs, _ = parse_catalogue(packed('https://cdn.test/a.bundle', broken_dep=True), 'https://cdn.test/catalog.json')
        row = match_scenes([Scene(1, 'AncientGizaRiverfront', '', '', [])], Index(locs), True)[0]
        self.assertNotEqual(row['status'], 'matched')
        self.assertTrue(row['issues'])

    def test_exact_not_substring_and_ambiguity(self):
        a, _ = parse_catalogue(packed('https://cdn.test/a.bundle'), 'https://cdn.test/1.json')
        b, _ = parse_catalogue(packed('https://cdn.test/b.bundle'), 'https://cdn.test/2.json')
        scene = Scene(1, 'AncientGizaRiverfront', '', '', [])
        self.assertEqual(match_scenes([scene], Index(a), True)[0]['status'], 'matched')
        self.assertEqual(match_scenes([scene], Index(a+b), True)[0]['status'], 'ambiguous')
        scene.scene_id = 'Giza'
        self.assertEqual(match_scenes([scene], Index(a), True)[0]['status'], 'missing')

    def test_partial_catalogues_never_certified(self):
        locs, _ = parse_catalogue(packed('https://cdn.test/a.bundle'), 'https://cdn.test/catalog.json')
        row = match_scenes([Scene(1, 'AncientGizaRiverfront', '', '', [])], Index(locs), False)[0]
        self.assertEqual(row['status'], 'provisional')

    def test_cycle_detected(self):
        a = Location('a', 'c', 'a', 'p', 'a', ['a'], ['b'])
        b = Location('b', 'c', 'b', 'p', 'b', ['b'], ['a'])
        _, issues = Index([a,b]).bundle_closure(['a'])
        self.assertTrue(any('cycle' in issue for issue in issues))

    def test_bundle_validation_does_not_give_unitypy_a_disk_handle(self):
        import UnityPy
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            part = Path(td) / 'sample.bundle.part'
            part.write_bytes(SAMPLE.read_bytes())
            with patch('UnityPy.load', wraps=UnityPy.load) as load:
                validate_bundle(part)
                self.assertIsInstance(load.call_args.args[0], bytes)
            # Rename and delete immediately, without relying on cyclic GC.
            renamed = part.with_suffix('.validated')
            part.replace(renamed)
            renamed.unlink()
            part.write_bytes(SAMPLE.read_bytes())
            with patch('UnityPy.load', side_effect=ValueError('broken bundle')):
                with self.assertRaisesRegex(DownloadError, 'broken bundle'):
                    validate_bundle(part)
            part.unlink()

    def test_numeric_unicode_keys(self):
        self.assertEqual(read_object(b'\x04'+struct.pack('<i', -42), 0), -42)
        raw = '日本'.encode('utf-16-le')
        self.assertEqual(read_object(b'\x01'+struct.pack('<i', len(raw))+raw, 0), '日本')

    def test_offline_cli_reports_actual_cached_bundle(self):
        from gardens.download import cache_name
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = 'https://catalogue.test/catalogue.json'
            bundle_url = 'https://catalogue.test/scene.bundle'
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({'catalogues': [source]}))
            output = root / 'output'
            catalogue = output / 'catalogues' / cache_name(source, '.json')
            catalogue.parent.mkdir(parents=True)
            catalogue.write_bytes(packed(bundle_url))
            atomic_json(str(catalogue)+'.verified.json', {'url': source, 'sha256': sha256(catalogue)})
            bundle = output / 'bundles' / cache_name(bundle_url, '.bundle')
            bundle.parent.mkdir(parents=True)
            bundle.write_bytes(SAMPLE.read_bytes())
            atomic_json(str(bundle)+'.verified.json', {'url': bundle_url, 'sha256': sha256(bundle)})
            result = subprocess.run([sys.executable, str(ROOT / 'gardens_renderer.py'),
                                     '--scene', 'AncientGizaRiverfront', '--offline',
                                     '--catalogues', str(manifest), '--output', str(output)],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            summary = json.loads((output / 'summary.json').read_text())
            self.assertEqual(summary['matches'], {'matched': 1})
            self.assertEqual(summary['downloads'], {'cached': 1})
            self.assertEqual(summary['images_generated'], 0)


class LocalServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = SAMPLE.read_bytes()
        cls.requests = []
        parent = cls
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                parent.requests.append((self.path, dict(self.headers)))
                if self.path == '/catalogue.json':
                    body = packed(parent.base + '/sample.bundle')
                elif self.path == '/changed':
                    body = b'updated contents'
                elif self.path == '/invalid':
                    body = b'<html>Not a bundle</html>'
                elif self.path == '/missing':
                    self.send_error(404); return
                else:
                    body = parent.payload
                start = 0
                rang = self.headers.get('Range')
                if rang and self.path not in ('/ignore-range', '/changed'):
                    start = int(rang.split('=')[1].split('-')[0])
                    self.send_response(206)
                    header_start = start+1 if self.path == '/bad-range' else start
                    self.send_header('Content-Range', f'bytes {header_start}-{len(body)-1}/{len(body)}')
                else:
                    self.send_response(200)
                self.send_header('Content-Length', str(len(body)-start))
                self.send_header('ETag', '"new"' if self.path == '/changed' else '"sample"')
                self.end_headers()
                self.wfile.write(body[start:])
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = Path(self.tmp.name) / 'sample.bundle'
        self.client = Downloader(retries=0, opener=urllib.request.build_opener(urllib.request.ProxyHandler({})))
        # Loopback fixture only; production code retains inherited proxy settings.

    def seed_partial(self, url):
        Path(str(self.dest)+'.part').write_bytes(self.payload[:10000])
        atomic_json(str(self.dest)+'.part.json', {'url': url, 'etag': '"sample"', 'total': len(self.payload)})

    def test_real_bundle_transfer_skip_and_corruption(self):
        url = self.base + '/sample.bundle'
        self.assertEqual(self.client.fetch(url, self.dest, validate_bundle)['status'], 'downloaded')
        before = len(self.requests)
        self.assertEqual(self.client.fetch(url, self.dest, validate_bundle)['status'], 'cached')
        self.assertEqual(len(self.requests), before)
        self.dest.write_bytes(self.dest.read_bytes()[:-10])
        with self.assertRaises(DownloadError):
            self.client.fetch(url, self.dest, validate_bundle)

    def test_resume_real_sample(self):
        url = self.base + '/sample.bundle'
        self.seed_partial(url)
        self.client.fetch(url, self.dest, validate_bundle)
        self.assertEqual(self.dest.read_bytes(), self.payload)
        self.assertEqual(self.requests[-1][1].get('Range'), 'bytes=10000-')
        self.assertEqual(self.requests[-1][1].get('If-Range'), '"sample"')

    def test_ignored_range_restarts_without_appending(self):
        url = self.base + '/ignore-range'
        self.seed_partial(url)
        self.client.fetch(url, self.dest, validate_bundle)
        self.assertEqual(self.dest.read_bytes(), self.payload)

    def test_changed_origin_restarts(self):
        url = self.base + '/changed'
        self.seed_partial(url)
        self.client.fetch(url, self.dest)
        self.assertEqual(self.dest.read_bytes(), b'updated contents')

    def test_wrong_range_rejected(self):
        url = self.base + '/bad-range'
        self.seed_partial(url)
        with self.assertRaises(DownloadError):
            self.client.fetch(url, self.dest, validate_bundle)
        self.assertFalse(self.dest.exists())

    def test_invalid_bundle_not_promoted(self):
        with self.assertRaises(DownloadError):
            self.client.fetch(self.base + '/invalid', self.dest, validate_bundle)
        self.assertFalse(self.dest.exists())
        self.assertTrue(Path(str(self.dest)+'.part').exists())

    def test_http_error(self):
        with self.assertRaisesRegex(DownloadError, 'HTTP 404'):
            self.client.fetch(self.base + '/missing', self.dest)

    def test_catalogue_to_real_bundle_end_to_end(self):
        catalogue = Path(self.tmp.name) / 'catalogue.json'
        url = self.base + '/catalogue.json'
        self.client.fetch(url, catalogue, lambda p: parse_catalogue(p.read_bytes(), url))
        locations, _ = parse_catalogue(catalogue.read_bytes(), url)
        scene = Scene(1, 'AncientGizaRiverfront', '', '', [])
        row = match_scenes([scene], Index(locations), True)[0]
        self.assertEqual(row['status'], 'matched')
        for bundle in row['candidates'][0]['bundles']:
            self.client.fetch(bundle['url'], self.dest, validate_bundle)
        self.assertEqual(sha256(self.dest), sha256(SAMPLE))


if __name__ == '__main__':
    unittest.main()
