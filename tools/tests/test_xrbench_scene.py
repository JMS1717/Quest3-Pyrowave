import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import patterns as p
from xrbench import scene_app as s


def test_eye_image_contains_panel_unscaled_and_centered():
    static = p.build_static("X")
    eye, (ox, oy) = s.build_eye_image(2560, 2560, static)
    assert (ox, oy) == ((2560 - p.PANEL_W) // 2, (2560 - p.PANEL_H) // 2)
    assert np.array_equal(eye[oy:oy + p.PANEL_H, ox:ox + p.PANEL_W], static)


def test_eye_smaller_than_panel_is_rejected():
    with pytest.raises(ValueError):
        s.build_eye_image(1440, 1440, p.build_static())


def test_optical_center_origin_symmetric_projection_is_centered():
    assert s.optical_center_origin(3132, 3132, (-1.0, 1.0, -1.0, 1.0)) == ((3132 - 1600) // 2, (3132 - 1200) // 2)


def test_optical_center_origin_follows_asymmetric_left_eye():
    # Left eye: more field of view to the left (outside), so the axis sits right of center.
    ox, oy = s.optical_center_origin(3132, 3132, (-1.4, 1.0, -1.1, 1.1))
    axis_x = 3132 * 1.4 / 2.4
    assert abs((ox + p.PANEL_W / 2) - axis_x) <= 1
    assert oy == (3132 - 1200) // 2


def test_optical_center_origin_is_clamped_inside_the_eye():
    ox, oy = s.optical_center_origin(2000, 1400, (-5.0, 0.2, -0.1, 3.0))
    assert 0 <= ox <= 2000 - p.PANEL_W and 0 <= oy <= 1400 - p.PANEL_H


def test_build_eye_image_accepts_explicit_origin():
    eye, origin = s.build_eye_image(3132, 3132, p.build_static(), origin=(900, 950))
    assert origin == (900, 950)
    assert np.array_equal(eye[950:950 + p.PANEL_H, 900:900 + p.PANEL_W], p.build_static())


def test_load_noise_is_deterministic_blocky_and_full_range():
    tile = s.load_noise_tile()
    assert tile.shape == (s.LOAD_TILE, s.LOAD_TILE, 3) and tile.dtype == np.uint8
    assert np.array_equal(tile, s.load_noise_tile())
    assert tile.min() == 0 and tile.max() == 255


def test_load_uv_rect_maps_each_noise_texel_to_block_pixels_and_scrolls():
    u0, v0, u1, v1 = s.load_uv_rect(0, 2560, 2560)
    assert (u1 - u0) * s.LOAD_TILE * s.LOAD_BLOCK_PX == 2560
    assert (v1 - v0) * s.LOAD_TILE * s.LOAD_BLOCK_PX == 2560
    a, b = s.load_uv_rect(10, 2560, 2560), s.load_uv_rect(11, 2560, 2560)
    step_px = (b[0] - a[0]) * s.LOAD_TILE * s.LOAD_BLOCK_PX
    assert step_px == s.LOAD_SPEED_PX


def test_dynamic_updates_reproduce_render_frame():
    static = p.build_static()
    eye, origin = s.build_eye_image(2048, 2048, static)
    for x, y, rgb in s.dynamic_updates(321, origin):
        eye[y:y + rgb.shape[0], x:x + rgb.shape[1]] = rgb[:, :, ::-1]
    ox, oy = origin
    assert np.array_equal(eye[oy:oy + p.PANEL_H, ox:ox + p.PANEL_W], p.render_frame(static, 321))


# Galaxy XR eye frustums, measured (tangents normalised so right-left == 1).
LEFT_EYE = (-0.62656, 0.37344, -0.5, 0.5)
RIGHT_EYE = (-0.37344, 0.62656, -0.5, 0.5)


def test_2560_per_eye_origins_are_unchanged():
    # what the working captures used; the margin must not disturb a placement that already fits
    assert s.optical_center_origin(2560, 2560, LEFT_EYE) == (804, 680)
    assert s.optical_center_origin(2560, 2560, RIGHT_EYE) == (156, 680)


def test_2048_per_eye_panel_is_not_flush_against_the_texture_edge():
    # the runtime's visible FOV crops the outermost columns: at 2048/eye the right eye clamped to
    # x=0 and every capture found 0 of 4 ArUco markers, so rectification had no anchor
    ox, oy = s.optical_center_origin(2048, 2048, RIGHT_EYE)
    assert ox >= s.EDGE_MARGIN
    assert 2048 - (ox + p.PANEL_W) >= s.EDGE_MARGIN


def test_panel_always_lands_fully_inside_the_eye_texture():
    for eye in (2048, 2304, 2560, 3200):
        for projection in (LEFT_EYE, RIGHT_EYE):
            ox, oy = s.optical_center_origin(eye, eye, projection)
            assert 0 <= ox and ox + p.PANEL_W <= eye, (eye, projection)
            assert 0 <= oy and oy + p.PANEL_H <= eye, (eye, projection)
