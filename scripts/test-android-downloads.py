#!/usr/bin/env python3
"""Verify public release downloads against pinned hashes on a local web server."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import urlopen, Request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:18089')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    if urlparse(base).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('only a local test server is allowed')
    manifest = json.loads((Path(__file__).resolve().parent.parent / 'web/frontend/android-release.json').read_text())
    for name, expected in manifest['files'].items():
        with urlopen(base + '/downloads/android/' + name, timeout=30) as response:
            assert response.status == 200
            assert response.headers['Content-Disposition'] == 'attachment'
            assert 'immutable' in response.headers['Cache-Control']
            mime = 'application/vnd.android.package-archive' if name.endswith('.apk') else 'application/gzip'
            assert response.headers.get_content_type() == mime
            digest = hashlib.sha256()
            while block := response.read(1024 * 1024):
                digest.update(block)
            assert digest.hexdigest() == expected
        print('PASS download and headers:', name)
    with urlopen(base + '/downloads/android/SHA256SUMS', timeout=10) as response:
        assert response.headers.get_content_type() == 'text/plain'
        assert response.headers['Content-Disposition'] == 'attachment'
        assert response.headers['Cache-Control'] == 'no-cache'
        expected = ''.join(f'{value}  {key}\n' for key, value in manifest['files'].items()).encode()
        assert response.read() == expected
    for path in ('/downloads/android/missing.apk', '/downloads/android/private-extra', '/downloads/missing'):
        try:
            urlopen(base + path, timeout=10)
        except HTTPError as error:
            assert error.code == 404
            assert 'no-store' in error.headers['Cache-Control']
        else:
            raise AssertionError('missing download must return 404')
    apk = next(name for name in manifest['files'] if name.endswith('.apk'))
    with urlopen(Request(base + '/downloads/android/' + apk, headers={'Range': 'bytes=0-15'}), timeout=10) as response:
        assert response.status == 206 and len(response.read()) == 16
    with urlopen(base + '/unrelated/spa-route', timeout=10) as response:
        assert response.status == 200 and response.headers.get_content_type() == 'text/html'
    print('PASS checksums, 404, range requests and SPA fallback')


if __name__ == '__main__':
    main()
