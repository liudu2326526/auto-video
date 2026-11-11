"""Mock API/CDN end-to-end ingestion; generated media is not third-party stock."""
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from material_agent.core import Library, MaterialError, read_json
from material_agent.providers import ProviderStore, search_provider
from material_agent.provider_ingest import download_provider


AT = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
KEY = 'SYNTHETIC_ONLY_PROVIDER_123456789'
USE = {'commercial': True, 'derivatives': True, 'distribution': 'public_social',
       'role': 'illustrative_context', 'share_alike_compatible': False}


def search_request(provider, kind):
    return {'schema': 'material_provider_request/v1', 'id': 'fixture_search', 'provider': provider,
            'project_id': 'synthetic_project', 'scene_id': 'context_shot', 'asset_id': 'context',
            'line_ids': [], 'public_nonpersonal': True, 'query': 'synthetic office',
            'media_type': kind, 'limit': 1, 'orientation': 'any', 'purpose': 'Synthetic contract test only'}


def photo_payload():
    return {'photos': [{'id': 123, 'width': 32, 'height': 32, 'url': 'https://www.pexels.com/photo/synthetic-123/',
                       'photographer': 'Synthetic fixture artist', 'photographer_url': 'https://www.pexels.com/@fixture',
                       'alt': 'Synthetic red card, not a stock photograph',
                       'src': {'original': 'https://images.pexels.com/photos/123/fixture.jpeg'}}]}


def video_payload(size):
    return {'total': 1, 'totalHits': 1, 'hits': [{'id': 456, 'pageURL': 'https://pixabay.com/videos/synthetic-456/',
            'type': 'film', 'tags': 'synthetic, fixture', 'user': 'fixture_artist', 'user_id': 1, 'duration': 1,
            'videos': {'medium': {'url': 'https://cdn.pixabay.com/video/2026/11/04/fixture_medium.mp4',
                                 'width': 32, 'height': 32, 'size': size}}}]}


