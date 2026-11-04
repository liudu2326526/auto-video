import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from creative_agent import core


class CreativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base_path = self.root / 'base.json'; self.base_path.write_text('{}')
        self.ev = self.root / 'evidence.txt'; self.ev.write_text('Observed synthetic fixture, not a product test')
        self.exp = self.root / 'export.mp4'; self.exp.write_bytes(b'mocked media')
        self.plan_path = self.root / 'plan.json'; self.rp = self.root / 'review.json'
        self.base = {'output': {'width': 1080, 'height': 1920, 'fps': 30, 'frames': 120},
                     'video': [{'id': 'one', 'timeline_in_frame': 0, 'timeline_out_frame': 120}],
                     'subtitles': [{'text': '真实画面。', 'line_id': 'line', 'in_frame': 0, 'out_frame': 120}],
                     'script': {'lines': {'line': '真实画面。'}}}
        self.plan = {'schema': 'creative_review_plan/v1', 'id': 'fixture',
                     'edit_plan': self.bound(self.base_path), 'required_gates': sorted(core.GATES),
                     'references': [{'id': 'ref', 'url': 'https://example.org/reference',
                                     'observation': 'Synthetic reference fixture', 'evidence': [self.bound(self.ev)]}],
                     'shots': [{'clip_id': 'one', 'audience_question': 'What changed?', 'one_focus': 'One visible fact',
                                'narration': ['真实画面。'], 'caption': ['真实画面。'], 'source_note': 'Source frames',
                                'source_evidence': [{'file': self.bound(self.exp), 'in_frame': 0, 'out_frame': 120,
                                                     'fps': 30, 'observation': 'Synthetic source range'}],
                                'reference_ids': ['ref'], 'change': 'Before→after', 'emotion': 'Curiosity',
                                'transition_purpose': 'Next factual comparison', 'missing_material_action': 'Request capture',
                                'accents': [{'id': 'arrow', 'kind': 'sticker', 'purpose': 'Direct attention',
                                             'resource': 'Own arrow fixture', 'rights_basis': 'Own geometry',
                                             'in_frame': 0, 'out_frame': 60, 'evidence_visibility': 'Outside evidence text'}]}]}
        self.save(self.plan_path, self.plan)
        self.receipt = {'schema': 'creative_export_review/v1', 'plan_sha256': core.digest(self.plan_path),
                        'export_sha256': core.digest(self.exp), 'reviewer': 'operator', 'observed': 'Synthetic fixture review',
                        'continuous_visual_review': True, 'audio_listened': True,
                        'shots': [{'clip_id': 'one', 'verdict': 'matched', 'reference_comparison': 'Fixture comparison',
                                   'observed_gap': 'None observed in fixture', 'revision_result': 'Checked fixture',
                                   'evidence': [self.bound(self.ev)]}],
                        'gates': [{'id': g, 'verdict': 'passed', 'observation': 'Synthetic gate observation',
                                   'evidence': [self.bound(self.ev)]} for g in sorted(core.GATES)],
                        'resources': [{'id': 'arrow', 'status': 'applied', 'observed_resource': 'Own fixture arrow',
                                       'rights_observation': 'Own fixture', 'evidence': [self.bound(self.ev)]}]}
        self.patcher = patch.object(core, 'edit_validate', return_value=(self.base, {})); self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.media = {'streams': [{'codec_type': 'video', 'width': 1080, 'height': 1920,
                                  'avg_frame_rate': '30/1', 'r_frame_rate': '30/1', 'nb_read_frames': '120'}, {'codec_type': 'audio'}]}
        self.probe_patcher = patch.object(core, 'probe', side_effect=lambda _: copy.deepcopy(self.media))
        self.probe_patcher.start(); self.addCleanup(self.probe_patcher.stop)

    def bound(self, p): return {'path': str(p), 'sha256': core.digest(p)}
    def save(self, p, d): p.write_text(json.dumps(d, ensure_ascii=False))
    def audit(self, r=None):
        self.save(self.rp, self.receipt if r is None else r)
        with patch.object(core, 'probe', return_value=self.media):
            return core.audit(self.plan_path, self.exp, self.rp)

    def test_complete_attributed_review_is_not_automatic_aesthetic_judgment(self):
        result = self.audit()
        self.assertEqual(result['status'], 'review_complete')
        self.assertFalse(result['automatic_aesthetic_judgment'])

    def test_hard_failure_cannot_be_offset_by_other_passes(self):
        for gate in core.GATES:
            with self.subTest(gate=gate):
                r = copy.deepcopy(self.receipt)
                next(g for g in r['gates'] if g['id'] == gate)['verdict'] = 'failed'
                self.assertEqual(self.audit(r)['status'], 'needs_revision')

    def test_missing_listening_and_continuous_viewing_remain_pending(self):
        for key in ('audio_listened', 'continuous_visual_review'):
            r = dict(self.receipt, **{key: False})
            self.assertEqual(self.audit(r)['status'], 'needs_review')

    def test_changed_export_or_plan_rejects_old_review(self):
        for key in ('export_sha256', 'plan_sha256'):
            with self.subTest(key=key), self.assertRaises(core.EditError):
                self.audit(dict(self.receipt, **{key: 'a' * 64}))

    def test_every_shot_gate_and_accent_must_be_reviewed(self):
        for key in ('shots', 'gates', 'resources'):
            with self.subTest(key=key), self.assertRaises(core.EditError): self.audit(dict(self.receipt, **{key: []}))

    def test_missing_uncleared_resource_does_not_pass(self):
        for status in ('missing', 'uncleared'):
            r = copy.deepcopy(self.receipt); r['resources'][0]['status'] = status
            self.assertEqual(self.audit(r)['status'], 'needs_review')

    def test_unknown_schema_fields_and_scores_rejected(self):
        for changed in (dict(self.plan, score=100), dict(self.plan, schema='creative_review_plan/v2')):
            self.save(self.plan_path, changed)
            with self.assertRaises(core.EditError): core.validate(self.plan_path)

    def test_duplicate_missing_and_unknown_shots_rejected(self):
        for rows in ([], self.plan['shots'] * 2, [dict(self.plan['shots'][0], clip_id='other')]):
            self.save(self.plan_path, dict(self.plan, shots=rows))
            with self.assertRaises(core.EditError): core.validate(self.plan_path)

    def test_caption_narration_and_reference_mismatch_rejected(self):
        for key, value in [('caption', ['Changed']), ('narration', ['Changed']), ('reference_ids', ['unknown'])]:
            p = copy.deepcopy(self.plan); p['shots'][0][key] = value; self.save(self.plan_path, p)
            with self.subTest(key=key), self.assertRaises(core.EditError): core.validate(self.plan_path)

    def test_out_of_shot_bool_reversed_and_unsupported_accent_rejected(self):
        for key, value in [('in_frame', True), ('out_frame', 121), ('in_frame', 60), ('kind', 'unknown')]:
            p = copy.deepcopy(self.plan); p['shots'][0]['accents'][0][key] = value; self.save(self.plan_path, p)
            with self.subTest(key=key), self.assertRaises(core.EditError): core.validate(self.plan_path)

    def test_mutated_or_symlink_evidence_rejected(self):
        self.ev.write_text('mutated')
        with self.assertRaises(core.EditError): core.validate(self.plan_path)
        link = self.root / 'link.txt'; link.symlink_to(self.ev)
        with self.assertRaises(core.EditError): core.bound_file({'path': str(link), 'sha256': core.digest(self.ev)}, self.root)

    def test_media_mismatch_not_hidden_by_human_receipt(self):
        self.media['streams'][0]['nb_read_frames'] = '119'
        with self.assertRaises(core.EditError): self.audit()

    def test_average_rate_alone_does_not_prove_cfr(self):
        self.media['streams'][0]['r_frame_rate'] = '60/1'
        with self.assertRaises(core.EditError): self.audit()

    def test_original_source_range_required_and_bounded(self):
        for key, value in [('out_frame', 121), ('in_frame', True), ('fps', 29), ('in_frame', 120)]:
            p = copy.deepcopy(self.plan); p['shots'][0]['source_evidence'][0][key] = value
            self.save(self.plan_path, p)
            with self.subTest(key=key), self.assertRaises(core.EditError): core.validate(self.plan_path)
        p = copy.deepcopy(self.plan); p['shots'][0]['source_evidence'] = []
        self.save(self.plan_path, p)
        with self.assertRaises(core.EditError): core.validate(self.plan_path)

    def test_reports_never_overwrite_previous_iteration_or_symlink(self):
        output = self.root / 'report.json'; core.save_new(output, {'status': 'needs_revision'})
        with self.assertRaises(FileExistsError): core.save_new(output, {'status': 'review_complete'})
        link = self.root / 'link.json'; link.symlink_to(output)
        with self.assertRaises(FileExistsError): core.save_new(link, {})
        self.assertEqual(json.loads(output.read_text())['status'], 'needs_revision')


if __name__ == '__main__': unittest.main()
