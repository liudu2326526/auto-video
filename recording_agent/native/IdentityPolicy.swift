import Foundation
import CoreFoundation

struct WindowIdentityEvidence {
    let ownerPID: Int32
    let windowID: UInt32
    let bundleID: String?
    let launchDate: Date?
}

enum WindowIdentityPolicy {
    // CGWindowListCreateDescriptionFromArray consumes raw CGWindowID values,
    // not a CFArray of boxed NSNumber objects. Nil callbacks retain no objects.
    static func windowIDArray(_ windowID: UInt32) -> CFArray? {
        guard windowID != 0 else { return nil }
        var raw: UnsafeRawPointer? = UnsafeRawPointer(bitPattern: UInt(windowID))
        return CFArrayCreate(kCFAllocatorDefault, &raw, 1, nil)
    }

    // An executing process cannot have its own PID reused while it is alive.
    // This proof applies only to the NSWindow owned and retained by this process.
    static func fixture(_ evidence: WindowIdentityEvidence, currentPID: Int32,
                        ownWindowID: UInt32?, visible: Bool) -> Bool {
        guard let ownWindowID else { return false }
        return evidence.ownerPID == currentPID && evidence.windowID == ownWindowID && visible
    }

    // External owners must retain a reliable startup identity from before selection.
    static func external(_ evidence: WindowIdentityEvidence, expectedPID: Int32,
                         expectedBundle: String, expectedLaunch: Date?) -> Bool {
        guard let launch = evidence.launchDate, let expectedLaunch else { return false }
        return evidence.ownerPID == expectedPID && evidence.bundleID == expectedBundle && launch == expectedLaunch
    }
}
