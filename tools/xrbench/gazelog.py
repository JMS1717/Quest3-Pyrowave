"""Parse the patched ALVR server's gaze diagnostics out of crash_log.txt.

The server logs these at `error!` on purpose: ALVR's `info!` goes to its event stream, which the
benchmark's telemetry capture filters down to statistics, so anything below error level is
invisible during a sweep. `error!` reaches `crash_log.txt`, which persists as a file.

Two line shapes matter, both emitted once a second per segment:

    [GAZE-SRV] frame N: yaw +6.2 pitch +26.9 (left true right false) | lag over 6 frames: \
p50 0.3 p95 6.7 max 25.9 deg (n=138)
    [FOV-CENTER] frame N: L +0.518 +0.734  R +0.402 +0.734

plus `[VIEWS] fov[i] deg: ...` once per connection, which is the only record of the FOV and is
needed to interpret anything else.

The number this exists for is `clamp_fraction`: how often the foveation centre sat at
`MAX_CENTER_SHIFT`, unable to follow the eye any further. That decides whether a tight centre is
usable in a given workload, and it is not something a user can judge by feel.
"""
import re

# Mirrors MAX_CENTER_SHIFT in alvr/server_openvr/src/lib.rs. A shift of exactly +/-1 is a
# singularity in the foveation maths -- the outer edge region collapses to zero width and its
# coefficients divide by zero -- so the server clamps short of it.
MAX_CENTER_SHIFT = 0.9
CLAMP_EPSILON = 1e-3

# Worn detection. A headset sitting on a desk still reports gaze: plausible-looking angles, non-null
# poses, `left true`. What it does not do is *move*. A real run had yaw pinned at +6.2 and pitch at
# +26.9 with `lag max 0.0 deg` for two solid minutes, which reads as valid unless movement is
# checked -- and an unworn segment is worthless, so it must be caught rather than inferred from the
# headset merely being awake. Thresholds are far below anything a wearer produces: the same
# protocol worn gave a 64.9 deg yaw span and 25.9 deg peak staleness.
WORN_MIN_SAMPLES = 3
WORN_MIN_SPAN_DEG = 2.0
WORN_MIN_STALENESS_DEG = 0.5

_GAZE = re.compile(
    r"\[GAZE-SRV\] frame \d+: yaw ([-+][\d.]+) pitch ([-+][\d.]+).*?"
    r"p50 (\S+) p95 (\S+) max (\S+) deg \(n=(\d+)\)")
_NO_GAZE = re.compile(r"\[GAZE-SRV\] frame \d+: no gaze in FaceData")
_SHIFT = re.compile(
    r"\[FOV-CENTER\] frame \d+: L ([-+][\d.]+) ([-+][\d.]+)\s+R ([-+][\d.]+) ([-+][\d.]+)")
_FOV = re.compile(
    r"\[VIEWS\] fov\[(\d)\] deg: left ([-+][\d.]+) right ([-+][\d.]+) "
    r"up ([-+][\d.]+) down ([-+][\d.]+)")


def _number(text):
    """A float, or None for the NaN the server prints before it has any samples."""
    try:
        value = float(text)
    except ValueError:
        return None
    return None if value != value else value


def _span(values):
    return (min(values), max(values)) if values else (None, None)


def parse(text):
    """Summarise one segment's worth of gaze diagnostics."""
    yaws, pitches, left, right = [], [], [], []
    staleness = (None, None, None)
    no_gaze = len(_NO_GAZE.findall(text))
    fov = {}

    for match in _GAZE.finditer(text):
        yaws.append(float(match.group(1)))
        pitches.append(float(match.group(2)))
        # the server reports running percentiles over the whole segment, so the last line with
        # real numbers is the segment's answer -- intermediate lines are prefixes of it
        current = tuple(_number(match.group(i)) for i in (3, 4, 5))
        if current[1] is not None:
            staleness = current

    for match in _SHIFT.finditer(text):
        left.append(float(match.group(1)))
        right.append(float(match.group(3)))

    for match in _FOV.finditer(text):
        fov[int(match.group(1))] = {"left": float(match.group(2)), "right": float(match.group(3)),
                                    "up": float(match.group(4)), "down": float(match.group(5))}

    shifts = left + right
    clamped = sum(1 for v in shifts if abs(v) >= MAX_CENTER_SHIFT - CLAMP_EPSILON)
    yaw_min, yaw_max = _span(yaws)
    pitch_min, pitch_max = _span(pitches)
    left_min, left_max = _span(left)
    right_min, right_max = _span(right)

    yaw_span = (yaw_max - yaw_min) if yaws else None
    pitch_span = (pitch_max - pitch_min) if pitches else None
    if len(yaws) < WORN_MIN_SAMPLES or staleness[2] is None:
        likely_worn = None       # not enough evidence either way; do not guess
    else:
        likely_worn = (max(yaw_span, pitch_span) >= WORN_MIN_SPAN_DEG
                       and staleness[2] >= WORN_MIN_STALENESS_DEG)

    return {
        "gaze_samples": len(yaws),
        "yaw_span": yaw_span, "pitch_span": pitch_span,
        "likely_worn": likely_worn,
        "no_gaze_reports": no_gaze,
        "yaw_min": yaw_min, "yaw_max": yaw_max,
        "pitch_min": pitch_min, "pitch_max": pitch_max,
        "staleness_p50": staleness[0],
        "staleness_p95": staleness[1],
        "staleness_max": staleness[2],
        "shift_samples": len(shifts),
        "shift_left_min": left_min, "shift_left_max": left_max,
        "shift_right_min": right_min, "shift_right_max": right_max,
        "clamped": clamped,
        "clamp_fraction": (clamped / len(shifts)) if shifts else None,
        "fov_deg": [fov[i] for i in sorted(fov)] or None,
    }
