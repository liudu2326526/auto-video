import Foundation
import Dispatch
import AVFoundation
import CoreVideo
import CoreGraphics
import CoreMedia
import Darwin
let scenario=CommandLine.arguments[1]
let path=CommandLine.arguments[2]
final class FrameState { var value=0 }
let queue=DispatchQueue(label:"crop-memory-test")
let done=DispatchSemaphore(value:0)
var result=[String:Any]()
var cropWriter:MemoryCropWriter?
func sample(_ frame:Int,_ wrongSize:Bool=false)throws->CMSampleBuffer {
 let width=wrongSize ? 66:64;var pixel:CVPixelBuffer?
 guard CVPixelBufferCreate(kCFAllocatorDefault,width,64,kCVPixelFormatType_32BGRA,nil,&pixel)==kCVReturnSuccess,let pixel else{throw MemoryCropError.rejected("pixel_alloc_failed")}
 CVPixelBufferLockBaseAddress(pixel,[])
 let row=CVPixelBufferGetBytesPerRow(pixel);let data=CVPixelBufferGetBaseAddress(pixel)!.assumingMemoryBound(to:UInt8.self)
 for y in 0..<64 {for x in 0..<width {let i=y*row+x*4;data[i]=UInt8((x+frame*3)%255);data[i+1]=180;data[i+2]=20;data[i+3]=255}}
 CVPixelBufferUnlockBaseAddress(pixel,[])
 var format:CMVideoFormatDescription?
 guard CMVideoFormatDescriptionCreateForImageBuffer(allocator:kCFAllocatorDefault,imageBuffer:pixel,formatDescriptionOut:&format)==noErr,let format else{throw MemoryCropError.rejected("format_failed")}
 var timing=CMSampleTimingInfo(duration:CMTime(value:1,timescale:30),presentationTimeStamp:CMTime(value:Int64(frame),timescale:30),decodeTimeStamp:.invalid)
 var output:CMSampleBuffer?
 guard CMSampleBufferCreateReadyWithImageBuffer(allocator:kCFAllocatorDefault,imageBuffer:pixel,formatDescription:format,sampleTiming:&timing,sampleBufferOut:&output)==noErr,let output else{throw MemoryCropError.rejected("sample_failed")}
 return output
}
func finish(_ success:Bool,_ reason:String){
 let snap=cropWriter!.memorySnapshot
 var usage=rusage();getrusage(RUSAGE_SELF,&usage)
 result=["success":success,"reason":reason,"frames":cropWriter!.frames,"retained_bytes":snap.bytes,"peak_retained_bytes":snap.peak,"failure":snap.failure as Any,"output_exists":FileManager.default.fileExists(atPath:path),"rss_peak_bytes":usage.ru_maxrss]
 if snap.failure==nil {result["failure"]=NSNull()}
 done.signal()
}

