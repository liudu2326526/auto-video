"""One explicitly requested public Commons original; never a crawler or login client.

Reuse discovery's public DNS validation, pinned TLS connection and deadline/budget
primitives. Follow media redirects here, only after validating the next URL and
robots. No inherited fetch() call (which would follow before our robots check).
"""
import fcntl
import hashlib
import http.client
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from datetime import timedelta
from urllib.parse import urljoin, urlsplit, parse_qs
import uuid

from discovery_agent.net import Fetcher, _PinnedHTTPSConnection, _public_ip, BudgetError, FetchError, FetchTimeout, PolicyError
from .core import (Library, MaterialError, canonical, context, digest, fields, file_hash, identity, instant,
                   location, need, now, stamp, text, validate_record)


CONTACT = 'https://github.com/liudu2326526/auto-video'
USER_AGENT = 'auto-video-materials/0.2 (+' + CONTACT + ')'
HOST = 'upload.wikimedia.org'
ROBOTS_URL = 'https://' + HOST + '/robots.txt'
DOH_HOST = 'cloudflare-dns.com'
DOH_URL = 'https://' + DOH_HOST + '/dns-query?name=' + HOST + '&type=A&cd=false'
TERMS_URLS = {
    'https://wikitech.wikimedia.org/wiki/Robot_policy',
    'https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy',
    'https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia',
}
LICENSES = {
    'CC0-1.0': {'https://creativecommons.org/publicdomain/zero/1.0/deed.en',
                'https://creativecommons.org/publicdomain/zero/1.0/'},
    'CC-BY-4.0': {'https://creativecommons.org/licenses/by/4.0/',
                   'https://creativecommons.org/licenses/by/4.0/deed.en'},
    'CC-BY-SA-4.0': {'https://creativecommons.org/licenses/by-sa/4.0/',
                      'https://creativecommons.org/licenses/by-sa/4.0/deed.en'},
}
MEDIA_PATH = re.compile(r'/wikipedia/commons/[a-f0-9]/[a-f0-9]{2}/[A-Za-z0-9_().,~+-]+\.(?:jpe?g|png|webp|mp4|webm|og[agv]|wav|mp3)', re.I)


def media_url(value):
    need(isinstance(value, str) and len(value) <= 2000 and value.isascii()
         and not any(ord(c) <= 32 or ord(c) == 127 for c in value), 'Invalid public download URL')
    p = urlsplit(value)
    need(p.scheme == 'https' and p.netloc == HOST and not p.query and not p.fragment
         and bool(MEDIA_PATH.fullmatch(p.path)), 'Only canonical public Commons originals on the approved host are supported')
    return value


