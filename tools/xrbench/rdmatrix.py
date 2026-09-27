"""Offline PyroWave rate-distortion matrix: Quality = f(per-eye resolution, bytes per frame).

The brief: before spending headset time, map where PyroWave's reconstruction
quality flattens against bytes/frame at each square per-eye resolution from 1920 to 2560, and
whether the 2560 @ 416,667 B (~300 Mbps at 90 Hz) reference sits below, near or above that knee.

Verified against the pyrowave checkout: the evaluator the brief names (`pyrowave-psnr-hvs-m`)
is not built anywhere and needs FFmpeg+SDL to build, so this uses what exists on the PC --
`pyrowave-encode <y4m> <out> <bytes_per_frame>` (an exact per-frame cap), `pyrowave-decode`,
and ffmpeg 8 with libvmaf for PSNR, SSIM and PSNR-HVS. Scores are against the *scaled* reference
(the same semantics as psnr.cpp's --scale-size), plus a second, CALCULATED set against the 2560
original after a lanczos upscale of the decoded frame.

Three stages, two machines:

    python -m xrbench.rdmatrix prepare --kodak <dir> --out <dir>     # Mac: five 2560x2560 PNGs
    python -m xrbench.rdmatrix run --sources <dir> --tools <dir> --out results.csv   # PC
    python -m xrbench.rdmatrix analyze results.csv --out <dir>       # Mac: report + plots

Categories are kept apart in every table: OBSERVED (measured), CALCULATED (derived by formula or
through a resampler we chose), INTERPOLATED (read between measured caps), UNKNOWN.
"""
import csv
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from .ratequality import parse_psnr

RESOLUTIONS = (1920, 2048, 2176, 2304, 2432, 2560)
REFERENCE_RESOLUTION = 2560
REFERENCE_BYTES = 416_667                      # ~300 Mbps at 90 Hz
SWEEP_BYTES = tuple(range(150_000, 550_001, 50_000))
FPS_EQUIVALENT = 90
KODAK = ("kodim04", "kodim07", "kodim08", "kodim19")
# pyrowave-encode's container: 8-byte magic, 8 int32, then per frame a uint32 length + payload.
CONTAINER_OVERHEAD = 8 + 32 + 4


# ---- arithmetic (CALCULATED) ------------------------------------------------------------------

def pixel_ratio(resolution):
    return (resolution / REFERENCE_RESOLUTION) ** 2


def constant_bpp_bytes(resolution):
    """The cap that keeps the reference's bits per pixel at this resolution."""
    return int(round(REFERENCE_BYTES * pixel_ratio(resolution)))


def caps_for(resolution):
    """The sweep, the 300 Mbps reference, and this resolution's constant-bpp point; sorted, unique."""
    return tuple(sorted(set(SWEEP_BYTES) | {REFERENCE_BYTES, constant_bpp_bytes(resolution)}))


def mbps_at_90hz(cap_bytes):
    return cap_bytes * 8 * FPS_EQUIVALENT / 1_000_000


def bits_per_pixel(cap_bytes, resolution):
    """Bits per pixel of the stereo (side-by-side) frame the pipeline encodes."""
    return cap_bytes * 8 / (2 * resolution * resolution)


# ---- sources ----------------------------------------------------------------------------------

def tile_to_square(img, size):
    """Fill a size x size frame with the image at native scale, mirroring alternate tiles so the
    seams do not add a second grid. Never upscales: the content keeps its true detail."""
    h, w = img.shape[:2]
    out = np.zeros((size, size, 3), img.dtype)
    for i, y in enumerate(range(0, size, h)):
        for j, x in enumerate(range(0, size, w)):
            tile = img
            if j % 2:
                tile = tile[:, ::-1]
            if i % 2:
                tile = tile[::-1, :]
            th, tw = min(h, size - y), min(w, size - x)
            out[y:y + th, x:x + tw] = tile[:th, :tw]
    return out


