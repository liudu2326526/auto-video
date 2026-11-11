"""Exercise the actual owned-child watchdog with capture-free subprocesses."""
import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from recording_agent import native
from recording_agent.core import DesktopLock


@unittest.skipUnless(os.name == 'posix', 'POSIX child signals and inherited flock required')
class NativeWatchdogTests(unittest.TestCase):
    def check_timeout(self, ignore_term):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / 'take-test'; folder.mkdir(mode=0o700)
            helper = root / 'owned-child'
            helper.write_text('#!' + sys.executable + '\n' + '''import json,os,signal,sys,time
from pathlib import Path
config=Path(sys.argv[1]); data=json.loads(config.read_text())
(config.parent/'owned-child.pid').write_text(str(os.getpid()))
''' + ('signal.signal(signal.SIGTERM,signal.SIG_IGN)\n' if ignore_term else '') +
                              'while True: time.sleep(0.1)\n')
            helper.chmod(0o700)
            real_popen = native.subprocess.Popen
            children = []
            def owned_popen(*args, **kwargs):
                child = real_popen(*args, **kwargs); children.append(child); return child
            t0 = time.monotonic()
            # Accelerate only the native supervisor's deadline clock. Popen.wait
            # retains real time, including its actual TERM -> KILL grace period.
            clock = SimpleNamespace(monotonic=lambda: (time.monotonic()-t0)*100,
                                    sleep=time.sleep)
            lease_path = root / 'desktop.lock'
            with DesktopLock(lease_path) as lease, \
                 patch.object(native, 'build', return_value=helper), \
                 patch.object(native, 'time', clock), \
                 patch.object(native.subprocess, 'Popen', side_effect=owned_popen):
                with self.assertRaises(TimeoutError):
                    native.NativeBackend().run({'take_id':'test', 'duration_seconds':1},
                                               folder, lease.fd, lambda *_: None)
            self.assertEqual(len(children), 1)
            child = children[0]
            self.assertEqual(int((folder/'owned-child.pid').read_text()), child.pid)
            self.assertEqual(child.poll(), -signal.SIGKILL if ignore_term else -signal.SIGTERM)
            self.assertFalse((folder/'raw.partial.mp4').exists())
            # The real child inherited the lease. It must have closed it on exit.
            with DesktopLock(lease_path): pass

    def test_timeout_terminates_and_reaps_only_owned_child(self):
        self.check_timeout(False)

    def test_timeout_kills_term_ignoring_owned_child_after_real_grace(self):
        self.check_timeout(True)