class Transfer(Fetcher):
    """A bounded single-hop HTTPS GET; explicit User-Agent, no cookies or auth.

    Bodies stream to a private file (or <=64KiB robots buffer), <=1MB/s. All DNS
    results must be public and the selected IP is pinned while TLS verifies host.
    """
    APPROVED_HOSTS = (HOST,)
    DNS_SUFFIXES = {HOST: ('wikimedia.org',)}
    REQUEST_BUDGET = 5

    def __init__(self, policy):
        super().__init__({'transport': 'direct', 'allowed_domains': self.APPROVED_HOSTS, 'max_requests': self.REQUEST_BUDGET,
                          'max_bytes': policy['max_bytes'], 'max_total_bytes': policy['max_bytes'] * 3 + 131072,
                          'max_redirects': 0, 'timeout': 15, 'total_timeout': policy['total_timeout'], 'min_interval': 2})
        self.resolver = policy.get('resolver', 'system')
        need(self.resolver in {'system', 'cloudflare_doh'}, 'Unknown resolver; no automatic fallback')
        self._doh_address = None
        self._doh_addresses = {}

    def _request_start(self, host):
        self._remaining()
        need(self.requests < self.max_requests, 'Download request budget exhausted')
        delay = self.min_interval - (time.monotonic() - self._last_request.get(host, -math.inf))
        if delay > 0:
            if delay >= self._remaining():
                raise FetchTimeout('Politeness interval exceeds deadline')
            time.sleep(delay)
        self.requests += 1
        self._last_request[host] = time.monotonic()

    def once(self, address, *, limit, sink=None, allowed_mime=None):
        address, host = self._validate_url(address)
        self._request_start(host)
        pinned = self._resolve(host)
        return self._get(address, host, pinned, limit=limit, sink=sink, allowed_mime=allowed_mime)

    def _resolve(self, host):
        if self.resolver == 'system':
            return super()._resolve(host)
        need(host in self.allowed_domains, 'DoH query domain must be an approved fixed host')
        if host in self._doh_addresses:
            return self._doh_addresses[host]
        self._request_start(DOH_HOST)
        result = self._get(self._doh_url(host), DOH_HOST, '1.1.1.1', limit=65536,
                           allowed_mime={'application/dns-json'}, accept='application/dns-json')
        need(result['status'] == 200, 'Public DNS service refused; no redirect or fallback')
        try:
            def unique(pairs):
                obj = {}
                for key, value in pairs:
                    need(key not in obj, 'Ambiguous public DNS JSON')
                    obj[key] = value
                return obj
            data = json.loads(result['body'], object_pairs_hook=unique)
            need(type(data['Status']) is int and data['Status'] == 0 and data['TC'] is False and data['CD'] is False,
                 'Public DNS response failed/truncated/disabled validation')
            question = data['Question']
            need(isinstance(question, list) and len(question) == 1 and isinstance(question[0], dict)
                 and set(question[0]) == {'name', 'type'} and type(question[0]['type']) is int
                 and question[0]['type'] == 1 and isinstance(question[0]['name'], str)
                 and question[0]['name'].removesuffix('.').lower() == host, 'Public DNS question mismatch')
            answers = data['Answer']
            need(isinstance(answers, list) and 1 <= len(answers) <= 16, 'Public DNS answer count invalid')
            aliases, addresses = {}, {}
            def name(value):
                need(isinstance(value, str), 'Invalid public DNS name')
                normalized = value.removesuffix('.').lower()
                need(normalized == host or any(normalized == suffix or normalized.endswith('.' + suffix)
                     for suffix in self.DNS_SUFFIXES.get(host, ())), 'Public DNS alias outside approved provider')
                return normalized
            for item in answers:
                owner = name(item['name'])
                need(type(item['type']) is int, 'Invalid public DNS record type')
                if item['type'] == 1:
                    public = _public_ip(item['data'])
                    need(public.version == 4, 'Expected public IPv4 A record')
                    addresses.setdefault(owner, []).append(str(public))
                elif item['type'] == 5:
                    need(owner not in aliases, 'Ambiguous public DNS alias')
                    aliases[owner] = name(item['data'])
                else:
                    raise MaterialError('Unexpected public DNS answer type')
            current, chain = host, set()
            for _ in range(5):
                need(current not in chain, 'Public DNS alias loop')
                chain.add(current)
                if current in addresses:
                    need(current not in aliases and set(addresses) <= chain and set(aliases) <= chain,
                         'Unrelated/ambiguous public DNS answers')
                    self._doh_addresses[host] = addresses[current][0]
                    if host == HOST:
                        self._doh_address = self._doh_addresses[host]
                    return self._doh_addresses[host]
                need(current in aliases, 'Public DNS has no address for the approved question')
                current = aliases[current]
            raise MaterialError('Public DNS alias chain exceeds budget')
        except (ValueError, TypeError, KeyError) as exc:
            raise MaterialError('Invalid public DNS response; no fallback') from exc

    def _doh_url(self, host):
        need(host in self.allowed_domains, 'Unapproved DNS question')
        return 'https://' + DOH_HOST + '/dns-query?name=' + host + '&type=A&cd=false'

    def _request_headers(self, address, host, accept):
        headers = {'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity', 'Connection': 'close'}
        if accept:
            headers['Accept'] = accept
        return headers

    def _get(self, address, host, pinned, *, limit, sink=None, allowed_mime=None, accept=None):
        # Only the ordinary allowlisted URL or the fixed public DoH service may enter here.
        need(host in self.allowed_domains or host == DOH_HOST and pinned == '1.1.1.1'
             and any(address == self._doh_url(h) for h in self.allowed_domains),
             'Unapproved transport destination')
        _public_ip(pinned)
        conn = _PinnedHTTPSConnection(host, pinned, min(self.timeout, self._remaining()), self._remaining)
        response, timer = None, None
        try:
            conn.connect()
            transport = conn.sock
            import threading
            def expire():
                try:
                    transport.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            timer = threading.Timer(self._remaining(), expire)
            timer.daemon = True
            timer.start()
            p = urlsplit(address)
            request_headers = self._request_headers(address, host, accept)
            conn.request('GET', p.path + ('?' + p.query if p.query else ''), headers=request_headers)
            transport.settimeout(min(self.timeout, self._remaining()))
            response = conn.getresponse()
            headers = response.headers
            mime_values = headers.get_all('Content-Type', [])
            need(len(mime_values) <= 1, 'Ambiguous Content-Type rejected')
            mime = mime_values[0].split(';')[0].strip().lower() if mime_values else ''
            locations = headers.get_all('Location', [])
            need(len(locations) <= 1, 'Ambiguous redirect rejected')
            result = {'status': response.status, 'final_url': address, 'content_type': mime,
                      'location': locations[0] if locations else None, 'bytes': 0, 'sha256': None,
                      'headers': {k.lower(): headers[k] for k in (
                          'X-RateLimit-Limit', 'X-RateLimit-Remaining', 'X-RateLimit-Reset',
                          'Retry-After', 'Last-Modified', 'ETag') if headers.get(k) is not None}}
            # Never read login, error, throttling or redirect bodies; never send credentials.
            if response.status != 200:
                return result
            need(allowed_mime is None or mime in allowed_mime, 'Unsupported response Content-Type')
            encoding = headers.get_all('Content-Encoding', [])
            need(len(encoding) <= 1 and (not encoding or encoding[0].strip().lower() in {'identity', ''}),
                 'Compressed response rejected')
            lengths = headers.get_all('Content-Length', [])
            transfers = headers.get_all('Transfer-Encoding', [])
            need(len(lengths) <= 1 and len(transfers) <= 1 and not (lengths and transfers), 'Ambiguous transfer length rejected')
            need(not transfers or transfers[0].strip().lower() == 'chunked', 'Unsupported Transfer-Encoding')
            declared = None
            if lengths:
                need(lengths[0].isascii() and lengths[0].isdigit(), 'Invalid Content-Length')
                declared = int(lengths[0])
                if declared > limit:
                    raise BudgetError('Content-Length exceeds download limit')
            output = sink if sink is not None else io.BytesIO()
            count, h, started = 0, hashlib.sha256(), time.monotonic()
            while not response.isclosed():
                transport.settimeout(min(self.timeout, self._remaining()))
                chunk = response.read1(min(65536, limit - count + 1))
                if not chunk:
                    break
                count += len(chunk)
                self.total_bytes += len(chunk)
                if count > limit or self.total_bytes > self.max_total_bytes:
                    raise BudgetError('Downloaded bytes exceed budget')
                output.write(chunk)
                h.update(chunk)
                # Single-process, single-transfer 8Mbps ceiling; substantially below provider limit.
                pause = count / 1_000_000 - (time.monotonic() - started)
                if pause > 0:
                    if pause >= self._remaining():
                        raise FetchTimeout('Rate limit exceeds deadline')
                    time.sleep(pause)
                self._remaining()
            need(declared is None or count == declared, 'Truncated response length')
            result.update(bytes=count, sha256=h.hexdigest())
            if sink is None:
                result['body'] = output.getvalue()
            return result
        except (TimeoutError, socket.timeout) as exc:
            raise FetchTimeout('Download timed out') from exc
        except (OSError, http.client.HTTPException) as exc:
            raise FetchError('Public download transport failed') from exc
        finally:
            if timer:
                timer.cancel()
            if response is not None:
                response.close()
            conn.close()


