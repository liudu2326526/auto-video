"""Contract rejection and real-media forward tests; no desktop input is sent.

The synthetic export below exercises verification failures.  It is deliberately
not a Jianying export and can only receive a partial result without GUI evidence.
"""
import copy
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from editing_agent import core


def plan_for(source, voice):
    return {
        'schema': 'jianying_edit_plan/v1', 'plan_id': 'forward-v1',
        'script': {'version': 'script-v1', 'lines': {
            'line-1': '先看第一段。', 'line-2': '再看第二段。'}},
        'output': {'width': 320, 'height': 240, 'fps': 30, 'frames': 270},
        'assets': {
            'source': {'path': str(source), 'sha256': core.digest(source),
                       'rights': 'first_party_owned', 'rights_basis': 'Locally generated test pixels'},
            'voice': {'path': str(voice), 'sha256': core.digest(voice),
                      'rights': 'first_party_owned', 'rights_basis': 'Locally generated test tone'}},
        'video': [
            {'id': 'shot-1', 'asset_id': 'source', 'source_in_frame': 37,
             'source_out_frame': 157, 'timeline_in_frame': 0, 'timeline_out_frame': 120},
            {'id': 'shot-2', 'asset_id': 'source', 'source_in_frame': 91,
             'source_out_frame': 241, 'timeline_in_frame': 120, 'timeline_out_frame': 270}],
        'subtitles': [
            {'line_id': 'line-1', 'text': '先看第一段。', 'in_frame': 0, 'out_frame': 120},
            {'line_id': 'line-2', 'text': '再看第二段。', 'in_frame': 120, 'out_frame': 270}],
        'narration': {'mode': 'provided', 'asset_id': 'voice'},
        'transitions': [{'at_frame': 120, 'kind': 'dissolve', 'duration_frames': 12,
                         'timing': 'preserve_total'}]}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def add_alignment(plan):
    plan = copy.deepcopy(plan)
    plan['visual_alignment'] = [
        {'line_id': cue['line_id'], 'clip_id': clip['id'],
         'source_in_frame': clip['source_in_frame'], 'source_out_frame': clip['source_out_frame'],
         'visible_summary': 'Synthetic generated colour fixture, not a product claim.'}
        for cue, clip in zip(plan['subtitles'], plan['video'])]
    return plan


def approval_for(plan, plan_path, exported, evidence):
    return {
        'schema': 'jianying_alignment_review/v1', 'plan_sha256': core.digest(plan_path),
        'export_sha256': core.digest(exported), 'decision': 'approved', 'reviewer': 'operator',
        'observed': 'Synthetic unit-test review, not actual human approval',
        'details': 'Only exercises attributed per-cue contract validation.',
        'shots': [dict(line_id=c['line_id'], clip_id=m['clip_id'], text=c['text'],
                       in_frame=c['in_frame'], out_frame=c['out_frame'], verdict='matched',
                       observation='Synthetic fixture observation.', evidence_index=0)
                  for c, m in zip(plan['subtitles'], plan['visual_alignment'])],
        'evidence': [{'path': str(evidence), 'sha256': core.digest(evidence)}]}


