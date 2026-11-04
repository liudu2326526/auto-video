"""Pure Pixabay request/response adapter. No keys, I/O, auth or licensing approval.

Only the reviewed official JPEG/MP4 paths enter variants. A rejected variant
retains its fixed rendition ID and a reason, never the untrusted response URL.
API responses may add fields; only explicitly selected metadata is retained.
"""
import math
import re
from urllib.parse import urlsplit

from .core import MaterialError, need


MEDIA_HOSTS = frozenset({'pixabay.com', 'cdn.pixabay.com'})
_ORIENTATIONS = {'any', 'all', 'landscape', 'portrait', 'square'}
_DATE = r'[0-9]{4}/[0-9]{2}/[0-9]{2}'
_PHOTO = re.compile(r'/photo/' + _DATE + r'/[0-9]{2}/[0-9]{2}/[A-Za-z0-9_-]+\.jpe?g', re.I)
_VIDEO = re.compile(r'/video/' + _DATE + r'/[A-Za-z0-9_-]+\.mp4', re.I)
_GET = re.compile(r'/get/[A-Za-z0-9_-]+\.jpe?g', re.I)
_PAGE = re.compile(r'/(?:photos|illustrations|vectors|videos|[a-z]{2})/[A-Za-z0-9_-]+-([1-9][0-9]{0,19})/')
_CREATOR = re.compile(r'[A-Za-z0-9_-]{1,100}')


def _kind(value):
    need(isinstance(value, str) and value in {'image', 'video'}, 'Pixabay requires image or video kind')
    return value


def _clean_text(value, maximum, *, empty=False):
    need(isinstance(value, str) and len(value) <= maximum
         and (empty or value.strip())
         and not any(ord(c) < 32 or ord(c) == 127 for c in value), 'Invalid Pixabay text field')
    return value.strip()


def _identifier(value):
    if type(value) is int:
        need(1 <= value <= 10 ** 20 - 1, 'Invalid Pixabay asset identity')
        return str(value)
    need(isinstance(value, str) and bool(re.fullmatch(r'[1-9][0-9]{0,19}', value)), 'Invalid Pixabay asset identity')
    return value


def _positive_int(value, *, optional=False):
    if value is None and optional:
        return None
    need(type(value) is int and 0 < value <= 2 ** 63 - 1, 'Invalid Pixabay media metadata')
    return value


def _dimension(value):
    if value is None:
        return None
    need(type(value) is int and 0 < value <= 65536, 'Invalid Pixabay media dimensions')
    return value


def _duration(value):
    if value is None:
        return None
    need(type(value) in {int, float} and 0 < value <= 86400 and math.isfinite(value),
         'Invalid Pixabay duration metadata')
    return value


def _url_parts(value):
    need(isinstance(value, str) and 0 < len(value) <= 4000 and value.isascii()
         and not any(ord(c) <= 32 or ord(c) == 127 for c in value)
         and '\\' not in value and '%' not in value, 'Invalid Pixabay URL')
    try:
        p = urlsplit(value)
    except ValueError:
        # urllib can include an invalid authority in its exception text.
        raise MaterialError('Invalid Pixabay URL') from None
    need(p.scheme == 'https' and p.username is None and p.password is None
         and not p.query and not p.fragment, 'Invalid Pixabay URL')
    return p


def _source_url(value, asset_id, kind):
    p = _url_parts(value)
    match = _PAGE.fullmatch(p.path)
    need(p.netloc == 'pixabay.com' and match is not None and match.group(1) == asset_id,
         'Invalid Pixabay source page')
    if kind == 'video':
        need(p.path.startswith('/videos/'), 'Invalid Pixabay video source page')
    else:
        need(not p.path.startswith('/videos/'), 'Invalid Pixabay image source page')
    return value


def _media_url(value, kind):
    p = _url_parts(value)
    need(p.netloc in MEDIA_HOSTS, 'unapproved_media_host')
    if kind == 'image':
        allowed = ((p.netloc == 'pixabay.com' and _GET.fullmatch(p.path))
                   or (p.netloc == 'cdn.pixabay.com' and _PHOTO.fullmatch(p.path)))
    else:
        allowed = p.netloc == 'cdn.pixabay.com' and _VIDEO.fullmatch(p.path)
    need(bool(allowed), 'unapproved_media_path')
    return value


def _tags(value):
    if value is None:
        return []
    raw = _clean_text(value, 4000, empty=True)
    result = []
    for item in raw.split(','):
        item = item.strip()
        if not item:
            continue
        _clean_text(item, 200)
        if item not in result:
            result.append(item)
        if len(result) == 32:
            break
    return result


