import email.message
import io
import http.client
import socket
import ssl
import unittest
from unittest.mock import Mock, patch
from discovery_agent.net import Fetcher, PolicyError, BudgetError, FetchTimeout, FetchError, _PinnedHTTPSConnection


def response(body=b'ok', status=200, headers=None):
    result = Mock(status=status)
    result.isclosed.return_value = False
    result.headers = email.message.Message()
    for key, value in (headers or {}).items():
        result.headers[key] = value
    result.getheaders.return_value = list(result.headers.items())
    chunks = [body, b'']
    result.read1.side_effect = lambda n: chunks.pop(0)
    return result


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.dns = patch('discovery_agent.net.socket.getaddrinfo', return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]).start()
        self.factory = patch('discovery_agent.net._PinnedHTTPSConnection').start()
        self.connection = self.factory.return_value
        self.connection.getresponse.return_value = response()
        self.addCleanup(patch.stopall)

    def fetcher(self, **kwargs):
        return Fetcher(dict(allowed_domains=['example.com'], min_interval=0, **kwargs))

    def test_normal_and_headers(self):
        fetcher = self.fetcher()
        result = fetcher.fetch('https://example.com/a?q=b#fragment', etag='"v1"')
        self.assertEqual(result['body'], b'ok')
        self.assertEqual(result['final_url'], 'https://example.com/a?q=b')
        self.assertEqual(fetcher.total_bytes, 2)
        args = self.connection.request.call_args
        self.assertEqual(args.args, ('GET', '/a?q=b'))
        self.assertEqual(args.kwargs['headers']['Accept-Encoding'], 'identity')
        self.assertNotIn('Authorization', args.kwargs['headers'])
        self.assertEqual(self.factory.call_args.args[:2], ('example.com', '93.184.216.34'))

    def test_bad_urls(self):
        for url in ['http://example.com', 'https://u:p@example.com', 'https://example.com:444/',
                    'https://example.com.evil/', 'https://example.com./', 'https://example.com/\n',
                    'https://169.254.169.254/', 'https://example.com\\@evil.com']:
            with self.subTest(url=url), self.assertRaises(PolicyError):
                self.fetcher().fetch(url)
        self.dns.assert_not_called()

    def test_all_dns_addresses_validated(self):
        for address in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '0.0.0.0', '::1',
                        '::ffff:93.184.216.34', '100.100.100.200', '224.0.0.1', '192.0.2.1']:
            self.dns.return_value.append((socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443)))
            with self.subTest(address=address), self.assertRaises(PolicyError):
                self.fetcher().fetch('https://example.com')
            self.dns.return_value.pop()
        self.factory.assert_not_called()

    def test_redirect_and_revalidation(self):
        self.connection.getresponse.side_effect = [response(status=302, headers={'Location': '/next'}), response()]
        f = self.fetcher()
        result = f.fetch('https://example.com')
        self.assertEqual(result['redirects'], ['https://example.com/next'])
        self.assertEqual(f.requests, 2)
        self.assertEqual(self.dns.call_count, 2)

    def test_downgrade_redirect(self):
        self.connection.getresponse.return_value = response(status=302, headers={'Location': 'http://example.com'})
        with self.assertRaises(PolicyError):
            self.fetcher().fetch('https://example.com')

    def test_redirect_to_private_dns(self):
        public = self.dns.return_value
        self.dns.side_effect = [public, [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))]]
        self.connection.getresponse.return_value = response(status=302, headers={'Location': '/next'})
        with self.assertRaises(PolicyError):
            self.fetcher().fetch('https://example.com')

    def test_request_and_redirect_budgets(self):
        f = self.fetcher(max_requests=1)
        f.fetch('https://example.com')
        with self.assertRaises(BudgetError):
            f.fetch('https://example.com')
        self.connection.getresponse.return_value = response(status=302, headers={'Location': '/next'})
        with self.assertRaises(BudgetError):
            self.fetcher(max_redirects=0).fetch('https://example.com')

    def test_dns_failure_consumes_request(self):
        self.dns.side_effect = OSError('dns error')
        f = self.fetcher(max_requests=1)
        with self.assertRaises(FetchError):
            f.fetch('https://example.com')
        self.assertEqual(f.requests, 1)
        with self.assertRaises(BudgetError):
            f.fetch('https://example.com')

    def test_content_length_and_actual_bytes(self):
        for headers in [{'Content-Length': '999'}, {'Content-Length': '-1'}, {'Content-Encoding': 'gzip'}]:
            self.connection.getresponse.return_value = response(headers=headers)
            with self.assertRaises(FetchError):
                self.fetcher(max_bytes=3).fetch('https://example.com')
        self.connection.getresponse.return_value = response(body=b'1234')
        with self.assertRaises(BudgetError):
            self.fetcher(max_bytes=3).fetch('https://example.com')

    def test_total_bytes(self):
        self.connection.getresponse.side_effect = [response(), response()]
        f = self.fetcher(max_total_bytes=3)
        f.fetch('https://example.com')
        with self.assertRaises(BudgetError):
            f.fetch('https://example.com')

    def test_timeout(self):
        self.connection.connect.side_effect = socket.timeout()
        with self.assertRaises(FetchTimeout):
            self.fetcher().fetch('https://example.com')
        f = self.fetcher()
        f._deadline = 0
        with self.assertRaises(FetchTimeout):
            f.fetch('https://example.com')

    def test_real_http_parser_closed_socket_body(self):
        class WireSocket:
            def makefile(self, *args):
                return io.BytesIO(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok')
        parsed = http.client.HTTPResponse(WireSocket())
        parsed.begin()
        self.connection.getresponse.return_value = parsed
        self.assertEqual(self.fetcher().fetch('https://example.com')['body'], b'ok')

    def test_dns_timeout_is_bounded(self):
        with patch('discovery_agent.net.queue.Queue') as queue_factory:
            import queue
            queue_factory.return_value.get.side_effect = queue.Empty()
            with self.assertRaises(FetchTimeout):
                self.fetcher().fetch('https://example.com')

    def test_invalid_configuration(self):
        for kwargs in [{'timeout': float('inf')}, {'max_requests': -1}, {'max_bytes': True}]:
            with self.assertRaises(ValueError):
                self.fetcher(**kwargs)


class TLSIntegrationUnitTests(unittest.TestCase):
    def test_connect_pins_ip_but_verifies_hostname(self):
        with patch('discovery_agent.net.socket.socket') as sock_factory:
            context = ssl.create_default_context()
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            connection = _PinnedHTTPSConnection('example.com', '93.184.216.34', 10, lambda: 20)
            with patch.object(connection, '_context') as mocked_context:
                connection.connect()
                sock_factory.return_value.connect.assert_called_once_with(('93.184.216.34', 443))
                mocked_context.wrap_socket.assert_called_once_with(sock_factory.return_value, server_hostname='example.com')

class ManagedProxyTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {
            'CODEX_NETWORK_PROXY_ACTIVE': '1', 'HTTPS_PROXY': 'http://127.0.0.1:8080',
            'CODEX_PROXY_CERT': '/managed/test-ca.pem'}, clear=True).start()
        patch('discovery_agent.net.os.path.isfile', return_value=True).start()
        self.context = patch('discovery_agent.net.ssl.create_default_context').start().return_value
        self.addCleanup(patch.stopall)

    def connection(self):
        from discovery_agent.net import _ManagedProxyHTTPSConnection
        return _ManagedProxyHTTPSConnection('example.com', 10, lambda: 20)

    def test_existing_proxy_connect_sni_and_ca(self):
        connection = self.connection()
        self.context.load_verify_locations.assert_called_once_with(cafile='/managed/test-ca.pem')
        self.assertEqual(connection._tunnel_host, 'example.com')
        self.assertNotIn('Proxy-Authorization', connection._tunnel_headers)
        self.assertNotIn('Authorization', connection._tunnel_headers)
        with patch('discovery_agent.net.socket.socket') as factory, patch.object(connection, '_tunnel') as tunnel:
            connection.connect()
            factory.return_value.connect.assert_called_once_with(('127.0.0.1', 8080))
            tunnel.assert_called_once()
            self.context.wrap_socket.assert_called_once_with(factory.return_value, server_hostname='example.com')

    def test_proxy_marker_ca_and_no_credentials(self):
        for changes in [{'CODEX_NETWORK_PROXY_ACTIVE': ''}, {'CODEX_PROXY_CERT': ''},
                        {'HTTPS_PROXY': 'http://user:secret@127.0.0.1:8080'},
                        {'HTTPS_PROXY': 'https://127.0.0.1:8080'},
                        {'HTTPS_PROXY': 'http://unapproved-proxy.example.com'}]:
            with self.subTest(changes=list(changes)), patch.dict('os.environ', changes), self.assertRaises(PolicyError):
                self.connection()

    def test_managed_transport_never_resolves_destination_locally(self):
        with patch('discovery_agent.net._ManagedProxyHTTPSConnection') as factory, patch('discovery_agent.net.socket.getaddrinfo') as dns:
            factory.return_value.getresponse.return_value = response()
            f = Fetcher({'allowed_domains': ['example.com'], 'transport': 'managed_proxy', 'min_interval': 0})
            self.assertEqual(f.fetch('https://example.com')['body'], b'ok')
            dns.assert_not_called()
            self.assertEqual(factory.call_args.args[0], 'example.com')

    def test_managed_rejects_ip_and_private_names_even_allowlisted(self):
        for host in ['93.184.216.34', '127.0.0.1', 'localhost', 'example.local', 'metadata.google.internal', '1234']:
            f = Fetcher({'allowed_domains': [host], 'transport': 'managed_proxy'})
            with self.subTest(host=host), self.assertRaises(PolicyError):
                f.fetch('https://' + host)

    def test_managed_redirect_still_checks_allowlist(self):
        with patch('discovery_agent.net._ManagedProxyHTTPSConnection') as factory:
            factory.return_value.getresponse.return_value = response(status=302, headers={'Location': 'https://unapproved.com/'})
            f = Fetcher({'allowed_domains': ['example.com'], 'transport': 'managed_proxy'})
            with self.assertRaises(PolicyError):
                f.fetch('https://example.com')
            self.assertEqual(factory.call_count, 1)

    def test_default_direct_never_falls_back_to_proxy(self):
        with patch('discovery_agent.net.socket.getaddrinfo', side_effect=OSError()), patch('discovery_agent.net._ManagedProxyHTTPSConnection') as proxy:
            with self.assertRaises(FetchError):
                Fetcher({'allowed_domains': ['example.com']}).fetch('https://example.com')
            proxy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