class EditingValidationTests(unittest.TestCase):
    """Cheap rejection cases use recorded metadata, never real GUI operations."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.voice = self.root / 'source.mkv', self.root / 'voice.wav'
        self.source.write_bytes(b'fake video payload: metadata is explicitly mocked')
        self.voice.write_bytes(b'fake audio payload: metadata is explicitly mocked')
        self.plan = plan_for(self.source, self.voice)
        self.path = self.root / 'plan.json'
        self.video_info = {'streams': [{'codec_type': 'video', 'width': 320, 'height': 240,
                                       'avg_frame_rate': '30/1', 'r_frame_rate': '30/1',
                                       'nb_read_frames': '300', 'time_base': '1/1000'}],
                           'format': {'duration': '10'}}
        self.audio_info = {'streams': [{'codec_type': 'audio', 'sample_rate': '48000',
                                       'channels': 2}], 'format': {'duration': '9'}}
        self.pts = [{'best_effort_timestamp_time': str(n / 30)} for n in range(300)]

    def validate(self, plan=None):
        save(self.path, self.plan if plan is None else plan)
        def metadata(path):
            return copy.deepcopy(self.audio_info if Path(path).resolve() == self.voice.resolve() else self.video_info)
        with patch.object(core, 'probe', side_effect=metadata), \
                patch.object(core, 'run', return_value=json.dumps({'frames': self.pts})):
            return core.validate(self.path)

    def test_valid_control_is_accepted(self):
        plan, assets = self.validate()
        self.assertEqual(plan['output']['frames'], 270)
        self.assertEqual(set(assets), {'source', 'voice'})

    def test_aligned_plan_is_accepted_and_bad_mappings_rejected(self):
        aligned = add_alignment(self.plan)
        self.validate(aligned)
        for key, value in [('line_id', 'unknown'), ('clip_id', 'unknown'),
                           ('source_in_frame', 38), ('source_out_frame', 158),
                           ('source_in_frame', True), ('visible_summary', '')]:
            changed = copy.deepcopy(aligned)
            changed['visual_alignment'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(core.EditError):
                self.validate(changed)
        for rows in ([], [aligned['visual_alignment'][0]] * 2):
            changed = dict(aligned, visual_alignment=rows)
            with self.assertRaises(core.EditError):
                self.validate(changed)

    def test_alignment_rejects_a_cue_spanning_multiple_pictures(self):
        changed = add_alignment(self.plan)
        changed['subtitles'][0]['out_frame'] = 121
        changed['subtitles'][1]['in_frame'] = 121
        with self.assertRaisesRegex(core.EditError, 'crosses'):
            self.validate(changed)

    def test_schema_and_unknown_fields_are_rejected(self):
        for scope, key, value in [
                (None, 'schema', 'historic_ffmpeg/v1'),
                (None, 'renderer', 'ffmpeg'),
                ('output', 'crop_px', [0, 0, 20, 20]),
                ('narration', 'gain_db', 6)]:
            with self.subTest(scope=scope, key=key):
                changed = copy.deepcopy(self.plan)
                (changed if scope is None else changed[scope])[key] = value
                with self.assertRaises(core.EditError):
                    self.validate(changed)
        with self.assertRaises(core.EditError):
            self.validate([])

    def test_boolean_fractional_and_negative_frame_numbers_are_rejected(self):
        for key in ('source_in_frame', 'source_out_frame', 'timeline_in_frame', 'timeline_out_frame'):
            for value in (True, 37.5, -1):
                with self.subTest(key=key, value=value):
                    changed = copy.deepcopy(self.plan)
                    changed['video'][0][key] = value
                    with self.assertRaises(core.EditError):
                        self.validate(changed)
        for value in (True, 29.97, 60):
            changed = copy.deepcopy(self.plan)
            changed['output']['fps'] = value
            with self.subTest(fps=value), self.assertRaises(core.EditError):
                self.validate(changed)

    def test_source_bounds_gap_overlap_and_implicit_speed_are_rejected(self):
        changes = [
            (0, 'source_out_frame', 301),
            (0, 'source_out_frame', 37),
            (1, 'timeline_in_frame', 121),
            (1, 'timeline_in_frame', 119),
            (0, 'timeline_out_frame', 119)]
        for index, field, value in changes:
            changed = copy.deepcopy(self.plan)
            changed['video'][index][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(core.EditError):
                self.validate(changed)

    def test_subtitles_match_frozen_words_and_stay_in_bounds(self):
        for field, value in [('text', '先看第一段！'), ('out_frame', 271),
                             ('in_frame', True), ('out_frame', 0)]:
            changed = copy.deepcopy(self.plan)
            changed['subtitles'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(core.EditError):
                self.validate(changed)
        changed = copy.deepcopy(self.plan)
        changed['subtitles'][1]['line_id'] = 'line-1'
        changed['subtitles'][1]['text'] = '先看第一段。'
        with self.assertRaises(core.EditError):
            self.validate(changed)

    def test_hash_mismatch_is_rejected(self):
        self.source.write_bytes(b'different bytes after the plan was frozen')
        with self.assertRaisesRegex(core.EditError, 'hash changed'):
            self.validate()

    def test_nonlocal_and_symlink_assets_are_rejected(self):
        for path in ('https://example.com/source.mp4', 'concat:a.mp4|b.mp4'):
            changed = copy.deepcopy(self.plan)
            changed['assets']['source']['path'] = path
            with self.subTest(path=path), self.assertRaises(core.EditError):
                self.validate(changed)
        linked = self.root / 'source-link.mkv'
        linked.symlink_to(self.source)
        changed = copy.deepcopy(self.plan)
        changed['assets']['source']['path'] = str(linked)
        with self.assertRaises(core.EditError):
            self.validate(changed)

    def test_duplicate_clip_ids_and_unsafe_plan_id_are_rejected(self):
        changed = copy.deepcopy(self.plan)
        changed['video'][1]['id'] = 'shot-1'
        with self.assertRaises(core.EditError):
            self.validate(changed)
        changed = copy.deepcopy(self.plan)
        changed['plan_id'] = '../other'
        with self.assertRaises(core.EditError):
            self.validate(changed)

    def test_duplicate_json_keys_and_nonfinite_numbers_are_rejected(self):
        for raw in ('{"schema":1,"schema":2}', '{"frames":NaN}', '{"frames":Infinity}', '{bad'):
            self.path.write_text(raw)
            with self.subTest(raw=raw), self.assertRaises(core.EditError):
                core.read_json(self.path)

    def test_provided_narration_overrun_and_missing_audio_are_rejected(self):
        self.audio_info['format']['duration'] = '11'
        with self.assertRaisesRegex(core.EditError, 'narration'):
            self.validate()
        self.audio_info['format']['duration'] = '9'
        self.audio_info['streams'] = []
        with self.assertRaisesRegex(core.EditError, 'narration'):
            self.validate()

    def test_nonmonotonic_or_nonfinite_source_timestamps_are_rejected(self):
        for value in (self.pts[9]['best_effort_timestamp_time'], '-1', 'nan', 'inf'):
            self.pts[10]['best_effort_timestamp_time'] = value
            with self.subTest(value=value), self.assertRaises(core.EditError):
                self.validate()

    def test_coarse_or_nonpositive_source_timebase_is_rejected(self):
        for value in ('1/2', '0/1', '-1/1000'):
            self.video_info['streams'][0]['time_base'] = value
            with self.subTest(value=value), self.assertRaises(core.EditError):
                self.validate()

    def test_unsupported_transition_or_timing_is_rejected(self):
        for field, value in [('kind', 'random'), ('timing', 'reduce_total'),
                             ('at_frame', 119), ('duration_frames', True), ('duration_frames', 31)]:
            changed = copy.deepcopy(self.plan)
            changed['transitions'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(core.EditError):
                self.validate(changed)

    def test_existing_attempt_is_preserved(self):
        attempt = self.root / 'attempt'
        attempt.mkdir()
        sentinel = attempt / 'old-export.mp4'
        sentinel.write_bytes(b'preserve earlier work')
        with patch.object(core, 'validate', return_value=(self.plan, {})), \
                self.assertRaises(FileExistsError):
            core.prepare(self.path, attempt)
        self.assertEqual(sentinel.read_bytes(), b'preserve earlier work')
        self.assertEqual({p.name for p in attempt.iterdir()}, {'old-export.mp4'})

    def test_failed_preparation_persists_failure_and_blocks_gui_events(self):
        attempt = self.root / 'failed'
        assets = {'source': {'path': str(self.source)}}
        with patch.object(core, 'validate', return_value=(self.plan, assets)), \
                patch.object(core, 'run', side_effect=core.EditError('synthetic encoder failure')), \
                self.assertRaisesRegex(core.EditError, 'encoder failure'):
            core.prepare(self.path, attempt)
        state = core.read_json(attempt / 'state.json')
        self.assertEqual(state['status'], 'failed_preparation')
        self.assertFalse((attempt / 'import_package.json').exists())
        evidence = self.root / 'evidence.txt'
        evidence.write_text('This is a test failure receipt, not GUI evidence.')
        with self.assertRaisesRegex(core.EditError, 'failed import package'):
            core.event(attempt, 'project_created', evidence)

    def test_gui_event_rejects_unstructured_or_wrong_receipts(self):
        attempt = self.root / 'prepared'
        attempt.mkdir()
        save(attempt / 'state.json', {'status': 'prepared'})
        save(attempt / 'edit_plan.json', self.plan)
        evidence = self.root / 'receipt.json'
        good = {'event': 'project_created', 'app': 'com.lemon.lvpro', 'project_name': 'fixture',
                'observed': 'Synthetic test receipt only', 'details': 'Not a real GUI observation'}
        for key, value in [('app', 'other.editor'), ('event', 'export_completed'),
                           ('project_name', ''), ('observed', ''), ('details', '')]:
            changed = dict(good, **{key: value})
            save(evidence, changed)
            with self.subTest(key=key), self.assertRaises(core.EditError):
                core.event(attempt, 'project_created', evidence)
        save(evidence, {})
        with self.assertRaises(core.EditError):
            core.event(attempt, 'project_created', evidence)
        for name in ('export_requested', 'export_completed', 'project_reopened'):
            save(evidence, dict(good, event=name))
            with self.subTest(name=name), self.assertRaises(core.EditError):
                core.event(attempt, name, evidence)
        self.assertFalse((attempt / 'events.jsonl').exists())


class AlignmentReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.attempt = self.root / 'attempt'
        self.attempt.mkdir()
        source, voice = self.root / 'source', self.root / 'voice'
        source.write_bytes(b'generated fixture source'); voice.write_bytes(b'generated tone')
        self.plan = add_alignment(plan_for(source, voice))
        self.plan_path = self.attempt / 'edit_plan.json'
        save(self.plan_path, self.plan)
        self.export = self.root / 'synthetic-export-not-media'
        self.export.write_bytes(b'not media; tests review identity, never decoder or actual GUI')
        self.anchor = self.root / 'synthetic-evidence.txt'
        self.anchor.write_text('This is an explicit synthetic review fixture, not actual video evidence.')
        self.receipt = self.root / 'review.json'
        self.good = approval_for(self.plan, self.plan_path, self.export, self.anchor)

    def record(self, data=None, filename='review.json'):
        path = self.root / filename
        save(path, self.good if data is None else data)
        return core.review(self.attempt, self.export, path)

    def rejection(self):
        return dict(self.good, decision='rejected', reviewer='user', shots=[], evidence=[],
                    details='Synthetic reproduction of explicit user rejection: unrelated captions.')

    def test_per_cue_review_is_hash_bound_and_idempotent(self):
        self.assertEqual(self.record()['decision'], 'approved')
        self.assertEqual(self.record()['decision'], 'approved')
        self.assertEqual(len((self.attempt / 'alignment-reviews.jsonl').read_text().splitlines()), 1)

    def test_missing_or_mismatched_cue_cannot_grant_approval(self):
        for key, value in [('text', 'A different claim'), ('clip_id', 'wrong'),
                           ('in_frame', True), ('out_frame', 121), ('verdict', 'not_reviewed'),
                           ('evidence_index', True), ('observation', '')]:
            changed = copy.deepcopy(self.good)
            changed['shots'][0][key] = value
            with self.subTest(key=key), self.assertRaises(core.EditError):
                self.record(changed)
        for shots in ([], [self.good['shots'][0]] * 2):
            with self.assertRaises(core.EditError):
                self.record(dict(self.good, shots=shots))

    def test_review_for_other_plan_or_export_is_rejected_before_append(self):
        for key in ('plan_sha256', 'export_sha256'):
            with self.subTest(key=key), self.assertRaisesRegex(core.EditError, 'bind'):
                self.record(dict(self.good, **{key: '0' * 64}))
        self.assertFalse((self.attempt / 'alignment-reviews.jsonl').exists())

    def test_changed_receipt_or_anchor_fails_closed(self):
        self.record()
        self.anchor.write_text('changed evidence')
        with self.assertRaisesRegex(core.EditError, 'evidence changed'):
            core.review_status(self.attempt, self.plan, self.export)
        self.anchor.write_text('This is an explicit synthetic review fixture, not actual video evidence.')
        self.receipt.write_text('{}')
        with self.assertRaisesRegex(core.EditError, 'receipt changed'):
            core.review_status(self.attempt, self.plan, self.export)

    def test_user_rejection_dominates_earlier_approval_and_operator_cannot_clear_it(self):
        self.record()
        self.record(self.rejection(), 'reject.json')
        self.assertEqual(core.review_status(self.attempt, self.plan, self.export)['decision'], 'rejected')
        with self.assertRaisesRegex(core.EditError, '[Cc]annot clear'):
            self.record(filename='operator-reapproval.json')

    def test_declared_user_role_cannot_override_rejected_bytes(self):
        self.record(self.rejection(), 'reject.json')
        with self.assertRaisesRegex(core.EditError, '[Cc]annot clear'):
            self.record(dict(self.good, reviewer='user'), 'claimed-user-reapproval.json')
        self.assertEqual(core.review_status(self.attempt, self.plan, self.export)['decision'], 'rejected')

    def test_new_export_does_not_reuse_old_approval(self):
        self.record()
        other = self.root / 'different-export'
        other.write_bytes(b'different output requires new review')
        self.assertEqual(core.review_status(self.attempt, self.plan, other)['decision'], 'not_reviewed')

    def test_plan_reformatting_does_not_erase_rejection_of_identical_media(self):
        self.record(self.rejection(), 'reject.json')
        self.plan_path.write_text(json.dumps(self.plan, indent=4))
        self.good['plan_sha256'] = core.digest(self.plan_path)
        self.assertEqual(core.review_status(self.attempt, self.plan, self.export)['decision'], 'rejected')
        with self.assertRaisesRegex(core.EditError, '[Cc]annot clear'):
            self.record(filename='new-plan-same-rejected-video.json')

    def test_legacy_plan_can_record_rejection_but_cannot_gain_semantic_approval(self):
        del self.plan['visual_alignment']
        save(self.plan_path, self.plan)
        self.good['plan_sha256'] = core.digest(self.plan_path)
        with self.assertRaisesRegex(core.EditError, 'every mapped cue'):
            self.record()
        self.assertEqual(self.record(self.rejection(), 'legacy-rejected.json')['decision'], 'rejected')


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'existing ffmpeg/ffprobe required')
class EditingRealMediaTests(unittest.TestCase):
    """Independent pixel comparison checks selected source frames, not just counts."""

    @classmethod
    def command(cls, args):
        return subprocess.run(args, check=True, capture_output=True, timeout=120).stdout

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.source, cls.voice = cls.root / 'source.mkv', cls.root / 'voice.wav'
        # A different uniform YUV colour encodes every source frame ordinal.
        cls.command(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                     "nullsrc=s=320x240:r=30:d=10,geq=lum='16+mod(N,220)':cb='16+mod(13*N,225)':cr='16+mod(37*N,225)'",
                     '-c:v', 'ffv1', '-pix_fmt', 'yuv420p', '-threads', '1', str(cls.source)])
        cls.command(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                     'sine=frequency=440:sample_rate=48000:duration=9',
                     '-ac', '2', '-c:a', 'pcm_s16le', str(cls.voice)])
        cls.plan = plan_for(cls.source, cls.voice)
        cls.plan_path = cls.root / 'plan.json'
        save(cls.plan_path, cls.plan)
        cls.attempt = cls.root / 'real-preparation'
        cls.package = core.prepare(cls.plan_path, cls.attempt)
        cls.synthetic_export = cls.root / 'synthetic-export-not-jianying.mp4'
        cls.command(['ffmpeg', '-nostdin', '-v', 'error',
                     '-i', cls.package['clips'][0]['path'], '-i', cls.package['clips'][1]['path'],
                     '-i', cls.package['narration'], '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]',
                     '-map', '[v]', '-map', '2:a:0', '-c:v', 'libx264', '-preset', 'veryfast',
                     '-crf', '18', '-c:a', 'aac', '-r', '30', str(cls.synthetic_export)])
        cls.silent_export = cls.root / 'no-audio.mp4'
        cls.command(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(cls.synthetic_export),
                     '-map', '0:v:0', '-c', 'copy', str(cls.silent_export)])
        # A fixture directory allows media-verifier testing; it is not GUI proof.
        cls.project = cls.root / 'synthetic-project-not-a-real-jianying-project'
        cls.project.mkdir()
        save(cls.project / 'draft_content.json', {'fixture_only': True})

    def verification_attempt(self, name):
        attempt = self.root / name
        attempt.mkdir()
        save(attempt / 'edit_plan.json', self.plan)
        save(attempt / 'state.json', {'status': 'prepared', 'plan_id': self.plan['plan_id']})
        return attempt

    def pixels(self, path, frame):
        return self.command(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path),
                             '-vf', f'select=eq(n\\,{frame})', '-frames:v', '1',
                             '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])

    def record_fixture_receipt(self, attempt, name, **values):
        receipt = {'event': name, 'app': 'com.lemon.lvpro', 'project_name': 'test-project',
                   'observed': 'Synthetic test receipt; not real GUI evidence',
                   'details': 'Exercises verifier rejection paths only'}
        receipt.update(values)
        previous = attempt / 'events.jsonl'
        index = len(previous.read_text().splitlines()) if previous.exists() else 0
        path = attempt / (f'{index:02}-' + name + '.json')
        save(path, receipt)
        core.event(attempt, name, path)

    def test_actual_prepare_selects_exact_source_frames_and_keeps_audio_subtitles(self):
        self.assertFalse(self.package['final_video_rendered'])
        self.assertEqual(core.read_json(self.attempt / 'state.json')['status'], 'prepared')
        self.assertEqual(self.package['native_transitions'], self.plan['transitions'])
        for item, length, first, last in zip(self.package['clips'], (120, 150), (37, 91), (156, 240)):
            info = core.probe(item['path'])
            video = [s for s in info['streams'] if s['codec_type'] == 'video']
            self.assertEqual(len(info['streams']), 1, 'prepared clips must strip unrequested source audio')
            self.assertEqual((int(video[0]['nb_read_frames']), video[0]['avg_frame_rate']), (length, '30/1'))
            self.assertEqual((video[0]['width'], video[0]['height']), (320, 240))
            self.assertEqual(core.digest(item['path']), item['sha256'])
            for actual_frame, source_frame in ((0, first), (length - 1, last)):
                actual = self.pixels(item['path'], actual_frame)
                expected = self.pixels(self.source, source_frame)
                neighbour = self.pixels(self.source, source_frame + 1)
                self.assertEqual(len(actual), 320 * 240 * 3)
                mae = sum(abs(a - b) for a, b in zip(actual, expected)) / len(actual)
                wrong_mae = sum(abs(a - b) for a, b in zip(actual, neighbour)) / len(actual)
                self.assertLess(mae, 3, f'wrong source frame {source_frame}: MAE={mae}')
                self.assertGreater(wrong_mae, mae + 5, 'fixture must distinguish a one-frame selection error')
        expected_srt = ('1\n00:00:00,000 --> 00:00:04,000\n先看第一段。\n\n'
                        '2\n00:00:04,000 --> 00:00:09,000\n再看第二段。\n')
        self.assertEqual((self.attempt / 'subtitles.srt').read_text(encoding='utf-8-sig'), expected_srt)
        with wave.open(self.package['narration'], 'rb') as audio:
            self.assertEqual((audio.getframerate(), audio.getnchannels(), audio.getnframes()), (48000, 2, 432000))
            self.assertTrue(any(audio.readframes(4800)), 'prepared audio must contain the provided signal')
        self.assertEqual(core.digest(self.package['narration']), self.package['narration_sha256'])

    def test_good_media_without_gui_evidence_is_only_partial(self):
        attempt = self.verification_attempt('missing-gui')
        report = core.verify(attempt, self.synthetic_export, self.project)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(set(report['missing_gui_reviews']), set(core.GUI_EVENTS) |
                         {'visual_alignment_plan', 'shot_alignment_review'})
        self.assertTrue(report['video']['full_decode_passed'])
        self.assertLess(report['video']['audio_max_dbfs'], 0)

    def test_export_missing_audio_is_rejected_without_success_report(self):
        attempt = self.verification_attempt('missing-audio')
        with self.assertRaisesRegex(core.EditError, 'stream counts'):
            core.verify(attempt, self.silent_export, self.project)
        self.assertFalse((attempt / 'verification.json').exists())

    def test_changed_evidence_is_rejected(self):
        attempt = self.verification_attempt('changed-evidence')
        evidence = attempt / 'observed.txt'
        evidence.write_text('Original test observation')
        recorded = {'event': 'export_completed', 'evidence': str(evidence), 'sha256': core.digest(evidence)}
        (attempt / 'events.jsonl').write_text(json.dumps(recorded) + '\n')
        evidence.write_text('Changed after recording')
        with self.assertRaisesRegex(core.EditError, 'evidence changed'):
            core.verify(attempt, self.synthetic_export, self.project)
        self.assertFalse((attempt / 'verification.json').exists())

    def test_recorded_export_path_must_match_actual_video(self):
        attempt = self.verification_attempt('wrong-export-target')
        wrong = str(self.root / 'some-other-video.mp4')
        self.record_fixture_receipt(attempt, 'export_requested', export_path=wrong)
        self.record_fixture_receipt(attempt, 'export_completed', export_path=wrong)
        with self.assertRaisesRegex(core.EditError, 'Export path differs'):
            core.verify(attempt, self.synthetic_export, self.project)
        self.assertFalse((attempt / 'verification.json').exists())

    def test_recorded_project_path_must_match_actual_native_directory(self):
        attempt = self.verification_attempt('wrong-project-target')
        self.record_fixture_receipt(attempt, 'project_reopened', project_path=str(self.root / 'other-project'))
        with self.assertRaisesRegex(core.EditError, 'Native project differs'):
            core.verify(attempt, self.synthetic_export, self.project)
        self.assertFalse((attempt / 'verification.json').exists())

    def test_export_completion_without_prior_request_is_rejected(self):
        attempt = self.verification_attempt('completion-without-request')
        self.record_fixture_receipt(attempt, 'export_completed', export_path=str(self.synthetic_export))
        with self.assertRaisesRegex(core.EditError, 'no preceding request'):
            core.verify(attempt, self.synthetic_export, self.project)
        self.assertFalse((attempt / 'verification.json').exists())

    def test_gui_receipts_for_different_projects_are_rejected(self):
        attempt = self.verification_attempt('mixed-projects')
        self.record_fixture_receipt(attempt, 'project_created')
        self.record_fixture_receipt(attempt, 'clips_on_timeline', project_name='different-project')
        with self.assertRaisesRegex(core.EditError, 'different projects'):
            core.verify(attempt, self.synthetic_export, self.project)
        self.assertFalse((attempt / 'verification.json').exists())

    def test_latest_unresolved_gui_state_cannot_reuse_older_success(self):
        for last_event in ('needs_input', 'export_unknown', 'export_requested'):
            attempt = self.verification_attempt('unresolved-' + last_event)
            for name in core.GUI_EVENTS:
                values = {}
                if name in ('export_requested', 'export_completed'):
                    values['export_path'] = str(self.synthetic_export)
                if name == 'project_reopened':
                    values['project_path'] = str(self.project)
                self.record_fixture_receipt(attempt, name, **values)
            # All earlier receipts are synthetic but internally valid. The new
            # unresolved action must prevent reusing that older success state.
            values = {'export_path': str(self.synthetic_export)} if last_event == 'export_requested' else {}
            self.record_fixture_receipt(attempt, last_event, **values)
            with self.subTest(last_event=last_event):
                try:
                    result = core.verify(attempt, self.synthetic_export, self.project)
                except core.EditError:
                    pass
                else:
                    self.assertNotEqual(result['status'], 'verified_with_recorded_gui_review',
                                        'later unresolved GUI state cannot reuse an old export success')

    def test_explicit_resolution_can_clear_latest_gui_blocker(self):
        for blocked, resolved in (('needs_input', 'blocker_resolved'),
                                  ('export_unknown', 'export_completed'),
                                  ('export_requested', 'export_completed')):
            attempt = self.verification_attempt('resolved-' + blocked)
            plan = add_alignment(self.plan)
            save(attempt / 'edit_plan.json', plan)
            anchor = attempt / 'review-evidence.txt'
            anchor.write_text('Synthetic contract review, not actual GUI evidence.')
            receipt = attempt / 'alignment-review.json'
            save(receipt, approval_for(plan, attempt / 'edit_plan.json', self.synthetic_export, anchor))
            core.review(attempt, self.synthetic_export, receipt)
            for name in core.GUI_EVENTS:
                values = {}
                if name in ('export_requested', 'export_completed'):
                    values['export_path'] = str(self.synthetic_export)
                if name == 'project_reopened':
                    values['project_path'] = str(self.project)
                self.record_fixture_receipt(attempt, name, **values)
            values = {'export_path': str(self.synthetic_export)} if blocked == 'export_requested' else {}
            self.record_fixture_receipt(attempt, blocked, **values)
            values = {'export_path': str(self.synthetic_export)} if resolved == 'export_completed' else {}
            self.record_fixture_receipt(attempt, resolved, **values)
            with self.subTest(blocked=blocked, resolved=resolved):
                result = core.verify(attempt, self.synthetic_export, self.project)
                self.assertEqual(result['status'], 'verified_with_recorded_gui_review')
                self.assertEqual(result['missing_gui_reviews'], [])

    def test_generic_gui_review_alone_does_not_prove_semantic_alignment(self):
        attempt = self.verification_attempt('all-gui-no-semantic-review')
        for name in core.GUI_EVENTS:
            values = {}
            if name in ('export_requested', 'export_completed'):
                values['export_path'] = str(self.synthetic_export)
            if name == 'project_reopened':
                values['project_path'] = str(self.project)
            self.record_fixture_receipt(attempt, name, **values)
        report = core.verify(attempt, self.synthetic_export, self.project)
        self.assertEqual(report['status'], 'partial')
        self.assertIn('visual_alignment_plan', report['missing_gui_reviews'])
        rejection = {'schema': 'jianying_alignment_review/v1',
                     'plan_sha256': core.digest(attempt / 'edit_plan.json'),
                     'export_sha256': core.digest(self.synthetic_export), 'decision': 'rejected',
                     'reviewer': 'user', 'observed': 'Synthetic explicit rejection fixture',
                     'details': 'Captions unrelated to picture, despite all generic GUI receipts.',
                     'shots': [], 'evidence': []}
        path = attempt / 'rejection.json'; save(path, rejection)
        core.review(attempt, self.synthetic_export, path)
        self.assertEqual(core.verify(attempt, self.synthetic_export, self.project)['status'], 'rejected')


if __name__ == '__main__':
    unittest.main()
