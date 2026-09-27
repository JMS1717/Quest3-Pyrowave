"""SteamVR scene app that shows the benchmark panel head-locked in both eyes.

Runs on the PC:  python -m xrbench.scene_app --label A07 --seconds 60 --out C:\\...\\A07
--selftest renders without SteamVR (writes the eye image and per-frame update timing).
"""
import argparse
import csv
import time
from pathlib import Path

import cv2
import numpy as np

from . import patterns as p


EDGE_MARGIN = 150   # px of texture kept clear around the panel; see optical_center_origin


def optical_center_origin(eye_w, eye_h, projection):
    """Panel origin that puts the panel's center on the eye's optical axis.

    projection = OpenVR getProjectionRaw(eye): tangents (left, right, top, bottom), with top-down
    texture rows. Eye frustums are asymmetric, so the axis is not the texture center; placing the
    panel on each eye's own axis puts it at optical infinity, so both eyes fuse it (correct overlap).

    The axis placement only fits while the eye texture is big enough for it. At 2048/eye the right
    eye's panel wants a negative origin, and clamping it to 0 put the panel flush against the
    texture edge -- which the runtime's visible FOV crops, taking the ArUco markers with it. In
    one session every 2048/eye capture found 0 of 4 markers and could not be rectified at all, while
    every 2560/eye capture found 4 of 4. So keep EDGE_MARGIN px clear on each side: 2560/eye is
    unaffected (its tightest margin is already 156 px), and smaller textures give up exact axis
    placement, which costs stereo overlap but keeps the panel measurable. The panel is still pasted
    1:1 -- resampling it to fit would soften the very detail the metrics are there to measure."""
    left, right, top, bottom = projection
    axis_x = eye_w * (-left) / (right - left)
    axis_y = eye_h * (-top) / (bottom - top)
    ox = int(round(axis_x - p.PANEL_W / 2))
    oy = int(round(axis_y - p.PANEL_H / 2))
    margin_x = min(EDGE_MARGIN, (eye_w - p.PANEL_W) // 2)
    margin_y = min(EDGE_MARGIN, (eye_h - p.PANEL_H) // 2)
    ox = min(max(ox, margin_x), eye_w - p.PANEL_W - margin_x)
    oy = min(max(oy, margin_y), eye_h - p.PANEL_H - margin_y)
    return ox, oy


def build_eye_image(eye_w, eye_h, static, origin=None):
    """Panel pasted 1:1 (no resampling), at `origin` or the texture center; returns image and origin."""
    if eye_w < p.PANEL_W or eye_h < p.PANEL_H:
        raise ValueError(f"eye {eye_w}x{eye_h} smaller than panel {p.PANEL_W}x{p.PANEL_H}")
    eye = np.full((eye_h, eye_w, 3), p.BACKGROUND, dtype=np.uint8)
    ox, oy = origin if origin is not None else ((eye_w - p.PANEL_W) // 2, (eye_h - p.PANEL_H) // 2)
    eye[oy:oy + p.PANEL_H, ox:ox + p.PANEL_W] = static
    return eye, (ox, oy)


def dynamic_updates(n, origin, layout="panel"):
    """(x, y, rgb_patch) for every region that changes on frame n, in eye-image pixels."""
    ox, oy = origin
    out = []
    for name, patch in (("counter", p.counter_patch(n, layout)), ("motion", p.motion_patch(n, layout))):
        x0, y0, _, _ = p.LAYOUTS[layout][name]
        out.append((ox + x0, oy + y0, np.ascontiguousarray(patch[:, :, ::-1])))
    return out


LOAD_TILE = 512          # noise texture size (texels)
LOAD_BLOCK_PX = 8        # screen pixels per noise texel, like the panel's motion band
LOAD_SPEED_PX = 5        # scroll per frame; not a multiple of 8 or 16 so blocks never align to macroblocks
LOAD_SEED = 4321


def load_noise_tile():
    """Deterministic RGB block noise that surrounds the panel so the encoder must spend its full
    bitrate budget (an almost-static scene lets CBR encoders coast far below target)."""
    rng = np.random.default_rng(LOAD_SEED)
    return rng.integers(0, 256, size=(LOAD_TILE, LOAD_TILE, 3), dtype=np.uint8)


def load_uv_rect(n, eye_w, eye_h):
    """Texture coordinates (u0, v0, u1, v1) for the full-eye noise quad on frame n (GL_REPEAT)."""
    span = LOAD_TILE * LOAD_BLOCK_PX
    shift = n * LOAD_SPEED_PX
    u0, v0 = shift / span, (shift * 0.6) / span
    return u0, v0, u0 + eye_w / span, v0 + eye_h / span


def selftest(out_dir, eye_w, eye_h, frames):
    static = p.build_static("SELFTEST")
    eye, origin = build_eye_image(eye_w, eye_h, static)
    cv2.imwrite(str(out_dir / "eye_reference.png"), eye)
    start = time.perf_counter()
    for n in range(frames):
        for x, y, patch in dynamic_updates(n, origin):
            eye[y:y + patch.shape[0], x:x + patch.shape[1]] = patch[:, :, ::-1]
    per_frame_ms = (time.perf_counter() - start) * 1000 / frames
    print(f"selftest eye {eye_w}x{eye_h}, panel origin {origin}, update {per_frame_ms:.2f} ms/frame")
    return per_frame_ms


def _texture(GL, width, height, pixels_rgb, repeat=False):
    texture = GL.glGenTextures(1)
    GL.glBindTexture(GL.GL_TEXTURE_2D, texture)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
    wrap = GL.GL_REPEAT if repeat else GL.GL_CLAMP_TO_EDGE
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, wrap)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, wrap)
    GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
    GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, width, height, 0, GL.GL_RGB,
                    GL.GL_UNSIGNED_BYTE, pixels_rgb)
    return texture


