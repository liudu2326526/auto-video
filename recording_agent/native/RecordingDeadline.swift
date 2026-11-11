import Foundation
import Dispatch

// Queue-confined, one-shot, monotonic deadline. Strict/leeway is best effort,
// not a hard real-time guarantee; media duration acceptance remains independent.
final class RecordingDeadline {
    private let queue: DispatchQueue
    private var timer: DispatchSourceTimer?

    init(queue: DispatchQueue) { self.queue = queue }

    @discardableResult
    func arm(at target: DispatchTime, fire: @escaping () -> Void) -> Bool {
        dispatchPrecondition(condition: .onQueue(queue))
        guard timer == nil else { return false }
        let source = DispatchSource.makeTimerSource(flags: .strict, queue: queue)
        source.setEventHandler { [weak self] in
            guard let self, self.timer != nil else { return }
            // Dispatch timers can wake early. Do not deliver an early stop.
            if DispatchTime.now().uptimeNanoseconds < target.uptimeNanoseconds {
                self.timer?.schedule(deadline: target, leeway: .milliseconds(1))
                return
            }
            self.cancel()
            fire()
        }
        source.schedule(deadline: target, leeway: .milliseconds(1))
        timer = source
        source.resume()
        return true
    }

    func cancel() {
        dispatchPrecondition(condition: .onQueue(queue))
        timer?.setEventHandler {}
        timer?.cancel()
        timer = nil
    }

    deinit { timer?.cancel() }
}
