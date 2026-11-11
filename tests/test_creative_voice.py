"""Local policy tests use mocked model metadata, not speech-quality claims."""
import copy
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from creative_agent import voice


class VoicePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.request = self.root / 'voice.json'
        ep = self.root / 'frozen.json'; ep.write_text('mocked frozen plan')
        self.base = {'output': {'frames': 60}, 'script': {'lines': {'line1': '先核对数字。'}},
                     'subtitles': [{'line_id': 'line1', 'in_frame': 0, 'out_frame': 60}]}
        files = {}; self.pins = {}
        for key in voice.PINNED:
            f = self.root / key; f.write_bytes(('mocked reviewed '+key).encode())
            self.pins[key] = voice.digest(f); files[key] = {'path': str(f), 'sha256': self.pins[key]}
        self.plan = {'schema': 'local_preset_voice/v1', 'id': 'local-fixture',
                     'edit_plan': {'path': str(ep), 'sha256': voice.digest(ep)}, 'repository': voice.REPO,
                     'revision': voice.REVISION, 'voice_id': 'zf_001', 'files': files, 'speeds': {'line1': 1.1}}
        self.write()

    def write(self): self.request.write_text(json.dumps(self.plan))
    def validate(self):
        self.write()
        with patch.object(voice, 'PINNED', self.pins), patch.object(voice, 'edit_validate', return_value=(self.base, {})):
            return voice.validate_voice(self.request)

    def test_pinned_request_is_timing_input_not_an_approval(self):
        p, b, files = self.validate()
        self.assertEqual(b['output']['frames'], 60); self.assertEqual(set(files), set(self.pins))
        self.assertNotIn('approved', p)

    def test_unknown_identity_fields_and_changed_model_rejected(self):
        original = copy.deepcopy(self.plan)
        for key, value in [('repository', 'other/model'), ('revision', 'main'), ('voice_id', 'cloned_voice'),
                           ('schema', 'v1'), ('score', 100)]:
            self.plan = dict(original, **{key: value})
            with self.subTest(key=key), self.assertRaises(voice.EditError): self.validate()
        self.plan = copy.deepcopy(original); Path(self.plan['files']['voice']['path']).write_bytes(b'changed')
        with self.assertRaises(voice.EditError): self.validate()

    def test_per_line_rate_bounds_and_unhandled_latin_refused(self):
        for speed in [True, float('nan'), .99, 1.36, 'fast']:
            self.plan['speeds']['line1'] = speed
            with self.subTest(speed=speed), self.assertRaises(voice.EditError): self.validate()
        self.plan['speeds'] = {}
        with self.assertRaises(voice.EditError): self.validate()
        self.plan['speeds'] = {'line1': 1.1}; self.base['script']['lines']['line1'] = '看看 Gamma。'
        with self.assertRaises(voice.EditError): self.validate()

    def test_timeout_reaps_only_own_worker_records_failure_and_never_overwrites(self):
        output = self.root / 'timeout'; child = MagicMock(pid=12345)
        child.wait.side_effect = [subprocess.TimeoutExpired('fixture', 1), 0]; child.poll.return_value = None
        with patch.object(voice, 'validate_voice', return_value=(self.plan, self.base, {})), \
             patch.object(voice.subprocess, 'Popen', return_value=child) as launch, \
             patch.object(voice.os, 'killpg') as kill:
            with self.assertRaises(voice.EditError): voice.prepare_voice(self.request, output, timeout=1)
            kill.assert_called_once_with(12345, signal.SIGTERM)
            env = launch.call_args.kwargs['env']
            self.assertEqual(env['HF_HUB_OFFLINE'], '1'); self.assertNotIn('HF_TOKEN', env)
            result = json.loads((output / 'result.json').read_text()); self.assertFalse(result['publication_approved'])
            with self.assertRaises(FileExistsError): voice.prepare_voice(self.request, output)
        self.assertFalse((output / 'local-narration.wav').exists())

    def test_actual_child_timeout_is_reaped_without_speech_or_desktop(self):
        real_popen = subprocess.Popen; children = []
        def fixture_child(*args, **kwargs):
            child = real_popen([sys.executable, '-c', 'import time; time.sleep(10)'], **kwargs)
            children.append(child); return child
        started = time.monotonic()
        with patch.object(voice, 'validate_voice', return_value=(self.plan, self.base, {})), \
             patch.object(voice.subprocess, 'Popen', side_effect=fixture_child):
            with self.assertRaises(voice.EditError):
                voice.prepare_voice(self.request, self.root / 'real-timeout', timeout=1)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(len(children), 1); self.assertIsNotNone(children[0].poll())
        self.assertFalse((self.root / 'real-timeout/local-narration.wav').exists())


@unittest.skipUnless(importlib.util.find_spec('numpy'), 'sample policy tests require NumPy')
class UtterancePlacementTests(unittest.TestCase):
    def test_complete_utterance_and_exact_frame_offset(self):
        import numpy as np
        timeline = np.zeros(48000, dtype=np.float32); audio = np.full(12000, .1, dtype=np.float32)
        r = voice.place_utterance(timeline, audio, {'in_frame': 30, 'out_frame': 60})
        self.assertFalse(r['truncated']); self.assertEqual(r['samples'], 12000)
        self.assertTrue(np.all(timeline[:24000] == 0)); self.assertTrue(np.all(timeline[24000:36000] == audio))
        self.assertTrue(np.all(timeline[36000:] == 0))

    def test_oversize_silent_clipped_nan_and_wrong_shape_never_placed(self):
        import numpy as np
        for audio in [np.ones(23900)*.1, np.zeros(100), np.ones(100)*1.01,
                      np.ones(100)*float('nan'), np.ones((10, 2))*.1]:
            timeline = np.zeros(24000, dtype=np.float32)
            with self.subTest(shape=audio.shape), self.assertRaises(voice.EditError):
                voice.place_utterance(timeline, audio, {'in_frame': 0, 'out_frame': 30})
            self.assertTrue(np.all(timeline == 0))


if __name__ == '__main__': unittest.main()
