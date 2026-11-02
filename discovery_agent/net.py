"""Bounded public HTTPS GETs. No caller auth/cookies; compressed bodies rejected.

Default direct transport validates and pins public DNS addresses. Explicit
transport=managed_proxy instead uses the existing marked runtime HTTP CONNECT
proxy and its installed CA. In that mode the managed proxy, NOT this process,
is the trusted DNS/egress enforcement boundary; local destination DNS pinning
is not performed. There is no automatic fallback or configurable proxy URL.

Byte budgets cover body bytes, not HTTP headers; redirect bodies are never read.
An oversized streaming body may consume one sentinel byte beyond its cap before
being rejected. Header parsing retains the standard library line/count limits.

DNS runs in a daemon thread so the caller's overall deadline also bounds libc DNS.
A timed-out resolver may finish in the background, but never opens a connection.
"""
import http.client
import ipaddress
import math
import os
import re
import queue
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit, urlunsplit


class FetchError(ValueError):
    pass


class PolicyError(FetchError):
    pass


class BudgetError(FetchError):
    pass


class FetchTimeout(FetchError):
    pass


def _public_ip(value):
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise PolicyError('DNS returned an invalid address') from exc
    if (not address.is_global or address.is_multicast or address.is_reserved
            or getattr(address, 'ipv4_mapped', None) is not None
            or getattr(address, 'sixtofour', None) is not None
            or getattr(address, 'teredo', None) is not None):
        raise PolicyError('Non-public DNS address rejected')
    return address


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, timeout, remaining):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self.address = address
        self.remaining = remaining

    def connect(self):
        address = ipaddress.ip_address(self.address)
        sock = socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET,
                             socket.SOCK_STREAM)
        try:
            sock.settimeout(min(self.timeout, self.remaining()))
            sock.connect((str(address), 443))
            sock.settimeout(min(self.timeout, self.remaining()))
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class _ManagedProxyHTTPSConnection(http.client.HTTPSConnection):
    """Existing runtime proxy only; CONNECT carries no caller credentials."""
    def __init__(self, host, timeout, remaining):
        if os.environ.get('CODEX_NETWORK_PROXY_ACTIVE', '').lower() not in ('1', 'true', 'yes'):
            raise PolicyError('Managed proxy runtime marker is required')
        try:
            proxy = urlsplit(os.environ.get('HTTPS_PROXY', ''))
            if (proxy.scheme != 'http' or not proxy.hostname or proxy.username is not None
                    or proxy.password is not None or proxy.path not in ('', '/')
                    or proxy.query or proxy.fragment):
                raise ValueError('Unsupported managed proxy')
            # This narrow adapter only supports the runtime numeric proxy endpoint.
            proxy_ip = str(ipaddress.ip_address(proxy.hostname))
            proxy_port = proxy.port or 80
        except ValueError:
            raise PolicyError('Unsupported managed proxy configuration') from None
        cert = os.environ.get('CODEX_PROXY_CERT')
        if not cert or not os.path.isfile(cert):
            raise PolicyError('Existing managed proxy CA file is required')
        context = ssl.create_default_context()
        try:
            context.load_verify_locations(cafile=cert)
        except (OSError, ssl.SSLError):
            raise PolicyError('Managed proxy CA cannot be loaded') from None
        super().__init__(proxy_ip, port=proxy_port, timeout=timeout, context=context)
        self.set_tunnel(host, port=443)
        self.remaining = remaining

    def connect(self):
        address = ipaddress.ip_address(self.host)
        self.sock = socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET,
                                  socket.SOCK_STREAM)
        def expire():
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        timer = threading.Timer(self.remaining(), expire)
        timer.daemon = True
        timer.start()
        try:
            self.sock.settimeout(min(self.timeout, self.remaining()))
            self.sock.connect((str(address), self.port))
            self._tunnel()
            self.sock.settimeout(min(self.timeout, self.remaining()))
            self.sock = self._context.wrap_socket(self.sock, server_hostname=self._tunnel_host)
        except BaseException:
            self.close()
            raise
        finally:
            timer.cancel()


