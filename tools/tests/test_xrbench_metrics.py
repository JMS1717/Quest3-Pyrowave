import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench.analyze import peak_signal_noise_ratio, structural_similarity

RNG = np.random.default_rng(7)
IMG = cv2.GaussianBlur(RNG.integers(0, 256, (200, 200), dtype=np.uint8), (0, 0), 2)


def test_identical_images():
    assert peak_signal_noise_ratio(IMG, IMG) == float("inf")
    assert abs(structural_similarity(IMG, IMG) - 1.0) < 1e-9


def test_psnr_known_value():
    shifted = np.clip(IMG.astype(int) + 5, 0, 255).astype(np.uint8)
    mse = np.mean((IMG.astype(float) - shifted) ** 2)
    assert abs(peak_signal_noise_ratio(IMG, shifted) - 10 * np.log10(255 ** 2 / mse)) < 1e-9


def test_ssim_decreases_with_noise():
    light = np.clip(IMG + RNG.normal(0, 5, IMG.shape), 0, 255).astype(np.uint8)
    heavy = np.clip(IMG + RNG.normal(0, 40, IMG.shape), 0, 255).astype(np.uint8)
    assert 1.0 > structural_similarity(IMG, light) > structural_similarity(IMG, heavy) > 0