def preflight(record, policy, use, at):
    validate_record(record, at)
    context(use)
    fields(policy, ['schema', 'provider', 'record_digest', 'terms_checked_at', 'terms_evidence',
                    'expected_sha256', 'max_bytes', 'total_timeout'], ['resolver'])
    need(policy['schema'] == 'material_download_policy/v1' and policy['provider'] == 'commons_original', 'Unsupported download policy/provider')
    need(policy['record_digest'] == digest(record), 'Download policy must bind this exact source/license record')
    need(policy.get('resolver', 'system') in {'system', 'cloudflare_doh'}, 'Unknown resolver; no automatic fallback')
    need(type(policy['max_bytes']) is int and 1 <= policy['max_bytes'] <= 32 * 1024 * 1024, 'Download limit must be 1..32 MiB')
    need(type(policy['total_timeout']) is int and 5 <= policy['total_timeout'] <= 120, 'Network deadline must be 5..120 seconds')
    need(at - timedelta(days=30) <= instant(policy['terms_checked_at']) <= at, 'Provider terms verification missing/expired')
    need(isinstance(policy['terms_evidence'], list) and len(policy['terms_evidence']) == 3, 'Three official provider-rule observations required')
    observed = set()
    for e in policy['terms_evidence']:
        fields(e, ['url', 'excerpt', 'retrieved_at'])
        need(e['url'] in TERMS_URLS, 'Unreviewed provider terms URL')
        text(e['excerpt'], 4000)
        need(instant(e['retrieved_at']) <= instant(policy['terms_checked_at']), 'Terms check predates its evidence')
        observed.add(e['url'])
    need(observed == TERMS_URLS, 'Missing official provider-rule evidence')
    need(policy['expected_sha256'] is None or isinstance(policy['expected_sha256'], str)
         and bool(re.fullmatch(r'[a-f0-9]{64}', policy['expected_sha256'])), 'Invalid expected SHA-256')
    source, rights = record['source'], record['rights']
    need(source['access'] == 'public_no_login', 'Login/unknown-history downloading is unsupported')
    address = media_url(source['download_url'])
    name = urlsplit(address).path.rsplit('/', 1)[-1]
    need(source['page_url'] == 'https://commons.wikimedia.org/wiki/File:' + name, 'Source file page does not match download identity')
    need(rights['license_id'] in LICENSES and rights['license_url'] in LICENSES[rights['license_id']],
         'Automatic download currently supports CC0/BY/BY-SA 4.0 with official terms only')
    asset_evidence = [e for e in rights['evidence'] if e['scope'] == 'asset_license']
    def same_file(e):
        p = urlsplit(e['url'])
        return (e['url'] == source['page_url'] or p.netloc == 'commons.wikimedia.org' and p.path == '/w/index.php'
                and parse_qs(p.query).get('title') == ['File:' + name])
    need(any(same_file(e) for e in asset_evidence), 'Asset-specific license evidence must identify the exact source file')
    issues = Library._rights_issues(record, use, at)
    need(not issues, '; '.join(issues))
    need(shutil.which('ffprobe') and shutil.which('ffmpeg'), 'ffprobe and ffmpeg required for download validation')
    return address


