"""Shot-scoped official API search, 24h response cache, and immutable provenance.

No account/key creation, OAuth, long-term credential configuration or bulk jobs.
The credential is read only from its existing process environment on cache miss.
"""
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import unicodedata
from datetime import timedelta
from urllib.parse import urlsplit

from .core import (MaterialError, canonical, context, digest, fields, identity, instant,
                   location, need, now, stamp, text)
from .provider_http import ENV_KEYS, api_get
from . import pexels, pixabay


ADAPTERS = {'pexels': pexels, 'pixabay': pixabay}
POLICIES = {
    'pexels': {
        'checked_at': '2026-11-04T10:19:01Z', 'license_id': 'Pexels-License',
        'license_url': 'https://www.pexels.com/license/',
        'api_url': 'https://www.pexels.com/api/documentation/',
        'terms_url': 'https://www.pexels.com/terms-of-service/',
        'credit_url': 'https://help.pexels.com/hc/en-us/articles/900005851903-How-should-I-give-credit-Can-I-use-your-logo',
        'cache_url': 'https://help.pexels.com/hc/en-us/articles/900006470063-What-steps-can-I-take-to-avoid-hitting-the-rate-limit',
        'license_summary': 'Pexels standard license permits project use and modification; no standalone resale, false endorsement, or assumption of third-party releases.',
        'api_credit': 'Link prominently to Pexels in API result displays; retain photographer credit and the asset page when possible.',
        'key_note': 'New API key issuance was paused at the checked date; an existing key has not been tested here.',
        'restrictions': ['Use only for the specific project/shot; no systematic copying, competing stock service or dataset harvesting.',
                         'Do not distribute unchanged standalone media or imply endorsement; inspect people, brands and other third-party rights.',
                         'API result displays must link to Pexels; retain the photographer and source page.',
                         'Stop use and remove affected copies when an applicable infringement/takedown notice requires it.'],
    },
    'pixabay': {
        'checked_at': '2026-11-04T10:20:48Z', 'license_id': 'Pixabay-Content-License',
        'license_url': 'https://pixabay.com/service/license-summary/',
        'api_url': 'https://pixabay.com/api/docs/', 'terms_url': 'https://pixabay.com/service/terms/',
        'credit_url': 'https://pixabay.com/api/docs/', 'cache_url': 'https://pixabay.com/api/docs/#api_rate_limit',
        'license_summary': 'Pixabay Content License permits conditional commercial use and modification. It is not CC0; sponsored third-party content is excluded.',
        'api_credit': 'Identify Pixabay as the source when displaying API search results; keep author and item-page provenance.',
        'key_note': 'An API key is required; no account, key or long-term configuration has been created.',
        'restrictions': ['Cache identical API queries for 24 hours; no mass queries, systematic downloads or competing stock library.',
                         'Do not distribute standalone media, misuse trademarks or imply endorsement; inspect third-party rights.',
                         'Identify Pixabay in API result displays; sponsored third-party stock is not covered by this license.',
                         'Stop use and remove affected copies when an applicable infringement/takedown notice requires it.'],
    },
}


def policy_for(provider, at=None):
    need(provider in POLICIES, 'Unsupported official material provider')
    policy = POLICIES[provider]
    at = at or now()
    need(at - timedelta(days=30) <= instant(policy['checked_at']) <= at, 'Provider policy observations expired; review official terms again')
    return policy


def request(value):
    fields(value, ['schema', 'id', 'provider', 'project_id', 'scene_id', 'asset_id', 'line_ids',
                   'public_nonpersonal', 'query', 'media_type', 'limit', 'orientation', 'purpose'])
    need(value['schema'] == 'material_provider_request/v1' and value['public_nonpersonal'] is True,
         'Explicit public, nonpersonal per-shot request required')
    need(value['provider'] in ADAPTERS, 'Unsupported provider')
    for key in ('id', 'project_id', 'scene_id', 'asset_id'):
        identity(value[key])
    need(isinstance(value['line_ids'], list) and len(value['line_ids']) <= 32, 'Bounded line references required')
    for line in value['line_ids']:
        identity(line)
    need(len(value['line_ids']) == len(set(value['line_ids'])), 'Duplicate line reference')
    q = unicodedata.normalize('NFKC', text(value['query'], 100)).strip().casefold()
    need(0 < len(q) <= 100, 'Normalized public query exceeds limit')
    need(not re.search(r'[@:/?=#\\]|\d{7,}|\b(?:password|api[_-]?key|bearer|token)\b', q, re.I),
         'Queries must be public descriptive terms, without contacts, identifiers, URLs or credentials')
    need(value['media_type'] in {'image', 'video'}, 'Provider search supports image/video only')
    need(type(value['limit']) is int and 1 <= value['limit'] <= 5, 'Only 1..5 candidates for one page are supported')
    need(value['orientation'] in {'any', 'all', 'landscape', 'portrait', 'square'}, 'Invalid orientation')
    text(value['purpose'], 1000)
    return {**value, 'query': ' '.join(q.split())}


