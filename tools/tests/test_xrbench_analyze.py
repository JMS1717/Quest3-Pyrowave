import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import analyze as a
from xrbench import patterns as p


@pytest.fixture(scope="module")
def static():
    return p.build_static()


def fake_capture(panel, jpeg_quality=None, posterize_levels=None, blur=0):
    """Embed the panel in a larger dark frame with a perspective tilt, like a headset capture."""
    img = panel.copy()
    if posterize_levels:
        step = 256 // posterize_levels
        img = ((img // step) * step).astype(np.uint8)
    if blur:
        img = cv2.GaussianBlur(img, (0, 0), blur)
    h, w = img.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[180, 140], [180 + w * 0.9, 120], [200 + w * 0.92, 160 + h * 0.9], [170, 150 + h * 0.88]])
    out = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (w + 400, h + 400),
                              flags=cv2.INTER_LINEAR, borderValue=(20, 20, 20))
    if jpeg_quality:
        ok, enc = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        out = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    return out


def test_rectify_recovers_panel_geometry(static):
    frame = p.render_frame(static, 77)
    panel = a.rectify(fake_capture(frame))
    assert panel.shape == frame.shape
    assert p.decode_counter(panel) == 77


def cover_marker(img, panel_xy, size=200):
    """Paint over the marker whose panel center is panel_xy, in the capture (fake_capture geometry)."""
    out = img.copy()
    h, w = p.PANEL_H, p.PANEL_W
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[180, 140], [180 + w * 0.9, 120], [200 + w * 0.92, 160 + h * 0.9], [170, 150 + h * 0.88]])
    m = cv2.getPerspectiveTransform(src, dst)
    cx, cy = cv2.perspectiveTransform(np.float32([[panel_xy]]), m)[0][0]
    out[int(cy) - size // 2:int(cy) + size // 2, int(cx) - size // 2:int(cx) + size // 2] = 20
    return out


def test_rectify_works_with_one_marker_hidden(static):
    frame = p.render_frame(static, 4321)
    capture = cover_marker(fake_capture(frame), p.MARKER_CENTERS[2])   # bottom-right out of view
    panel = a.rectify(capture)
    assert panel is not None and p.decode_counter(panel) == 4321


def test_rectify_needs_at_least_three_markers(static):
    capture = fake_capture(p.render_frame(static, 1))
    for i in (1, 2):
        capture = cover_marker(capture, p.MARKER_CENTERS[i])
    assert a.rectify(capture) is None


def test_rectify_returns_none_without_markers():
    assert a.rectify(np.full((800, 800, 3), 40, np.uint8)) is None


def test_still_reads_counter_through_heavy_compression(static):
    result = a.analyze_still(fake_capture(p.render_frame(static, 4242), jpeg_quality=25), static)
    assert result["counter"] == 4242


def test_psnr_orders_quality_levels(static):
    frame = p.render_frame(static, 9)
    good = a.analyze_still(fake_capture(frame, jpeg_quality=95), static)
    bad = a.analyze_still(fake_capture(frame, jpeg_quality=15), static)
    for region in ("detail", "motion", "overall"):
        assert good["psnr"][region] > bad["psnr"][region] + 2, region
        assert good["ssim"][region] > bad["ssim"][region], region


def test_banding_detects_posterized_gradient(static):
    frame = p.render_frame(static, 3)
    clean = a.analyze_still(fake_capture(frame), static)["banding"]["gradient_gray"]
    banded = a.analyze_still(fake_capture(frame, posterize_levels=32), static)["banding"]["gradient_gray"]
    assert banded["step_ratio"] > 0.5 > clean["step_ratio"]
    assert banded["effective_levels"] < 40 < clean["effective_levels"]


def test_banding_profile_math_on_synthetic_ramps():
    smooth = np.linspace(0, 255, 1200)
    stepped = np.floor(np.linspace(0, 255, 1200) / 16) * 16
    assert a.banding_metrics(smooth)["step_ratio"] < 0.1
    assert a.banding_metrics(stepped)["step_ratio"] > 0.9
    assert a.banding_metrics(stepped)["effective_levels"] == 16


def test_blur_lowers_detail_sharpness_score(static):
    frame = p.render_frame(static, 5)
    sharp = a.analyze_still(fake_capture(frame), static)["sharpness"]["detail"]
    soft = a.analyze_still(fake_capture(frame, blur=2.5), static)["sharpness"]["detail"]
    assert soft < sharp * 0.7


def test_sequence_clean_stream_captured_at_same_rate():
    seq = a.sequence_metrics(list(range(100, 172)), stream_fps=72, capture_fps=72)
    assert seq["skipped"] == 0 and seq["repeated"] == 0 and seq["unreadable"] == 0


def test_sequence_counts_skips_repeats_and_unreadable():
    counters = [1, 2, 3, 3, 3, 4, 7, 8, None, 9, 10]
    seq = a.sequence_metrics(counters, stream_fps=72, capture_fps=72)
    assert seq["skipped"] == 2        # 5 and 6 never shown
    assert seq["repeated"] == 2       # 3 held for two extra captures
    assert seq["unreadable"] == 1
    assert seq["max_gap"] == 3


def test_sequence_slower_capture_does_not_count_expected_gaps():
    counters = [round(i * 72 / 60) for i in range(60)]  # 60 fps capture of 72 fps stream
    seq = a.sequence_metrics(counters, stream_fps=72, capture_fps=60)
    assert seq["skipped"] == 0 and seq["repeated"] == 0


def test_sequence_half_rate_capture_flags_irregular_advances():
    # Headset screenrecord samples every 2nd display frame (36 fps of 72 Hz).
    clean = list(range(0, 200, 2))
    seq = a.sequence_metrics(clean, stream_fps=72, capture_fps=36)
    assert seq["irregular"] == 0 and seq["skipped"] == 0 and seq["repeated"] == 0
    held = [0, 2, 4, 5, 7, 9]           # one frame shown twice: advance 1 then back in phase
    dropped = [0, 2, 4, 7, 9]            # a skipped frame visible as an advance of 3
    assert a.sequence_metrics(held, 72, 36)["irregular"] == 1
    assert a.sequence_metrics(dropped, 72, 36)["irregular"] == 1
    assert a.sequence_metrics(dropped, 72, 36)["skipped"] == 1


def test_sequence_handles_counter_wraparound():
    counters = [p.COUNTER_MAX - 1, p.COUNTER_MAX, 0, 1]
    assert a.sequence_metrics(counters, 72, 72)["skipped"] == 0


def test_sequence_treats_implausible_jumps_as_misreads():
    counters = [0, 2, 4, 40000, 8, 10]       # one garbled counter read
    seq = a.sequence_metrics(counters, 72, 36)
    assert seq["skipped"] == 0 and seq["misread"] == 1


def test_display_latency_from_still_and_frame_log():
    submitted = {100: 10.000, 101: 10.014, 102: 10.028, 150: 10.700}
    lat = a.display_latency(still_counter=101, t_request=10.500, submitted=submitted)
    assert abs(lat["latency_ms"] - 486.0) < 0.5
    assert lat["frames_behind"] == 1           # newest frame submitted before the request is 102