MIMES = {'image/jpeg', 'image/png', 'image/webp', 'video/mp4', 'video/webm', 'audio/webm',
         'audio/wav', 'audio/x-wav', 'audio/mpeg', 'audio/ogg', 'video/ogg', 'application/ogg'}


def validate_media(path, mime, expected_kind, *, allow_animation=False):
    with Path(path).open('rb') as stream:
        head = stream.read(32)
    animated = head.startswith((b'GIF87a', b'GIF89a'))
    if animated:
        need(allow_animation, 'Animated GIF requires explicit full-animation validation')
        allowed, suffix, kind = {'image/gif'}, '.gif', 'image'
    elif head.startswith(b'\xff\xd8\xff'):
        allowed, suffix, kind = {'image/jpeg'}, '.jpg', 'image'
    elif head.startswith(b'\x89PNG\r\n\x1a\n'):
        allowed, suffix, kind = {'image/png'}, '.png', 'image'
    elif head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        allowed, suffix, kind = {'image/webp'}, '.webp', 'image'
    elif head[4:8] == b'ftyp' and head[8:12] in {b'isom', b'iso2', b'mp41', b'mp42', b'avc1'}:
        allowed, suffix, kind = {'video/mp4'}, '.mp4', 'video'
    elif head.startswith(b'\x1a\x45\xdf\xa3'):
        need(expected_kind in {'video', 'audio'}, 'WebM cannot be classified as a still image')
        allowed, suffix, kind = {'video/webm', 'audio/webm'}, '.webm', expected_kind
    elif head[:4] == b'RIFF' and head[8:12] == b'WAVE':
        allowed, suffix, kind = {'audio/wav', 'audio/x-wav'}, '.wav', 'audio'
    elif head.startswith(b'OggS'):
        need(expected_kind in {'video', 'audio'}, 'Ogg cannot be classified as a still image')
        allowed, suffix, kind = {'audio/ogg', 'video/ogg', 'application/ogg'}, '.ogv' if expected_kind == 'video' else '.ogg', expected_kind
    elif head.startswith(b'ID3') or len(head) > 1 and head[0] == 0xff and head[1] & 0xe0 == 0xe0:
        allowed, suffix, kind = {'audio/mpeg'}, '.mp3', 'audio'
    else:
        raise MaterialError('Unsupported media magic; HTML/SVG/archive/executable forbidden')
    need(mime in allowed and kind == expected_kind, 'MIME, file magic and declared media kind disagree')
    command = ['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe']
    if animated:
        command += ['-count_frames']
    command += ['-show_entries', 'stream=codec_type,width,height,nb_read_frames:format=duration,format_name',
                '-of', 'json', str(path)]
    # Decoding needs executable/locale/temp settings, not the parent's credentials.
    child_env = {k: v for k, v in os.environ.items() if k in {
        'PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TZ', 'TMPDIR', 'TMP', 'TEMP', 'SYSTEMROOT', 'WINDIR'}}
    try:
        raw = subprocess.run(command, check=True, capture_output=True, text=True, timeout=15, env=child_env)
        need(len(raw.stdout) <= 65536, 'Media metadata exceeds limit')
        info = json.loads(raw.stdout)
        streams = info['streams']
        need(1 <= len(streams) <= 8, 'Unsupported stream count')
        video = [s for s in streams if s.get('codec_type') == 'video']
        audio = [s for s in streams if s.get('codec_type') == 'audio']
        need((kind in {'image', 'video'} and len(video) == 1) or (kind == 'audio' and audio and not video), 'Decoded media kind mismatch')
        for stream in video:
            w, h = stream['width'], stream['height']
            need(type(w) is int and type(h) is int and 0 < w <= 16384 and 0 < h <= 16384
                 and w * h <= 40_000_000, 'Image/video dimensions exceed decode budget')
        animation = None
        if animated:
            frames = int(video[0]['nb_read_frames'])
            duration = float(info['format']['duration'])
            need(1 < frames <= 300 and math.isfinite(duration) and 0 < duration <= 30
                 and frames * video[0]['width'] * video[0]['height'] <= 200_000_000,
                 'Animation frame/duration/decode-work budget exceeded or not animated')
            animation = {'animated': True, 'frames': frames, 'duration_ms': round(duration * 1000),
                         'editing_compatible': False}
        if kind != 'image':
            duration = float(info['format']['duration'])
            need(math.isfinite(duration) and 0 < duration <= 600, 'Media duration exceeds 10 minute limit')
        decode = ['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-max_alloc', '67108864', '-threads', '1',
                  '-protocol_whitelist', 'file,pipe', '-i', str(path)]
        if kind == 'image' and not animated:
            decode += ['-frames:v', '1']
        subprocess.run(decode + ['-f', 'null', '-'], check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=15, env=child_env)
    except (OSError, subprocess.SubprocessError, KeyError, ValueError) as exc:
        raise MaterialError('Media probe/decode failed or exceeded validation deadline') from exc
    result = {'mime': mime, 'media_type': kind, 'suffix': suffix, 'probe': info, 'decoded': True}
    if animation:
        result['animation'] = animation
    return result


