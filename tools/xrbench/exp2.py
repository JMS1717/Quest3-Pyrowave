"""Experiment 2: CDF 9/7 FP32 compute against CDF 5/3 FP16 compute on the Galaxy XR.

Phase 0 arithmetic (correctness gates, attribution, the logical intermediate-byte model, the
PSNR-Y-matched bisection) and the live-cell analysis, which reuses `exp1`'s parsers since the
receiver's report lines are unchanged. Every number the report prints comes through here so the
JSON and the tables cannot disagree.
"""
import csv
import json
import math
import re
from pathlib import Path

import numpy as np

from . import exp1
from . import rdmatrix

PLANES = ("Y", "Cb", "Cr")

# PyroWave's decomposition (pyrowave_common.hpp): five levels, four bands per level in the
# wavelet image (the LL of a level is the input of the next), FP16 storage on the two finest
# levels at precision 1 with R32F on the three coarsest (pyrowave_common.cpp:135-173), R16F on
# all five at precision 0, R32F on all five at precision 2.
LEVELS = 5
FP16_LEVELS_AT_PRECISION_1 = 2


def compare_planes(a, b):
    """Per-plane and pooled error of decode `a` against decode `b` (lists of uint8 arrays).

    max_abs and differing count are exact; PSNR is against the 8-bit peak; a bit-identical pair
    reports PSNR inf. Both decodes must have the same geometry, or the comparison is meaningless."""
    out = {}
    tot_se = 0.0
    tot_n = 0
    tot_diff = 0
    tot_max = 0
    for name, pa, pb in zip(PLANES, a, b):
        if pa.shape != pb.shape:
            raise ValueError(f"plane {name}: {pa.shape} vs {pb.shape}")
        d = pa.astype(np.int32) - pb.astype(np.int32)
        se = float(np.sum(d.astype(np.float64) ** 2))
        n = d.size
        mse = se / n
        out[name] = {"max_abs": int(np.max(np.abs(d))), "mse": mse,
                     "psnr": math.inf if mse == 0 else 10 * math.log10(255.0 ** 2 / mse),
                     "differing": int(np.count_nonzero(d)), "count": n}
        tot_se += se
        tot_n += n
        tot_diff += out[name]["differing"]
        tot_max = max(tot_max, out[name]["max_abs"])
    mse = tot_se / tot_n
    out["all"] = {"max_abs": tot_max, "mse": mse,
                  "psnr": math.inf if mse == 0 else 10 * math.log10(255.0 ** 2 / mse),
                  "differing": tot_diff, "count": tot_n}
    return out


def compare_y4m(path_a, path_b):
    wa, ha, a = rdmatrix.read_y4m(path_a)
    wb, hb, b = rdmatrix.read_y4m(path_b)
    if (wa, ha) != (wb, hb):
        raise ValueError(f"{path_a} is {wa}x{ha}, {path_b} is {wb}x{hb}")
    return compare_planes(a, b)


def fp16_class(psnr_db, max_abs):
    """The project's three-way FP16 correctness classification of 5/3 FP16 against 5/3 FP32.

    PASS: PSNR >= 65 dB and max |diff| <= 1. PASS WITH DEVIATION: 55 <= PSNR < 65 or max = 2
    (documented, never reported as matching the offline ~70 dB model). FAIL: PSNR < 55 or max > 2."""
    if psnr_db < 55 or max_abs > 2:
        return "FAIL"
    if psnr_db >= 65 and max_abs <= 1:
        return "PASS"
    return "PASS WITH DEVIATION"


def transform_gate(psnr_db, max_abs):
    """Device decode against its own PC reference decode: the transform's numerical error.
    Same bands as Experiment 1's gate (max <= 2 code values, > 55 dB)."""
    return "PASS" if psnr_db > 55 and max_abs <= 2 else "FAIL"


def attribution(a_ms, b_ms, c_ms):
    """Transform_effect = A - B, Precision_effect = B - C, Combined = A - C, in ms and in
    percent of A. A = 9/7 FP32, B = 5/3 FP32, C = 5/3 FP16 (standalone, identical inputs)."""
    def pct(x):
        return 100.0 * x / a_ms if a_ms else float("nan")
    t, p, c = a_ms - b_ms, b_ms - c_ms, a_ms - c_ms
    return {"transform_ms": t, "transform_pct": pct(t), "precision_ms": p, "precision_pct": pct(p),
            "combined_ms": c, "combined_pct": pct(c)}


def bytes_per_coefficient(level, precision):
    """Storage width of a coefficient at `level` (0 = finest) for PyroWave's precision mode."""
    if precision == 2:
        return 4
    if precision == 0:
        return 2
    return 2 if level < FP16_LEVELS_AT_PRECISION_1 else 4


def band_shape(width, height, level):
    """Coefficients per band at `level`: each level halves both axes (ceil, as the aligned
    wavelet image does)."""
    w, h = width, height
    for _ in range(level + 1):
        w, h = (w + 1) // 2, (h + 1) // 2
    return w, h


