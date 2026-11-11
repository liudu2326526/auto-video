"""Offline curated-source boundaries and complete, real GIF validation."""
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from material_agent.core import Library, MaterialError, read_json, validate_record
from material_agent.download import validate_media
from material_agent.provider_ingest import catalog, download_catalog, _variant_url


ROOT = Path(__file__).resolve().parents[1]
CHECKED = {'noto': '2026-11-04T10:21:05Z', 'mixkit': '2026-11-04T10:18:33Z'}
NOTO = {'noto_thinking': '1f914', 'noto_party': '1f389'}
USE = read_json(ROOT / 'fixtures/materials/use-context.json')
FIXED_NOW = datetime(2026, 11, 4, 11, tzinfo=timezone.utc)


class CatalogTests(unittest.TestCase):
    def test_catalogs_preserve_strict_records_and_original_checks(self):
        for source in ('noto', 'mixkit'):
            with self.subTest(source=source):
                value = catalog(source)
                self.assertEqual('material_source_catalog/v1', value['schema'])
                self.assertEqual(CHECKED[source], value['checked_at'])
                self.assertEqual(2, len(value['items']))
                for item in value['items']:
                    record = item['record']
                    self.assertEqual(record, validate_record(record, at=FIXED_NOW))
                    self.assertEqual(source, item['provider'])
                    self.assertEqual(CHECKED[source], record['source']['retrieved_at'])
                    self.assertEqual(CHECKED[source], record['rights']['checked_at'])
                    self.assertTrue(item['evidence_urls'])
                    self.assertGreaterEqual({e['scope'] for e in record['rights']['evidence']},
                                            {'asset_license', 'license_terms'})
                    self.assertTrue(all(e['retrieved_at'] == CHECKED[source]
                                        for e in record['rights']['evidence']))

    def test_catalog_rejects_unknown_envelope_and_record_fields(self):
        for mutation in ('envelope', 'item', 'record', 'rights', 'duplicate_id'):
            with self.subTest(mutation=mutation):
                value = copy.deepcopy(catalog('noto'))
                if mutation == 'envelope':
                    value['unexpected'] = True
                elif mutation == 'item':
                    value['items'][0]['unexpected'] = True
                elif mutation == 'duplicate_id':
                    value['items'][1]['id'] = value['items'][0]['id']
                elif mutation == 'record':
                    value['items'][0]['record']['approved'] = True
                else:
                    value['items'][0]['record']['rights']['approved'] = True
                with mock.patch('material_agent.provider_ingest.read_json', return_value=value):
                    with self.assertRaises(MaterialError):
                        catalog('noto')

    def test_noto_gifs_have_family_license_binding_and_required_credit(self):
        value = catalog('noto')
        self.assertEqual(set(NOTO), {item['id'] for item in value['items']})
        for item in value['items']:
            with self.subTest(item=item['id']):
                record, rights = item['record'], item['record']['rights']
                rgi = NOTO[item['id']]
                self.assertEqual('animation', item['kind'])
                self.assertIs(True, item['automation_allowed'])
                self.assertIsNone(item['blocked_reason'])
                self.assertEqual('image', record['media_type'])
                self.assertEqual('Google / Google Fonts', record['source']['creator'])
                self.assertEqual('https://googlefonts.github.io/noto-emoji-files/', record['source']['page_url'])
                self.assertEqual('https://fonts.gstatic.com/s/e/notoemoji/latest/' + rgi + '/512.gif',
                                 record['source']['download_url'])
                self.assertEqual('known', rights['status'])
                self.assertEqual('CC-BY-4.0', rights['license_id'])
                self.assertEqual('https://creativecommons.org/licenses/by/4.0/', rights['license_url'])
                self.assertIs(True, rights['attribution_required'])
                self.assertIs(False, rights['share_alike_required'])
                self.assertEqual(('allowed', 'allowed'), (rights['commercial_use'], rights['derivatives']))
                self.assertEqual({'internal', 'public_social', 'paid_advertising'},
                                 set(rights['allowed_distributions']))
                credit = rights['attribution_text']
                for part in ('Google', 'Source:', record['source']['page_url'],
                             record['source']['download_url'], rights['license_url'],
                             'Changes: none (original file)'):
                    self.assertIn(part, credit)
                evidence = rights['evidence']
                self.assertTrue(any(e['scope'] == 'asset_license'
                                    and e['url'] == record['source']['page_url'] for e in evidence))
                self.assertTrue(any(e['scope'] == 'asset_license' and 'emojis_data.js' in e['url']
                                    and rgi in e['excerpt'] and 'hasGfontsAnim' in e['excerpt']
                                    for e in evidence))
                self.assertTrue(any(e['scope'] == 'license_terms'
                                    and e['url'] == 'https://creativecommons.org/licenses/by/4.0/legalcode.en'
                                    for e in evidence))
                self.assertIn('latest; HEAD Last-Modified=', item['source_version'])
                self.assertIn('no immutable animation commit verified', item['source_version'])
                risks = ' '.join(record['risks'])
                for caution in ('No GIF bytes', 'all GIF frames', 'not an approved static frame',
                                'editing-compatible video', 'mutable latest'):
                    self.assertIn(caution, risks)
                restrictions = ' '.join(rights['restrictions'])
                self.assertIn('actual changes', restrictions)
                self.assertIn('endorsement', restrictions)

    def test_mixkit_is_manual_only_even_with_google_login_permission(self):
        value = catalog('mixkit')
        for item in value['items']:
            self.assertIs(False, item['automation_allowed'])
            self.assertTrue(item['blocked_reason'])
            self.assertEqual('restricted', item['record']['rights']['status'])
            self.assertIsNone(item['record']['source']['download_url'])
        # Login permission grants no exception to the source's automation terms.
        # Even a modified catalog flag must not turn Mixkit into a network request.
        edited = copy.deepcopy(value)
        for item in edited['items']:
            item['automation_allowed'] = True
            item['blocked_reason'] = None
        permission = {**USE, 'google_login_authorized': True}
        with mock.patch('material_agent.provider_ingest.catalog', return_value=edited), \
                mock.patch('material_agent.provider_ingest.MediaTransfer') as transfer, \
                mock.patch('material_agent.provider_ingest._ingest') as ingest:
            for item_id in [i['id'] for i in value['items']] + ['mixkit_any_other_item']:
                with self.subTest(item_id=item_id), self.assertRaisesRegex(MaterialError, 'Mixkit prohibits'):
                    download_catalog(None, item_id, permission)
            transfer.assert_not_called()
            ingest.assert_not_called()

    def test_noto_dispatch_explicitly_requests_bounded_animation_validation(self):
        with mock.patch('material_agent.provider_ingest._ingest', return_value={'synthetic': True}) as ingest:
            for item_id, rgi in NOTO.items():
                self.assertEqual({'synthetic': True}, download_catalog(None, item_id, USE))
                args, kwargs = ingest.call_args
                record, checker = args[1], args[4]
                self.assertEqual('noto', args[2]['provider'])
                self.assertEqual(item_id, args[2]['asset_id'])
                self.assertEqual(record['source']['download_url'], checker(record['source']['download_url']))
                self.assertEqual('https://fonts.gstatic.com/s/e/notoemoji/latest/' + rgi + '/512.gif',
                                 record['source']['download_url'])
                self.assertIs(True, kwargs['allow_animation'])
                self.assertEqual('image/gif', kwargs['mime'])
                self.assertEqual(8 * 1024 * 1024, kwargs['max_bytes'])
                with self.assertRaises(MaterialError):
                    checker('https://fonts.gstatic.com/s/e/notoemoji/latest/1f600/512.gif')

    def test_noto_hardcoded_ids_hosts_paths_and_renditions_cannot_change(self):
        for item_id, rgi in NOTO.items():
            address = 'https://fonts.gstatic.com/s/e/notoemoji/latest/' + rgi + '/512.gif'
            self.assertEqual(address, _variant_url('noto', address, None, catalog_id=item_id))
            rejected = [address.replace('fonts.gstatic.com', 'evil.example'),
                        address.replace('https://', 'http://'),
                        address.replace('fonts.gstatic.com', 'fonts.gstatic.com.evil.example'),
                        address.replace('fonts.gstatic.com', 'user:secret@fonts.gstatic.com'),
                        address.replace('/' + rgi + '/', '/1f600/'),
                        address.replace('512.gif', '512.webp'), address + '?x=1', address + '#x']
            for changed in rejected:
                with self.subTest(item=item_id, changed=changed), self.assertRaises(MaterialError):
                    _variant_url('noto', changed, None, catalog_id=item_id)
            for changed_id in ('noto_unknown', 'mixkit_anything', None):
                with self.subTest(changed_id=changed_id), self.assertRaises(MaterialError):
                    _variant_url('noto', address, None, catalog_id=changed_id)
        thinking = 'https://fonts.gstatic.com/s/e/notoemoji/latest/1f914/512.gif'
        with self.assertRaises(MaterialError):
            _variant_url('noto', thinking, None, catalog_id='noto_party')

    def test_tampered_noto_catalog_stops_before_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            library = Library(Path(directory).resolve() / 'library')
            try:
                for address in ('https://evil.example/512.gif',
                                'https://fonts.gstatic.com/s/e/notoemoji/latest/1f600/512.gif'):
                    value = copy.deepcopy(catalog('noto'))
                    value['items'][0]['record']['source']['download_url'] = address
                    with mock.patch('material_agent.provider_ingest.catalog', return_value=value), \
                            mock.patch('material_agent.provider_ingest.now', return_value=FIXED_NOW), \
                            mock.patch('material_agent.provider_ingest.MediaTransfer') as transfer:
                        with self.subTest(address=address), self.assertRaisesRegex(MaterialError, 'two specifically reviewed'):
                            download_catalog(library, 'noto_thinking', USE)
                        transfer.assert_not_called()
                self.assertEqual(0, library.db.execute('SELECT count(*) FROM records').fetchone()[0])
            finally:
                library.close()


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg validation tools unavailable')
class AnimationDecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.animated = cls.root / 'two-frames.gif'
        cls.still = cls.root / 'one-frame.gif'
        cls.real_run = staticmethod(subprocess.run)
        for path, count in ((cls.animated, 2), (cls.still, 1)):
            frames = b'\xff\x00\x00' * 16
            if count == 2:
                frames += b'\x00\x00\xff' * 16
            cls.real_run([shutil.which('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'rawvideo',
                          '-pixel_format', 'rgb24', '-video_size', '4x4', '-framerate', '2',
                          '-i', 'pipe:0', '-frames:v', str(count), '-loop', '0', '-f', 'gif', str(path)],
                         input=frames, check=True, capture_output=True, timeout=15)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_real_two_frame_gif_requires_explicit_animation_and_complete_decode(self):
        with mock.patch('material_agent.download.subprocess.run', wraps=self.real_run) as run:
            with self.assertRaisesRegex(MaterialError, 'explicit full-animation'):
                validate_media(self.animated, 'image/gif', 'image')
            run.assert_not_called()
            result = validate_media(self.animated, 'image/gif', 'image', allow_animation=True)
            self.assertTrue(result['decoded'])
            self.assertEqual(('image/gif', 'image', '.gif'),
                             (result['mime'], result['media_type'], result['suffix']))
            self.assertEqual(2, result['animation']['frames'])
            self.assertGreater(result['animation']['duration_ms'], 0)
            self.assertIs(False, result['animation']['editing_compatible'])
            self.assertEqual(2, len(run.call_args_list))
            probe, decode = [call.args[0] for call in run.call_args_list]
            self.assertIn('-count_frames', probe)
            self.assertIn('-xerror', decode)
            self.assertNotIn('-frames:v', decode)
            self.assertEqual(['-f', 'null', '-'], decode[-3:])

    def test_real_static_gif_is_not_accepted_as_animation(self):
        with self.assertRaises(MaterialError):
            validate_media(self.still, 'image/gif', 'image', allow_animation=True)

    def test_truncated_real_gif_is_rejected(self):
        original = self.animated.read_bytes()
        for size in (32, len(original) // 2, len(original) - 20):
            with self.subTest(size=size):
                damaged = self.root / ('truncated-' + str(size) + '.gif')
                damaged.write_bytes(original[:size])
                with self.assertRaises(MaterialError):
                    validate_media(damaged, 'image/gif', 'image', allow_animation=True)

    def test_gif_mime_or_declared_media_kind_must_match(self):
        for mime, kind in (('image/png', 'image'), ('image/gif', 'video')):
            with self.subTest(mime=mime, kind=kind), self.assertRaises(MaterialError):
                validate_media(self.animated, mime, kind, allow_animation=True)

    def test_animation_budget_failures_stop_before_ffmpeg_decode(self):
        base = {'streams': [{'codec_type': 'video', 'width': 4, 'height': 4, 'nb_read_frames': '2'}],
                'format': {'duration': '1', 'format_name': 'gif'}}
        for failure in ('frames', 'duration', 'nonfinite', 'decode_work', 'dimensions'):
            info = copy.deepcopy(base)
            stream = info['streams'][0]
            if failure == 'frames':
                stream['nb_read_frames'] = '301'
            elif failure == 'duration':
                info['format']['duration'] = '31'
            elif failure == 'nonfinite':
                info['format']['duration'] = 'nan'
            elif failure == 'decode_work':
                stream.update(width=1000, height=1000, nb_read_frames='201')
            else:
                stream['width'] = 16385
            response = subprocess.CompletedProcess(['ffprobe'], 0, stdout=json.dumps(info), stderr='')
            with self.subTest(failure=failure), \
                    mock.patch('material_agent.download.subprocess.run', return_value=response) as run:
                with self.assertRaises(MaterialError):
                    validate_media(self.animated, 'image/gif', 'image', allow_animation=True)
                self.assertEqual(1, run.call_count)
                self.assertEqual('ffprobe', run.call_args.args[0][0])


if __name__ == '__main__':
    unittest.main()
