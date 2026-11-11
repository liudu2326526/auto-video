"""Pure, bounded Pexels API adapter: no credentials, network, files or approval.

The exact CDN set comes from Pexels' official API examples. It is intentionally
incomplete: unfamiliar CDN or Vimeo URLs remain blocked, not inferred safe from
an API response. Metadata is an operator research input, never rights clearance.
"""
import math
import re
from urllib.parse import parse_qsl, urlsplit

from .core import MaterialError, need


MEDIA_HOSTS = frozenset({'images.pexels.com', 'static-videos.pexels.com'})
_PAGE_HOSTS = frozenset({'www.pexels.com', 'pexels.com'})
_SECRET = re.compile(r'(?:api[_-]?key|access[_-]?token|authorization|password|secret)\s*[:=]|'
                     r'\bBearer\s+[A-Za-z0-9._~-]{8,}|\b[^\s@]+@[^\s@]+\.[^\s@]+', re.I)
_IMAGE_NAMES = ('original', 'large', 'medium')
_TRANSFORMS = {'auto', 'cs', 'dpr', 'h', 'w', 'fit'}


def _public_text(value, maximum, *, default=None):
    if value is None or value == '':
        need(default is not None, 'Invalid Pexels public metadata')
        return default
    need(isinstance(value, str) and value.strip() and len(value) <= maximum
         and not any(ord(c) < 32 or ord(c) == 127 for c in value)
         and not _SECRET.search(value), 'Invalid Pexels public metadata')
    return value.strip()


def _identifier(value):
    need(type(value) is int and 0 < value < 10 ** 20, 'Invalid Pexels asset identity')
    return str(value)


def _split(value):
    need(isinstance(value, str) and 0 < len(value) <= 2048 and value.isascii()
         and not any(ord(c) <= 32 or ord(c) == 127 for c in value)
         and '\\' not in value and '%' not in value, 'unsafe_media_url')
    try:
        result = urlsplit(value)
        need(result.scheme == 'https' and result.netloc and result.username is None
             and result.password is None and result.port is None and not result.fragment,
             'unsafe_media_url')
    except ValueError:
        raise MaterialError('unsafe_media_url') from None
    return result


def _page(value, asset_id=None, kind=None):
    try:
        parsed = _split(value)
        need(parsed.netloc in _PAGE_HOSTS and not parsed.query, 'Invalid Pexels public page')
        if asset_id is None:
            valid = re.fullmatch(r'/@[A-Za-z0-9_.-]{1,160}/?', parsed.path)
        else:
            prefix = 'photo' if kind == 'image' else 'video'
            valid = re.fullmatch(r'/' + prefix + r'/(?:[A-Za-z0-9_-]+-)?' + asset_id + r'/?', parsed.path)
        need(valid, 'Invalid Pexels public page')
    except MaterialError:
        raise MaterialError('Invalid Pexels public page') from None
    return value


def _dimension(value):
    need(type(value) is int and 0 < value <= 16384, 'invalid_media_metadata')
    return value


def _number(value, maximum, *, optional=True):
    if value is None and optional:
        return None
    need(type(value) in (int, float) and math.isfinite(value) and 0 < value <= maximum,
         'invalid_media_metadata')
    return value


def _media_url(value, asset_id, kind):
    parsed = _split(value)
    need(parsed.netloc in MEDIA_HOSTS, 'unapproved_media_host')
    extension = r'jpe?g' if kind == 'image' else r'mp4'
    folder = 'photos' if kind == 'image' else 'videos'
    need(re.fullmatch(r'/' + folder + '/' + asset_id + r'/[A-Za-z0-9_-]{1,180}\.' + extension,
                      parsed.path, re.I), 'unsupported_media_path')
    if kind == 'video':
        need(not parsed.query, 'unsupported_media_query')
        return value
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True,
                          max_num_fields=6)
    except ValueError:
        raise MaterialError('unsupported_media_query') from None
    names = [key for key, _ in pairs]
    need(len(names) == len(set(names)) and set(names) <= _TRANSFORMS, 'unsupported_media_query')
    for key, content in pairs:
        if key in {'w', 'h'}:
            valid = bool(re.fullmatch(r'[1-9][0-9]{0,4}', content)) and int(content) <= 16384
        else:
            valid = content in {'auto': {'compress'}, 'cs': {'tinysrgb'},
                                'dpr': {'1', '2'}, 'fit': {'crop'}}[key]
        need(valid, 'unsupported_media_query')
    return value


def search_request(kind, query, limit=3, orientation=None):
    """Return only an API path and public parameters; never take an API key."""
    need(isinstance(kind, str) and kind in {'image', 'video'}, 'Unsupported Pexels media kind')
    clean = _public_text(query, 200)
    need(type(limit) is int and 1 <= limit <= 5, 'Pexels result limit must be 1..5')
    need(orientation is None or isinstance(orientation, str)
         and orientation in {'any', 'all', 'landscape', 'portrait', 'square'},
         'Unsupported Pexels orientation')
    params = {'query': clean, 'per_page': limit, 'page': 1}
    if orientation not in {None, 'any', 'all'}:
        params['orientation'] = orientation
    return ('/v1/search' if kind == 'image' else '/v1/videos/search'), params