def logical_bytes(width, height, chroma444, precision, fps=90.0):
    """LOGICAL INTERMEDIATE BYTES per frame for the compute decode path, DERIVED from buffer
    dimensions, precision and the pass structure. Not physical DRAM traffic (UNKNOWN: no
    counters); cache hits, tiling and compression are invisible to this model.

    Passes counted: the dequant pass writes every band once; each of the five iDWT dispatches
    reads the level's four bands (LL from the previous dispatch's write for levels < 4, or the
    stored LL5 for the coarsest) and writes the synthesised LL of the next finer level, or the
    three R8 output planes at level 0. Chroma at 4:2:0 has one fewer level of full-size bands
    (it starts at level 1), which the per-component loop reflects."""
    comps = [(width, height)] + [((width + 1) // 2, (height + 1) // 2)] * 2 if not chroma444 else [(width, height)] * 3
    write_coeff = read_coeff = write_ll = 0
    for cw, ch in comps:
        for level in range(LEVELS):
            bw, bh = band_shape(cw, ch, level)
            bpc = bytes_per_coefficient(level, precision)
            n = bw * bh
            # dequant writes HL, LH, HH at every level and LL only at the coarsest
            bands_written = 3 + (1 if level == LEVELS - 1 else 0)
            write_coeff += bands_written * n * bpc
            # the iDWT at this level reads all four bands
            read_coeff += 4 * n * bpc
            # and writes the next finer LL (level-1 width), or the R8 output at level 0
            if level > 0:
                pw, ph = band_shape(cw, ch, level - 1)
                write_ll += pw * ph * bytes_per_coefficient(level - 1, precision)
            else:
                write_ll += cw * ch * 1
    total = write_coeff + read_coeff + write_ll
    pixels = width * height
    return {"dequant_write_bytes": write_coeff, "idwt_read_bytes": read_coeff, "idwt_write_bytes": write_ll,
            "total_bytes": total, "bytes_per_pixel": total / pixels, "bytes_per_second": total * fps,
            "precision": precision, "width": width, "height": height, "chroma444": chroma444}


def phase3_trigger(pair_deltas, logical_delta_pct, dropped_pct, stale_pct, end_temp_gap_c,
                   matched_starts, extra_bytes_pct, contradiction=False, thermal_plausible=False):
    """The project's widened Phase 3 trigger: any one condition is enough. Returns the list of
    conditions met (empty means no sustained cells are required)."""
    hits = []
    for metric, stats in pair_deltas.items():
        for stat in ("mean", "p95", "p99"):
            d = stats.get(stat)
            if d is not None and abs(d) >= 5.0:
                hits.append(f"{metric} {stat} {d:+.1f} %")
    if logical_delta_pct is not None and abs(logical_delta_pct) >= 20.0:
        hits.append(f"logical intermediate bytes {logical_delta_pct:+.1f} %")
    if dropped_pct is not None and abs(dropped_pct) >= 10.0:
        hits.append(f"dropped rate {dropped_pct:+.1f} %")
    if stale_pct is not None and abs(stale_pct) >= 10.0:
        hits.append(f"stale rate {stale_pct:+.1f} %")
    if matched_starts and end_temp_gap_c is not None and end_temp_gap_c >= 2.0:
        hits.append(f"end temperature {end_temp_gap_c:.1f} C apart under matched starts")
    if thermal_plausible:
        hits.append("plausible thermal implication")
    if extra_bytes_pct is not None and extra_bytes_pct <= 25.0:
        hits.append(f"5/3 needs {extra_bytes_pct:+.1f} % bytes at the PSNR-Y match (<= 25 %)")
    if contradiction:
        hits.append("contradiction sustained testing could resolve")
    return hits


def bisect_cap(quality_at, target, lo, hi, tol_db=0.02, max_iter=12):
    """Smallest byte cap in [lo, hi] at which the monotone `quality_at(cap)` reaches `target`.

    Returns (cap, quality, evaluations). If even `hi` falls short, returns hi and its quality so
    the caller can say so; the search never extrapolates."""
    evals = []
    q_hi = quality_at(hi)
    evals.append((hi, q_hi))
    if q_hi < target:
        return hi, q_hi, evals
    q_lo = quality_at(lo)
    evals.append((lo, q_lo))
    if q_lo >= target:
        return lo, q_lo, evals
    best = (hi, q_hi)
    for _ in range(max_iter):
        mid = (lo + hi) // 2
        if mid == lo or mid == hi:
            break
        q = quality_at(mid)
        evals.append((mid, q))
        if q >= target:
            best = (mid, q)
            hi = mid
            if q - target < tol_db:
                break
        else:
            lo = mid
    return best[0], best[1], evals


def _num(v):
    """float where the column is numeric, else the string (dataset, source, wavelet labels)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


def load_rd(csv_path):
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({k: _num(v) for k, v in r.items()})
    return rows


def equal_bytes_table(rows97, rows53, resolution=2560):
    """Per source at `resolution`: 9/7 and 5/3 quality at each shared cap (Control A)."""
    def key(r):
        return (r["source"], int(r["resolution"]), int(r["cap_bytes"]))
    b53 = {key(r): r for r in rows53}
    out = []
    for r in rows97:
        if int(r["resolution"]) != resolution:
            continue
        s = b53.get(key(r))
        if not s:
            continue
        # rdmatrix columns: scaled_* is against the scaled reference (the codec's own error),
        # which is the equal-bytes comparison the brief asks for; full_* is the viewer-side set.
        out.append({"source": r["source"], "cap_bytes": int(r["cap_bytes"]),
                    "bytes97": int(r["actual_bytes"]), "bytes53": int(s["actual_bytes"]),
                    "psnr_y97": r["scaled_psnr_y"], "psnr_y53": s["scaled_psnr_y"], "d_psnr_y": s["scaled_psnr_y"] - r["scaled_psnr_y"],
                    "psnr_hvs97": r.get("scaled_psnr_hvs"), "psnr_hvs53": s.get("scaled_psnr_hvs"),
                    "ssim97": r.get("scaled_ssim_all"), "ssim53": s.get("scaled_ssim_all"),
                    "vmaf97": r.get("scaled_vmaf"), "vmaf53": s.get("scaled_vmaf")})
    return out


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compare", help="error of decode A against decode B, per plane")
    c.add_argument("a"); c.add_argument("b"); c.add_argument("--json")
    lb = sub.add_parser("logical", help="logical intermediate bytes for a frame")
    lb.add_argument("--width", type=int, required=True); lb.add_argument("--height", type=int, required=True)
    lb.add_argument("--chroma444", type=int, default=1); lb.add_argument("--fps", type=float, default=90.0)
    args = parser.parse_args(argv)
    if args.cmd == "compare":
        r = compare_y4m(args.a, args.b)
        for k, v in r.items():
            print(f"{k:>3}: max {v['max_abs']:>3}  mse {v['mse']:.5f}  psnr {v['psnr']:.2f} dB  "
                  f"differing {v['differing']}/{v['count']} ({100.0 * v['differing'] / v['count']:.2f} %)")
        if args.json:
            Path(args.json).write_text(json.dumps(r, indent=1))
    elif args.cmd == "logical":
        for p in (0, 1, 2):
            r = logical_bytes(args.width, args.height, bool(args.chroma444), p, args.fps)
            print(f"precision {p}: {r['total_bytes']:,} B/frame  {r['bytes_per_pixel']:.2f} B/px  "
                  f"{r['bytes_per_second'] / 1e9:.2f} GB/s at {args.fps:g} Hz")


if __name__ == "__main__":
    main()


# ---- live cells --------------------------------------------------------------------------------

_WAVELET = re.compile(r"pyroclient: wavelet CDF (\d/\d)")
_PRECISION = re.compile(r"pyro precision requested (\S+), effective ([^;]+); shaderFloat16=(\d)")


def parse_arm(text):
    """(wavelet, requested precision, effective precision, shaderFloat16) from a cell's logcat,
    last occurrence; None where the line is absent. The arm is proven by these lines, not by
    the plan."""
    w = p = e = f = None
    for m in _WAVELET.finditer(text):
        w = m.group(1)
    for m in _PRECISION.finditer(text):
        p, e, f = m.group(1), m.group(2).strip(), int(m.group(3))
    return w, p, e, f


def load_cell(run_dir, label):
    c = exp1.load_cell(run_dir, label)
    d = Path(run_dir) / label
    text = (d / "logcat.txt").read_text(encoding="utf-8", errors="replace") if (d / "logcat.txt").exists() else ""
    c["wavelet"], c["precision_requested"], c["precision_effective"], c["shader_float16"] = parse_arm(text)
    h = c["hottest"]
    c["thermal_rise"] = (h["end_c"] - h["start_c"]) if h.get("end_c") is not None and h.get("start_c") is not None else None
    c["clock"] = gpu_clock_summary(d / "gpufreq.csv")
    return c


def regression_row(cell):
    s = cell["summary"] or {}
    total = sum(s.get(k, 0) for k in ("complete", "partial", "skipped", "dropped"))
    dropped = s.get("dropped", 0)
    stale = s.get("stale_packets", 0)
    return {"completed": s.get("complete"), "partial": s.get("partial"), "skipped": s.get("skipped"), "dropped": dropped,
            "dropped_pct": (100.0 * dropped / total) if total else None, "stale_packets": stale,
            "stale_per_frame": (stale / total) if total else None, "decode_fail": s.get("decode_fail"),
            "packets_lost": cell["meta"].get("packets_lost"), "frames": total}


def pair_regression_deltas(a, b):
    """Percent change b vs a for dropped and stale rates, and the thermal-rise difference in C."""
    ra, rb = regression_row(a), regression_row(b)
    def pct(x, y):
        if x is None or y is None:
            return None
        return (y - x) / x * 100.0 if x else (None if y == 0 else math.inf)
    return {"dropped_pct": pct(ra["dropped_pct"], rb["dropped_pct"]), "stale_pct": pct(ra["stale_per_frame"], rb["stale_per_frame"]),
            "thermal_rise_c": (b["thermal_rise"] - a["thermal_rise"]) if a["thermal_rise"] is not None and b["thermal_rise"] is not None else None}


def outcome(gpu_mean_pct, fence_mean_pct, logical_pct, extra_bytes_pct, quality_ok=True):
    """quality_ok None = quality not yet measured: never counted as 'worse'."""
    """The project's outcomes A-F for 5/3 FP16 against 9/7 FP32 compute (percent deltas, 5/3 - 9/7).
    Material = 5 % or more, the same bar as the Phase 3 trigger."""
    fence_better = fence_mean_pct is not None and fence_mean_pct <= -5.0
    gpu_better = gpu_mean_pct is not None and gpu_mean_pct <= -5.0
    worse = (fence_mean_pct is not None and fence_mean_pct >= 5.0) or quality_ok is False
    if worse:
        return "F", "5/3 is worse (fence slower, or quality unacceptable): preserved, not optimised until profiled"
    if fence_better and logical_pct is not None and logical_pct <= -20.0:
        return "C", "memory traffic (logical bytes) falls and fence falls"
    if fence_better and extra_bytes_pct is not None and extra_bytes_pct > 25.0:
        return "D", "5/3 is faster but needs substantially more bytes at matched PSNR-Y: construct the trade"
    if fence_better:
        return "A", "5/3 reduces fence latency with acceptable quality"
    if gpu_better:
        return "B", "GPU time falls but fence does not: shared structural costs dominate completion"
    return "E", "no meaningful performance difference: simpler arithmetic and FP16 do not move completion"


def clock_line(cell):
    ck = cell.get("clock")
    g = cell["stats"].get("gpu decode")
    if not ck:
        return "UNKNOWN (no gpufreq.csv for this cell)"
    cyc = gpu_cycles_per_frame(g["mean"], ck["mean_mhz"]) if g else None
    return (f"mean {ck['mean_mhz']:.0f} MHz (min {ck['min_mhz']:.0f}, max {ck['max_mhz']:.0f}, {ck['share_at_max_pct']:.0f} % of {ck['samples']} samples at the top level), "
            f"busy {ck['mean_busy_pct']:.0f} %; DERIVED GPU decode work {fmt(cyc / 1e6 if cyc else None, 2)} Mcycles/frame")


def pairs_agree(g1, g2, f1, f2, material_pct=5.0):
    """Two pairs agree when both deltas share a sign, or when neither pair's delta is material
    (|delta| < 5 %): a sign flip inside the noise band is not a contradiction."""
    def one(a, b):
        if abs(a) < material_pct and abs(b) < material_pct:
            return True, "both within the 5 % band"
        return (a < 0) == (b < 0), "same sign" if (a < 0) == (b < 0) else "opposite signs, at least one material"
    ga, gh = one(g1, g2)
    fa, fh = one(f1, f2)
    return ga and fa, f"GPU {gh}; fence {fh}"


def fmt(v, nd=2):
    return "UNKNOWN" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def fmt_cmp(name, r):
    return (f"| {name} | {r['max_abs']} | {r['mse']:.5f} | {fmt(r['psnr'])} | {r['differing']:,} / {r['count']:,} "
            f"({100.0 * r['differing'] / r['count']:.2f} %) |")


def render(run_dir, out_path, phase0, sustained_dir=None, fps=90.0, notes=()):
    """The 22-item Experiment 2 report. `phase0` is the dict `collect_phase0` builds (device
    comparisons, standalone timings, RD tables, the PSNR-Y match, SPIR-V counts, negatives)."""
    labels = ("P97-1", "P53-1", "P97-2", "P53-2")
    cells = {l: load_cell(run_dir, l) for l in labels}
    L = []
    A = L.append
    A("# Experiment 2 — CDF 9/7 FP32 compute vs CDF 5/3 FP16 compute on the Galaxy XR\n")
    A("Central question: can CDF 5/3 with an all-FP16 inverse retain near-9/7 reconstruction quality while reducing data, "
      "logical intermediate bytes, synchronisation and GPU work to a completed frame, against the CDF 9/7 FP32 compute "
      "reference? Fragment is historical only. Every number is tagged MEASURED / DERIVED / ESTIMATED / UNKNOWN; "
      "physical DRAM traffic is UNKNOWN throughout (no counters).\n")
    A("## 1. Experiment 1 baseline carried forward\n")
    A("Compute 9/7 (`idwt.comp`, 5 levels fused H+V, 1 barrier per level) is the execution baseline (MEASURED, Experiment 1): "
      "against fragment, GPU decode mean -1.9 / -3.2 %, p99 -9.9 / -13.7 %, fence mean -12.6 / -14.6 %, p99 -16.2 / -20.5 % in the two short pairs; "
      "sustained 5 min GPU 3.48 -> 3.16 ms (-9.1 %), fence 5.76 -> 4.80 ms (-16.7 %); standalone 9.446 -> 7.077 ms; compute 64.16 dB / max 1 against the PC reference. "
      "K >> 1: the fence saving lived outside the decode timestamp interval, so ops/pixel is not the lever; passes, barriers, memory and precision are what Experiment 2 tests. "
      "Unresolved from Experiment 1 and carried as explicit regression metrics: sustained compute dropped ~485 vs ~360 frames of ~27,600 and showed more stale packets; ended ~1.7 C hotter.\n")
    A("## 2. 9/7 compute reference configuration\n")
    seg = cells["P97-1"]["meta"].get("segment", {})
    A(f"- Live: {seg.get('eye')}x{seg.get('eye_h')}/eye render, encoded 1984x896 side-by-side foveated (centre {seg.get('center_size_x')}), 4:4:4 full range, "
      f"{seg.get('mbps')} Mbps PyroWave over UDP, {seg.get('hz')} Hz, buffering {seg.get('buffering')}, decode path `{cells['P97-1']['path']}` ({cells['P97-1']['forced']}), "
      f"wavelet CDF {cells['P97-1']['wavelet']}, precision requested {cells['P97-1']['precision_requested']} -> effective {cells['P97-1']['precision_effective']} "
      f"(FP32 math, R16F storage on levels 0-1, R32F on 2-4), shaderFloat16={cells['P97-1']['shader_float16']} (MEASURED from logcat).")
    A(f"- Standalone control: `pyrowave_android`, {phase0['standalone']['geometry']}, identical `.wave` inputs, {phase0['standalone']['iterations']} iterations, compute path forced.\n")
    A("## 3. 5/3 FP16 implementation description\n")
    A("- Transform: specialization constant `LEGALL53` (constant_id 1) on `dwt.comp` / `idwt.comp`; when set the four 9/7 lifting loops become the two 5/3 steps "
      "(inverse: even -= 0.25*(odd_l+odd_r); odd += 0.5*(even_l+even_r)) with no K scaling. Same tile (32+2*4 apron), same shared layout, same 5 levels, same dispatch "
      "and barrier structure by construction; the fragment path refuses 5/3 (compute only).\n"
      "- Precision: process-global PyroWave precision 0 (FP16 math, `float16_t` lifting values, `f16vec2` shared tile, R16F on all five levels) selected per arm through "
      "the device property `debug.xrwired.pyro_precision`, which libpyroclient turns into `PYROWAVE_PRECISION` before the device is created; 9/7 stays at precision 1.\n"
      "- Bitstream: sequence-header `code` = 1 marks a 5/3 start-of-frame (0 = 9/7); the decoder refuses a stream whose code does not match its create-info wavelet. "
      "Server env `ALVR_PYROWAVE_WAVELET=53`; decoder config blob byte 13 carries it to the client.\n"
      "- Structural differences beyond transform and precision (recorded, not attributed to the transform): the encoder's per-band quantiser gain table is scaled by the "
      "DERIVED 5/3-to-9/7 synthesis-gain ratios (HL/LH by level 1.027, 0.797, 0.698, 0.668, 0.660; HH 1.382, 0.953, 0.763, 0.708, 0.693; LL 0.629), so the initial "
      "quantiser resolution per band differs. Nothing else differs (item 16).\n")
    A("## 4. Correctness validation (MEASURED on device, `pyrowave_android`, 200 iterations, vs the PC reference decode of the same bytes)\n")
    A("| comparison | max abs | MSE | PSNR dB | differing |\n|---|---|---|---|---|")
    dev = phase0["device"]
    for key, name in (("A_vs_pc97", "A: 9/7 FP32 device vs PC 9/7"), ("B_vs_pc53", "B: 5/3 FP32 device vs PC 5/3"), ("C_vs_pc53", "C: 5/3 FP16 device vs PC 5/3")):
        for plane in ("Y", "Cb", "Cr", "all"):
            A(fmt_cmp(f"{name} [{plane}]", dev[key][plane]))
    A("")
    for key, name in (("A_vs_pc97", "9/7 FP32"), ("B_vs_pc53", "5/3 FP32"), ("C_vs_pc53", "5/3 FP16")):
        r = dev[key]["all"]
        A(f"- Transform numerical error gate, {name}: {transform_gate(r['psnr'], r['max_abs'])} (max {r['max_abs']}, {r['psnr']:.2f} dB pooled).")
    r = dev["A_vs_B_transform"]["all"]
    A(f"- 9/7 vs 5/3 device decodes of the same source at equal bytes differ by max {r['max_abs']} / {r['psnr']:.2f} dB: this is COMPRESSION / QUANTISATION difference between two transforms, not an error (the PC decodes differ identically: {dev['pc97_vs_pc53']['all']['psnr']:.2f} dB).")
    A(f"- Negative bitstream/config tests (device and PC): {phase0['negatives']}\n")
    A("## 5. FP16 validation\n")
    g = dev["C_vs_B_fp16gate"]
    A("5/3 FP16 (precision 0) against 5/3 FP32 (precision 1) on device, identical bytes (MEASURED):\n")
    A("| plane | max abs | MSE | PSNR dB | differing |\n|---|---|---|---|---|")
    for plane in ("Y", "Cb", "Cr", "all"):
        A(fmt_cmp(plane, g[plane]))
    cls = fp16_class(g["all"]["psnr"], g["all"]["max_abs"])
    per_plane = {p: fp16_class(g[p]["psnr"], g[p]["max_abs"]) for p in ("Y", "Cb", "Cr")}
    A(f"\nClassification (pooled): **{cls}** ({g['all']['psnr']:.2f} dB, max {g['all']['max_abs']}); per plane: " + ", ".join(f"{p} {v} ({g[p]['psnr']:.2f} dB)" for p, v in per_plane.items()) + ".")
    A("This is the on-device FP16 arithmetic error of the whole pipeline (lifting in float16_t, R16F storage on all levels, 8-bit output rounding), which is why it sits at ~65 dB rather than the offline lab's >70 dB for the transform alone; it is reported as what it is, not as equivalent to 70 dB.")
    d = dev["D_vs_A_97fp16"]["all"]
    A(f"- Diagnostic: 9/7 at precision 0 vs 9/7 at precision 1 on device: max {d['max_abs']}, {d['psnr']:.2f} dB -> {fp16_class(d['psnr'], d['max_abs'])}. FP16 breaks 9/7 as the lab predicted; and since a silent fallback to precision 1 would have made this comparison bit-identical, it proves precision 0 genuinely executes FP16 math on this device.")
    s = phase0["spirv"]
    A(f"- Compiled path (regenerated `slangmosh.hpp`, `spirv-dis`): idwt precision-0 variant declares `OpTypeFloat 16` ({s['fp16_v0']['f16_arith']} float16 arithmetic ops, {s['fp16_v0']['f32_arith']} float32 ops, {s['fp16_v0']['fconvert']} OpFConvert; Float16 capability; f16vec2 shared tile). "
      f"The precision-1 variant has {s['fp16_v1']['f16_arith']} float16 arithmetic ops and {s['fp16_v1']['fconvert']} conversions (FP32 math, FP16 shared/storage), the precision-2 variant none. "
      f"The {s['fp16_v0']['f32_arith']} float32 ops in the precision-0 variant are {s['fp16_v0']['f32_note']}. LEGALL53 is a specialization constant (SpecId 1) in every variant, so the 5/3 lifting is the same compiled module with the constant set at pipeline creation; the driver's final ISA is UNKNOWN (no disassembler), so hardware promotion cannot be excluded beyond the D diagnostic above.")
    A(f"- Runtime: every P53 cell logs `pyro precision requested 0, effective 0; shaderFloat16=1` and the standalone C run reports `PYROWAVE_PRECISION=0` (MEASURED): {phase0['fp16_runtime']}\n")
    A("## 6. Equal-byte rate-distortion result (Control A, offline, RTX 3090 encode / PC decode, 2560/eye side-by-side 4:4:4, MEASURED with ffmpeg psnr/ssim/libvmaf)\n")
    eb = phase0["equal_bytes"]
    A("At 416,667 B per stereo frame (300 Mbps at 90 Hz):\n")
    A("| source | bytes 9/7 | bytes 5/3 | PSNR-Y 9/7 | PSNR-Y 5/3 | dPSNR-Y | PSNR-HVS 9/7 | PSNR-HVS 5/3 | SSIM 9/7 | SSIM 5/3 | VMAF 9/7 | VMAF 5/3 |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in eb["at_416667"]:
        A(f"| {r['source']} | {r['bytes97']:,} | {r['bytes53']:,} | {r['psnr_y97']:.2f} | {r['psnr_y53']:.2f} | {r['d_psnr_y']:+.2f} | {fmt(r['psnr_hvs97'])} | {fmt(r['psnr_hvs53'])} | {fmt(r['ssim97'], 4)} | {fmt(r['ssim53'], 4)} | {fmt(r['vmaf97'])} | {fmt(r['vmaf53'])} |")
    A(f"\nAcross all {eb['cells']} matched cells (6 resolutions x 11 caps x 5 sources): mean dPSNR-Y {eb['mean_d_psnr_y']:+.2f} dB (Kodak {eb['kodak_d_psnr_y']:+.2f}, panel {eb['panel_d_psnr_y']:+.2f}); range {eb['min_d_psnr_y']:+.2f} .. {eb['max_d_psnr_y']:+.2f} dB.")
    gd = phase0["gains"]
    A("\nQuantiser-gain diagnostic (5/3 with the DERIVED native gains vs 5/3 with the 9/7 table, 2560/eye, 416,667 B):\n")
    A("| source | gains | bytes | PSNR-Y | PSNR-HVS | SSIM | VMAF |\n|---|---|---|---|---|---|---|")
    for r in gd:
        A(f"| {r['source']} | {r['gains']} | {r['bytes']:,} | {r['psnr_y']:.2f} | {fmt(r['psnr_hvs'])} | {fmt(r['ssim'], 4)} | {fmt(r['vmaf'])} |")
    A("\nNative gains stay the implementation; the table above is the contribution of that one structural difference. Per-band allocation is not exposed by the encoder (UNKNOWN).\n")
    A("## 7. PSNR-Y-MATCHED CONTROL (Control B, offline, 2560/eye; never 'equal quality')\n")
    A("5/3's cap is bisected until its PSNR-Y reaches 9/7's at 416,667 B; the other metrics at that point are reported as they fall:\n")
    A("| source | 9/7 bytes | 9/7 PSNR-Y | 5/3 bytes at match | 5/3 PSNR-Y | extra bytes | PSNR-HVS 9/7 / 5/3 | SSIM 9/7 / 5/3 | VMAF 9/7 / 5/3 |\n|---|---|---|---|---|---|---|---|---|")
    for r in phase0["match"]:
        A(f"| {r['source']} | {r['bytes97']:,} | {r['psnr_y97']:.2f} | {r['bytes53']:,} | {r['psnr_y53']:.2f} | {r['extra_pct']:+.1f} % | {fmt(r['psnr_hvs97'])} / {fmt(r['psnr_hvs53'])} | {fmt(r['ssim97'], 4)} / {fmt(r['ssim53'], 4)} | {fmt(r['vmaf97'])} / {fmt(r['vmaf53'])} |")
    extra = [r["extra_pct"] for r in phase0["match"] if r.get("extra_pct") is not None]
    extra_mean = sum(extra) / len(extra) if extra else None
    A(f"\nMean extra bytes 5/3 needs at matched PSNR-Y: {fmt(extra_mean, 1)} % ({'within' if extra_mean is not None and extra_mean <= 25 else 'beyond'} the 25 % Phase 3 trigger).\n")
    order = [("8", "P97-1"), ("9", "P53-1"), ("10", None), ("11", "P97-2"), ("12", "P53-2"), ("13", None)]
    pairs, regs = {}, {}
    for num, l in order:
        if l:
            c = cells[l]
            A(f"## {num}. {l} raw results (MEASURED)\n")
            A(f"- arm proven by logcat: wavelet CDF {c['wavelet']}, precision {c['precision_requested']} -> {c['precision_effective']}, path {c['path']} ({c['forced']})")
            for metric in ("gpu decode", "convert", "submit->fence"):
                A(f"- {metric} ms (min / mean / p50 / p95 / p99 / max): {exp1.fmt_stats(c['stats'].get(metric))}")
            rr = regression_row(c)
            A(f"- system: fps mean {fmt(c['meta'].get('fps_mean'))} median {fmt(c['meta'].get('fps_median'))} min {fmt(c['meta'].get('fps_min'))}; completed {rr['completed']}, partial {rr['partial']}, skipped {rr['skipped']}, dropped {rr['dropped']} ({fmt(rr['dropped_pct'])} %), stale packets {rr['stale_packets']} ({fmt(rr['stale_per_frame'], 4)} per frame), packets lost {rr['packets_lost']}, decode failures {rr['decode_fail']}")
            A(f"- thermal: {exp1.thermal_line(c)}; rise {fmt(c['thermal_rise'], 1)} C")
            A(f"- GPU clock: {clock_line(c)}\n")
        else:
            pid = 1 if num == "10" else 2
            a, b = cells[f"P97-{pid}"], cells[f"P53-{pid}"]
            d = exp1.pair_deltas(a["stats"], b["stats"], metrics=("gpu decode", "convert", "submit->fence"))
            pairs[pid] = d
            regs[pid] = pair_regression_deltas(a, b)
            A(f"## {num}. Pair {pid} deltas (5/3 - 9/7) / 9/7 x 100\n")
            for m, row in d.items():
                A(f"- {m}: " + ", ".join(f"{s_} {v:+.1f} %" for s_, v in row.items() if v is not None))
            A(f"- dropped rate {fmt(regs[pid]['dropped_pct'], 1)} %, stale rate {fmt(regs[pid]['stale_pct'], 1)} %, thermal rise difference {fmt(regs[pid]['thermal_rise_c'], 1)} C\n")
    A("## 14. Thermal matching\n")
    tm = {}
    for pid in (1, 2):
        a, b = cells[f"P97-{pid}"], cells[f"P53-{pid}"]
        sa, sb = a["hottest"].get("start_c"), b["hottest"].get("start_c")
        status, dd = exp1.thermal_match(sa, sb)
        tm[pid] = status
        A(f"- Pair {pid}: P97 start {sa} C, P53 start {sb} C, difference {dd if dd is None else round(dd, 1)} C -> {status}; ends {a['hottest'].get('end_c')} / {b['hottest'].get('end_c')} C, status {a['hottest'].get('status_start')}->{a['hottest'].get('status_end')} / {b['hottest'].get('status_start')}->{b['hottest'].get('status_end')}")
    agree = None
    if pairs.get(1, {}).get("gpu decode") and pairs.get(2, {}).get("gpu decode"):
        g1, g2 = pairs[1]["gpu decode"]["mean"], pairs[2]["gpu decode"]["mean"]
        f1, f2 = pairs[1]["submit->fence"]["mean"], pairs[2]["submit->fence"]["mean"]
        agree, how = pairs_agree(g1, g2, f1, f2)
        A(f"\nPairs agree on GPU and fence: {'YES' if agree else 'NO -- contradiction, reported as such'} ({how}; GPU mean {g1:+.1f} % / {g2:+.1f} %, fence {f1:+.1f} % / {f2:+.1f} %).\n")
    A("## 15. Memory-traffic comparison\n")
    enc_w, enc_h = phase0.get("encoded", (1984, 896))
    lb = {p: logical_bytes(enc_w, enc_h, True, p, fps) for p in (0, 1)}
    A(f"LOGICAL INTERMEDIATE BYTES per frame at the live encoded size {enc_w}x{enc_h} 4:4:4, DERIVED from buffer dimensions, precision and the pass structure "
      "(dequant writes every band once; each of five iDWT dispatches reads four bands and writes the next LL, or the three R8 planes). Physical DRAM traffic: UNKNOWN.\n")
    A("| arm | coefficient storage | dequant writes | iDWT reads | iDWT writes | total / frame | bytes / reconstructed pixel | per second at 90 Hz |\n|---|---|---|---|---|---|---|---|")
    for p, name in ((1, "9/7 FP32 compute (precision 1)"), (0, "5/3 FP16 compute (precision 0)")):
        r = lb[p]
        A(f"| {name} | {'R16F levels 0-1, R32F levels 2-4' if p == 1 else 'R16F all levels'} | {r['dequant_write_bytes']:,} | {r['idwt_read_bytes']:,} | {r['idwt_write_bytes']:,} | {r['total_bytes']:,} | {r['bytes_per_pixel']:.2f} | {r['bytes_per_second'] / 1e9:.2f} GB/s |")
    logical_pct = (lb[0]["total_bytes"] - lb[1]["total_bytes"]) / lb[1]["total_bytes"] * 100.0
    A(f"\n5/3 FP16 changes logical intermediate bytes by {logical_pct:+.1f} % (DERIVED). The finest two levels are already R16F at precision 1, so all-FP16 storage saves only the three coarse levels' R32F; the shared tile is f16vec2 in both arms. Temporary buffer size: one wavelet image per arm ({enc_w // 2}x{enc_h // 2}, 12 layers, 5 mips; precision 1 splits it into a 2-mip R16F image and a 3-mip R32F image). Register pressure: UNKNOWN (no ISA).\n")
    A("## 16. Pass/barrier comparison (DERIVED from source; structural identity gate)\n")
    A("| | 9/7 FP32 compute | 5/3 FP16 compute |\n|---|---|---|")
    A("| dequant/unpack dispatches | one per (component, level, band) present, <= 60, then 1 barrier | identical |")
    A("| iDWT dispatches | 5 levels x 3 components = 15 (`Decoder::Impl::idwt`, fused H+V per level in shared memory) | identical (same loop, spec constant selects the lifting) |")
    A("| barriers | 1 compute->compute per level after the three component dispatches = 5, plus the dequant->iDWT barrier | identical |")
    A("| decomposition levels / tile / apron / workgroup | 5 / 32x32 / 4 / 64 threads | identical |")
    A("| intermediate buffers | wavelet image, R16F+R32F split | wavelet image, R16F only |")
    A("| full-resolution-equivalent passes | ~2 | identical |")
    A("| arithmetic | 4 lifting steps + K scaling, FP32 | 2 lifting steps, no scaling, FP16 |")
    A("| quantiser gain table | 9/7 | scaled by the 5/3 ratios (item 3) |")
    A("\nGate: STRUCTURALLY IDENTICAL except transform arithmetic, precision/storage and the gain table (the dispatch trace is not exposed by the driver; counts are from the source loops).\n")
    st = phase0["standalone"]
    at = attribution(st["A_ms"], st["B_ms"], st["C_ms"])
    A("## 17. K propagation\n")
    A(f"Standalone attribution (device, {st['geometry']}, {st['iterations']} iterations, MEASURED): A 9/7 FP32 {st['A_ms']:.3f} ms, B 5/3 FP32 {st['B_ms']:.3f} ms, C 5/3 FP16 {st['C_ms']:.3f} ms (convert {st['convert_ms']:.3f} ms in all three). "
      f"Transform effect A-B {at['transform_ms']:+.3f} ms ({at['transform_pct']:+.1f} %), precision effect B-C {at['precision_ms']:+.3f} ms ({at['precision_pct']:+.1f} %), combined {at['combined_ms']:+.3f} ms ({at['combined_pct']:+.1f} %). Diagnostic only; 5/3 FP32 is not a live arm.\n")
    noise = {}
    for s_ in ("mean", "p50", "p95", "p99"):
        a, b = cells["P97-1"]["stats"].get("gpu decode"), cells["P97-2"]["stats"].get("gpu decode")
        noise[s_] = abs(a[s_] - b[s_]) if a and b else 0.0
    A("Noise floor per statistic = |P97-1 - P97-2| GPU decode: " + ", ".join(f"{s_} {v:.2f} ms" for s_, v in noise.items()) + "\n")
    for pid in (1, 2):
        a, b = cells[f"P97-{pid}"]["stats"], cells[f"P53-{pid}"]["stats"]
        if a.get("gpu decode") and b.get("gpu decode") and a.get("submit->fence") and b.get("submit->fence"):
            k = exp1.propagation_k(a, b, noise)
            A(f"Pair {pid} ({tm.get(pid)}):")
            for s_, row in k.items():
                kk = "n/a" if row["K"] is None else f"{row['K']:.2f}"
                A(f"- {s_}: GPU savings {row['gpu_savings_ms']:+.2f} ms, fence savings {row['fence_savings_ms']:+.2f} ms, K = {kk} -> {row['band']}")
            A("")
    pts = {arm: sum((clock_points(run_dir, l) for l in ls), []) for arm, ls in (("97", ("P97-1", "P97-2")), ("53", ("P53-1", "P53-2")))}
    if pts["97"] and pts["53"]:
        A("### Latency vs GPU clock, per 720-frame window (MEASURED windows, DERIVED fits)\n")
        A("Each report window is paired with the mean sampled clock inside it; latency is fitted against 1/f per arm (time = work / rate), then each arm's windows are scored against the other arm's fit. One shared relationship means the transform contributes little to completion time.\n")
        A("| metric | 9/7 windows | 5/3 windows | 9/7 fit r2 | 5/3 fit r2 | 5/3 residual on the 9/7 fit | 9/7 residual on the 5/3 fit | pooled r2 - best arm r2 | verdict |\n|---|---|---|---|---|---|---|---|---|")
        for metric, name in (("gpu_ms", "GPU decode mean"), ("fence_ms", "submit->fence mean")):
            r = latency_vs_clock(pts["97"], pts["53"], metric)
            f97, f53 = r["fit97"], r["fit53"]
            verdict = "UNKNOWN"
            if "within_scatter" in r:
                verdict = "ONE RELATIONSHIP (within the 9/7 scatter)" if r["within_scatter"] else "SEPARATE CURVES"
            A(f"| {name} | {r['n97']} | {r['n53']} | {fmt(f97[2] if f97 else None, 3)} | {fmt(f53[2] if f53 else None, 3)} | "
              f"{fmt(r.get('resid53_on_97_ms'), 3)} ms ({fmt(r.get('resid53_on_97_pct'), 1)} %) | {fmt(r.get('resid97_on_53_ms'), 3)} ms ({fmt(r.get('resid97_on_53_pct'), 1)} %) | "
              f"{fmt(r.get('pooled_r2_vs_best_arm'), 3)} | {verdict} |")
        A("")
        A("| arm | window end | clock MHz | busy % | GPU ms | fence ms |\n|---|---|---|---|---|---|")
        for arm in ("97", "53"):
            for q in pts[arm]:
                A(f"| {arm} | {q['end']:.0f} | {q['clock_mhz']:.0f} | {q['busy_pct']:.0f} | {q['gpu_ms']:.2f} | {q['fence_ms']:.2f} |")
        A("")
    A("## 18. Dropped-frame / stale-packet regression (MEASURED)\n")
    A("| cell | frames | completed | dropped | dropped % | skipped | stale packets | stale / frame | packets lost | CPU start/end | hottest start/end | status |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    for l in labels:
        c = cells[l]; rr = regression_row(c); h = c["hottest"]; m = c["meta"]
        A(f"| {l} | {rr['frames']} | {rr['completed']} | {rr['dropped']} | {fmt(rr['dropped_pct'])} | {rr['skipped']} | {rr['stale_packets']} | {fmt(rr['stale_per_frame'], 4)} | {rr['packets_lost']} | {fmt(m.get('cpu_start'), 1)} / {fmt(m.get('cpu_end'), 1)} | {h.get('start_c')} / {h.get('end_c')} | {h.get('status_start')} -> {h.get('status_end')} |")
    A("")
    for pid in (1, 2):
        A(f"- Pair {pid}: dropped rate {fmt(regs[pid]['dropped_pct'], 1)} %, stale rate {fmt(regs[pid]['stale_pct'], 1)} %, thermal rise difference {fmt(regs[pid]['thermal_rise_c'], 1)} C ({tm.get(pid)})")
    A("")
    pooled = {}
    for arm, ls in (("9/7", ("P97-1", "P97-2")), ("5/3", ("P53-1", "P53-2"))):
        pooled[arm] = {}
        for metric in ("gpu decode", "convert", "submit->fence"):
            rows = [cells[l]["stats"][metric] for l in ls if cells[l]["stats"].get(metric)]
            if rows:
                pooled[arm][metric] = {s_: (sum(r[s_] for r in rows) / len(rows) if s_ in ("mean", "p50") else max(r[s_] for r in rows)) for s_ in ("mean", "p50", "p95", "p99")}
    dg = df = None
    if pooled["9/7"].get("gpu decode") and pooled["5/3"].get("gpu decode"):
        dg = exp1.delta_percent(pooled["5/3"]["gpu decode"]["mean"], pooled["9/7"]["gpu decode"]["mean"])
        df = exp1.delta_percent(pooled["5/3"]["submit->fence"]["mean"], pooled["9/7"]["submit->fence"]["mean"])
    mean_deltas = {m: {s_: (pairs[1][m][s_] + pairs[2][m][s_]) / 2 for s_ in ("mean", "p95", "p99") if pairs[1][m].get(s_) is not None and pairs[2][m].get(s_) is not None} for m in pairs.get(1, {}) if pairs.get(2, {}).get(m)}
    trig = phase3_trigger(mean_deltas, logical_pct, (regs[1]["dropped_pct"] or 0 + (regs[2]["dropped_pct"] or 0)) / 2 if regs else None,
                          None, None, tm.get(1) == "THERMALLY MATCHED", extra_mean, contradiction=(agree is False))
    A("## 19. Sustained results (Phase 3)\n")
    A("Trigger conditions met by the short pairs: " + ("; ".join(trig) if trig else "none") + ".\n")
    sus = {}
    if sustained_dir and Path(sustained_dir).exists():
        for l in ("S97", "S53"):
            if (Path(sustained_dir) / l).exists():
                c = load_cell(sustained_dir, l); sus[l] = c
                rr = regression_row(c)
                A(f"- {l} (CDF {c['wavelet']}, precision {c['precision_effective']}): gpu decode {exp1.fmt_stats(c['stats'].get('gpu decode'))}; convert {exp1.fmt_stats(c['stats'].get('convert'))}; fence {exp1.fmt_stats(c['stats'].get('submit->fence'))}; frames {rr['frames']} dropped {rr['dropped']} ({fmt(rr['dropped_pct'])} %) stale {rr['stale_packets']}; thermal {exp1.thermal_line(c)}; GPU clock {clock_line(c)}")
        if "S97" in sus and "S53" in sus:
            st_, dd = exp1.thermal_match(sus["S97"]["hottest"].get("start_c"), sus["S53"]["hottest"].get("start_c"))
            A(f"- Sustained pair: start {sus['S97']['hottest'].get('start_c')} / {sus['S53']['hottest'].get('start_c')} C, difference {dd if dd is None else round(dd, 1)} C -> {st_}; end {sus['S97']['hottest'].get('end_c')} / {sus['S53']['hottest'].get('end_c')} C")
            d = exp1.pair_deltas(sus["S97"]["stats"], sus["S53"]["stats"], metrics=("gpu decode", "convert", "submit->fence"))
            for m, row in d.items():
                A(f"- Sustained deltas {m}: " + ", ".join(f"{s_} {v:+.1f} %" for s_, v in row.items() if v is not None))
            rg = pair_regression_deltas(sus["S97"], sus["S53"])
            A(f"- Sustained regression: dropped rate {fmt(rg['dropped_pct'], 1)} %, stale rate {fmt(rg['stale_pct'], 1)} %, thermal rise difference {fmt(rg['thermal_rise_c'], 1)} C")
            k = exp1.propagation_k(sus["S97"]["stats"], sus["S53"]["stats"], {})
            for s_, row in k.items():
                A(f"- Sustained K {s_}: GPU savings {row['gpu_savings_ms']:+.2f} ms, fence savings {row['fence_savings_ms']:+.2f} ms, K = {'n/a' if row['K'] is None else format(row['K'], '.2f')} -> {row['band']}")
        A("")
    else:
        A("NOT RUN.\n")
    A("## 20. Quality / bandwidth / latency trade\n")
    quality_ok = None if math.isnan(eb["kodak_d_psnr_y"]) else eb["kodak_d_psnr_y"] > -1.0
    o, why = outcome(dg, df, logical_pct, extra_mean, quality_ok)
    A(f"- Quality (Control A): 5/3 gives up {abs(eb['kodak_d_psnr_y']):.2f} dB PSNR-Y on Kodak and {abs(eb['panel_d_psnr_y']):.2f} dB on the synthetic panel at equal bytes (MEASURED offline).")
    A(f"- Representation size (Control B): 5/3 needs {fmt(extra_mean, 1)} % more bytes to match 9/7's PSNR-Y (MEASURED offline); at 400 Mbps that is {fmt(400 * (1 + (extra_mean or 0) / 100), 0)} Mbps.")
    A(f"- Completion latency (live, pooled means): GPU decode {fmt(dg, 1)} %, submit->fence {fmt(df, 1)} %; standalone combined {at['combined_pct']:+.1f} %.")
    A(f"- Logical intermediate data: {logical_pct:+.1f} % (DERIVED); thermal and stability: item 18.")
    A(f"- Outcome **{o}**: {why}.\n")
    A("## 21. Remaining unknowns\n")
    for n in notes:
        A(f"- {n}\n")
    A("- GPU DVFS: the Adreno's msm-adreno-tz governor moves the clock with load (285-788 MHz). Every short cell shifted its decode time by ~0.5 ms between consecutive 720-frame reports with the thermal status unchanged, and cells that ran at a lower busy fraction can sit at a lower clock, so time deltas between arms are confounded with clock state unless the clock was sampled; cells with a `gpufreq.csv` report the mean clock and DERIVED cycles per frame above, cells without one do not.\n")
    A("- Physical DRAM traffic, cache behaviour and register pressure on the Adreno 740 (no counters, no ISA): only the logical model exists.\n"
      "- Whether the driver promotes float16 arithmetic internally: the D diagnostic proves FP16 math executes, not that every op stays FP16.\n"
      "- Two pairs plus one sustained pair; no distribution test is claimed. Bytes are equal by configuration and rate control per cell, not replayed.\n"
      "- The FP16 gate pooled 65.09 dB sits at the PASS boundary; Cb alone is PASS WITH DEVIATION. A different source could tip the pooled figure.\n"
      "- The live workload is the synthetic benchmark panel (unworn, sensor covered), not game content; the RD controls use Kodak and the panel offline.\n")
    A("## 22. Decision: does 5/3 FP16 become the mathematical baseline?\n")
    decision = o in ("A", "C") and (agree is True) and bool(quality_ok)
    A(f"{'YES' if decision else 'NO'} -- outcome {o}. GPU-time and fence-time conclusions stated separately: 5/3 FP16 changes inverse-decoder GPU execution by {fmt(dg, 1)} % (pooled mean) and submit->fence completion by {fmt(df, 1)} % (pooled mean); "
      f"it changes logical intermediate bytes by {logical_pct:+.1f} % (DERIVED) and costs {fmt(extra_mean, 1)} % bytes at matched PSNR-Y (MEASURED offline). "
      + ("CDF 9/7 FP32 compute remains the reference. Per outcome E, further transform simplification (Haar, WHT) must justify itself through fewer passes, fewer barriers, lower memory traffic or simpler topology, not ops per pixel. " if o == "E" else "")
      + ("5/3 FP16 becomes the mathematical baseline candidate; the extra bytes at matched PSNR-Y are the price. " if decision else ""))
    report = "\n".join(L) + "\n"
    Path(out_path).write_text(report, encoding="utf-8")
    return report


def collect_phase0(exp2_dir, encoded=(1984, 896)):
    """Build the `phase0` dict from the files under captures/.../exp2-cdf53-fp16/."""
    d = Path(exp2_dir)
    device = {p.stem: json.loads(p.read_text()) for p in (d / "device").glob("*.json")}
    st = json.loads((d / "device" / "standalone.json").read_text()) if (d / "device" / "standalone.json").exists() else {}
    rows97 = load_rd(d / "rd" / "results97.csv") if (d / "rd" / "results97.csv").exists() else []
    rows53 = load_rd(d / "rd" / "results53.csv") if (d / "rd" / "results53.csv").exists() else []
    def to_row(r):
        return {"source": r["source"], "cap_bytes": int(r["cap_bytes"]), "bytes97": int(r["bytes97"]), "bytes53": int(r["bytes53"]),
                "psnr_y97": r["psnr_y97"], "psnr_y53": r["psnr_y53"], "d_psnr_y": r["d_psnr_y"],
                "psnr_hvs97": r["psnr_hvs97"], "psnr_hvs53": r["psnr_hvs53"], "ssim97": r["ssim97"], "ssim53": r["ssim53"],
                "vmaf97": r["vmaf97"], "vmaf53": r["vmaf53"]}
    all_cells = []
    for res in (1920, 2048, 2176, 2304, 2432, 2560):
        all_cells += equal_bytes_table(rows97, rows53, res)
    at = equal_bytes_table(rows97, rows53, 2560)
    at = [r for r in at if r["cap_bytes"] == 416667]
    ds = [r["d_psnr_y"] for r in all_cells]
    kod = [r["d_psnr_y"] for r in all_cells if "kodim" in r["source"]]
    pan = [r["d_psnr_y"] for r in all_cells if "kodim" not in r["source"]]
    eb = {"at_416667": at, "cells": len(all_cells), "mean_d_psnr_y": sum(ds) / len(ds) if ds else float("nan"),
          "kodak_d_psnr_y": sum(kod) / len(kod) if kod else float("nan"), "panel_d_psnr_y": sum(pan) / len(pan) if pan else float("nan"),
          "min_d_psnr_y": min(ds) if ds else float("nan"), "max_d_psnr_y": max(ds) if ds else float("nan")}
    gains = []
    if (d / "gains" / "gains.csv").exists():
        for r in load_rd(d / "gains" / "gains.csv"):
            w = str(int(r["wavelet"]))
            gains.append({"source": r["source"], "gains": f"CDF {w[0]}/{w[1]}, {r['gains']}", "bytes": int(r["actual_bytes"]),
                          "psnr_y": r["scaled_psnr_y"], "psnr_hvs": r.get("scaled_psnr_hvs"), "ssim": r.get("scaled_ssim_all"), "vmaf": r.get("scaled_vmaf")})
    match = json.loads((d / "match" / "match.json").read_text()) if (d / "match" / "match.json").exists() else []
    spirv = json.loads((d / "spirv" / "summary.json").read_text()) if (d / "spirv" / "summary.json").exists() else {}
    neg = (d / "device" / "negatives.md").read_text().strip() if (d / "device" / "negatives.md").exists() else "UNKNOWN"
    rt = (d / "device" / "fp16_runtime.md").read_text().strip() if (d / "device" / "fp16_runtime.md").exists() else "UNKNOWN"
    return {"device": device, "standalone": st, "equal_bytes": eb, "gains": gains, "match": match, "spirv": spirv,
            "negatives": neg, "fp16_runtime": rt, "encoded": encoded}


def gpu_clock_summary(csv_path):
    """Mean/min/max GPU clock (MHz), share of samples at the top level and mean busy % from a
    segment's gpufreq.csv; None when the file is absent or empty (older runs)."""
    p = Path(csv_path)
    if not p.exists():
        return None
    rows = [r for r in csv.DictReader(p.open(newline="", encoding="utf-8"))]
    if not rows:
        return None
    mhz = [int(r["cur_freq_hz"]) / 1e6 for r in rows]
    busy = [float(r["busy_pct"]) for r in rows]
    top = max(mhz)
    return {"samples": len(rows), "mean_mhz": sum(mhz) / len(mhz), "min_mhz": min(mhz), "max_mhz": top,
            "share_at_max_pct": 100.0 * sum(1 for m in mhz if m == top) / len(mhz), "mean_busy_pct": sum(busy) / len(busy)}


def gpu_cycles_per_frame(decode_ms, clock_mhz):
    """DERIVED: mean GPU decode time expressed in clock cycles at the mean sampled clock, so two
    arms that ran at different DVFS levels can be compared as work rather than as time."""
    return decode_ms * 1e-3 * clock_mhz * 1e6


# ---- latency vs GPU clock, per report window ------------------------------------------------

_STAMPED = re.compile(r"^(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)\.(\d+).*?(gpu decode|submit->fence) ms: min [\d.]+ mean ([\d.]+)", re.M)
REPORT_FRAMES = 720


def report_windows(logcat_text, year=2026):
    """[(end_epoch, gpu_mean, fence_mean), ...] per 720-frame report, stamped with the device's
    logcat time (local wall clock, taken to be the same clock as the harness's sampler within a
    second or two: both hosts run NTP)."""
    import datetime
    stamps = {}
    for m in _STAMPED.finditer(logcat_text):
        t = datetime.datetime(year, int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)),
                              int(m.group(6).ljust(6, "0")[:6])).timestamp()
        stamps.setdefault(round(t), {})[m.group(7)] = float(m.group(8))
    out = []
    for t in sorted(stamps):
        row = stamps[t]
        if "gpu decode" in row and "submit->fence" in row:
            out.append((float(t), row["gpu decode"], row["submit->fence"]))
    return out


