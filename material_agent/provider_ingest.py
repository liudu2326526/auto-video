"""Explicit one-candidate ingestion; no API credential enters the media role."""
import copy
import json
from pathlib import Path
import tempfile
from urllib.parse import urljoin, urlsplit
import uuid

from .core import (Library, MaterialError, canonical, context, digest, fields, file_hash,
                   identity, instant, need, now, read_json, stamp, validate_record)
from .download import DownloadLock, event, parse_robots, PrefixRobots, validate_media, USER_AGENT
from .provider_http import MediaTransfer
from .providers import ADAPTERS, ProviderStore, policy_for


NAMES = {'noto_thinking': '1f914', 'noto_party': '1f389'}


def catalog(source):
    need(source in {'mixkit', 'noto'}, 'Unknown curated source')
    path = Path(__file__).resolve().parents[1] / 'fixtures' / 'materials' / (source + '-catalog.json')
    value = read_json(path)
    fields(value, ['schema', 'checked_at', 'items'])
    need(value['schema'] == 'material_source_catalog/v1', 'Unsupported source catalog')
    need(isinstance(value['items'], list) and 1 <= len(value['items']) <= 4, 'Only a small reviewed catalog is supported')
    seen = set()
    for item in value['items']:
        fields(item, ['id', 'provider', 'kind', 'automation_allowed', 'blocked_reason', 'source_version', 'record', 'evidence_urls'])
        identity(item['id'])
        need(item['id'] not in seen and item['provider'] == source, 'Catalog identity mismatch')
        seen.add(item['id'])
        validate_record(item['record'])
    return value


def _api_record(candidate, variant):
    provider, asset = candidate['provider'], candidate['asset']
    policy = policy_for(provider)
    author, source = asset['creator'], asset['source_url']
    api_endpoint = 'https://' + ('api.pexels.com' if provider == 'pexels' else 'pixabay.com') + candidate['api_request']['path']
    checked = max(instant(candidate['fetched_at']), instant(policy['checked_at']))
    record_id = provider + '_' + asset['media_type'] + '_' + asset['provider_asset_id'] + '_' + digest([candidate, variant])[:12]
    record = {'schema': 'material_record/v1', 'id': record_id, 'title': asset['title'],
              'description': 'Illustrative context selected for ' + candidate['request']['purpose'] + '; not product-test evidence.',
              'media_type': asset['media_type'], 'tags': list(dict.fromkeys([provider, *asset['tags']]))[:64],
              'source': {'provider': provider, 'page_url': source, 'creator': author,
                         'retrieved_at': candidate['fetched_at'], 'access': 'public_no_login', 'download_url': variant['url']},
              'rights': {'status': 'known', 'license_id': policy['license_id'], 'license_url': policy['license_url'],
                         'commercial_use': 'allowed', 'derivatives': 'allowed', 'attribution_required': False,
                         'attribution_text': author + ' — ' + provider + '. Source: ' + source + '. License: ' + policy['license_url']
                                             + '. ' + policy['api_credit'],
                         'restrictions': policy['restrictions'], 'checked_at': stamp(checked),
                         'evidence': [{'url': api_endpoint, 'retrieved_at': candidate['fetched_at'], 'scope': 'asset_license',
                                       'excerpt': 'Official API metadata identifies asset ' + asset['provider_asset_id']
                                                  + ' at ' + source + '; combined with the provider standard license, not a release verification.'},
                                      {'url': policy['license_url'], 'retrieved_at': policy['checked_at'], 'scope': 'license_terms',
                                       'excerpt': policy['license_summary']}],
                         'allowed_distributions': ['internal', 'public_social', 'paid_advertising'],
                         'valid_until': None, 'share_alike_required': False},
              'risks': ['Standard provider license claim is recorded; people, brands, property and intended use require actual human review.',
                        'API attribution conditions remain distinct from the copyright license; do not remove source/author credit.',
                        'Do not build or distribute a stock mirror. Stop use and handle deletion on an applicable withdrawal notice.']}
    validate_record(record)
    return record


def _variant_url(provider, address, asset, *, catalog_id=None):
    if provider == 'noto':
        need(catalog_id in NAMES and address == 'https://fonts.gstatic.com/s/e/notoemoji/latest/' + NAMES[catalog_id] + '/512.gif',
             'Noto download is limited to the two specifically reviewed official GIFs')
        return address
    adapter = ADAPTERS[provider]
    if provider == 'pexels':
        return adapter._media_url(address, asset['provider_asset_id'], asset['media_type'])
    return adapter._media_url(address, asset['media_type'])


