"""Run real Dispatch timers without AppKit, capture, device or account access."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'Existing macOS Swift compiler required')
class RecordingDeadlineTests(unittest.TestCase):
    def test_one_shot_no_early_fire_cancel_rearm_and_blocked_queue(self):
        source = Path(__file__).resolve().parents[1] / 'recording_agent/native/RecordingDeadline.swift'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / 'main.swift'
            main.write_text('''import Foundation
import Dispatch
let queue = DispatchQueue(label: "deadline-tests", qos: .userInitiated)
let timer = RecordingDeadline(queue: queue)
let fired = DispatchSemaphore(value: 0)
let start = DispatchTime.now()
var count = 0
var elapsed = 0.0
var duplicateAccepted = true
queue.sync {
    precondition(timer.arm(at: start + 0.25) {
        count += 1
        elapsed = Double(DispatchTime.now().uptimeNanoseconds-start.uptimeNanoseconds)/1e9
        fired.signal()
    })
    duplicateAccepted = timer.arm(at: start + 0.01) { fatalError("Duplicate arm replaced active timer") }
}
precondition(fired.wait(timeout: .now()+3) == .success)
Thread.sleep(forTimeInterval: 0.1)
queue.sync { precondition(count == 1 && elapsed >= 0.25 && elapsed < 0.55 && !duplicateAccepted) }
let cancelled = DispatchSemaphore(value: 0)
queue.sync {
    precondition(timer.arm(at: .now()+0.05) { cancelled.signal() })
    timer.cancel(); timer.cancel()
}
precondition(cancelled.wait(timeout: .now()+0.15) == .timedOut)
let afterCancel = DispatchSemaphore(value: 0)
queue.sync { precondition(timer.arm(at: .now()+0.05) { afterCancel.signal() }) }
precondition(afterCancel.wait(timeout: .now()+2) == .success)
// A serial queue that is actually blocked cannot provide hard-real-time delivery.
// Verify it still fires only once, after its absolute target, rather than rearming
// for another relative duration when execution resumes.
let blocked = DispatchSemaphore(value: 0)
let blockStart = DispatchTime.now()
var blockedElapsed = 0.0
queue.sync {
    precondition(timer.arm(at: blockStart + 0.05) {
        blockedElapsed = Double(DispatchTime.now().uptimeNanoseconds-blockStart.uptimeNanoseconds)/1e9
        blocked.signal()
    })
    Thread.sleep(forTimeInterval: 0.15)
}
precondition(blocked.wait(timeout: .now()+2) == .success)
queue.sync { precondition(blockedElapsed >= 0.15 && blockedElapsed < 0.45); timer.cancel() }
print("{\\"fire_count\\":\\(count),\\"elapsed_seconds\\":\\(elapsed),\\"duplicate_arm_refused\\":true,\\"cancelled_callback_suppressed\\":true,\\"blocked_elapsed_seconds\\":\\(blockedElapsed)}")
''')
            binary = root / 'deadline-tests'
            compiled = subprocess.run([shutil.which('swiftc'), '-swift-version', '5', '-module-cache-path',
                                       str(root / 'modules'), str(source), str(main), '-o', str(binary)],
                                      capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data['fire_count'], 1)
            self.assertTrue(data['duplicate_arm_refused'])
            self.assertTrue(data['cancelled_callback_suppressed'])