class DownloadLock:
    """One media download per local user, including across different libraries."""
    def __enter__(self):
        path = location(Path(tempfile.gettempdir()) / ('auto-video-material-download-' + str(os.getuid()) + '.lock'))
        self.fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            info = os.fstat(self.fd)
            need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600,
                 'Unsafe download lock file')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(self.fd)
            raise MaterialError('Another download is active or the download lock is unsafe')
        return self

    def __exit__(self, *args):
        os.close(self.fd)


def event(library, attempt, record_id, outcome, details, *, commit=True):
    payload = {'schema': 'material_download_event/v1', 'attempt_id': attempt, 'record_id': record_id,
               'at': stamp(), 'outcome': outcome, 'details': details}
    def insert():
        library.db.execute('INSERT INTO download_events(attempt_id,record_id,document) VALUES (?,?,?)',
                           (attempt, record_id, canonical(payload)))
    if commit:
        with library.db:
            insert()
    else:
        insert()


class PrefixRobots:
    def __init__(self, prefixes):
        self.prefixes = prefixes

    def can_fetch(self, agent, address):
        return not any(urlsplit(address).path.startswith(p) for p in self.prefixes)

    def crawl_delay(self, agent):
        return None  # Other directives are rejected, never silently ignored.

    def request_rate(self, agent):
        return None


