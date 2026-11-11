"""One-file workflow, decoding, cache reuse, failures, and append-only audit."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import struct
import tempfile
import unittest
from unittest import mock
import zlib

from material_agent.core import Library, MaterialError, digest, read_json
from material_agent.download import download_record, preflight, validate_media, ROBOTS_URL


def png():
    def chunk(kind, body):
        return struct.pack('!I', len(body)) + kind + body + struct.pack('!I', zlib.crc32(kind + body))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', 2, 2, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\x00\x00\xff' * 2) * 2)) + chunk(b'IEND', b''))


class FakeTransfer:
    instances = []
    actions = []
    def __init__(self, policy):
        self.requests = 0
        self.min_interval = 0
        self.calls = []
        self.__class__.instances.append(self)

    def once(self, address, *, limit, sink=None, allowed_mime=None):
        self.requests += 1
        self.calls.append(address)
        if self.actions:
            action = self.actions.pop(0)
            if isinstance(action, BaseException):
                raise action
            body, status, mime, location = action
        elif address == ROBOTS_URL:
            body, status, mime, location = b'User-agent: *\nDisallow: /wikipedia/commons/archive/\n', 200, 'text/plain', None
        else:
            body, status, mime, location = png(), 200, 'image/png', None
        if sink is not None:
            sink.write(body)
        return {'status': status, 'content_type': mime, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
                'body': body, 'final_url': address, 'location': location}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.lib = Library(self.root / 'library')
        fixtures = Path(__file__).resolve().parents[1] / 'fixtures/materials'
        self.record = read_json(fixtures / 'commons-laptop-download.json')
        self.record['id'] = 'synthetic_download'
        self.record['source'].update(page_url='https://commons.wikimedia.org/wiki/File:Unit_test.png',
                                      download_url='https://upload.wikimedia.org/wikipedia/commons/a/ab/Unit_test.png')
        self.record['rights']['evidence'][0]['url'] = self.record['source']['page_url']
        self.policy = read_json(fixtures / 'commons-download-policy.json')
        self.policy.update(record_digest=digest(self.record), expected_sha256=None)
        self.use = read_json(fixtures / 'use-context.json')
        FakeTransfer.instances, FakeTransfer.actions = [], []
        self.net = mock.patch('material_agent.download.Transfer', FakeTransfer)
        self.net.start()
        self.tools = mock.patch('material_agent.download.shutil.which', return_value='/synthetic/test-tool')
        self.tools.start()
        # Workflow tests exercise the gate/storage; decoding gets a separate real-tool case.
        self.decode = mock.patch('material_agent.download.validate_media', return_value={
            'mime': 'image/png', 'media_type': 'image', 'suffix': '.png', 'probe': {}, 'decoded': True})
        self.decode.start()

    def tearDown(self):
        mock.patch.stopall()
        self.lib.close()
        self.temp.cleanup()

    def run_download(self):
        self.policy['record_digest'] = digest(self.record)
        return download_record(self.lib, self.record, self.policy, self.use)

    def assert_clean_failure(self):
        self.assertEqual(0, self.lib.db.execute('SELECT count(*) FROM records').fetchone()[0])
        self.assertEqual(0, self.lib.db.execute('SELECT count(*) FROM blobs').fetchone()[0])
        self.assertFalse(list((self.lib.root / 'blobs').rglob('*.*')))
        self.assertFalse(list(self.lib.root.glob('.download-*')))
        self.assertEqual('failed', self.lib.download_history(self.record['id'])[-1]['outcome'])

    def test_download_cache_dedup_and_no_fake_human_or_edit_use(self):
        first = self.run_download()
        self.assertEqual('downloaded', first['outcome'])
        row = first['record']
        self.assertEqual(hashlib.sha256(png()).hexdigest(), row['blob_sha256'])
        self.assertEqual(png(), self.lib._blob(row['blob_sha256']).read_bytes())
        second = self.run_download()
        self.assertEqual('reused_local', second['outcome'])
        self.assertEqual(0, second['network_requests'])
        self.assertEqual(1, len(FakeTransfer.instances))
        self.assertFalse(self.lib.eligibility(row['id'], self.use)['eligible'])
        self.assertEqual([], self.lib.history(row['id'])['reviews'])
        self.assertEqual([], self.lib.history(row['id'])['usages'])
        self.record['id'] = 'separate_source_record'
        self.run_download()
        self.assertEqual(1, self.lib.db.execute('SELECT count(*) FROM blobs').fetchone()[0])
        self.assertEqual(2, self.lib.db.execute('SELECT count(*) FROM records').fetchone()[0])
        self.assertFalse(list(self.lib.root.glob('.download-*')))

    def test_known_license_and_terms_are_required_before_network(self):
        for mutation in ('unknown', 'expired_terms', 'different_file', 'private_url', 'wrong_license_url'):
            with self.subTest(mutation=mutation):
                original, policy = copy.deepcopy(self.record), copy.deepcopy(self.policy)
                if mutation == 'unknown': self.record['rights']['status'] = 'unknown'
                if mutation == 'expired_terms': self.policy['terms_checked_at'] = '2020-01-01T00:00:00Z'
                if mutation == 'different_file': self.record['source']['page_url'] += 'other'
                if mutation == 'private_url': self.record['source']['download_url'] = 'https://169.254.169.254/latest/meta-data/'
                if mutation == 'wrong_license_url': self.record['rights']['license_url'] = 'https://example.org/license'
                with self.assertRaises(MaterialError): self.run_download()
                self.assertEqual([], FakeTransfer.instances)
                self.assert_clean_failure()
                self.record, self.policy = original, policy

    def test_robots_denial_and_missing_rules_stop_before_media(self):
        for status in (403, 404, 429, 500):
            FakeTransfer.actions = [(b'', status, 'text/plain', None)]
            with self.assertRaises(MaterialError): self.run_download()
            self.assertEqual([ROBOTS_URL], FakeTransfer.instances[-1].calls)
            self.assert_clean_failure()
        FakeTransfer.actions = [(b'User-agent: *\nDisallow: /\n', 200, 'text/plain', None)]
        with self.assertRaises(MaterialError): self.run_download()
        self.assertEqual([ROBOTS_URL], FakeTransfer.instances[-1].calls)
        self.assert_clean_failure()

    def test_redirect_invalid_or_new_file_never_followed(self):
        for target in ('https://evil.example/a.png', 'http://upload.wikimedia.org/a.png',
                       'https://127.0.0.1/a.png', 'https://upload.wikimedia.org/wikipedia/commons/a/ab/Other.png'):
            FakeTransfer.actions = [(b'User-agent: *\nDisallow:\n', 200, 'text/plain', None),
                                    (b'', 302, '', target)]
            with self.assertRaises(MaterialError): self.run_download()
            self.assertEqual(2, len(FakeTransfer.instances[-1].calls))
            self.assert_clean_failure()

    def test_redirect_same_file_is_checked_against_robots_and_logged(self):
        next_url = 'https://upload.wikimedia.org/wikipedia/commons/b/bc/Unit_test.png'
        FakeTransfer.actions = [(b'User-agent: *\nDisallow:\n', 200, 'text/plain', None),
                                (b'', 302, '', next_url), (png(), 200, 'image/png', None)]
        row = self.run_download()['record']
        verification = json.loads(row['download_record'])['verification']
        self.assertEqual(next_url, verification['final_url'])
        self.assertEqual(1, len(verification['redirects']))

    def test_redirect_target_forbidden_by_robots_is_not_fetched(self):
        next_url = 'https://upload.wikimedia.org/wikipedia/commons/b/bc/Unit_test.png'
        FakeTransfer.actions = [(b'User-agent: *\nDisallow: /wikipedia/commons/b/\n', 200, 'text/plain', None),
                                (b'', 302, '', next_url)]
        with self.assertRaises(MaterialError): self.run_download()
        self.assertEqual(2, len(FakeTransfer.instances[-1].calls))
        self.assert_clean_failure()

    def test_checksum_and_decode_failure_leave_no_half_files(self):
        self.policy['expected_sha256'] = '0' * 64
        with self.assertRaises(MaterialError): self.run_download()
        self.assert_clean_failure()
        self.policy['expected_sha256'] = None
        self.decode.stop()
        with mock.patch('material_agent.download.validate_media', side_effect=MaterialError('Decode failed')):
            with self.assertRaises(MaterialError): self.run_download()
        self.assert_clean_failure()

    def test_record_insert_failure_rolls_back_new_blob(self):
        self.lib.db.execute("CREATE TRIGGER fail_import BEFORE INSERT ON records BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.run_download()
        self.assert_clean_failure()

    def test_success_audit_failure_also_rolls_back_import(self):
        self.lib.db.execute("CREATE TRIGGER fail_success_audit BEFORE INSERT ON download_events WHEN json_extract(NEW.document,'$.outcome')='downloaded' BEGIN SELECT RAISE(ABORT,'synthetic audit failure'); END")
        with self.assertRaises(sqlite3.IntegrityError): self.run_download()
        self.assert_clean_failure()

    def test_unsupported_robots_semantics_stop_without_media_request(self):
        for rules in (b'User-agent: *\nDisallow: /*.png$\n',
                      b'User-agent: *\nAllow: /wikipedia/commons/\nDisallow: /wikipedia/commons/a/\n',
                      b'User-agent: *\nDisallow:\n\nUser-agent: *\nDisallow: /\n'):
            FakeTransfer.actions = [(rules, 200, 'text/plain', None)]
            with self.assertRaises(MaterialError): self.run_download()
            self.assertEqual([ROBOTS_URL], FakeTransfer.instances[-1].calls)
            self.assert_clean_failure()

    def test_empty_disallow_cannot_mask_a_later_restriction(self):
        FakeTransfer.actions = [(b'User-agent: *\nDisallow:\nDisallow: /wikipedia/commons/\n', 200, 'text/plain', None)]
        with self.assertRaises(MaterialError): self.run_download()
        self.assertEqual([ROBOTS_URL], FakeTransfer.instances[-1].calls)
        self.assert_clean_failure()

    def test_download_audit_is_append_only_and_does_not_log_bad_url_credentials(self):
        self.record['source']['download_url'] = 'https://user:secret@upload.wikimedia.org/a.png'
        with self.assertRaises(MaterialError): self.run_download()
        logs = canonical_audit = json.dumps(self.lib.download_history(self.record['id']))
        self.assertNotIn('secret', logs)
        self.assertNotIn('user:', canonical_audit)
        with self.assertRaises(sqlite3.IntegrityError): self.lib.db.execute('DELETE FROM download_events')


@unittest.skipUnless(shutil.which('ffprobe') and shutil.which('ffmpeg'), 'FFmpeg validation tools unavailable')
class DecodeTests(unittest.TestCase):
    def test_real_png_decode_and_spoof_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'public.part'
            path.write_bytes(png())
            result = validate_media(path, 'image/png', 'image')
            self.assertTrue(result['decoded'])
            self.assertEqual(2, result['probe']['streams'][0]['width'])
            for mime, kind in [('image/jpeg', 'image'), ('image/png', 'video')]:
                with self.assertRaises(MaterialError): validate_media(path, mime, kind)
            path.write_bytes(b'<html>login required</html>')
            with self.assertRaises(MaterialError): validate_media(path, 'image/png', 'image')

    def test_video_container_cannot_be_labeled_as_still_image(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'not-a-still.part'
            for header, mime in [(b'\x1a\x45\xdf\xa3' + b'\0' * 28, 'video/webm'), (b'OggS' + b'\0' * 28, 'video/ogg')]:
                path.write_bytes(header)
                with self.assertRaises(MaterialError): validate_media(path, mime, 'image')


if __name__ == '__main__':
    unittest.main()
