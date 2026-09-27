"""Score headset captures of the benchmark panel: geometry, counter, fidelity, banding, frame pacing."""
import math

import cv2
import numpy as np

from . import patterns as p


def peak_signal_noise_ratio(reference, test, data_range=255):
    mse = np.mean((reference.astype(np.float64) - test.astype(np.float64)) ** 2)
    return float("inf") if mse == 0 else 10.0 * math.log10(data_range ** 2 / mse)


def structural_similarity(reference, test, data_range=255):
    """Mean SSIM with the standard 11x11 Gaussian window (sigma 1.5), Wang et al. 2004."""
    x, y = reference.astype(np.float64), test.astype(np.float64)
    c1, c2 = (0.01 * data_range) ** 2, (0.03 * data_range) ** 2
    blur = lambda img: cv2.GaussianBlur(img, (11, 11), 1.5)
    mu_x, mu_y = blur(x), blur(y)
    var_x = blur(x * x) - mu_x ** 2
    var_y = blur(y * y) - mu_y ** 2
    cov = blur(x * y) - mu_x * mu_y
    ssim_map = ((2 * mu_x * mu_y + c1) * (2 * cov + c2)) / ((mu_x ** 2 + mu_y ** 2 + c1) * (var_x + var_y + c2))
    return float(ssim_map[5:-5, 5:-5].mean())

FIDELITY_REGIONS = ("detail", "motion", "bars", "gradient_gray", "gradient_rgb", "gradient_dark")


def capture_homography(capture):
    """3x3 transform mapping capture pixels onto panel pixels; None with fewer than 3 markers.

    Four markers give a full perspective fit. Head motion plus head-locked content can push one
    marker out of the captured view; with three, an affine fit (rotation, scale, shear) is used,
    which is accurate for a panel viewed near head-on.
    """
    found = p.find_markers(capture)
    ids = sorted(found)
    if len(ids) == 4:
        homography, _ = cv2.findHomography(np.float32([found[i] for i in ids]), p.MARKER_CENTERS)
        return homography
    if len(ids) == 3:
        affine = cv2.getAffineTransform(np.float32([found[i] for i in ids]), p.MARKER_CENTERS[ids])
        return np.vstack([affine, [0.0, 0.0, 1.0]])
    return None


def rectify(capture, homography=None):
    """Warp a capture so the panel lands on panel pixel coordinates; None if markers are missing."""
    if homography is None:
        homography = capture_homography(capture)
        if homography is None:
            return None
    return cv2.warpPerspective(capture, homography, (p.PANEL_W, p.PANEL_H), flags=cv2.INTER_LINEAR)


def matched_reference(reference, homography, capture_shape):
    """The reference sent through the same geometry round trip as the capture, so resampling
    blur is shared and fidelity scores measure only what the stream itself lost."""
    h, w = capture_shape[:2]
    in_capture = cv2.warpPerspective(reference, np.linalg.inv(homography), (w, h), flags=cv2.INTER_LINEAR)
    return rectify(in_capture, homography)


def _crop(img, name):
    x0, y0, x1, y1 = p.REGIONS[name]
    return img[y0:y1, x0:x1]


def _gray(img):
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def banding_metrics(profile, min_run=3):
    """Plateau analysis of a left-to-right ramp profile.

    effective_levels: distinct plateaus (runs of >= min_run px at one code value).
    step_ratio: share of the ramp's total rise that happens in jumps of >= 2 code values
    between neighbouring plateaus (0 = smooth, 1 = pure staircase).
    """
    values = np.round(np.asarray(profile, dtype=np.float64)).astype(int)
    plateaus = []
    start = 0
    for i in range(1, len(values) + 1):
        if i == len(values) or values[i] != values[start]:
            if i - start >= min_run:
                plateaus.append(values[start])
            start = i
    effective = len(set(plateaus))
    if len(plateaus) < 2:
        return {"effective_levels": effective, "step_ratio": 0.0, "max_step": 0}
    jumps = np.diff(plateaus)
    rise = max(int(np.sum(np.abs(jumps))), 1)
    big = int(np.sum(np.abs(jumps[np.abs(jumps) >= 2])))
    return {"effective_levels": effective, "step_ratio": big / rise, "max_step": int(np.max(np.abs(jumps)))}


def _ramp_profiles(panel):
    """Row-averaged 1-D profiles for each gradient (rows trimmed to avoid edge bleed)."""
    profiles = {}
    g = _crop(panel, "gradient_gray")
    profiles["gradient_gray"] = _gray(g)[10:-10].mean(axis=0)
    d = _crop(panel, "gradient_dark")
    profiles["gradient_dark"] = _gray(d)[10:-10].mean(axis=0)
    rgb = _crop(panel, "gradient_rgb")
    stripe = rgb.shape[0] // 3
    for c, name in enumerate(("red", "green", "blue")):
        band = rgb[c * stripe + 6:(c + 1) * stripe - 6, :, 2 - c]
        profiles[f"gradient_{name}"] = band.mean(axis=0)
    return profiles


