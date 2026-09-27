"""The photo layout: a natural mosaic in the panel, reproducible frame by frame."""
import numpy as np
import pytest

from xrbench import patterns as p
from xrbench import tapquality as tq


def _mosaic():
    rng = np.random.default_rng(7)
    imgs = [rng.integers(0, 256, (512, 768, 3), dtype=np.uint8) for _ in range(4)]
    return p.compose_photo(imgs)


def test_compose_photo_is_1200x640_and_refuses_wrong_counts():
    assert _mosaic().shape == (640, 1200, 3)
    with pytest.raises(ValueError):
        p.compose_photo([np.zeros((512, 768, 3), np.uint8)] * 3)
    with pytest.raises(ValueError):
        p.compose_photo([np.zeros((64, 64, 3), np.uint8)] * 4)  # would need >2x upscaling


def test_photo_static_places_the_mosaic_and_the_counter_still_decodes():
    static = p.build_photo_static(_mosaic(), "AB-300-1")
    assert static.shape == (p.PANEL_H, p.PANEL_W, 3)
    x0, y0, x1, y1 = p.PHOTO_REGIONS["photo"]
    assert np.array_equal(static[y0:y1, x0:x1], _mosaic())
    frame = p.render_frame(static, 4242, "photo")
    assert p.decode_counter(frame) == 4242
    # the fence strip really has one-pixel lines
    fx0, fy0, fx1, fy1 = p.PHOTO_REGIONS["fence"]
    assert static[fy0 + 5, fx0, 0] == 230 and static[fy0 + 5, fx0 + 1, 0] == 96


def test_tapquality_scores_the_photo_layout_against_the_supplied_static():
    static = p.build_photo_static(_mosaic(), "AB")
    frame_panel = p.render_frame(static, 12, "photo")
    eye = np.full((1400, 1800, 3), p.BACKGROUND, np.uint8)
    eye[100:100 + p.PANEL_H, 100:100 + p.PANEL_W] = frame_panel
    sbs = np.concatenate([eye, eye], axis=1)
    rows = tq.measure_frame(sbs, [(100, 100), (100, 100)], "AB", static=static, layout="photo")
    assert rows[0]["counter"] == 12 and rows[0]["psnr"] == float("inf")
    assert set(rows[0]["regions"]) == set(tq.PHOTO_REGIONS)
    with pytest.raises(ValueError):
        tq.measure_frame(sbs, [(100, 100), (100, 100)], "AB", layout="photo")
