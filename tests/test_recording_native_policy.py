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
print("identity_policy_assertions=\\(passed)")
''')
            binary = root / 'identity-policy-tests'
            subprocess.run([shutil.which('swiftc'), '-swift-version', '5', '-module-cache-path',
                            str(root / 'modules'), str(source), str(main), '-o', str(binary)],
                           check=True, timeout=120, capture_output=True, text=True, umask=0o077)
            result = subprocess.run([str(binary)], check=True, timeout=10, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), 'identity_policy_assertions=14')