def _sharpness(panel, reference, name):
    got = cv2.Laplacian(_gray(_crop(panel, name)), cv2.CV_64F).var()
    want = cv2.Laplacian(_gray(_crop(reference, name)), cv2.CV_64F).var()
    return float(got / want) if want else float("nan")


def analyze_still(capture, static):
    """Full-quality analysis of one capture. Returns None if the panel cannot be located."""
    homography = capture_homography(capture)
    if homography is None:
        return None
    panel = rectify(capture, homography)
    counter = p.decode_counter(panel)
    sent = p.render_frame(static, counter if counter is not None else 0)
    reference = matched_reference(sent, homography, capture.shape)

    regions = [r for r in FIDELITY_REGIONS if counter is not None or r != "motion"]
    psnr, ssim = {}, {}
    for name in regions:
        want, got = _gray(_crop(reference, name)), _gray(_crop(panel, name))
        psnr[name] = float(peak_signal_noise_ratio(want, got, data_range=255))
        ssim[name] = float(structural_similarity(want, got, data_range=255))

    masked_ref, masked_got = _gray(reference), _gray(panel)
    for name in ("label",) + (() if counter is not None else ("motion", "counter")):
        x0, y0, x1, y1 = p.REGIONS[name]
        masked_got[y0:y1, x0:x1] = masked_ref[y0:y1, x0:x1]
    psnr["overall"] = float(peak_signal_noise_ratio(masked_ref, masked_got, data_range=255))
    ssim["overall"] = float(structural_similarity(masked_ref, masked_got, data_range=255))

    return {
        "counter": counter,
        "psnr": psnr,
        "ssim": ssim,
        "banding": {k: banding_metrics(v) for k, v in _ramp_profiles(panel).items()},
        "sharpness": {"detail": _sharpness(panel, sent, "detail")},
        "panel": panel,
    }


def sequence_metrics(counters, stream_fps, capture_fps):
    """Frame pacing from counters read off consecutive captures (None = unreadable capture).

    A capture interval should advance the counter by stream_fps / capture_fps frames on average.
    skipped: stream frames never shown beyond that allowance; repeated: captures that show the
    same frame again when a new one was due.
    """
    per_capture = stream_fps / capture_fps
    # When the capture samples a whole number of stream frames (e.g. 36 fps of 72 Hz = 2), every
    # step between adjacent captures should be exactly that; any other step is a pacing error.
    whole = round(per_capture) if abs(per_capture - round(per_capture)) < 0.05 else None
    modulo = p.COUNTER_MAX + 1
    skipped = repeated = irregular = misread = 0
    unreadable = sum(1 for c in counters if c is None)
    max_gap = 0
    deltas = []
    last_value, last_index = None, None
    for index, value in enumerate(counters):
        if value is None:
            continue
        if last_value is not None:
            gap = (value - last_value) % modulo
            captures = index - last_index
            if gap > per_capture * captures * 10 + 10:
                misread += 1          # a garbled counter read, not a real multi-second jump
                continue
            deltas.append(gap / captures)
            max_gap = max(max_gap, gap)
            if whole is not None and captures == 1 and gap != whole:
                irregular += 1
            allowed = math.ceil(per_capture * captures - 1e-9)
            if gap == 0 and per_capture >= 1:
                repeated += captures
            elif gap > allowed:
                skipped += gap - allowed
        last_value, last_index = value, index
    readable = len(counters) - unreadable
    return {
        "captures": len(counters),
        "unreadable": unreadable,
        "skipped": skipped,
        "repeated": repeated,
        "irregular": irregular,
        "misread": misread,
        "max_gap": max_gap,
        "mean_advance": float(np.mean(deltas)) if deltas else None,
        "advance_jitter": float(np.std(deltas)) if deltas else None,
        "expected_advance": per_capture,
        "readable_ratio": readable / len(counters) if counters else None,
    }


def display_latency(still_counter, t_request, submitted):
    """How stale the headset image was when a screenshot was requested.

    submitted: {frame number: perf_counter time the scene app submitted it} (frames.csv).
    Screenshot request times come from the same Windows QueryPerformanceCounter clock, so
    latency = request time - submit time of the frame the headset was showing. This includes
    screencap start-up delay, so compare runs with each other rather than as absolute
    motion-to-photon. frames_behind: frames submitted after it but before the request.
    """
    if still_counter is None or still_counter not in submitted:
        return None
    newest = max((k for k, t in submitted.items() if t <= t_request), default=still_counter)
    return {"latency_ms": (t_request - submitted[still_counter]) * 1000.0,
            "frames_behind": newest - still_counter}