def _tags(value):
    if value is None:
        return []
    need(isinstance(value, list) and len(value) <= 16, 'Invalid Pexels public metadata')
    result = []
    for item in value:
        tag = _public_text(item, 80)
        if tag not in result:
            result.append(tag)
    return result


def _variant(vid, address, asset_id, kind, width, height, fps=None):
    return {'id': vid, 'url': _media_url(address, asset_id, kind),
            'mime': 'image/jpeg' if kind == 'image' else 'video/mp4',
            'width': width, 'height': height, 'bytes': None, 'fps': fps,
            # This bounds metadata/link refresh, not a claim about license expiry.
            'expires_after_seconds': 86400}


def parse_search(payload, kind):
    """Parse up to five assets, retaining no pagination URL or unknown metadata.

    Unsafe variants keep only a safe local identity and a fixed reason. Resized
    image dimensions are unknown until actual probing; API image dimensions
    describe only the original. Missing byte sizes are never guessed.
    """
    need(isinstance(kind, str) and kind in {'image', 'video'}, 'Unsupported Pexels media kind')
    need(isinstance(payload, dict), 'Invalid Pexels search response')
    rows = payload.get('photos' if kind == 'image' else 'videos')
    need(isinstance(rows, list) and len(rows) <= 80, 'Invalid Pexels search response')
    result, seen = [], set()
    for item in rows[:5]:
        need(isinstance(item, dict), 'Invalid Pexels search response')
        asset_id = _identifier(item.get('id'))
        need(asset_id not in seen, 'Duplicate Pexels asset identity')
        seen.add(asset_id)
        source = _page(item.get('url'), asset_id, kind)
        creator_data = item if kind == 'image' else item.get('user')
        need(isinstance(creator_data, dict), 'Invalid Pexels public metadata')
        creator = _public_text(creator_data.get('photographer' if kind == 'image' else 'name'), 200)
        profile = creator_data.get('photographer_url' if kind == 'image' else 'url')
        profile = _page(profile) if profile is not None else None
        title = _public_text(item.get('alt') if kind == 'image' else None, 500,
                             default='Pexels ' + kind + ' ' + asset_id)
        asset = {'provider_asset_id': asset_id, 'media_type': kind, 'title': title,
                 'source_url': source, 'creator': creator, 'creator_url': profile,
                 'tags': _tags(item.get('tags')), 'duration_seconds': None,
                 'variants': [], 'blocked_variants': []}
        urls = set()
        if kind == 'image':
            sizes = item.get('src')
            need(isinstance(sizes, dict), 'Invalid Pexels image variants')
            candidates = [(name, sizes[name]) for name in _IMAGE_NAMES if name in sizes]
            for name, address in candidates:
                try:
                    width = _dimension(item.get('width')) if name == 'original' else None
                    height = _dimension(item.get('height')) if name == 'original' else None
                    variant = _variant(name, address, asset_id, kind, width, height)
                    if variant['url'] not in urls:
                        urls.add(variant['url'])
                        asset['variants'].append(variant)
                except MaterialError as exc:
                    asset['blocked_variants'].append({'id': name, 'reason': str(exc)})
        else:
            asset['duration_seconds'] = _number(item.get('duration'), 86400, optional=False)
            files = item.get('video_files')
            need(isinstance(files, list) and len(files) <= 32, 'Invalid Pexels video variants')
            ids = set()
            for index, entry in enumerate(files):
                fallback = 'file_' + str(index + 1)
                vid = fallback
                try:
                    need(isinstance(entry, dict), 'invalid_media_metadata')
                    vid = _identifier(entry.get('id'))
                    need(vid not in ids, 'duplicate_variant_identity')
                    ids.add(vid)
                    need(entry.get('file_type') == 'video/mp4' and isinstance(entry.get('quality'), str)
                         and entry['quality'] in {'hd', 'sd'},
                         'unsupported_media_type')
                    variant = _variant(vid, entry.get('link'), asset_id, kind,
                                       _dimension(entry.get('width')), _dimension(entry.get('height')),
                                       _number(entry.get('fps'), 240))
                    if variant['url'] not in urls:
                        urls.add(variant['url'])
                        if len(asset['variants']) < 3:
                            asset['variants'].append(variant)
                        else:
                            asset['blocked_variants'].append({'id': vid, 'reason': 'variant_limit'})
                except MaterialError as exc:
                    # A malformed remote ID must not carry a token into diagnostics.
                    reason = 'invalid_variant_identity' if vid == fallback else str(exc)
                    asset['blocked_variants'].append({'id': vid, 'reason': reason})
        result.append(asset)
    return result