def prepare_sources(kodak_dir, out_dir):
    """Five lossless 2560x2560 per-eye PNGs: four Kodak tilings and the synthetic panel eye."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in KODAK:
        img = cv2.imread(str(Path(kodak_dir) / f"{name}.png"))
        if img is None:
            raise FileNotFoundError(f"{name}.png not in {kodak_dir}")
        path = out_dir / f"kodak_{name}.png"
        cv2.imwrite(str(path), tile_to_square(img, REFERENCE_RESOLUTION))
        written.append(path)
    from . import scene_app
    scene_app.selftest(out_dir, REFERENCE_RESOLUTION, REFERENCE_RESOLUTION, frames=1)
    (out_dir / "eye_reference.png").rename(out_dir / "synthetic_panel.png")
    written.append(out_dir / "synthetic_panel.png")
    return written


def dataset_of(source_name):
    return "kodak" if source_name.startswith("kodak_") else "synthetic"


# ---- y4m ----------------------------------------------------------------------------------------

def y4m_header_line(width, height, fps=FPS_EQUIVALENT):
    return f"YUV4MPEG2 W{width} H{height} F{fps}:1 Ip A1:1 C444 XCOLORRANGE=FULL"


def bgr_to_ycbcr_full(bgr):
    """BT.709 full range, 8-bit, the pipeline's convention (FrameRender's planar pass)."""
    rgb = bgr[:, :, ::-1].astype(np.float32)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    cb = (b - y) / 1.8556 + 128.0
    cr = (r - y) / 1.5748 + 128.0
    return [np.clip(np.rint(p), 0, 255).astype(np.uint8) for p in (y, cb, cr)]


def write_y4m(path, frame_bgr):
    """One 4:4:4 full-range frame."""
    h, w = frame_bgr.shape[:2]
    planes = bgr_to_ycbcr_full(frame_bgr)
    with open(path, "wb") as f:
        f.write((y4m_header_line(w, h) + "\n").encode())
        f.write(b"FRAME\n")
        for p in planes:
            f.write(np.ascontiguousarray(p).tobytes())


def read_y4m(path):
    """(width, height, [Y, Cb, Cr]) of the first frame of a 4:4:4 8-bit y4m."""
    with open(path, "rb") as f:
        header = f.readline().decode("ascii", "replace")
        w = int(re.search(r" W(\d+)", header).group(1))
        h = int(re.search(r" H(\d+)", header).group(1))
        if "C444" not in header:
            raise ValueError(f"{path}: expected C444, header {header.strip()}")
        tag = f.readline()
        if not tag.startswith(b"FRAME"):
            raise ValueError(f"{path}: no FRAME tag")
        planes = [np.frombuffer(f.read(w * h), np.uint8).reshape(h, w) for _ in range(3)]
    return w, h, planes


def ycbcr_full_to_bgr(planes):
    y, cb, cr = [p.astype(np.float32) for p in planes]
    r = y + 1.5748 * (cr - 128.0)
    b = y + 1.8556 * (cb - 128.0)
    g = y - 0.1873 * (cb - 128.0) - 0.4681 * (cr - 128.0)
    return np.clip(np.rint(np.stack([b, g, r], axis=-1)), 0, 255).astype(np.uint8)


def side_by_side(eye_bgr, resolution):
    """The stereo frame the pipeline encodes, left = right (no lossless stereo pairs exist)."""
    if resolution != eye_bgr.shape[0]:
        eye_bgr = cv2.resize(eye_bgr, (resolution, resolution), interpolation=cv2.INTER_LANCZOS4)
    return np.concatenate([eye_bgr, eye_bgr], axis=1)


# ---- one cell (OBSERVED) ---------------------------------------------------------------------

def parse_ssim(text):
    m = re.search(r"SSIM Y:([0-9.]+).*?U:([0-9.]+).*?V:([0-9.]+).*?All:([0-9.]+)", text)
    return None if not m else {"ssim_y": float(m.group(1)), "ssim_all": float(m.group(4))}


def parse_vmaf_log(path):
    """psnr_hvs and vmaf from libvmaf's JSON log (pooled means)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    pooled = data.get("pooled_metrics", {})
    out = {}
    for key, col in (("psnr_hvs", "psnr_hvs"), ("vmaf", "vmaf"), ("psnr_hvs_y", "psnr_hvs_y")):
        if key in pooled and "mean" in pooled[key]:
            out[col] = float(pooled[key]["mean"])
    return out


