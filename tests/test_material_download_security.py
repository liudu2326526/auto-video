"""Offline transport-policy tests: synthetic responses and real DNS/IP guards."""
import email.message
import hashlib
import http.client
import io
import json
import socket
import unittest
from unittest.mock import Mock, patch

from discovery_agent.net import BudgetError, FetchError, FetchTimeout, PolicyError
from material_agent.core import MaterialError
from material_agent.download import CONTACT, HOST, USER_AGENT, Transfer, media_url


ORIGINAL = 'https://upload.wikimedia.org/wikipedia/commons/6/6b/Laptop_image.jpg'
PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]


def response(body=b'jpeg fixture', status=200, headers=None):
    """Respect read1's byte bound; allow duplicate headers for ambiguity cases."""
    result = Mock(status=status)
    result.headers = email.message.Message()
    for key, value in (headers if headers is not None else [('Content-Type', 'image/jpeg')]):
        result.headers[key] = value
    source = io.BytesIO(body)
    result.isclosed.return_value = False
    result.read1.side_effect = source.read
    return result


class TransferSecurityTests(unittest.TestCase):
    def setUp(self):
        self.dns = self.start_patch('discovery_agent.net.socket.getaddrinfo', return_value=list(PUBLIC_DNS))
        self.factory = self.start_patch('material_agent.download._PinnedHTTPSConnection')
        self.connection = self.factory.return_value
        self.response = response()
        self.connection.getresponse.return_value = self.response
        # Avoid sleeping or spawning real deadline timers; no IP policy is patched.
        self.start_patch('material_agent.download.time.sleep')
        self.timer = self.start_patch('threading.Timer').return_value

    def start_patch(self, name, **kwargs):
        target = patch(name, **kwargs)
        value = target.start()
        self.addCleanup(target.stop)
        return value

    def transfer(self, maximum=1024):
        return Transfer({'max_bytes': maximum, 'total_timeout': 10})

    def once(self, transfer=None, limit=1024, sink=None):
        return (transfer or self.transfer()).once(ORIGINAL, limit=limit, sink=sink,
                                                 allowed_mime={'image/jpeg'})

    def test_public_ip_is_pinned_and_headers_have_contact_without_credentials(self):
        net = self.transfer()
        result = self.once(net)
        self.assertEqual(b'jpeg fixture', result['body'])
        self.assertEqual(hashlib.sha256(b'jpeg fixture').hexdigest(), result['sha256'])
        self.assertEqual(12, result['bytes'])
        self.assertEqual(12, net.total_bytes)
        self.assertEqual((HOST, '93.184.216.34'), self.factory.call_args.args[:2])
        self.connection.connect.assert_called_once_with()
        call = self.connection.request.call_args
        self.assertEqual(('GET', '/wikipedia/commons/6/6b/Laptop_image.jpg'), call.args)
        headers = call.kwargs['headers']
        self.assertEqual({'user-agent', 'accept-encoding', 'connection'}, {h.lower() for h in headers})
        self.assertEqual(USER_AGENT, headers['User-Agent'])
        self.assertIn(CONTACT, headers['User-Agent'])
        self.assertTrue(CONTACT.startswith('https://'))
        self.assertIn('auto-video-materials/', headers['User-Agent'])
        self.assertEqual('identity', headers['Accept-Encoding'])
        for forbidden in ('cookie', 'authorization', 'proxy-authorization'):
            self.assertNotIn(forbidden, {h.lower() for h in headers})
        self.response.close.assert_called_once_with()
        self.connection.close.assert_called_once_with()
        self.timer.cancel.assert_called_once_with()

    def test_private_or_mixed_dns_never_reaches_connection(self):
        for address in ('127.0.0.1', '10.0.0.1', '172.16.0.1', '192.168.1.1',
                        '169.254.169.254', '100.100.100.200', '0.0.0.0', '224.0.0.1',
                        '192.0.2.1', '::1', 'fc00::1', 'fe80::1', '::ffff:93.184.216.34'):
            family = socket.AF_INET6 if ':' in address else socket.AF_INET
            private = (family, socket.SOCK_STREAM, 6, '', (address, 443))
            for answers in ([private], [*PUBLIC_DNS, private]):
                with self.subTest(address=address, mixed=len(answers) > 1):
                    self.dns.return_value = answers
                    with self.assertRaises(PolicyError):
                        self.once()
        self.factory.assert_not_called()

    def test_dns_failure_and_connection_timeout_are_bounded(self):
        self.dns.side_effect = OSError('synthetic DNS failure')
        with self.assertRaises(FetchError):
            self.once()
        self.factory.assert_not_called()
        self.dns.side_effect = None
        self.connection.connect.side_effect = socket.timeout('synthetic connect timeout')
        with self.assertRaises(FetchTimeout):
            self.once()
        self.connection.close.assert_called_once_with()

    def test_body_timeout_closes_transport_and_response(self):
        self.response.read1.side_effect = socket.timeout('synthetic body timeout')
        with self.assertRaises(FetchTimeout):
            self.once()
        self.response.close.assert_called_once_with()
        self.connection.close.assert_called_once_with()
        self.timer.cancel.assert_called_once_with()

    def test_expired_overall_deadline_stops_before_dns(self):
        net = self.transfer()
        net._deadline = 0
        with self.assertRaises(FetchTimeout):
            self.once(net)
        self.dns.assert_not_called()
        self.factory.assert_not_called()

    def test_mime_compression_and_ambiguous_headers_rejected_before_body(self):
        cases = [
            [],
            [('Content-Type', 'text/html')],
            [('Content-Type', 'image/jpeg'), ('Content-Type', 'image/png')],
            [('Content-Type', 'image/jpeg'), ('Content-Encoding', 'gzip')],
            [('Content-Type', 'image/jpeg'), ('Content-Encoding', 'br')],
            [('Content-Type', 'image/jpeg'), ('Content-Encoding', 'identity'), ('Content-Encoding', 'identity')],
            [('Content-Type', 'image/jpeg'), ('Content-Length', '1'), ('Content-Length', '1')],
            [('Content-Type', 'image/jpeg'), ('Content-Length', '1'), ('Transfer-Encoding', 'chunked')],
            [('Content-Type', 'image/jpeg'), ('Transfer-Encoding', 'chunked'), ('Transfer-Encoding', 'chunked')],
            [('Content-Type', 'image/jpeg'), ('Transfer-Encoding', 'gzip')],
            [('Content-Type', 'image/jpeg'), ('Location', '/one'), ('Location', '/two')],
        ]
        for headers in cases:
            with self.subTest(headers=headers):
                self.connection.getresponse.return_value = result = response(headers=headers)
                with self.assertRaises(MaterialError):
                    self.once()
                result.read1.assert_not_called()
                result.close.assert_called_once_with()

    def test_malformed_lengths_and_declared_over_limit_stop_before_body(self):
        for length in ('-1', '+1', '1.0', '1, 1', ' 1', '1 ', 'nan', '\u0661'):
            with self.subTest(length=length):
                self.connection.getresponse.return_value = result = response(
                    headers=[('Content-Type', 'image/jpeg'), ('Content-Length', length)])
                with self.assertRaises(MaterialError):
                    self.once()
                result.read1.assert_not_called()
        self.connection.getresponse.return_value = result = response(
            headers=[('Content-Type', 'image/jpeg'), ('Content-Length', '5')])
        with self.assertRaises(BudgetError):
            self.once(limit=4)
        result.read1.assert_not_called()

    def test_actual_over_limit_does_not_write_sentinel_bytes(self):
        self.connection.getresponse.return_value = response(body=b'12345')
        sink = io.BytesIO()
        with self.assertRaises(BudgetError):
            self.once(limit=4, sink=sink)
        self.assertEqual(b'', sink.getvalue())

    def test_total_body_budget_applies_across_calls(self):
        net = self.transfer()
        net.max_total_bytes = 5
        self.connection.getresponse.return_value = response(body=b'123')
        self.once(net)
        self.connection.getresponse.return_value = response(body=b'456')
        with self.assertRaises(BudgetError):
            self.once(net)

    def test_truncated_declared_length_is_rejected(self):
        self.connection.getresponse.return_value = response(body=b'12', headers=[
            ('Content-Type', 'image/jpeg'), ('Content-Length', '3')])
        with self.assertRaisesRegex(MaterialError, 'Truncated'):
            self.once()

    def test_error_throttle_and_redirect_bodies_are_never_read_or_followed(self):
        for status in (301, 302, 303, 307, 308, 401, 403, 404, 429, 500):
            with self.subTest(status=status):
                self.factory.reset_mock()
                self.dns.reset_mock()
                self.connection.getresponse.return_value = result = response(status=status, headers=[
                    ('Content-Type', 'text/html'), ('Content-Encoding', 'gzip'),
                    ('Content-Length', '999999999'), ('Location', 'https://169.254.169.254/latest/meta-data/')])
                observed = self.once()
                self.assertEqual(status, observed['status'])
                self.assertEqual('https://169.254.169.254/latest/meta-data/', observed['location'])
                self.assertEqual(0, observed['bytes'])
                self.assertIsNone(observed['sha256'])
                self.assertNotIn('body', observed)
                result.read1.assert_not_called()
                self.assertEqual(1, self.factory.call_count)
                self.assertEqual(1, self.dns.call_count)

    def test_real_http_parser_closed_socket_and_chunked_body(self):
        for wire in (b'Content-Length: 2\r\n\r\nok',
                     b'Transfer-Encoding: chunked\r\n\r\n2\r\nok\r\n0\r\n\r\n'):
            class WireSocket:
                def makefile(self, *args):
                    return io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Type: image/jpeg\r\n'
                                      b'Connection: close\r\n' + wire)
            parsed = http.client.HTTPResponse(WireSocket())
            parsed.begin()
            self.connection.getresponse.return_value = parsed
            with self.subTest(wire=wire):
                self.assertEqual(b'ok', self.once()['body'])


