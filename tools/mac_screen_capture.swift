import Foundation
import ScreenCaptureKit
import CoreMedia
import CoreVideo

final class FrameWriter: NSObject, SCStreamOutput {
    private let output = FileHandle.standardOutput

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of outputType: SCStreamOutputType) {
        guard outputType == .screen, sampleBuffer.isValid,
              let image = sampleBuffer.imageBuffer else { return }
        CVPixelBufferLockBaseAddress(image, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(image, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(image) else { return }
        let widthBytes = CVPixelBufferGetWidth(image) * 4
        let height = CVPixelBufferGetHeight(image)
        let stride = CVPixelBufferGetBytesPerRow(image)
        if stride == widthBytes {
            output.write(Data(bytes: base, count: stride * height))
        } else {
            for row in 0..<height {
                output.write(Data(bytes: base.advanced(by: row * stride), count: widthBytes))
            }
        }
    }
}

let arguments = CommandLine.arguments
let width = arguments.count > 1 ? Int(arguments[1]) ?? 1920 : 1920
let height = arguments.count > 2 ? Int(arguments[2]) ?? 1080 : 1080
let fps = arguments.count > 3 ? Int(arguments[3]) ?? 60 : 60
let displayIndex = arguments.count > 4 ? Int(arguments[4]) ?? 0 : 0
let writer = FrameWriter()
var activeStream: SCStream?

SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) { content, error in
    guard let content else {
        FileHandle.standardError.write(Data("ScreenCaptureKit: \(error?.localizedDescription ?? "no displays")\n".utf8))
        exit(1)
    }
    let displays = content.displays.sorted { $0.displayID < $1.displayID }
    FileHandle.standardError.write(Data("ScreenCaptureKit: found \(displays.count) displays\n".utf8))
    guard displayIndex >= 0 && displayIndex < displays.count else {
        FileHandle.standardError.write(Data("ScreenCaptureKit: display index \(displayIndex) unavailable\n".utf8))
        exit(2)
    }
    let filter = SCContentFilter(display: displays[displayIndex], excludingWindows: [])
    let config = SCStreamConfiguration()
    config.width = width
    config.height = height
    config.pixelFormat = kCVPixelFormatType_32BGRA
    config.minimumFrameInterval = CMTime(value: 1, timescale: CMTimeScale(fps))
    config.queueDepth = 3
    config.showsCursor = true
    let stream = SCStream(filter: filter, configuration: config, delegate: nil)
    activeStream = stream
    do {
        try stream.addStreamOutput(writer, type: .screen, sampleHandlerQueue: DispatchQueue(label: "screen.frames"))
    } catch {
        FileHandle.standardError.write(Data("ScreenCaptureKit output: \(error)\n".utf8))
        exit(3)
    }
    stream.startCapture { error in
        if let error {
            FileHandle.standardError.write(Data("ScreenCaptureKit start: \(error)\n".utf8))
            exit(4)
        }
        FileHandle.standardError.write(Data("ScreenCaptureKit: capture started on display \(displayIndex)\n".utf8))
    }
}

RunLoop.main.run()
