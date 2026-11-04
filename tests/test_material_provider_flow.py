"""Offline orchestration contract tests; keys/assets are obvious synthetic fixtures.

No credential creation, real API/media call, account login or GUI use occurs.
The adapter fixtures below describe invented assets, not licensed real media.
"""
import copy
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import traceback
import unittest
from unittest.mock import Mock, patch

from material_agent.core import Library, MaterialError, canonical, digest, instant, stamp
from material_agent.providers import ProviderStore, search_provider, write_search, POLICIES
from material_agent.provider_http import ENV_KEYS
from material_agent.provider_ingest import download_provider
from test_material_pixabay import image as pixabay_image, video as pixabay_video
from test_material_pexels import photo as pexels_photo


AT = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
KEYS = {'pixabay': 'SYNTHETIC_PIXABAY_KEY_NOT_VALID_4819',
        'pexels': 'SYNTHETIC_PEXELS_KEY_NOT_VALID_4819'}
CANARY = 'SYNTHETIC_SERVER_CANARY_DO_NOT_RETAIN_4819'
USE = {'commercial': True, 'derivatives': True, 'distribution': 'public_social',
       'role': 'illustrative_context', 'share_alike_compatible': False}


def shot(provider='pixabay', *, query='synthetic office', request_id='shot_search', limit=3,
         orientation='any', kind='image'):
    return {'schema': 'material_provider_request/v1', 'id': request_id, 'provider': provider,
            'project_id': 'synthetic_project', 'scene_id': 'synthetic_scene', 'asset_id': 'synthetic_context',
            'line_ids': ['synthetic_line'], 'public_nonpersonal': True, 'query': query,
            'media_type': kind, 'limit': limit, 'orientation': orientation,
            'purpose': 'Synthetic office-context test; not product-result evidence'}


def response(provider='pixabay', *, status=200, headers=None, payload=None):
    if payload is None:
        payload = {'hits': [pixabay_image()]} if provider == 'pixabay' else {'photos': [pexels_photo()]}
    return {'status': status, 'headers': {} if headers is None else headers,
            'data': payload, 'network_requests': 1}


def pixabay_rows():
    landscape, square = pixabay_image(), pixabay_image()
    landscape['webformatWidth'], landscape['webformatHeight'] = 640, 480
    square.update(id=124, pageURL='https://pixabay.com/photos/synthetic-square-124/')
    square['webformatWidth'], square['webformatHeight'] = 480, 480
    square['webformatURL'] = 'https://cdn.pixabay.com/photo/2020/01/02/03/04/synthetic-124_640.jpg'
    square['largeImageURL'] = 'https://pixabay.com/get/synthetic_square_1280.jpg'
    return [landscape, square]


class ProviderFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.lib = Library(self.root / 'library')
        # Override only the provider env names, never inspect or alter other secrets.
        self.environment = patch.dict(os.environ, {name: '' for name in ENV_KEYS.values()})
        self.environment.start()
        self.no_network = patch('socket.socket', side_effect=AssertionError('Offline synthetic flow test prohibits network'))
        self.no_network.start()
        # This suite checks API orchestration, not the separately tested OS lock.
        # It must not contend with an authorized real download in another process.
        self.download_lock = patch('material_agent.download.DownloadLock', side_effect=lambda: nullcontext())
        self.download_lock.start()

    def tearDown(self):
        self.download_lock.stop()
        self.no_network.stop()
        self.environment.stop()
        self.lib.close()
        self.temp.cleanup()

    def allow_key(self, provider='pixabay'):
        os.environ[ENV_KEYS[provider]] = KEYS[provider]

    def search(self, request=None, client=None, at=AT):
        return search_provider(self.lib, request or shot(), client=client, at=at)

    def store(self):
        return ProviderStore(self.lib)

    def assert_no_sensitive_disk_or_output(self, output=None):
        for marker in (*KEYS.values(), CANARY, 'DO_NOT_RETAIN'):
            if output is not None:
                self.assertNotIn(marker, json.dumps(output, ensure_ascii=False))
            for file in self.root.rglob('*'):
                if file.is_file():
                    self.assertNotIn(marker.encode(), file.read_bytes(), str(file))

    def test_public_shot_request_is_first_page_bounded_and_key_is_only_client_argument(self):
        self.allow_key()
        client = Mock(return_value=response())
        report = self.search(client=client)
        provider, path, params, secret, resolver = client.call_args.args
        self.assertEqual(('pixabay', '/api/', KEYS['pixabay'], 'system'), (provider, path, secret, resolver))
        self.assertEqual(1, params['page'])
        self.assertEqual(3, params['per_page'])
        self.assertNotIn('key', params)
        self.assertEqual('synthetic_scene', report['candidates'][0]['request']['scene_id'])
        self.assertEqual(['synthetic_line'], report['candidates'][0]['request']['line_ids'])
        self.assertFalse(report['human_usage_approval'])
        self.assert_no_sensitive_disk_or_output(report)

    def test_private_unscoped_or_unbounded_requests_stop_before_client(self):
        self.allow_key()
        client = Mock(return_value=response())
        cases = [('public_nonpersonal', False), ('query', 'person@example.invalid'),
                 ('query', 'https://private.invalid/'), ('query', 'api_key secret'),
                 ('limit', 0), ('limit', 6), ('limit', True), ('orientation', 'diagonal')]
        for field, value in cases:
            request = shot()
            request[field] = value
            with self.subTest(field=field), self.assertRaises(MaterialError):
                self.search(request, client)
        with self.assertRaises(MaterialError):
            self.search({**shot(), 'page': 2}, client)
        client.assert_not_called()

    def test_limit_one_or_two_still_uses_supported_pixabay_per_page_and_only_returns_requested_count(self):
        self.allow_key()
        client = Mock(return_value=response(payload={'hits': pixabay_rows()}))
        for limit in (1, 2):
            report = self.search(shot(limit=limit, request_id='limit_' + str(limit)), client)
            self.assertEqual(limit, len(report['candidates']))
        self.assertEqual(1, client.call_count)  # Both queries use API per_page=3.
        self.assertEqual(3, client.call_args.args[2]['per_page'])

    def test_second_search_cache_is_persistent_and_readable_without_a_key(self):
        self.allow_key()
        first_client = Mock(return_value=response())
        first = self.search(client=first_client)
        self.lib.close()
        self.lib = Library(self.root / 'library')
        os.environ.pop(ENV_KEYS['pixabay'], None)
        later_client = Mock(side_effect=AssertionError('Cache hit must not call API'))
        second = self.search(client=later_client, at=AT + timedelta(hours=23, minutes=59))
        self.assertFalse(first['cache_hit'])
        self.assertTrue(second['cache_hit'])
        self.assertEqual(0, second['network_requests'])
        self.assertEqual(first['candidates'], second['candidates'])
        first_client.assert_called_once()
        later_client.assert_not_called()

    def test_identical_public_query_reuses_cache_across_explicit_shots(self):
        self.allow_key()
        client = Mock(return_value=response())
        first = self.search(client=client)
        request = {**shot(request_id='second_shot'), 'scene_id': 'other_scene', 'asset_id': 'other_context'}
        second = self.search(request, client, AT + timedelta(seconds=1))
        self.assertTrue(second['cache_hit'])
        self.assertEqual('other_scene', second['candidates'][0]['request']['scene_id'])
        self.assertNotEqual(first['candidates'][0]['id'], second['candidates'][0]['id'])
        self.assertEqual(1, client.call_count)

    def test_expiry_boundary_requires_new_api_request_only_after_twenty_four_hours(self):
        self.allow_key()
        client = Mock(return_value=response())
        first = self.search(client=client)
        self.assertEqual(AT + timedelta(hours=24), instant(first['expires_at']))
        self.assertTrue(self.search(client=client, at=AT + timedelta(hours=24) - timedelta(microseconds=1))['cache_hit'])
        renewed = self.search(client=client, at=AT + timedelta(hours=24))
        self.assertFalse(renewed['cache_hit'])
        self.assertEqual(2, client.call_count)
        self.assertEqual(stamp(AT + timedelta(hours=24)), renewed['fetched_at'])

    def test_missing_key_on_cache_miss_never_attempts_an_api_call(self):
        client = Mock(side_effect=AssertionError('No key means no API call'))
        with self.assertRaises(MaterialError) as caught:
            self.search(client=client)
        self.assertIn('Missing PIXABAY_API_KEY', str(caught.exception))
        client.assert_not_called()
        self.assertEqual(0, self.lib.db.execute('SELECT count(*) FROM provider_attempts').fetchone()[0])

    def test_429_cooldown_uses_pixabay_delta_and_pexels_epoch_and_blocks_cache_misses(self):
        for provider in ('pixabay', 'pexels'):
            self.allow_key(provider)
            reset = '120' if provider == 'pixabay' else str(int((AT + timedelta(seconds=120)).timestamp()))
            client = Mock(side_effect=[response(provider, status=429, headers={
                'x-ratelimit-limit': '100', 'x-ratelimit-remaining': '0', 'x-ratelimit-reset': reset}), response(provider)])
            with self.subTest(provider=provider):
                with self.assertRaises(MaterialError):
                    self.search(shot(provider), client)
                store = self.store()
                try:
                    self.assertEqual(AT + timedelta(seconds=120), instant(store.state(provider)['blocked_until']))
                finally:
                    store.close()
                with self.assertRaises(MaterialError):
                    self.search(shot(provider, query='another synthetic office'), client, AT + timedelta(seconds=119))
                self.assertEqual(1, client.call_count)
                report = self.search(shot(provider, query='another synthetic office'), client, AT + timedelta(seconds=120))
                self.assertFalse(report['cache_hit'])
                self.assertEqual(2, client.call_count)

    def test_remaining_zero_success_caches_result_but_blocks_later_misses(self):
        self.allow_key()
        client = Mock(return_value=response(headers={'x-ratelimit-remaining': '0', 'x-ratelimit-reset': '60'}))
        first = self.search(client=client)
        self.assertEqual(1, len(first['candidates']))
        self.assertTrue(self.search(client=client, at=AT + timedelta(seconds=10))['cache_hit'])
        with self.assertRaises(MaterialError):
            self.search(shot(query='different public office'), client, AT + timedelta(seconds=10))
        self.assertEqual(1, client.call_count)

    def test_retry_after_longer_than_reset_is_not_ignored(self):
        self.allow_key()
        client = Mock(side_effect=[response(status=429, headers={
            'x-ratelimit-remaining': '0', 'x-ratelimit-reset': '30', 'retry-after': '120'}), response()])
        with self.assertRaises(MaterialError): self.search(client=client)
        with self.assertRaises(MaterialError):
            self.search(shot(query='different synthetic office'), client, AT + timedelta(seconds=31))
        self.assertEqual(1, client.call_count)

    def test_malformed_quota_header_does_not_erase_known_access_refusal(self):
        self.allow_key()
        client = Mock(side_effect=[response(status=429, headers={'x-ratelimit-reset': CANARY}), response()])
        with self.assertRaises(MaterialError): self.search(client=client)
        with self.assertRaises(MaterialError):
            self.search(shot(query='different synthetic office'), client, AT + timedelta(seconds=1))
        self.assertEqual(1, client.call_count)
        self.assert_no_sensitive_disk_or_output()

    def test_orientation_is_applied_on_cache_hit_and_does_not_narrow_shared_cache(self):
        self.allow_key()
        client = Mock(return_value=response(payload={'hits': pixabay_rows()}))
        all_first = self.search(shot(limit=5), client)
        os.environ.pop(ENV_KEYS['pixabay'], None)
        square = self.search(shot(limit=5, orientation='square', request_id='square_shot'), client)
        self.assertTrue(square['cache_hit'])
        self.assertEqual(['124'], [c['asset']['provider_asset_id'] for c in square['candidates']])
        self.assertEqual(2, len(all_first['candidates']))
        self.assertEqual(1, client.call_count)
        self.allow_key()
        only_square = self.search(shot(query='another public office', limit=5, orientation='square'), client)
        self.assertEqual(1, len(only_square['candidates']))
        all_after = self.search(shot(query='another public office', limit=5, request_id='any_again'), client)
        self.assertTrue(all_after['cache_hit'])
        self.assertEqual(2, len(all_after['candidates']))
        self.assertEqual(2, client.call_count)

    def test_video_orientation_changes_reuse_raw_cache_and_apply_current_dimensions(self):
        self.allow_key()
        landscape, portrait = pixabay_video(), pixabay_video()
        portrait.update(id=790, pageURL='https://pixabay.com/videos/id-790/')
        for value in portrait['videos'].values():
            value.update(width=480, height=720)
        client = Mock(return_value=response(payload={'hits': [landscape, portrait]}))
        first = self.search(shot(kind='video', orientation='landscape'), client)
        second = self.search(shot(kind='video', orientation='portrait', request_id='portrait_video'), client)
        self.assertEqual(['789'], [c['asset']['provider_asset_id'] for c in first['candidates']])
        self.assertEqual(['790'], [c['asset']['provider_asset_id'] for c in second['candidates']])
        self.assertTrue(second['cache_hit'])
        self.assertEqual(1, client.call_count)
        self.assertNotIn('orientation', client.call_args.args[2])

    def test_unknown_raw_response_fields_key_and_canary_never_enter_cache_sqlite_or_rendered_output(self):
        self.allow_key()
        payload = {'hits': [pixabay_image()], 'opaque_debug': CANARY, 'private_diagnostic': CANARY,
                   'next_page': 'https://invalid.invalid/?token=' + CANARY}
        client = Mock(return_value=response(payload=payload, headers={'x-private-debug': CANARY}))
        report = self.search(client=client)
        write_search(report, self.root / 'rendered-report')
        self.assert_no_sensitive_disk_or_output(report)

    def test_current_key_in_unknown_response_field_is_rejected_before_quota_cache_or_provenance(self):
        self.allow_key()
        client = Mock(return_value=response(payload={'hits': [pixabay_image()], 'opaque_debug': KEYS['pixabay']}))
        with self.assertRaises(MaterialError) as caught:
            self.search(client=client)
        self.assertNotIn(KEYS['pixabay'], str(caught.exception))
        self.assertEqual(0, self.lib.db.execute('SELECT count(*) FROM provider_candidates').fetchone()[0])
        self.assert_no_sensitive_disk_or_output()

    def test_current_credential_echo_in_normalized_metadata_is_rejected_before_cache_or_candidate_save(self):
        self.allow_key()
        hit = pixabay_image()
        hit['tags'] = KEYS['pixabay']
        client = Mock(return_value=response(payload={'hits': [hit]}))
        with self.assertRaises(MaterialError) as caught:
            self.search(client=client)
        self.assertNotIn(KEYS['pixabay'], str(caught.exception))
        self.assertEqual(0, self.lib.db.execute('SELECT count(*) FROM provider_candidates').fetchone()[0])
        store = self.store()
        try:
            self.assertEqual(0, store.cache.execute('SELECT count(*) FROM query_cache').fetchone()[0])
        finally:
            store.close()
        self.assert_no_sensitive_disk_or_output()

    def test_api_get_error_with_sensitive_detail_is_fixed_text_and_never_persisted(self):
        self.allow_key()
        error = RuntimeError('Synthetic transport body: ' + CANARY + ' key=' + KEYS['pixabay'])
        with patch('material_agent.providers.api_get', side_effect=error) as api:
            with self.assertRaises(MaterialError) as caught:
                self.search()
        api.assert_called_once()
        self.assertEqual('Official API search failed; credentials/response details omitted; no automatic retry', str(caught.exception))
        rendered = ''.join(traceback.format_exception(caught.exception))
        self.assertNotIn(CANARY, rendered)
        self.assertNotIn(KEYS['pixabay'], rendered)
        self.assert_no_sensitive_disk_or_output()

    def test_transport_exception_with_429_attribute_still_pauses_without_raw_detail(self):
        self.allow_key()
        error = MaterialError('Synthetic body ' + CANARY)
        error.http_status = 429
        client = Mock(side_effect=error)
        with self.assertRaises(MaterialError): self.search(client=client)
        with self.assertRaises(MaterialError):
            self.search(shot(query='different public office'), client, AT + timedelta(minutes=1))
        self.assertEqual(1, client.call_count)
        self.assert_no_sensitive_disk_or_output()

    def test_saved_candidate_cannot_be_replaced_via_store_or_sql(self):
        self.allow_key()
        report = self.search(client=Mock(return_value=response()))
        candidate = report['candidates'][0]
        changed = copy.deepcopy(candidate)
        changed['asset']['title'] = 'Changed synthetic metadata'
        store = self.store()
        try:
            with self.assertRaises(MaterialError): store.save_candidate(changed)
            with self.assertRaises(sqlite3.IntegrityError):
                self.lib.db.execute('UPDATE provider_candidates SET document=? WHERE id=?', (canonical(changed), candidate['id']))
            self.lib.db.rollback()
            self.assertEqual(candidate, store.candidate(candidate['id'], at=AT + timedelta(seconds=1)))
        finally:
            store.close()

    def test_expired_candidate_download_is_rejected_before_media_transport(self):
        self.allow_key()
        report = self.search(client=Mock(return_value=response()))
        candidate = report['candidates'][0]
        with patch('material_agent.providers.now', return_value=AT + timedelta(hours=24)), \
             patch('material_agent.provider_ingest.MediaTransfer') as media:
            with self.assertRaises(MaterialError):
                download_provider(self.lib, candidate['id'], candidate['asset']['variants'][0]['id'], USE)
        media.assert_not_called()

    def test_corrupt_candidate_digest_download_is_rejected_before_media_transport(self):
        self.allow_key()
        report = self.search(client=Mock(return_value=response()))
        candidate = report['candidates'][0]
        corrupt = copy.deepcopy(candidate)
        corrupt['id'] += '_corrupt'
        corrupt['asset']['title'] = 'Changed synthetic metadata'
        with self.lib.db:
            self.lib.db.execute('INSERT INTO provider_candidates VALUES (?,?,?)',
                                (corrupt['id'], canonical(corrupt), digest(candidate)))
        with patch('material_agent.providers.now', return_value=AT), \
             patch('material_agent.provider_ingest.MediaTransfer') as media:
            with self.assertRaises(MaterialError):
                download_provider(self.lib, corrupt['id'], corrupt['asset']['variants'][0]['id'], USE)
        media.assert_not_called()

    def test_provider_terms_change_invalidates_download_without_refetch_or_media_request(self):
        self.allow_key()
        report = self.search(client=Mock(return_value=response()))
        candidate = report['candidates'][0]
        altered = copy.deepcopy(POLICIES['pixabay'])
        altered['restrictions'].append('Synthetic newly reviewed restriction')
        with patch.dict(POLICIES, {'pixabay': altered}), patch('material_agent.providers.now', return_value=AT), \
             patch('material_agent.provider_ingest.MediaTransfer') as media:
            with self.assertRaises(MaterialError):
                download_provider(self.lib, candidate['id'], candidate['asset']['variants'][0]['id'], USE)
        media.assert_not_called()

    def test_cache_integrity_failure_does_not_silently_refetch(self):
        self.allow_key()
        client = Mock(return_value=response())
        self.search(client=client)
        store = self.store()
        try:
            row = store.cache.execute('SELECT id,document FROM query_cache').fetchone()
            changed = json.loads(row['document'])
            changed['assets'][0]['title'] = 'Changed synthetic cache metadata'
            with store.cache:
                store.cache.execute('UPDATE query_cache SET document=? WHERE id=?', (canonical(changed), row['id']))
        finally:
            store.close()
        with self.assertRaises(MaterialError): self.search(client=client, at=AT + timedelta(seconds=1))
        self.assertEqual(1, client.call_count)


if __name__ == '__main__':
    unittest.main()