class Fetcher:
    """One bounded session; counters and total_timeout span all fetch() calls."""
    def __init__(self, config):
        self.transport = config.get('transport', 'direct')
        if self.transport not in ('direct', 'managed_proxy'):
            raise PolicyError('Unknown transport')
        self.allowed_domains = {str(x).lower() for x in config['allowed_domains']}
        self.max_requests = self._positive(config, 'max_requests', 8)
        self.max_bytes = self._positive(config, 'max_bytes', 2_000_000)
        self.max_total_bytes = self._positive(config, 'max_total_bytes', 8_000_000)
        self.max_redirects = self._positive(config, 'max_redirects', 3, zero=True)
        self.timeout = self._duration(config, 'timeout', 10)
        self.total_timeout = self._duration(config, 'total_timeout', 60)
        self.min_interval = self._duration(config, 'min_interval', 2, zero=True)
        self.requests = 0
        self.total_bytes = 0
        self._deadline = time.monotonic() + self.total_timeout
        self._last_request = {}

    @staticmethod
    def _positive(config, key, default, zero=False):
        value = config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < (0 if zero else 1):
            raise ValueError(key + ' must be a bounded positive integer')
        return value

    @staticmethod
    def _duration(config, key, default, zero=False):
        value = float(config.get(key, default))
        if not math.isfinite(value) or value < 0 or (not zero and value == 0):
            raise ValueError(key + ' must be a finite positive duration')
        return value

    def _remaining(self):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise FetchTimeout('Overall fetch deadline exceeded')
        return remaining

    def _validate_url(self, url):
        if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url) or '\\' in url:
            raise PolicyError('Invalid URL characters')
        try:
            parts = urlsplit(url)
            host = parts.hostname
            port = parts.port
        except ValueError as exc:
            raise PolicyError('Malformed URL') from exc
        if parts.scheme != 'https' or not host or parts.username is not None or parts.password is not None:
            raise PolicyError('Only credential-free HTTPS URLs are permitted')
        if port not in (None, 443) or host not in self.allowed_domains:
            raise PolicyError('Host or port not allowed')
        # Exact ASCII DNS names only, never ambiguous URL/IP parser forms.
        if not host.isascii() or host.endswith('.') or '%' in host:
            raise PolicyError('Non-canonical host rejected')
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            _public_ip(str(address))
        if self.transport == 'managed_proxy':
            if (not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', host)
                    or host.endswith(('.localhost', '.local', '.internal', '.home', '.lan', '.test', '.invalid', '.example'))):
                raise PolicyError('Managed proxy requires an approved public DNS name')
        return urlunsplit(('https', parts.netloc, parts.path or '/', parts.query, '')), host

    def _resolve(self, host):
        result = queue.Queue(maxsize=1)
        def work():
            try:
                result.put((True, socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
            except Exception as exc:
                result.put((False, exc))
        threading.Thread(target=work, daemon=True).start()
        try:
            ok, addresses = result.get(timeout=min(self.timeout, self._remaining()))
        except queue.Empty as exc:
            raise FetchTimeout('DNS deadline exceeded') from exc
        self._remaining()
        if not ok:
            raise FetchError('DNS lookup failed') from addresses
        if not addresses:
            raise FetchError('DNS returned no addresses')
        valid = []
        for family, socktype, protocol, name, sockaddr in addresses:
            if family not in (socket.AF_INET, socket.AF_INET6):
                raise PolicyError('Unexpected DNS address family')
            valid.append(str(_public_ip(sockaddr[0])))
        return valid[0]

    def fetch(self, url, etag=None):
        redirects = []
        original_host = None
        while True:
            url, host = self._validate_url(url)
            original_host = original_host or host
            self._remaining()
            if self.requests >= self.max_requests:
                raise BudgetError('Request budget exhausted')
            if self.total_bytes >= self.max_total_bytes:
                raise BudgetError('Total byte budget exhausted')
            delay = self.min_interval - (time.monotonic() - self._last_request.get(host, -math.inf))
            if delay > 0:
                if delay >= self._remaining():
                    raise FetchTimeout('Politeness interval exceeds deadline')
                time.sleep(delay)
            self.requests += 1  # Includes DNS/TLS failures and redirects.
            self._last_request[host] = time.monotonic()
            if self.transport == 'managed_proxy':
                connection = _ManagedProxyHTTPSConnection(host, min(self.timeout, self._remaining()), self._remaining)
            else:
                address = self._resolve(host)
                connection = _PinnedHTTPSConnection(host, address, min(self.timeout, self._remaining()), self._remaining)
            deadline_timer = None
            response = None
            try:
                parts = urlsplit(url)
                headers = {'User-Agent': 'auto-video-discovery/0.1 (+public-source-research)',
                           'Accept-Encoding': 'identity', 'Connection': 'close'}
                if etag is not None and host == original_host:
                    if not isinstance(etag, str) or len(etag) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in etag):
                        raise PolicyError('Invalid ETag')
                    headers['If-None-Match'] = etag
                connection.connect()
                transport = connection.sock
                def expire():
                    try:
                        transport.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                deadline_timer = threading.Timer(self._remaining(), expire)
                deadline_timer.daemon = True
                deadline_timer.start()
                connection.request('GET', urlunsplit(('', '', parts.path, parts.query, '')), headers=headers)
                transport.settimeout(min(self.timeout, self._remaining()))
                response = connection.getresponse()
                response_headers = {k.lower(): v for k, v in response.getheaders()}
                if response.status in (301, 302, 303, 307, 308):
                    if len(redirects) >= self.max_redirects:
                        raise BudgetError('Redirect budget exhausted')
                    location = response_headers.get('location')
                    if not location:
                        raise FetchError('Redirect has no Location')
                    target, _ = self._validate_url(urljoin(url, location))
                    redirects.append(target)
                    url = target
                    continue
                if response_headers.get('content-encoding', 'identity').strip().lower() not in ('', 'identity'):
                    raise PolicyError('Compressed responses are not accepted')
                limit = min(self.max_bytes, self.max_total_bytes - self.total_bytes)
                lengths = response.headers.get_all('Content-Length', [])
                if lengths:
                    if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                        raise PolicyError('Invalid Content-Length')
                    if int(lengths[0]) > limit:
                        raise BudgetError('Content-Length exceeds byte budget')
                body = bytearray()
                while not response.isclosed():
                    transport.settimeout(min(self.timeout, self._remaining()))
                    self._remaining()
                    chunk = response.read1(min(65536, limit - len(body) + 1))
                    self.total_bytes += len(chunk)
                    if len(body) + len(chunk) > limit:
                        raise BudgetError('Response exceeds byte budget')
                    if not chunk:
                        break
                    body.extend(chunk)
                self._remaining()
                return {'status': response.status, 'headers': response_headers,
                        'body': bytes(body), 'final_url': url, 'redirects': redirects}
            except (TimeoutError, socket.timeout) as exc:
                raise FetchTimeout('HTTPS request timed out') from exc
            except (OSError, http.client.HTTPException) as exc:
                raise FetchError('HTTPS request failed') from exc
            finally:
                if deadline_timer is not None:
                    deadline_timer.cancel()
                if response is not None:
                    response.close()
                connection.close()


SafeFetcher = Fetcher