def score(ffmpeg, distorted_y4m, reference_y4m, workdir):
    """PSNR (ffmpeg psnr), SSIM (ffmpeg ssim), PSNR-HVS + VMAF (libvmaf) of distorted vs reference."""
    # libvmaf's option parser treats ':' as a separator, so a Windows drive path cannot be given
    # as log_path; the log is written relative to the work dir instead.
    log = Path(workdir) / "vmaf.json"
    graph = ("[0:v]split=3[a1][a2][a3];[1:v]split=3[b1][b2][b3];"
             "[a1][b1]psnr=stats_file=-[p];[a2][b2]ssim=stats_file=-[s];"
             "[a3][b3]libvmaf=feature=name=psnr_hvs:log_fmt=json:log_path=vmaf.json[v]")
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(Path(distorted_y4m).resolve()), "-i", str(Path(reference_y4m).resolve()),
         "-lavfi", graph, "-map", "[p]", "-map", "[s]", "-map", "[v]", "-f", "null", "-"],
        capture_output=True, text=True, check=False, cwd=str(workdir))
    text = result.stdout + result.stderr
    out = {}
    out.update(parse_psnr(text) or {})
    out.update(parse_ssim(text) or {})
    out.update(parse_vmaf_log(log))
    try:
        os.remove(log)
    except OSError:
        pass
    return out


