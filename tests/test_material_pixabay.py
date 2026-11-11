"""Synthetic API metadata only: no key, actual media, network or license assertion."""
import copy
import json
import traceback
import unittest
from urllib.parse import parse_qs, urlencode
from unittest.mock import patch

from material_agent.core import MaterialError
from material_agent.pixabay import MEDIA_HOSTS, search_request, parse_search


def image():
    return {'id': 123, 'pageURL': 'https://pixabay.com/photos/synthetic-office-123/', 'type': 'photo',
            'tags': 'synthetic, office, blue, office', 'user': 'Synthetic-Creator', 'user_id': 456,
            'userImageURL': 'https://unknown.invalid/avatar?secret=DO_NOT_RETAIN',
            'largeImageURL': 'https://pixabay.com/get/synthetic_hash_1280.jpg',
            'webformatURL': 'https://cdn.pixabay.com/photo/2020/01/02/03/04/synthetic-123_640.jpg',
            'webformatWidth': 640, 'webformatHeight': 480,
            'imageWidth': 4000, 'imageHeight': 3000, 'imageSize': 12345678,
            'new_future_field': {'token': 'DO_NOT_RETAIN'}}


def video():
    def rendition(name, width, height):
        return {'url': 'https://cdn.pixabay.com/video/2020/01/02/synthetic_' + name + '.mp4',
                'width': width, 'height': height, 'size': 123456,
                'thumbnail': 'https://unknown.invalid/poster?secret=DO_NOT_RETAIN', 'fps': 60}
    return {'id': 789, 'pageURL': 'https://pixabay.com/videos/id-789/', 'type': 'film',
            'tags': 'synthetic, video', 'duration': 12.5, 'user': 'Synthetic-Creator', 'user_id': 456,
            'videos': {'large': rendition('large', 3840, 2160), 'medium': rendition('medium', 1920, 1080),
                       'small': rendition('small', 1280, 720), 'tiny': rendition('tiny', 640, 360)}}


class PixabayRequestTests(unittest.TestCase):
    def test_request_is_pure_and_never_contains_a_key(self):
        query = '电脑 办公 & + #'
        with patch('socket.socket', side_effect=AssertionError('No network permitted')):
            path, params = search_request('image', query, 1, 'landscape')
        self.assertEqual('/api/', path)
        self.assertEqual(query, params['q'])
        self.assertNotIn('key', params)
        self.assertEqual('photo', params['image_type'])
        self.assertEqual('horizontal', params['orientation'])
        self.assertEqual('true', params['safesearch'])
        self.assertEqual([query], parse_qs(urlencode(params))['q'])

    def test_per_page_minimum_three_and_fixed_page_one(self):
        for limit in range(1, 6):
            _, params = search_request('image', 'synthetic', limit, 'portrait')
            self.assertEqual(max(3, limit), params['per_page'])
            self.assertEqual(1, params['page'])
            self.assertEqual('vertical', params['orientation'])

    def test_video_and_square_do_not_send_unsupported_orientation(self):
        for orientation in ('landscape', 'portrait', 'square', 'any', 'all', None):
            path, params = search_request('video', 'synthetic', 2, orientation)
            self.assertEqual('/api/videos/', path)
            self.assertEqual('all', params['video_type'])
            self.assertNotIn('orientation', params)
        for orientation in ('square', 'any', 'all', None):
            self.assertNotIn('orientation', search_request('image', 'synthetic', 3, orientation)[1])

    def test_invalid_requests_raise_static_material_errors(self):
        bad = [('audio', 'ok', 1, 'any'), ('image', '', 1, 'any'), ('image', 'a' * 101, 1, 'any'),
               ('image', 'private\nquery', 1, 'any'), ('image', 'ok', True, 'any'),
               ('image', 'ok', 0, 'any'), ('image', 'ok', 6, 'any'), ('image', 'ok', 1, 'diagonal'),
               ('image', 'ok', 1, [])]
        for args in bad:
            with self.subTest(args=args), self.assertRaises(MaterialError):
                search_request(*args)


