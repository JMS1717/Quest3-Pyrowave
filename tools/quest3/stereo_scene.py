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


def eye_pattern(width, height, label, color, projection, quality=False):
    image = np.full((height, width, 3), 24, dtype=np.uint8)
    for x in range(0, width, 128):
        cv2.line(image, (x, 0), (x, height - 1), (80, 80, 80), 2)
    for y in range(0, height, 128):
        cv2.line(image, (0, y), (width - 1, y), (80, 80, 80), 2)
    left, right, top, bottom = projection
    cx = int(width * -left / (right - left))
    cy = int(height * -top / (bottom - top))
    cv2.rectangle(image, (cx - 450, cy - 250), (cx + 450, cy + 250), color, -1)
    cv2.putText(image, label, (cx - 300, cy + 40), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 255), 10)
    cv2.putText(image, 'TOP', (cx - 100, cy - 320), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 6)
    cv2.putText(image, 'BOTTOM', (cx - 160, cy + 400), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 6)
    cv2.arrowedLine(image, (cx - 600, cy), (cx - 850, cy), (0, 255, 255), 12)
    cv2.arrowedLine(image, (cx + 600, cy), (cx + 850, cy), (0, 255, 255), 12)
    if quality:
        # Head-locked high-chroma diagnostics above the separate client overlay.
        x0,y0=cx-650,cy-760
        cv2.rectangle(image,(x0,y0),(cx+650,cy-100),(28,28,28),-1)
        colors=[(0,220,255),(255,255,0),(255,0,255),(0,255,80)]
        for i,c in enumerate(colors):
            y=y0+80+i*100
            cv2.putText(image,'HUD 0123456789 AaBb RGB + HP 100%',(x0+40,y),
                        cv2.FONT_HERSHEY_SIMPLEX,.7+i*.15,c,1,cv2.LINE_8)
            cv2.rectangle(image,(x0+40,y+20),(x0+700,y+38),c,1)
            cv2.line(image,(x0+740,y-25),(x0+1150,y+35),c,1)
        # Red/cyan and blue/yellow edge pairs at several source-pixel widths.
        for i,stripe in enumerate((1,2,4,8)):
            top=y0+490+i*32
            for x in range(x0+40,x0+1240,stripe):
                c=(0,0,255) if ((x-x0-40)//stripe)%2==0 else (255,255,0)
                cv2.rectangle(image,(x,top),(x+stripe-1,top+20),c,-1)
    return image


def main():
    import glfw
    import openvr
    from OpenGL import GL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--seconds', type=float, default=30)
    parser.add_argument('--quality', action='store_true', help='Add fine colored HUD text and saturated edge diagnostics')
    parser.add_argument('--stop-file', type=Path, help='End gracefully when this file appears; duration remains a hard limit')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600:
        parser.error('Use 1-600 seconds')
    if args.stop_file and args.stop_file.exists():
        parser.error('Stop file already exists; use a fresh path')
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
        width, height = system.getRecommendedRenderTargetSize()
        textures = []
        projections = []
        for eye, label, color in [(openvr.Eye_Left, 'LEFT', (32, 32, 200)),
                                  (openvr.Eye_Right, 'RIGHT', (200, 64, 32))]:
            projection = system.getProjectionRaw(eye); projections.append(projection)
            image = eye_pattern(width, height, label, color, projection, args.quality)
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
        GL.glFinish()
        bounds = openvr.VRTextureBounds_t()
        bounds.uMin, bounds.uMax, bounds.vMin, bounds.vMax = 0, 1, 1, 0
        poses = (openvr.TrackedDevicePose_t * openvr.k_unMaxTrackedDeviceCount)()
        frames = 0; start = time.monotonic(); started_unix_ns = time.time_ns()
        (root / 'ready.json').write_text(json.dumps({'started_unix_ns':started_unix_ns,
            'source_eye_size':[width,height], 'quality_chart':args.quality}),encoding='utf-8')
        while time.monotonic() - start < args.seconds and not (args.stop_file and args.stop_file.exists()):
            compositor.waitGetPoses(poses, None)
            for eye, texture in textures:
                compositor.submit(eye, texture, bounds)
            GL.glFlush(); frames += 1
        result = {'frames_submitted': frames, 'seconds': time.monotonic() - start,
                  'source_eye_size': [width, height], 'projections': projections,
                  'started_unix_ns':started_unix_ns,'ended_unix_ns':time.time_ns(),
                  'left_label': 'LEFT', 'right_label': 'RIGHT', 'quality_chart':args.quality,
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
