"""App packaging and failed-build containment. No GUI, capture or permissions."""
import os
import plistlib
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from recording_agent import native
from recording_agent.core import RecordingError


class NativeBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'runtime'
        self.compiles = 0

    def compiler(self, command, **kwargs):
        self.compiles += 1
        out = Path(command[command.index('-o') + 1])
        self.assertFalse(out.exists())
        self.assertEqual(stat.S_IMODE(out.parent.stat().st_mode), 0o700)
        out.write_bytes(b'fake-compiler-output-not-executed')
        out.chmod(0o700)
        return SimpleNamespace(returncode=0, stderr='')

    def build(self):
        with patch.object(native.sys, 'platform', 'darwin'), \
             patch.object(native.shutil, 'which', return_value='/existing/swiftc'), \
             patch.object(native.subprocess, 'run', side_effect=self.compiler), \
             patch.object(native.subprocess, 'Popen') as launch:
            target = native.build(self.root)
            launch.assert_not_called()
            return target

    def test_build_publishes_private_standard_app_and_reuses_cache_without_launch(self):
        target = self.build()
        app = target.parents[2]
        self.assertEqual(app.name, 'AutoVideoRecorder.app')
        self.assertEqual(target.relative_to(app), Path('Contents/MacOS/WindowRecorder'))
        info = app / 'Contents/Info.plist'
        self.assertEqual(plistlib.loads(info.read_bytes()), native.APP_INFO)
        self.assertEqual(native.APP_INFO['CFBundlePackageType'], 'APPL')
        self.assertEqual(native.APP_INFO['CFBundleExecutable'], target.name)
        for directory in (app, app / 'Contents', target.parent):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(info.stat().st_mode), 0o600)
        self.assertTrue(os.access(target, os.X_OK))
        self.assertEqual(self.build(), target)
        self.assertEqual(self.compiles, 1)

    def test_packaging_version_change_invalidates_cache_without_overwrite(self):
        first = self.build()
        original = first.read_bytes()
        with patch.object(native, 'APP_INFO', dict(native.APP_INFO,
                          CFBundleVersion=str(int(native.APP_INFO['CFBundleVersion']) + 1))):
            second = self.build()
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), original)
        self.assertEqual(self.compiles, 2)

    def test_unexpected_metadata_refused_and_preserved(self):
        target = self.build()
        info = target.parents[1] / 'Info.plist'
        changed = plistlib.dumps(dict(native.APP_INFO, CFBundleExecutable='other'))
        info.write_bytes(changed)
        with self.assertRaises(RecordingError): self.build()
        self.assertEqual(info.read_bytes(), changed)
        self.assertEqual(self.compiles, 1)

    def test_symlinked_internal_directory_refused(self):
        target = self.build()
        original = target.parent.with_name('original-MacOS')
        target.parent.rename(original)
        target.parent.symlink_to(original, target_is_directory=True)
        with self.assertRaises(RecordingError): self.build()
        self.assertEqual(self.compiles, 1)

    def test_exposed_internal_metadata_refused_without_chmod(self):
        target = self.build()
        info = target.parents[1] / 'Info.plist'
        info.chmod(0o644)
        with self.assertRaises(RecordingError): self.build()
        self.assertEqual(stat.S_IMODE(info.stat().st_mode), 0o644)
        self.assertEqual(self.compiles, 1)

    def test_failed_compilation_publishes_no_executable_or_partial_compiler_files(self):
        def fail(command, **kwargs):
            out = Path(command[command.index('-o') + 1])
            out.write_bytes(b'incomplete')
            out.chmod(0o700)
            return SimpleNamespace(returncode=1, stderr='expected compiler failure')
        with patch.object(self, 'compiler', side_effect=fail):
            with self.assertRaises(RecordingError): self.build()
        self.assertFalse(list(self.root.rglob('WindowRecorder')))
        self.assertFalse(list(self.root.rglob('compile-*')))


if __name__ == '__main__': unittest.main()
