"""Deterministic benchmark test panel.

Everything drawn is a pure function of (label, frame number), so the analyzer can rebuild the
exact panel the headset was sent and compare it with what the headset captured.
Only numpy and OpenCV are used; both the PC scene app and the Mac analyzer import this file.
"""
import cv2
import numpy as np

PANEL_W, PANEL_H = 1600, 1200
BACKGROUND = 40
COUNTER_BITS = 16
COUNTER_MAX = (1 << COUNTER_BITS) - 1
MOTION_SPEED_PX = 7
NOISE_SEED = 1234
NOISE_BLOCK_PX = 8

MARKER_SIZE = 120
MARKER_DICT = cv2.aruco.DICT_4X4_50
# Marker ids 0..3 at top-left, top-right, bottom-right, bottom-left; centers in panel pixels.
MARKER_CENTERS = np.array([[110, 110], [1490, 110], [1490, 1090], [110, 1090]], dtype=np.float32)

# (x0, y0, x1, y1) in panel pixels.
REGIONS = {
    "counter": (200, 40, 1400, 160),
    "gradient_gray": (200, 200, 1400, 330),
    "gradient_rgb": (200, 350, 1400, 480),
    "detail": (200, 500, 790, 1000),
    "bars": (810, 500, 1400, 620),
    "label": (810, 640, 1400, 740),
    "motion": (810, 760, 1400, 1000),
    "gradient_dark": (200, 1020, 1400, 1100),
}
STATIC_REGIONS = ("gradient_gray", "gradient_rgb", "gradient_dark", "detail", "bars")

# The "photo" layout: a natural image where the synthetic detail was, for image-quality A/Bs on
# content with foliage, hair, fences and text rather than a zone plate. Counter, markers, moving
# noise and the dark gradient keep their roles; the text and fence strips are drawn here so they
# are exactly reproducible. (x0, y0, x1, y1) in panel pixels.
PHOTO_REGIONS = {
    "counter": (200, 40, 1400, 160),
    "photo": (200, 180, 1400, 820),
    "text": (200, 840, 700, 1000),
    "fence": (720, 840, 1000, 1000),
    "motion": (1020, 840, 1400, 1000),
    "gradient_dark": (200, 1020, 1400, 1100),
}
LAYOUTS = {"panel": REGIONS, "photo": PHOTO_REGIONS}
PHOTO_STATIC_REGIONS = ("photo", "text", "fence", "gradient_dark")


def _ramp(width, lo, hi):
    """Integer ramp covering every code value from lo to hi exactly once-or-more, left to right."""
    levels = hi - lo + 1
    return (lo + np.floor(np.arange(width) * levels / width)).astype(np.uint8)


def _box(name, layout="panel"):
    x0, y0, x1, y1 = LAYOUTS[layout][name]
    return slice(y0, y1), slice(x0, x1), x1 - x0, y1 - y0


