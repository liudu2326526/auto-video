import AppKit
import ScreenCaptureKit
import AVFoundation
import ApplicationServices

func emit(_ event: String, _ data: [String: Any] = [:]) {
    var row = data
    row["event"] = event
    row["utc"] = ISO8601DateFormatter().string(from: Date())
    row["monotonic_seconds"] = ProcessInfo.processInfo.systemUptime
    if let bytes = try? JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]),
       let line = String(data: bytes, encoding: .utf8) { print(line); fflush(stdout) }
}

final class Fixture: NSView {
    var tick = 0
    override func draw(_ dirtyRect: NSRect) {
        NSColor(calibratedRed: 0.02, green: 0.06, blue: 0.12, alpha: 1).setFill(); bounds.fill()
        let attributes: [NSAttributedString.Key: Any] = [.font: NSFont.monospacedSystemFont(ofSize: 22, weight: .bold), .foregroundColor: NSColor.white]
        "AUTO VIDEO | SAFE WINDOW".draw(at: NSPoint(x: 30, y: 240), withAttributes: attributes)
        "Silent capture / no camera / tick \(tick)".draw(at: NSPoint(x: 30, y: 180), withAttributes: attributes)
        NSColor.systemMint.setFill(); NSRect(x: 30 + CGFloat(tick % 100) * 5, y: 50, width: 50, height: 35).fill()
    }
}

final class WindowRecorder: NSObject, NSApplicationDelegate, SCStreamDelegate, SCRecordingOutputDelegate, SCContentSharingPickerObserver {
    let config: [String: Any]
    var window: NSWindow?
    let fixture = Fixture(frame: NSRect(x: 0, y: 0, width: 720, height: 320))
    var stream: SCStream?
    var recording: SCRecordingOutput?
    var animation: Timer?
    var monitor: Timer?
    var controlMonitor: Timer?
    var started = false
    var stopping = false
    var finished = false
    var unsafe = false
    var sourcePID: pid_t = 0
    var sourceID: CGWindowID = 0
    var sourceBundle = ""
    var sourceLaunch: Date?
    var expectedLaunch: Date?
    let instanceID = UUID().uuidString
    var sourceBounds: CGRect = .zero
    var sourceDisplaySignature = ""
    var selectionUsed = false
    var duration: Double { config["duration_seconds"] as! Double }
    var mode: String { config["mode"] as! String }

