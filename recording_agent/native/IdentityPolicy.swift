import Foundation
import CoreFoundation
import CoreGraphics

struct ExplicitWindowCandidate {
    let ownerPID: Int32
    let bundleID: String
    let windowID: UInt32
    let bounds: CGRect
    let onScreen: Bool
    let layer: Int
}

struct WindowIdentityEvidence {
    let ownerPID: Int32
    let windowID: UInt32
    let bundleID: String?
    let launchDate: Date?
}

enum WindowIdentityPolicy {
    static func uniqueExplicit(_ candidates: [ExplicitWindowCandidate], expectedPID: Int32,
                               expectedBundle: String, expectedWindowID: UInt32?, bounds: CGRect) -> UInt32? {
        let matches = candidates.filter {
            $0.ownerPID == expectedPID && $0.bundleID == expectedBundle && $0.onScreen && $0.layer == 0 &&
            $0.windowID != 0 && $0.bounds == bounds && (expectedWindowID == nil || $0.windowID == expectedWindowID)
        }
        return matches.count == 1 ? matches[0].windowID : nil
    }

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
