import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from recording_agent.core import Recorder, RecordingError, DesktopLock, REPO, validate_spec, root_path, new_private_file, atomic_json
from recording_agent.media import inspect_media, MediaError


def spec(**overrides):
    return dict({'request_id': 'request-1', 'take_id': 'take-1', 'job_id': 'job-1',
                 'upstream_test_run_id': None, 'mode': 'fixture', 'duration_seconds': 7,
                 'expected_bundle_id': None, 'expected_owner_pid': None}, **overrides)


def good_media(*args, **kwargs):
    return {'sha256': 'a' * 64, 'duration_seconds': args[1], 'audio_streams': 0,
            'presentation_timestamps_seconds': [0, 0.1], 'width': 100, 'height': 100}


class FakeBackend:
    def __init__(self, events=None, exit_code=0, source=None):
        self.calls = 0
        self.events = events
        self.exit_code = exit_code
        self.source = source or {'mode': 'fixture', 'window_id': 100, 'owner_pid': 200}

    def run(self, request, folder, lease_fd, progress):
        self.calls += 1
        with new_private_file(folder / 'raw.partial.mp4') as f: f.write('fixture-not-a-real-video')
        events = self.events if self.events is not None else [
            {'event': 'source_verified', 'source': self.source}, {'event': 'recording_started'},
            {'event': 'recording_finished', 'clean': True}, {'event': 'process_exit', 'exit_code': 0}]
        with new_private_file(folder / 'backend-events.jsonl') as f: f.write('\n'.join(json.dumps(e) for e in events) + '\n')
        progress(request['take_id'], events)
        return {'exit_code': self.exit_code, 'events': events}


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'runtime'
        self.lock = Path(self.temp.name) / 'desktop.lock'
        self.backend = FakeBackend()
        self.rec = Recorder(self.root, backend=self.backend, validator=good_media, lock_path=self.lock)

    def tearDown(self):
        self.rec.close(); self.temp.cleanup()

    def test_record_replay_is_noop_and_does_not_claim_tool_success(self):
        a = self.rec.record(spec())
        b = self.rec.record(spec())
        self.assertEqual(a, b); self.assertEqual(self.backend.calls, 1)
        self.assertEqual(a['state'], 'validated')
        manifest = json.loads(Path(a['manifest']).read_text())
        self.assertEqual(manifest['tool_test_status'], 'not_performed')
        self.assertEqual(manifest['timing']['pts_mapping_quality'], 'callback_anchor_only_not_frame_accurate')

    def test_same_request_different_parameters_rejected(self):
        self.rec.record(spec())
        with self.assertRaisesRegex(RecordingError, 'REQUEST_CONFLICT'):
            self.rec.record(spec(duration_seconds=8))

    def test_same_take_different_request_rejected(self):
        self.rec.record(spec())
        with self.assertRaisesRegex(RecordingError, 'TAKE_ALREADY_RESERVED'):
            self.rec.record(spec(request_id='request-2'))

    def test_bad_ids_and_durations_rejected_before_backend(self):
        for value in ('../escape', '.', '', '/absolute', '含隐私'):
            with self.subTest(value=value), self.assertRaises(RecordingError):
                self.rec.record(spec(take_id=value))
        for value in (0, 31, float('nan'), float('inf'), True):
            with self.subTest(value=value), self.assertRaises(RecordingError):
                self.rec.record(spec(duration_seconds=value))
        self.assertEqual(self.backend.calls, 0)

    def test_no_sound_or_unknown_parameter_opt_in(self):
        s = spec(); s['audio'] = True
        with self.assertRaises(RecordingError): self.rec.record(s)

    def test_picker_needs_explicit_owner(self):
        for s in (spec(mode='picker'), spec(mode='picker', expected_bundle_id='com.example.Tool'),
                  spec(mode='fixture', expected_owner_pid=12)):
            with self.assertRaises(RecordingError): validate_spec(s)

    def test_runtime_inside_repository_rejected(self):
        with self.assertRaisesRegex(RecordingError, 'outside'):
            root_path(REPO / 'var' / 'should-not-be-created')

    def test_runtime_inside_another_git_repository_rejected(self):
        other = Path(self.temp.name) / 'another-repo'; other.mkdir(); (other / '.git').mkdir()
        with self.assertRaisesRegex(RecordingError, 'outside'):
            root_path(other / 'private-captures')

    def test_existing_public_roots_refused_without_chmod(self):
        for mode in (0o755, 0o777):
            path = Path(self.temp.name) / ('public-' + str(mode)); path.mkdir(); path.chmod(mode)
            with self.assertRaisesRegex(RecordingError, 'private'):
                root_path(path)
            self.assertEqual(path.stat().st_mode & 0o777, mode)
        private = Path(self.temp.name) / 'private'; private.mkdir(mode=0o700)
        self.assertEqual(root_path(private), private.resolve())

    def test_private_state_files_and_database_permission_refusal(self):
        for path in self.rec.store.private.iterdir():
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        self.rec.close()
        db = self.root / '.recording-state/state.sqlite'; db.chmod(0o644)
        with self.assertRaisesRegex(RecordingError, 'private'):
            Recorder(self.root, lock_path=self.lock)
        self.assertEqual(db.stat().st_mode & 0o777, 0o644)

    def test_internal_state_directory_symlink_refused(self):
        other_root = Path(self.temp.name) / 'other-root'; other_root.mkdir(mode=0o700)
        outside = Path(self.temp.name) / 'outside'; outside.mkdir(mode=0o700)
        (other_root / '.recording-state').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(RecordingError): Recorder(other_root, lock_path=self.lock)
        self.assertEqual(list(outside.iterdir()), [])

    def test_atomic_json_is_private_create_only(self):
        path = self.root / 'control.json'; atomic_json(path, {'first': True})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError): atomic_json(path, {'overwrite': True})
        self.assertEqual(json.loads(path.read_text()), {'first': True})
        link = self.root / 'linked.json'; link.symlink_to(self.root / 'missing')
        with self.assertRaises(FileExistsError): atomic_json(link, {'overwrite': True})
        self.assertTrue(link.is_symlink())

    def test_symlink_root_and_take_rejected(self):
        link = Path(self.temp.name) / 'linked'; link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(RecordingError, 'symlink'): root_path(link)
        target = Path(self.temp.name) / 'other'; target.mkdir()
        (self.root / 'take-take-1').symlink_to(target, target_is_directory=True)
        with self.assertRaises(RecordingError): self.rec.record(spec())
        self.assertEqual(list(target.iterdir()), [])

    def test_existing_folder_is_not_overwritten(self):
        folder = self.root / 'take-take-1'; folder.mkdir(mode=0o700)
        (folder / 'raw.mp4').write_bytes(b'preserve')
        with self.assertRaises(FileExistsError): self.rec.record(spec())
        self.assertEqual((folder / 'raw.mp4').read_bytes(), b'preserve')

    def test_permission_refusal_is_not_success_and_no_retry(self):
        self.rec.backend = FakeBackend(events=[{'event': 'blocked_or_failed', 'reason': 'screen_capture_permission_denied'}], exit_code=2)
        a = self.rec.record(spec()); b = self.rec.record(spec())
        self.assertEqual(a['state'], 'quarantined'); self.assertEqual(a, b)
        self.assertEqual(self.rec.backend.calls, 1)

    def test_pid_exit_and_file_alone_do_not_prove_success(self):
        self.rec.backend = FakeBackend(events=[{'event': 'process_exit', 'exit_code': 0}])
        self.assertEqual(self.rec.record(spec())['state'], 'unknown')

    def test_interference_quarantines_even_with_finish_callback(self):
        self.rec.backend = FakeBackend(events=[{'event': 'interference', 'reason': 'wrong_foreground'},
                                              {'event': 'recording_finished', 'clean': False}])
        self.assertEqual(self.rec.record(spec())['state'], 'quarantined')

    def test_selected_wrong_owner_is_quarantined(self):
        self.rec.backend = FakeBackend(source={'mode': 'picker', 'window_id': 1, 'owner_pid': 99, 'bundle_id': 'com.wrong.Tool'})
        out = self.rec.record(spec(mode='picker', expected_owner_pid=33, expected_bundle_id='com.example.Tool'))
        self.assertEqual(out['state'], 'quarantined')
        self.assertFalse((self.root / 'take-take-1/raw.mp4').exists())

    def test_media_failure_preserves_partial_and_replays_failure(self):
        def fail(*args, **kwargs): raise MediaError('audio present')
        self.rec.validator = fail
        a = self.rec.record(spec())
        self.assertEqual(a['state'], 'quarantined')
        self.assertTrue((self.root / 'take-take-1/raw.partial.mp4').exists())
        self.assertEqual(self.rec.record(spec()), a)

    def test_clean_finish_too_short_is_quarantined(self):
        def short(*args, **kwargs): return dict(good_media(*args, **kwargs), duration_seconds=0.1)
        self.rec.validator = short
        result = self.rec.record(spec())
        self.assertEqual(result['state'], 'quarantined')
        self.assertFalse((self.root / 'take-take-1/raw.mp4').exists())

    def test_explicit_acknowledged_stop_publishes_partial_not_complete(self):
        def short(*args, **kwargs): return dict(good_media(*args, **kwargs), duration_seconds=0.1)
        self.rec.validator = short
        original = self.backend.run
        def run(*args):
            result = original(*args)
            self.rec.stop('take-1', 'stop-early')
            result['events'].insert(-2, {'event': 'stop_intent', 'reason': 'requested'})
            return result
        self.backend.run = run
        result = self.rec.record(spec())
        self.assertEqual(result['state'], 'partial')
        self.assertFalse(result['plan_complete'])
        self.assertEqual(result['plan_completion'], 'stopped_early')
        manifest = json.loads(Path(result['manifest']).read_text())
        self.assertTrue(manifest['plan']['explicit_stop_acknowledged'])
        self.assertEqual(self.rec.record(spec()), result)

    def test_backend_stop_reason_without_durable_stop_request_is_not_enough(self):
        self.rec.validator = lambda *a, **k: dict(good_media(*a, **k), duration_seconds=0.1)
        self.backend.events = [{'event': 'source_verified', 'source': self.backend.source},
                               {'event': 'stop_intent', 'reason': 'requested'},
                               {'event': 'recording_finished', 'clean': True}]
        self.assertEqual(self.rec.record(spec())['state'], 'quarantined')

    def test_existing_different_final_file_is_preserved(self):
        backend = self.backend
        original = backend.run
        def run(*args):
            result = original(*args)
            with new_private_file(args[1] / 'raw.mp4') as f: f.write('preserve-unrelated-file')
            return result
        backend.run = run
        self.assertEqual(self.rec.record(spec())['state'], 'quarantined')
        self.assertEqual((self.root / 'take-take-1/raw.mp4').read_bytes(), b'preserve-unrelated-file')

    def test_recover_publication_before_state_commit(self):
        original = self.rec.store.put
        def crash_on_success(take):
            if take['state'] == 'validated': raise RuntimeError('simulated state commit crash')
            original(take)
        with patch.object(self.rec.store, 'put', side_effect=crash_on_success):
            self.assertEqual(self.rec.record(spec())['state'], 'unknown')
        folder = self.root / 'take-take-1'
        self.assertTrue(os.path.samefile(folder / 'raw.partial.mp4', folder / 'raw.mp4'))
        manifest = (folder / 'manifest.json').read_bytes()
        self.assertEqual(self.rec.recover('take-1')['state'], 'validated')
        self.assertEqual((folder / 'manifest.json').read_bytes(), manifest)
        self.assertFalse((folder / 'raw.partial.mp4').exists())

    def test_desktop_busy_across_runtime_roots(self):
        with DesktopLock(self.lock):
            with self.assertRaisesRegex(RecordingError, 'DESKTOP_BUSY'):
                self.rec.record(spec())
        self.assertEqual(self.backend.calls, 0)

    def test_inherited_lock_outlives_supervisor_descriptor(self):
        with DesktopLock(self.lock) as lease:
            proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(0.4)'], pass_fds=(lease.fd,))
        try:
            with self.assertRaisesRegex(RecordingError, 'DESKTOP_BUSY'):
                with DesktopLock(self.lock): pass
        finally: proc.wait(timeout=3)
        with DesktopLock(self.lock): pass

    def make_active(self):
        self.rec.record(spec())
        with self.rec.store.transaction():
            take = self.rec.store.get('take-1'); take['state'] = 'recording'; self.rec.store.put(take)

    def test_lost_execution_becomes_unknown_not_success(self):
        self.make_active()
        self.assertEqual(self.rec.status('take-1')['state'], 'unknown')

    def test_replayed_start_after_loss_returns_unknown_without_restart(self):
        self.make_active()
        self.assertEqual(self.rec.record(spec())['state'], 'unknown')
        self.assertEqual(self.backend.calls, 1)

    def test_stop_is_idempotent_marker_not_arbitrary_pid_signal(self):
        self.make_active()
        with DesktopLock(self.lock):
            a = self.rec.stop('take-1', 'stop-1'); b = self.rec.stop('take-1', 'stop-1')
            self.assertEqual(a['state'], 'stopping'); self.assertEqual(b['state'], 'stopping')
            marker = json.loads((self.root / 'take-take-1/stop.json').read_text())
            self.assertEqual(marker, {'take_id': 'take-1', 'operation': 'stop'})
        with self.assertRaisesRegex(RecordingError, 'REQUEST_CONFLICT'):
            self.rec.stop('take-1', 'request-1')

    def test_terminal_stop_does_not_change_completed_recording(self):
        a = self.rec.record(spec())
        self.assertEqual(self.rec.stop('take-1', 'stop-1'), a)
        self.assertEqual(self.rec.stop('take-1', 'stop-1'), a)

    def test_stop_marker_symlink_is_refused_not_overwritten(self):
        self.make_active()
        marker = self.root / 'take-take-1/stop.json'
        marker.symlink_to(self.root / 'missing-target')
        with DesktopLock(self.lock), self.assertRaises(RecordingError):
            self.rec.stop('take-1', 'stop-linked')
        self.assertTrue(marker.is_symlink())

    def test_restart_persists_idempotency(self):
        a = self.rec.record(spec()); self.rec.close()
        self.rec = Recorder(self.root, backend=FakeBackend(), validator=good_media, lock_path=self.lock)
        self.assertEqual(self.rec.record(spec()), a)
        self.assertEqual(self.rec.backend.calls, 0)

    def test_recover_without_completion_stays_unknown(self):
        self.rec.backend = FakeBackend(events=[{'event': 'process_exit', 'exit_code': 0}])
        self.rec.record(spec())
        self.assertEqual(self.rec.recover('take-1')['state'], 'unknown')

    def test_recovery_malformed_event_is_not_success(self):
        self.rec.backend = FakeBackend(events=[{'event': 'process_exit', 'exit_code': 0}])
        self.rec.record(spec())
        (self.root / 'take-take-1/backend-events.jsonl').write_text('[]\n{"event":')
        self.assertEqual(self.rec.recover('take-1')['state'], 'unknown')

    def test_recover_after_clean_backend_exit_validates_media(self):
        self.rec.backend = FakeBackend(events=[{'event': 'process_exit', 'exit_code': 0}])
        self.rec.record(spec())
        events = [{'event': 'source_verified', 'source': {'mode': 'fixture', 'window_id': 2, 'owner_pid': 3}},
                  {'event': 'recording_finished', 'clean': True}, {'event': 'process_exit', 'exit_code': 0}]
        (self.root / 'take-take-1/backend-events.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        self.assertEqual(self.rec.recover('take-1')['state'], 'validated')


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.path = Path(self.temp.name) / 'video.mp4'
        self.path.write_bytes(b'only-a-metadata-fixture')
        self.path.chmod(0o600)

    def tearDown(self): self.temp.cleanup()

    def runner(self, streams=None, pts=None, decode_code=0, duration='0.4'):
        def run(args, **kwargs):
            if '-show_streams' in args:
                out = {'streams': streams or [{'codec_type': 'video', 'width': 64, 'height': 64, 'codec_name': 'h264'}], 'format': {'duration': duration}}
            elif '-show_frames' in args:
                out = {'frames': [{'best_effort_timestamp_time': t} for t in (pts if pts is not None else [0, 0.2])]}
            else: return subprocess.CompletedProcess(args, decode_code, '', '' if not decode_code else 'decode error')
            return subprocess.CompletedProcess(args, 0, json.dumps(out), '')
        return run

    @patch('recording_agent.media.shutil.which', side_effect=lambda n: '/fake/' + n)
    def test_audio_stream_refused(self, _):
        with self.assertRaises(MediaError): inspect_media(self.path, 1, self.runner(streams=[{'codec_type': 'video'}, {'codec_type': 'audio'}]))

    @patch('recording_agent.media.shutil.which', side_effect=lambda n: '/fake/' + n)
    def test_corruption_and_bad_pts_refused(self, _):
        for run in [self.runner(decode_code=1), self.runner(pts=[0.2, 0.1]), self.runner(pts=[])]:
            with self.assertRaises(MediaError): inspect_media(self.path, 1, run)

    def test_symlink_media_refused(self):
        link = self.path.with_name('link.mp4'); link.symlink_to(self.path)
        with self.assertRaises(MediaError): inspect_media(link, 1)

    def test_public_media_refused(self):
        self.path.chmod(0o644)
        with self.assertRaisesRegex(MediaError, 'private'): inspect_media(self.path, 1)

    @patch('recording_agent.media.shutil.which', side_effect=lambda n: '/fake/' + n)
    def test_duration_plan_and_explicit_stop_policy(self, _):
        with self.assertRaises(MediaError): inspect_media(self.path, 7, self.runner(duration='0.1'))
        early = inspect_media(self.path, 7, self.runner(duration='0.1'), allow_early_stop=True)
        self.assertEqual(early['plan_completion'], 'stopped_early'); self.assertFalse(early['plan_complete'])
        normal = inspect_media(self.path, 7, self.runner(duration='7.2'))
        self.assertTrue(normal['plan_complete'])

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Existing media tools required')
    def test_actual_synthetic_silent_media_full_decode(self):
        # Offline generated color, no screen, account, camera, microphone or device input.
        self.path.unlink()
        subprocess.run([shutil.which('ffmpeg'), '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                        'color=c=blue:s=64x64:r=10:d=0.5', '-an', '-c:v', 'libx264', str(self.path)], check=True, timeout=10, umask=0o077)
        media = inspect_media(self.path, 1)
        self.assertEqual(media['audio_streams'], 0)
        self.assertEqual(media['full_decode_exit'], 0)
        self.assertEqual(len(media['presentation_timestamps_seconds']), 5)


if __name__ == '__main__': unittest.main()