def _zone_plate(w, h):
    y, x = np.mgrid[0:h, 0:w].astype(np.float64)
    cx, cy = w / 2.0, h / 2.0
    r2 = (x - cx) ** 2 + (y - cy) ** 2
    zone = 0.5 + 0.5 * np.cos(np.pi * r2 / (2.0 * max(w, h)))
    img = (zone * 255).astype(np.uint8)
    # Right quarter: 1 px and 2 px line pairs for ringing/softening.
    q = w * 3 // 4
    img[:, q:] = np.where((np.arange(w - q) // (1 + (np.arange(h)[:, None] > h // 2))) % 2 == 0, 255, 0)
    return img


def _noise_base():
    ys, xs, w, h = _box("motion")
    rng = np.random.default_rng(NOISE_SEED)
    # 8 px random blocks: high entropy for the encoder, yet coarse enough to survive the
    # resampling every headset capture goes through, so fidelity scores stay meaningful.
    block = NOISE_BLOCK_PX
    coarse = rng.integers(0, 256, size=(-(-h // block), -(-w * 2 // block)), dtype=np.uint8)
    return np.repeat(np.repeat(coarse, block, axis=0), block, axis=1)[:h, :w * 2]


_NOISE = _noise_base()


def build_static(label=""):
    img = np.full((PANEL_H, PANEL_W, 3), BACKGROUND, dtype=np.uint8)

    ys, xs, w, h = _box("gradient_gray")
    img[ys, xs] = _ramp(w, 0, 255)[None, :, None]

    ys, xs, w, h = _box("gradient_rgb")
    ramp = _ramp(w, 0, 255)
    stripe = h // 3
    for c in range(3):
        img[ys.start + c * stripe: ys.start + (c + 1) * stripe, xs, :] = 0
        img[ys.start + c * stripe: ys.start + (c + 1) * stripe, xs, 2 - c] = ramp  # RGB order on screen

    ys, xs, w, h = _box("gradient_dark")
    img[ys, xs] = _ramp(w, 0, 31)[None, :, None]

    ys, xs, w, h = _box("detail")
    img[ys, xs] = _zone_plate(w, h)[:, :, None]

    ys, xs, w, h = _box("bars")
    colors = [(192, 192, 192), (192, 192, 0), (0, 192, 192), (0, 192, 0),
              (192, 0, 192), (192, 0, 0), (0, 0, 192)]  # RGB
    bw = w / len(colors)
    for i, (r, g, b) in enumerate(colors):
        img[ys, xs.start + int(i * bw): xs.start + int((i + 1) * bw)] = (b, g, r)

    ys, xs, w, h = _box("label")
    if label:
        cv2.putText(img, label, (xs.start + 10, ys.stop - 25), cv2.FONT_HERSHEY_SIMPLEX,
                    2.0, (255, 255, 255), 4, cv2.LINE_AA)

    dictionary = cv2.aruco.getPredefinedDictionary(MARKER_DICT)
    for marker_id, (cx, cy) in enumerate(MARKER_CENTERS.astype(int)):
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, MARKER_SIZE)
        half = MARKER_SIZE // 2
        pad = 16  # white quiet zone so detection survives compression
        img[cy - half - pad: cy + half + pad, cx - half - pad: cx + half + pad] = 255
        img[cy - half: cy + half, cx - half: cx + half] = marker[:, :, None]
    return img  # BGR, like everything OpenCV


def counter_value(n):
    return n % (COUNTER_MAX + 1)


def counter_patch(n, layout="panel"):
    """Two rows of cells: the 16 bits MSB-first, then their complement (a self-check)."""
    ys, xs, w, h = _box("counter", layout)
    value = counter_value(n)
    bits = [(value >> (COUNTER_BITS - 1 - i)) & 1 for i in range(COUNTER_BITS)]
    cell_w, cell_h = w // COUNTER_BITS, h // 2
    patch = np.zeros((h, w), dtype=np.uint8)
    for i, bit in enumerate(bits):
        patch[:cell_h, i * cell_w:(i + 1) * cell_w] = 255 * bit
        patch[cell_h:, i * cell_w:(i + 1) * cell_w] = 255 * (1 - bit)
    return np.repeat(patch[:, :, None], 3, axis=2)


def motion_patch(n, layout="panel"):
    ys, xs, w, h = _box("motion", layout)
    offset = (n * MOTION_SPEED_PX) % _NOISE.shape[1]
    band = np.roll(_NOISE, -offset, axis=1)[:h, :w]  # the photo layout's band is shorter
    return np.repeat(band[:, :, None], 3, axis=2)


def render_frame(static, n, layout="panel"):
    frame = static.copy()
    ys, xs, _, _ = _box("counter", layout)
    frame[ys, xs] = counter_patch(n, layout)
    ys, xs, _, _ = _box("motion", layout)
    frame[ys, xs] = motion_patch(n, layout)
    return frame


def compose_photo(images, width=1200, height=640):
    """A width x height BGR mosaic of the given images in a 2x2 grid, each cover-cropped to its
    cell without upscaling beyond 2x, so the content keeps its native detail."""
    if len(images) != 4:
        raise ValueError("compose_photo wants exactly four images")
    cw, ch = width // 2, height // 2
    out = np.zeros((height, width, 3), dtype=np.uint8)
    for i, img in enumerate(images):
        h, w = img.shape[:2]
        scale = max(cw / w, ch / h)
        if scale > 2.0:
            raise ValueError(f"image {i} is {w}x{h}: too small for a {cw}x{ch} cell")
        resized = cv2.resize(img, (int(round(w * scale)), int(round(h * scale))), interpolation=cv2.INTER_AREA)
        y0 = (resized.shape[0] - ch) // 2
        x0 = (resized.shape[1] - cw) // 2
        r, c = divmod(i, 2)
        out[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw] = resized[y0:y0 + ch, x0:x0 + cw]
    return out


def build_photo_static(photo, label=""):
    """The photo layout's static panel: `photo` is the 1200x640 BGR mosaic from compose_photo."""
    img = np.full((PANEL_H, PANEL_W, 3), BACKGROUND, dtype=np.uint8)
    ys, xs, w, h = _box("photo", "photo")
    if photo.shape[:2] != (h, w):
        raise ValueError(f"photo must be {w}x{h}, got {photo.shape[1]}x{photo.shape[0]}")
    img[ys, xs] = photo

    # Text at four sizes, white on the background: the smallest is ~9 px x-height.
    ys, xs, w, h = _box("text", "photo")
    y = ys.start + 22
    for scale, thick in ((0.45, 1), (0.6, 1), (0.8, 2), (1.1, 2)):
        cv2.putText(img, "Quick fox 0123 ilIl|", (xs.start + 6, y), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, (255, 255, 255), thick, cv2.LINE_AA)
        y += int(30 * scale) + 14
    # A fence: one-pixel verticals every 4 px, then every 3 px, over a mid grey, plus a 2 px grid.
    ys, xs, w, h = _box("fence", "photo")
    img[ys, xs] = 96
    half = w // 2
    for x in range(xs.start, xs.start + half, 4):
        img[ys, x] = 230
    for x in range(xs.start + half, xs.stop, 3):
        img[ys, x] = 230
    for yy in range(ys.start, ys.stop, 16):
        img[yy:yy + 2, xs] = 230
    ys, xs, w, h = _box("gradient_dark", "photo")
    img[ys, xs] = _ramp(w, 0, 31)[None, :, None]
    if label:
        cv2.putText(img, label, (xs.start + 10, PANEL_H - 40), cv2.FONT_HERSHEY_SIMPLEX,
                    1.2, (255, 255, 255), 2, cv2.LINE_AA)

    dictionary = cv2.aruco.getPredefinedDictionary(MARKER_DICT)
    for marker_id, (cx, cy) in enumerate(MARKER_CENTERS.astype(int)):
        marker = cv2.aruco.generateImageMarker(dictionary, marker_id, MARKER_SIZE)
        half = MARKER_SIZE // 2
        pad = 16
        img[cy - half - pad: cy + half + pad, cx - half - pad: cx + half + pad] = 255
        img[cy - half: cy + half, cx - half: cx + half] = marker[:, :, None]
    return img


def decode_counter(panel, min_contrast=60):
    """Read the counter from a panel-aligned image; None if any cell is unreadable or inconsistent."""
    ys, xs, w, h = _box("counter")
    gray = panel[ys, xs].mean(axis=2) if panel.ndim == 3 else panel[ys, xs]
    cell_w, cell_h = w // COUNTER_BITS, h // 2
    value = 0
    for i in range(COUNTER_BITS):
        cx0, cx1 = i * cell_w + cell_w // 4, (i + 1) * cell_w - cell_w // 4
        top = gray[cell_h // 4: cell_h - cell_h // 4, cx0:cx1].mean()
        bottom = gray[cell_h + cell_h // 4: h - cell_h // 4, cx0:cx1].mean()
        if abs(top - bottom) < min_contrast:
            return None
        value = (value << 1) | int(top > bottom)
    return value


def find_markers(image):
    """{marker id: center in image pixels} for every marker 0..3 that is detected."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    detector = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(MARKER_DICT),
                                       cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2).mean(axis=0) for i, c in zip(ids.flatten(), corners) if 0 <= int(i) < 4}


def find_marker_corners(image):
    """Centers of markers 0..3 (TL, TR, BR, BL) in image pixels, or None unless all four are found."""
    found = find_markers(image)
    if not all(k in found for k in range(4)):
        return None
    return np.array([found[k] for k in range(4)], dtype=np.float32)
