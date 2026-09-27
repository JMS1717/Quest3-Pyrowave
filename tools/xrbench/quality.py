"""Objective image quality of live PyroWave cells.

A quality cell records two things for the same frames: the encoded stream the headset was sent
(bitstream tap, `<cell>/tap-*.idx` + payload) and the lossless encoder input (runtime dump,
`<cell>/quality/source.y4m` + `.frames.csv`). Frames are paired by target timestamp, the tapped ones
are decoded with PyroWave's PC decoder (bit-identical to the headset within one code value), and
the decode is scored against the source with the harness's ffmpeg scorer (PSNR, SSIM, PSNR-HVS,
VMAF). The result is `<cell>/quality.json`, which `xrbench.matrix` picks up.

This measures what the codec delivered for the frames that arrived, at the encoded resolution. It
does not include transport loss (a dropped frame is not scored) or the display path.

    python -m xrbench.quality <run dir> [--decoder pyrowave-decode.exe] [--ffmpeg ffmpeg]
"""
import argparse
import csv
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

from . import bitstream
from . import paths
from . import pyrowave_wave
from . import rdmatrix

DEFAULT_DECODER = paths.CODE.parents[1] / "research" / "pyrowave" / "build-pc" / "Release" / "pyrowave-decode.exe"


def read_sidecar(path):
    """The dump's frames, in dump order: dump_index, encoder frame count, target timestamp."""
    with open(path, newline="", encoding="utf-8") as f:
        return [{"index": int(r["dump_index"]), "frame": int(r["frame_count"]), "ts": int(r["target_timestamp_ns"])}
                for r in csv.DictReader(f)]


def match(dump_frames, tap_rows):
    """Pair each dumped frame with the tapped frame of the same target timestamp.
    Returns ([(dump_index, tap_row), ...] in dump order, [dump indices with no tapped frame])."""
    by_ts = {r["pts_ns"]: r for r in tap_rows}
    pairs, missing = [], []
    for f in dump_frames:
        row = by_ts.get(f["ts"])
        if row is None:
            missing.append(f["index"])
        else:
            pairs.append((f["index"], row))
    return pairs, missing


def tap_dimensions(idx_path):
    """(width, height) of the encoded frames, from the tap index header."""
    with open(idx_path, encoding="utf-8") as f:
        first = f.readline()
    w = re.search(r"\bwidth=(\d+)", first)
    h = re.search(r"\bheight=(\d+)", first)
    if not (w and h):
        raise ValueError(f"{idx_path}: no width/height in the header")
    return int(w.group(1)), int(h.group(1))


def write_wave(payload_path, rows, out_path, width, height, fps=90):
    """A PyroWave container holding exactly `rows`, in the given order. Returns frames written."""
    written = 0
    with open(payload_path, "rb") as payload, open(out_path, "wb") as out:
        out.write(pyrowave_wave.header(width, height, fps_num=fps))
        for row in rows:
            payload.seek(row["offset"])
            data = payload.read(row["bytes"])
            if len(data) != row["bytes"]:
                break
            out.write(struct.pack("<I", len(data)))
            out.write(data)
            written += 1
    return written


def _y4m_layout(path):
    with open(path, "rb") as f:
        header = f.readline()
    w = int(re.search(rb"\bW(\d+)", header).group(1))
    h = int(re.search(rb"\bH(\d+)", header).group(1))
    chroma = re.search(rb"\bC(\S+)", header)
    c = chroma.group(1) if chroma else b"420jpeg"
    planes = 3 if c.startswith(b"444") else 1.5
    return header, int(w * h * planes)


def write_y4m_subset(src, indices, out):
    """Copy the frames `indices` (0-based, in that order) of a y4m into a new y4m."""
    header, frame_bytes = _y4m_layout(src)
    written = 0
    with open(src, "rb") as f, open(out, "wb") as o:
        o.write(header)
        start = len(header)
        for i in indices:
            f.seek(start + i * (len(b"FRAME\n") + frame_bytes))
            tag = f.readline()
            if not tag.startswith(b"FRAME"):
                raise ValueError(f"{src}: frame {i} does not start with FRAME")
            data = f.read(frame_bytes)
            if len(data) != frame_bytes:
                break
            o.write(b"FRAME\n" + data)
            written += 1
    return written


def ensure_full_range(path):
    """PyroWave's stream is full range; a y4m that does not say so is read as limited by every
    tool, which costs ~29 dB against a full-range reference (ratequality.declared_colour_range)."""
    p = Path(path)
    data = p.read_bytes()
    header, rest = data.split(b"\n", 1)
    if b"XCOLORRANGE=FULL" in header:
        return
    header = re.sub(rb" XCOLORRANGE=\S+", b"", header) + b" XCOLORRANGE=FULL"
    p.write_bytes(header + b"\n" + rest)


def find_tap(cell):
    """(index, payload) of the cell's tap; the largest if the server opened more than one."""
    idxs = sorted(Path(cell).glob("tap-*.idx"), key=lambda p: p.stat().st_size, reverse=True)
    for idx in idxs:
        for ext in (".h264", ".h265", ".bin"):
            payload = idx.with_suffix(ext)
            if payload.exists():
                return idx, payload
    return None, None


def score_cell(cell, decoder=DEFAULT_DECODER, ffmpeg="ffmpeg"):
    cell = Path(cell)
    source = cell / "quality" / "source.y4m"
    sidecar = Path(str(source) + ".frames.csv")
    idx, payload = find_tap(cell)
    if not (source.exists() and sidecar.exists() and idx):
        return None
    work = cell / "quality"
    dump_frames = read_sidecar(sidecar)
    rows = pyrowave_wave.merge_packets(bitstream.read_index(idx))
    pairs, missing = match(dump_frames, rows)
    result = {"frames": 0, "dumped": len(dump_frames), "missing_from_tap": missing}
    if pairs:
        w, h = tap_dimensions(idx)
        wave, decoded, reference = work / "tapped.wave", work / "decoded.y4m", work / "reference.y4m"
        write_wave(payload, [r for _, r in pairs], wave, w, h)
        run = subprocess.run([str(decoder), str(wave), str(decoded)], capture_output=True, text=True)
        if run.returncode != 0 or not decoded.exists():
            result["error"] = f"decode failed: {run.stderr[-300:]}"
        else:
            ensure_full_range(decoded)
            write_y4m_subset(source, [i for i, _ in pairs], reference)
            s = rdmatrix.score(ffmpeg, decoded, reference, work)
            result.update({"psnr_y": s.get("psnr_y"), "psnr_u": s.get("psnr_u"), "psnr_v": s.get("psnr_v"),
                           "ssim": s.get("ssim_y"), "psnr_hvs": s.get("psnr_hvs"), "vmaf": s.get("vmaf"),
                           "frames": len(pairs), "encoded": f"{w}x{h}"})
            for p in (wave, decoded, reference):
                p.unlink(missing_ok=True)
    (cell / "quality.json").write_text(json.dumps(result, indent=2))
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("runs", nargs="+")
    p.add_argument("--decoder", default=str(DEFAULT_DECODER))
    p.add_argument("--ffmpeg", default="ffmpeg")
    a = p.parse_args(argv)
    n = 0
    for run in a.runs:
        for q in sorted(Path(run).glob("*/quality")):
            r = score_cell(q.parent, a.decoder, a.ffmpeg)
            n += 1
            print(f"{q.parent.name}: {r if r is None else {k: r.get(k) for k in ('frames', 'psnr_y', 'ssim', 'vmaf', 'error')}}")
    if not n:
        sys.exit("no quality cells (*/quality) under the given runs")


if __name__ == "__main__":
    main()
