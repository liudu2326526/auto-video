"""Actual crop arithmetic, bounded segment state and synthetic native H264 tests."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from recording_agent.media import inspect_media


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'Existing macOS Swift compiler required')
class CropMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name).resolve()
        repo = Path(__file__).resolve().parents[1]
        main = cls.root / 'main.swift'
        main.write_bytes((repo / 'tests/recording_crop_main.swift').read_bytes())
        cls.binary = cls.root / 'crop-tests'
        sources = [repo / 'recording_agent/native' / name for name in
                   ['CaptureCrop.swift', 'BoundedSegments.swift', 'MemoryCropWriter.swift']]
        r = subprocess.run([shutil.which('swiftc'), '-swift-version', '5', '-target', 'arm64-apple-macos15.2',
                            '-module-cache-path', str(cls.root / 'modules'), *map(str, sources),
                            str(main), '-o', str(cls.binary)], capture_output=True, text=True, timeout=120)
        if r.returncode:
            cls.temp.cleanup()
            raise AssertionError(r.stderr)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def case(self, name):
        folder = self.root / name
        folder.mkdir(mode=0o700)
        output = folder / 'output.mp4'
        r = subprocess.run([str(self.binary), name, str(output)], capture_output=True, text=True,
                           timeout=20, umask=0o077)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertNotIn('init_failed', data, data)
        return data, output

    def test_inward_crop_points_scales_negative_origin_and_mouse_mapping(self):
        data, output = self.case('policy')
        self.assertGreaterEqual(data['checks'], 20)
        self.assertFalse(output.exists())

    def test_fragment_order_duplicate_gap_budget_and_geometry_latch(self):
        data, output = self.case('segments')
        self.assertGreaterEqual(data['checks'], 8)
        self.assertFalse(output.exists())

    def test_authoritative_generated_frames_mux_and_decode(self):
        data, output = self.case('fixture')
        self.assertTrue(data['success'], data)
        self.assertLessEqual(data['peak_retained_bytes'], 1024 * 1024)
        media = inspect_media(output, 1)
        self.assertEqual(media['full_decode_exit'], 0)
        self.assertEqual(media['audio_streams'], 0)
        self.assertEqual(len(media['presentation_timestamps_seconds']), 30)

    def rejected(self, name, limit):
        data, output = self.case(name)
        self.assertFalse(data['success'], data)
        self.assertFalse(output.exists())
        self.assertEqual(list(output.parent.iterdir()), [])
        self.assertEqual(data['retained_bytes'], 0)
        self.assertLessEqual(data['peak_retained_bytes'], limit)
        return data

    def test_external_non_atomic_evidence_never_writes(self): self.rejected('external', 1024 * 1024)
    def test_budget_rejection_never_writes(self): self.rejected('overflow', 64)
    def test_geometry_change_discards_already_encoded_segments(self):
        data = self.rejected('cancel', 1024 * 1024)
        self.assertGreater(data['peak_retained_bytes'], 0)
        self.assertEqual(data['frames'], 45)
    def test_wrong_dimensions_never_writes(self): self.rejected('wrong_dimensions', 1024 * 1024)