def parse_robots(body):
    """Fail closed outside the initial provider's universal disallow-prefix rules.

    stdlib robotparser lacks RFC9309 wildcard/longest-match and group merging.
    Do not silently allow media if the provider changes to such a policy.
    """
    rules = body.decode('utf-8-sig')
    need('<html' not in rules.casefold(), 'Robots returned HTML')
    groups, prefixes = 0, []
    for raw in rules.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        key, separator, value = line.partition(':')
        key, value = key.strip().lower(), value.strip()
        need(separator and key in {'user-agent', 'disallow', 'sitemap'}, 'Unsupported robots directive; stop for review')
        if key == 'user-agent':
            groups += 1
            need(groups == 1 and value == '*', 'Unsupported robots user-agent groups; stop for review')
        elif key == 'disallow':
            need(groups == 1 and '*' not in value and '$' not in value
                 and value.isascii() and not any(c.isspace() for c in value)
                 and not any(c in value for c in '%\\?')
                 and (not value or value.startswith('/')), 'Unsupported robots rule syntax; stop for review')
            if value:
                prefixes.append(value)  # Empty Disallow is a no-op, never an allow-all override.
    return PrefixRobots(prefixes)


def download_record(library, record, policy, use):
    attempt = 'download_' + uuid.uuid4().hex
    rid = record.get('id') if isinstance(record, dict) else None
    identity(rid)
    event(library, attempt, rid, 'started', {})
    try:
        at = now()
        address = preflight(record, policy, use, at)
        existing = library.db.execute('SELECT id FROM records WHERE id=?', (rid,)).fetchone()
        if existing:
            row = library.get(rid)
            need(row['digest'] == digest(record), 'Existing immutable record differs')
            need(row['blob_sha256'] is not None, 'Metadata-only record exists; download with a new record ID')
            path = library._blob(row['blob_sha256'])
            previous = json.loads(row['download_record'])
            need(previous['method'] == 'automated_public_download' and previous.get('verification', {}).get('file_sha256') == row['blob_sha256']
                 and previous['verification']['media_validation'].get('decoded') is True,
                 'Existing local import has no automatic validation; retain it and use a new record ID')
            need(path.stat().st_size <= policy['max_bytes'], 'Cached file exceeds this download policy limit')
            need(policy['expected_sha256'] in {None, row['blob_sha256']}, 'Cached file SHA-256 mismatch')
            event(library, attempt, rid, 'reused_local', {'sha256': row['blob_sha256'], 'network_requests': 0,
                                                       'record_digest': row['digest'], 'policy_digest': digest(policy),
                                                       'use_context': use})
            return {'record': row, 'outcome': 'reused_local', 'network_requests': 0, 'attempt_id': attempt}
        with DownloadLock(), tempfile.TemporaryDirectory(prefix='.download-', dir=library.root) as temporary:
            net = Transfer(policy)
            robots = net.once(ROBOTS_URL, limit=65536, allowed_mime={'text/plain'})
            need(robots['status'] == 200, 'Robots unavailable/denied; stop without media request')
            parser = parse_robots(robots['body'])
            delay = parser.crawl_delay('auto-video-materials')
            rate = parser.request_rate('auto-video-materials')
            if rate:
                delay = max(delay or 0, rate.seconds / rate.requests)
            need(delay is None or delay <= 10, 'Provider crawl delay exceeds supported single-transfer budget')
            net.min_interval = max(2, delay or 0)
            target = Path(temporary) / 'payload.part'
            visited, chain = set(), []
            current = address
            for hop in range(3):
                media_url(current)
                need(current not in visited, 'Redirect loop rejected')
                visited.add(current)
                need(parser.can_fetch('auto-video-materials', current), 'robots.txt forbids this media path')
                with target.open('wb') as stream:
                    response = net.once(current, limit=policy['max_bytes'], sink=stream, allowed_mime=MIMES)
                if response['status'] in {301, 302, 303, 307, 308}:
                    need(hop < 2 and response['location'], 'Media redirect budget exhausted or missing Location')
                    next_url = media_url(urljoin(current, response['location']))
                    need(urlsplit(next_url).path.rsplit('/', 1)[-1] == urlsplit(address).path.rsplit('/', 1)[-1],
                         'Redirect changes the licensed file identity')
                    chain.append({'from': current, 'to': next_url, 'status': response['status']})
                    current = next_url
                    continue
                need(response['status'] == 200, 'Media HTTP access/status refused; no login, retries or bypass')
                break
            need(response['bytes'] > 0 and file_hash(target) == response['sha256'], 'Empty or changed temporary download')
            need(policy['expected_sha256'] in {None, response['sha256']}, 'Downloaded SHA-256 mismatch')
            checked = validate_media(target, response['content_type'], record['media_type'])
            final_path = target.with_suffix(checked['suffix'])
            target.rename(final_path)
            downloaded = now()
            preflight(record, policy, use, downloaded)  # A license can expire during transfer/validation.
            verification = {'schema': 'material_download_verification/v1', 'policy_digest': digest(policy),
                            'file_sha256': response['sha256'], 'byte_count': response['bytes'],
                            'requested_url': address, 'final_url': current, 'redirects': chain,
                            'robots_url': ROBOTS_URL, 'robots_sha256': robots['sha256'],
                            'robots_checked_at': stamp(at), 'media_validation': checked,
                            'network_requests': net.requests, 'user_agent': USER_AGENT, 'use_context': use}
            verification['resolver'] = policy.get('resolver', 'system')
            row = library.add(record, final_path, {'downloaded_at': stamp(downloaded), 'method': 'automated_public_download',
                               'source_url': address, 'verification': verification}, at=downloaded,
                               _before_commit=lambda: event(library, attempt, rid, 'downloaded',
                                                            {'sha256': response['sha256'], 'verification': verification}, commit=False))
            return {'record': row, 'outcome': 'downloaded', 'network_requests': net.requests, 'attempt_id': attempt}
    except BaseException as exc:
        library.db.rollback()
        # No server body, cookie, credential-bearing URL or arbitrary exception text is logged.
        event(library, attempt, rid, 'failed', {'error_type': type(exc).__name__,
                                             'reason': str(exc)[:500] if isinstance(exc, (MaterialError, FetchError)) else 'Operation interrupted/failed'})
        raise
