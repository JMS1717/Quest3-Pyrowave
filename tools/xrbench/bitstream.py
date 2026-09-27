"""Read the server-side bitstream tap: a raw Annex B stream plus a per-frame index.

The tap writes two files side by side. `<name>.h264` / `.h265` is the encoded access units
concatenated with nothing added, so ffmpeg decodes it directly -- that is the point, since an
offline conformance decode is what lets image quality be measured without capturing anything on
the headset. `<name>.idx` is a CSV saying where each frame sits and what it was:

    # alvr-bitstream-tap v1 codec=h264
    pts_ns,offset,bytes,idr
    9215574029609,0,695439,1

pts_ns is ALVR's `target_timestamp`, the same key `GraphStatistics.target_timestamp_ns` carries,
so a tapped frame joins to its latency numbers.
"""
import csv


def read_codec(path):
    """The codec named in the index header, or None if the header is missing or unrecognised."""
    with open(path, encoding="utf-8") as handle:
        first = handle.readline()
    if not first.startswith("#"):
        return None
    for token in first.lstrip("#").split():
        if token.startswith("codec="):
            return token.split("=", 1)[1]
    return None


def read_index(path):
    """Per-frame rows, in file order."""
    with open(path, encoding="utf-8") as handle:
        rows = list(csv.DictReader(line for line in handle if not line.startswith("#")))
    return [{"pts_ns": int(r["pts_ns"]), "offset": int(r["offset"]),
             "bytes": int(r["bytes"]), "idr": r["idr"] == "1"} for r in rows]


def keyed(rows):
    """Only the frames that can be joined to anything.

    The encoder emits some frames before a target timestamp exists -- IDRs at stream start, which
    ALVR reports with pts 0. They belong in the payload because the decoder needs them, but they
    identify no frame, so any timing or joining work must leave them out."""
    return [r for r in rows if r["pts_ns"] > 0]


def gaps(rows):
    """(index, expected_offset, actual_offset) wherever a frame does not start where the previous
    one ended. The payload is meant to be one continuous stream; a gap means a frame was written
    to the stream but not recorded, or recorded but not written, and any offset-based seek after
    that point is wrong."""
    out = []
    expected = rows[0]["offset"] if rows else 0
    for i, row in enumerate(rows):
        if row["offset"] != expected:
            out.append((i - 1, expected, row["offset"]))
        expected = row["offset"] + row["bytes"]
    return out


def bitrate_mbps(rows):
    """Achieved bitrate over the span the timestamps actually cover, or None if under two frames.

    Deliberately not frames x nominal rate: that would divide by the frame rate we *asked* for and
    so hide dropped frames, which is one of the things the tap exists to expose."""
    timed = keyed(rows)
    if len(timed) < 2:
        return None
    span_s = (timed[-1]["pts_ns"] - timed[0]["pts_ns"]) / 1e9
    if span_s <= 0:
        return None
    # only the timestamped frames, on both sides of the division. Their bytes because the
    # unkeyed ones were encoded before this span began and counting them here would overstate the
    # rate; their span because a single pts of 0 would stretch it by ~10000 s and turn a 400 Mbps
    # stream into a few Mbps.
    return sum(r["bytes"] for r in timed) * 8 / span_s / 1e6


def read_counters(path):
    """{"frames": n, "dropped": n} from the trailing comment the tap writes when it closes, or
    None if there isn't one -- which means the capture was cut short and may be truncated."""
    last = None
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#") and "frames=" in line:
                last = line
    if last is None:
        return None
    out = {}
    for token in last.lstrip("#").split():
        key, _, value = token.partition("=")
        if key in ("frames", "dropped") and value.isdigit():
            out[key] = int(value)
    return out or None


def idr_indices(rows):
    """Frame indices of the IDRs -- the points an offline decode can be started from."""
    return [i for i, row in enumerate(rows) if row["idr"]]