def _quad(GL, x0, y0, x1, y1, u0, v0, u1, v1):
    GL.glBegin(GL.GL_QUADS)
    GL.glTexCoord2f(u0, v0); GL.glVertex2f(x0, y0)
    GL.glTexCoord2f(u1, v0); GL.glVertex2f(x1, y0)
    GL.glTexCoord2f(u1, v1); GL.glVertex2f(x1, y1)
    GL.glTexCoord2f(u0, v1); GL.glVertex2f(x0, y1)
    GL.glEnd()


def run_vr(label, seconds, out_dir, load=True, photo=None):
    """Each frame, per eye: draw scrolling full-eye noise (encoder load), then the panel 1:1 on the
    eye's optical axis. Rows are top-down throughout (texture row 0 = image row 0) and the
    submitted bounds flip v, so image row 0 is displayed at the top."""
    import glfw
    import openvr
    from OpenGL import GL

    vr_system = openvr.init(openvr.VRApplication_Scene)
    compositor = openvr.VRCompositor()
    eye_w, eye_h = vr_system.getRecommendedRenderTargetSize()
    if eye_w < p.PANEL_W or eye_h < p.PANEL_H:
        raise ValueError(f"eye {eye_w}x{eye_h} smaller than panel {p.PANEL_W}x{p.PANEL_H}")

    if not glfw.init():
        raise RuntimeError("glfw init failed")
    glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
    window = glfw.create_window(64, 64, "xrbench", None, None)
    glfw.make_context_current(window)

    if photo is not None:
        mosaic = cv2.imread(str(photo))
        if mosaic is None:
            raise ValueError(f"cannot read photo mosaic {photo}")
        static = p.build_photo_static(mosaic, label)
        layout = "photo"
    else:
        static = p.build_static(label)
        layout = "panel"
    # The analyzer reads this back as the reference, so the photo never has to be rebuilt.
    cv2.imwrite(str(out_dir / "panel_static.png"), static)
    panel_texture = _texture(GL, p.PANEL_W, p.PANEL_H, np.ascontiguousarray(static[:, :, ::-1]))
    noise_texture = _texture(GL, LOAD_TILE, LOAD_TILE, load_noise_tile(), repeat=True)

    eyes = []  # (openvr eye, fbo, openvr texture, panel origin)
    for vr_eye, name in ((openvr.Eye_Left, "left"), (openvr.Eye_Right, "right")):
        origin = optical_center_origin(eye_w, eye_h, vr_system.getProjectionRaw(vr_eye))
        eye_image, origin = build_eye_image(eye_w, eye_h, static, origin)
        cv2.imwrite(str(out_dir / f"eye_reference_{name}.png"), eye_image)
        color = _texture(GL, eye_w, eye_h, None)
        fbo = GL.glGenFramebuffers(1)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, color, 0)
        if GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER) != GL.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError("eye framebuffer incomplete")
        vr_texture = openvr.Texture_t()
        vr_texture.handle = int(color)
        vr_texture.eType = openvr.TextureType_OpenGL
        # Plain RGBA8 + ColorSpace_Gamma: code values reach the compositor unchanged (no sRGB
        # decode), so the 8-bit ramps really contain every level.
        vr_texture.eColorSpace = openvr.ColorSpace_Gamma
        eyes.append((vr_eye, fbo, vr_texture, origin))
    GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)

    bounds = openvr.VRTextureBounds_t()
    bounds.uMin, bounds.uMax, bounds.vMin, bounds.vMax = 0.0, 1.0, 1.0, 0.0  # image rows are top-down

    poses = (openvr.TrackedDevicePose_t * openvr.k_unMaxTrackedDeviceCount)()
    n = 0
    deadline = time.perf_counter() + seconds
    with open(out_dir / "frames.csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["frame", "t_wait_done", "t_submitted"])
        try:
            while time.perf_counter() < deadline:
                compositor.waitGetPoses(poses, None)
                t_wait = time.perf_counter()
                # compositor.submit() shares this GL context and leaves its own state behind;
                # set everything this frame depends on explicitly.
                GL.glBindBuffer(GL.GL_PIXEL_UNPACK_BUFFER, 0)
                GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
                GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, 0)
                GL.glPixelStorei(GL.GL_UNPACK_SKIP_ROWS, 0)
                GL.glPixelStorei(GL.GL_UNPACK_SKIP_PIXELS, 0)
                GL.glBindTexture(GL.GL_TEXTURE_2D, panel_texture)
                for x, y, patch in dynamic_updates(n, (0, 0), layout):
                    GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, x, y, patch.shape[1], patch.shape[0],
                                       GL.GL_RGB, GL.GL_UNSIGNED_BYTE, patch)
                GL.glUseProgram(0)
                GL.glDisable(GL.GL_BLEND)
                GL.glDisable(GL.GL_DEPTH_TEST)
                GL.glDisable(GL.GL_SCISSOR_TEST)
                GL.glDisable(GL.GL_CULL_FACE)
                GL.glEnable(GL.GL_TEXTURE_2D)
                GL.glColor4f(1.0, 1.0, 1.0, 1.0)
                GL.glTexEnvi(GL.GL_TEXTURE_ENV, GL.GL_TEXTURE_ENV_MODE, GL.GL_REPLACE)
                GL.glMatrixMode(GL.GL_PROJECTION)
                GL.glLoadIdentity()
                GL.glOrtho(0, eye_w, 0, eye_h, -1, 1)
                GL.glMatrixMode(GL.GL_MODELVIEW)
                GL.glLoadIdentity()
                for vr_eye, fbo, vr_texture, (ox, oy) in eyes:
                    GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
                    GL.glViewport(0, 0, eye_w, eye_h)
                    if load:
                        GL.glBindTexture(GL.GL_TEXTURE_2D, noise_texture)
                        _quad(GL, 0, 0, eye_w, eye_h, *load_uv_rect(n, eye_w, eye_h))
                    else:
                        GL.glClearColor(p.BACKGROUND / 255, p.BACKGROUND / 255, p.BACKGROUND / 255, 1)
                        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
                    GL.glBindTexture(GL.GL_TEXTURE_2D, panel_texture)
                    _quad(GL, ox, oy, ox + p.PANEL_W, oy + p.PANEL_H, 0.0, 0.0, 1.0, 1.0)
                    GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
                    compositor.submit(vr_eye, vr_texture, bounds)
                GL.glFlush()
                writer.writerow([n, f"{t_wait:.6f}", f"{time.perf_counter():.6f}"])
                n += 1
        finally:
            openvr.shutdown()
            glfw.terminate()
    origins = {("left", "right")[i]: e[3] for i, e in enumerate(eyes)}
    print(f"{label}: {n} frames in {seconds}s, eye {eye_w}x{eye_h}, load={'noise' if load else 'off'}, "
          f"panel origins {origins}")
    return n


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--out", required=True)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--no-load", action="store_true", help="plain background instead of moving noise")
    parser.add_argument("--photo", default=None, help="1200x640 mosaic PNG: use the photo layout")
    parser.add_argument("--eye", type=int, nargs=2, default=(2560, 2560), help="selftest eye size")
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.selftest:
        selftest(out_dir, *args.eye, frames=720)
    else:
        run_vr(args.label, args.seconds, out_dir, load=not args.no_load, photo=args.photo)


if __name__ == "__main__":
    main()
