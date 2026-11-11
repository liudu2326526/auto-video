"""Offline invariants for private sessions, replay prevention, media and archives."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import uuid

import analyze
import media
import report
import storage
import transport
from transport import ServiceClient, ServiceError


class Response(io.BytesIO):
    status = 200


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='zhicut-transport-test-')
        self.root = Path(self.temporary.name)
        self.account = self.root / 'account.txt'
        self.password = 'FAKE_PASSWORD_FOR_OFFLINE_TEST_ONLY'
        self.account.write_text('username: fake-user\npassword: ' + self.password)
        self.calls = []
        now = datetime.now(timezone.utc)
        self.sid = str(uuid.uuid4())
        self.session = {'protocol_version': 'desktop-auth-entry.v1', 'desktop_session_id': self.sid,
            'agent_session': {'audience': 'desktop-agent', 'scope': ['desktop-agent:session', 'desktop-agent:inference:invoke'],
                'session_jti': str(uuid.uuid4()), 'access_token': 'A' * 64, 'refresh_handle': 'R' * 64,
                'expires_at': (now + timedelta(hours=1)).isoformat(),
                'refresh_expires_at': (now + timedelta(days=1)).isoformat()}}

    def tearDown(self):
        self.temporary.cleanup()

    def client(self, terminal='completed'):
        def opener(request, timeout):
            self.calls.append(request)
            if request.full_url.endswith('/password/login'):
                return Response(json.dumps({'data': self.session}).encode())
            if request.method == 'DELETE':
                return Response(json.dumps({'data': {'desktop_session_id': self.sid, 'status': 'revoked',
                    'agent_revoked': True, 'web_revoked': True}}).encode())
            if request.full_url.endswith('/audio/transcriptions'):
                self.assertIn(b'timestamp_granularities[]', request.data)
                return Response(b'{"text":"synthetic speech","segments":[],"words":[]}')
            if terminal == 'unknown':
                return Response(b'data: {"type":"response.created"}\n\n')
            event = {'type': 'response.' + terminal, 'response': {'status': terminal, 'output': []}}
            return Response(('data: ' + json.dumps(event) + '\n\n').encode())
        return ServiceClient(self.account, self.root / 'private', ['username'], ['password'], transport=opener)

    def test_completed_model_is_reused_and_session_is_revoked(self):
        client = self.client()
        client.login()
        self.assertEqual(os.stat(client.session_path).st_mode & 0o777, 0o600)
        wav = self.root / 'audio.wav'; wav.write_bytes(b'RIFF-synthetic')
        self.assertEqual(client.transcribe(wav)['text'], 'synthetic speech')
        body = {'model': 'test', 'stream': True, 'input': []}
        evidence = self.root / 'evidence'
        client.analyze(body, evidence); client.analyze(body, evidence)
        self.assertEqual(sum(r.full_url.endswith('/gpt/responses') for r in self.calls), 1)
        self.assertTrue(client.logout()['agentRevoked'])
        self.assertFalse(client.session_path.exists())
        for path in evidence.iterdir():
            text = path.read_text()
            self.assertNotIn(self.password, text)
            self.assertNotIn('A' * 64, text)

    def test_unknown_or_incomplete_is_not_replayed(self):
        for terminal in ['unknown', 'incomplete']:
            with self.subTest(terminal=terminal):
                client = self.client(terminal); client.login()
                evidence = self.root / terminal
                body = {'model': 'test', 'stream': True, 'input': []}
                before = len(self.calls)
                with self.assertRaises(ServiceError): client.analyze(body, evidence)
                with self.assertRaises(ServiceError): client.analyze(body, evidence)
                self.assertEqual(len(self.calls) - before, 1)
                state = storage.read_json(evidence / 'model-request-state.json')['state']
                self.assertEqual(state, 'incomplete' if terminal == 'incomplete' else 'outcome_unknown')
                client.logout()


    def test_verified_context_reuses_bundle_only_when_default_store_is_empty(self):
        context = mock.Mock()
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        context.cert_store_stats.side_effect = [{'x509_ca': 0}, {'x509_ca': 1}]
        certifi = mock.Mock()
        certifi.where.return_value = '/offline-fixture/trusted-ca.pem'
        with mock.patch.object(transport.ssl, 'create_default_context', return_value=context), \
                mock.patch.dict(sys.modules, {'certifi': certifi}):
            self.assertIs(transport._verified_context(), context)
        context.load_verify_locations.assert_called_once_with(cafile='/offline-fixture/trusted-ca.pem')
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_verified_context_preserves_explicit_or_platform_trust(self):
        context = mock.Mock()
        context.cert_store_stats.return_value = {'x509_ca': 3}
        with mock.patch.dict(os.environ, {'SSL_CERT_FILE': '/offline-fixture/explicit-ca.pem'}), \
                mock.patch.object(transport.ssl, 'create_default_context', return_value=context), \
                mock.patch.dict(sys.modules, {'certifi': None}):
            self.assertIs(transport._verified_context(), context)
        context.load_verify_locations.assert_not_called()

    def test_missing_bundle_fails_before_credentials_or_network(self):
        context = mock.Mock()
        context.cert_store_stats.return_value = {'x509_ca': 0}
        with mock.patch.object(transport.ssl, 'create_default_context', return_value=context), \
                mock.patch.dict(sys.modules, {'certifi': None}), \
                mock.patch.object(ServiceClient, '_credentials') as credentials, \
                mock.patch.object(transport.urllib.request, 'build_opener') as opener:
            with self.assertRaisesRegex(ServiceError, 'local_ca_bundle_required') as raised:
                ServiceClient(self.account, self.root / 'private', ['username'], ['password'])
        self.assertTrue(raised.exception.outcome_known)
        credentials.assert_not_called()
        opener.assert_not_called()
        self.assertFalse((self.root / 'private' / 'session.json').exists())

    def test_injected_transport_does_not_initialize_system_trust(self):
        with mock.patch.object(transport, '_verified_context', side_effect=AssertionError('Unexpected CA setup')):
            client = self.client()
            self.assertTrue(client.login()['authenticated'])
            self.assertTrue(client.logout()['agentRevoked'])

    def test_tls_refusal_is_known_and_never_retried(self):
        for wrapped in [False, True]:
            with self.subTest(wrapped=wrapped):
                client = self.client()
                client.login()
                calls = []
                def refused(request, timeout):
                    calls.append(request)
                    error = ssl.SSLCertVerificationError(1, 'PRIVATE_DIAGNOSTIC_NOT_FOR_OUTPUT')
                    raise urllib.error.URLError(error) if wrapped else error
                client._transport = refused
                body = {'model': 'test', 'stream': True, 'input': []}
                evidence = self.root / ('tls-wrapped' if wrapped else 'tls-direct')
                with self.assertRaisesRegex(ServiceError, 'tls_certificate_verification_failed') as raised:
                    client.analyze(body, evidence)
                self.assertTrue(raised.exception.outcome_known)
                self.assertNotIn('PRIVATE_DIAGNOSTIC', str(raised.exception))
                with self.assertRaises(ServiceError): client.analyze(body, evidence)
                self.assertEqual(len(calls), 1)
                self.assertEqual(storage.read_json(evidence / 'model-request-state.json')['state'], 'failed')
                # Restore the fake offline transport only to revoke the fixture session.
                client._transport = self.client()._transport
                client.logout()

    def test_other_network_failures_remain_unknown(self):
        client = self.client()
        client.login()
        def disconnected(request, timeout):
            raise urllib.error.URLError(TimeoutError('PRIVATE_NETWORK_DIAGNOSTIC'))
        client._transport = disconnected
        evidence = self.root / 'network-timeout'
        with self.assertRaisesRegex(ServiceError, 'network_outcome_unknown') as raised:
            client.analyze({'model': 'test', 'stream': True, 'input': []}, evidence)
        self.assertFalse(raised.exception.outcome_known)
        self.assertNotIn('PRIVATE_NETWORK', str(raised.exception))
        self.assertEqual(storage.read_json(evidence / 'model-request-state.json')['state'], 'outcome_unknown')
        client._transport = self.client()._transport
        client.logout()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='zhicut-workflow-test-')
        self.root = Path(self.temporary.name)
        self.config = storage.read_json(storage.SKILL_ROOT / 'assets' / 'defaults.json')
        self.config.update({'output_root': str(self.root / 'outputs'), 'analysis_date': '2026-11-11',
                            'account_file': str(self.root / 'fake-account.txt'), 'locale': 'en'})
        self.ffmpeg = os.environ.get('ZHICUT_TEST_FFMPEG') or shutil.which('ffmpeg')
        self.ffprobe = os.environ.get('ZHICUT_TEST_FFPROBE') or shutil.which('ffprobe')
        if not self.ffmpeg or not self.ffprobe:
            self.skipTest('Runnable FFmpeg and FFprobe are required for media integration tests')
        try:
            for command in [self.ffmpeg, self.ffprobe]:
                subprocess.run([command, '-version'], check=True, capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            self.skipTest('Media binaries are not runnable')
        self.config.update(ffmpeg=self.ffmpeg, ffprobe=self.ffprobe)
        self.source = self.root / 'synthetic.mp4'
        subprocess.run([self.ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=10',
            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000', '-t', '1',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(self.source)], check=True, capture_output=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_versioned_full_pipeline_import_and_no_repeat_inference(self):
        directory = analyze.prepare(self.source, self.config, count=2)
        duration = storage.read_json(directory / 'metadata.json')['durationSeconds']
        data = {'title': 'Synthetic test report', 'whole_video_insights': {'thesis': 'Synthetic fixture only',
            'basis': 'Offline test', 'items': [{'title': 'Fixture', 'text': 'Not real analysis', 'evidence_times': ['00:00']}]},
            'overview': ['Synthetic only'], 'source_scope': 'Offline test',
            'chapters': [{'start_seconds': 0, 'end_seconds': duration, 'title': 'Fixture', 'points': ['Synthetic']}],
            'key_findings': [], 'narrative_structure': [], 'practical_takeaways': [], 'limitations': []}
        class FakeClient:
            calls = 0
            def __init__(self, *args, **kwargs): pass
            def login(self): pass
            def transcribe(self, path, language):
                return {'text': 'Synthetic speech', 'segments': [{'start': 0, 'end': duration, 'text': 'Synthetic speech'}],
                        'words': [], 'timing_quality': 'partial'}
            def analyze(self, body, evidence):
                FakeClient.calls += 1
                response = {'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': json.dumps(data)}]}]}
                storage.write_json(evidence / 'model-response.json', response)
                return response
            def logout(self): return {'status': 'revoked', 'agentRevoked': True, 'webRevoked': True}
        with mock.patch.object(analyze, 'ServiceClient', FakeClient):
            output = analyze.execute_run(directory, self.config)
            analyze.execute_run(directory, self.config)
        self.assertTrue(output.is_file())
        self.assertEqual(FakeClient.calls, 1)
        self.assertEqual(storage.read_json(directory / 'run.json')['status'], 'completed')
        self.assertEqual(storage.read_json(directory / 'report-render-verification.json')['imageEmbeddedOnce'], True)
        frame_path = storage.read_json(directory / 'frames.json')[0]['file']
        html = output.read_text()
        html = re.sub(r'src="data:image/jpeg;base64,[^"]+"', 'src="' + frame_path + '"', html, count=1)
        output.write_text(html)
        imported = storage.import_existing(directory, self.root / 'imported', 'en')
        self.assertTrue((imported / frame_path).is_file())
        self.assertIn('src="' + frame_path + '"', (imported / 'report.html').read_text())
        self.assertEqual(storage.source_hash(imported / 'report.html'), storage.source_hash(output))
        self.assertTrue((self.root / 'imported' / 'index.html').exists())
        self.assertEqual(len(storage.read_json(self.root / 'imported' / 'index.json')['entries']), 1)
        second = storage.new_run(self.config['output_root'], storage.read_json(directory / 'metadata.json'))
        self.assertNotEqual(directory, second)
        self.assertEqual(directory.parent, second.parent)

    def test_cleanup_only_revokes_the_run_owned_private_session(self):
        directory = analyze.prepare(self.source, self.config, count=1)
        unsafe = self.root / 'unrelated-private-directory'
        unsafe.mkdir()
        storage.write_json(unsafe / 'task.json', {'runDirectory': str(directory)})
        storage.set_status(directory, 'cleanup-pending', privateStateDirectory=str(unsafe))
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            analyze.cleanup_session(directory, self.config)
        private = Path(tempfile.mkdtemp(prefix='zhicut-analysis-session-')).resolve()
        try:
            storage.write_json(private / 'task.json', {'runDirectory': str(directory)})
            storage.set_status(directory, 'cleanup-pending', privateStateDirectory=str(private))
            fake = mock.Mock()
            fake.logout.return_value = {'status': 'revoked', 'agentRevoked': True, 'webRevoked': True}
            with mock.patch.object(analyze, 'ServiceClient', return_value=fake):
                result = analyze.cleanup_session(directory, self.config)
            self.assertTrue(result['agentRevoked'])
            fake.logout.assert_called_once()
            fake.login.assert_not_called()
            self.assertFalse(private.exists())
            self.assertIsNone(storage.read_json(directory / 'run.json')['privateStateDirectory'])
        finally:
            shutil.rmtree(private, ignore_errors=True)

    def test_pending_transcription_and_changed_source_are_rejected(self):
        directory = analyze.prepare(self.source, self.config, count=1)
        storage.write_json(directory / 'evidence' / 'asr-000-state.json', {'status': 'outcome_unknown'})
        with self.assertRaisesRegex(RuntimeError, 'Previous transcription'):
            media.transcribe(directory, None, self.config)
        original = self.source.read_bytes(); self.source.write_bytes(original + b'changed')
        with self.assertRaisesRegex(RuntimeError, 'Source video'):
            media.verify_source(storage.read_json(directory / 'metadata.json'))


if __name__ == '__main__':
    with mock.patch.object(ServiceClient, '_host_identity', return_value=('darwin', 'arm64')):
        unittest.main()
