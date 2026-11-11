"""Synthetic adapter fixtures only; no real Pexels asset, key, or network use."""
import copy
import json
import unittest

from material_agent.core import MaterialError
from material_agent.pexels import MEDIA_HOSTS, parse_search, search_request


def photo():
    return {'id': 123, 'width': 1920, 'height': 1080,
            'url': 'https://www.pexels.com/photo/synthetic-office-123/',
            'photographer': 'Synthetic Fixture Author',
            'photographer_url': 'https://www.pexels.com/@synthetic-fixture',
            'alt': 'Synthetic office fixture, not a real asset', 'tags': ['office', '电脑', 'office'],
            'src': {
                'original': 'https://images.pexels.com/photos/123/pexels-photo-123.jpeg',
                'large': 'https://images.pexels.com/photos/123/pexels-photo-123.jpeg?auto=compress&cs=tinysrgb&h=650&w=940',
                'medium': 'https://images.pexels.com/photos/123/pexels-photo-123.jpeg?auto=compress&cs=tinysrgb&h=350'}}


def video_file(vid=456, host='static-videos.pexels.com'):
    return {'id': vid, 'quality': 'hd', 'file_type': 'video/mp4', 'width': 1280,
            'height': 720, 'fps': 29.97,
            'link': 'https://' + host + '/videos/123/synthetic-' + str(vid) + '.mp4'}


def video():
    return {'id': 123, 'width': 1920, 'height': 1080,
            'url': 'https://www.pexels.com/video/synthetic-office-123/', 'duration': 8,
            'user': {'id': 1, 'name': 'Synthetic Fixture Author',
                     'url': 'https://www.pexels.com/@synthetic-fixture'},
            'video_files': [video_file()]}


class PexelsSearchTests(unittest.TestCase):
    def test_search_is_bounded_public_first_page_without_key(self):
        for kind, path in [('image', '/v1/search'), ('video', '/v1/videos/search')]:
            actual, params = search_request(kind, ' office laptop ', 3, 'portrait')
            self.assertEqual(path, actual)
            self.assertEqual({'query': 'office laptop', 'per_page': 3, 'page': 1,
                              'orientation': 'portrait'}, params)
            self.assertNotIn('key', json.dumps(params).lower())
        for orientation in (None, 'any', 'all'):
            self.assertNotIn('orientation', search_request('image', '办公', 1, orientation)[1])

    def test_invalid_query_and_search_options_are_not_reflected(self):
        secret = 'api_key=synthetic-private-fixture-value'
        for args in [('audio', 'office', 1, None), ('image', 'office', True, None),
                     ('image', 'office', 0, None), ('image', 'office', 6, None),
                     ('image', 'office', 1, 'vertical'), ('image', 'office\n', 1, None),
                     ([], 'office', 1, None), ('image', 'office', 1, {}),
                     ('image', secret, 1, None), ('image', 'x' * 201, 1, None)]:
            with self.subTest(args=args):
                with self.assertRaises(MaterialError) as caught:
                    search_request(*args)
                self.assertNotIn(secret, str(caught.exception))


