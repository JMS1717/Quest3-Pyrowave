"""Show labelled eyes and orientation markers through SteamVR for Quest screenshot checks.

Run with: python -m tools.quest3.stereo_scene --out <private-output-dir> --seconds 30
Requires: pip install numpy opencv-python openvr glfw PyOpenGL
"""
import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np


def eye_pattern(width, height, label, color, projection, quality=False, normalized=False):
    image = np.full((height, width, 3), 24, dtype=np.uint8)
    # Draw vector primitives at the actual source resolution, while keeping
    # chart dimensions in the same projected space across render-resolution A/B.
    # Legacy pixel-sized diagnostics remain the default for existing captures.
    canvas_width, canvas_height = (2080, 2208) if normalized else (width, height)
    sx, sy = width / canvas_width, height / canvas_height
    scale = min(sx, sy)
    def point(p): return (round(p[0] * sx), round(p[1] * sy))
    def stroke(t): return max(1, round(t * scale))
    def line(a, b, c, t): cv2.line(image, point(a), point(b), c, stroke(t))
    def rectangle(a, b, c, t):
        cv2.rectangle(image, point(a), point(b), c, t if t < 0 else stroke(t))
    def text(value, pos, size, c, t):
        cv2.putText(image, value, point(pos), cv2.FONT_HERSHEY_SIMPLEX, size * scale, c, stroke(t), cv2.LINE_8)
    def arrow(a, b, c, t): cv2.arrowedLine(image, point(a), point(b), c, stroke(t))
    for x in range(0, canvas_width, 128):
        line((x, 0), (x, canvas_height - 1), (80, 80, 80), 2)
    for y in range(0, canvas_height, 128):
        line((0, y), (canvas_width - 1, y), (80, 80, 80), 2)
    left, right, top, bottom = projection
    cx = int(canvas_width * -left / (right - left))
    cy = int(canvas_height * -top / (bottom - top))
    rectangle((cx - 450, cy - 250), (cx + 450, cy + 250), color, -1)
    text(label, (cx - 300, cy + 40), 4, (255, 255, 255), 10)
    text('TOP', (cx - 100, cy - 320), 2, (255, 255, 255), 6)
    text('BOTTOM', (cx - 160, cy + 400), 2, (255, 255, 255), 6)
    arrow((cx - 600, cy), (cx - 850, cy), (0, 255, 255), 12)
    arrow((cx + 600, cy), (cx + 850, cy), (0, 255, 255), 12)
    if quality:
        # Head-locked high-chroma diagnostics above the separate client overlay.
        x0,y0=cx-650,cy-760
        rectangle((x0,y0),(cx+650,cy-100),(28,28,28),-1)
        colors=[(0,220,255),(255,255,0),(255,0,255),(0,255,80)]
        for i,c in enumerate(colors):
            y=y0+80+i*100
            text('HUD 0123456789 AaBb RGB + HP 100%',(x0+40,y),.7+i*.15,c,1)
            rectangle((x0+40,y+20),(x0+700,y+38),c,1)
            line((x0+740,y-25),(x0+1150,y+35),c,1)
        # Red/cyan and blue/yellow edge pairs at several source-pixel widths.
        for i,stripe in enumerate((1,2,4,8)):
            top=y0+490+i*32
            for x in range(x0+40,x0+1240,stripe):
                c=(0,0,255) if ((x-x0-40)//stripe)%2==0 else (255,255,0)
                # Inclusive pixel coordinates need a full normalized stripe
                # width; a one-pixel stripe must not stay one source pixel.
                if normalized:
                    start_x=round(x*sx)
                    end_x=max(start_x,round((x+stripe)*sx)-1)
                    cv2.rectangle(image,(start_x,round(top*sy)),(end_x,round((top+20)*sy)),c,-1)
                else:
                    rectangle((x,top),(x+stripe-1,top+20),c,-1)
    return image


def main():
    import glfw
    import openvr
    from OpenGL import GL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--quality', action='store_true', help='Add fine colored HUD text and saturated edge diagnostics')
    parser.add_argument('--normalized-chart', action='store_true', help='Keep projected chart scale fixed while rasterizing at each source resolution')
    parser.add_argument('--source-eye', type=int, nargs=2, metavar=('WIDTH','HEIGHT'), help='Explicit per-eye source texture size; avoids SteamVR automatic-resolution drift')
    parser.add_argument('--pulse', action='store_true', help='Add a changing 10 Hz counter to detect stale imported pixels; separate from FPS measurement')
    parser.add_argument('--stop-file', type=Path, help='End gracefully when this file appears; duration remains a hard limit')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600:
        parser.error('Use 1-600 seconds')
    if args.stop_file and args.stop_file.exists():
        parser.error('Stop file already exists; use a fresh path')
    if args.normalized_chart and args.pulse:
        parser.error('Normalized chart does not support the pixel-sized pulse patch')
    if args.source_eye and any(not 32 <= v <= 8192 for v in args.source_eye):
        parser.error('Source axes must be 32-8192 pixels')
    root = Path(args.out); root.mkdir(parents=True, exist_ok=True)
    window = None
    system = openvr.init(openvr.VRApplication_Scene)
    try:
        if not glfw.init():
            raise RuntimeError('GLFW initialization failed')
        glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
        window = glfw.create_window(64, 64, 'Quest3 PyroWave stereo check', None, None)
        if not window:
            raise RuntimeError('GLFW context creation failed')
        glfw.make_context_current(window)
        compositor = openvr.VRCompositor()
        recommended = list(system.getRecommendedRenderTargetSize())
        width, height = args.source_eye or recommended
        if max(width,height) > int(GL.glGetIntegerv(GL.GL_MAX_TEXTURE_SIZE)):
            raise RuntimeError('Requested source exceeds GL texture limit')
        textures = []
        pulse_textures = []
        projections = []
        for eye, label, color in [(openvr.Eye_Left, 'LEFT', (32, 32, 200)),
                                  (openvr.Eye_Right, 'RIGHT', (200, 64, 32))]:
            projection = system.getProjectionRaw(eye); projections.append(projection)
            image = eye_pattern(width, height, label, color, projection, args.quality, args.normalized_chart)
            cv2.imwrite(str(root / f'{label.lower()}-reference.png'), image)
            texture = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, width, height, 0,
                            GL.GL_RGB, GL.GL_UNSIGNED_BYTE, np.ascontiguousarray(image[:, :, ::-1]))
            vr_texture = openvr.Texture_t()
            vr_texture.handle = int(texture)
            vr_texture.eType = openvr.TextureType_OpenGL
            vr_texture.eColorSpace = openvr.ColorSpace_Gamma
            textures.append((eye, vr_texture))
            left, right, top, bottom = projection
            cx = int(width * -left / (right - left))
            cy = int(height * -top / (bottom - top))
            pulse_textures.append((texture, max(0, min(width - 512, cx - 256)),
                                   max(0, min(height - 112, cy - 950))))
        GL.glFinish()
        bounds = openvr.VRTextureBounds_t()
        bounds.uMin, bounds.uMax, bounds.vMin, bounds.vMax = 0, 1, 1, 0
        poses = (openvr.TrackedDevicePose_t * openvr.k_unMaxTrackedDeviceCount)()
        frames = 0; start = time.monotonic(); started_unix_ns = time.time_ns()
        pulse_tick = -1; pulse_events = []
        (root / 'ready.json').write_text(json.dumps({'started_unix_ns':started_unix_ns,
            'source_eye_size':[width,height], 'quality_chart':args.quality,
            'steamvr_recommended_eye_size':recommended,
            'source_eye_explicit':args.source_eye is not None,
            'normalized_chart':args.normalized_chart,
            'chart_reference_size':[2080,2208] if args.normalized_chart else None,
            'pulse':args.pulse}),encoding='utf-8')
        while time.monotonic() - start < args.seconds and not (args.stop_file and args.stop_file.exists()):
            compositor.waitGetPoses(poses, None)
            if args.pulse:
                tick = int((time.monotonic() - start) * 10)
                if tick != pulse_tick:
                    pulse_tick = tick
                    patch = np.full((112, 512, 3), (0, 96, 160 if tick % 20 < 10 else 0), dtype=np.uint8)
                    cv2.putText(patch, f'T{tick:06d}', (20, 78), cv2.FONT_HERSHEY_SIMPLEX,
                                2, (255, 255, 255), 4, cv2.LINE_8)
                    for texture, x, y in pulse_textures:
                        GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
                        GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, x, y, 512, 112,
                                          GL.GL_RGB, GL.GL_UNSIGNED_BYTE, patch)
                    pulse_events.append({'tick':tick, 'uploaded_unix_ns':time.time_ns()})
            for eye, texture in textures:
                compositor.submit(eye, texture, bounds)
            GL.glFlush(); frames += 1
        result = {'frames_submitted': frames, 'seconds': time.monotonic() - start,
                  'source_eye_size': [width, height], 'projections': projections,
                  'steamvr_recommended_eye_size':recommended,
                  'source_eye_explicit':args.source_eye is not None,
                  'started_unix_ns':started_unix_ns,'ended_unix_ns':time.time_ns(),
                  'left_label': 'LEFT', 'right_label': 'RIGHT', 'quality_chart':args.quality,
                  'normalized_chart':args.normalized_chart,
                  'chart_reference_size':[2080,2208] if args.normalized_chart else None,
                  'pulse': args.pulse, 'pulse_events':pulse_events,
                  'stop_reason': 'stop_file' if args.stop_file and args.stop_file.exists() else 'duration',
                  'note': 'Submission count is not decoded or displayed frame rate.'}
        (root / 'scene.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        openvr.shutdown()
        if window:
            glfw.destroy_window(window)
        glfw.terminate()


if __name__ == '__main__':
    main()