class FakeMedia:
    NETWORK_MODE = 'mock'
    body = b''
    mime = 'image/jpeg'
    instances = []
    def __init__(self, policy):
        self.requests = 0
        self.calls = []
        self.__class__.instances.append(self)

    def once(self, address, *, limit, sink=None, allowed_mime=None):
        self.requests += 1
        self.calls.append(address)
        if address.endswith('/robots.txt'):
            return {'status': 404, 'sha256': None, 'bytes': 0, 'content_type': '', 'location': None, 'headers': {}}
        if sink:
            sink.write(self.body)
        return {'status': 200, 'sha256': hashlib.sha256(self.body).hexdigest(), 'bytes': len(self.body),
                'content_type': self.mime, 'location': None, 'headers': {}, 'body': self.body}


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Real media validation needs FFmpeg')
class IngestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.lib = Library(self.root / 'library')
        self.patches = [mock.patch.dict(os.environ, {'PEXELS_API_KEY': KEY, 'PIXABAY_API_KEY': KEY}),
                        mock.patch('material_agent.providers.now', return_value=AT),
                        mock.patch('material_agent.provider_ingest.now', return_value=AT),
                        mock.patch('material_agent.core.now', return_value=AT),
                        mock.patch('material_agent.download.DownloadLock', side_effect=lambda: nullcontext()),
                        mock.patch('material_agent.provider_ingest.DownloadLock', side_effect=lambda: nullcontext()),
                        mock.patch('material_agent.provider_ingest.MediaTransfer', FakeMedia)]
        for p in self.patches: p.start()
        FakeMedia.instances = []

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.lib.close()
        self.temp.cleanup()

    def media(self, kind):
        path = self.root / ('synthetic.jpg' if kind == 'image' else 'synthetic.mp4')
        args = ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=32x32:r=30:d=1']
        if kind == 'image': args += ['-frames:v', '1', '-c:v', 'mjpeg', '-threads', '1']
        else: args += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-threads', '1']
        subprocess.run(args + [str(path)], check=True, timeout=15)
        FakeMedia.body, FakeMedia.mime = path.read_bytes(), 'image/jpeg' if kind == 'image' else 'video/mp4'
        return path

    def candidate(self, provider, kind):
        payload = photo_payload() if kind == 'image' else video_payload(len(FakeMedia.body))
        def client(*args):
            return {'status': 200, 'headers': {}, 'data': payload, 'network_requests': 1}
        report = search_provider(self.lib, search_request(provider, kind), client=client, at=AT)
        return report['candidates'][0]

    def test_mock_photo_and_video_validate_import_provenance_and_keep_review_pending(self):
        for provider, kind in [('pexels', 'image'), ('pixabay', 'video')]:
            self.media(kind)
            candidate = self.candidate(provider, kind)
            variant = candidate['asset']['variants'][0]['id']
            result = download_provider(self.lib, candidate['id'], variant, USE)
            self.assertEqual('downloaded', result['outcome'])
            self.assertEqual(hashlib.sha256(FakeMedia.body).hexdigest(), result['sha256'])
            row = self.lib.get(result['record_id'])
            self.assertEqual(FakeMedia.body, self.lib._blob(row['blob_sha256']).read_bytes())
            self.assertFalse(self.lib.eligibility(row['id'], USE, AT)['eligible'])
            self.assertEqual([], self.lib.history(row['id'])['reviews'])
            self.assertEqual([], self.lib.history(row['id'])['usages'])
            self.assertEqual(provider, self.lib.db.execute('SELECT json_extract(document,"$.provider") FROM provider_imports WHERE record_id=?', (row['id'],)).fetchone()[0])
            prior = len(FakeMedia.instances)
            cached = download_provider(self.lib, candidate['id'], variant, USE)
            self.assertEqual(0, cached['network_requests'])
            self.assertEqual(prior, len(FakeMedia.instances))
        self.assertFalse(list(self.lib.root.glob('.provider-download-*')))
        for path in self.lib.root.glob('*.sqlite3'):
            self.assertNotIn(KEY.encode(), path.read_bytes())

    def test_import_audit_failure_rolls_back_all_new_rows_and_bytes(self):
        self.media('image'); c = self.candidate('pexels', 'image')
        self.lib.db.execute("CREATE TRIGGER fail_provenance BEFORE INSERT ON provider_imports BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            download_provider(self.lib, c['id'], c['asset']['variants'][0]['id'], USE)
        self.assertEqual(0, self.lib.db.execute('SELECT count(*) FROM records').fetchone()[0])
        self.assertEqual(0, self.lib.db.execute('SELECT count(*) FROM blobs').fetchone()[0])
        self.assertEqual(0, self.lib.db.execute('SELECT count(*) FROM provider_imports').fetchone()[0])
        self.assertFalse(list((self.lib.root / 'blobs').rglob('*.*')))
        self.assertFalse(list(self.lib.root.glob('.provider-download-*')))

    def test_mock_candidate_cannot_start_real_media_transport(self):
        self.media('image'); c = self.candidate('pexels', 'image')
        self.assertEqual('mock', c['discovery_mode'])
        with mock.patch.object(FakeMedia, 'NETWORK_MODE', 'live'):
            with self.assertRaises(MaterialError):
                download_provider(self.lib, c['id'], c['asset']['variants'][0]['id'], USE)
        self.assertEqual([], FakeMedia.instances)

    def test_provider_withdrawal_blocks_cache_download_and_existing_eligibility(self):
        self.media('image'); c = self.candidate('pexels', 'image')
        result = download_provider(self.lib, c['id'], c['asset']['variants'][0]['id'], USE)
        store = ProviderStore(self.lib)
        try:
            receipt = store.withdraw('pexels', c['asset']['provider_asset_id'], 'fixture operator', 'Synthetic removal notice')
        finally: store.close()
        self.assertIn(result['record_id'], receipt['blocked_record_ids'])
        self.assertIn('withdrawn', ';'.join(self.lib.eligibility(result['record_id'], USE, AT)['blockers']))
        with self.assertRaises(MaterialError):
            download_provider(self.lib, c['id'], c['asset']['variants'][0]['id'], USE)
        self.assertTrue(self.lib._blob(result['sha256']).exists(), 'No unrequested destructive deletion')


if __name__ == '__main__': unittest.main()