class MediaUrlSecurityTests(unittest.TestCase):
    def test_canonical_public_original_is_accepted(self):
        self.assertEqual(ORIGINAL, media_url(ORIGINAL))

    def test_noncanonical_private_and_credential_urls_are_rejected(self):
        path = '/wikipedia/commons/6/6b/Laptop_image.jpg'
        bad = [
            'http://upload.wikimedia.org' + path,
            'file:///tmp/Laptop_image.jpg',
            'https://localhost' + path,
            'https://127.0.0.1' + path,
            'https://10.0.0.1' + path,
            'https://[::1]' + path,
            'https://169.254.169.254/latest/meta-data/',
            'https://metadata.google.internal/computeMetadata/v1/',
            'https://upload.wikimedia.org.evil.invalid' + path,
            'https://example.org' + path,
            'https://commons.wikimedia.org' + path,
            'https://user:password@upload.wikimedia.org' + path,
            'https://upload.wikimedia.org:443' + path,
            'https://upload.wikimedia.org:444' + path,
            'https://upload.wikimedia.org.' + path,
            'https://UPLOAD.WIKIMEDIA.ORG' + path,
            ORIGINAL + '?download=1', ORIGINAL + '#fragment',
            ORIGINAL.replace('Laptop_image', 'Laptop%5fimage'),
            ORIGINAL.replace('Laptop_image', '%2e%2e%2fLaptop_image'),
            ORIGINAL.replace('/6/6b/', '/archive/6/6b/'),
            ORIGINAL.replace('/6/6b/', '/6/6b/../'),
            ORIGINAL.replace('Laptop_image', '笔记本'),
            ORIGINAL.replace('.jpg', '.svg'),
            ORIGINAL.replace('/6/6b/', '/6/6b\\'),
            ORIGINAL + '\n', ORIGINAL + '\x00',
        ]
        for address in bad:
            with self.subTest(address=address), self.assertRaises((MaterialError, ValueError)):
                media_url(address)


