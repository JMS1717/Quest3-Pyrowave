import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import mathlab as M
from xrbench import mathlab_transforms as T


def test_entropy_bits_of_a_two_symbol_band_is_one_bit_per_coefficient():
    q = {"a": np.array([[0, 1], [1, 0]] * 8, dtype=np.int32)}
    assert abs(M.entropy_bits(q) - 32.0) < 1e-9


def test_bitplane_cost_counts_planes_signs_and_headers_and_is_zero_for_empty_bands():
    empty = {"a": np.zeros((32, 32), np.int32)}
    assert M.bitplane_bits(empty) == 0
    one = {"a": np.zeros((32, 32), np.int32)}
    one["a"][0, 0] = 5          # 3 planes for one 8x8 group, 1 sign, 4 control, + 80 block overhead
    assert M.bitplane_bits(one) == 3 * 64 + 1 + 4 + 80
    neg = {"a": np.zeros((32, 32), np.int32)}
    neg["a"][0, 0] = -5
    assert M.bitplane_bits(neg) == M.bitplane_bits(one), "sign costs one bit either way"


def test_bitplane_cost_is_monotone_in_the_quantiser_step():
    rng = np.random.default_rng(0)
    x = (rng.random((256, 256), np.float32) * 255)
    tr = T.Transform("cdf97"); bands = tr.forward(x); gains = tr.band_gains(x.shape)
    costs = [M.bitplane_bits(M.quantise(bands, d, gains)[0]) for d in (1, 2, 4, 8, 16)]
    assert costs == sorted(costs, reverse=True)


def test_find_delta_meets_the_cap_from_above():
    rng = np.random.default_rng(1)
    x = (rng.random((256, 256), np.float32) * 255)
    tr = T.Transform("haar"); bands = tr.forward(x); gains = tr.band_gains(x.shape)
    cap = 6000
    delta, q, rec, bits = M.find_delta_for_bytes(bands, gains, cap)
    assert bits <= cap * 8
    tighter = M.bitplane_bits(M.quantise(bands, delta / 1.5, gains)[0])
    assert tighter > cap * 8, "a noticeably finer step would have overrun the cap"


def test_psnr_and_ssim_are_perfect_for_identical_images():
    x = np.random.default_rng(2).random((64, 64), np.float32) * 255
    assert M.psnr(x, x) == float("inf")
    assert abs(M.ssim(x, x) - 1.0) < 1e-6
    assert M.psnr(x, x + 1.0) == pytest.approx(48.13, abs=0.01)


def test_report_pareto_and_lookup_helpers():
    from xrbench import mathlab_report as R
    pts = [{"name": "a", "quality": 30, "bytes": 1, "ops": 10, "passes": 2},
           {"name": "b", "quality": 30, "bytes": 1, "ops": 5, "passes": 2},   # dominates a (fewer ops)
           {"name": "c", "quality": 32, "bytes": 1, "ops": 20, "passes": 4}]  # better quality, not dominated
    front, dominated = R.pareto4(pts)
    assert {p["name"] for p in front} == {"b", "c"} and dominated[0][0]["name"] == "a"
    rows = [{"transform": "haar", "source": "s", "resolution": "2560", "cap_bytes": "150000", "psnr_y": "20"},
            {"transform": "haar", "source": "s", "resolution": "2560", "cap_bytes": "200000", "psnr_y": "25"}]
    assert R.bytes_for_quality(rows, "haar", "s", 24.0) == (200000, 25.0)
    assert R.bytes_for_quality(rows, "haar", "s", 30.0) is None
    assert R.ops_per_pixel("wht16") == 8 and R.ops_per_pixel("haar") == 2.7
