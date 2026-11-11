"""Meaningful probe storage, HTTP origin/scope and replay boundaries; no browser capture."""
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from recording_agent import browser_probe as probe


class BrowserProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)

    def tearDown(self):
        self.temp.cleanup()

    def handler(self, route, body=b'', kind='fixture', origin=True):
        h = object.__new__(probe.Handler)
        h.server = SimpleNamespace(origin='http://127.0.0.1:12345', nonce='nonce', kind=kind,
                                   root=self.root, test_case='normal', saved=False, final_report=None)
        h.headers = {'Host':'127.0.0.1:12345','Origin':'http://127.0.0.1:12345' if origin else 'https://evil.example',
                     'Content-Length':str(len(body)), 'Content-Type':'video/webm'}
        h.path = '/nonce/' + route
        h.rfile = io.BytesIO(body)
        h.reply = lambda code, *args: setattr(h, 'response', code)
        return h

    def test_identity_endpoint_cannot_accept_video_or_serve_recording_assets(self):
        h = self.handler('video', bytes.fromhex('1a45dfa3')+b'not-a-video', kind='gamma-identity')
        h.do_POST(); self.assertEqual(h.response, 403); self.assertEqual(list(self.root.iterdir()), [])
        for name in ('fixture.html', 'recorder.html', 'probe.js'):
            h.path = '/nonce/' + name; h.do_GET(); self.assertEqual(h.response, 404)

    def test_wrong_origin_host_and_path_never_write(self):
        for change in ('origin', 'host', 'path'):
            h = self.handler('video', bytes.fromhex('1a45dfa3')+b'body', origin=change!='origin')
            if change == 'host': h.headers['Host']='evil.example'
            if change == 'path': h.path='/nonce/../video'
            h.do_POST(); self.assertEqual(h.response, 403); self.assertEqual(list(self.root.iterdir()), [])

    def test_oversized_chunked_and_invalid_video_rejected(self):
        h = self.handler('video', b'invalid');h.do_POST();self.assertEqual(h.response,400)
        h = self.handler('video', b'body');h.headers['Content-Length']=str(probe.MAX_VIDEO+1);h.do_POST();self.assertEqual(h.response,413)
        h = self.handler('video', b'body');h.headers['Transfer-Encoding']='chunked';h.do_POST();self.assertEqual(h.response,400)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_write_is_private_create_only_and_refuses_symlink(self):
        p=self.root/'file';probe.write_new(p,b'original')
        with self.assertRaises(FileExistsError):probe.write_new(p,b'changed')
        self.assertEqual(p.read_bytes(),b'original');self.assertEqual(p.stat().st_mode & 0o777,0o600)
        q=self.root/'link';q.symlink_to(p)
        with self.assertRaises(OSError):probe.write_new(q,b'changed')
        self.assertEqual(p.read_bytes(),b'original')

    def test_existing_public_root_rejected_without_chmod(self):
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):probe.private_root(self.root)
        self.assertEqual(self.root.stat().st_mode & 0o777,0o755)

    def request(self):
        return {'schema_version':1,'kind':'fixture','test_case':'normal','timeout_seconds':120,'audio':False,'third_party_recording':False}

    def test_same_root_replays_unknown_without_starting_server(self):
        probe.atomic_json(self.root/'probe-request.json', self.request())
        with patch.object(probe,'ProbeServer') as server:
            result=probe.serve(self.root)
            self.assertEqual(result['state'],'unknown');self.assertTrue(result['replayed']);server.assert_not_called()
        with self.assertRaises(ValueError):probe.serve(self.root,test_case='invalidate')

    def test_status_refuses_missing_version_bool_version_and_symlink(self):
        p=self.root/'probe-result.json'
        for data in ({'state':'saved'},{'schema_version':True,'state':'saved'}):
            p.write_text(json.dumps(data));p.chmod(0o600)
            with self.assertRaises(ValueError):probe.read_status(self.root)
            p.unlink()
        q=self.root/'target';q.write_text('{"schema_version":1}');q.chmod(0o600);p.symlink_to(q)
        with self.assertRaises(ValueError):probe.read_status(self.root)

    def test_terminal_report_requires_both_explicit_true_flags(self):
        for detail,expected in [({'terminal':True},False),({'terminal':True,'stream_stopped':True},True)]:
            data=json.dumps(dict(state='discarded',**detail)).encode()
            h=self.handler('report',data);h.do_POST();self.assertEqual(h.response,201)
            self.assertEqual(h.server.final_report is not None,expected)

    def test_gamma_preflight_cannot_enable_recording_test_cases(self):
        with self.assertRaises(ValueError):probe.serve(self.root,kind='gamma-identity',test_case='overflow')
        self.assertEqual(list(self.root.iterdir()),[])
