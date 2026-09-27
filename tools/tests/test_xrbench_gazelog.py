import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import gazelog as gl

SAMPLE = """17:55:50.618 [ERROR] [VIEWS] fov[0] deg: left -54.5 right +39.9 up +52.6 down -52.6 (h 94.4 v 105.1) ipd 62.2 mm
17:55:50.618 [ERROR] [VIEWS] fov[1] deg: left -39.9 right +54.5 up +52.6 down -52.6 (h 94.4 v 105.1) ipd 62.2 mm
17:55:50.618 [ERROR] [GAZE-SRV] frame 0: yaw +6.2 pitch +26.9 (left true right false) | lag over 6 frames: p50 NaN p95 NaN max NaN deg (n=0)
17:55:50.950 [ERROR] [GAZE-SRV] frame 72: yaw -3.1 pitch -12.0 (left true right false) | lag over 6 frames: p50 0.2 p95 5.8 max 23.7 deg (n=66)
17:55:51.284 [ERROR] [GAZE-SRV] frame 144: yaw +11.4 pitch +2.5 (left true right false) | lag over 6 frames: p50 0.3 p95 6.7 max 25.9 deg (n=138)
17:55:50.618 [ERROR] [FOV-CENTER] frame 0: L +0.518 +0.734  R +0.402 +0.734
17:55:50.950 [ERROR] [FOV-CENTER] frame 72: L +0.900 -0.112  R -0.900 -0.112
17:55:51.284 [ERROR] [FOV-CENTER] frame 144: L +0.300 +0.050  R +0.600 +0.050
"""


def test_parses_gaze_angle_range():
    stats = gl.parse(SAMPLE)
    assert stats["yaw_min"] == -3.1 and stats["yaw_max"] == 11.4
    assert stats["pitch_min"] == -12.0 and stats["pitch_max"] == 26.9
    assert stats["gaze_samples"] == 3


def test_takes_the_last_staleness_figures_since_they_are_cumulative():
    # the server reports running percentiles over the whole segment, so the final line is the
    # segment's answer -- averaging the intermediate lines would be wrong
    stats = gl.parse(SAMPLE)
    assert stats["staleness_p50"] == 0.3
    assert stats["staleness_p95"] == 6.7
    assert stats["staleness_max"] == 25.9


def test_ignores_nan_staleness_from_the_first_line():
    only_first = "\n".join(SAMPLE.splitlines()[:3])
    stats = gl.parse(only_first)
    assert stats["staleness_p95"] is None


def test_reports_per_eye_shift_range():
    stats = gl.parse(SAMPLE)
    assert stats["shift_left_min"] == 0.3 and stats["shift_left_max"] == 0.9
    assert stats["shift_right_min"] == -0.9 and stats["shift_right_max"] == 0.6


def test_clamp_fraction_counts_samples_at_the_limit():
    # MAX_CENTER_SHIFT is 0.9 in the patched server; hitting it means the centre could not follow
    # the eye any further, which is the number that decides whether a tight centre is usable
    stats = gl.parse(SAMPLE)
    assert stats["shift_samples"] == 6           # 3 frames x 2 eyes
    assert stats["clamped"] == 2                 # L +0.900 and R -0.900
    assert stats["clamp_fraction"] == 2 / 6


def test_records_the_fov_so_a_run_can_be_interpreted_later():
    stats = gl.parse(SAMPLE)
    assert stats["fov_deg"] == [
        {"left": -54.5, "right": 39.9, "up": 52.6, "down": -52.6},
        {"left": -39.9, "right": 54.5, "up": 52.6, "down": -52.6},
    ]


def test_empty_log_is_not_an_error():
    stats = gl.parse("")
    assert stats["gaze_samples"] == 0 and stats["shift_samples"] == 0
    assert stats["clamp_fraction"] is None
    assert stats["yaw_min"] is None


def test_gaze_absent_is_reported_rather_than_looking_like_zero_movement():
    # "no gaze in FaceData" means the tracker gave nothing -- very different from gaze pinned at 0,
    # and the difference decides whether a run is usable at all
    text = ("17:55:50.618 [ERROR] [GAZE-SRV] frame 0: no gaze in FaceData\n"
            "17:55:50.950 [ERROR] [GAZE-SRV] frame 72: no gaze in FaceData\n")
    stats = gl.parse(text)
    assert stats["gaze_samples"] == 0
    assert stats["no_gaze_reports"] == 2


# --- worn detection: a headset on a desk produces gaze that looks valid but never moves ---


def test_moving_gaze_reads_as_worn():
    assert gl.parse(SAMPLE)["likely_worn"] is True


def test_frozen_gaze_reads_as_not_worn():
    # measured verbatim from a real run where the headset sat on the desk: plausible-looking
    # angles, non-null poses, and zero movement for the entire segment
    frozen = "\n".join(
        f"17:0{i}:00.000 [ERROR] [GAZE-SRV] frame {i*72}: yaw +6.2 pitch +26.9 "
        f"(left true right false) | lag over 6 frames: p50 0.0 p95 0.0 max 0.0 deg (n={i*72})"
        for i in range(1, 6))
    stats = gl.parse(frozen)
    assert stats["gaze_samples"] == 5
    assert stats["likely_worn"] is False


def test_worn_is_unknown_when_there_is_too_little_to_judge():
    one = "17:00:00.000 [ERROR] [GAZE-SRV] frame 0: yaw +6.2 pitch +26.9 (left true right false) | lag over 6 frames: p50 NaN p95 NaN max NaN deg (n=0)"
    assert gl.parse(one)["likely_worn"] is None
    assert gl.parse("")["likely_worn"] is None
