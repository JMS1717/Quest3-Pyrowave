"""Read the headset's thermal zones, so a segment records temperatures rather than a 0-3 status.

`thermal_end` from `dumpsys thermalservice` is a four-step severity, which is too coarse to tell
apart "warm" from "about to throttle", and it is why several of this project's comparisons came out
confounded: decode time moved, thermal status moved, and there was no way to say by how much.

Measured immediately after a heavy sweep, the hierarchy was:

    cpu        76.0 C     <- hottest
    sys-therm  73.3
    aoss       73.0
    gpuss/ddr  71.6
    video      69.9       <- the decode block, 6 C cooler than the CPU
    camera     67.9

So the decoder is **not** what throttles this headset, which means reducing encoded pixels is the
wrong lever for thermals. That matches the resolution sweep the same morning, where decode time was
flat against a 19% pixel increase but rose with temperature.

Zones are read from sysfs rather than `dumpsys` because the names and values are the kernel's own.
"""


def _zone_group(name):
    """Which family a zone belongs to, or None for ones not worth reporting separately."""
    for prefix, group in (("cpu", "cpu"), ("gpuss", "gpu"), ("video", "video"), ("ddr", "ddr"),
                          ("nspss", "nsp"), ("camera", "camera"), ("sys-therm", "sys"),
                          ("aoss", "aoss")):
        if name.startswith(prefix):
            return group
    return None


def parse_zones(text):
    """Summarise `<millidegrees> <zone name>` lines into per-family maxima.

    Malformed lines are skipped rather than raising: this runs around a benchmark segment, and a
    kernel that prints something unexpected must not fail an otherwise good run."""
    groups = {}
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        raw, name = parts
        try:
            celsius = int(raw) / 1000.0
        except ValueError:
            continue
        group = _zone_group(name.strip())
        if group is None:
            continue
        groups[group] = max(groups.get(group, celsius), celsius)

    hottest_group = max(groups, key=groups.get) if groups else None
    return {
        "cpu_max_c": groups.get("cpu"),
        "gpu_max_c": groups.get("gpu"),
        "video_c": groups.get("video"),
        "ddr_c": groups.get("ddr"),
        "nsp_max_c": groups.get("nsp"),
        "camera_max_c": groups.get("camera"),
        "sys_max_c": groups.get("sys"),
        "aoss_max_c": groups.get("aoss"),
        "hottest_group": hottest_group,
        "hottest_c": groups.get(hottest_group) if hottest_group else None,
    }


def rise(before, after):
    """Per-field temperature rise across a segment. None wherever either sample is missing."""
    out = {}
    for key, value in after.items():
        start = before.get(key)
        if isinstance(value, (int, float)) and isinstance(start, (int, float)):
            out[key] = round(value - start, 1)
        else:
            out[key] = None
    return out