func box(_ name:String,_ payload:Data=Data())->Data {var n=UInt32(payload.count+8).bigEndian;var d=withUnsafeBytes(of:&n){Data($0)};d.append(name.data(using:.ascii)!);d.append(payload);return d}
func fragment(_ n:UInt32)->Data {var n=n.bigEndian;var p=Data(repeating:0,count:4);p.append(withUnsafeBytes(of:&n){Data($0)});return box("moof",box("mfhd",p))+box("mdat",Data([0]))}
if scenario=="policy" {
 var checks=0
 func check(_ value:Bool){precondition(value);checks+=1}
 let window=CGRect(x:-400,y:-600,width:720,height:428)
 for scale in [1.0,2.0,3.0,4.0] {
  let viewport=CGRect(x:-399.75,y:-471.75,width:719.5,height:299.5)
  let crop=CaptureCrop.make(viewport:viewport,filterContent:window,scale:scale)!
  check(crop.width%2==0 && crop.height%2==0)
  check(viewport.contains(crop.global))
  check(crop.outputPoint(fromGlobal:crop.global.origin)==CGPoint.zero)
  check(crop.outputPoint(fromGlobal:CGPoint(x:-400,y:-600))==nil)
 }
 check(CaptureCrop.make(viewport:CGRect(x:0,y:121,width:1400,height:879),filterContent:CGRect(x:0,y:0,width:1400,height:1000),scale:1)!.height==878)
 check(CaptureCrop.make(viewport:CGRect(x:0,y:0,width:1,height:20),filterContent:window,scale:1)==nil)
 check(CaptureCrop.make(viewport:window,filterContent:window,scale:0.5)==nil)
 check(CaptureCrop.make(viewport:window,filterContent:window,scale:Double.nan)==nil)
 check(CaptureCrop.make(viewport:CGRect(x:-401,y:-600,width:720,height:428),filterContent:window,scale:1)==nil)
 print("{\"checks\":\(checks)}");exit(0)
}
if scenario=="segments" {
 var checks=0
 func check(_ value:Bool){precondition(value);checks+=1}
 let header=box("ftyp")+box("moov")
 let store=BoundedSegments(limit:4096)
 try store.append(fragment(2),initialization:false);try store.append(header,initialization:true);try store.append(fragment(1),initialization:false)
 let sorted=try store.seal(geometryTrusted:true,writerCompleted:true)
 check(sorted==[header,fragment(1),fragment(2)]);check(store.snapshot.peak<=4096)
 let duplicate=BoundedSegments(limit:4096);try duplicate.append(header,initialization:true);try duplicate.append(fragment(1),initialization:false)
 do{try duplicate.append(fragment(1),initialization:false);fatalError("duplicate accepted")}catch{check(duplicate.snapshot.bytes==0)}
 let changed=BoundedSegments(limit:4096);try changed.append(header,initialization:true);try changed.append(fragment(1),initialization:false);changed.invalidate("geometry_changed")
 do{_ = try changed.seal(geometryTrusted:true,writerCompleted:true);fatalError("invalidated accepted")}catch{check(changed.snapshot.bytes==0)}
 let external=BoundedSegments(limit:4096);try external.append(header,initialization:true);try external.append(fragment(1),initialization:false)
 do{_ = try external.seal(geometryTrusted:false,writerCompleted:true);fatalError("external accepted")}catch{check(external.snapshot.bytes==0)}
 let budget=BoundedSegments(limit:header.count+fragment(1).count-1);try budget.append(header,initialization:true)
 do{try budget.append(fragment(1),initialization:false);fatalError("overbudget accepted")}catch{check(budget.snapshot.bytes==0);check(budget.snapshot.peak<=header.count)}
 let gap=BoundedSegments(limit:4096);try gap.append(header,initialization:true);try gap.append(fragment(2),initialization:false)
 do{_ = try gap.seal(geometryTrusted:true,writerCompleted:true);fatalError("gap accepted")}catch{check(gap.snapshot.bytes==0)}
 let corrupt=BoundedSegments(limit:4096)
 do{try corrupt.append(Data([255,255,255,255,109,100,97,116]),initialization:false);fatalError("invalid size accepted")}catch{check(corrupt.snapshot.bytes==0)}
 print("{\"checks\":\(checks)}");exit(0)
}

queue.async {
 do {cropWriter=try MemoryCropWriter(width:64,height:64,byteLimit:scenario=="overflow" ? 64:1024*1024,queue:queue)}catch{result=["init_failed":true,"error":String(describing:error)];done.signal();return}
 let w=cropWriter!;let timeout=DispatchTime.now()+10;let state=FrameState()
 func pump(){
  if w.memorySnapshot.failure != nil {w.abort("observed_rejection");finish(false,"segment_rejected");return}
  if scenario=="cancel" && state.value==45 {
   if w.memorySnapshot.fragments==0 {
    if DispatchTime.now().uptimeNanoseconds>timeout.uptimeNanoseconds {w.abort("segment_wait_timeout");finish(false,"segment_wait_timeout");return}
    queue.asyncAfter(deadline:.now()+0.005){pump()};return
   }
   w.abort("geometry_changed");finish(false,"cancelled");return
  }
  if state.value==(scenario=="cancel" ? 60:30) {
   w.finish(evidence:scenario=="external" ? .externalAXOnly:.authoritativeFixture){r in
    switch r {
     case .success(let chunks):
      do {var bytes=Data();for chunk in chunks {bytes.append(chunk)};try bytes.write(to:URL(fileURLWithPath:path),options:.withoutOverwriting);finish(true,"committed")}
      catch {w.abort("test_file_write_failed");finish(false,"write_failed")}
     case .failure:finish(false,"completion_rejected")
    }
   };return
  }
  if DispatchTime.now().uptimeNanoseconds>timeout.uptimeNanoseconds {w.abort("test_timeout");finish(false,"timeout");return}
  if !w.ready {queue.asyncAfter(deadline:.now()+0.002){pump()};return}
  do {try w.append(sample(state.value,scenario=="wrong_dimensions"));state.value+=1;queue.async{pump()}}
  catch {finish(false,"append_rejected")}
 }
 pump()
}
guard done.wait(timeout:.now()+15) == .success else{print("timeout");exit(3)}
print(String(data:try! JSONSerialization.data(withJSONObject:result,options:[.sortedKeys]),encoding:.utf8)!)