class ProviderStore:
    """Disposable query cache; candidate/quota/withdrawal provenance stays in the library DB."""
    def __init__(self, library):
        self.library, self.db = library, library.db
        path = location(library.root / 'provider-query-cache.sqlite3')
        self.cache = sqlite3.connect(path)
        self.cache.row_factory = sqlite3.Row
        self.cache.execute('CREATE TABLE IF NOT EXISTS query_cache (id TEXT PRIMARY KEY, document TEXT NOT NULL, digest TEXT NOT NULL, expires_at TEXT NOT NULL)')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS provider_candidates(id TEXT PRIMARY KEY, document TEXT NOT NULL, digest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS provider_imports(record_id TEXT PRIMARY KEY REFERENCES records(id), candidate_id TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS provider_attempts(seq INTEGER PRIMARY KEY, provider TEXT NOT NULL, attempted_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS provider_state(provider TEXT PRIMARY KEY, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS provider_blocks(seq INTEGER PRIMARY KEY, provider TEXT NOT NULL, asset_id TEXT NOT NULL, document TEXT NOT NULL);
        ''')
        for table in ('provider_candidates', 'provider_imports', 'provider_attempts', 'provider_blocks'):
            for action in ('UPDATE', 'DELETE'):
                self.db.execute(f'CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} BEFORE {action} ON {table} '
                                "BEGIN SELECT RAISE(ABORT, 'append-only provider provenance'); END")

    def close(self):
        self.cache.close()

    def cached(self, fingerprint, at):
        row = self.cache.execute('SELECT * FROM query_cache WHERE id=?', (fingerprint,)).fetchone()
        if row is None:
            return None
        if instant(row['expires_at']) <= at:
            with self.cache:
                self.cache.execute('DELETE FROM query_cache WHERE id=?', (fingerprint,))
            return None
        value = json.loads(row['document'])
        need(digest(value) == row['digest'], 'Provider cache integrity mismatch')
        need(instant(value['fetched_at']) <= at, 'Provider cache observation is in the future')
        return value

    def save_cache(self, fingerprint, value):
        with self.cache:
            self.cache.execute('INSERT INTO query_cache VALUES (?,?,?,?)',
                               (fingerprint, canonical(value), digest(value), value['expires_at']))

    def state(self, provider):
        row = self.db.execute('SELECT document FROM provider_state WHERE provider=?', (provider,)).fetchone()
        return json.loads(row[0]) if row else None

    def check_state(self, provider, at):
        state = self.state(provider)
        if state and state.get('blocked'):
            until = state['blocked_until']
            need(until is not None and instant(until) <= at, 'Provider is paused by access/rate response; resolve with provider, do not bypass')

    def begin_attempt(self, provider, at):
        self.check_state(provider, at)
        count = self.db.execute('SELECT count(*) FROM provider_attempts WHERE provider=? AND attempted_at>?',
                               (provider, stamp(at - timedelta(hours=1)))).fetchone()[0]
        need(count < 20, 'Local maximum 20 API attempts/hour reached; cached data does not consume requests')
        with self.db:
            self.db.execute('INSERT INTO provider_attempts(provider,attempted_at) VALUES (?,?)', (provider, stamp(at)))

    def observe_rate(self, provider, response, at):
        status, headers = response['status'], response['headers']
        need(type(status) is int and 100 <= status <= 599, 'Invalid API HTTP status')
        rate, reset_at, invalid = {}, None, False
        try:
            for key in ('limit', 'remaining', 'reset'):
                raw = headers.get('x-ratelimit-' + key)
                if raw is not None:
                    need(isinstance(raw, str) and raw.isascii() and raw.isdigit() and len(raw) <= 12, 'Invalid API quota header')
                    rate[key] = int(raw)
            reset = rate.get('reset')
            if reset is not None:
                if provider == 'pixabay':
                    need(reset <= 86400, 'Pixabay quota delta outside supported range')
                    reset_at = at + timedelta(seconds=reset)
                else:
                    from datetime import datetime, timezone
                    reset_at = datetime.fromtimestamp(reset, timezone.utc)
                    need(at <= reset_at <= at + timedelta(days=366), 'Pexels reset epoch outside supported range')
            retry = headers.get('retry-after')
            if retry is not None:
                need(isinstance(retry, str), 'Invalid Retry-After')
                if retry.isascii() and retry.isdigit():
                    need(0 <= int(retry) <= 86400, 'Retry-After delta outside supported range')
                    retry_at = at + timedelta(seconds=int(retry))
                else:
                    from email.utils import parsedate_to_datetime
                    retry_at = parsedate_to_datetime(retry)
                    need(retry_at.tzinfo is not None and at <= retry_at <= at + timedelta(days=366), 'Invalid Retry-After date')
                reset_at = max(reset_at, retry_at) if reset_at else retry_at
        except (ValueError, TypeError, OverflowError):
            # A malformed header must never erase a known access/throttle refusal.
            rate, reset_at, invalid = {}, None, True
        blocked = invalid or status in {401, 403, 429, 451} or rate.get('remaining') == 0
        # A 401/403 is an account/access problem, never merely a timer to bypass.
        until = stamp(reset_at) if reset_at and status not in {401, 403, 451} else None
        state = {'blocked': blocked, 'blocked_until': until, 'http_status': status, 'rate': rate,
                 'invalid_quota_headers': invalid, 'observed_at': stamp(at)}
        with self.db:
            self.db.execute('INSERT INTO provider_state VALUES (?,?) ON CONFLICT(provider) DO UPDATE SET document=excluded.document',
                            (provider, canonical(state)))
        need(not invalid, 'Invalid quota headers; provider paused conservatively')
        return state

    def save_candidate(self, value):
        old = self.db.execute('SELECT document FROM provider_candidates WHERE id=?', (value['id'],)).fetchone()
        if old:
            need(old[0] == canonical(value), 'Immutable provider candidate changed')
        else:
            with self.db:
                self.db.execute('INSERT INTO provider_candidates VALUES (?,?,?)', (value['id'], canonical(value), digest(value)))

    def candidate(self, candidate_id, at=None):
        identity(candidate_id)
        row = self.db.execute('SELECT document,digest FROM provider_candidates WHERE id=?', (candidate_id,)).fetchone()
        need(row is not None, 'Unknown provider candidate')
        value = json.loads(row[0])
        need(digest(value) == row[1], 'Provider candidate integrity mismatch')
        need(instant(value['expires_at']) > (at or now()), 'Provider candidate/link expired; run the explicit search again')
        self.check_asset(value['provider'], value['asset']['provider_asset_id'])
        return value

    def check_asset(self, provider, asset_id):
        need(self.db.execute('SELECT 1 FROM provider_blocks WHERE provider=? AND asset_id=?', (provider, asset_id)).fetchone() is None,
             'Provider asset has been withdrawn; do not download or reuse it')

    def withdraw(self, provider, asset_id, operator, reason):
        need(provider in {'pexels', 'pixabay', 'noto'}, 'Unknown provider')
        text(asset_id, 100); text(operator, 500); text(reason, 4000)
        receipt = {'provider': provider, 'asset_id': asset_id, 'operator': operator, 'reason': reason, 'at': stamp()}
        affected = []
        for row in self.db.execute('SELECT record_id,document FROM provider_imports'):
            provenance = json.loads(row['document'])
            if provenance['provider'] == provider and provenance['asset_id'] == asset_id:
                affected.append(row['record_id'])
        with self.db:
            self.db.execute('INSERT INTO provider_blocks(provider,asset_id,document) VALUES (?,?,?)', (provider, asset_id, canonical(receipt)))
            for record_id in affected:
                self.db.execute('INSERT INTO material_withdrawals(record_id,document) VALUES (?,?)', (record_id, canonical(receipt)))
        return {**receipt, 'blocked_record_ids': affected,
                'manual_followup': 'Stop affected project use; remove library/export copies required by the notice, checking shared blobs and other valid licenses. No source file is silently deleted.'}


def _orientation(asset, desired):
    if desired in {'any', 'all'}:
        return True
    known = [v for v in asset['variants'] if v['width'] and v['height']]
    if not known:
        return True  # Unknown dimensions are not asserted; download probing is still required.
    return any((desired == 'landscape' and v['width'] > v['height']) or
               (desired == 'portrait' and v['height'] > v['width']) or
               (desired == 'square' and v['width'] == v['height']) for v in known)


def search_provider(library, search, *, resolver='system', client=None, at=None):
    from .download import DownloadLock
    with DownloadLock():
        return _search_provider(library, search, resolver=resolver, client=client, at=at)


def _search_provider(library, search, *, resolver='system', client=None, at=None):
    supplied_at = at
    search, at = request(search), at or now()
    provider, adapter = search['provider'], ADAPTERS[search['provider']]
    policy = policy_for(provider, at)
    path, params = adapter.search_request(search['media_type'], search['query'], search['limit'], search['orientation'])
    # Query cache has no key, project/private brief, pagination link or raw API body.
    query = {'provider': provider, 'path': path, 'params': params, 'media_type': search['media_type']}
    fingerprint = digest(query)
    store = ProviderStore(library)
    try:
        cached = store.cached(fingerprint, at)
        hit = cached is not None
        if not hit:
            key = os.environ.get(ENV_KEYS[provider], '')
            need(bool(key), 'Missing ' + ENV_KEYS[provider] + '; provide an existing key securely in the process environment; no account/key was created')
            store.begin_attempt(provider, at)
            try:
                response = (client or api_get)(provider, path, params, key, resolver)
            except Exception as exc:
                status = getattr(exc, 'http_status', None)
                if status in {401, 403, 429, 451}:
                    store.observe_rate(provider, {'status': status, 'headers': {}}, at)
                raise MaterialError('Official API search failed; credentials/response details omitted; no automatic retry') from None
            need(key not in canonical(response), 'Credential echo rejected before quota/cache/provenance')
            received_at = supplied_at or now()
            state = store.observe_rate(provider, response, received_at)
            need(response['status'] == 200, 'Official API refused HTTP status ' + str(response['status']) + '; no retry or alternate endpoint')
            assets = adapter.parse_search(response['data'], search['media_type'])
            cached = {'provider': provider, 'query': query, 'assets': assets, 'fetched_at': stamp(received_at),
                      'expires_at': stamp(received_at + timedelta(hours=24)), 'rate': state['rate'],
                      'discovery_mode': 'mock' if client is not None else 'live'}
            need(key not in canonical(cached), 'Credential echo rejected before cache/provenance')
            store.save_cache(fingerprint, cached)
        candidates = []
        for asset in [a for a in cached['assets'] if _orientation(a, search['orientation'])][:search['limit']]:
            value = {'schema': 'material_provider_candidate/v1', 'provider': provider, 'asset': asset,
                     'request': search, 'api_request': query, 'query_fingerprint': fingerprint,
                     'fetched_at': cached['fetched_at'], 'expires_at': cached['expires_at'], 'policy_digest': digest(policy),
                     'discovery_mode': cached.get('discovery_mode', 'unverified')}
            value['id'] = provider + '_' + search['media_type'] + '_' + asset['provider_asset_id'] + '_' + digest(value)[:12]
            store.save_candidate(value)
            candidates.append(value)
        return {'schema': 'material_provider_search/v1', 'provider': provider, 'request_id': search['id'],
                'cache_hit': hit, 'network_requests': 0 if hit else response['network_requests'],
                'fetched_at': cached['fetched_at'], 'expires_at': cached['expires_at'], 'rate': cached['rate'],
                'discovery_mode': cached.get('discovery_mode', 'unverified'),
                'api_attribution': policy['api_credit'], 'source_link': 'https://www.pexels.com/' if provider == 'pexels' else 'https://pixabay.com/',
                'candidates': candidates, 'human_usage_approval': False}
    finally:
        store.close()


def write_search(report, output):
    from .core import runtime_location
    output = runtime_location(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'candidates.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    rows = [f'<a href="{html.escape(report["source_link"], quote=True)}">{html.escape(report["provider"])}</a>',
            html.escape(report['api_attribution']), '']
    for value in report['candidates']:
        asset = value['asset']
        rows.append(f'<a href="{html.escape(asset["source_url"], quote=True)}">{html.escape(asset["title"])}</a> — '
                    + html.escape(asset['creator']) + ' · ' + html.escape(value['id']))
        rows.append('Allowed renditions: ' + ', '.join(v['id'] for v in asset['variants']))
    (output / 'candidates.md').write_text('\n\n'.join(rows) + '\n', encoding='utf-8')


def provider_status(library):
    store = ProviderStore(library)
    try:
        return {name: {'api_key_present': bool(os.environ.get(ENV_KEYS[name])), 'api_key_env': ENV_KEYS[name],
                       'key_note_as_of': policy['checked_at'], 'key_note': policy['key_note'],
                       'cache_seconds': 86400, 'max_page': 1, 'max_candidates': 5, 'state': store.state(name),
                       'live_api_verified_in_this_delivery': False,
                       'live_candidates_recorded': bool(store.db.execute(
                           "SELECT 1 FROM provider_candidates WHERE json_extract(document,'$.provider')=? "
                           "AND json_extract(document,'$.discovery_mode')='live' LIMIT 1", (name,)).fetchone())}
                for name, policy in POLICIES.items()}
    finally:
        store.close()
