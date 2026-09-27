import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import mathlab_transforms as T


@pytest.fixture
def img():
    rng = np.random.default_rng(3)
    x = rng.random((256, 320), dtype=np.float32) * 255
    # add structure so the low band carries energy
    yy, xx = np.mgrid[0:256, 0:320]
    return (x * 0.2 + 100 + 60 * np.sin(xx / 17.0) + 40 * np.cos(yy / 23.0)).astype(np.float32)


@pytest.mark.parametrize("name", ["haar", "cdf53", "cdf97", "db2", "db4", "lap",
                                  "wht8", "wht16", "wht32", "wht64", "dct8", "dct16", "dct32"])
def test_every_transform_round_trips(img, name):
    t = T.Transform(name)
    bands = t.forward(img)
    back = t.inverse(bands)
    assert back.shape == img.shape
    assert np.max(np.abs(back - img)) < 1e-2, name


def test_wavelet_band_shapes_and_count(img):
    bands = T.dwt2(img, "cdf97")
    assert bands["LL5"].shape == (8, 10)
    assert bands["HH1"].shape == (128, 160)
    assert sum(v.size for v in bands.values()) == img.size


def test_laplacian_pyramid_is_overcomplete_by_a_third(img):
    bands = T.lap_forward(img)
    ratio = sum(v.size for v in bands.values()) / img.size
    assert 1.30 < ratio < 1.34


def test_block_transforms_are_orthonormal():
    for kind in ("wht", "dct"):
        m = T._wht_matrix(16) if kind == "wht" else T._dct_matrix(16)
        assert np.allclose(m @ m.T, np.eye(16), atol=1e-5)


def test_band_gains_are_one_for_orthonormal_and_scale_dependent_for_97(img):
    assert all(abs(g - 1.0) < 1e-6 for g in T.Transform("dct8").band_gains(img.shape).values())
    g = T.Transform("cdf97").band_gains(img.shape)
    assert g["LL5"] > g["HH1"], "coarse 9/7 coefficients reconstruct with larger norm"
    gh = T.Transform("haar").band_gains(img.shape)
    assert all(abs(v - 1.0) < 1e-4 for v in gh.values()), "orthonormal Haar lifting"


def test_haar_lift_round_trips_and_has_unit_dc_gain():
    rng = np.random.default_rng(3)
    img = rng.random((64, 64), np.float32)
    t = T.Transform("haar_lift")
    assert np.allclose(t.inverse(t.forward(img)), img, atol=1e-5)
    flat = np.full((64, 64), 7.0, np.float32)
    bands = t.forward(flat)
    low = [k for k in bands if k.startswith("LL")]
    # every detail band of a flat image is zero and the coarsest LL keeps the DC value
    for k, v in bands.items():
        if k in low:
            assert np.allclose(v, 7.0)
        else:
            assert np.allclose(v, 0.0)
