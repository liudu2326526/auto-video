import Foundation
import CoreGraphics

struct CaptureCrop {
    let windowLocal: CGRect
    let global: CGRect
    let width: Int
    let height: Int
    let scale: Double

    static func make(viewport: CGRect, filterContent: CGRect, scale: Double) -> CaptureCrop? {
        let numbers = [viewport.minX, viewport.minY, viewport.width, viewport.height,
                       filterContent.minX, filterContent.minY, filterContent.width, filterContent.height]
        guard numbers.allSatisfy({ $0.isFinite }), scale.isFinite, scale >= 1, scale <= 4,
              viewport.width > 0, viewport.height > 0, filterContent.contains(viewport) else { return nil }
        let x = ceil((viewport.minX-filterContent.minX)*scale)
        let y = ceil((viewport.minY-filterContent.minY)*scale)
        let endX = floor((viewport.maxX-filterContent.minX)*scale)
        let endY = floor((viewport.maxY-filterContent.minY)*scale)
        guard x >= 0, y >= 0, endX-x >= 2, endY-y >= 2,
              endX-x <= 4096, endY-y <= 4096 else { return nil }
        let width = Int(endX-x)/2*2, height = Int(endY-y)/2*2
        guard width*height <= 4_194_304 else { return nil }
        let local = CGRect(x:x/scale, y:y/scale, width:Double(width)/scale, height:Double(height)/scale)
        let global = local.offsetBy(dx:filterContent.minX, dy:filterContent.minY)
        guard viewport.contains(global) else { return nil }
        return CaptureCrop(windowLocal:local, global:global, width:width, height:height, scale:scale)
    }

    func outputPoint(fromGlobal point: CGPoint) -> CGPoint? {
        let x = (point.x-global.minX)*scale, y = (point.y-global.minY)*scale
        guard x >= 0, y >= 0, x < Double(width), y < Double(height) else { return nil }
        return CGPoint(x:x,y:y)
    }
}