class PexelsParseTests(unittest.TestCase):
    def parse_photo(self, item=None):
        return parse_search({'photos': [photo() if item is None else item]}, 'image')[0]

    def parse_video(self, item=None):
        return parse_search({'videos': [video() if item is None else item]}, 'video')[0]

    def test_photo_shape_unknown_fields_and_honest_dimensions(self):
        item = photo()
        item['photographer_avatar'] = 'https://example.invalid/private-avatar'
        item['access_token'] = 'synthetic-secret-fixture'
        item['src']['tiny'] = 'https://example.invalid/ignored'
        payload = {'photos': [item], 'next_page': 'https://example.invalid/?token=synthetic-secret-fixture'}
        before = copy.deepcopy(payload)
        asset = parse_search(payload, 'image')[0]
        self.assertEqual(before, payload)
        self.assertEqual({'provider_asset_id', 'media_type', 'title', 'source_url', 'creator',
                          'creator_url', 'tags', 'duration_seconds', 'variants', 'blocked_variants'}, set(asset))
        self.assertEqual('123', asset['provider_asset_id'])
        self.assertEqual(['office', '电脑'], asset['tags'])
        self.assertIsNone(asset['duration_seconds'])
        self.assertEqual(3, len(asset['variants']))
        original, large, medium = asset['variants']
        self.assertEqual((1920, 1080), (original['width'], original['height']))
        self.assertEqual((None, None), (large['width'], medium['height']))
        self.assertTrue(all(v['mime'] == 'image/jpeg' and v['bytes'] is None and v['fps'] is None
                            and v['expires_after_seconds'] == 86400 for v in asset['variants']))
        self.assertNotIn('synthetic-secret-fixture', json.dumps(asset))
        self.assertNotIn('avatar', json.dumps(asset))

    def test_exact_media_hosts_only_and_no_sensitive_blocked_url(self):
        self.assertEqual(frozenset({'images.pexels.com', 'static-videos.pexels.com'}), MEDIA_HOSTS)
        for address in (
            'https://images.pexels.com.evil.invalid/photos/123/fixture.jpeg',
            'https://videos.pexels.com/video-files/123/fixture.mp4?token=synthetic-secret-fixture',
            'https://player.vimeo.com/external/fixture.mp4?oauth2_token_id=synthetic-secret-fixture',
            'https://api.pexels.com/v1/photos/123?key=synthetic-secret-fixture',
            'https://127.0.0.1/photos/123/fixture.jpeg',
            'https://169.254.169.254/photos/123/fixture.jpeg',
        ):
            with self.subTest(address=address):
                item = photo()
                item['src'] = {'original': address}
                asset = self.parse_photo(item)
                self.assertEqual([], asset['variants'])
                self.assertEqual({'id', 'reason'}, set(asset['blocked_variants'][0]))
                serialized = json.dumps(asset)
                self.assertNotIn(address, serialized)
                self.assertNotIn('synthetic-secret-fixture', serialized)

    def test_media_credentials_path_queries_percent_and_non_jpeg_rejected(self):
        root = 'https://images.pexels.com/photos/123/fixture.jpeg'
        for address in (
            root.replace('https:', 'http:'),
            root.replace('images.pexels.com', 'user:synthetic-secret-fixture@images.pexels.com'),
            root.replace('images.pexels.com', 'images.pexels.com:443'),
            root.replace('images.pexels.com', 'images.pexels.com.'),
            root.replace('/123/', '/124/'), root.replace('/123/', '/123/../'),
            root.replace('fixture', 'fix%74ure'), root.replace('.jpeg', '.png'),
            root + '?token=synthetic-secret-fixture', root + '?api_key=synthetic-secret-fixture',
            root + '?h=100&h=200', root + '?h=0', root + '?w=20000', root + '?auto=format',
            root + '?fit=synthetic-secret-fixture', root + '?h=%31', root + '#fragment',
            root + '\n', root.replace('/fixture', '\\fixture'),
        ):
            with self.subTest(address=address):
                item = photo()
                item['src'] = {'original': address}
                asset = self.parse_photo(item)
                self.assertEqual([], asset['variants'])
                self.assertEqual(1, len(asset['blocked_variants']))
                self.assertNotIn('synthetic-secret-fixture', json.dumps(asset))

    def test_public_transform_values_and_duplicate_url_dedup(self):
        item = photo()
        item['src'] = {'original': item['src']['original'] + '?auto=compress&cs=tinysrgb&fit=crop&dpr=2&h=650&w=940'}
        self.assertEqual(1, len(self.parse_photo(item)['variants']))
        item = photo()
        item['src']['large'] = item['src']['original']
        self.assertEqual(['original', 'medium'], [v['id'] for v in self.parse_photo(item)['variants']])

    def test_only_matching_official_source_and_public_creator_profile_retained(self):
        for field, address in (
            ('url', 'https://evil.invalid/photo/synthetic-123/'),
            ('url', 'https://www.pexels.com/photo/synthetic-124/'),
            ('url', 'https://www.pexels.com/video/synthetic-123/'),
            ('url', 'https://www.pexels.com/photo/synthetic-123/?key=synthetic-secret-fixture'),
            ('photographer_url', 'https://www.pexels.com/@fixture?token=synthetic-secret-fixture'),
            ('photographer_url', 'https://private:synthetic-secret-fixture@www.pexels.com/@fixture'),
            ('photographer_url', 'https://www.pexels.com/api/'),
        ):
            item = photo()
            item[field] = address
            with self.subTest(field=field):
                with self.assertRaises(MaterialError) as caught:
                    self.parse_photo(item)
                self.assertNotIn(address, str(caught.exception))
                self.assertNotIn('synthetic-secret-fixture', str(caught.exception))
        item = photo()
        item['photographer_url'] = None
        self.assertIsNone(self.parse_photo(item)['creator_url'])

    def test_video_mp4_bounded_variants_and_unknown_host_block(self):
        item = video()
        item['video_files'] = [video_file(456), video_file(457), video_file(458), video_file(459),
                               video_file(460, 'videos.pexels.com')]
        asset = self.parse_video(item)
        self.assertEqual('Pexels video 123', asset['title'])
        self.assertEqual(8, asset['duration_seconds'])
        self.assertEqual(['456', '457', '458'], [v['id'] for v in asset['variants']])
        self.assertEqual(29.97, asset['variants'][0]['fps'])
        self.assertEqual([{'id': '459', 'reason': 'variant_limit'},
                          {'id': '460', 'reason': 'unapproved_media_host'}], asset['blocked_variants'])

    def test_video_invalid_ids_mime_dimensions_fps_and_queries_are_blocked(self):
        cases = [('id', 'synthetic-secret-fixture'), ('file_type', 'text/html'), ('quality', 'hls'), ('quality', {}),
                 ('width', True), ('height', 0), ('fps', float('nan')), ('fps', -1),
                 ('link', 'https://static-videos.pexels.com/videos/123/fixture.mp4?token=synthetic-secret-fixture')]
        for field, value in cases:
            item = video()
            item['video_files'][0][field] = value
            with self.subTest(field=field):
                asset = self.parse_video(item)
                self.assertEqual([], asset['variants'])
                self.assertEqual(1, len(asset['blocked_variants']))
                self.assertNotIn('synthetic-secret-fixture', json.dumps(asset))
        item = video()
        del item['video_files'][0]['fps']
        self.assertIsNone(self.parse_video(item)['variants'][0]['fps'])

    def test_top_level_invalid_metadata_fail_closed_without_sensitive_echo(self):
        for payload, kind in [(None, 'image'), ({'photos': {}}, 'image'), ({'videos': []}, 'audio'),
                              ({'photos': [photo(), photo()]}, 'image')]:
            with self.subTest(kind=kind), self.assertRaises(MaterialError):
                parse_search(payload, kind)
        for field, value in [('id', True), ('photographer', 'api_key=synthetic-secret-fixture'),
                             ('alt', 'token@example.invalid'), ('tags', ['secret=synthetic-secret-fixture'])]:
            item = photo()
            item[field] = value
            with self.subTest(field=field):
                with self.assertRaises(MaterialError) as caught:
                    self.parse_photo(item)
                self.assertNotIn('synthetic-secret-fixture', str(caught.exception))
        for duration in (False, 0, -1, float('inf'), None):
            item = video()
            item['duration'] = duration
            with self.subTest(duration=duration), self.assertRaises(MaterialError):
                self.parse_video(item)

    def test_empty_and_bounded_page_result(self):
        self.assertEqual([], parse_search({'photos': [], 'page': 1}, 'image'))
        rows = []
        for i in range(123, 129):
            item = photo()
            item['id'] = i
            item['url'] = 'https://www.pexels.com/photo/synthetic-' + str(i) + '/'
            rows.append(item)
        self.assertEqual(5, len(parse_search({'photos': rows}, 'image')))


if __name__ == '__main__':
    unittest.main()
