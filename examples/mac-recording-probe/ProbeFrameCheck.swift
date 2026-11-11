import Foundation
import Vision
import AppKit

let allowed = ["AUTO VIDEO", "SAFE WINDOW TEST", "Only this generated window", "Microphone OFF", "System audio OFF", "Camera unused", "ScreenCaptureKit", "desktop-independent", "Animation tick", "Auto Video", "Generated Recording Test"]
for file in CommandLine.arguments.dropFirst() {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = false
    let handler = VNImageRequestHandler(url: URL(fileURLWithPath: file))
    do {
        try handler.perform([request])
        let text = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
        let unexpected = text.filter { row in !allowed.contains(where: { row.localizedCaseInsensitiveContains($0) }) }
        let matched = allowed.filter { needle in text.contains(where: { $0.localizedCaseInsensitiveContains(needle) }) }
        let ticks = text.filter { $0.localizedCaseInsensitiveContains("Animation tick") }
        let row: [String: Any] = ["frame": URL(fileURLWithPath: file).lastPathComponent, "matched_expected_phrases": matched, "unexpected_text_count": unexpected.count, "animation_counter": ticks]
        let out = try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
        print(String(data: out, encoding: .utf8)!)
    } catch {
        let failure: [String: Any] = ["status": "local_ocr_failed", "domain": (error as NSError).domain, "code": (error as NSError).code]
        let out = try! JSONSerialization.data(withJSONObject: failure, options: [.sortedKeys])
        print(String(data: out, encoding: .utf8)!); exit(2)
    }
}
