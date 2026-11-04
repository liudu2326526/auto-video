"""Material-library invariants, rights gates, writer/editor and CLI handoffs."""
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from material_agent.core import Library, MaterialError, digest, stamp, validate_requests, read_json


AT = datetime(2026, 11, 4, 8, 32, tzinfo=timezone.utc)
USE = {'commercial': True, 'derivatives': True, 'distribution': 'public_social', 'role': 'illustrative_context',
       'share_alike_compatible': False}


def record(rid='office_1', kind='image'):
    return {'schema': 'material_record/v1', 'id': rid, 'title': 'Office laptop',
            'description': '电脑办公情境，不是产品结果', 'media_type': kind, 'tags': ['电脑', 'office'],
            'source': {'provider': 'test fixture only', 'creator': 'fictional test creator',
                       'page_url': 'https://example.org/asset', 'download_url': 'https://example.org/a.png',
                       'retrieved_at': stamp(AT), 'access': 'public_no_login'},
            'rights': {'status': 'known', 'license_id': 'TEST-ONLY', 'license_url': 'https://example.org/license',
                       'commercial_use': 'allowed', 'derivatives': 'allowed', 'attribution_required': True,
                       'attribution_text': 'Fictional fixture creator, TEST-ONLY', 'restrictions': ['Credit the creator'],
                       'checked_at': stamp(AT), 'evidence': [
                           {'url': 'https://example.org/asset', 'retrieved_at': stamp(AT),
                            'scope': 'asset_license', 'excerpt': 'Synthetic asset-specific test assertion'},
                           {'url': 'https://example.org/license', 'retrieved_at': stamp(AT),
                            'scope': 'license_terms', 'excerpt': 'Synthetic commercial/derivative test assertion'}],
                       'allowed_distributions': ['internal', 'public_social'], 'valid_until': None,
                       'share_alike_required': False},
            'risks': ['Test only; no real-world license assertion']}


def requests(kind='image'):
    return {'schema': 'material_requests/v1', 'project_id': 'gamma_test', 'public_nonpersonal': True,
            'use_context': copy.deepcopy(USE), 'requests': [
                {'id': 'req_office', 'scene_id': 'scene_02', 'line_ids': ['line_context'], 'asset_id': 'office',
                 'description': '电脑办公氛围插入镜头', 'public_queries': ['office laptop'], 'media_types': [kind],
                 'tags': ['电脑'], 'role': 'illustrative_context'}]}


def receipt(row, decision='approved', use=None):
    return {'schema': 'material_review/v1', 'record_id': row['id'], 'record_digest': row['digest'],
            'file_sha256': row['blob_sha256'], 'reviewer': 'fixture operator, not a real clearance',
            'reviewed_at': stamp(AT), 'expires_at': stamp(AT + timedelta(days=7)),
            'use_context': copy.deepcopy(use or USE), 'decision': decision,
            'conditions_acknowledged': ['Credit the creator'], 'risk_review': 'Fixture assertion only',
            'visual_review': 'Fixture assertion only; not a real observed public asset', 'reviewer_kind': 'human'}


class MaterialTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / 'library')
        self.source = self.root / 'original.png'
        # Tiny valid PNG retained as an input; no downloaded media in the test tree.
        import base64
        self.source.write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='))

    def tearDown(self):
        self.library.close()
        self.temp.cleanup()

    def add(self, item=None, source=None):
        item = item or record()
        return self.library.add(item, source or self.source,
                                {'downloaded_at': stamp(AT), 'method': 'previous_local_material',
                                 'source_url': item['source']['page_url']}, at=AT)

    def approve(self, row):
        self.library.review(receipt(row), at=AT)

    def test_original_preserved_hash_dedup_separate_licenses(self):
        original = self.source.read_bytes()
        a = self.add()
        b = record('office_2')
        b['source']['page_url'] = 'https://example.org/other-source'
        b['rights']['status'] = 'unknown'
        b = self.add(b)
        self.assertEqual(a['blob_sha256'], b['blob_sha256'])
        self.assertEqual(1, self.library.db.execute('SELECT count(*) FROM blobs').fetchone()[0])
        self.approve(a)
        self.assertTrue(self.library.eligibility(a['id'], USE, AT)['eligible'])
        self.assertFalse(self.library.eligibility(b['id'], USE, AT)['eligible'])
        self.assertEqual(original, self.source.read_bytes())
        self.source.unlink()
        self.assertTrue(self.library.eligibility(a['id'], USE, AT)['eligible'])

    def test_candidate_no_local_bytes_and_immutable_id(self):
        row = self.library.add(record(), at=AT)
        self.assertIn('Candidate has no local file', '; '.join(self.library.eligibility(row['id'], USE, AT)['blockers']))
        with self.assertRaises(MaterialError):
            self.add()
        with self.assertRaises(MaterialError):
            self.library.review(receipt(row), AT)

    def test_unknown_license_does_not_clear_even_with_receipt(self):
        for status in ('unknown', 'restricted'):
            r = record(status)
            r['rights']['status'] = status
            row = self.add(r)
            with self.assertRaises(MaterialError):
                self.approve(row)
            self.assertFalse(self.library.eligibility(row['id'], USE, AT)['eligible'])

    def test_old_local_material_can_be_saved_without_inventing_a_source(self):
        item = record('unknown_history')
        item['source'].update(page_url=None, download_url=None, creator='unknown',
                              provider='previous local material; origin unknown', access='unknown_local_history')
        item['rights'].update(status='unknown', license_id='unknown', license_url=None, commercial_use='unknown',
                              derivatives='unknown', attribution_required=None, attribution_text='', checked_at=None,
                              evidence=[], allowed_distributions=None, share_alike_required=None)
        row = self.add(item)
        self.assertIsNotNone(row['blob_sha256'])
        self.assertIsNone(self.library.history(row['id'])['download_record']['source_url'])
        self.assertFalse(self.library.eligibility(row['id'], USE, AT)['eligible'])
        with self.assertRaises(MaterialError):
            self.approve(row)

    def test_expiry_context_changes_rejection_and_hash_tampering(self):
        row = self.add()
        self.approve(row)
        self.assertTrue(self.library.eligibility(row['id'], USE, AT)['eligible'])
        other = {**USE, 'distribution': 'paid_advertising'}
        self.assertFalse(self.library.eligibility(row['id'], other, AT)['eligible'])
        self.assertFalse(self.library.eligibility(row['id'], USE, AT + timedelta(days=8))['eligible'])
        self.library.review(receipt(row, decision='rejected'), AT)
        self.assertFalse(self.library.eligibility(row['id'], USE, AT)['eligible'])
        renewed = receipt(row)
        later = AT + timedelta(seconds=1)
        renewed['reviewed_at'] = stamp(later)
        self.library.review(renewed, later)
        self.library._blob(row['blob_sha256']).write_bytes(b'changed')
        self.assertFalse(self.library.eligibility(row['id'], USE, later)['eligible'])

    def test_old_approval_cannot_be_replayed_after_rejection(self):
        row = self.add()
        approved = receipt(row)
        self.library.review(approved, AT)
        self.library.review(approved, AT)
        self.assertEqual(1, len(self.library.history(row['id'])['reviews']))
        rejected = receipt(row, decision='rejected')
        rejected['reviewed_at'] = stamp(AT + timedelta(days=1))
        self.library.review(rejected, AT + timedelta(days=1))
        with self.assertRaises(MaterialError):
            self.library.review(approved, AT + timedelta(days=1))
        self.assertFalse(self.library.eligibility(row['id'], USE, AT + timedelta(days=1))['eligible'])

    def test_scope_flags_unknown_attribution_and_complete_evidence(self):
        for key, value in [('commercial_use', 'unknown'), ('derivatives', 'prohibited'), ('attribution_required', None)]:
            item = record(key)
            item['rights'][key] = value
            with self.assertRaises(MaterialError):
                self.approve(self.add(item))
        item = record('missing_asset_proof')
        item['rights']['evidence'] = item['rights']['evidence'][1:]
        with self.assertRaises(MaterialError):
            self.add(item)

    def test_review_bindings_conditions_and_dates(self):
        row = self.add()
        for key, value in [('record_digest', '0' * 64), ('file_sha256', '0' * 64),
                           ('conditions_acknowledged', []), ('reviewed_at', stamp(AT - timedelta(days=1))),
                           ('expires_at', stamp(AT + timedelta(days=31)))]:
            bad = receipt(row)
            bad[key] = value
            with self.assertRaises(MaterialError):
                self.library.review(bad, AT)

    def test_review_and_usage_cannot_predate_local_acquisition(self):
        later = AT + timedelta(days=2)
        item = record()
        row = self.library.add(item, self.source, {'downloaded_at': stamp(later),
                               'method': 'manual_public_download', 'source_url': item['source']['page_url']}, at=later)
        with self.assertRaises(MaterialError):
            self.library.review(receipt(row), at=later)
        self.assertFalse(self.library.eligibility(row['id'], USE, AT)['eligible'])
        fixed = receipt(row)
        fixed['reviewed_at'] = stamp(later)
        self.library.review(fixed, later)
        self.assertTrue(self.library.eligibility(row['id'], USE, later)['eligible'])

    def test_empty_library_still_validates_context(self):
        with self.assertRaises(MaterialError):
            self.library.search('no match', {})

    def test_distribution_sharealike_expiry_and_human_kind(self):
        for index, (key, value) in enumerate([('allowed_distributions', ['internal']), ('share_alike_required', True),
                                             ('share_alike_required', None), ('valid_until', stamp(AT - timedelta(seconds=1)))]):
            item = record('scope_' + str(index))
            item['rights'][key] = value
            with self.assertRaises(MaterialError):
                self.approve(self.add(item))
        row = self.add()
        bad = receipt(row)
        bad['reviewer_kind'] = 'agent'
        with self.assertRaises(MaterialError):
            self.library.review(bad, AT)
        item = record('soon_expired')
        item['rights']['valid_until'] = stamp(AT + timedelta(days=1))
        row = self.add(item)
        with self.assertRaises(MaterialError):
            self.approve(row)

    def test_sql_records_are_append_only(self):
        row = self.add()
        self.approve(row)
        for sql in ["UPDATE records SET digest='changed'", 'DELETE FROM reviews', 'DELETE FROM blobs']:
            with self.assertRaises(sqlite3.IntegrityError):
                self.library.db.execute(sql)

    def test_search_plan_and_reference_validation(self):
        row = self.add()
        plan = self.library.plan(requests(), at=AT)
        self.assertFalse(plan['automatic_web_search'])
        self.assertEqual(row['id'], plan['requests'][0]['local_matches'][0]['record_id'])
        self.assertFalse(plan['requests'][0]['local_matches'][0]['eligible'])
        draft = {'scenes': [{'id': 'scene_02', 'asset_ids': ['office'], 'lines': [{'id': 'line_context'}]}]}
        validate_requests(requests(), draft)
        for field, value in [('public_nonpersonal', False)]:
            bad = requests()
            bad[field] = value
            with self.assertRaises(MaterialError):
                validate_requests(bad)
        draft['scenes'][0]['lines'] = []
        with self.assertRaises(MaterialError):
            validate_requests(requests(), draft)

    def test_handoff_credit_writer_and_real_usage(self):
        row = self.add()
        selection = {'schema': 'material_selection/v1', 'requests_digest': digest(requests()),
                     'selections': [{'request_id': 'req_office', 'record_id': row['id'], 'operator_notes': 'Context only'}]}
        with self.assertRaises(MaterialError):
            self.library.handoff(requests(), selection, self.root / 'blocked', AT)
        self.assertFalse((self.root / 'blocked').exists())
        self.approve(row)
        result = self.library.handoff(requests(), selection, self.root / 'handoff', AT)
        self.assertFalse(result['materials'][0]['product_test_evidence'])
        self.assertTrue(result['materials'][0]['requires_media_preparation'])
        self.assertEqual({}, read_json(self.root / 'handoff/editor-assets.json'))
        self.assertEqual('available_unverified', read_json(self.root / 'handoff/writer-assets.json')[0]['status'])
        self.assertIn('Fictional fixture creator', (self.root / 'handoff/attribution.txt').read_text())
        self.assertEqual([], self.library.history(row['id'])['usages'])
        self.assertTrue(self.library.verify_handoff(self.root / 'handoff/materials.json', AT)['eligible'])
        with self.assertRaises(MaterialError):
            self.library.verify_handoff(self.root / 'handoff/materials.json', AT + timedelta(days=8))
        usage = {'schema': 'material_usage/v1', 'id': 'use_1', 'record_id': row['id'], 'record_digest': row['digest'],
                 'file_sha256': row['blob_sha256'], 'project_id': 'gamma_test', 'scene_id': 'scene_02',
                 'line_ids': ['line_context'], 'use_context': USE, 'used_at': stamp(AT),
                 'operator': 'fixture operator', 'edit_reference': 'Test edit project, not real usage'}
        self.library.record_usage(usage, AT)
        self.assertEqual(1, len(self.library.history(row['id'])['usages']))
        with self.assertRaises(MaterialError):
            self.library.handoff(requests(), selection, self.root / 'handoff', AT)
        (self.root / 'handoff/attribution.txt').write_text('credit removed')
        with self.assertRaises(MaterialError):
            self.library.verify_handoff(self.root / 'handoff/materials.json', AT)

    def test_symlinks_and_duplicate_json_keys(self):
        link = self.root / 'link.png'
        link.symlink_to(self.source)
        with self.assertRaises(MaterialError):
            self.add(source=link)
        rootlink = self.root / 'rootlink'
        rootlink.symlink_to(self.root / 'library', target_is_directory=True)
        with self.assertRaises(MaterialError):
            Library(rootlink)
        bad = self.root / 'bad.json'
        bad.write_text('{"id":1,"id":2}')
        with self.assertRaises(MaterialError):
            read_json(bad)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required for video integration')
    def test_real_30fps_video_editor_precheck(self):
        video = self.root / 'test.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:r=30:d=1',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True, timeout=30)
        row = self.add(record(kind='video'), source=video)
        self.approve(row)
        req = requests('video')
        sel = {'schema': 'material_selection/v1', 'requests_digest': digest(req),
               'selections': [{'request_id': 'req_office', 'record_id': row['id'], 'operator_notes': 'Synthetic blue test'}]}
        result = self.library.handoff(req, sel, self.root / 'video-handoff', AT)
        self.assertTrue(result['materials'][0]['editor_video_candidate'])
        assets = read_json(self.root / 'video-handoff/editor-assets.json')
        self.assertIn('office', assets)
        # Check the actual existing editor contract, without launching a voice or GUI.
        from editing_agent.core import validate
        plan = {'schema': 'jianying_edit_plan/v1', 'plan_id': 'material_contract_test',
                'script': {'version': 'v1', 'lines': {'line_context': '电脑办公情境'}},
                'output': {'width': 320, 'height': 240, 'fps': 30, 'frames': 30}, 'assets': assets,
                'video': [{'id': 'clip_1', 'asset_id': 'office', 'source_in_frame': 0, 'source_out_frame': 30,
                           'timeline_in_frame': 0, 'timeline_out_frame': 30}],
                'subtitles': [{'line_id': 'line_context', 'text': '电脑办公情境', 'in_frame': 0, 'out_frame': 30}],
                'narration': {'mode': 'macos_say', 'voice': 'Samantha', 'rate': 175}, 'transitions': []}
        planpath = self.root / 'video-handoff/edit-plan.json'
        planpath.write_text(json.dumps(plan, ensure_ascii=False))
        with mock.patch('editing_agent.core.shutil.which', return_value='/usr/bin/say'):
            checked_plan, checked_assets = validate(planpath)
        self.assertEqual(30, checked_plan['output']['frames'])
        self.assertEqual(row['blob_sha256'], checked_assets['office']['sha256'])

    def test_cli_public_example_register_and_plan(self):
        base = Path(__file__).resolve().parents[1]
        command = [sys.executable, '-m', 'material_agent', '--library', str(self.root / 'cli-library')]
        registered = subprocess.run(command + ['add', '--record', 'fixtures/materials/commons-laptop.json'],
                                    cwd=base, capture_output=True, text=True, timeout=15)
        self.assertEqual(0, registered.returncode, registered.stderr)
        planned = subprocess.run(command + ['plan', '--requests', 'fixtures/materials/gamma-requests.json'],
                                 cwd=base, capture_output=True, text=True, timeout=15)
        self.assertEqual(0, planned.returncode, planned.stderr)
        self.assertFalse(json.loads(planned.stdout)['requests'][0]['local_matches'][0]['eligible'])


if __name__ == '__main__':
    unittest.main()
