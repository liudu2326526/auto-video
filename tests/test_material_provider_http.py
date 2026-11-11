"""Offline credential-boundary tests; every key and response is SYNTHETIC_ONLY."""
import email.message
import io
import json
import socket
import traceback
import tempfile
from pathlib import Path
import subprocess
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock, patch

from material_agent.core import MaterialError
from material_agent.provider_http import API_HOSTS, MEDIA_HOSTS, ApiTransfer, MediaTransfer, api_get


CANARY = 'SYNTHETIC_ONLY_NOT_A_REAL_API_KEY_9z2X'
PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]


def response(body=b'{}', status=200, headers=None):
    result = Mock(status=status)
    result.headers = email.message.Message()
    for name, value in (headers if headers is not None else [('Content-Type', 'application/json')]):
        result.headers[name] = value
    source = io.BytesIO(body)
    result.isclosed.return_value = False
    result.read1.side_effect = source.read
    return result


class ProviderHttpSecurityTests(unittest.TestCase):
    def setUp(self):
        self.dns = self.start_patch('discovery_agent.net.socket.getaddrinfo', return_value=list(PUBLIC))
        self.factory = self.start_patch('material_agent.download._PinnedHTTPSConnection')
        self.connection = self.factory.return_value
        self.reply = response()
        self.connection.getresponse.return_value = self.reply
        self.start_patch('material_agent.download.time.sleep')
        self.start_patch('threading.Timer')
        self.start_patch('os.environ', {'PEXELS_API_KEY': CANARY, 'PIXABAY_API_KEY': CANARY,
                                       'HTTP_COOKIE': CANARY, 'AUTHORIZATION': CANARY})

    def start_patch(self, name, *args, **kwargs):
        target = patch(name, *args, **kwargs)
        value = target.start()
        self.addCleanup(target.stop)
        return value

    def request(self, provider='pexels', params=None, resolver='system'):
        return api_get(provider, '/v1/search' if provider == 'pexels' else '/api/',
                       {'query' if provider == 'pexels' else 'q': 'synthetic office', 'page': 1}
                       if params is None else params, CANARY, resolver)

    def assert_safe_result(self, result):
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(CANARY, rendered)
        self.assertNotIn('location', rendered.lower())
        self.assertNotIn('final_url', rendered.lower())

    def assert_safe_exception(self, operation):
        try:
            operation()
        except MaterialError as exc:
            rendered = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self.assertNotIn(CANARY, rendered)
            self.assertNotIn(CANARY, str(exc))
            self.assertNotIn(CANARY, repr(exc))
            return
        self.fail('Expected a redacted MaterialError')

    def test_pexels_raw_key_only_in_authorization_on_fixed_api(self):
        result = self.request()
        self.assert_safe_result(result)
        call = self.connection.request.call_args
        self.assertEqual('GET', call.args[0])
        self.assertEqual('/v1/search', urlsplit(call.args[1]).path)
        self.assertNotIn(CANARY, call.args[1])
        self.assertEqual(CANARY, call.kwargs['headers']['Authorization'])
        self.assertNotIn('Bearer', call.kwargs['headers']['Authorization'])
        self.assertEqual(('api.pexels.com', '93.184.216.34'), self.factory.call_args.args[:2])
        self.assertNotIn('Cookie', call.kwargs['headers'])
        self.assertNotIn('Proxy-Authorization', call.kwargs['headers'])

    def test_pixabay_key_only_in_official_api_query_never_authorization(self):
        result = self.request('pixabay')
        self.assert_safe_result(result)
        call = self.connection.request.call_args
        parsed = urlsplit(call.args[1])
        self.assertEqual('/api/', parsed.path)
        self.assertEqual([CANARY], parse_qs(parsed.query)['key'])
        headers = call.kwargs['headers']
        self.assertNotIn(CANARY, json.dumps(headers))
        self.assertNotIn('Authorization', headers)
        self.assertNotIn('Cookie', headers)
        self.assertEqual(('pixabay.com', '93.184.216.34'), self.factory.call_args.args[:2])

    def test_cloudflare_doh_never_receives_provider_key_or_api_query(self):
        for provider, host in API_HOSTS.items():
            with self.subTest(provider=provider):
                self.dns.reset_mock()
                self.factory.reset_mock()
                dns_connection, api_connection = Mock(), Mock()
                data = {'Status': 0, 'TC': False, 'CD': False,
                        'Question': [{'name': host + '.', 'type': 1}],
                        'Answer': [{'name': host + '.', 'type': 1, 'TTL': 30, 'data': '93.184.216.34'}]}
                dns_connection.getresponse.return_value = response(json.dumps(data).encode(), headers=[
                    ('Content-Type', 'application/dns-json')])
                api_connection.getresponse.return_value = response()
                self.factory.side_effect = [dns_connection, api_connection]
                result = self.request(provider, resolver='cloudflare_doh')
                self.assert_safe_result(result)
                self.dns.assert_not_called()
                self.assertEqual(('cloudflare-dns.com', '1.1.1.1'), self.factory.call_args_list[0].args[:2])
                dns_call = dns_connection.request.call_args
                self.assertEqual(('GET', '/dns-query?name=' + host + '&type=A&cd=false'), dns_call.args)
                self.assertNotIn(CANARY, repr(dns_call))
                self.assertNotIn('Authorization', dns_call.kwargs['headers'])
                self.assertNotIn('Cookie', dns_call.kwargs['headers'])
                self.assertEqual((host, '93.184.216.34'), self.factory.call_args_list[1].args[:2])

    def test_all_approved_media_hosts_receive_no_ambient_credentials(self):
        for host in sorted(MEDIA_HOSTS):
            with self.subTest(host=host):
                self.connection.getresponse.return_value = response(b'media', headers=[('Content-Type', 'video/mp4')])
                media = MediaTransfer({'max_bytes': 1024, 'total_timeout': 10})
                result = media.once('https://' + host + '/synthetic-fixture.mp4', limit=1024,
                                    allowed_mime={'video/mp4'})
                call = self.connection.request.call_args
                self.assertNotIn(CANARY, repr(call))
                self.assertNotIn('key=', call.args[1].lower())
                self.assertFalse({'authorization', 'cookie', 'proxy-authorization', 'x-api-key'}
                                 & {key.lower() for key in call.kwargs['headers']})
                self.assertNotIn(CANARY, repr(result))

    def test_media_role_rejects_credential_urls_before_dns(self):
        addresses = [
            'https://images.pexels.com/photos/123/fixture.jpeg?key=' + CANARY,
            'https://cdn.pixabay.com/photo/2026/01/01/00/00/fixture.jpg?api_key=' + CANARY,
            'https://pixabay.com/api/?key=' + CANARY,
            'https://fonts.gstatic.com/s/fixture.ttf?token=' + CANARY,
        ]
        for address in addresses:
            with self.subTest(host=urlsplit(address).hostname):
                self.dns.reset_mock()
                self.factory.reset_mock()
                media = MediaTransfer({'max_bytes': 1024, 'total_timeout': 10})
                with self.assertRaises((MaterialError, ValueError)):
                    media.once(address, limit=1024, allowed_mime={'application/json'})
                self.dns.assert_not_called()
                self.factory.assert_not_called()

    def test_api_redirect_does_not_follow_or_return_location_original_url(self):
        for provider in API_HOSTS:
            with self.subTest(provider=provider):
                self.dns.reset_mock()
                self.factory.reset_mock()
                self.connection.getresponse.return_value = reply = response(status=302, body=CANARY.encode(), headers=[
                    ('Content-Type', 'text/html'), ('Location', 'https://evil.invalid/?key=' + CANARY)])
                result = self.request(provider)
                self.assertEqual(302, result['status'])
                self.assert_safe_result(result)
                self.assertEqual(1, self.factory.call_count)
                self.assertEqual(1, self.dns.call_count)
                reply.read1.assert_not_called()

    def test_error_bodies_never_read_and_results_are_redacted(self):
        for status in (401, 403, 429, 500):
            for provider in API_HOSTS:
                with self.subTest(status=status, provider=provider):
                    self.connection.getresponse.return_value = reply = response(CANARY.encode(), status=status,
                        headers=[('Content-Type', 'text/plain'), ('Location', 'https://evil.invalid/' + CANARY)])
                    result = self.request(provider)
                    self.assertEqual(status, result['status'])
                    self.assert_safe_result(result)
                    reply.read1.assert_not_called()

    def test_connection_body_dns_and_json_exceptions_never_echo_key(self):
        for provider in API_HOSTS:
            for stage in ('connect', 'body', 'dns', 'json'):
                with self.subTest(provider=provider, stage=stage):
                    self.connection.connect.side_effect = None
                    self.dns.side_effect = None
                    self.connection.getresponse.return_value = reply = response()
                    if stage == 'connect':
                        self.connection.connect.side_effect = socket.timeout(CANARY)
                    elif stage == 'body':
                        reply.read1.side_effect = socket.timeout(CANARY)
                    elif stage == 'dns':
                        self.dns.side_effect = OSError(CANARY)
                    else:
                        self.connection.getresponse.return_value = response(b'{"malformed":"' + CANARY.encode() + b'",')
                    self.assert_safe_exception(lambda: self.request(provider))

    def test_successful_json_or_header_key_echo_is_discarded(self):
        cases = [
            (json.dumps({'nested': [{'echo': CANARY}]}).encode(), [('Content-Type', 'application/json')]),
            (json.dumps({CANARY: 'echoed JSON object key'}).encode(), [('Content-Type', 'application/json')]),
            (b'{}', [('Content-Type', 'application/json'), ('ETag', 'https://pixabay.com/api/?key=' + CANARY)]),
            (b'{}', [('Content-Type', 'application/json'), ('X-RateLimit-Reset', CANARY)]),
        ]
        for provider in API_HOSTS:
            for body, headers in cases:
                with self.subTest(provider=provider, kind='body' if body != b'{}' else 'header'):
                    self.connection.getresponse.return_value = response(body, headers=headers)
                    self.assert_safe_exception(lambda: self.request(provider))

    def test_public_params_never_accept_credential_keys_in_any_case(self):
        for key in ('key', 'token', 'api_key', 'authorization', 'KEY', 'Token', 'Api_Key', 'Authorization'):
            for provider in API_HOSTS:
                with self.subTest(provider=provider, param=key):
                    self.dns.reset_mock()
                    self.factory.reset_mock()
                    self.assert_safe_exception(lambda: self.request(provider, {key: CANARY}))
                    self.dns.assert_not_called()
                    self.factory.assert_not_called()

    def test_api_destination_validation_precedes_dns_and_transport(self):
        for provider, host in API_HOSTS.items():
            path = '/v1/search' if provider == 'pexels' else '/api/'
            valid = 'https://' + host + path
            query = '?key=' + CANARY if provider == 'pixabay' else '?query=synthetic'
            addresses = [
                valid.replace('https:', 'http:') + query,
                valid.replace(host, host + '.evil.invalid') + query,
                valid.replace(host, '127.0.0.1') + query,
                valid.replace(host, '169.254.169.254') + query,
                valid.replace(host, 'user:' + CANARY + '@' + host) + query,
                'https://' + host + '/unapproved-path' + query,
                valid + query + '#fragment',
            ]
            for address in addresses:
                with self.subTest(provider=provider, target=urlsplit(address).hostname):
                    self.dns.reset_mock()
                    self.factory.reset_mock()
                    transport = ApiTransfer(provider, CANARY)
                    with self.assertRaises((MaterialError, ValueError)) as caught:
                        transport.once(address, limit=1024, allowed_mime={'application/json'})
                    self.assertNotIn(CANARY, str(caught.exception))
                    self.dns.assert_not_called()
                    self.factory.assert_not_called()

    def test_media_probe_and_decoder_do_not_inherit_provider_api_keys(self):
        from material_agent.download import validate_media
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.jpg'
            path.write_bytes(b'\xff\xd8\xff' + b'\0' * 32)
            probe = {'streams': [{'codec_type': 'video', 'width': 2, 'height': 2}], 'format': {'format_name': 'jpeg_pipe'}}
            with patch('material_agent.download.subprocess.run', side_effect=[
                    subprocess.CompletedProcess([], 0, stdout=json.dumps(probe)), subprocess.CompletedProcess([], 0)]) as run:
                validate_media(path, 'image/jpeg', 'image')
                for call in run.call_args_list:
                    self.assertNotIn('PEXELS_API_KEY', call.kwargs['env'])
                    self.assertNotIn('PIXABAY_API_KEY', call.kwargs['env'])
                    self.assertNotIn(CANARY, json.dumps(call.kwargs['env']))


if __name__ == '__main__':
    unittest.main()
