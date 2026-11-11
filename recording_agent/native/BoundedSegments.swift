import Foundation

enum SegmentError: Error { case rejected(String) }

// RAM only. It never opens a file and never accepts a geometry uncertainty as a
// caveat. AVAssetWriter's asynchronous delegate calls are serialized by the lock.
final class BoundedSegments {
    private let lock = NSLock()
    private let limit: Int
    private var initialization: Data?
    private var fragments = [UInt32:Data]()
    private var retained = 0
    private var peak = 0
    private var rejected: String?
    private var sealed = false

    init(limit: Int) { precondition(limit > 0 && limit <= 256*1024*1024); self.limit=limit }

    var snapshot: (bytes:Int, peak:Int, fragments:Int, failure:String?) {
        lock.lock(); defer { lock.unlock() }
        return (retained,peak,fragments.count,rejected)
    }

    func invalidate(_ reason: String) {
        lock.lock(); defer { lock.unlock() }
        rejectLocked(reason)
    }

    private func rejectLocked(_ reason: String) {
        if rejected == nil { rejected=reason }
        initialization=nil;fragments.removeAll();retained=0
    }

    func append(_ data: Data, initialization isInitialization: Bool) throws {
        lock.lock(); defer { lock.unlock() }
        if let reason=rejected { throw SegmentError.rejected(reason) }
        guard !sealed else { throw SegmentError.rejected("already_sealed") }
        guard !data.isEmpty, data.count <= limit-retained, fragments.count < 64 else {
            rejectLocked("memory_budget_exceeded"); throw SegmentError.rejected("memory_budget_exceeded")
        }
        do {
            if isInitialization {
                let boxes=try Self.boxes(data, start:0, end:data.count)
                guard initialization == nil, boxes.filter({$0.name=="ftyp"}).count==1,
                      boxes.filter({$0.name=="moov"}).count==1,
                      !boxes.contains(where:{$0.name=="moof" || $0.name=="mdat"}) else {
                    throw SegmentError.rejected("invalid_initialization")
                }
                initialization=data
            } else {
                let sequence=try Self.sequence(data)
                guard sequence>0, fragments[sequence]==nil else { throw SegmentError.rejected("duplicate_fragment") }
                fragments[sequence]=data
            }
            retained+=data.count;peak=max(peak,retained)
        } catch {
            rejectLocked("invalid_segment");throw error
        }
    }

    func seal(geometryTrusted: Bool, writerCompleted: Bool) throws -> [Data] {
        lock.lock(); defer { lock.unlock() }
        guard geometryTrusted, writerCompleted else {
            rejectLocked("untrusted_completion");throw SegmentError.rejected("untrusted_completion")
        }
        if let reason=rejected { throw SegmentError.rejected(reason) }
        guard !sealed, let header=initialization, !fragments.isEmpty else {
            rejectLocked("incomplete_segments");throw SegmentError.rejected("incomplete_segments")
        }
        let keys=fragments.keys.sorted()
        guard keys == Array(1...UInt32(keys.count)) else {
            rejectLocked("fragment_sequence_gap");throw SegmentError.rejected("fragment_sequence_gap")
        }
        sealed=true
        return [header]+keys.map{fragments[$0]!}
    }

    private struct Box { let name:String;let content:Int;let end:Int }
    private static func u32(_ d:Data,_ p:Int) -> UInt32 {
        (UInt32(d[p])<<24)|(UInt32(d[p+1])<<16)|(UInt32(d[p+2])<<8)|UInt32(d[p+3])
    }
    private static func boxes(_ d:Data,start:Int,end:Int) throws -> [Box] {
        var p=start;var result=[Box]()
        while p<end {
            guard end-p>=8, result.count<128 else { throw SegmentError.rejected("invalid_box") }
            let n=u32(d,p);var size=UInt64(n);var header=8
            if n==1 {
                guard end-p>=16 else{throw SegmentError.rejected("invalid_extended_box")}
                size=(UInt64(u32(d,p+8))<<32)|UInt64(u32(d,p+12));header=16
            } else if n==0 {size=UInt64(end-p)}
            guard size>=UInt64(header),size<=UInt64(end-p),
                  let name=String(data:d[(p+4)..<(p+8)],encoding:.ascii) else {throw SegmentError.rejected("invalid_box_size")}
            result.append(Box(name:name,content:p+header,end:p+Int(size)));p+=Int(size)
        }
        return result
    }
    private static func sequence(_ d:Data) throws -> UInt32 {
        let top=try boxes(d,start:0,end:d.count)
        let movies=top.filter{$0.name=="moof"}
        guard movies.count==1,top.filter({$0.name=="mdat"}).count==1 else {throw SegmentError.rejected("invalid_fragment")}
        let children=try boxes(d,start:movies[0].content,end:movies[0].end)
        let heads=children.filter{$0.name=="mfhd"}
        guard heads.count==1,heads[0].end-heads[0].content>=8 else{throw SegmentError.rejected("missing_fragment_sequence")}
        return u32(d,heads[0].content+4)
    }
}