def run_cell(source_png, resolution, cap_bytes, tools, workdir):
    """Encode the source at this resolution under an exact per-frame cap, decode, score twice."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    eye = cv2.imread(str(source_png))
    ref_scaled = workdir / "ref_scaled.y4m"
    ref_full = workdir / "ref_full.y4m"
    wave = workdir / "cell.wave"
    decoded = workdir / "decoded.y4m"
    upscaled = workdir / "upscaled.y4m"

    if not ref_scaled.exists() or getattr(run_cell, "_scaled_for", None) != (source_png, resolution):
        write_y4m(ref_scaled, side_by_side(eye, resolution))
        run_cell._scaled_for = (source_png, resolution)
    if not ref_full.exists() or getattr(run_cell, "_full_for", None) != source_png:
        write_y4m(ref_full, side_by_side(eye, REFERENCE_RESOLUTION))
        run_cell._full_for = source_png

    t0 = time.perf_counter()
    enc = subprocess.run([tools["encode"], str(ref_scaled), str(wave), str(cap_bytes)],
                         capture_output=True, text=True, check=False)
    encode_s = time.perf_counter() - t0
    row = {"resolution": resolution, "cap_bytes": cap_bytes,
           "actual_bytes": max(wave.stat().st_size - CONTAINER_OVERHEAD, 0) if wave.exists() else 0,
           "encode_s": round(encode_s, 3), "decode_s": None}
    if enc.returncode != 0 or row["actual_bytes"] == 0:
        row["error"] = (enc.stderr or enc.stdout)[-200:]
        return row

    t0 = time.perf_counter()
    dec = subprocess.run([tools["decode"], str(wave), str(decoded)], capture_output=True, text=True, check=False)
    row["decode_s"] = round(time.perf_counter() - t0, 3)
    if dec.returncode != 0 or not decoded.exists():
        row["error"] = (dec.stderr or dec.stdout)[-200:]
        return row

    # OBSERVED: against the scaled reference (psnr.cpp semantics)
    for k, v in score(tools["ffmpeg"], decoded, ref_scaled, workdir).items():
        row[f"scaled_{k}"] = v
    # CALCULATED: against the 2560 original after a lanczos upscale of what was decoded
    w, h, planes = read_y4m(decoded)
    bgr = ycbcr_full_to_bgr(planes)
    if (w, h) != (2 * REFERENCE_RESOLUTION, REFERENCE_RESOLUTION):
        bgr = cv2.resize(bgr, (2 * REFERENCE_RESOLUTION, REFERENCE_RESOLUTION), interpolation=cv2.INTER_LANCZOS4)
    write_y4m(upscaled, bgr)
    for k, v in score(tools["ffmpeg"], upscaled, ref_full, workdir).items():
        row[f"full_{k}"] = v
    for path in (wave, decoded, upscaled):
        try:
            os.remove(path)
        except OSError:
            pass
    return row


COLUMNS = ["dataset", "source", "resolution", "pixel_ratio", "cap_bytes", "cap_kb", "mbps_90hz",
           "bits_per_pixel_stereo", "is_reference", "is_constant_bpp", "actual_bytes", "fill_pct",
           "encode_s", "decode_s",
           "scaled_psnr_y", "scaled_psnr_u", "scaled_psnr_v", "scaled_ssim_y", "scaled_ssim_all",
           "scaled_psnr_hvs", "scaled_vmaf",
           "full_psnr_y", "full_psnr_u", "full_psnr_v", "full_ssim_y", "full_ssim_all",
           "full_psnr_hvs", "full_vmaf", "error"]


def run_matrix(sources_dir, tools_dir, out_csv, ffmpeg="ffmpeg", resolutions=RESOLUTIONS, log=print):
    tools_dir = Path(tools_dir)
    tools = {"encode": str(tools_dir / "pyrowave-encode.exe"), "decode": str(tools_dir / "pyrowave-decode.exe"),
             "ffmpeg": ffmpeg}
    sources = sorted(Path(sources_dir).glob("*.png"))
    if not sources:
        raise FileNotFoundError(f"no PNG sources in {sources_dir}")
    workdir = Path(out_csv).with_suffix("") / "work"
    rows = []
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for src in sources:
            for res in resolutions:
                for cap in caps_for(res):
                    t0 = time.perf_counter()
                    cell = run_cell(src, res, cap, tools, workdir)
                    row = {"dataset": dataset_of(src.stem), "source": src.stem, "resolution": res,
                           "pixel_ratio": round(pixel_ratio(res), 4), "cap_bytes": cap,
                           "cap_kb": cap / 1000, "mbps_90hz": round(mbps_at_90hz(cap), 2),
                           "bits_per_pixel_stereo": round(bits_per_pixel(cap, res), 4),
                           "is_reference": int(cap == REFERENCE_BYTES),
                           "is_constant_bpp": int(cap == constant_bpp_bytes(res))}
                    row.update({k: v for k, v in cell.items() if k in COLUMNS})
                    row["fill_pct"] = round(100.0 * row.get("actual_bytes", 0) / cap, 1)
                    for k, v in row.items():
                        if isinstance(v, float):
                            row[k] = round(v, 4)
                    writer.writerow(row)
                    f.flush()
                    rows.append(row)
                    log(f"{src.stem} {res} {cap}: {row.get('actual_bytes')} B, "
                        f"psnr_hvs {row.get('scaled_psnr_hvs')} psnr_y {row.get('scaled_psnr_y')} "
                        f"({time.perf_counter() - t0:.1f} s){' ERROR ' + row['error'] if row.get('error') else ''}")
    return rows


# ---- analysis ----------------------------------------------------------------------------------

def marginal_gains(curve, step=50_000):
    """[(cap, dQ per `step` bytes)] between adjacent points of a (cap, quality) curve."""
    out = []
    for (b0, q0), (b1, q1) in zip(curve, curve[1:]):
        out.append((b1, (q1 - q0) / (b1 - b0) * step))
    return out


def knee_position(curve, cap, flat_fraction=0.25):
    """Where `cap` sits on a (cap, quality) curve: the knee is the first step whose marginal gain
    falls below `flat_fraction` of the curve's first step. 'near' is within one step of it."""
    gains = marginal_gains(curve)
    if not gains:
        return "unknown"
    first = gains[0][1]
    knee = None
    for b, g in gains:
        if first > 0 and g < flat_fraction * first:
            knee = b
            break
    if knee is None:
        return "below"          # never flattened inside the measured range
    step = curve[1][0] - curve[0][0]
    if cap < knee - step:
        return "below"
    if cap > knee + step:
        return "above"
    return "near"