def _ingest(library, record, provenance, use, checker, *, resolver, mime, allow_animation=False, max_bytes=32 * 1024 * 1024):
    context(use)
    validate_record(record)
    issues = Library._rights_issues(record, use, now())
    need(not issues, '; '.join(issues))
    address = checker(record['source']['download_url'])
    attempt = 'download_' + uuid.uuid4().hex
    event(library, attempt, record['id'], 'started', {'provider': provenance['provider']})
    store = ProviderStore(library)
    try:
        store.check_asset(provenance['provider'], provenance['asset_id'])
        previous = library.db.execute('SELECT id FROM records WHERE id=?', (record['id'],)).fetchone()
        if previous:
            row = library.get(record['id'])
            need(row['digest'] == digest(record) and row['blob_sha256'], 'Immutable provider record differs or has no bytes')
            saved = library.db.execute('SELECT document FROM provider_imports WHERE record_id=?', (record['id'],)).fetchone()
            need(saved is not None and json.loads(saved[0])['candidate_digest'] == provenance['candidate_digest'], 'Source provenance mismatch')
            path = library._blob(row['blob_sha256'])
            need(path.stat().st_size <= max_bytes, 'Cached provider file exceeds current limit')
            need(not library.db.execute('SELECT 1 FROM material_withdrawals WHERE record_id=?', (record['id'],)).fetchone(), 'Material withdrawn')
            event(library, attempt, record['id'], 'reused_local', {'sha256': row['blob_sha256'], 'network_requests': 0, 'use_context': use})
            return {'outcome': 'reused_local', 'record_id': row['id'], 'sha256': row['blob_sha256'], 'network_requests': 0, 'human_usage_approval': False}
        with DownloadLock(), tempfile.TemporaryDirectory(prefix='.provider-download-', dir=library.root) as temporary:
            net = MediaTransfer({'max_bytes': max_bytes, 'total_timeout': 90, 'resolver': resolver})
            host = urlsplit(address).netloc
            robots_url = 'https://' + host + '/robots.txt'
            observation = net.once(robots_url, limit=65536, allowed_mime={'text/plain'})
            if observation['status'] in {404, 410}:
                # Known official API/CDN delivery and reviewed Noto download links only;
                # RFC9309 unavailable robots is not a copyright authorization.
                rules = PrefixRobots([])
            else:
                need(observation['status'] == 200, 'Media robots/access denied; no alternate host, cookie or login')
                rules = parse_robots(observation['body'])
            target = Path(temporary) / 'payload.part'
            seen, redirects, current = set(), [], address
            for hop in range(3):
                checker(current)
                need(current not in seen, 'Media redirect loop')
                seen.add(current)
                need(urlsplit(current).netloc == host and rules.can_fetch('auto-video-materials', current), 'Media redirect/robots forbids request')
                with target.open('wb') as output:
                    response = net.once(current, limit=max_bytes, sink=output, allowed_mime={mime})
                if response['status'] in {301, 302, 303, 307, 308}:
                    need(hop < 2 and response['location'], 'Media redirect budget exceeded')
                    next_url = checker(urljoin(current, response['location']))
                    need(urlsplit(next_url).netloc == host and urlsplit(next_url).path.rsplit('/', 1)[-1] == urlsplit(address).path.rsplit('/', 1)[-1],
                         'Media redirect changes approved host/file identity')
                    redirects.append({'from': current, 'to': next_url, 'status': response['status']})
                    current = next_url
                    continue
                need(response['status'] == 200, 'Public media access/status refused; no retry')
                break
            need(response['bytes'] > 0 and file_hash(target) == response['sha256'], 'Empty/changed temporary media')
            media = validate_media(target, mime, record['media_type'], allow_animation=allow_animation)
            final_path = target.with_suffix(media['suffix']); target.rename(final_path)
            issues = Library._rights_issues(record, use, now())
            need(not issues, '; '.join(issues))
            if provenance.get('candidate_expires_at'):
                need(instant(provenance['candidate_expires_at']) > now(), 'Candidate expired during download; no automatic refresh')
            verification = {'schema': 'material_download_verification/v1', 'policy_digest': digest(provenance),
                            'file_sha256': response['sha256'], 'byte_count': response['bytes'],
                            'requested_url': address, 'final_url': current, 'redirects': redirects,
                            'robots_url': robots_url, 'robots_sha256': observation['sha256'], 'robots_checked_at': stamp(),
                            'media_validation': media, 'network_requests': net.requests, 'user_agent': USER_AGENT,
                            'use_context': use, 'resolver': resolver}
            frozen = {**provenance, 'record_id': record['id'], 'blob_sha256': response['sha256'],
                      'download': verification, 'robots_http_status': observation['status'],
                      'response_headers': {k: v for k, v in response.get('headers', {}).items() if k in {'last-modified', 'etag'}},
                      'human_usage_approval': False}
            def before_commit():
                store.check_asset(provenance['provider'], provenance['asset_id'])
                library.db.execute('INSERT INTO provider_imports VALUES (?,?,?)',
                                   (record['id'], provenance['candidate_id'], canonical(frozen)))
                event(library, attempt, record['id'], 'downloaded', {'provider': provenance['provider'], 'verification': verification}, commit=False)
            row = library.add(record, final_path, {'downloaded_at': stamp(), 'method': 'automated_public_download',
                              'source_url': address, 'verification': verification}, _before_commit=before_commit)
            return {'outcome': 'downloaded', 'record_id': row['id'], 'sha256': row['blob_sha256'],
                    'network_requests': net.requests, 'media': media, 'human_usage_approval': False}
    except BaseException as exc:
        library.db.rollback()
        event(library, attempt, record['id'], 'failed', {'error_type': type(exc).__name__, 'reason': 'Provider acquisition failed; no credentials or server body logged'})
        raise
    finally:
        store.close()


