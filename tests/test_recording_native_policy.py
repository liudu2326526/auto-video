"""Compile/run the actual pure Swift identity policy; no AppKit app, GUI or capture API."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'Existing macOS Swift compiler required')
class NativeIdentityPolicyTests(unittest.TestCase):
    def test_actual_swift_fixture_and_external_identity_policy(self):
        source = Path(__file__).resolve().parents[1] / 'recording_agent/native/IdentityPolicy.swift'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / 'main.swift'
            main.write_text('''import Foundation
import CoreFoundation
var passed = 0
func check(_ condition: Bool) { if !condition { fatalError("Identity policy assertion failed") }; passed += 1 }
let noDate = WindowIdentityEvidence(ownerPID: 10, windowID: 20, bundleID: nil, launchDate: nil)
check(WindowIdentityPolicy.fixture(noDate, currentPID: 10, ownWindowID: 20, visible: true))
check(!WindowIdentityPolicy.fixture(noDate, currentPID: 11, ownWindowID: 20, visible: true))
check(!WindowIdentityPolicy.fixture(noDate, currentPID: 10, ownWindowID: 21, visible: true))
check(!WindowIdentityPolicy.fixture(noDate, currentPID: 10, ownWindowID: nil, visible: true))
check(!WindowIdentityPolicy.fixture(noDate, currentPID: 10, ownWindowID: 20, visible: false))
let date = Date(timeIntervalSince1970: 1000)
let good = WindowIdentityEvidence(ownerPID: 10, windowID: 20, bundleID: "com.example.Tool", launchDate: date)
check(WindowIdentityPolicy.external(good, expectedPID: 10, expectedBundle: "com.example.Tool", expectedLaunch: date))
check(!WindowIdentityPolicy.external(noDate, expectedPID: 10, expectedBundle: "com.example.Tool", expectedLaunch: date))
check(!WindowIdentityPolicy.external(good, expectedPID: 10, expectedBundle: "com.example.Tool", expectedLaunch: nil))
check(!WindowIdentityPolicy.external(good, expectedPID: 11, expectedBundle: "com.example.Tool", expectedLaunch: date))
check(!WindowIdentityPolicy.external(good, expectedPID: 10, expectedBundle: "com.other.Tool", expectedLaunch: date))
check(!WindowIdentityPolicy.external(good, expectedPID: 10, expectedBundle: "com.example.Tool", expectedLaunch: Date(timeIntervalSince1970: 1001)))
let exactWindow = WindowIdentityPolicy.windowIDArray(20)!
check(CFArrayGetCount(exactWindow) == 1)
check(UInt(bitPattern: CFArrayGetValueAtIndex(exactWindow, 0)) == 20)
check(WindowIdentityPolicy.windowIDArray(0) == nil)
let bounds = CGRect(x: 1800, y: -600, width: 720, height: 400)
func candidate(_ pid: Int32 = 10, _ wid: UInt32 = 20, _ frame: CGRect = bounds, _ on: Bool = true, _ layer: Int = 0) -> ExplicitWindowCandidate {
    ExplicitWindowCandidate(ownerPID: pid, bundleID: "com.example.Tool", windowID: wid, bounds: frame, onScreen: on, layer: layer)
}
func select(_ rows: [ExplicitWindowCandidate], _ wid: UInt32? = nil) -> UInt32? {
    WindowIdentityPolicy.uniqueExplicit(rows, expectedPID: 10, expectedBundle: "com.example.Tool", expectedWindowID: wid, bounds: bounds)
}
check(select([candidate()]) == 20)
check(select([]) == nil)
check(select([candidate(), candidate(10, 21)]) == nil)
check(select([candidate(), candidate(10, 21)], 20) == 20)
check(select([candidate()], 21) == nil)
check(select([candidate(11)]) == nil)
check(select([candidate(10, 0)]) == nil)
check(select([candidate(10, 20, CGRect(x: 0, y: 0, width: 720, height: 400))]) == nil)
check(select([candidate(10, 20, bounds, false)]) == nil)
check(select([candidate(10, 20, bounds, true, 1)]) == nil)
print("identity_policy_assertions=\\(passed)")
''')
            binary = root / 'identity-policy-tests'
            compiled = subprocess.run([shutil.which('swiftc'), '-swift-version', '5', '-module-cache-path',
                            str(root / 'modules'), str(source), str(main), '-o', str(binary)],
                           check=False, timeout=120, capture_output=True, text=True, umask=0o077)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary)], check=True, timeout=10, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), 'identity_policy_assertions=24')
