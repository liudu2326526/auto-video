import AppKit
import ScreenCaptureKit
import AVFoundation
import ApplicationServices

func emit(_ event: String, _ values: [String: Any] = [:]) {
    var row = values
    row["event"] = event
    row["utc"] = ISO8601DateFormatter().string(from: Date())
    row["monotonic_seconds"] = ProcessInfo.processInfo.systemUptime
    if let data = try? JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]),
       let line = String(data: data, encoding: .utf8) { print(line); fflush(stdout) }
}

final class Board: NSView {
    var tick = 0
    override func draw(_ dirtyRect: NSRect) {
        NSColor(calibratedRed: 0.025, green: 0.06, blue: 0.12, alpha: 1).setFill()
        bounds.fill()
        let title = "AUTO VIDEO | SAFE WINDOW TEST"
        title.draw(at: NSPoint(x: 35, y: 290), withAttributes: [
            .font: NSFont.monospacedSystemFont(ofSize: 22, weight: .bold),
            .foregroundColor: NSColor.white
        ])
        let lines = ["Only this generated window is selected.",
                     "Microphone OFF  /  System audio OFF  /  Camera unused",
                     "ScreenCaptureKit desktop-independent window filter",
                     "Animation tick: \(tick)"]
        for (i, text) in lines.enumerated() {
            text.draw(at: NSPoint(x: 35, y: 235 - i * 32), withAttributes: [
                .font: NSFont.monospacedSystemFont(ofSize: 15, weight: .medium),
                .foregroundColor: NSColor(calibratedRed: 0.55, green: 0.87, blue: 1, alpha: 1)
            ])
        }
        NSColor.systemMint.setFill()
        NSRect(x: 35 + CGFloat(tick % 120) * 5, y: 45, width: 55, height: 35).fill()
    }
}

final class Probe: NSObject, NSApplicationDelegate, SCRecordingOutputDelegate, SCStreamDelegate {
    var window: NSWindow!
    let board = Board(frame: NSRect(x: 0, y: 0, width: 800, height: 360))
    var stream: SCStream?
    var recording: SCRecordingOutput?
    var animation: Timer?
    var guardTimer: Timer?
    var isStopping = false
    var failed = false
    var didStart = false
    var didFinish = false
    var startTime: Double = 0
    let output: URL

    init(output: URL) { self.output = output; super.init() }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let screenPermission = CGPreflightScreenCaptureAccess()
        let axPermission = AXIsProcessTrusted()
        emit("preflight", ["screen_capture": screenPermission,
                            "accessibility": axPermission,
                            "microphone_requested": false,
                            "camera_requested": false])
        guard screenPermission else { fail("screen_capture_not_authorized"); return }
        guard !FileManager.default.fileExists(atPath: output.path) else { fail("output_already_exists"); return }
        window = NSWindow(contentRect: board.frame, styleMask: [.titled, .closable],
                          backing: .buffered, defer: false)
        window.title = "Auto Video — Generated Recording Test"
        window.contentView = board
        window.isReleasedWhenClosed = false
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        animation = Timer.scheduledTimer(withTimeInterval: 1.0 / 15.0, repeats: true) { [weak self] _ in
            guard let self else { return }; self.board.tick += 1; self.board.needsDisplay = true
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { self.selectOwnWindow() }
        DispatchQueue.main.asyncAfter(deadline: .now() + 25) {
            if !self.didFinish { self.failed = true; emit("timeout_unknown"); self.stop("deadline")
                DispatchQueue.main.asyncAfter(deadline: .now() + 3) { self.quit(3) }
            }
        }
    }

    func selectOwnWindow() {
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == getpid(), window.isVisible else {
            fail("foreground_interference_before_capture"); return
        }
        // macOS 14.4+ API restricts enumeration to this process, avoiding other app titles.
        SCShareableContent.getCurrentProcessShareableContent { content, error in
            DispatchQueue.main.async {
                if let error { self.fail("shareable_content_error", error); return }
                guard let target = content?.windows.first(where: {
                    $0.windowID == CGWindowID(self.window.windowNumber) &&
                    $0.owningApplication?.processID == getpid()
                }) else { self.fail("own_window_not_found"); return }
                self.start(target)
            }
        }
    }

