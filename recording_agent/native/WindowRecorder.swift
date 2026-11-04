import AppKit
import ScreenCaptureKit
import AVFoundation
import ApplicationServices

let eventOutputLock = NSLock()
func emit(_ event: String, _ data: [String: Any] = [:]) {
    var row = data
    row["event"] = event
    row["utc"] = ISO8601DateFormatter().string(from: Date())
    row["monotonic_seconds"] = ProcessInfo.processInfo.systemUptime
    if let bytes = try? JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]),
       let line = String(data: bytes, encoding: .utf8) {
        eventOutputLock.lock(); defer { eventOutputLock.unlock() }
        print(line); fflush(stdout)
    }
}

func timedValue<T>(_ stage: String, _ operation: () -> T) -> T {
    let start = ProcessInfo.processInfo.systemUptime
    defer {
        let elapsed = ProcessInfo.processInfo.systemUptime - start
        if elapsed > 0.025 { emit("slow_native_check", ["stage": stage, "elapsed_ms": elapsed * 1000]) }
    }
    return operation()
}

final class IdleApplication: NSObject, NSApplicationDelegate {
    var window: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let item = NSMenuItem(); let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Quit Auto Video Recorder", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.submenu = appMenu; menu.addItem(item); NSApp.mainMenu = menu
        let panel = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 460, height: 160),
                             styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        panel.title = "Auto Video Recorder — Idle"
        panel.isReleasedWhenClosed = false
        let label = NSTextField(wrappingLabelWithString:
            "Idle — no recording session. No window picker or capture is active. Start an authorized session through the project CLI.")
        label.frame = NSRect(x: 20, y: 65, width: 420, height: 70)
        panel.contentView?.addSubview(label)
        let quit = NSButton(title: "Quit", target: NSApp, action: #selector(NSApplication.terminate(_:)))
        quit.frame = NSRect(x: 325, y: 20, width: 115, height: 34)
        quit.setAccessibilityIdentifier("recording-idle-quit")
        panel.contentView?.addSubview(quit)
        window = panel
        panel.center(); panel.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        emit("idle_gui_ready", ["bundle_id": Bundle.main.bundleIdentifier ?? "",
            "capture_started": false, "picker_active": false])
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) {
        emit("idle_gui_exit", ["capture_started": false, "picker_active": false])
    }
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
    var selectionWindow: NSWindow?
    var pickerRequested = false
    var pickerActivated = false
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
    var explicitQueryUsed = false
    var duration: Double { config["duration_seconds"] as! Double }
    var mode: String { config["mode"] as! String }
    var validationOnly: Bool { config["validation_only"] as? Bool ?? false }
    var localBrowserRaw: Bool { mode == "explicit" && config["expected_bundle_id"] as? String == "com.google.Chrome" && config["browser_ui_policy"] as? String == "local_raw_then_crop" && !(config["expected_window_title"] as? String ?? "").isEmpty }
    var sourceDisplayID: CGDirectDisplayID = 0
    let durationDeadline = RecordingDeadline(queue: .main)
    var durationReference: DispatchSourceTimer?

    init(_ config: [String: Any]) { self.config = config; super.init() }

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard let fd = config["lease_fd"] as? Int32, fcntl(fd, F_GETFD) != -1 else { fail("missing_inherited_desktop_lease"); return }
        guard CGPreflightScreenCaptureAccess() else { fail("screen_capture_permission_denied"); return }
        emit("permission_preflight", ["screen_capture": true, "accessibility": AXIsProcessTrusted(), "audio": false, "microphone": false, "camera": false])
        let output = config["output"] as! String
        guard !FileManager.default.fileExists(atPath: output) else { fail("output_exists"); return }
        controlMonitor = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            guard let self, !self.stopping else { return }
            let allowed = timedValue("control_permission_preflight") { CGPreflightScreenCaptureAccess() }
            if getppid() != self.config["parent_pid"] as? pid_t || !allowed {
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
            if let expectedEpoch = config["expected_owner_launch_epoch"] as? Double,
               expectedEpoch != launch.timeIntervalSince1970 { fail("expected_owner_launch_identity_changed"); return }
            // New Chrome recordings must not silently use the old full-window
            // path after the content-only requirement. AX + SCK cannot attest
            // an atomic viewport/frame association; RAM buffering alone does
            // not prove that no excluded chrome appeared during a transient.
            // Existing request replays never reach this backend guard.
            if app.bundleIdentifier == "com.google.Chrome", !validationOnly, !localBrowserRaw {
                fail("browser_content_atomicity_unproven_no_capture"); return
            }
            if self.localBrowserRaw { emit("browser_framing_notice", ["policy":"local_raw_then_crop", "raw_contains_browser_ui":true, "raw_local_only":true]) }
            if mode == "explicit" { discoverExplicitTarget() }
            else { showSelectionControls() }
        }
        let deadline = validationOnly ? 15.0 : 60 + duration + 5
        DispatchQueue.main.asyncAfter(deadline: .now() + deadline) {
            if !self.finished { self.unsafe = true; emit("timeout_unknown"); self.stop("hard_deadline")
                DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.quit(3) }
            }
        }
    }

    func discoverExplicitTarget() {
        guard !explicitQueryUsed, let expectedPID = config["expected_owner_pid"] as? pid_t,
              let expectedBundle = config["expected_bundle_id"] as? String,
              let values = config["expected_bounds"] as? [Double], values.count == 4,
              values.allSatisfy({ $0.isFinite }), values[2] > 0, values[3] > 0 else {
            fail("invalid_explicit_target"); return
        }
        explicitQueryUsed = true
        let approvedBounds = CGRect(x: values[0], y: values[1], width: values[2], height: values[3])
        let expectedWindow = (config["expected_window_id"] as? UInt32)
        emit("target_metadata_query_started", ["scope": "one_on_screen_query_then_expected_pid_filter",
            "other_app_metadata_may_be_returned": true, "titles_read_or_logged": false,
            "capture_started": false])
        SCShareableContent.getExcludingDesktopWindows(true, onScreenWindowsOnly: true) { content, error in
            emit("target_metadata_query_callback", ["error_domain": (error as NSError?)?.domain ?? "",
                "error_code": (error as NSError?)?.code ?? 0])
            DispatchQueue.main.async {
                guard !self.stopping, !self.finished else { return }
                guard error == nil, let content else { self.fail("target_metadata_query_failed"); return }
                guard let app = NSRunningApplication(processIdentifier: expectedPID), !app.isTerminated,
                      app.bundleIdentifier == expectedBundle, app.launchDate != nil,
                      app.launchDate == self.expectedLaunch else { self.fail("owner_changed_during_query"); return }
                guard content.windows.count <= 2048 else { self.fail("metadata_limit"); return }
                // The API returns a system list. Read no titles, and inspect geometry
                // only after the expected PID filter; never persist unrelated rows.
                let own = content.windows.filter { $0.owningApplication?.processID == expectedPID }
                let candidates = own.map { ExplicitWindowCandidate(ownerPID: expectedPID,
                    bundleID: $0.owningApplication?.bundleIdentifier ?? "", windowID: $0.windowID,
                    bounds: $0.frame, onScreen: $0.isOnScreen, layer: $0.windowLayer) }
                guard let selectedID = WindowIdentityPolicy.uniqueExplicit(candidates, expectedPID: expectedPID,
                    expectedBundle: expectedBundle, expectedWindowID: expectedWindow, bounds: approvedBounds),
                    let target = own.first(where: { $0.windowID == selectedID }) else {
                    self.fail("explicit_target_missing_or_ambiguous"); return
                }
                self.selectionUsed = true
                self.accept(SCContentFilter(desktopIndependentWindow: target), target)
            }
        }
    }

    func showSelectionControls() {
        // A real, bundled app window gives supported accessibility tools a stable
        // application target. It neither selects a source nor starts capture.
        let panel = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 460, height: 200),
                             styleMask: [.titled], backing: .buffered, defer: false)
        panel.title = "Auto Video Recorder — Select one window"
        panel.isReleasedWhenClosed = false
        let label = NSTextField(wrappingLabelWithString:
            "Silent single-window recording. Choose only the approved target window in the macOS picker. Nothing is recorded until its identity is verified.")
        label.frame = NSRect(x: 20, y: 110, width: 420, height: 65)
        panel.contentView?.addSubview(label)
        let session = NSTextField(labelWithString: "Session: \(config["take_id"] as? String ?? "unknown")")
        session.frame = NSRect(x: 20, y: 75, width: 420, height: 22)
        panel.contentView?.addSubview(session)
        let choose = NSButton(title: "Choose target window", target: self, action: #selector(presentPicker(_:)))
        choose.frame = NSRect(x: 20, y: 24, width: 245, height: 34)
        choose.setAccessibilityIdentifier("recording-choose-target")
        panel.contentView?.addSubview(choose)
        let cancel = NSButton(title: "Cancel recording", target: self, action: #selector(cancelSelection(_:)))
        cancel.frame = NSRect(x: 275, y: 24, width: 165, height: 34)
        panel.contentView?.addSubview(cancel)
        selectionWindow = panel
        panel.center(); panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        emit("picker_controls_ready", ["bundle_id": Bundle.main.bundleIdentifier ?? "",
            "take_id": config["take_id"] as? String ?? "", "selection_required": true,
            "capture_started": false, "picker_active": false])
    }

    @objc func presentPicker(_ sender: NSButton) {
        guard !pickerRequested, !selectionUsed, !stopping, !finished else { return }
        guard let expected = config["expected_owner_pid"] as? pid_t,
              let app = NSRunningApplication(processIdentifier: expected), !app.isTerminated,
              app.bundleIdentifier == config["expected_bundle_id"] as? String,
              app.launchDate != nil, app.launchDate == expectedLaunch else {
            fail("expected_owner_identity_changed_before_picker"); return
        }
        pickerRequested = true; sender.isEnabled = false
        var pickerConfig = SCContentSharingPickerConfiguration()
        pickerConfig.allowedPickerModes = [.singleWindow]
        pickerConfig.allowsChangingSelectedContent = false
        let picker = SCContentSharingPicker.shared
        picker.defaultConfiguration = pickerConfig; picker.add(self); picker.isActive = true
        pickerActivated = true
        emit("picker_activated", ["audio": false, "microphone": false, "camera": false])
        picker.present(using: .window)
        // API invocation is not evidence that the system UI is visible or selected.
        emit("picker_presentation_requested")
    }

    @objc func cancelSelection(_ sender: NSButton) {
        guard !started, !stopping else { return }
        fail("selection_cancelled")
    }

    func contentSharingPicker(_ picker: SCContentSharingPicker, didCancelFor stream: SCStream?) {
        emit("picker_callback_received", ["kind": "cancel"])
        DispatchQueue.main.async { self.fail("selection_cancelled") }
    }
    func contentSharingPickerStartDidFailWithError(_ error: Error) {
        emit("picker_callback_received", ["kind": "error", "error_domain": (error as NSError).domain, "error_code": (error as NSError).code])
        DispatchQueue.main.async { self.fail("selection_failed") }
    }
    func contentSharingPicker(_ picker: SCContentSharingPicker, didUpdateWith filter: SCContentFilter, for stream: SCStream?) {
        emit("picker_callback_received", ["kind": "selection"])
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
        if mode == "explicit", let expectedID = config["expected_display_id"] as? UInt32, expectedID != id { return nil }
        guard let screen = NSScreen.screens.first(where: { ($0.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)?.uint32Value == id }) else { return nil }
        sourceDisplayID = id
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
        if !timedValue("source_permission_preflight", { CGPreflightScreenCaptureAccess() }) { return "permission_lost" }
        if getppid() != config["parent_pid"] as? pid_t { return "parent_lost" }
        guard let d = timedValue("source_window_description", { targetDescription() }) else { return "target_disappeared" }
        if d[kCGWindowOwnerPID as String] as? pid_t != sourcePID { return "window_owner_changed" }
        if d[kCGWindowIsOnscreen as String] as? Bool != true { return "window_not_onscreen" }
        if localBrowserRaw, d[kCGWindowName as String] as? String != config["expected_window_title"] as? String { return "approved_browser_window_title_changed" }
        guard let b = d[kCGWindowBounds as String] as? [String: Any],
              let bounds = CGRect(dictionaryRepresentation: b as CFDictionary), bounds == sourceBounds else { return "window_geometry_changed" }
        if timedValue("source_display_signature", { displaySignature() }) != sourceDisplaySignature { return "display_or_scale_changed" }
        let app = timedValue("source_running_application") { NSRunningApplication(processIdentifier: sourcePID) }
        let evidence = timedValue("source_launch_identity") { WindowIdentityEvidence(ownerPID: sourcePID, windowID: sourceID, bundleID: app?.bundleIdentifier, launchDate: app?.launchDate) }
        if mode == "fixture" {
            if !WindowIdentityPolicy.fixture(evidence, currentPID: getpid(), ownWindowID: window.map { UInt32($0.windowNumber) }, visible: window?.isVisible ?? false) { return "fixture_identity_changed" }
        } else {
            if app?.isTerminated != false || !WindowIdentityPolicy.external(evidence, expectedPID: sourcePID, expectedBundle: sourceBundle, expectedLaunch: sourceLaunch) { return "external_identity_changed" }
        }
        if timedValue("source_frontmost_application", { NSWorkspace.shared.frontmostApplication?.processIdentifier }) != sourcePID { return "foreground_changed" }
        return nil
    }

    func accept(_ filter: SCContentFilter, _ target: SCWindow) {
        guard !stopping, !finished, !started, self.stream == nil, let owner = target.owningApplication else { fail("invalid_selected_identity"); return }
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
            selectionWindow?.close(); selectionWindow = nil
            _ = app?.activate(options: [.activateIgnoringOtherApps])
        }
        sourcePID = owner.processID; sourceID = target.windowID; sourceBundle = owner.bundleIdentifier
        guard let d = targetDescription(), let b = d[kCGWindowBounds as String] as? [String: Any],
              let bounds = CGRect(dictionaryRepresentation: b as CFDictionary) else { fail("target_window_disappeared"); return }
        sourceBounds = bounds
        if mode == "explicit" {
            guard let values = config["expected_bounds"] as? [Double], values.count == 4,
                  sourceBounds == CGRect(x: values[0], y: values[1], width: values[2], height: values[3]),
                  (config["expected_window_id"] as? UInt32 == nil || config["expected_window_id"] as? UInt32 == sourceID) else {
                fail("explicit_target_changed_before_accept"); return
            }
        }
        guard let signature = displaySignature() else { fail("requires_one_stable_display"); return }
        sourceDisplaySignature = signature
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            if let reason = self.sourceInvalidReason() { self.fail("before_start_" + reason); return }
            let c = SCStreamConfiguration(); let scale = Double(filter.pointPixelScale)
            c.width = max(2, Int(filter.contentRect.width * scale) / 2 * 2)
            c.height = max(2, Int(filter.contentRect.height * scale) / 2 * 2)
            c.minimumFrameInterval = CMTime(value: 1, timescale: 30); c.queueDepth = 3
            c.showsCursor = false; c.capturesAudio = false; c.captureMicrophone = false
            c.includeChildWindows = false
            c.scalesToFit = false; c.ignoreShadowsSingleWindow = true
            var source: [String: Any] = ["mode": self.mode, "window_id": self.sourceID, "owner_pid": self.sourcePID,
                "bundle_id": self.sourceBundle, "backend_instance_id": self.instanceID,
                "process_identity_kind": self.mode == "fixture" ? "current_pid_and_retained_nswindow" : "pid_bundle_and_verified_launch_date",
                "filter": "desktopIndependentWindow", "point_pixel_scale": scale,
                "content_width_points": filter.contentRect.width, "content_height_points": filter.contentRect.height,
                "filter_content_rect_points": [filter.contentRect.minX, filter.contentRect.minY, filter.contentRect.width, filter.contentRect.height],
                "output_width": c.width, "output_height": c.height, "display_signature": self.sourceDisplaySignature,
                "display_id": self.sourceDisplayID,
                "bounds": [self.sourceBounds.origin.x, self.sourceBounds.origin.y, self.sourceBounds.width, self.sourceBounds.height],
                "audio": false, "microphone": false, "camera": false, "include_child_windows": false]
            if let launch = self.sourceLaunch { source["process_launch_epoch"] = launch.timeIntervalSince1970 }
            if self.localBrowserRaw { source["browser_ui_policy"] = "local_raw_then_crop"; source["window_title_verified"] = true; source["raw_contains_browser_ui"] = true }
            if let launch = self.sourceLaunch { source["process_launch_utc"] = ISO8601DateFormatter().string(from: launch) }
            else { source["process_launch_utc"] = NSNull() }
            emit("source_verified", ["source": source])
            if self.validationOnly {
                // This branch must remain before construction of *any* capture stream/output.
                self.finished = true
                emit("selection_validation_completed", ["capture_started": false, "audio": false])
                self.quit(0); return
            }
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
        let callbackUptime = ProcessInfo.processInfo.systemUptime
        let target = DispatchTime.now() + duration
        let expectedStop = callbackUptime + duration
        emit("recording_start_callback_received", ["main_thread": Thread.isMainThread])
        DispatchQueue.main.async {
            guard !self.started, !self.stopping, !self.finished else { emit("duplicate_or_late_recording_start"); return }
            self.started = true; emit("recording_started")
            emit("duration_timer_armed", ["expected_stop_monotonic": expectedStop, "target_seconds": self.duration,
                "scheduler": "strict_dispatch_source", "leeway_ms": 1,
                "callback_to_main_ms": (ProcessInfo.processInfo.systemUptime - callbackUptime) * 1000])
            let reference = DispatchSource.makeTimerSource(flags: .strict, queue: .global(qos: .userInitiated))
            reference.setEventHandler {
                emit("duration_background_reference", ["expected_stop_monotonic": expectedStop,
                    "lateness_ms": (ProcessInfo.processInfo.systemUptime - expectedStop) * 1000])
            }
            reference.schedule(deadline: target, leeway: .milliseconds(1))
            self.durationReference = reference; reference.resume()
            guard self.durationDeadline.arm(at: target, fire: { [weak self] in
                guard let self else { return }
                emit("duration_main_deadline_fired", ["expected_stop_monotonic": expectedStop,
                    "lateness_ms": (ProcessInfo.processInfo.systemUptime - expectedStop) * 1000])
                self.stop("duration_limit")
            }) else { self.fail("duration_timer_arm_failed"); return }
            self.monitor = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
                guard let self, !self.stopping else { return }
                if let reason = self.sourceInvalidReason() { self.unsafe = true; emit("interference", ["reason": reason]); self.stop("fail_closed"); return }
            }
        }
    }

    func stop(_ reason: String) {
        guard !stopping else { return }; stopping = true; monitor?.invalidate()
        durationDeadline.cancel(); durationReference?.cancel(); durationReference = nil
        emit("stop_intent", ["reason": reason])
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
        durationDeadline.cancel(); durationReference?.cancel(); durationReference = nil
        animation?.invalidate(); monitor?.invalidate(); controlMonitor?.invalidate(); window?.close()
        selectionWindow?.close(); selectionWindow = nil
        if pickerActivated {
            SCContentSharingPicker.shared.isActive = false
            SCContentSharingPicker.shared.remove(self)
        }
        emit("process_exit", ["exit_code": code]); exit(code)
    }
}

if CommandLine.arguments.count == 1 {
    let app = NSApplication.shared; app.setActivationPolicy(.regular)
    let idle = IdleApplication(); app.delegate = idle
    withExtendedLifetime(idle) { app.run() }
    exit(0)
}

guard CommandLine.arguments.count == 2,
      let data = FileManager.default.contents(atPath: CommandLine.arguments[1]),
      let config = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
      let duration = config["duration_seconds"] as? Double, duration.isFinite, duration >= 1, duration <= 30,
      let mode = config["mode"] as? String, ["fixture", "picker", "explicit"].contains(mode),
      config["output"] is String, config["stop_file"] is String else { emit("blocked_or_failed", ["reason": "invalid_config"]); exit(2) }
let app = NSApplication.shared; app.setActivationPolicy(.regular)
let delegate = WindowRecorder(config); app.delegate = delegate
withExtendedLifetime(delegate) { app.run() }
