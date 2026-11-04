import Foundation
import AVFoundation
import UniformTypeIdentifiers
import CoreMedia
import CoreVideo

enum CropPublicationEvidence {
    // Only the retained, self-created fixture can provide synchronous layout
    // invalidation before a change. AX snapshots of another app are not atomic.
    case authoritativeFixture
    case externalAXOnly
}

enum MemoryCropError: Error { case rejected(String) }

final class MemoryCropWriter: NSObject, AVAssetWriterDelegate {
    let queue: DispatchQueue
    private let segments: BoundedSegments
    let width: Int
    let height: Int
    private let writer: AVAssetWriter
    private let input: AVAssetWriterInput
    private var firstPTS: CMTime?
    private var lastPTS: CMTime?
    private var finishing = false
    private(set) var frames = 0
    var memorySnapshot:(bytes:Int,peak:Int,fragments:Int,failure:String?) {segments.snapshot}

    init(width:Int, height:Int, byteLimit:Int, queue:DispatchQueue) throws {
        guard width>0,height>0,width%2==0,height%2==0,width<=4096,height<=4096,
              width*height<=4_194_304 else {throw MemoryCropError.rejected("invalid_crop_dimensions")}
        self.width=width;self.height=height;self.queue=queue
        segments=BoundedSegments(limit:byteLimit)
        // This initializer delivers segments to the delegate. There is no file
        // URL and no provisional plaintext recording anywhere on disk.
        writer=AVAssetWriter(contentType:.mpeg4Movie)
        writer.outputFileTypeProfile = .mpeg4AppleHLS
        writer.preferredOutputSegmentInterval=CMTime(seconds:1,preferredTimescale:600)
        writer.initialSegmentStartTime = .zero
        input=AVAssetWriterInput(mediaType:.video,outputSettings:[
            AVVideoCodecKey:AVVideoCodecType.h264,AVVideoWidthKey:width,AVVideoHeightKey:height,
            AVVideoCompressionPropertiesKey:[AVVideoAverageBitRateKey:4_000_000,
                AVVideoMaxKeyFrameIntervalKey:30,AVVideoAllowFrameReorderingKey:false]])
        input.expectsMediaDataInRealTime=true
        super.init()
        guard writer.canAdd(input) else {throw MemoryCropError.rejected("writer_input_refused")}
        writer.add(input);writer.delegate=self
        guard writer.startWriting() else {
            let error=writer.error as NSError?
            throw MemoryCropError.rejected("writer_start_failed:\(error?.domain ?? "unknown"):\(error?.code ?? 0)")
        }
        writer.startSession(atSourceTime:.zero)
    }

    var ready:Bool { input.isReadyForMoreMediaData && segments.snapshot.failure == nil && !finishing }

    // Caller provides samples already cropped by SCK, never full-window pixels.
    // Returns true only for the first successfully appended frame.
    @discardableResult
    func append(_ sample:CMSampleBuffer) throws -> Bool {
        dispatchPrecondition(condition:.onQueue(queue))
        guard !finishing,segments.snapshot.failure == nil else {throw MemoryCropError.rejected("writer_not_open")}
        guard CMSampleBufferIsValid(sample),CMSampleBufferDataIsReady(sample),CMSampleBufferGetNumSamples(sample)==1,
              let image=CMSampleBufferGetImageBuffer(sample),CVPixelBufferGetWidth(image)==width,
              CVPixelBufferGetHeight(image)==height else {abort("sample_dimensions_or_state_invalid");throw MemoryCropError.rejected("sample_dimensions_or_state_invalid")}
        let pts=CMSampleBufferGetPresentationTimeStamp(sample)
        guard pts.isNumeric,pts.seconds.isFinite,lastPTS==nil || CMTimeCompare(pts,lastPTS!)>0 else {
            abort("invalid_sample_pts");throw MemoryCropError.rejected("invalid_sample_pts")
        }
        guard input.isReadyForMoreMediaData else {abort("bounded_encoder_backpressure");throw MemoryCropError.rejected("bounded_encoder_backpressure")}
        if firstPTS==nil {firstPTS=pts}
        var timing=CMSampleTimingInfo(duration:CMSampleBufferGetDuration(sample),
            presentationTimeStamp:CMTimeSubtract(pts,firstPTS!),decodeTimeStamp:.invalid)
        var copy:CMSampleBuffer?
        guard CMSampleBufferCreateCopyWithNewTiming(allocator:kCFAllocatorDefault,sampleBuffer:sample,
            sampleTimingEntryCount:1,sampleTimingArray:&timing,sampleBufferOut:&copy)==noErr,
            let copy,input.append(copy) else {abort("encode_failed");throw MemoryCropError.rejected("encode_failed")}
        lastPTS=pts;frames+=1
        return frames==1
    }

    func abort(_ reason:String) {
        dispatchPrecondition(condition:.onQueue(queue))
        segments.invalidate(reason)
        if writer.status == .writing {writer.cancelWriting()}
    }

    func finish(evidence:CropPublicationEvidence,completion:@escaping(Result<[Data],Error>)->Void) {
        dispatchPrecondition(condition:.onQueue(queue))
        guard !finishing,frames>0,segments.snapshot.failure == nil,writer.status == .writing else {
            abort("unconfirmed_writer_completion");completion(.failure(MemoryCropError.rejected("unconfirmed_writer_completion")));return
        }
        finishing=true;input.markAsFinished()
        writer.finishWriting { [self] in queue.async {
            do {
                let trusted:Bool
                switch evidence {case .authoritativeFixture:trusted=true;case .externalAXOnly:trusted=false}
                let data=try self.segments.seal(geometryTrusted:trusted,writerCompleted:self.writer.status == .completed)
                completion(.success(data))
            } catch {self.segments.invalidate("publication_evidence_rejected");completion(.failure(error))}
        }}
    }

    func assetWriter(_ writer:AVAssetWriter,didOutputSegmentData data:Data,segmentType:AVAssetSegmentType) {
        do {
            guard segmentType == .initialization || segmentType == .separable else {throw MemoryCropError.rejected("unknown_segment_type")}
            try segments.append(data,initialization:segmentType == .initialization)
        } catch {segments.invalidate("segment_rejected")}
    }
}