    init(_ config: [String: Any]) { self.config = config; super.init() }

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let fd = config["lease_fd"] as? Int32, fcntl(fd, F_GETFD) != -1 else { fail("missing_inherited_desktop_lease"); return }
        guard CGPreflightScreenCaptureAccess() else { fail("screen_capture_permission_denied"); return }
        emit("permission_preflight", ["screen_capture": true, "accessibility": AXIsProcessTrusted(), "audio": false, "microphone": false, "camera": false])
        let output = config["output"] as! String
        guard !FileManager.default.fileExists(atPath: output) else { fail("output_exists"); return }
        controlMonitor = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            guard let self, !self.stopping else { return }
            if getppid() != self.config["parent_pid"] as? pid_t || !CGPreflightScreenCaptureAccess() {
                self.fail("parent_or_permission_lost"); return
            }
            if let size = self.recording?.recordedFileSize, size >= 256 * 1024 * 1024 {
                self.fail("recording_size_limit"); return
            }
            if let free = try? FileManager.default.attributesOfFileSystem(forPath: output)[.systemFreeSize] as? NSNumber,
               free.int64Value < 64 * 1024 * 1024 { self.fail("low_disk_space"); return }
            if FileManager.default.fileExists(atPath: self.config["stop_file"] as! String) {
                if !self.validStopRequest() { self.fail("invalid_stop_marker"); return }
                if self.started { self.stop("requested") } else { self.fail("cancelled_before_start") }
            }
        }
        if mode == "fixture" {
            let window = NSWindow(contentRect: fixture.frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
            self.window = window; window.title = "Auto Video Safe Fixture"; window.contentView = fixture
            window.isReleasedWhenClosed = false; window.center(); window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
            animation = Timer.scheduledTimer(withTimeInterval: 1.0 / 15.0, repeats: true) { [weak self] _ in
                guard let self else { return }; self.fixture.tick += 1; self.fixture.needsDisplay = true
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                SCShareableContent.getCurrentProcessShareableContent { content, error in
                    DispatchQueue.main.async {
                        guard error == nil, let target = content?.windows.first(where: { $0.windowID == CGWindowID(window.windowNumber) && $0.owningApplication?.processID == getpid() }) else { self.fail("fixture_window_not_found"); return }
                        self.accept(SCContentFilter(desktopIndependentWindow: target), target)
                    }
                }
            }
        } else {
            guard let expected = config["expected_owner_pid"] as? pid_t,
                  let app = NSRunningApplication(processIdentifier: expected),
                  app.bundleIdentifier == config["expected_bundle_id"] as? String else { fail("expected_owner_not_running"); return }
            guard let launch = app.launchDate else { fail("expected_owner_launch_identity_unavailable"); return }
            expectedLaunch = launch
            var pickerConfig = SCContentSharingPickerConfiguration()
            pickerConfig.allowedPickerModes = [.singleWindow]
            pickerConfig.allowsChangingSelectedContent = false
            let picker = SCContentSharingPicker.shared
            picker.defaultConfiguration = pickerConfig; picker.add(self); picker.isActive = true
            picker.present(using: .window)
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 60 + duration + 5) {
            if !self.finished { self.unsafe = true; emit("timeout_unknown"); self.stop("hard_deadline")
                DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.quit(3) }
            }
        }
    }

    func contentSharingPicker(_ picker: SCContentSharingPicker, didCancelFor stream: SCStream?) {
        DispatchQueue.main.async { self.fail("selection_cancelled") }
    }
    func contentSharingPickerStartDidFailWithError(_ error: Error) {
        DispatchQueue.main.async { self.fail("selection_failed") }
    }
    func contentSharingPicker(_ picker: SCContentSharingPicker, didUpdateWith filter: SCContentFilter, for stream: SCStream?) {
        DispatchQueue.main.async {
            guard !self.selectionUsed, filter.style == .window, filter.includedWindows.count == 1 else { self.fail("not_one_explicit_window"); return }
            self.selectionUsed = true
            let target = filter.includedWindows[0]
            guard target.owningApplication?.processID == self.config["expected_owner_pid"] as? pid_t,
                  target.owningApplication?.bundleIdentifier == self.config["expected_bundle_id"] as? String else { self.fail("selected_owner_mismatch"); return }
            self.accept(filter, target)
        }
    }

    func targetDescription() -> [String: Any]? {
        // Exact-window metadata only; never enumerate other apps or read window titles.
        guard let ids = WindowIdentityPolicy.windowIDArray(sourceID) else { return nil }
        guard let rows = CGWindowListCreateDescriptionFromArray(ids) as? [[String: Any]], rows.count == 1 else { return nil }
        return rows[0]
    }

    func displaySignature() -> String? {
        var ids = [CGDirectDisplayID](repeating: 0, count: 16); var count: UInt32 = 0
        guard CGGetDisplaysWithRect(sourceBounds, 16, &ids, &count) == .success, count == 1 else { return nil }
        let id = ids[0]
        guard let screen = NSScreen.screens.first(where: { ($0.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value == id }) else { return nil }
        return "\(id):\(CGDisplayPixelsWide(id))x\(CGDisplayPixelsHigh(id)):\(screen.backingScaleFactor)"
    }

    func validStopRequest() -> Bool {
        guard let data = FileManager.default.contents(atPath: config["stop_file"] as! String), data.count < 4096,
              let request = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              Set(request.keys) == Set(["take_id", "operation"]),
              request["take_id"] as? String == config["take_id"] as? String,
              request["operation"] as? String == "stop" else { return false }
        return true
    }

    func sourceInvalidReason() -> String? {
        if !CGPreflightScreenCaptureAccess() { return "permission_lost" }
        if getppid() != config["parent_pid"] as? pid_t { return "parent_lost" }
        guard let d = targetDescription() else { return "target_disappeared" }
        if d[kCGWindowOwnerPID as String] as? pid_t != sourcePID { return "window_owner_changed" }
        if d[kCGWindowIsOnscreen as String] as? Bool != true { return "window_not_onscreen" }
        guard let b = d[kCGWindowBounds as String] as? [String: Any],
              let bounds = CGRect(dictionaryRepresentation: b as CFDictionary), bounds == sourceBounds else { return "window_geometry_changed" }
        if displaySignature() != sourceDisplaySignature { return "display_or_scale_changed" }
        let app = NSRunningApplication(processIdentifier: sourcePID)
        let evidence = WindowIdentityEvidence(ownerPID: sourcePID, windowID: sourceID, bundleID: app?.bundleIdentifier, launchDate: app?.launchDate)
        if mode == "fixture" {
            if !WindowIdentityPolicy.fixture(evidence, currentPID: getpid(), ownWindowID: window.map { UInt32($0.windowNumber) }, visible: window?.isVisible ?? false) { return "fixture_identity_changed" }
        } else {
            if app?.isTerminated != false || !WindowIdentityPolicy.external(evidence, expectedPID: sourcePID, expectedBundle: sourceBundle, expectedLaunch: sourceLaunch) { return "external_identity_changed" }
        }
        if NSWorkspace.shared.frontmostApplication?.processIdentifier != sourcePID { return "foreground_changed" }
        return nil
    }

    func accept(_ filter: SCContentFilter, _ target: SCWindow) {
        guard !started, self.stream == nil, let owner = target.owningApplication else { fail("invalid_selected_identity"); return }
        let app = NSRunningApplication(processIdentifier: owner.processID)
        let evidence = WindowIdentityEvidence(ownerPID: owner.processID, windowID: target.windowID, bundleID: owner.bundleIdentifier, launchDate: app?.launchDate)
        emit("identity_observation", ["mode": mode, "owner_pid": owner.processID, "current_pid": getpid(),
            "window_id": target.windowID, "fixture_window_id": window?.windowNumber ?? -1,
            "running_application_present": app != nil, "launch_date_present": app?.launchDate != nil])
        if mode == "fixture" {
            guard WindowIdentityPolicy.fixture(evidence, currentPID: getpid(), ownWindowID: window.map { UInt32($0.windowNumber) }, visible: window?.isVisible ?? false) else { fail("fixture_identity_mismatch"); return }
            sourceLaunch = nil  // Deliberately absent, never fabricated for an unbundled CLI.
            NSApp.activate(ignoringOtherApps: true)
        } else {
            guard WindowIdentityPolicy.external(evidence, expectedPID: config["expected_owner_pid"] as! pid_t,
                expectedBundle: config["expected_bundle_id"] as! String, expectedLaunch: expectedLaunch) else { fail("external_identity_unverifiable"); return }
            sourceLaunch = evidence.launchDate
            _ = app?.activate(options: [.activateIgnoringOtherApps])
        }
        sourcePID = owner.processID; sourceID = target.windowID; sourceBundle = owner.bundleIdentifier
        guard let d = targetDescription(), let b = d[kCGWindowBounds as String] as? [String: Any],
              let bounds = CGRect(dictionaryRepresentation: b as CFDictionary) else { fail("target_window_disappeared"); return }
        sourceBounds = bounds
        guard let signature = displaySignature() else { fail("requires_one_stable_display"); return }
        sourceDisplaySignature = signature
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            if let reason = self.sourceInvalidReason() { self.fail("before_start_" + reason); return }
            let c = SCStreamConfiguration(); let scale = Double(filter.pointPixelScale)
            c.width = max(2, Int(filter.contentRect.width * scale) / 2 * 2)
            c.height = max(2, Int(filter.contentRect.height * scale) / 2 * 2)
            c.minimumFrameInterval = CMTime(value: 1, timescale: 30); c.queueDepth = 3
            c.showsCursor = false; c.capturesAudio = false; c.captureMicrophone = false
            c.scalesToFit = false; c.ignoreShadowsSingleWindow = true
            var source: [String: Any] = ["mode": self.mode, "window_id": self.sourceID, "owner_pid": self.sourcePID,
                "bundle_id": self.sourceBundle, "backend_instance_id": self.instanceID,
                "process_identity_kind": self.mode == "fixture" ? "current_pid_and_retained_nswindow" : "pid_bundle_and_verified_launch_date",
                "filter": "desktopIndependentWindow", "point_pixel_scale": scale,
                "content_width_points": filter.contentRect.width, "content_height_points": filter.contentRect.height,
                "output_width": c.width, "output_height": c.height, "display_signature": self.sourceDisplaySignature,
                "audio": false, "microphone": false, "camera": false]
            if let launch = self.sourceLaunch { source["process_launch_utc"] = ISO8601DateFormatter().string(from: launch) }
            else { source["process_launch_utc"] = NSNull() }
            emit("source_verified", ["source": source])
            let rc = SCRecordingOutputConfiguration(); rc.outputURL = URL(fileURLWithPath: self.config["output"] as! String)
            rc.videoCodecType = .h264; rc.outputFileType = .mp4
            let recording = SCRecordingOutput(configuration: rc, delegate: self); self.recording = recording
            let stream = SCStream(filter: filter, configuration: c, delegate: self); self.stream = stream
            do { try stream.addRecordingOutput(recording) } catch { self.fail("recording_output_failed"); return }
            emit("start_intent")
            stream.startCapture { error in DispatchQueue.main.async {
                if error != nil { self.fail("start_failed") } else { emit("start_acknowledged") }
            } }
        }
    }

    func recordingOutputDidStartRecording(_ recordingOutput: SCRecordingOutput) {
        DispatchQueue.main.async {
            self.started = true; emit("recording_started")
            self.monitor = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
                guard let self, !self.stopping else { return }
                if let reason = self.sourceInvalidReason() { self.unsafe = true; emit("interference", ["reason": reason]); self.stop("fail_closed"); return }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + self.duration) { self.stop("duration_limit") }
        }
    }

    func stop(_ reason: String) {
        guard !stopping else { return }; stopping = true; monitor?.invalidate(); emit("stop_intent", ["reason": reason])
        guard let stream else { quit(2); return }
        stream.stopCapture { error in DispatchQueue.main.async {
            if error != nil { self.unsafe = true; emit("blocked_or_failed", ["reason": "stop_failed"]) }
            else { emit("stop_acknowledged") }
        } }
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { if !self.finished { emit("timeout_unknown"); self.quit(3) } }
    }
    func recordingOutputDidFinishRecording(_ recordingOutput: SCRecordingOutput) {
        DispatchQueue.main.async { self.finished = true; emit("recording_finished", ["clean": !self.unsafe && self.stopping]); self.quit(self.unsafe ? 2 : 0) }
    }
    func recordingOutput(_ recordingOutput: SCRecordingOutput, didFailWithError error: Error) { DispatchQueue.main.async { self.fail("recording_failed") } }
    func stream(_ stream: SCStream, didStopWithError error: Error) { DispatchQueue.main.async { self.fail("stream_failed") } }
    func fail(_ reason: String) {
        unsafe = true; emit("blocked_or_failed", ["reason": reason])
        if started { stop(reason) } else { quit(2) }
    }
    func quit(_ code: Int32) {
        animation?.invalidate(); monitor?.invalidate(); controlMonitor?.invalidate(); window?.close()
        SCContentSharingPicker.shared.isActive = false
        emit("process_exit", ["exit_code": code]); exit(code)
    }
}

guard CommandLine.arguments.count == 2,
      let data = FileManager.default.contents(atPath: CommandLine.arguments[1]),
      let config = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
      let duration = config["duration_seconds"] as? Double, duration.isFinite, duration >= 1, duration <= 30,
      let mode = config["mode"] as? String, ["fixture", "picker"].contains(mode),
      config["output"] is String, config["stop_file"] is String else { emit("blocked_or_failed", ["reason": "invalid_config"]); exit(2) }
let app = NSApplication.shared; app.setActivationPolicy(.regular)
let delegate = WindowRecorder(config); app.delegate = delegate; app.run()
