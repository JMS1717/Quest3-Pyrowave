"""Offline PyroWave resolution x bytes-per-frame matrix: the arithmetic and the bookkeeping."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import rdmatrix as rd


def test_resolutions_and_caps_match_the_brief():
    assert rd.RESOLUTIONS == (1920, 2048, 2176, 2304, 2432, 2560)
    caps = rd.caps_for(2560)
    assert set(range(150_000, 550_001, 50_000)) <= set(caps) and caps == tuple(sorted(caps))
    assert 416_667 in caps and len(caps) == 10, "2560's constant-bpp point is the reference itself"
    caps_1920 = rd.caps_for(1920)
    assert 234_375 in caps_1920 and 416_667 in caps_1920 and len(caps_1920) == 11


def test_pixel_ratio_and_constant_bpp_bytes():
    assert rd.pixel_ratio(2560) == 1.0
    assert abs(rd.pixel_ratio(1920) - 0.5625) < 1e-9
    assert rd.constant_bpp_bytes(2432) == 376_042
    assert rd.constant_bpp_bytes(2304) == 337_500
    assert rd.constant_bpp_bytes(2176) == 301_042
    assert rd.constant_bpp_bytes(2048) == 266_667
    assert rd.constant_bpp_bytes(1920) == 234_375


def test_derived_columns_are_the_briefs_formulas():
    assert rd.mbps_at_90hz(416_667) == pytest.approx(300.0, abs=0.001)
    assert rd.mbps_at_90hz(150_000) == pytest.approx(108.0)
    # bits per pixel per stereo frame: bytes*8 / (2 * R * R)
    assert rd.bits_per_pixel(416_667, 2560) == pytest.approx(416_667 * 8 / (2 * 2560 * 2560))


def test_tiling_fills_the_frame_at_native_scale_with_flips():
    img = np.zeros((512, 768, 3), np.uint8)
    img[0, 0] = (1, 2, 3)  # a marker pixel to see the flips
    frame = rd.tile_to_square(img, 2560)
    assert frame.shape == (2560, 2560, 3)
    assert tuple(frame[0, 0]) == (1, 2, 3)
    assert tuple(frame[0, 2 * 768 - 1]) == (1, 2, 3), "second column tile is mirrored horizontally"
    assert tuple(frame[2 * 512 - 1, 0]) == (1, 2, 3), "second row tile is mirrored vertically"


def test_marginal_gain_and_pareto():
    curve = [(150_000, 30.0), (200_000, 33.0), (250_000, 34.0), (300_000, 34.2)]
    gains = rd.marginal_gains(curve)
    assert gains[0] == pytest.approx((200_000, 3.0 / 50_000 * 50_000))
    assert gains[-1][1] == pytest.approx(0.2)
    cells = [
        {"resolution": 2560, "cap_bytes": 300_000, "q": 34.0},
        {"resolution": 2048, "cap_bytes": 300_000, "q": 36.0},  # better quality, lower resolution cost
        {"resolution": 2048, "cap_bytes": 200_000, "q": 33.0},
        {"resolution": 1920, "cap_bytes": 200_000, "q": 33.0},  # same quality, cheaper on both axes
    ]
    front = rd.pareto(cells, "q")
    labels = {(c["resolution"], c["cap_bytes"]) for c in front}
    assert (2048, 300_000) in labels and (1920, 200_000) in labels
    assert (2560, 300_000) not in labels, "dominated by 2048 @ 300 KB"
    assert (2048, 200_000) not in labels, "dominated by 1920 @ 200 KB (equal quality, cheaper)"


def test_knee_classification_uses_second_differences():
    # steep then flat: the knee sits at 300 KB
    curve = [(200_000, 30.0), (250_000, 33.0), (300_000, 34.5), (350_000, 34.8), (400_000, 34.9)]
    assert rd.knee_position(curve, 416_667) == "above"
    assert rd.knee_position(curve, 300_000) == "near"
    assert rd.knee_position(curve, 200_000) == "below"


def test_y4m_header_declares_full_range_444():
    header = rd.y4m_header_line(5120, 2560)
    assert header.startswith("YUV4MPEG2 W5120 H2560")
    assert "C444" in header and "XCOLORRANGE=FULL" in header
