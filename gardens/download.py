"""Streaming, validator-aware HTTP resume with atomic promotion and local hashes."""
import hashlib
import http.client
import json
import os
import re
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path


class DownloadError(RuntimeError):
    pass


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def validate_bundle(path, expected_size=None):
    """Check real UnityFS length, block decompression and serialized-file parsing.

    Catalogue Hash128 is Unity's cache version, NOT the file MD5. Catalogue CRC
    is over uncompressed bundle data, NOT the downloaded bytes' CRC32.
    Neither is falsely reported as an HTTP artifact checksum here.
    """
    path = Path(path)
    if expected_size and path.stat().st_size != expected_size:
        raise DownloadError(f'Bundle size differs from catalogue: {path.stat().st_size} != {expected_size}')
    with path.open('rb') as f:
        def cstring():
            out = bytearray()
            while len(out) < 256:
                ch = f.read(1)
                if ch == b'\0':
                    return bytes(out)
                if not ch:
                    break
                out.extend(ch)
            raise DownloadError('Truncated UnityFS header')
        if cstring() != b'UnityFS':
            raise DownloadError('Unsupported or missing UnityFS signature')
        version = f.read(4)
        if len(version) != 4 or struct.unpack('>I', version)[0] not in (6, 7, 8):
            raise DownloadError('Unsupported UnityFS format version')
        cstring(); cstring()
        size = f.read(8)
        if len(size) != 8 or struct.unpack('>Q', size)[0] != path.stat().st_size:
            raise DownloadError('Truncated UnityFS file or incorrect embedded length')
    try:
        import UnityPy
    except ImportError as exc:
        raise DownloadError('Install requirements.txt: UnityPy is required for bundle validation') from exc
    try:
        # UnityPy retains readers in its object graph. Loading by filename can
        # keep the OS handle alive past validation, preventing os.replace and
        # temporary-directory cleanup on Windows. Supply owned bytes instead;
        # read_bytes closes its file even when UnityPy subsequently raises.
        env = UnityPy.load(path.read_bytes())
        if not env.objects:
            raise DownloadError('No serialized Unity objects in bundle')
        # Loading decompresses blocks; read all object bytes to detect broken extents.
        for obj in env.objects:
            obj.get_raw_data()
    except Exception as exc:
        raise DownloadError(f'Unity bundle validation failed: {exc}') from exc


