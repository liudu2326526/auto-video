"""Separate authenticated official-API and credential-free media transports."""
import json
import re
from urllib.parse import urlencode, urlsplit, parse_qs, parse_qsl

from .core import MaterialError, canonical, need
from .download import Transfer, DOH_HOST
from . import pexels, pixabay


API_HOSTS = {'pexels': 'api.pexels.com', 'pixabay': 'pixabay.com'}
ENV_KEYS = {'pexels': 'PEXELS_API_KEY', 'pixabay': 'PIXABAY_API_KEY'}
API_PATHS = {'pexels': {'/v1/search', '/v1/videos/search'}, 'pixabay': {'/api/', '/api/videos/'}}
MEDIA_HOSTS = frozenset(pexels.MEDIA_HOSTS | pixabay.MEDIA_HOSTS | {'fonts.gstatic.com'})


class MediaTransfer(Transfer):
    """No key or authentication argument exists on the public download role."""
    NETWORK_MODE = 'live'
    APPROVED_HOSTS = tuple(sorted(MEDIA_HOSTS))
    DNS_SUFFIXES = {host: (host.split('.', 1)[-1] if host.count('.') >= 2 else host,)
                    for host in MEDIA_HOSTS}
    DNS_SUFFIXES['fonts.gstatic.com'] = ('gstatic.com', 'google.com', '1e100.net')
    REQUEST_BUDGET = 12

    def once(self, address, **kwargs):
        p = urlsplit(address)
        names = [k for k, _ in parse_qsl(p.query, keep_blank_values=True)]
        need(not any(k.lower() in {'key', 'api_key', 'apikey', 'token', 'access_token', 'authorization', 'password'} for k in names),
             'Credentials forbidden in a public media URL')
        need(not (p.netloc == 'pixabay.com' and p.path.startswith('/api')), 'API paths forbidden on media role')
        if p.query:
            need(p.netloc == 'images.pexels.com' and len(names) == len(set(names))
                 and set(names) <= {'auto', 'cs', 'dpr', 'w', 'h', 'fit'}, 'Unapproved public media query')
            for key, value in parse_qsl(p.query, keep_blank_values=True):
                need((key == 'auto' and value == 'compress') or (key == 'cs' and value == 'tinysrgb')
                     or (key == 'fit' and value == 'crop') or (key == 'dpr' and value in {'1', '2'})
                     or (key in {'w', 'h'} and value.isascii() and value.isdigit() and 0 < int(value) <= 16384),
                     'Unapproved public media transformation')
        return super().once(address, **kwargs)

    def _request_headers(self, address, host, accept):
        headers = super()._request_headers(address, host, accept)
        need(not set(k.lower() for k in headers) & {'authorization', 'cookie', 'proxy-authorization', 'x-api-key'},
             'Public media must never receive credentials')
        return headers


class ApiTransfer(Transfer):
    def __init__(self, provider, key, resolver='system'):
        need(provider in API_HOSTS, 'Unknown official API')
        need(isinstance(key, str) and re.fullmatch(r'[A-Za-z0-9_-]{8,200}', key), 'Invalid API key format; value omitted')
        self.provider, self._secret = provider, key
        host = API_HOSTS[provider]
        self.APPROVED_HOSTS = (host,)
        self.DNS_SUFFIXES = {host: ('pexels.com',) if provider == 'pexels' else ('pixabay.com',)}
        super().__init__({'max_bytes': 2 * 1024 * 1024, 'total_timeout': 45, 'resolver': resolver})

    def once(self, address, **kwargs):
        p = urlsplit(address)
        need(p.scheme == 'https' and p.netloc == API_HOSTS[self.provider]
             and p.path in API_PATHS[self.provider] and not p.fragment, 'Unapproved authenticated API destination')
        query = parse_qs(p.query)
        if self.provider == 'pixabay':
            need(query.get('key') == [self._secret], 'Pixabay authentication must use the official API query')
        else:
            need('key' not in query, 'Pexels key must not appear in a URL')
        return super().once(address, **kwargs)

    def _request_headers(self, address, host, accept):
        headers = super()._request_headers(address, host, accept)
        if host == DOH_HOST:
            return headers  # DNS bootstrap never receives the provider credential.
        p = urlsplit(address)
        need(host == API_HOSTS[self.provider] and p.path in API_PATHS[self.provider], 'Credential destination mismatch')
        if self.provider == 'pexels':
            headers['Authorization'] = self._secret
        return headers


def api_get(provider, path, public_params, key, resolver='system'):
    """The private URL/key never escapes this boundary, including on an error."""
    need(provider in API_HOSTS and path in API_PATHS[provider], 'Unapproved API endpoint')
    allowed = ({'query', 'orientation', 'per_page', 'page'} if provider == 'pexels'
               else {'q', 'orientation', 'per_page', 'page', 'safesearch', 'image_type', 'video_type'})
    need(isinstance(public_params, dict) and set(public_params) <= allowed
         and all(isinstance(k, str) and k == k.lower() and type(v) in {str, int, bool}
                 for k, v in public_params.items()), 'Credentials/unknown fields forbidden in public request params')
    params = dict(public_params)
    if provider == 'pixabay':
        params['key'] = key
    status = None
    try:
        transport = ApiTransfer(provider, key, resolver)
        response = transport.once('https://' + API_HOSTS[provider] + path + '?' + urlencode(params),
                                  limit=2 * 1024 * 1024, allowed_mime={'application/json'})
        status = response['status']
        result = {'status': response['status'], 'headers': response['headers'], 'data': None,
                  'network_requests': transport.requests}
        if response['status'] == 200:
            def pairs(items):
                obj = {}
                for name, value in items:
                    need(name not in obj, 'Duplicate API JSON key')
                    obj[name] = value
                return obj
            result['data'] = json.loads(response['body'], object_pairs_hook=pairs,
                parse_constant=lambda _: (_ for _ in ()).throw(MaterialError('Nonfinite API JSON')))
        # Prevent even a malicious key-echo response from reaching cache, logs or stdout.
        need(key not in canonical(result), 'API response echoed a credential; discarded')
        return result
    except Exception:
        error = MaterialError('Official API transport/response failed; credentials and response details omitted')
        error.http_status = status
        raise error from None