def _creator(hit):
    author = hit.get('user')
    author = _clean_text(author, 200, empty=True) if author is not None else ''
    if not author:
        return 'Unknown contributor', None
    uid = hit.get('user_id')
    if _CREATOR.fullmatch(author):
        try:
            uid = _identifier(uid)
        except MaterialError:
            return author, None
        return author, 'https://pixabay.com/users/' + author + '-' + uid + '/'
    return author, None  # Keep the public credit text; do not fabricate a profile slug.


def search_request(kind, query, limit, orientation='any'):
    """Return unencoded parameters. The transport adds its secret key and urlencode once.

    Pixabay requires per_page>=3; orchestration must slice the final user limit.
    Video/square orientation filtering uses observed dimensions outside this adapter.
    """
    _kind(kind)
    query = _clean_text(query, 100)
    need(type(limit) is int and 1 <= limit <= 5, 'Pixabay limit must be an integer from 1 to 5')
    need(orientation is None or isinstance(orientation, str) and orientation in _ORIENTATIONS,
         'Invalid Pixabay orientation')
    params = {'q': query, 'per_page': max(3, limit), 'page': 1, 'safesearch': 'true'}
    if kind == 'image':
        params['image_type'] = 'photo'
        if orientation in {'landscape', 'portrait'}:
            params['orientation'] = {'landscape': 'horizontal', 'portrait': 'vertical'}[orientation]
        return '/api/', params
    params['video_type'] = 'all'
    return '/api/videos/', params


def _variant(vid, address, kind, *, width=None, height=None, size=None, expires=None):
    return {'id': vid, 'url': _media_url(address, kind), 'mime': 'image/jpeg' if kind == 'image' else 'video/mp4',
            'width': _dimension(width), 'height': _dimension(height), 'bytes': _positive_int(size, optional=True),
            'fps': None, 'expires_after_seconds': expires}


def parse_search(payload, kind):
    """Normalize at most five assets; this is metadata, never rights or content approval."""
    _kind(kind)
    need(isinstance(payload, dict) and isinstance(payload.get('hits'), list)
         and len(payload['hits']) <= 200, 'Invalid Pixabay search response')
    result, seen = [], set()
    for hit in payload['hits'][:5]:
        need(isinstance(hit, dict), 'Invalid Pixabay search item')
        aid = _identifier(hit.get('id'))
        need(aid not in seen, 'Duplicate Pixabay asset identity')
        seen.add(aid)
        source = _source_url(hit.get('pageURL'), aid, kind)
        tags = _tags(hit.get('tags'))
        creator, creator_url = _creator(hit)
        asset = {'provider_asset_id': aid, 'media_type': kind,
                 'title': ', '.join(tags[:5])[:500] or 'Pixabay ' + kind + ' ' + aid,
                 'source_url': source, 'creator': creator, 'creator_url': creator_url,
                 'tags': tags, 'duration_seconds': _duration(hit.get('duration')) if kind == 'video' else None,
                 'variants': [], 'blocked_variants': []}
        if kind == 'image':
            versions = [('largeImageURL', hit.get('largeImageURL'), None, None, None),
                        ('webformatURL', hit.get('webformatURL'), hit.get('webformatWidth'),
                         hit.get('webformatHeight'), 86400)]
            for vid, address, width, height, expires in versions:
                if address is None or address == '':
                    continue
                try:
                    asset['variants'].append(_variant(vid, address, kind, width=width, height=height, expires=expires))
                except MaterialError as exc:
                    reason = str(exc) if str(exc) in {'unapproved_media_host', 'unapproved_media_path'} else 'invalid_variant_metadata_or_url'
                    asset['blocked_variants'].append({'id': vid, 'reason': reason})
        else:
            videos = hit.get('videos', {})
            need(isinstance(videos, dict), 'Invalid Pixabay video variants')
            for vid in ('large', 'medium', 'small'):
                value = videos.get(vid)
                if value is None:
                    continue
                if not isinstance(value, dict):
                    asset['blocked_variants'].append({'id': vid, 'reason': 'invalid_variant_metadata_or_url'})
                    continue
                address, size = value.get('url'), value.get('size')
                if address is None or address == '' or type(size) is int and size == 0:
                    continue
                try:
                    asset['variants'].append(_variant(vid, address, kind, width=value.get('width'),
                                                      height=value.get('height'), size=size))
                except MaterialError as exc:
                    reason = str(exc) if str(exc) in {'unapproved_media_host', 'unapproved_media_path'} else 'invalid_variant_metadata_or_url'
                    asset['blocked_variants'].append({'id': vid, 'reason': reason})
        result.append(asset)
    return result
