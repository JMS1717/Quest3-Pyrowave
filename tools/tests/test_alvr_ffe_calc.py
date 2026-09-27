import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alvr_ffe_calc import FoveationConfig, encoded_eye_size, max_eye_size, fits_limit

GUIDE = FoveationConfig(center_size_x=0.45, center_size_y=0.40,
                        center_shift_x=0.0, center_shift_y=0.0,
                        edge_ratio_x=3.0, edge_ratio_y=4.0)


def test_guide_settings_at_2560_encode_to_1632x1408_per_eye():
    assert encoded_eye_size(2560, 2560, GUIDE) == (1632, 1408)


def test_side_by_side_at_2560_fits_h264_limit():
    assert fits_limit(2560, 2560, GUIDE, limit=(4096, 2048))


def test_foveation_disabled_is_identity_rounded_to_32():
    assert encoded_eye_size(2560, 2560, None) == (2560, 2560)


def test_3200_fits_but_3300_does_not():
    assert fits_limit(3200, 3200, GUIDE, limit=(4096, 2048))
    assert not fits_limit(3300, 3300, GUIDE, limit=(4096, 2048))


def test_max_square_eye_size_is_the_largest_fitting_multiple_of_step():
    best = max_eye_size(GUIDE, limit=(4096, 2048), step=32)
    assert fits_limit(best, best, GUIDE, limit=(4096, 2048))
    assert not fits_limit(best + 32, best + 32, GUIDE, limit=(4096, 2048))


def test_cli_prints_sizes_and_verdict():
    script = Path(__file__).resolve().parents[1] / "alvr_ffe_calc.py"
    out = subprocess.run([sys.executable, str(script), "2560"],
                         capture_output=True, text=True, check=True).stdout
    assert "1632x1408" in out and "3264x1408" in out and "OK" in out