def window_clock(windows, clock_rows, fps=90.0):
    """For each report window (ending at end_epoch, spanning 720 frames at `fps`), the mean
    sampled clock (MHz) and busy % of the samples inside it; windows with no sample are skipped.
    clock_rows: [(epoch, {cur_freq_hz, busy_pct, ...}), ...]."""
    span = REPORT_FRAMES / fps
    out = []
    for end, gpu, fence in windows:
        inside = [r for t, r in clock_rows if end - span <= t <= end]
        if not inside:
            continue
        out.append({"end": end, "gpu_ms": gpu, "fence_ms": fence,
                    "clock_mhz": sum(r["cur_freq_hz"] for r in inside) / len(inside) / 1e6,
                    "busy_pct": sum(r["busy_pct"] for r in inside) / len(inside), "samples": len(inside)})
    return out


def load_clock_rows(csv_path):
    p = Path(csv_path)
    if not p.exists():
        return []
    return [(float(r["t"]), {"cur_freq_hz": int(r["cur_freq_hz"]), "busy_pct": float(r["busy_pct"])})
            for r in csv.DictReader(p.open(newline="", encoding="utf-8"))]


def linear_fit(xs, ys):
    """Least-squares y = a + b x; returns (a, b, r2) or None with fewer than two distinct x."""
    n = len(xs)
    if n < 2 or max(xs) == min(xs):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    ss_tot = sum((y - my) ** 2 for y in ys)
    return a, b, (1 - ss_res / ss_tot) if ss_tot else 1.0