class Downloader:
    def __init__(self, timeout=30, retries=2, opener=None):
        self.timeout, self.retries = timeout, retries
        self.opener = opener or urllib.request.build_opener()

    def fetch(self, url, destination, validator=None, refresh=False):
        dest = Path(destination)
        dest.parent.mkdir(parents=True, exist_ok=True)
        receipt = Path(str(dest) + '.verified.json')
        cached = read_json(receipt)
        if dest.exists() and not refresh:
            if cached.get('url') == url and cached.get('sha256') == sha256(dest):
                if validator:
                    validator(dest)
                return {'status': 'cached', 'path': str(dest), **cached}
            # Preserve unknown/corrupt originals rather than silently overwrite them.
            raise DownloadError(f'Unverified or changed file at {dest}; move it aside or use --refresh')
        if dest.exists():
            archive = dest.with_name(dest.name + '.previous-' + sha256(dest)[:16])
            if not archive.exists():
                os.replace(dest, archive)
            else:
                dest.unlink()  # byte-identical original is retained in archive
        part = Path(str(dest) + '.part')
        state_path = Path(str(dest) + '.part.json')
        for attempt in range(self.retries + 1):
            try:
                state = read_json(state_path)
                offset = part.stat().st_size if part.exists() else 0
                # Safe resume needs an origin validator. Otherwise restart the partial.
                tag = state.get('etag') or state.get('last_modified')
                resume = offset > 0 and state.get('url') == url and tag
                headers = {'User-Agent': 'GardensSceneRecovery/0.1', 'Accept-Encoding': 'identity'}
                if resume:
                    headers.update({'Range': f'bytes={offset}-', 'If-Range': tag})
                request = urllib.request.Request(url, headers=headers)
                with self.opener.open(request, timeout=self.timeout) as response:
                    status = response.status
                    total = None
                    if status == 206:
                        match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', response.headers.get('Content-Range', ''))
                        if not resume or not match or int(match[1]) != offset:
                            raise DownloadError('Invalid resumed Content-Range')
                        start, end, total = map(int, match.groups())
                        if end < start or end >= total or (state.get('total') and total != state['total']):
                            raise DownloadError('Changed or invalid resumed total length')
                        if response.headers.get('ETag') and state.get('etag') and response.headers['ETag'] != state['etag']:
                            raise DownloadError('ETag changed during resumed transfer')
                        expected_response = end - start + 1
                        mode = 'ab'
                    elif status == 200:
                        offset, mode = 0, 'wb'
                        length = response.headers.get('Content-Length')
                        total = int(length) if length else None
                        expected_response = total
                    else:
                        raise DownloadError(f'Unexpected HTTP status {status}')
                    if response.headers.get('Content-Encoding', 'identity') != 'identity':
                        raise DownloadError('Unexpected Content-Encoding; cannot safely resume')
                    etag = response.headers.get('ETag')
                    if etag and etag.startswith('W/'):
                        etag = None
                    state = {'url': url, 'etag': etag or (state.get('etag') if mode == 'ab' else None),
                             'last_modified': response.headers.get('Last-Modified') or (state.get('last_modified') if mode == 'ab' else None),
                             'total': total}
                    # Truncate before updating state so interruption cannot associate old
                    # bytes with a new ETag after a server ignores Range.
                    with part.open(mode) as f:
                        atomic_json(state_path, state)
                        received = 0
                        while True:
                            block = response.read(1024 * 1024)
                            if not block:
                                break
                            f.write(block)
                            received += len(block)
                        f.flush()
                        os.fsync(f.fileno())
                    if expected_response is not None and received != expected_response:
                        raise DownloadError('HTTP response body length mismatch')
                    if total is not None and part.stat().st_size != total:
                        raise DownloadError('Download is incomplete')
                if validator:
                    validator(part)
                result = {**state, 'bytes': part.stat().st_size, 'sha256': sha256(part),
                          'verification': 'local-sha256-and-format' if validator else 'local-sha256'}
                os.replace(part, dest)
                atomic_json(receipt, result)
                state_path.unlink(missing_ok=True)
                return {'status': 'downloaded', 'path': str(dest), **result}
            except urllib.error.HTTPError as exc:
                if exc.code == 416:
                    # A complete .part may have survived interruption before promotion.
                    if (resume and state.get('total') == offset and
                            exc.headers.get('Content-Range') == f'bytes */{offset}' and
                            ((state.get('etag') and exc.headers.get('ETag') == state['etag']) or
                             (not state.get('etag') and exc.headers.get('Last-Modified') == state.get('last_modified')))):
                        if validator:
                            validator(part)
                        result = {**state, 'bytes': offset, 'sha256': sha256(part), 'verification': 'local-sha256-and-format'}
                        os.replace(part, dest); atomic_json(receipt, result)
                        state_path.unlink(missing_ok=True)
                        return {'status': 'downloaded', 'path': str(dest), **result}
                    state_path.unlink(missing_ok=True)
                elif exc.code not in (408, 429, 500, 502, 503, 504):
                    raise DownloadError(f'HTTP {exc.code} for {url}') from exc
                error = exc
            except (OSError, ValueError, DownloadError, urllib.error.URLError, http.client.HTTPException) as exc:
                error = exc
                if isinstance(exc, DownloadError) and 'validation' in str(exc).lower():
                    state_path.unlink(missing_ok=True)
            if attempt < self.retries:
                time.sleep(min(2 ** attempt, 4))
        raise DownloadError(f'{url}: {error}') from error


def cache_name(url, suffix=''):
    # URL hash isolates same-named assets from different catalogues/CDN paths.
    return hashlib.sha256(url.encode()).hexdigest() + suffix