class PixabayParseTests(unittest.TestCase):
    def test_image_schema_expiry_and_no_invented_rendition_size(self):
        result = parse_search({'hits': [image()], 'unknown': 'ignored'}, 'image')[0]
        self.assertEqual('123', result['provider_asset_id'])
        self.assertEqual(['synthetic', 'office', 'blue'], result['tags'])
        self.assertEqual('https://pixabay.com/users/Synthetic-Creator-456/', result['creator_url'])
        self.assertIsNone(result['duration_seconds'])
        large, web = result['variants']
        self.assertEqual('image/jpeg', large['mime'])
        self.assertIsNone(large['width'])
        self.assertIsNone(large['height'])
        self.assertIsNone(large['bytes'])
        self.assertIsNone(large['fps'])
        self.assertIsNone(large['expires_after_seconds'])
        self.assertEqual((640, 480, 86400), (web['width'], web['height'], web['expires_after_seconds']))
        self.assertIsNone(web['bytes'])
        self.assertNotIn('DO_NOT_RETAIN', json.dumps(result))

    def test_old_english_source_page_pattern_is_supported(self):
        hit = image()
        hit['pageURL'] = 'https://pixabay.com/en/synthetic-office-123/'
        self.assertEqual(hit['pageURL'], parse_search({'hits': [hit]}, 'image')[0]['source_url'])

    def test_video_has_at_most_three_mp4_versions_and_unknown_fps(self):
        result = parse_search({'hits': [video()]}, 'video')[0]
        self.assertEqual(12.5, result['duration_seconds'])
        self.assertEqual(['large', 'medium', 'small'], [v['id'] for v in result['variants']])
        self.assertTrue(all(v['mime'] == 'video/mp4' and v['fps'] is None and v['expires_after_seconds'] is None
                            for v in result['variants']))
        self.assertEqual(1920, result['variants'][1]['width'])
        self.assertNotIn('DO_NOT_RETAIN', json.dumps(result))

    def test_empty_and_zero_size_video_versions_are_skipped(self):
        hit = video()
        hit['videos']['large']['url'] = ''
        hit['videos']['medium']['size'] = 0
        self.assertEqual(['small'], [v['id'] for v in parse_search({'hits': [hit]}, 'video')[0]['variants']])

    def test_unknown_media_host_is_only_a_fixed_id_and_reason(self):
        hit = image()
        hit['largeImageURL'] = 'https://unknown.invalid/private?key=DO_NOT_RETAIN'
        hit['webformatURL'] = 'https://unknown.invalid/private.jpg'
        result = parse_search({'hits': [hit]}, 'image')[0]
        self.assertEqual([], result['variants'])
        self.assertEqual(['largeImageURL', 'webformatURL'], [r['id'] for r in result['blocked_variants']])
        self.assertTrue(all(set(row) == {'id', 'reason'} for row in result['blocked_variants']))
        self.assertNotIn('unknown.invalid', json.dumps(result))
        self.assertNotIn('DO_NOT_RETAIN', json.dumps(result))

    def test_bad_media_urls_never_escape_into_results_or_exceptions(self):
        path = '/get/synthetic.jpg'
        bad = ['http://pixabay.com' + path, 'https://user:DO_NOT_RETAIN@pixabay.com' + path,
               'https://pixabay.com/get/synthetic.jpg?key=DO_NOT_RETAIN',
               'https://pixabay.com/get/synthetic.jpg#token=DO_NOT_RETAIN',
               'https://pixabay.com/api/?key=DO_NOT_RETAIN', 'https://cdn.pixabay.com/api/private.jpg',
               'https://pixabay.com:443' + path, 'https://pixabay.com.evil.invalid' + path,
               'https://pixabay.com/get/%2e%2e%2fDO_NOT_RETAIN.jpg',
               'https://pixabay.com/get/synthetic.svg', 'file:///private/DO_NOT_RETAIN.jpg',
               'https://127.0.0.1' + path, 'https://pixabay.com/get/DO_NOT_RETAIN\\path.jpg']
        for address in bad:
            hit = image()
            hit['largeImageURL'] = address
            result = parse_search({'hits': [hit]}, 'image')[0]
            self.assertEqual(['webformatURL'], [v['id'] for v in result['variants']])
            self.assertNotIn('DO_NOT_RETAIN', json.dumps(result))

    def test_source_is_official_and_binds_the_numeric_asset_identity(self):
        bad = ['https://evil.invalid/photos/synthetic-123/', 'https://pixabay.com/api/123/',
               'https://pixabay.com/photos/synthetic-456/', 'https://pixabay.com/users/synthetic-123/',
               'https://pixabay.com/photos/synthetic-123/?key=DO_NOT_RETAIN',
               'https://user:DO_NOT_RETAIN@pixabay.com/photos/synthetic-123/']
        for address in bad:
            hit = image()
            hit['pageURL'] = address
            with self.subTest(address=address), self.assertRaises(MaterialError) as caught:
                parse_search({'hits': [hit]}, 'image')
            self.assertNotIn('DO_NOT_RETAIN', str(caught.exception))

    def test_creator_avatar_and_unreviewed_profile_fields_are_ignored(self):
        hit = image()
        hit['user'] = '作者名字'
        hit['profileURL'] = 'https://evil.invalid/?key=DO_NOT_RETAIN'
        result = parse_search({'hits': [hit]}, 'image')[0]
        self.assertEqual('作者名字', result['creator'])
        self.assertIsNone(result['creator_url'])
        self.assertNotIn('DO_NOT_RETAIN', json.dumps(result))

    def test_invalid_authority_does_not_leak_through_exception_chain(self):
        hit = image()
        hit['pageURL'] = 'https://[DO_NOT_RETAIN]/photos/synthetic-123/'
        with self.assertRaises(MaterialError) as caught:
            parse_search({'hits': [hit]}, 'image')
        self.assertNotIn('DO_NOT_RETAIN', ''.join(traceback.format_exception(caught.exception)))
        hit = image()
        hit['largeImageURL'] = 'https://[DO_NOT_RETAIN]/get/synthetic.jpg'
        self.assertNotIn('DO_NOT_RETAIN', json.dumps(parse_search({'hits': [hit]}, 'image')))

    def test_bad_variant_metadata_is_blocked_and_not_retained(self):
        for bad in (-1, True, 'DO_NOT_RETAIN', float('nan')):
            hit = video()
            hit['videos']['large']['width'] = bad
            result = parse_search({'hits': [hit]}, 'video')[0]
            self.assertEqual(['medium', 'small'], [v['id'] for v in result['variants']])
            self.assertEqual([{'id': 'large', 'reason': 'invalid_variant_metadata_or_url'}], result['blocked_variants'])
            self.assertNotIn('DO_NOT_RETAIN', json.dumps(result))

    def test_response_bounds_errors_and_unknown_keys_are_safe(self):
        for payload in (None, {}, {'error': 'key=DO_NOT_RETAIN'}, {'hits': 'DO_NOT_RETAIN'}, {'hits': [None]},
                        {'hits': [dict(image(), id=True)]}, {'hits': [dict(video(), duration=float('inf'))]}):
            with self.subTest(payload=payload), self.assertRaises(MaterialError) as caught:
                parse_search(payload, 'video' if isinstance(payload, dict) and isinstance(payload.get('hits'), list)
                             and payload['hits'] and isinstance(payload['hits'][0], dict) and payload['hits'][0].get('id') == 789 else 'image')
            self.assertNotIn('DO_NOT_RETAIN', str(caught.exception))
        self.assertEqual([], parse_search({'hits': [], 'future_key': 'ignored'}, 'image'))
        self.assertEqual(frozenset({'pixabay.com', 'cdn.pixabay.com'}), MEDIA_HOSTS)

    def test_nonfinite_bool_and_extremely_large_durations_are_material_errors(self):
        for duration in (True, float('nan'), float('inf'), -1, 0, 10 ** 1000):
            hit = video()
            hit['duration'] = duration
            with self.subTest(duration_type=type(duration).__name__), self.assertRaises(MaterialError):
                parse_search({'hits': [hit]}, 'video')


if __name__ == '__main__':
    unittest.main()