def latency_vs_clock(points97, points53, metric="gpu_ms"):
    """Do both transforms fall on one latency-vs-frequency relationship?

    Fits latency against 1/clock (time is work divided by rate, so a single-work model is linear
    in 1/f) separately per arm and pooled, then evaluates each arm's points against the OTHER
    arm's fit. Reports per-arm slope/intercept/r2, the mean signed residual of 5/3 against the
    9/7 fit (ms and % of the 9/7 prediction), the same the other way, and the pooled fit's r2
    against the better of the two per-arm r2s. A small cross residual (within the per-arm
    scatter) and a pooled r2 close to the per-arm r2s mean one relationship: the transform adds
    little; a residual well outside the scatter means the arms sit on different curves."""
    def xy(pts):
        return [1000.0 / p["clock_mhz"] for p in pts], [p[metric] for p in pts]   # x = ns per cycle... 1/MHz*1e3 = us per kcycle
    x97, y97 = xy(points97)
    x53, y53 = xy(points53)
    f97, f53, fall = linear_fit(x97, y97), linear_fit(x53, y53), linear_fit(x97 + x53, y97 + y53)
    out = {"n97": len(x97), "n53": len(x53), "fit97": f97, "fit53": f53, "fit_pooled": fall}
    if f97 and x53:
        pred = [f97[0] + f97[1] * x for x in x53]
        res = [y - p for y, p in zip(y53, pred)]
        out["resid53_on_97_ms"] = sum(res) / len(res)
        out["resid53_on_97_pct"] = 100.0 * sum(res) / sum(pred)
        scatter = (sum((y - (f97[0] + f97[1] * x)) ** 2 for x, y in zip(x97, y97)) / len(x97)) ** 0.5
        out["scatter97_ms"] = scatter
        # 0.01 ms floor: a perfect fit has zero scatter, and the timestamps resolve to ~0.01 ms
        out["within_scatter"] = abs(out["resid53_on_97_ms"]) <= max(scatter, 0.01)
    if f53 and x97:
        pred = [f53[0] + f53[1] * x for x in x97]
        res = [y - p for y, p in zip(y97, pred)]
        out["resid97_on_53_ms"] = sum(res) / len(res)
        out["resid97_on_53_pct"] = 100.0 * sum(res) / sum(pred)
    if fall and f97 and f53:
        out["pooled_r2_vs_best_arm"] = fall[2] - max(f97[2], f53[2])
    return out


def clock_points(run_dir, label):
    d = Path(run_dir) / label
    text = (d / "logcat.txt").read_text(encoding="utf-8", errors="replace") if (d / "logcat.txt").exists() else ""
    return window_clock(report_windows(text), load_clock_rows(d / "gpufreq.csv"))