    func start(_ target: SCWindow) {
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier == getpid() else {
            fail("foreground_interference_before_start"); return
        }
        let filter = SCContentFilter(desktopIndependentWindow: target)
        let scale = Double(filter.pointPixelScale)
        let config = SCStreamConfiguration()
        config.width = max(2, Int(filter.contentRect.width * scale) / 2 * 2)
        config.height = max(2, Int(filter.contentRect.height * scale) / 2 * 2)
        config.minimumFrameInterval = CMTime(value: 1, timescale: 30)
        config.queueDepth = 3
        config.showsCursor = false
        config.capturesAudio = false
        config.captureMicrophone = false
        config.scalesToFit = false
        config.ignoreShadowsSingleWindow = true
        emit("source_verified", ["window_id": target.windowID,
            "owner_pid": getpid(), "filter": "desktopIndependentWindow",
            "enumeration_scope": "currentProcessOnly", "point_pixel_scale": scale,
            "content_width_points": filter.contentRect.width,
            "content_height_points": filter.contentRect.height,
            "output_width": config.width, "output_height": config.height,
            "captures_audio": config.capturesAudio, "captures_microphone": config.captureMicrophone,
            "camera_opened": false, "output": output.path])
        let recorderConfig = SCRecordingOutputConfiguration()
        recorderConfig.outputURL = output
        recorderConfig.videoCodecType = .h264
        recorderConfig.outputFileType = .mp4
        let recording = SCRecordingOutput(configuration: recorderConfig, delegate: self)
        self.recording = recording
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        self.stream = stream
        do { try stream.addRecordingOutput(recording) }
        catch { fail("add_recording_failed", error); return }
        emit("start_intent")
        stream.startCapture { error in
            DispatchQueue.main.async {
                if let error { self.fail("start_capture_error", error) }
                else { emit("start_capture_acknowledged") }
            }
        }
    }

    func recordingOutputDidStartRecording(_ recordingOutput: SCRecordingOutput) {
        DispatchQueue.main.async {
            self.didStart = true
            self.startTime = ProcessInfo.processInfo.systemUptime
            emit("recording_started")
            self.guardTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
                guard let self, !self.isStopping else { return }
                if NSWorkspace.shared.frontmostApplication?.processIdentifier != getpid() || !self.window.isVisible {
                    self.failed = true; emit("foreground_interference"); self.stop("interference")
                }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 7) { self.stop("planned_seven_seconds") }
        }
    }

    func stop(_ reason: String) {
        guard !isStopping else { return }
        isStopping = true
        guardTimer?.invalidate()
        emit("stop_intent", ["reason": reason, "elapsed_seconds": didStart ? ProcessInfo.processInfo.systemUptime - startTime : 0])
        guard let stream else { quit(2); return }
        stream.stopCapture { error in
            DispatchQueue.main.async {
                if let error { self.failed = true; emit("stop_capture_error", ["domain": (error as NSError).domain, "code": (error as NSError).code]) }
                else { emit("stop_capture_acknowledged") }
            }
        }
    }

    func recordingOutputDidFinishRecording(_ recordingOutput: SCRecordingOutput) {
        DispatchQueue.main.async {
            self.didFinish = true
            emit("recording_finished", ["duration_seconds": CMTimeGetSeconds(recordingOutput.recordedDuration),
                 "recorded_bytes": recordingOutput.recordedFileSize,
                 "status": self.failed ? "quarantined" : "needs_media_validation"])
            self.quit(self.failed ? 2 : 0)
        }
    }

    func recordingOutput(_ recordingOutput: SCRecordingOutput, didFailWithError error: Error) {
        DispatchQueue.main.async { self.fail("recording_failed", error) }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        DispatchQueue.main.async { self.fail("stream_stopped_with_error", error) }
    }

    func fail(_ reason: String, _ error: Error? = nil) {
        failed = true
        var values: [String: Any] = ["reason": reason]
        if let error { values["domain"] = (error as NSError).domain; values["code"] = (error as NSError).code }
        emit("blocked_or_failed", values)
        if didStart { stop(reason) } else { quit(2) }
    }

    func quit(_ status: Int32) {
        animation?.invalidate(); guardTimer?.invalidate(); window?.close()
        emit("process_exit", ["exit_code": status])
        exit(status)
    }
}

guard CommandLine.arguments.count == 2 else { emit("usage_error"); exit(2) }
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let delegate = Probe(output: URL(fileURLWithPath: CommandLine.arguments[1]))
app.delegate = delegate
app.run()
