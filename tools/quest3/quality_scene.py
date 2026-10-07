"""Deterministic image-quality scene for SteamVR: game-like content, optionally panning.

The stereo chart (stereo_scene.py) is flat colour and large text, which any codec compresses
well. This scene instead fills each eye with content that is hard for an intra wavelet codec and
that resembles what players complained about: stone/brick texture, fine multicolour HUD text,
thin lines at many angles (chains, rails), saturated colour edges (purple sky against brown cloth),
smooth gradients, foliage-like noise, a zone plate and a Siemens star.

The world texture is rendered 1:1 onto the SteamVR source texture, so the PC downsample to the
stream size is in the loop exactly as it is for a game. --pan-deg-s pans the world horizontally
at the angular speed of a head turn, so every frame changes; PyroWave codes each frame on its
own, so motion shows up as frame-to-frame flicker rather than lower per-frame quality. Frame offsets are a function of the submitted frame index, not
wall time, so a dumped clip can be reproduced.

Run with: python -m tools.quest3.quality_scene --out <dir> --seconds 60 [--pan-deg-s 30]
Requires: pip install numpy opencv-python openvr glfw PyOpenGL
"""
import argparse
import json
import math
import os
import time
from pathlib import Path

import cv2
import numpy as np

TILE = 1024


def value_noise(rng, height, width, octaves=6, base=8):
    """Sum of bilinearly upsampled random grids: a cheap fractal texture."""
    out = np.zeros((height, width), np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        cells = base * 2 ** o
        grid = rng.random((cells + 1, cells + 1)).astype(np.float32)
        out += amp * cv2.resize(grid, (width, height), interpolation=cv2.INTER_CUBIC)
        total += amp
        amp *= 0.55
    out /= total
    return (out - out.min()) / max(1e-6, out.max() - out.min())


def tile_brick(rng):
    n = value_noise(rng, TILE, TILE, 7, 6)
    fine = value_noise(rng, TILE, TILE, 3, 128)
    base = 0.55 + 0.3 * n + 0.15 * fine
    img = np.stack([base * 0.78, base * 0.82, base * 0.86], -1)  # BGR, warm
    mortar = np.zeros((TILE, TILE), np.float32)
    for row, y in enumerate(range(0, TILE, 64)):
        mortar[y:y + 5, :] = 1
        shift = 64 if row % 2 else 0
        for x in range(shift, TILE, 128):
            mortar[y:y + 64, x:x + 5] = 1
    mortar = cv2.GaussianBlur(mortar, (3, 3), 0.8)
    img = img * (1 - 0.45 * mortar[..., None])
    spots = (value_noise(rng, TILE, TILE, 4, 64) > 0.72).astype(np.float32)
    img = img * (1 - 0.25 * cv2.GaussianBlur(spots, (5, 5), 1.2)[..., None])
    return np.clip(img * 255, 0, 255).astype(np.uint8)


def tile_text(rng):
    img = np.full((TILE, TILE, 3), 22, np.uint8)
    colors = [(255, 255, 255), (0, 220, 255), (255, 255, 0), (255, 0, 255), (0, 255, 80),
              (60, 60, 255), (255, 128, 0)]
    y = 30
    for i, scale in enumerate((0.35, 0.4, 0.5, 0.6, 0.75, 0.9, 1.1)):
        for j in range(3):
            c = colors[(i + j) % len(colors)]
            cv2.putText(img, 'HP 100% AMMO 30/90 abcdefghijklm 0123456789', (12, y),
                        cv2.FONT_HERSHEY_SIMPLEX, scale, c, 1, cv2.LINE_AA)
            y += int(34 * scale) + 10
    # Dark text on a light panel, as menus and signs have.
    cv2.rectangle(img, (10, y), (TILE - 10, TILE - 10), (200, 210, 215), -1)
    y += 30
    while y < TILE - 20:
        cv2.putText(img, 'The quick brown fox jumps over the lazy dog 1234567890', (20, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (30, 30, 30), 1, cv2.LINE_AA)
        y += 22
    return img


def tile_lines(rng):
    sky = np.linspace(0, 1, TILE, dtype=np.float32)[:, None] * np.ones((1, TILE), np.float32)
    img = np.stack([0.62 + 0.2 * sky, 0.45 + 0.15 * sky, 0.55 + 0.1 * sky], -1)  # purple sky (BGR)
    img = (np.clip(img, 0, 1) * 255).astype(np.uint8)
    for k in range(48):
        angle = math.pi * k / 48
        x0, y0 = TILE / 2, TILE / 2
        dx, dy = math.cos(angle) * TILE * 0.7, math.sin(angle) * TILE * 0.7
        color = (40, 40, 40) if k % 2 else (235, 235, 235)
        cv2.line(img, (int(x0 - dx), int(y0 - dy)), (int(x0 + dx), int(y0 + dy)), color,
                 1 + (k % 3), cv2.LINE_AA)
    # Chain-like rows of small rings.
    for row in range(6):
        for x in range(20, TILE - 20, 14):
            cv2.ellipse(img, (x, 80 + row * 160), (6, 4 if row % 2 else 3), 0, 0, 360,
                        (180, 180, 190), 1, cv2.LINE_AA)
    return img


def tile_color_edges(rng):
    img = np.zeros((TILE, TILE, 3), np.uint8)
    sky = np.linspace(0, 1, TILE // 2, dtype=np.float32)[:, None]
    sky_bgr = np.stack([0.75 - 0.1 * sky, 0.55 - 0.1 * sky, 0.62 - 0.05 * sky], -1)
    img[:TILE // 2] = (sky_bgr * 255).astype(np.uint8).repeat(TILE, 1)
    # Brown cloth shapes against the sky, like a hanging garment.
    for k in range(5):
        x = 80 + k * 190
        pts = np.array([[x, 60], [x + 120, 60], [x + 100, 420], [x + 20, 420]], np.int32)
        cv2.fillPoly(img, [pts], (40, 70, 110), cv2.LINE_AA)
        cv2.line(img, (x + 10, 0), (x + 10, 60), (170, 170, 170), 2, cv2.LINE_AA)
        cv2.line(img, (x + 110, 0), (x + 110, 60), (170, 170, 170), 2, cv2.LINE_AA)
    pairs = [((0, 0, 255), (255, 0, 0)), ((0, 255, 255), (255, 0, 255)), ((0, 255, 0), (255, 0, 255)),
             ((0, 0, 200), (0, 200, 0)), ((255, 255, 255), (0, 0, 255)), ((30, 30, 30), (0, 255, 255))]
    y = TILE // 2 + 10
    for a, b in pairs:
        cell = 24
        for i in range(TILE // cell):
            for j in range(3):
                cv2.rectangle(img, (i * cell, y + j * cell), (i * cell + cell - 1, y + j * cell + cell - 1),
                              a if (i + j) % 2 else b, -1)
        cv2.putText(img, 'COLOR TEXT 123', (12, y + 3 * cell + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, b, 2,
                    cv2.LINE_AA)
        y += 3 * cell + 32
        if y > TILE - 3 * 24 - 32:
            break
    return img


def tile_gradients(rng):
    x = np.linspace(0, 1, TILE, dtype=np.float32)[None, :]
    y = np.linspace(0, 1, TILE, dtype=np.float32)[:, None]
    img = np.zeros((TILE, TILE, 3), np.float32)
    img[:TILE // 2] = np.stack([x * np.ones_like(y), 0.3 + 0.4 * x * np.ones_like(y),
                                1 - x * np.ones_like(y)], -1)[:TILE // 2]
    dark = 0.12 * x + 0.02
    img[TILE // 2:] = np.stack([dark, dark * 0.9, dark * 1.1], -1).repeat(TILE // 2, 0)
    # Soft spheres with highlights.
    for k, (cx, cy) in enumerate(((200, 700), (512, 820), (820, 700))):
        yy, xx = np.mgrid[0:TILE, 0:TILE]
        r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / 140
        shade = np.clip(1 - r, 0, 1) ** 0.6
        col = np.array([(0.2, 0.5, 0.9), (0.3, 0.8, 0.3), (0.9, 0.4, 0.3)][k], np.float32)
        img = img * (1 - (r < 1)[..., None]) + (shade[..., None] * col) * (r < 1)[..., None]
    return np.clip(img * 255, 0, 255).astype(np.uint8)


def tile_zone_plate(rng):
    yy, xx = np.mgrid[0:TILE, 0:TILE].astype(np.float32)
    r2 = (xx - TILE / 2) ** 2 + (yy - TILE / 2) ** 2
    # Instantaneous frequency reaches Nyquist (0.5 cycles/pixel) at the tile edge.
    zp = 0.5 + 0.5 * np.cos(math.pi * r2 / TILE)
    return (np.repeat(zp[..., None], 3, -1) * 255).astype(np.uint8)


def tile_star(rng):
    yy, xx = np.mgrid[0:TILE, 0:TILE].astype(np.float32)
    theta = np.arctan2(yy - TILE / 2, xx - TILE / 2)
    star = (np.sin(theta * 72) > 0).astype(np.float32)
    star = cv2.GaussianBlur(star, (3, 3), 0.6)
    img = np.stack([star * 0.9 + 0.05, star * 0.85 + 0.08, star * 0.8 + 0.1], -1)
    return (img * 255).astype(np.uint8)


def tile_foliage(rng):
    img = np.zeros((TILE, TILE, 3), np.uint8)
    img[:] = (30, 70, 35)
    for _ in range(9000):
        x, y = rng.integers(0, TILE, 2)
        a, b = rng.integers(3, 14), rng.integers(2, 6)
        g = int(rng.integers(70, 200))
        cv2.ellipse(img, (int(x), int(y)), (int(a), int(b)), float(rng.random() * 180), 0, 360,
                    (int(g * 0.35), g, int(g * 0.5)), -1, cv2.LINE_AA)
    return img


TILES = [tile_brick, tile_text, tile_lines, tile_color_edges, tile_gradients, tile_zone_plate,
         tile_star, tile_foliage]


def build_world(seed=1717, columns=6, rows=4):
    """columns x rows tiles; repeats the tile list so neighbours differ."""
    rng = np.random.default_rng(seed)
    made = [f(rng) for f in TILES]
    world = np.zeros((rows * TILE, columns * TILE, 3), np.uint8)
    for r in range(rows):
        for c in range(columns):
            world[r * TILE:(r + 1) * TILE, c * TILE:(c + 1) * TILE] = made[(r * 3 + c) % len(made)]
    return world


def main():
    import glfw
    import openvr
    from OpenGL import GL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--seconds', type=float, default=60)
    parser.add_argument('--pan-deg-s', type=float, default=0.0,
                        help='Horizontal pan speed as a head-turn angular rate; 0 is static')
    parser.add_argument('--source-eye', type=int, nargs=2, metavar=('WIDTH', 'HEIGHT'))
    parser.add_argument('--stop-file', type=Path)
    parser.add_argument('--control-file', type=Path,
                        help='JSON {"pan_deg_s": x} re-read every second to change speed live')
    args = parser.parse_args()
    root = Path(args.out); root.mkdir(parents=True, exist_ok=True)
    world = build_world()
    cv2.imwrite(str(root / 'world.png'), world)
    system = openvr.init(openvr.VRApplication_Scene)
    window = None
    try:
        if not glfw.init():
            raise RuntimeError('GLFW initialization failed')
        glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
        window = glfw.create_window(64, 64, 'Quest3 PyroWave quality scene', None, None)
        glfw.make_context_current(window)
        compositor = openvr.VRCompositor()
        recommended = list(system.getRecommendedRenderTargetSize())
        width, height = args.source_eye or recommended
        left, right, top, bottom = system.getProjectionRaw(openvr.Eye_Left)
        px_per_deg = width / math.degrees(math.atan(right) - math.atan(left))

        world_tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, world_tex)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        for p, v in ((GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR), (GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR),
                     (GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT), (GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, p, v)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, world.shape[1], world.shape[0], 0,
                        GL.GL_RGB, GL.GL_UNSIGNED_BYTE, np.ascontiguousarray(world[:, :, ::-1]))

        eyes = []
        for eye in (openvr.Eye_Left, openvr.Eye_Right):
            tex = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, width, height, 0, GL.GL_RGBA,
                            GL.GL_UNSIGNED_BYTE, None)
            fbo = GL.glGenFramebuffers(1)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
            GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, tex, 0)
            vr = openvr.Texture_t(); vr.handle = int(tex)
            vr.eType = openvr.TextureType_OpenGL; vr.eColorSpace = openvr.ColorSpace_Gamma
            eyes.append((eye, fbo, vr))
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        bounds = openvr.VRTextureBounds_t()
        bounds.uMin, bounds.uMax, bounds.vMin, bounds.vMax = 0, 1, 0, 1
        poses = (openvr.TrackedDevicePose_t * openvr.k_unMaxTrackedDeviceCount)()
        ww, wh = world.shape[1], world.shape[0]
        # Source pixels map 1:1 to world texels; the view starts at a fixed, content-rich spot.
        v0, v1 = (wh - height) / 2 / wh, (wh + height) / 2 / wh
        pan = args.pan_deg_s
        # A minimum frame time stands in for a game that renders below the refresh rate, so the
        # streamer presents fewer frames than the panel shows (Q3PW_SCENE_FRAME_MS, or the control
        # file's "frame_ms").
        frame_ms = float(os.environ.get('Q3PW_SCENE_FRAME_MS') or 0)
        # Redraw each eye this many times: a GPU-bound game, which also delays SteamVR's compositor.
        overdraw = max(1, int(os.environ.get('Q3PW_SCENE_OVERDRAW') or 1))
        frames = 0; offset_px = 0.0; start = time.monotonic(); last_control = start
        (root / 'ready.json').write_text(json.dumps({
            'source_eye_size': [width, height], 'px_per_deg': px_per_deg,
            'pan_deg_s': pan, 'world_size': [ww, wh], 'started_unix_ns': time.time_ns()}))
        log = (root / 'frames.csv').open('w')
        log.write('frame,unix_ns,offset_px,pan_deg_s\n')
        while time.monotonic() - start < args.seconds and not (args.stop_file and args.stop_file.exists()):
            compositor.waitGetPoses(poses, None)
            now = time.monotonic()
            if args.control_file and now - last_control > 1.0:
                last_control = now
                try:
                    control = json.loads(args.control_file.read_text())
                    pan = float(control['pan_deg_s'])
                    frame_ms = float(control.get('frame_ms', frame_ms))
                except (OSError, ValueError, KeyError):
                    pass
            for index, (eye, fbo, vr) in enumerate(eyes):
                # A small fixed disparity so the eyes are not byte-identical.
                u0 = (offset_px + 24 * index) / ww
                u1 = u0 + width / ww
                GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
                GL.glViewport(0, 0, width, height)
                GL.glEnable(GL.GL_TEXTURE_2D)
                GL.glBindTexture(GL.GL_TEXTURE_2D, world_tex)
                for _ in range(overdraw):
                    GL.glBegin(GL.GL_QUADS)
                    GL.glTexCoord2f(u0, v1); GL.glVertex2f(-1, -1)
                    GL.glTexCoord2f(u1, v1); GL.glVertex2f(1, -1)
                    GL.glTexCoord2f(u1, v0); GL.glVertex2f(1, 1)
                    GL.glTexCoord2f(u0, v0); GL.glVertex2f(-1, 1)
                    GL.glEnd()
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
            for eye, fbo, vr in eyes:
                compositor.submit(eye, vr, bounds)
            GL.glFlush()
            log.write(f'{frames},{time.time_ns()},{offset_px:.3f},{pan}\n')
            frames += 1
            # Advance by the frame period the compositor paces us at.
            offset_px = (offset_px + pan * px_per_deg / 207.0) % ww
            if frame_ms:
                while time.monotonic() - now < frame_ms / 1000:
                    pass
        log.close()
        (root / 'scene.json').write_text(json.dumps({'frames_submitted': frames,
                                                     'seconds': time.monotonic() - start}, indent=2))
    finally:
        openvr.shutdown()
        if window:
            glfw.destroy_window(window)
        glfw.terminate()


if __name__ == '__main__':
    main()