def download_provider(library, candidate_id, variant_id, use, *, resolver='system'):
    store = ProviderStore(library)
    try:
        value = store.candidate(candidate_id)
    finally:
        store.close()
    provider, asset = value['provider'], value['asset']
    need(value.get('discovery_mode') == 'live' or value.get('discovery_mode') == 'mock'
         and getattr(MediaTransfer, 'NETWORK_MODE', None) == 'mock',
         'Unverified/mock API candidates cannot initiate real media requests')
    policy = policy_for(provider)
    need(value['policy_digest'] == digest(policy), 'Provider terms changed; search/review the source again')
    variants = [v for v in asset['variants'] if v['id'] == variant_id]
    need(len(variants) == 1, 'Unknown/blocked rendition; select one returned approved variant')
    variant = variants[0]
    need(variant['bytes'] is None or variant['bytes'] <= 32 * 1024 * 1024, 'Selected media exceeds 32MiB budget')
    record = _api_record(value, variant)
    provenance = {'schema': 'material_provider_import/v1', 'provider': provider, 'asset_id': asset['provider_asset_id'],
                  'candidate_id': candidate_id, 'candidate_digest': digest(value), 'candidate_expires_at': value['expires_at'],
                  'source_request': value['request'], 'api_request': value['api_request'], 'api_fetched_at': value['fetched_at'],
                  'discovery_mode': value['discovery_mode'],
                  'provider_policy': policy, 'author_profile_url': asset['creator_url'], 'selected_variant': variant}
    return _ingest(library, record, provenance, use, lambda url: _variant_url(provider, url, asset), resolver=resolver, mime=variant['mime'])


def download_catalog(library, item_id, use, *, resolver='system'):
    # Mixkit's prohibition is a code boundary, not an editable catalog flag.
    if item_id.startswith('mixkit'):
        raise MaterialError('Mixkit prohibits unauthorized automated access/download; use normal manual acquisition and verify its item license')
    items = [item for item in catalog('noto')['items'] if item['id'] == item_id]
    need(len(items) == 1 and items[0]['automation_allowed'] is True, 'Unknown/non-automated catalog item')
    item = items[0]
    record = copy.deepcopy(item['record'])
    need(item['kind'] == 'animation' and record['media_type'] == 'image'
         and record['source']['page_url'] == 'https://googlefonts.github.io/noto-emoji-files/'
         and record['rights']['license_id'] == 'CC-BY-4.0' and record['rights']['attribution_required'] is True
         and record['rights']['license_url'] in {'https://creativecommons.org/licenses/by/4.0/',
             'https://creativecommons.org/licenses/by/4.0/legalcode.en', 'https://creativecommons.org/licenses/by/4.0/deed.en'},
         'Noto animation license/source/attribution policy changed; stop for review')
    provenance = {'schema': 'material_provider_import/v1', 'provider': 'noto', 'asset_id': item_id,
                  'candidate_id': item_id, 'candidate_digest': digest(item), 'source_version': item['source_version'],
                  'evidence_urls': item['evidence_urls'], 'selected_variant': {'id': 'gif_512', 'mime': 'image/gif'}}
    return _ingest(library, record, provenance, use, lambda url: _variant_url('noto', url, None, catalog_id=item_id),
                   resolver=resolver, mime='image/gif', allow_animation=True, max_bytes=8 * 1024 * 1024)
