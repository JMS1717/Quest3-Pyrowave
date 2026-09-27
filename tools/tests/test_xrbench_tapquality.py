import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import patterns as p
from xrbench import tapquality as tq


def sbs_frame(counter, label="A20-400", eye=2048, origins=((298, 424), (150, 424))):
    """A side-by-side encode frame the way ALVR packs it: left eye then right eye."""
    static = p.build_static(label)
    panel = p.render_frame(static, counter)
    frame = np.full((eye, eye * 2, 3), p.BACKGROUND, dtype=np.uint8)
    for i, (ox, oy) in enumerate(origins):
        frame[oy:oy + p.PANEL_H, i * eye + ox:i * eye + ox + p.PANEL_W] = panel[:, :, ::-1]
    return frame[:, :, ::-1]  # BGR, as cv2.imread would give


def test_split_eyes_halves_a_side_by_side_frame():
    left, right = tq.split_eyes(sbs_frame(7))
    assert left.shape == right.shape == (2048, 2048, 3)


def test_panel_is_found_at_the_scene_reported_origin():
    frame = sbs_frame(4242)
    left, _ = tq.split_eyes(frame)
    panel = tq.crop_panel(left, (298, 424))
    assert panel.shape[:2] == (p.PANEL_H, p.PANEL_W)
    assert p.decode_counter(panel) == 4242


def test_measure_frame_is_near_perfect_on_an_unencoded_frame():
    # no codec in the loop, so this is the measurement's own noise floor -- it must be tiny,
    # otherwise any codec number it reports is really reporting the harness
    result = tq.measure_frame(sbs_frame(100), [(298, 424), (150, 424)], "A20-400")
    assert len(result) == 2
    for eye in result:
        assert eye["counter"] == 100
        assert eye["psnr"] > 60, eye
        assert eye["ssim"] > 0.999, eye


def test_measure_frame_reports_per_region_psnr():
    result = tq.measure_frame(sbs_frame(5), [(298, 424), (150, 424)], "A20-400")
    assert set(result[0]["regions"]) >= {"detail", "motion", "bars", "gradient_gray"}


def test_unreadable_counter_is_reported_rather_than_guessed():
    frame = sbs_frame(9)
    frame[:] = 0  # a blank frame has no counter to read
    result = tq.measure_frame(frame, [(298, 424), (150, 424)], "A20-400")
    assert all(eye["counter"] is None and eye["psnr"] is None for eye in result)


def test_parse_origins_reads_the_scene_stdout_line():
    line = ("A20-400: 2374 frames in 33.0s, eye 2048x2048, load=noise, "
            "panel origins {'left': (298, 424), 'right': (150, 424)}")
    assert tq.parse_origins(line) == [(298, 424), (150, 424)]


def test_parse_origins_returns_none_when_the_line_is_missing_the_origins():
    assert tq.parse_origins("A20-400: 2374 frames in 33.0s") is None
