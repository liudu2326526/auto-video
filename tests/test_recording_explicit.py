"""Capture-free state and identity tests; no GUI or real recording is started."""
import json
import tempfile
import unittest
from pathlib import Path
from recording_agent.core import Recorder, RecordingError, validate_spec, new_private_file


def spec(**overrides):
    return dict({'request_id': 'selection-request', 'take_id': 'selection-take', 'job_id': 'job',
                 'upstream_test_run_id': None, 'mode': 'explicit', 'duration_seconds': 5,
                 'expected_bundle_id': 'com.example.Safe', 'expected_owner_pid': 20,
                 'expected_bounds': [1800, -600, 720, 400], 'expected_display_id': 3,
                 'validation_only': True}, **overrides)


def source(**overrides):
    return dict({'mode': 'explicit', 'window_id': 42, 'owner_pid': 20, 'bundle_id': 'com.example.Safe',
                 'process_launch_epoch': 1000.125, 'bounds': [1800, -600, 720, 400], 'display_id': 3,
                 'filter': 'desktopIndependentWindow', 'audio': False, 'microphone': False,
                 'camera': False, 'include_child_windows': False}, **overrides)


class ValidationBackend:
    def __init__(self, selected=None, extra_events=None, media=False):
        self.calls = 0; self.selected = selected or source(); self.extra = extra_events or []; self.media = media

    def run(self, request, folder, lease_fd, progress):
        self.calls += 1
        events = [{'event': 'source_verified', 'source': self.selected}, *self.extra,
                  {'event': 'selection_validation_completed', 'capture_started': False},
                  {'event': 'process_exit', 'exit_code': 0}]
        with new_private_file(folder / 'backend-events.jsonl') as f: f.write('\n'.join(json.dumps(e) for e in events))
        if self.media:
            with new_private_file(folder / 'raw.partial.mp4') as f: f.write('must never accept this')
        return {'exit_code': 0, 'events': events}


class ExplicitRecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name) / 'runtime'
        self.backend = ValidationBackend()
        self.rec = Recorder(self.root, backend=self.backend, validator=lambda *a, **k: self.fail('No media inspection for source-only validation'),
                            lock_path=Path(self.temp.name) / 'lock')

    def tearDown(self): self.rec.close(); self.temp.cleanup()

    def test_source_only_is_distinct_terminal_and_replays_without_backend(self):
        result = self.rec.record(spec())
        self.assertEqual(result['state'], 'source_validated'); self.assertFalse(result['capture_started'])
        receipt = json.loads(Path(result['selection']).read_text())
        self.assertEqual(receipt['artifact_kind'], 'source_validation')
        self.assertEqual(receipt['source']['window_id'], 42)
        self.assertEqual(self.rec.record(spec()), result); self.assertEqual(self.backend.calls, 1)
        self.assertEqual(self.rec.recover('selection-take'), result)
        self.assertEqual(self.rec.stop('selection-take', 'stop'), result)
        self.assertEqual(list(self.root.rglob('*.mp4')), [])

    def test_changed_validation_target_conflicts_and_record_is_not_same_operation(self):
        self.rec.record(spec())
        for changes in ({'expected_owner_pid': 21}, {'expected_bounds': [1801, -600, 720, 400]},
                        {'validation_only': False, 'expected_window_id': 42, 'expected_owner_launch_epoch': 1000.125}):
            with self.subTest(changes=changes), self.assertRaisesRegex(RecordingError, 'REQUEST_CONFLICT'):
                self.rec.record(spec(**changes))

    def test_record_requires_prior_window_and_launch_identity(self):
        for changes in ({}, {'expected_window_id': 42}, {'expected_owner_launch_epoch': 1000.125}):
            with self.subTest(changes=changes), self.assertRaises(RecordingError):
                validate_spec(spec(validation_only=False, **changes))
        validate_spec(spec(validation_only=False, expected_window_id=42, expected_owner_launch_epoch=1000.125))

    def test_local_browser_raw_requires_explicit_opt_in_and_exact_scope(self):
        valid=spec(expected_bundle_id='com.google.Chrome',expected_window_id=42,expected_owner_launch_epoch=1000.125,
                   browser_ui_policy='local_raw_then_crop',expected_window_title='Approved Gamma')
        validate_spec(valid)
        for changes in ({'mode':'picker'},{'mode':'fixture'},{'expected_bundle_id':'com.other.App'},
                        {'expected_window_title':None},{'expected_window_title':''},{'expected_window_id':None},
                        {'expected_owner_launch_epoch':None},{'browser_ui_policy':True},{'browser_ui_policy':'disable_privacy'}):
            with self.subTest(changes=changes),self.assertRaises(RecordingError):validate_spec(dict(valid,**changes))

    def test_local_raw_policy_is_part_of_request_identity(self):
        base=spec(expected_bundle_id='com.google.Chrome',expected_window_id=42,expected_owner_launch_epoch=1000.125)
        self.rec.backend=ValidationBackend(source(bundle_id='com.google.Chrome'))
        self.rec.record(base)
        with self.assertRaisesRegex(RecordingError,'REQUEST_CONFLICT'):
            self.rec.record(dict(base,browser_ui_policy='local_raw_then_crop',expected_window_title='Approved Gamma'))

    def test_local_raw_source_must_confirm_policy_and_title(self):
        approved=spec(expected_bundle_id='com.google.Chrome',expected_window_id=42,expected_owner_launch_epoch=1000.125,
                      browser_ui_policy='local_raw_then_crop',expected_window_title='Approved Gamma')
        selected=source(bundle_id='com.google.Chrome')
        with self.assertRaises(RecordingError):Recorder._check_source(approved,selected)
        Recorder._check_source(approved,dict(selected,browser_ui_policy='local_raw_then_crop',window_title_verified=True))

    def test_invalid_or_unscoped_discovery_parameters_rejected(self):
        for changes in ({'expected_bounds': None}, {'expected_bounds': [0, 0, 1]}, {'expected_bounds': [0, 0, True, 40]},
                        {'expected_bounds': [0, 0, float('nan'), 40]}, {'expected_bounds': [0, 0, -1, 40]},
                        {'expected_display_id': True}, {'expected_display_id': 0}, {'expected_window_id': -1},
                        {'expected_window_id': 2**32}, {'expected_owner_launch_epoch': float('inf')},
                        {'validation_only': 'true'}, {'mode': 'picker'}):
            with self.subTest(changes=changes), self.assertRaises(RecordingError): validate_spec(spec(**changes))

    def test_wrong_owner_window_launch_geometry_display_and_audio_refused(self):
        for i,changes in enumerate([{'owner_pid': 21}, {'bundle_id': 'com.other.App'}, {'window_id': 43},
                                    {'process_launch_epoch': 1001}, {'bounds': [0, 0, 720, 400]}, {'display_id': 4},
                                    {'audio': True}, {'include_child_windows': True}]):
            self.rec.backend = ValidationBackend(source(**changes))
            result = self.rec.record(spec(request_id=f'req-{i}', take_id=f'take-{i}', expected_window_id=42, expected_owner_launch_epoch=1000.125))
            self.assertEqual(result['state'], 'quarantined', changes)
            self.assertFalse((self.root / f'take-take-{i}/selection.json').exists())

    def test_validation_rejects_capture_events_or_any_video_file(self):
        for i,backend in enumerate([ValidationBackend(extra_events=[{'event': 'start_intent'}]),
                                    ValidationBackend(extra_events=[{'event': 'recording_started'}]), ValidationBackend(media=True)]):
            self.rec.backend = backend
            result = self.rec.record(spec(request_id=f'req-{i}', take_id=f'take-{i}'))
            self.assertEqual(result['state'], 'quarantined')
            self.assertFalse((self.root / f'take-take-{i}/selection.json').exists())

