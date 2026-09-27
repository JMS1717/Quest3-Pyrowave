"""Measure decoded quality from a tapped bitstream, against the reference the scene rendered.

This replaces the screen-capture path for image quality. That path went
decoded frame -> compositor -> adb screencap -> ArUco homography -> rectified panel, and every
one of those stages added error: it reported PSNR ~20 dB on a synthetic pattern and, worse,
showed no trend across a 3x bitrate range, so it was measuring itself rather than the codec.

Here the frame comes from the encoded stream the server actually sent, decoded offline. The
panel sits at the exact pixel offset `scene_app` pasted it at, so there is no registration step
at all -- and the panel carries its own frame number, so the reference is rendered for that exact
frame rather than assumed. What is left is the codec.
"""
import re

import cv2
import numpy as np

from . import analyze as a
from . import patterns as p

REGIONS = ("detail", "motion", "bars", "gradient_gray", "gradient_rgb", "gradient_dark")
PHOTO_REGIONS = ("photo", "text", "fence", "motion", "gradient_dark")

_ORIGINS = re.compile(r"'(left|right)':\s*\((\d+),\s*(\d+)\)")


def parse_origins(scene_stdout):
    """[(x, y), ...] per eye from scene_app's summary line, or None if it has none.

    Read rather than recomputed: the origin depends on the eye texture size and the runtime's
    frustum, and a wrong guess silently shifts the crop and ruins every number downstream."""
    found = dict((m.group(1), (int(m.group(2)), int(m.group(3))))
                 for m in _ORIGINS.finditer(scene_stdout))
    if "left" not in found or "right" not in found:
        return None
    return [found["left"], found["right"]]


def split_eyes(frame):
    """The two eye images of a side-by-side encode frame."""
    half = frame.shape[1] // 2
    return frame[:, :half], frame[:, half:]


def crop_panel(eye, origin):
    ox, oy = origin
    return eye[oy:oy + p.PANEL_H, ox:ox + p.PANEL_W]


def measure_frame(frame, origins, label, static=None, layout="panel"):
    """Per-eye quality for one decoded side-by-side frame.

    Each eye reports the frame number read out of the panel, so a caller can tell which reference
    was used and spot duplicated or skipped frames. An unreadable counter yields None rather than
    a guess -- comparing against the wrong reference would produce a plausible-looking number.

    `static` is the panel the scene wrote (`panel_static.png`); for the photo layout it must be
    supplied, since the photo is not reproducible from the label."""
    if static is None:
        if layout != "panel":
            raise ValueError("the photo layout needs the scene's panel_static.png as `static`")
        static = p.build_static(label)
    names = PHOTO_REGIONS if layout == "photo" else REGIONS
    out = []
    for eye, origin in zip(split_eyes(frame), origins):
        panel = crop_panel(eye, origin)
        counter = p.decode_counter(panel) if panel.shape[:2] == (p.PANEL_H, p.PANEL_W) else None
        if counter is None:
            out.append({"counter": None, "psnr": None, "ssim": None, "regions": {}})
            continue
        ref = p.render_frame(static, counter, layout)
        regions = {}
        for name in names:
            x0, y0, x1, y1 = p.LAYOUTS[layout][name]
            regions[name] = a.peak_signal_noise_ratio(panel[y0:y1, x0:x1], ref[y0:y1, x0:x1])
        out.append({"counter": counter,
                    "psnr": a.peak_signal_noise_ratio(panel, ref),
                    "ssim": a.structural_similarity(panel, ref),
                    "regions": regions})
    return out


def measure_files(paths, origins, label, static=None, layout="panel"):
    """measure_frame over decoded PNGs, flattened, skipping frames that could not be read."""
    rows = []
    for path in paths:
        frame = cv2.imread(str(path))
        if frame is None:
            continue
        for eye_index, eye in enumerate(measure_frame(frame, origins, label, static, layout)):
            if eye["counter"] is not None:
                rows.append({"file": str(path), "eye": eye_index, **eye})
    return rows
