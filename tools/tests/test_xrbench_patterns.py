import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import patterns as p


@pytest.fixture(scope="module")
def static():
    return p.build_static()


def test_panel_is_fixed_size_rgb_uint8(static):
    assert static.shape == (p.PANEL_H, p.PANEL_W, 3) and static.dtype == np.uint8


@pytest.mark.parametrize("n", [0, 1, 2, 255, 256, 40000, p.COUNTER_MAX])
def test_counter_round_trips(static, n):
    frame = p.render_frame(static, n)
    assert p.decode_counter(frame) == n


def test_counter_rejects_corrupted_block(static):
    frame = p.render_frame(static, 1234).copy()
    x0, y0, x1, y1 = p.REGIONS["counter"]
    frame[y0:y1, x0:(x0 + x1) // 2] = 128
    assert p.decode_counter(frame) is None


def test_counter_wraps_at_max():
    assert p.counter_value(p.COUNTER_MAX + 1) == 0


def test_gradient_rows_step_through_every_8bit_level(static):
    x0, y0, x1, y1 = p.REGIONS["gradient_gray"]
    row = static[(y0 + y1) // 2, x0:x1, 0].astype(int)
    assert row[0] == 0 and row[-1] == 255
    assert len(np.unique(row)) == 256
    assert np.all(np.diff(row) >= 0)


def test_motion_band_changes_every_frame_and_is_deterministic(static):
    a = p.render_frame(static, 10)
    b = p.render_frame(static, 11)
    x0, y0, x1, y1 = p.REGIONS["motion"]
    assert not np.array_equal(a[y0:y1, x0:x1], b[y0:y1, x0:x1])
    assert np.array_equal(a, p.render_frame(p.build_static(), 10))


def test_static_regions_do_not_change_between_frames(static):
    a = p.render_frame(static, 10)
    b = p.render_frame(static, 500)
    for name in ("gradient_gray", "gradient_rgb", "detail", "bars"):
        x0, y0, x1, y1 = p.REGIONS[name]
        assert np.array_equal(a[y0:y1, x0:x1], b[y0:y1, x0:x1]), name


def test_regions_are_inside_panel_and_do_not_overlap():
    boxes = list(p.REGIONS.values())
    for x0, y0, x1, y1 in boxes:
        assert 0 <= x0 < x1 <= p.PANEL_W and 0 <= y0 < y1 <= p.PANEL_H
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]


def test_marker_corners_are_found_on_clean_panel(static):
    corners = p.find_marker_corners(static)
    assert corners is not None
    np.testing.assert_allclose(corners, p.MARKER_CENTERS, atol=1.0)