def doh_answer():
    return {'Status': 0, 'TC': False, 'CD': False, 'AD': False,
            'Question': [{'name': HOST, 'type': 1}],
            'Answer': [{'name': HOST + '.', 'type': 1, 'TTL': 300, 'data': '93.184.216.34'}]}


class DoHTransferSecurityTests(unittest.TestCase):
    """An explicit fixed resolver must retain the original destination policy."""
    def setUp(self):
        self.dns = self.start_patch('discovery_agent.net.socket.getaddrinfo',
                                    side_effect=AssertionError('DoH must not call system DNS'))
        self.factory = self.start_patch('material_agent.download._PinnedHTTPSConnection')
        self.start_patch('material_agent.download.time.sleep')
        self.start_patch('threading.Timer')

    def start_patch(self, name, **kwargs):
        target = patch(name, **kwargs)
        result = target.start()
        self.addCleanup(target.stop)
        return result

    def exchange(self, data=None, *, status=200, raw=None, headers=None):
        self.resolver_connection, self.media_connection = Mock(), Mock()
        self.resolver_response = response(
            body=raw if raw is not None else json.dumps(data if data is not None else doh_answer()).encode(),
            status=status, headers=headers if headers is not None else [('Content-Type', 'application/dns-json')])
        self.resolver_connection.getresponse.return_value = self.resolver_response
        self.media_connection.getresponse.return_value = response()
        self.factory.reset_mock()
        self.factory.side_effect = [self.resolver_connection, self.media_connection]
        return Transfer({'max_bytes': 1024, 'total_timeout': 10, 'resolver': 'cloudflare_doh'})

    def once(self, net):
        return net.once(ORIGINAL, limit=1024, allowed_mime={'image/jpeg'})

    def assert_no_origin_or_system_fallback(self):
        self.dns.assert_not_called()
        self.assertEqual(1, self.factory.call_count)
        self.assertEqual(('cloudflare-dns.com', '1.1.1.1'), self.factory.call_args.args[:2])
        self.media_connection.connect.assert_not_called()

    def test_fixed_bootstrap_resolver_identity_accept_and_original_tls_target(self):
        net = self.exchange()
        result = self.once(net)
        self.assertEqual(b'jpeg fixture', result['body'])
        self.dns.assert_not_called()
        self.assertEqual(2, net.requests)
        self.assertEqual(('cloudflare-dns.com', '1.1.1.1'), self.factory.call_args_list[0].args[:2])
        self.assertEqual((HOST, '93.184.216.34'), self.factory.call_args_list[1].args[:2])
        call = self.resolver_connection.request.call_args
        self.assertEqual(('GET', '/dns-query?name=upload.wikimedia.org&type=A&cd=false'), call.args)
        headers = call.kwargs['headers']
        self.assertEqual('application/dns-json', headers['Accept'])
        self.assertEqual(USER_AGENT, headers['User-Agent'])
        self.assertEqual('identity', headers['Accept-Encoding'])
        self.assertEqual({'user-agent', 'accept', 'accept-encoding', 'connection'},
                         {key.lower() for key in headers})
        self.assertNotIn('Accept', self.media_connection.request.call_args.kwargs['headers'])
        # Same-operation cache uses the vetted address; it does not resolve again.
        self.assertEqual('93.184.216.34', net._resolve(HOST))
        self.assertEqual(2, self.factory.call_count)
        with self.assertRaises(MaterialError):
            net._resolve('another.wikimedia.org')

    def test_documented_fully_qualified_question_is_valid(self):
        data = doh_answer()
        data['Question'][0]['name'] = HOST + '.'
        net = self.exchange(data)
        self.assertEqual(b'jpeg fixture', self.once(net)['body'])
        self.dns.assert_not_called()

    def test_valid_cname_chain_pins_only_its_public_a_record(self):
        data = doh_answer()
        data['Answer'] = [
            {'name': HOST + '.', 'type': 5, 'TTL': 300, 'data': 'upload.eqiad.wikimedia.org.'},
            {'name': 'upload.eqiad.wikimedia.org.', 'type': 1, 'TTL': 300, 'data': '93.184.216.34'}]
        net = self.exchange(data)
        self.assertEqual(b'jpeg fixture', self.once(net)['body'])
        self.assertEqual((HOST, '93.184.216.34'), self.factory.call_args_list[1].args[:2])
        self.dns.assert_not_called()

    def test_private_mixed_and_non_ipv4_a_answers_block_without_fallback(self):
        for address in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '198.18.0.45',
                        '100.100.100.200', '192.0.2.1', '2606:4700:4700::1111'):
            for mixed in (False, True):
                with self.subTest(address=address, mixed=mixed):
                    data = doh_answer()
                    item = {'name': HOST + '.', 'type': 1, 'TTL': 30, 'data': address}
                    data['Answer'] = [*data['Answer'], item] if mixed else [item]
                    net = self.exchange(data)
                    with self.assertRaises((MaterialError, PolicyError)):
                        self.once(net)
                    self.assert_no_origin_or_system_fallback()

    def test_wrong_question_disabled_validation_truncation_and_error_status_block(self):
        cases = [
            ('Question', [{'name': 'other.wikimedia.org', 'type': 1}]),
            ('Question', [{'name': HOST, 'type': 28}]),
            ('Question', [{'name': HOST, 'type': 1}, {'name': HOST, 'type': 1}]),
            ('CD', True), ('TC', True), ('Status', 3), ('Status', False),
            ('Answer', []),
        ]
        for key, value in cases:
            with self.subTest(field=key, value=value):
                data = doh_answer()
                data[key] = value
                net = self.exchange(data)
                with self.assertRaises(MaterialError):
                    self.once(net)
                self.assert_no_origin_or_system_fallback()

    def test_cname_cycle_unrelated_answers_and_external_alias_block(self):
        records = [
            [
                {'name': HOST, 'type': 5, 'TTL': 30, 'data': 'upload.eqiad.wikimedia.org'},
                {'name': 'upload.eqiad.wikimedia.org', 'type': 5, 'TTL': 30, 'data': HOST},
            ],
            doh_answer()['Answer'] + [
                {'name': 'unrelated.wikimedia.org', 'type': 1, 'TTL': 30, 'data': '1.1.1.1'}],
            [{'name': HOST, 'type': 5, 'TTL': 30, 'data': 'example.org'},
             {'name': 'example.org', 'type': 1, 'TTL': 30, 'data': '93.184.216.34'}],
            doh_answer()['Answer'] + [
                {'name': HOST, 'type': 5, 'TTL': 30, 'data': 'upload.eqiad.wikimedia.org'}],
        ]
        for answers in records:
            with self.subTest(answers=answers):
                data = doh_answer()
                data['Answer'] = answers
                net = self.exchange(data)
                with self.assertRaises(MaterialError):
                    self.once(net)
                self.assert_no_origin_or_system_fallback()

    def test_doh_redirect_and_refusal_never_read_body_or_fallback(self):
        for status in (301, 302, 307, 308, 403, 429, 500):
            with self.subTest(status=status):
                net = self.exchange(status=status, headers=[
                    ('Content-Type', 'text/html'), ('Location', 'https://dns.google/resolve')])
                with self.assertRaises(MaterialError):
                    self.once(net)
                self.resolver_response.read1.assert_not_called()
                self.assert_no_origin_or_system_fallback()

    def test_bounded_json_mime_duplicates_and_malformed_data_block(self):
        cases = [
            {'raw': b'{not-json'},
            {'raw': b'{"Status":0,"Status":0}'},
            {'headers': [('Content-Type', 'text/plain')]},
            {'headers': [('Content-Type', 'application/dns-json'), ('Content-Length', '65537')]},
            {'raw': b' ' * 65537},
        ]
        for kwargs in cases:
            with self.subTest(case=list(kwargs)):
                net = self.exchange(**kwargs)
                with self.assertRaises((MaterialError, BudgetError)):
                    self.once(net)
                self.assert_no_origin_or_system_fallback()

    def test_doh_timeout_never_falls_back_to_system_dns(self):
        net = self.exchange()
        self.resolver_connection.connect.side_effect = socket.timeout('synthetic DoH timeout')
        with self.assertRaises(FetchTimeout):
            self.once(net)
        self.assert_no_origin_or_system_fallback()

    def test_system_mode_does_not_fallback_when_dns_returns_fake_ip(self):
        self.dns.side_effect = None
        self.dns.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('198.18.0.45', 443))]
        net = Transfer({'max_bytes': 1024, 'total_timeout': 10})
        with self.assertRaises(PolicyError):
            self.once(net)
        self.factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