def pareto(cells, quality_key):
    """Cells not dominated: another cell with quality >= and resolution <= and bytes <=, with at
    least one of those strict. Resolution and bytes are both costs (decode, transport)."""
    front = []
    for c in cells:
        q = c.get(quality_key)
        if q is None:
            continue
        dominated = False
        for o in cells:
            oq = o.get(quality_key)
            if o is c or oq is None:
                continue
            if (oq >= q and o["resolution"] <= c["resolution"] and o["cap_bytes"] <= c["cap_bytes"]
                    and (oq > q or o["resolution"] < c["resolution"] or o["cap_bytes"] < c["cap_bytes"])):
                dominated = True
                break
        if not dominated:
            front.append(c)
    return front


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_results(path):
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in r:
            if k in ("dataset", "source", "error"):
                continue
            r[k] = _num(r[k])
        r["resolution"] = int(r["resolution"]) if r["resolution"] is not None else None
        r["cap_bytes"] = int(r["cap_bytes"]) if r["cap_bytes"] is not None else None
    return rows


def analyze(results_csv, out_dir, primary="scaled_psnr_hvs"):
    """Tables A-E per dataset and per source, plots, and the live-test region. Returns report text."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [r for r in load_results(results_csv) if not r.get("error")]
    if not rows:
        raise ValueError("no successful cells")
    if all(r.get(primary) is None for r in rows):
        primary = "scaled_psnr_y"
    lines = [f"# PyroWave offline resolution x bytes/frame matrix\n",
             f"Primary quality metric: `{primary}` (OBSERVED, against the scaled reference). "
             f"Secondary: `full_psnr_y`/`full_ssim_all` (CALCULATED, decoded frame lanczos-upscaled to 2560 "
             f"and scored against the original). Datasets are never averaged together.\n"]
    sources = sorted({r["source"] for r in rows})
    for src in sources:
        srows = [r for r in rows if r["source"] == src]
        ds = srows[0]["dataset"]
        lines.append(f"\n## {src} ({ds})\n")
        # C: per-resolution RD curves + marginal gains
        lines.append("### Analysis C: rate-distortion per resolution (OBSERVED), marginal gain per +50 KB (CALCULATED)\n")
        lines.append("| res | " + " | ".join(f"{c // 1000}k" for c in caps_for(1920)) + " |")
        lines.append("|---|" + "---|" * len(caps_for(1920)))
        fig, ax = plt.subplots(figsize=(8, 5))
        knee_lines = []
        for res in RESOLUTIONS:
            rr = sorted([r for r in srows if r["resolution"] == res and r.get(primary) is not None], key=lambda r: r["cap_bytes"])
            byc = {r["cap_bytes"]: r[primary] for r in rr}
            lines.append(f"| {res} | " + " | ".join(f"{byc[c]:.2f}" if c in byc else "" for c in caps_for(1920)) + " |")
            curve = [(r["cap_bytes"], r[primary]) for r in rr if r["cap_bytes"] in SWEEP_BYTES]
            if len(curve) >= 3:
                gains = marginal_gains(curve)
                knee_lines.append(f"- {res}: gains per +50 KB " + ", ".join(f"{b // 1000}k:{g:+.2f}" for b, g in gains)
                                  + f"; 416,667 B is **{knee_position(curve, REFERENCE_BYTES)}** the knee (CALCULATED from the sweep points; 416,667 itself is OBSERVED)")
            ax.plot([b / 1000 for b, _ in curve], [q for _, q in curve], marker="o", label=str(res))
        ax.set_xlabel("bytes per stereo frame (KB)"); ax.set_ylabel(primary); ax.set_title(src); ax.grid(True, alpha=0.3); ax.legend(title="per-eye res")
        ax.axvline(REFERENCE_BYTES / 1000, color="k", ls="--", alpha=0.5)
        fig.tight_layout(); fig.savefig(out_dir / f"rd_{src}.png", dpi=110); plt.close(fig)
        lines.extend(knee_lines)
        # A: fixed bandwidth columns
        lines.append("\n### Analysis A: quality vs resolution at fixed bytes/frame (OBSERVED)\n")
        lines.append("| bytes | " + " | ".join(str(r) for r in RESOLUTIONS) + " | best |")
        lines.append("|---|" + "---|" * (len(RESOLUTIONS) + 1))
        for cap in list(SWEEP_BYTES) + [REFERENCE_BYTES]:
            vals = {r["resolution"]: r[primary] for r in srows if r["cap_bytes"] == cap and r.get(primary) is not None}
            if not vals:
                continue
            best = max(vals, key=vals.get)
            lines.append(f"| {cap:,} | " + " | ".join(f"{vals[res]:.2f}" if res in vals else "" for res in RESOLUTIONS) + f" | {best} |")
        # B: constant bpp
        lines.append("\n### Analysis B: constant bits-per-pixel points (OBSERVED at CALCULATED caps)\n")
        lines.append("| res | cap bytes | actual | " + primary + " | full_psnr_y | full_ssim_all |")
        lines.append("|---|---|---|---|---|---|")
        for res in RESOLUTIONS:
            r = next((r for r in srows if r["resolution"] == res and r["cap_bytes"] == constant_bpp_bytes(res)), None)
            if r:
                lines.append(f"| {res} | {r['cap_bytes']:,} | {int(r['actual_bytes'] or 0):,} | {r.get(primary) or 0:.2f} | "
                             f"{r.get('full_psnr_y') or 0:.2f} | {r.get('full_ssim_all') or 0:.4f} |")
        # D: the reference region
        ref = [r for r in srows if r["resolution"] == REFERENCE_RESOLUTION and r["cap_bytes"] in (350_000, 400_000, REFERENCE_BYTES, 450_000, 500_000)]
        lines.append("\n### Analysis D: the 2560 @ 416,667 B region (all OBSERVED)\n")
        for r in sorted(ref, key=lambda r: r["cap_bytes"]):
            lines.append(f"- 2560 @ {r['cap_bytes']:,} B (actual {int(r['actual_bytes'] or 0):,}): {primary} {r.get(primary) or 0:.2f}, full_psnr_y {r.get('full_psnr_y') or 0:.2f}")
        # E: Pareto on the primary metric and on full_psnr_y
        for key in (primary, "full_psnr_y"):
            front = pareto([r for r in srows if r.get(key) is not None], key)
            lines.append(f"\n### Analysis E: Pareto frontier on `{key}` ({'OBSERVED' if key.startswith('scaled') else 'CALCULATED'})\n")
            for r in sorted(front, key=lambda r: (r["cap_bytes"], r["resolution"])):
                lines.append(f"- {r['resolution']} @ {r['cap_bytes']:,} B -> {r[key]:.2f}")
    # cross-dataset plot: quality vs resolution at the reference bytes
    fig, ax = plt.subplots(figsize=(8, 5))
    for src in sources:
        pts = sorted([(r["resolution"], r[primary]) for r in rows if r["source"] == src and r["cap_bytes"] == REFERENCE_BYTES and r.get(primary) is not None])
        if pts:
            ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", label=src)
    ax.set_xlabel("per-eye resolution"); ax.set_ylabel(primary); ax.set_title("quality vs resolution at 416,667 B/frame"); ax.grid(True, alpha=0.3); ax.legend()
    fig.tight_layout(); fig.savefig(out_dir / "quality_vs_resolution_at_reference.png", dpi=110); plt.close(fig)
    lines.append("\n## UNKNOWN\n\n- Stereo disparity effects: left = right in every source (no lossless stereo pairs exist).\n"
                 "- PSNR-HVS-M with PyroWave's own CSF weights: not computed (its evaluator is not built); libvmaf's psnr_hvs is the nearest available.\n"
                 "- GPU memory bandwidth: not available offline. Encode/decode seconds are process wall time on the RTX 3090, not the headset.\n")
    report = "\n".join(lines) + "\n"
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    return report


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--kodak", required=True); p.add_argument("--out", required=True)
    r = sub.add_parser("run"); r.add_argument("--sources", required=True); r.add_argument("--tools", required=True)
    r.add_argument("--out", required=True); r.add_argument("--ffmpeg", default="ffmpeg")
    r.add_argument("--resolutions", default=",".join(str(x) for x in RESOLUTIONS))
    a = sub.add_parser("analyze"); a.add_argument("results"); a.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.cmd == "prepare":
        for path in prepare_sources(args.kodak, args.out):
            print(path)
    elif args.cmd == "run":
        run_matrix(args.sources, args.tools, args.out, ffmpeg=args.ffmpeg,
                   resolutions=tuple(int(x) for x in args.resolutions.split(",")))
    else:
        print(analyze(args.results, args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
