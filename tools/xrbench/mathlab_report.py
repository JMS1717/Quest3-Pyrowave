"""Render the strict 24-section research report from the lab CSVs, the offline RD matrix, the
measured headset baseline and the (static) external evidence inventory. The JSON in section 23 is
built from the same Python objects that fill the tables, so the two cannot disagree.

    python -m xrbench.mathlab_report --lab <dir with rd.csv ...> --matrix <results.csv> --out <dir>
"""
import csv
import json
from pathlib import Path

# ---- fixed identities ---------------------------------------------------------------------------

CANDIDATES = [
    # id, name, transform, implementation, repo, license, gpu_api, cpu, gpu, enc, dec, maint, evidence_quality
    ("C01", "PyroWave", "CDF 9/7 irreversible DWT, 5 levels, raw bit-planes (no entropy coder)",
     "pyrowave (Themaister)", "https://github.com/Themaister/pyrowave", "MIT", "Vulkan compute / fragment",
     "packet parse only", "dequant + iDWT", "YES", "YES", "active (2025)", "MEASURED on device + PUBLISHED desktop"),
    ("C02", "Haar wavelet", "2-tap orthonormal lifting, hierarchical", "none found (lab: numpy)", "UNKNOWN", "NOT APPLICABLE",
     "NOT APPLICABLE", "NOT APPLICABLE", "NOT APPLICABLE", "lab only", "lab only", "NOT APPLICABLE", "MEASURED (lab RD) + DERIVED cost"),
    ("C03", "Walsh-Hadamard", "block WHT 8/16/32/64, add/sub butterflies", "none found for images (HadaCore is an ML kernel)",
     "https://github.com/HanGuo97/hadacore (UNVERIFIED)", "UNKNOWN", "CUDA (HadaCore)", "NOT APPLICABLE", "NOT APPLICABLE",
     "lab only", "lab only", "UNKNOWN", "MEASURED (lab RD) + DERIVED cost"),
    ("C04", "DCT", "block DCT-II 8/16/32 (orthonormal)", "GPUJPEG (CESNET), nvJPEG, ffmpeg mjpeg",
     "https://github.com/CESNET/GPUJPEG", "BSD-2-Clause (GPUJPEG)", "CUDA", "Huffman (GPUJPEG: GPU)", "IDCT + colour",
     "YES", "YES", "active (2024-25)", "PUBLISHED desktop timings + MEASURED (lab RD)"),
    ("C05", "CDF 5/3 wavelet", "2-step integer lifting (JPEG 2000 reversible / JPEG XS)", "JPEG XS (intoPIX, commercial); lab numpy",
     "https://www.intopix.com/fasttico-xs-sdks", "commercial", "CUDA/OpenCL (intoPIX)", "entropy decode", "iDWT",
     "YES (commercial)", "YES (commercial)", "active", "PUBLISHED (marketing, no numbers) + MEASURED (lab RD)"),
    ("C06", "Daubechies db2/db4", "orthogonal 4-/8-tap filter banks", "none (lab numpy)", "UNKNOWN", "NOT APPLICABLE",
     "NOT APPLICABLE", "NOT APPLICABLE", "NOT APPLICABLE", "lab only", "lab only", "NOT APPLICABLE", "MEASURED (lab RD) + DERIVED cost"),
    ("C07", "Laplacian pyramid", "5-tap binomial down/up, residual pyramid (4/3 over-complete)", "none (lab numpy)", "UNKNOWN",
     "NOT APPLICABLE", "NOT APPLICABLE", "NOT APPLICABLE", "NOT APPLICABLE", "lab only", "lab only", "NOT APPLICABLE",
     "MEASURED (lab RD) + DERIVED cost"),
    ("C08", "Fixed PCA / KLT", "offline eigenbasis (colour measured; spatial not run)", "none", "UNKNOWN", "NOT APPLICABLE",
     "NOT APPLICABLE", "NOT APPLICABLE", "NOT APPLICABLE", "no", "no", "NOT APPLICABLE", "MEASURED (colour PCA only)"),
    ("C09", "VC-2 / Dirac Pro", "wavelet (5/3, 9/7 options), slices, exp-Golomb", "FFmpeg vc2 (CPU); Vulkan port in progress",
     "https://github.com/FFmpeg/FFmpeg", "LGPL-2.1+", "Vulkan compute (in progress)", "Golomb parse", "iDWT",
     "YES", "YES", "active", "PUBLISHED structure only"),
    ("C10", "Hardware H.264 / HEVC", "block DCT + inter/intra prediction, CABAC", "Adreno video block via MediaCodec",
     "NOT APPLICABLE", "NOT APPLICABLE", "MediaCodec", "none", "fixed-function", "NVENC", "hardware", "NOT APPLICABLE",
     "MEASURED on device"),
]

EVIDENCE = [
    ("E001", "C01", "https://themaister.net/blog/2025/06/16/i-designed-my-own-ridiculously-fast-game-streaming-video-codec-pyrowave/",
     "RX 9070 XT (RADV)", "1080p / 4K 4:2:0", "y4m 8-bit", "~1.5 bpp", "200+ Mbit/s at 60 fps", "0.13 ms (1080p) / 0.25 ms (4K)",
     "\"Under 100 microseconds\"", "none", "UNKNOWN", "GPU time; transfers/parse UNKNOWN", "PUBLISHED"),
    ("E002", "C01", "https://github.com/Themaister/pyrowave", "UNKNOWN", "1080p / 4K", "UNKNOWN", "UNKNOWN", "UNKNOWN",
     "< ~0.1 ms (1080p), < ~0.2 ms (4K)", "same claim", "none", "UNKNOWN", "UNKNOWN", "PUBLISHED"),
    ("E003", "C01", "https://docs.punktfunk.unom.io/docs/pyrowave", "RTX 5070 Ti", "1080p", "4:2:0 8-bit", "~1.6 bpp", "UNKNOWN",
     "~0.15 ms", "~0.07 ms", "none", "UNKNOWN", "\"GPU compute\"; transfers UNKNOWN", "PUBLISHED (secondary)"),
    ("E004", "C04", "https://github.com/CESNET/GPUJPEG/blob/master/README.md", "RTX 3080", "HD / 4K / 8K", "RGB", "q75, non-interleaved, rst 24-36",
     "UNKNOWN", "0.54 ms HD / 1.71 ms 4K", "0.75 ms HD / 1.94 ms 4K / 6.76 ms 8K", "none", "UNKNOWN", "mean of 99 runs; transfers UNKNOWN", "PUBLISHED"),
    ("E005", "C04", "https://github.com/CESNET/GPUJPEG/issues/25", "RTX 2070 Super", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN",
     "UNKNOWN", "23.01 ms GPU vs 278.78 ms full", "none", "UNKNOWN", "full figure includes transfers+preprocessing", "PUBLISHED (issue thread)"),
    ("E006", "C04", "https://github.com/CESNET/UltraGrid/wiki/Performance", "UNKNOWN", "1080p30", "UNKNOWN", "JPEG q90", "UNKNOWN",
     "NOT APPLICABLE", "end-to-end 4 frames vs 3.75 uncompressed", "none", "UNKNOWN", "whole system, frames", "PUBLISHED"),
    ("E007", "C04", "https://arxiv.org/abs/2111.09219", "A100 / V100", "UNKNOWN", "JPEG", "UNKNOWN", "UNKNOWN", "NOT APPLICABLE",
     "relative: up to 3.4x over nvJPEG HW", "none", "UNKNOWN", "UNKNOWN", "PUBLISHED"),
    ("E008", "C05", "https://www.intopix.com/fasttico-xs-sdks", "UNKNOWN", "\"8K real-time\"", "UNKNOWN", "UNKNOWN", "UNKNOWN",
     "UNKNOWN", "UNKNOWN", "none", "UNKNOWN", "marketing claim", "PUBLISHED (marketing)"),
    ("E009", "C09", "https://www.khronos.org/blog/video-encoding-and-decoding-with-vulkan-compute-shaders-in-ffmpeg", "UNKNOWN", "UNKNOWN",
     "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "none", "UNKNOWN", "structure only (Golomb parse simplification)", "PUBLISHED"),
    ("E010", "FFv1 (reference only)", "https://www.khronos.org/blog/video-encoding-and-decoding-with-vulkan-compute-shaders-in-ffmpeg (claim unverified at source)",
     "RTX 6000 Ada", "3840x2160 bgr0", "lossless", "50 Mbps", "NOT APPLICABLE", "UNKNOWN", "\"400 fps\" (throughput)", "lossless", "NOT APPLICABLE",
     "throughput, not single-frame latency", "PUBLISHED (unverified)"),
    ("E011", "C04/DXT", "https://doi.org/10.1016/j.future.2013.06.006", "UNKNOWN", "1080p", "UNKNOWN", "UNKNOWN", "UNKNOWN", "NOT APPLICABLE",
     "133 ms (JPEG) / 166 ms (DXT) motion-to-photon", "none", "UNKNOWN", "whole system", "PUBLISHED"),
    ("E101", "C01", "captures/pyrowave-in-alvr/udp-ladder/README.md (this project)", "Galaxy XR, Adreno 740", "1984x896 4:4:4 (60 % render, foveated)",
     "PyroWave UDP", "400 Mbps @ 90 Hz", "~556 KB/frame", "6.8 ms (RTX 3090, ALVR stage)", "GPU decode 2.5/3.7/4.5/5.2 ms min/mean/p95/p99; submit->fence 6.4/8.3/8.9",
     "subjective", "\"good and smooth\"", "GPU timestamps on device (decode, convert, fence)", "MEASURED"),
    ("E102", "C01", "memory: pyrowave-client-decode-path (this project)", "Galaxy XR, Adreno 740", "3328x1472 4:4:4 / 4:2:0", "PyroWave", "600 Mbps @ 72 Hz",
     "1,041,660 B", "NOT APPLICABLE", "4:4:4 6.23 best / 8.19 mean ms; 4:2:0 3.64 / 5.08", "PSNR-Y vs PC decode", "55.16 dB (headset) vs 57.76 (PC)",
     "GPU timestamps, 200 iterations", "MEASURED"),
    ("E103", "C10", "memory: pyrowave-beats-the-hw-decoder (this project)", "Galaxy XR, Adreno video block", "3328x1472", "H.264 / HEVC via MediaCodec",
     "600 Mbps", "UNKNOWN", "NOT APPLICABLE", "H.264 14.80-18.88 ms; HEVC 82.01 ms", "none", "NOT APPLICABLE", "ALVR decoder stage (packet received -> frame out)", "MEASURED"),
    ("E104", "C01", "captures/pyrowave-in-alvr/rd-matrix/SUMMARY.md (this project)", "RTX 3090 (encode/decode), scoring ffmpeg/libvmaf", "1920..2560 per eye, SBS",
     "4:4:4 8-bit", "150..550 KB caps", "exact caps", "~0.3 s process", "~0.3 s process (not decode latency)", "PSNR / PSNR-HVS / SSIM / VMAF",
     "see matrix", "process wall time", "MEASURED (quality) / NOT decode latency"),
]

# Decoder cost models per N luma pixels (DERIVED from transform structure; see section 7/9/10 text).
COST = {
    "C01": dict(mult=8.0, add=11.0, passes_compute=2.0, passes_fragment=4.0, temp="1x coefficients (FP16) + LL chain", serial="5 sequential levels",
                parallel="per 32x32 tile per level; bands independent", sync="1 barrier/level (compute); 3/level (fragment)", conf="DERIVED"),
    "C02": dict(mult=0.0, add=2.7, passes_compute=1.0, passes_fragment=2.0, temp="coefficients only; multi-level fusable in one tile", serial="levels fusable (2-tap support)",
                parallel="fully tile-local", sync="1 (if fused)", conf="ESTIMATED"),
    "C03": dict(mult=0.0, add=None, passes_compute=1.0, passes_fragment=1.0, temp="one block in registers", serial="none across blocks",
                parallel="block-local", sync="1", conf="DERIVED (2*log2 n adds/pixel: 6/8/10/12 for 8/16/32/64)"),
    "C04": dict(mult=None, add=None, passes_compute=1.0, passes_fragment=1.0, temp="one block in registers", serial="none across blocks",
                parallel="block-local", sync="1", conf="DERIVED (direct: 2n mult+2n add per pixel; fast 8x8 AAN ~1.25 mult + 7 add)"),
    "C05": dict(mult=0.0, add=8.0 * 1.33, passes_compute=2.0, passes_fragment=4.0, temp="as C01", serial="5 sequential levels",
                parallel="as C01", sync="as C01", conf="DERIVED (shifts instead of multiplies)"),
    "C06": dict(mult=16.0 * 1.33, add=14.0 * 1.33, passes_compute=2.0, passes_fragment=4.0, temp="as C01", serial="5 levels",
                parallel="as C01 (8-tap support widens aprons)", sync="as C01", conf="DERIVED (db4)"),
    "C07": dict(mult=10.0 * 1.33, add=9.0 * 1.33, passes_compute=2.0 * 1.33, passes_fragment=None, temp="1.33x coefficients", serial="5 levels",
                parallel="per level", sync="1/level", conf="DERIVED"),
    "C08": dict(mult=None, add=None, passes_compute=1.0, passes_fragment=None, temp="basis matrix", serial="none", parallel="block-local",
                sync="1", conf="ESTIMATED (n^2 mult per block sample for an n x n basis; not run spatially)"),
    "C09": dict(mult=None, add=None, passes_compute=None, passes_fragment=None, temp="UNKNOWN", serial="Golomb parse serial per slice",
                parallel="per slice", sync="UNKNOWN", conf="UNKNOWN"),
    "C10": dict(mult=None, add=None, passes_compute=None, passes_fragment=None, temp="fixed-function", serial="CABAC serial", parallel="fixed-function",
                sync="MediaCodec queue", conf="NOT APPLICABLE (hardware)"),
}

TRANSFORM_TO_CANDIDATE = {"cdf97": "C01", "haar": "C02", "wht8": "C03", "wht16": "C03", "wht32": "C03", "wht64": "C03",
                          "dct8": "C04", "dct16": "C04", "dct32": "C04", "cdf53": "C05", "db2": "C06", "db4": "C06", "lap": "C07"}

INTEGRITY_RULES = """1. Never convert an ESTIMATE into a RESULT.
2. Never compare incompatible quality metrics as equivalent.
3. Never extrapolate desktop GPU latency to Galaxy XR and call it measured headset performance.
4. Never use throughput FPS as a substitute for single-frame decode latency.
5. Never hide missing data.
6. Never average results from different datasets without preserving the individual results.
7. Never silently exclude contradictory evidence.
8. Never select a preferred candidate before comparable measurements exist.
9. Never optimize for compression ratio when decoder latency is the primary objective.
10. Never assume symmetric encoder/decoder complexity is desirable.
11. Always preserve source URLs for externally derived claims.
12. Always state what a benchmark actually timed.
13. Always distinguish same-frame CPU/GPU parallelism from multi-frame pipelining.
14. Always preserve raw benchmark results alongside derived conclusions.
15. When evidence is insufficient, output UNKNOWN rather than filling the gap with intuition."""


# ---- data loading -------------------------------------------------------------------------------

def _rows(path):
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def rd_pivot(rd_rows, resolution=2560):
    """{source: {transform: {cap: row}}} at one resolution."""
    out = {}
    for r in rd_rows:
        if int(r["resolution"]) != resolution:
            continue
        out.setdefault(r["source"], {}).setdefault(r["transform"], {})[int(r["cap_bytes"])] = r
    return out


def transform_summary(rd_rows, resolution=2560, cap=416_667):
    """Per transform: mean psnr_y over Kodak sources and over the synthetic source at the cap,
    plus mean entropy-floor bytes -- datasets kept separate."""
    piv = rd_pivot(rd_rows, resolution)
    out = {}
    for src, by_t in piv.items():
        ds = "kodak" if src.startswith("kodak") else "synthetic"
        for t, by_cap in by_t.items():
            r = by_cap.get(cap)
            if not r:
                continue
            d = out.setdefault(t, {"kodak": [], "synthetic": [], "entropy_kodak": [], "psnr_hvs_kodak": [], "psnr_hvs_synth": [], "ssim_kodak": []})
            d[ds].append(_f(r["psnr_y"]))
            if ds == "kodak":
                d["entropy_kodak"].append(_f(r["bytes_entropy"]))
                d["ssim_kodak"].append(_f(r["ssim_y"]))
                if _f(r.get("psnr_hvs")) is not None:
                    d["psnr_hvs_kodak"].append(_f(r["psnr_hvs"]))
            elif _f(r.get("psnr_hvs")) is not None:
                d["psnr_hvs_synth"].append(_f(r["psnr_hvs"]))
    mean = lambda xs: (sum(xs) / len(xs)) if xs else None
    return {t: {"psnr_y_kodak_mean": mean(d["kodak"]), "psnr_y_synth": mean(d["synthetic"]), "entropy_bytes_kodak_mean": mean(d["entropy_kodak"]),
                "psnr_hvs_kodak_mean": mean(d["psnr_hvs_kodak"]), "psnr_hvs_synth": mean(d["psnr_hvs_synth"]), "ssim_kodak_mean": mean(d["ssim_kodak"])}
            for t, d in out.items()}


def bytes_for_quality(rd_rows, transform, source, target_psnr, resolution=2560):
    """Smallest measured cap whose psnr_y >= target, else None (no interpolation)."""
    rows = sorted([r for r in rd_rows if r["transform"] == transform and r["source"] == source and int(r["resolution"]) == resolution],
                  key=lambda r: int(r["cap_bytes"]))
    for r in rows:
        if _f(r["psnr_y"]) is not None and _f(r["psnr_y"]) >= target_psnr:
            return int(r["cap_bytes"]), _f(r["psnr_y"])
    return None


def pareto4(points):
    """points: dicts with quality (higher better), bytes, cost_ops, cost_passes (lower better).
    Returns (front, dominated_with_reason)."""
    front, dominated = [], []
    for p in points:
        dom = None
        for o in points:
            if o is p:
                continue
            if (o["quality"] >= p["quality"] and o["bytes"] <= p["bytes"] and o["ops"] <= p["ops"] and o["passes"] <= p["passes"]
                    and (o["quality"] > p["quality"] or o["bytes"] < p["bytes"] or o["ops"] < p["ops"] or o["passes"] < p["passes"])):
                dom = o
                break
        (dominated if dom else front).append((p, dom))
    return [p for p, _ in front], dominated


def ops_per_pixel(transform):
    c = COST[TRANSFORM_TO_CANDIDATE[transform]]
    if transform.startswith("wht"):
        n = int(transform[3:]); return 2 * (n.bit_length() - 1)
    if transform.startswith("dct"):
        n = int(transform[3:]); return 4 * n  # direct matrix: 2n mult + 2n add per pixel (upper bound)
    return (c["mult"] or 0) + (c["add"] or 0)


def passes(transform, path="fragment"):
    c = COST[TRANSFORM_TO_CANDIDATE[transform]]
    v = c["passes_fragment"] if path == "fragment" else c["passes_compute"]
    return v if v is not None else c["passes_compute"]


# ---- rendering ----------------------------------------------------------------------------------

def render(lab_dir, matrix_csv, out_dir):
    lab_dir, out_dir = Path(lab_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rd = _rows(lab_dir / "rd.csv"); sp = _rows(lab_dir / "sparsity.csv"); pr = _rows(lab_dir / "precision.csv")
    co = _rows(lab_dir / "colour.csv"); pc = _rows(lab_dir / "precond.csv"); bv = _rows(lab_dir / "bandvalue.csv")
    mx = _rows(matrix_csv)
    summ = transform_summary(rd)
    kodak_sources = sorted({r["source"] for r in rd if r["source"].startswith("kodak")})
    L = []
    A = L.append

    def t_ok(name):
        return name in summ

    # ---------- 0
    A("# 0. EXECUTIVE RESEARCH SUMMARY\n")
    A("Current PyroWave baseline (all MEASURED unless marked):")
    A("- transform: CDF 9/7 irreversible float lifting, 5 levels, 32x32 independent blocks, raw bit-planes with no entropy coder (from source, checkout d2997ac)")
    A("- target resolution: 2560x2560 per eye is the research reference; the measured live operating point renders 60 % linear (2131x2304) and encodes a gaze-foveated 1984x896 frame")
    A("- target refresh rate: 90 Hz (11.1 ms period)")
    A("- practical bitrate region: 300-400 Mbps at 90 Hz (416,667-555,556 B/frame); link measured 708-978 Mbps")
    A("- known decode constraints: on the Adreno 740 the GPU decode is 3.7 ms mean / 5.2 ms p99 at 1984x896 4:4:4 but submit->fence is 6.4 / 8.9 ms, and the fence balloons to 12-18 ms at thermal status 3; the fragment path (3 render passes per level) is what the Adreno runs (E101, E102)")
    A("- known quality reference: PSNR-Y 57.45 dB at 600 Mbps on the dashboard frame (3328x1472, decoded vs encoder input); offline, 2560^2 @ 416,667 B scores PSNR-HVS 21.6 dB on tiled kodim08 and 57.5 dB on the synthetic panel against the scaled reference (E104)\n")
    A("Most important findings:")
    findings = []
    if t_ok("cdf97") and t_ok("haar"):
        d = summ["cdf97"]["psnr_y_kodak_mean"] - summ["haar"]["psnr_y_kodak_mean"]
        findings.append(f"MEASURED (lab, bit-plane size model, 2560^2, 416,667 B): CDF 9/7 beats Haar by {d:.2f} dB PSNR-Y on the Kodak mean ({summ['cdf97']['psnr_y_kodak_mean']:.2f} vs {summ['haar']['psnr_y_kodak_mean']:.2f}); on the synthetic panel the order REVERSES: Haar {summ['haar']['psnr_y_synth']:.2f} vs 9/7 {summ['cdf97']['psnr_y_synth']:.2f} (contradictory evidence, preserved). Haar's inverse needs no multiplications and about a quarter of 9/7's adds (DERIVED).")
    if t_ok("cdf53"):
        findings.append(f"MEASURED: CDF 5/3 (shift-only lifting) scores {summ['cdf53']['psnr_y_kodak_mean']:.2f} dB Kodak mean vs 9/7's {summ['cdf97']['psnr_y_kodak_mean']:.2f} at the same bytes -- the cheapest transform that keeps most of 9/7's compaction.")
    best_block = max([t for t in summ if t.startswith("dct") or t.startswith("wht")], key=lambda t: summ[t]["psnr_y_kodak_mean"], default=None)
    if best_block:
        findings.append(f"MEASURED: best block transform is {best_block} at {summ[best_block]['psnr_y_kodak_mean']:.2f} dB Kodak mean; block transforms are single-pass decoders (1 read + 1 write, DERIVED) against the wavelets' 2 full-resolution-equivalent passes (compute) or ~4 (fragment path).")
    if t_ok("lap"):
        findings.append(f"MEASURED: the Laplacian pyramid scores {summ['lap']['psnr_y_kodak_mean']:.2f} dB Kodak mean at the same bytes, carrying 4/3 the coefficients (over-complete) -- it pays for its simple arithmetic with memory traffic.")
    if pr:
        r97 = next((r for r in pr if r["transform"] == "cdf97" and r["precision"] == "fp16_math" and r["source"] == "kodak_kodim08"), None)
        r53 = next((r for r in pr if r["transform"] == "cdf53" and r["precision"] == "fp16_math" and r["source"] == "kodak_kodim08"), None)
        if r97 and r53:
            findings.append(f"MEASURED (lab): CDF 9/7 lifting in FP16 arithmetic reconstructs at only {float(r97['psnr_vs_fp32']):.1f} dB against its FP32 result (max error {r97['max_abs_err']} code values), while 5/3, Haar, DCT and WHT stay above {float(r53['psnr_vs_fp32']):.0f} dB in FP16 math; FP16 *storage* is free for all (>= 78 dB). The shorter transforms can run the whole inverse at half precision; 9/7 cannot -- which is why PyroWave defaults to FP32 math with FP16 storage.")
    if pc:
        r = next((r for r in pc if r["transform"] == "cdf97" and r["prefilter"] == "gauss_0.5" and r["source"] == "kodak_kodim08"), None)
        r0 = next((r for r in pc if r["transform"] == "cdf97" and r["prefilter"] == "none" and r["source"] == "kodak_kodim08"), None)
        rp = next((r for r in pc if r["transform"] == "cdf97" and r["prefilter"] == "gauss_0.5" and r["source"] == "synthetic_panel"), None)
        rp0 = next((r for r in pc if r["transform"] == "cdf97" and r["prefilter"] == "none" and r["source"] == "synthetic_panel"), None)
        if r and r0 and rp and rp0:
            findings.append(f"MEASURED (lab): encoder-side pre-filtering (Gaussian sigma 0.5) at the 416,667 B cap GAINS {float(r['psnr_vs_original_at_cap']) - float(r0['psnr_vs_original_at_cap']):+.2f} dB against the ORIGINAL on dense natural content (bytes at fixed step fall {100 * (1 - int(r['bytes_bitplane']) / int(r0['bytes_bitplane'])):.0f} %) and costs {float(rp['psnr_vs_original_at_cap']) - float(rp0['psnr_vs_original_at_cap']):+.1f} dB on the synthetic panel -- zero decoder cost, content-dependent sign.")
    findings.append("MEASURED (device): the arithmetic is not the bottleneck on the Adreno -- GPU decode 3.7 ms vs 6.4 ms submit->fence at a cool headset and 12-18 ms hot (E101); any candidate must be judged on passes and synchronisation, not multiplies.")
    # The strict format asks for five: device fence first, then the transform, 5/3, precision and
    # pre-filter results; block-transform and Laplacian findings stay in sections 14-16.
    order = [f_ for f_ in findings if f_.startswith("MEASURED (device)")] + \
            [f_ for f_ in findings if "beats Haar" in f_] + [f_ for f_ in findings if "CDF 5/3 (shift-only" in f_] + \
            [f_ for f_ in findings if "FP16 arithmetic" in f_] + [f_ for f_ in findings if "pre-filtering" in f_] + \
            [f_ for f_ in findings if f_ not in []]
    findings = list(dict.fromkeys(order))
    for i, f_ in enumerate(findings[:5], 1):
        A(f"{i}. {f_}")
    A("\nMost promising candidates for actual testing (unranked; see section 18):")
    for i, c in enumerate(("C05 CDF 5/3 inside PyroWave's own pipeline (shift-only lifting, same passes)",
                           "C02 Haar with multi-level fusion in one tile (fewest passes of any wavelet)",
                           "C04 block DCT 16/32 with a Vulkan port of a GPUJPEG-class IDCT (single pass)",
                           "C03 block WHT 16/32 (add/sub only, single pass) at the extra bytes the lab measured",
                           "C01 PyroWave compute path forced on the Adreno (halves the fragment path's passes) -- a build flag, not a new codec"), 1):
        A(f"{i}. {c}")
    A("\nLargest remaining unknowns:")
    for i, u in enumerate(("No mobile-GPU decode timing exists for any candidate but PyroWave (C01) and the hardware decoders (C10).",
                           "Whether PyroWave's compute path runs correctly and faster than its fragment path on the Adreno 740 (the driver check forces fragment).",
                           "How much of the 6.4-18 ms fence is queue contention with the runtime compositor rather than decode work.",
                           "Bit-plane packing cost is a model of PyroWave's bitstream applied to other transforms; a real entropy coder would change every size in this report.",
                           "Stereo disparity and real game content: every source is left = right and either tiled Kodak or the synthetic panel."), 1):
        A(f"{i}. {u}")
    A("\n**Scope of the lab caps:** every lab cell applies its byte cap to ONE eye's LUMA plane (2560x2560 = 6.55 Msamples), not to the pipeline's stereo 4:4:4 frame (5120x2560x3 = 39.3 Msamples). The caps therefore mean ~6x more bits per sample than the same numbers in the offline PyroWave matrix (E104), which is why lab PSNR-Y values sit ~7 dB above the matrix's for the same frame. Comparisons BETWEEN transforms in the lab are at equal bytes and equal samples and are fair; lab absolutes are not comparable to the matrix or to the headset.\n")
    A("No overall winner is declared: no candidate other than C01 and C10 has a comparable device measurement.\n")

    # ---------- 1
    A("---\n\n# 1. REQUIREMENTS AND CONSTRAINTS\n")
    A("| Variable | Requirement / Current Value | Evidence Class | Notes |\n|---|---:|---|---|")
    for row in (("Target per-eye resolution", "2560 x 2560 (research reference); 2131 x 2304 live", "MEASURED", "live point is 60 % of the 3552x3840 panel"),
                ("Target FPS", "90", "MEASURED", "runtime grants 90 Hz; held 5 min"),
                ("Frame interval", "11.11 ms", "DERIVED", "1/90"),
                ("Preferred bitrate", "300 Mbps", "MEASURED", "X60-300-90: 55.5 ms, 13 dropped of ~3,600"),
                ("Practical bitrate ceiling", "400 Mbps at 90 Hz", "MEASURED", "24k late packets, 92 dropped at 400; link 708-978 Mbps"),
                ("Target decode latency", "< 11.1 ms incl. fence, sustained hot", "DERIVED", "one period at 90 Hz"),
                ("Current PyroWave decode latency", "GPU 3.7 ms mean / 5.2 p99; fence 6.4 / 8.9 (cool); 12-18 hot", "MEASURED", "1984x896 4:4:4, E101"),
                ("Current PyroWave quality", "PSNR-Y 57.45 dB @ 600 Mbps dashboard frame; subjective \"good and smooth\" @ 400 Mbps 60 %", "MEASURED", "different frames; not comparable to lab numbers"),
                ("Encoder hardware", "RTX 3090", "OBSERVED", "encode 3.8-7.7 ms in ALVR; 0.25 ms in pyrowave-bench"),
                ("Decoder hardware", "Galaxy XR SM-I610, Adreno 740, Vulkan 1.3.295", "MEASURED", "fragment decode path selected by driver id"),
                ("Encoder compute constraint", "LOW PRIORITY", "NOT APPLICABLE", ""),
                ("Decoder compute constraint", "CRITICAL", "NOT APPLICABLE", ""),
                ("Decoder memory traffic", "CRITICAL", "NOT APPLICABLE", "fragment path ~2x compute-path intermediates (DERIVED)"),
                ("Thermal load", "CRITICAL", "MEASURED", "78 C plateau at status 2-3 over 5 min; fence 12-18 ms at status 3")):
        A("| " + " | ".join(row) + " |")

    # ---------- 2
    A("\n---\n\n# 2. CANDIDATE INVENTORY\n")
    A("| ID | Transform / Method | Implementation | Repository | License | GPU API | CPU Path | GPU Path | Encoder | Decoder | Maintenance Status | Evidence Quality |\n|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in CANDIDATES:
        A("| " + " | ".join([c[0], f"{c[1]}: {c[2]}", c[3], c[4], c[5], c[6], c[7], c[8], c[9], c[10], c[11], c[12]]) + " |")

    # ---------- 3
    A("\n---\n\n# 3. SOURCE EVIDENCE TABLE\n")
    A("| Evidence ID | Candidate ID | Source URL | Hardware | Resolution | Input Format | Quality Setting | Size / Bitrate | Encode Time | Decode Time | Quality Metric | Quality Value | What Was Timed | Evidence Class |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for e in EVIDENCE:
        A("| " + " | ".join(e) + " |")

    # ---------- 4
    A("\n---\n\n# 4. PUBLISHED BENCHMARKS\n")
    pub = {"C01": ("E001", "RX 9070 XT (RADV)", "1080p / 4K", "2,073,600 / 8,294,400", "\"under 100 microseconds\" (blog)", "0.13 ms / 0.25 ms", "UNKNOWN", "200+ Mbit/s @ 60 fps", "none", "none"),
           "C04": ("E004", "RTX 3080", "HD / 4K", "2,073,600 / 8,294,400", "0.75 ms / 1.94 ms", "0.54 ms / 1.71 ms", "UNKNOWN", "UNKNOWN (q75)", "none", "none"),
           "C05": ("E008", "UNKNOWN", "\"8K\"", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "none", "none"),
           "C09": ("E009", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "none", "none"),
           "C10": ("E103", "Galaxy XR Adreno video block", "3328x1472", "4,898,816", "H.264 14.80-18.88 ms; HEVC 82.01 ms (MEASURED, ALVR stage)", "NOT APPLICABLE", "UNKNOWN", "600 Mbps", "none", "none")}
    scope = {"C01": ("NO", "UNKNOWN", "YES (GPU timestamps)", "NO", "UNKNOWN", "NO", "NOT APPLICABLE (no entropy coder)", "YES"),
             "C04": ("UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "YES", "YES"),
             "C05": ("UNKNOWN",) * 8, "C09": ("UNKNOWN",) * 8,
             "C10": ("YES (MediaCodec input)", "YES", "YES", "NOT APPLICABLE (surface out)", "UNKNOWN", "YES", "YES", "YES")}
    for c in CANDIDATES:
        cid = c[0]
        A(f"## {cid} — {c[1]}\n")
        A("### Published Performance\n")
        if cid in pub:
            e = pub[cid]
            for k, v in zip(("Resolution", "Pixels", "Hardware", "Decode latency", "Encode latency", "Compressed size", "Bitrate", "Quality metric", "Quality value", "Source", "Evidence ID"),
                            (e[2], e[3], e[1], e[4], e[5], e[6], e[7], e[8], e[9], [x for x in EVIDENCE if x[0] == e[0]][0][2], e[0])):
                A(f"{k}: {v}")
        else:
            for k in ("Resolution", "Pixels", "Hardware", "Decode latency", "Encode latency", "Compressed size", "Bitrate", "Quality metric", "Quality value", "Source", "Evidence ID"):
                A(f"{k}: UNKNOWN (no published benchmark found; lab-only candidate)")
        A("\n### Timing Scope\n")
        s = scope.get(cid, ("NOT APPLICABLE",) * 8)
        for k, v in zip(("CPU parsing included", "Host→device transfer included", "GPU synchronization included", "Device→host transfer included",
                         "Allocation included", "Color conversion included", "Entropy decoding included", "Inverse transform included"), s):
            A(f"{k}: {v}")
        A("\n### Important Limitations\n")
        lim = {"C01": "Desktop numbers are for a different GPU class and a 4:2:0 1080p frame; our device numbers are for a 1984x896 4:4:4 frame with the fragment path. Neither is a 2560^2 stereo frame.",
               "C04": "GPUJPEG is CUDA-only; the Adreno has no CUDA. The published time includes Huffman decoding of a JPEG, which is not the DCT-alone architecture this research asks about.",
               "C05": "Marketing claim with no numbers, hardware or timing scope.",
               "C09": "Structure description only; no timings; Vulkan decoder status unclear.",
               "C10": "Measured as ALVR's decoder stage (packet received to frame out), which includes MediaCodec queueing; not comparable to GPU-timestamp decode times."}
        A(lim.get(cid, "No published performance exists for this candidate; the only evidence is this laboratory's rate-distortion measurement and a derived cost model.") + "\n")

    # ---------- 5
    A("---\n\n# 5. NORMALIZED DERIVED ESTIMATES\n")
    A("Target: 2560 x 2560 per eye, 2 eyes, 90 FPS. Total pixels/frame: 13,107,200. Total pixels/second: 1,179,648,000.\n")
    for name, eid, orig_px, orig_ms, conf, note in (
            ("C01 PyroWave decode, RX 9070 XT (E001)", "E001", 2_073_600, 0.10, "LOW", "blog says 'under 100 microseconds' at 1080p; GPU class differs from the Adreno by roughly two orders of magnitude in throughput, so this says nothing about the headset"),
            ("C04 GPUJPEG decode, RTX 3080 (E004)", "E004", 2_073_600, 0.75, "LOW", "includes Huffman decode; desktop CUDA")):
        A(f"Original benchmark: {name}\nOriginal pixel count: {orig_px:,}\nOriginal decode time: {orig_ms} ms\nTarget pixel count: 13,107,200\nScaling assumption: linear in pixels on the SAME GPU\nDerived target decode time: {orig_ms * 13_107_200 / orig_px:.2f} ms (same desktop GPU)\nEvidence class: DERIVED\nConfidence: {conf}\nNote: {note}\n")
    A("Headset: NORMALIZATION NOT JUSTIFIED. Desktop-to-Adreno scaling is not linear in pixels: our own device measurement shows the fence (6.4-18 ms) exceeding the GPU work (3.7 ms) and rising with temperature, so no published desktop number can be scaled to the Galaxy XR.\n")

    # ---------- 6
    A("---\n\n# 6. QUALITY COMPARABILITY MATRIX\n")
    A("| Candidate | Metric | Best Published Quality | Bitrate / Size | Comparable to PyroWave? | Reason |\n|---|---|---:|---:|---|---|")
    A("| C01 | PSNR-HVS-M-H (author's modified, luma) | ~35 dB 'good' curve | 125-300 Mbit/s 720p-4K | NO | author's own CSF weights and viewing-distance model; not reproducible here |")
    A("| C01 | PSNR-Y (this project) | 57.45 dB | 600 Mbps, 3328x1472 dashboard | YES | same evaluator (ffmpeg psnr) as the lab |")
    for cid, t in (("C02", "haar"), ("C03", best_block if best_block and best_block.startswith("wht") else "wht16"), ("C04", best_block if best_block and best_block.startswith("dct") else "dct16"), ("C05", "cdf53"), ("C06", "db4"), ("C07", "lap")):
        if t in summ:
            A(f"| {cid} | PSNR-Y (lab, bit-plane model) | {summ[t]['psnr_y_kodak_mean']:.2f} dB Kodak mean | 416,667 B | YES | identical sources, quantiser and size model as C01's lab row |")
    A("| C08 | none | NOT APPLICABLE | NOT APPLICABLE | NO | not run spatially |")
    A("| C09 | none published | UNKNOWN | UNKNOWN | UNKNOWN | no numbers found |")
    A("| C10 | subjective / fps | NOT APPLICABLE | 600 Mbps | NO | hardware codec; no PSNR pairing with timing |")
    A("\nPSNR, PSNR-HVS, PSNR-HVS-M, SSIM and MS-SSIM are reported in separate columns everywhere and never combined.\n")

    # ---------- 7
    A("---\n\n# 7. DECODER COMPUTATIONAL MODEL\n")
    stages = {"C01": ["CPU packet parse (headers, block offsets; no entropy decode)", "upload payload + offset table; barrier", "one fused bit-plane unpack + dequant dispatch per band (up to 60)", "inverse DWT: 5 levels; compute path fuses H+V per level, fragment path 3 render passes per level", "output planes -> colour convert (separate pass in libpyroclient)"],
              "C02": ["packet parse (same container assumed)", "unpack + dequant", "inverse Haar: 2-tap lifting per level; several levels fusable inside one 32x32 tile", "output", "colour convert"],
              "C03": ["packet parse", "unpack + dequant", "block WHT butterflies (log2 n stages of add/sub) per row then column, in registers", "output", "colour convert"],
              "C04": ["packet parse", "unpack + dequant (or Huffman if JPEG-style)", "block IDCT rows then columns in registers", "output", "colour convert"],
              "C05": ["packet parse", "unpack + dequant", "inverse 5/3: 2 lifting steps (shift+add) per level, same pass structure as C01", "output", "colour convert"],
              "C06": ["packet parse", "unpack + dequant", "8-tap synthesis filter bank per level (wider apron)", "output", "colour convert"],
              "C07": ["packet parse", "unpack + dequant of residual pyramid", "upsample + 5-tap blur + add residual, 5 levels (each level reads base and residual)", "output", "colour convert"],
              "C08": ["packet parse", "unpack", "n x n matrix multiply per block", "output", "colour convert"],
              "C09": ["slice parse (serial exp-Golomb)", "dequant", "inverse DWT", "output", "colour"],
              "C10": ["bitstream -> MediaCodec", "CABAC (fixed function)", "IDCT + prediction (fixed function)", "surface out", "sampler"]}
    for c in CANDIDATES:
        cid = c[0]; k = COST[cid]
        A(f"## {cid} — {c[1]}\n\n### Decode Stages\n")
        for i, s in enumerate(stages[cid], 1):
            A(f"{i}. {s}")
        A("\n### Estimated Computational Structure\n")
        A(f"Arithmetic complexity: mult/pixel {k['mult'] if k['mult'] is not None else 'see note'}, add/pixel {k['add'] if k['add'] is not None else 'see note'} ({k['conf']})")
        A(f"Memory reads: coefficients once per level-pass; full-res-equivalent passes {k['passes_compute']} (compute) / {k['passes_fragment']} (fragment)")
        A(f"Memory writes: same pass count; temporaries: {k['temp']}")
        A(f"Full-frame passes: {k['passes_compute'] if k['passes_compute'] is not None else 'UNKNOWN'}")
        A(f"Temporary buffers: {k['temp']}")
        A(f"Branching: {'per-8x8 ballot skip' if cid == 'C01' else 'UNKNOWN'}")
        A(f"Serial dependencies: {k['serial']}")
        A(f"Parallelism: {k['parallel']}")
        A(f"Synchronization points: {k['sync']}")
        A("\n### Likely Primary Bottleneck\n")
        bn = {"C01": "CPU/GPU SYNCHRONIZATION and MEMORY BANDWIDTH. Evidence: MEASURED device GPU decode 3.7 ms vs submit->fence 6.4-18 ms (E101); the fragment path's 3 passes per level double intermediate traffic (DERIVED from source).",
              "C10": "SERIAL DEPENDENCY (CABAC) in fixed function; MEASURED 14.8-82 ms at 3328x1472 (E103)."}
        A(bn.get(cid, "UNKNOWN on the headset (no measurement). From structure: block transforms (C03, C04, C08) are GPU DISPATCH / MEMORY BANDWIDTH bound with one pass; multi-level transforms (C02, C05, C06, C07) inherit C01's per-level synchronisation unless levels are fused. ESTIMATED.") + "\n")

    # ---------- 8
    A("---\n\n# 8. CPU + GPU PARALLELISM\n")
    A("| Candidate | CPU Work | GPU Work | Same-Frame Overlap Possible? | Independent Tiles/Bands? | Expected Benefit | Synchronization Risk | Evidence |\n|---|---|---|---|---|---|---|---|")
    for cid, cpu, gpu, ov, ind, ben, risk, ev in (
            ("C01", "packet parse, offset table", "unpack, dequant, iDWT", "YES: parse of later packets overlaps decode of earlier bands only if dispatch is per band as packets land (not today: decode starts after decode_is_ready)", "YES: 32x32 blocks and bands", "parse is ~1 ms scale (UNKNOWN exactly); main win is starting dequant before the last packet", "upload barrier per partial dispatch", "source (pyrowave_decoder.cpp), MEASURED counters"),
            ("C02", "parse", "lifting", "YES as C01", "YES", "as C01", "as C01", "ESTIMATED"),
            ("C03", "parse", "butterflies", "YES: blocks independent; decode can start per received block", "YES (blocks)", "highest: single pass, block-granular", "none beyond upload", "ESTIMATED"),
            ("C04", "parse (Huffman if JPEG)", "IDCT", "YES as C03", "YES (blocks)", "as C03", "Huffman is serial per restart interval if used", "E004 structure"),
            ("C05", "parse", "lifting", "as C01", "YES", "as C01", "as C01", "ESTIMATED"),
            ("C06", "parse", "filter bank", "as C01", "YES", "as C01", "as C01", "ESTIMATED"),
            ("C07", "parse", "upsample/add", "levels serial", "per level", "low", "per level", "ESTIMATED"),
            ("C08", "parse", "matrix mult", "as C03", "YES", "as C03", "none", "ESTIMATED"),
            ("C09", "Golomb parse (serial per slice)", "iDWT", "UNKNOWN", "slices", "UNKNOWN", "UNKNOWN", "E009"),
            ("C10", "none", "fixed function", "NO (MediaCodec queue)", "NO", "none", "queue", "MEASURED")):
        A(f"| {cid} | {cpu} | {gpu} | {ov} | {ind} | {ben} | {risk} | {ev} |")
    A("\nMULTI-FRAME PIPELINING (decoding frame N+1 while presenting N) is what ALVR's receiver already does and it does not reduce single-frame latency; only SAME-FRAME PARALLELISM does. No benchmark in section 3 demonstrates same-frame CPU/GPU overlap reducing latency.\n")

    # ---------- 9
    A("---\n\n# 9. MEMORY TRAFFIC ANALYSIS\n")
    N = 2560 * 2560 * 2
    A(f"Stereo luma frame N = {N:,} pixels (2 x 2560^2). Chroma 4:4:4 triples every figure below; values are per frame in MB, DERIVED from the pass models in section 7 with FP16 coefficients (2 B) and 8-bit output (1 B). Payload read = actual bytes (MEASURED per cell).\n")
    A("| Candidate | Input bytes read | Coefficient bytes written+read | Temporary written+reread | Output bytes written | Full-res passes | Total MB/frame (luma) | MB/s @ 90 Hz | Class |\n|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for cid, coef_mult, temp_mult, pss in (("C01", 2 * 2, 0.33 * 2 * 2, "2 (compute) / 4 (fragment)"), ("C02", 2 * 2, 0.0, "1 (fused)"), ("C03", 2 * 2, 0.0, "1"), ("C04", 2 * 2, 0.0, "1"),
                                          ("C05", 2 * 2, 0.33 * 2 * 2, "2 / 4"), ("C06", 2 * 2, 0.33 * 2 * 2, "2 / 4"), ("C07", 1.33 * 2 * 2, 0.33 * 2 * 2, "2.7"), ("C08", 2 * 2, 0.0, "1")):
        payload = 416_667
        total = (payload + coef_mult * N + temp_mult * N + N) / 1e6
        A(f"| {cid} | {payload / 1e6:.2f} (MEASURED cap) | {coef_mult * N / 1e6:.1f} | {temp_mult * N / 1e6:.1f} | {N / 1e6:.1f} | {pss} | {total:.1f} | {total * 90:.0f} | DERIVED |")
    A("| C09 | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |")
    A("| C10 | 0.42 | NOT APPLICABLE | UNKNOWN (fixed function) | 13.1 | NOT APPLICABLE | UNKNOWN | UNKNOWN | UNKNOWN |")
    A("\nThe fragment path (what the Adreno runs today) roughly doubles C01/C05/C06's temporary traffic (DERIVED from `idwt_fragment`: `vert[2][2]` targets of w_l x 2h_l per level). No candidate's memory traffic has been MEASURED.\n")

    # ---------- 10
    A("---\n\n# 10. MATHEMATICAL COMPLEXITY\n")
    A("| Candidate | Forward Transform Complexity | Inverse Transform Complexity | Primary Operations | Multiplications | Adds/Subtracts | Memory Passes | Serial Components |\n|---|---|---|---|---|---|---|---|")
    for cid, fwd, inv, ops, mul, add, mp, ser in (
            ("C01", "O(N) lifting, 5 levels", "O(N)", "4 lifting steps + scale per dim per level", "~8/pixel", "~11/pixel", "2 (compute) / 4 (fragment)", "5 levels"),
            ("C02", "O(N)", "O(N)", "add, sub", "0 (scale folded)", "~2.7/pixel", "1 if levels fused", "levels (fusable)"),
            ("C03", "O(N log n) per block", "O(N log n)", "add, sub butterflies", "0 (+1 scale)", "2 log2 n /pixel: 6, 8, 10, 12", "1", "none"),
            ("C04", "O(N n) direct / O(N log n) fast", "same", "mult-add", "direct 2n/pixel; AAN 8x8 ~1.25", "direct 2n; AAN ~7", "1", "none"),
            ("C05", "O(N)", "O(N)", "shift, add", "0", "~11/pixel", "2 / 4", "5 levels"),
            ("C06", "O(N)", "O(N)", "8-tap FIR", "~21/pixel (db4)", "~19/pixel", "2 / 4", "5 levels"),
            ("C07", "O(N)", "O(N)", "5-tap separable blur + add", "~13/pixel", "~12/pixel", "~2.7", "5 levels"),
            ("C08", "O(N n) per n x n basis", "same", "matrix-vector", "n/pixel per dim", "n/pixel per dim", "1", "none"),
            ("C09", "O(N) lifting", "O(N)", "lifting + Golomb", "UNKNOWN", "UNKNOWN", "UNKNOWN", "Golomb per slice"),
            ("C10", "NOT APPLICABLE", "NOT APPLICABLE", "fixed function", "NOT APPLICABLE", "NOT APPLICABLE", "NOT APPLICABLE", "CABAC")):
        A(f"| {cid} | {fwd} | {inv} | {ops} | {mul} | {add} | {mp} | {ser} |")
    A("\nAll counts DERIVED from the transform definitions (section 7 models); none MEASURED on hardware.\n")

    # ---------- 11
    A("---\n\n# 11. ASYMMETRIC ENCODER OPPORTUNITY\n")
    for cid, ans, moved, enc, dec, tx, arith, mem in (
            ("C01", "YES", "per-block quantiser selection already runs on the encoder (rate control); further: encoder-side pre-filtering (section 13 lab: preconditioning) and choosing per-band steps by measured marginal value (bandvalue.csv) move nothing to the decoder", "rate-control search over more candidates", "none (decoder unchanged)", "none", "0", "0"),
            ("C02", "YES", "encoder can pick per-block Haar depth so the decoder fuses all levels in one tile", "depth search", "level passes", "1 byte/block", "UNKNOWN", "up to (passes-1) x coefficient traffic"),
            ("C03", "YES", "encoder chooses block size per region; decoder always single pass", "block-size search", "none", "1 byte/block", "0", "0"),
            ("C04", "YES", "as C03; encoder can also pre-compensate quantisation (trellis) so the decoder does plain IDCT", "trellis search", "none", "none", "0", "0"),
            ("C05", "YES", "as C01", "as C01", "none", "none", "0", "0"),
            ("C06", "UNKNOWN", "NOT APPLICABLE", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN"),
            ("C07", "YES", "encoder can choose the downsample filter so the decoder's upsample is a fixed cheap kernel", "filter search", "none", "none", "0", "0"),
            ("C08", "YES", "basis learned offline on the encoder side; decoder applies a fixed matrix", "eigen-analysis (offline)", "none", "basis once", "0", "0"),
            ("C09", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN"),
            ("C10", "NO", "NOT APPLICABLE (fixed-function decoder)", "NOT APPLICABLE", "NOT APPLICABLE", "NOT APPLICABLE", "NOT APPLICABLE", "NOT APPLICABLE")):
        A(f"## {cid}\n\nCan additional RTX 3090 encoder computation reduce headset decode work? {ans}\n\nWhat moves: {moved}\n\nAdditional encoder work: {enc}\nRemoved decoder work: {dec}\nAdditional transmitted data: {tx}\nDecoder arithmetic saved: {arith}\nDecoder memory traffic saved: {mem}\n")

    # ---------- 12
    A("---\n\n# 12. COMMON-DATASET TESTABILITY\n")
    A("| Candidate | Kodak Compatible | Synthetic Panel Compatible | Exact Byte Target Possible | Exact Reconstruction Available | Timing Instrumentation Available | Integration Difficulty |\n|---|---|---|---|---|---|---|")
    for cid, k, s, b, r, t, d in (("C01", "YES (done)", "YES (done)", "YES (encoder cap)", "YES", "YES (GPU timestamps, device)", "LOW"),
                                  ("C02", "YES (lab)", "YES (lab)", "YES (lab model)", "YES", "NO (no decoder)", "MEDIUM: new lifting shader in PyroWave's pipeline"),
                                  ("C03", "YES (lab)", "YES (lab)", "YES (lab model)", "YES", "NO", "HIGH: new block-coefficient bitstream + shader; PyroWave's band/block layout does not map"),
                                  ("C04", "YES (lab)", "YES (lab)", "YES (lab model)", "YES", "NO on device (GPUJPEG is CUDA)", "HIGH: same as C03 plus a Vulkan IDCT port"),
                                  ("C05", "YES (lab)", "YES (lab)", "YES (lab model)", "YES", "NO", "LOW-MEDIUM: swap lifting constants/steps in dwt shaders; same passes"),
                                  ("C06", "YES (lab)", "YES (lab)", "YES (lab model)", "YES", "NO", "MEDIUM: wider apron in the tile shaders"),
                                  ("C07", "YES (lab)", "YES (lab)", "YES (lab model)", "YES", "NO", "HIGH: different bitstream (over-complete)"),
                                  ("C08", "NO (not run)", "NO", "UNKNOWN", "NO", "NO", "EXTREME: new codec"),
                                  ("C09", "YES (ffmpeg CPU)", "YES", "NO (bitrate control, not exact)", "YES", "NO on device", "EXTREME: FFmpeg Vulkan decoder into an Android client"),
                                  ("C10", "NO (video codec)", "NO", "NO", "NO", "YES (ALVR stage)", "NOT APPLICABLE (in use)")):
        A(f"| {cid} | {k} | {s} | {b} | {r} | {t} | {d} |")
    A("\nHIGH/EXTREME reasons: C03/C04/C07 need a new coefficient container and shaders outside PyroWave's band structure; C08 has no implementation at all; C09 would mean porting FFmpeg's in-progress Vulkan decoder into a NativeActivity client.\n")

    # ---------- 13
    A("---\n\n# 13. PROPOSED CONTROLLED BENCHMARK\n")
    A("Datasets: A Kodak (kodim04/07/08/19 tiled to 2560^2, done), B synthetic panel (done), C real lossless game imagery (NOT AVAILABLE: no VR game installed; only a SteamVR-void dump exists). Resolutions 1920..2560 (lab ran 1920 and 2560; the matrix ran all six for C01). Byte budgets 150 KB..550 KB + 416,667 B (all run in the lab under the bit-plane model; exact for C01 via its encoder). Metrics: PSNR-Y (numpy), SSIM-Y (numpy), PSNR-HVS (libvmaf, reference cap only), PSNR-HVS-M NOT AVAILABLE. Performance metrics: decode mean/p95/p99 exist for C01 and C10 on device only; CPU/GPU split, memory traffic, temporary memory and synchronization time UNKNOWN for every candidate except C01's GPU timestamps (decode, convert, submit->fence).\n")
    A("**Scope of the lab caps:** every lab cell applies its byte cap to ONE eye's LUMA plane (2560x2560 = 6.55 Msamples), not to the pipeline's stereo 4:4:4 frame (5120x2560x3 = 39.3 Msamples). The caps therefore mean ~6x more bits per sample than the same numbers in the offline PyroWave matrix (E104), which is why lab PSNR-Y values sit ~7 dB above the matrix's for the same frame. Comparisons BETWEEN transforms in the lab are at equal bytes and equal samples and are fair; lab absolutes are not comparable to the matrix or to the headset.\n")
    A("What this benchmark does NOT do: it does not run any candidate's decoder on the headset except C01; sizes for C02-C07 come from a packing model of PyroWave's bitstream applied to their coefficients, so a real entropy coder (or PyroWave's exact 4x2 layout) would move every size.\n")

    # ---------- 14
    A("---\n\n# 14. DECODER-FIRST RESULTS TABLE\n")
    A("Lab rows: cap applied to one eye's luma plane (see section 13); 'Mbps @ 90 Hz' for lab rows is the cap x 8 x 90 and would be ~6x higher for a stereo 4:4:4 frame at the same bits per sample.\n")
    A("| Candidate | Resolution | Bytes/Frame | Mbps @ 90 Hz | Quality | Decode Mean | Decode P95 | Decode P99 | CPU Time | GPU Time | Memory Traffic | Evidence Class |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    A("| C01 (device) | 1984x896 SBS (60 %) | ~556 KB (400 Mbps) | 400 | subjective good | fence 6.4 ms | 8.3 | 8.9 | UNKNOWN | 3.7 mean / 5.2 p99 | UNKNOWN | MEASURED (E101) |")
    A("| C01 (device) | 3328x1472 SBS 4:4:4 | 1,041,660 | 750 (at 90) | PSNR-Y 55.16 vs PC | 8.19 ms (GPU) | UNKNOWN | UNKNOWN | UNKNOWN | 6.23 best | UNKNOWN | MEASURED (E102) |")
    A("| C10 H.264 (device) | 3328x1472 | UNKNOWN | 600 @ 72 | subjective | 14.8-18.9 ms stage | UNKNOWN | UNKNOWN | NOT APPLICABLE | fixed fn | UNKNOWN | MEASURED (E103) |")
    A("| C10 HEVC (device) | 3328x1472 | UNKNOWN | 600 @ 72 | subjective | 82.0 ms stage | UNKNOWN | UNKNOWN | NOT APPLICABLE | fixed fn | UNKNOWN | MEASURED (E103) |")
    for t, cid in TRANSFORM_TO_CANDIDATE.items():
        if t in summ and summ[t]["psnr_y_kodak_mean"] is not None:
            A(f"| {cid} {t} (lab) | 2560x2560 per eye | 416,667 (model) | 300 | PSNR-Y {summ[t]['psnr_y_kodak_mean']:.2f} Kodak mean / {summ[t]['psnr_y_synth']:.2f} panel | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | DERIVED model (sec. 9) | MEASURED quality, no timing |")

    # ---------- 15
    A("\n---\n\n# 15. QUALITY-TARGET TABLE\n")
    A("Thresholds are defined on the lab's PSNR-Y (numpy, BT.709 luma, 2560^2 per eye) for the tiled kodim08 frame, the hardest source: A High = 24 dB, B Very High = 26 dB, C Near-Transparent = 30 dB. These are chosen from the measured range of this frame (its scores run ~18-27 dB across the caps), not from any candidate's convenience; they do not transfer to game content.\n")
    for label, thr in (("A — High Quality", 24.0), ("B — Very High Quality", 26.0), ("C — Near-Transparent", 30.0)):
        A(f"## Target {label} (PSNR-Y >= {thr} dB on kodak_kodim08)\n")
        A("| Candidate | Required Bytes/Frame | Mbps @ 90 Hz | Decode Time | Memory Traffic |\n|---|---:|---:|---:|---:|")
        for t, cid in TRANSFORM_TO_CANDIDATE.items():
            hit = bytes_for_quality(rd, t, "kodak_kodim08", thr)
            if hit:
                A(f"| {cid} {t} | {hit[0]:,} (smallest measured cap reaching {hit[1]:.2f} dB) | {hit[0] * 8 * 90 / 1e6:.0f} | UNKNOWN (C01: 3.7 ms GPU at 1984x896 only) | DERIVED sec. 9 |")
            elif t in summ:
                A(f"| {cid} {t} | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |")
        A("")

    # ---------- 16
    A("---\n\n# 16. PARETO FRONTIER\n")
    pts = []
    for t, cid in TRANSFORM_TO_CANDIDATE.items():
        if t not in summ or summ[t]["psnr_y_kodak_mean"] is None:
            continue
        pts.append({"name": f"{cid} {t}", "quality": summ[t]["psnr_y_kodak_mean"], "bytes": 416_667, "ops": ops_per_pixel(t), "passes": passes(t)})
    front, dominated = pareto4(pts)
    A("Axes: quality (lab PSNR-Y Kodak mean at 416,667 B, MEASURED), bytes (equal by construction), decode arithmetic (ops/pixel, DERIVED), memory passes (fragment-path count, DERIVED). Decode latency and memory traffic are model values, so nothing here is a confirmed Pareto point except C01's device measurement against C10.\n")
    A("### Confirmed Pareto Points\n")
    A("- C01 PyroWave on device dominates C10 H.264/HEVC on decode latency at equal or higher bitrate (MEASURED: 3.7-8 ms vs 14.8-82 ms, E101/E103); quality comparability with C10 is NO, so this is confirmed on latency only.\n")
    A("### Potential Pareto Points (lab quality x model cost)\n")
    for p in sorted(front, key=lambda p: -p["quality"]):
        A(f"- {p['name']}: PSNR-Y {p['quality']:.2f} dB, {p['ops']:.1f} ops/pixel, {p['passes']} passes")
    A("\n### Dominated Configurations\n")
    for p, d in dominated:
        A(f"- {p['name']} ({p['quality']:.2f} dB, {p['ops']:.1f} ops, {p['passes']} passes) dominated by {d['name']} ({d['quality']:.2f} dB, {d['ops']:.1f} ops, {d['passes']} passes)")
    A("")

    # ---------- 17
    A("---\n\n# 17. HEADSET-SIDE FEASIBILITY\n")
    feas = {"C01": ("Vulkan 1.3 compute or graphics", "runs (MEASURED)", "packet parse thread", "Adreno 740: fragment path forced; storage-on-AHB RGBA8", "coefficient images + output", "78 C plateau at 60 %/400 Mbps/90 Hz; status 3 at full panel", "fence 6-18 ms; no external semaphores (SYNC_FD only)", "AHB R8 unsupported -> convert pass", "E101, E102, memory notes", "HIGH"),
            "C02": ("Vulkan compute", "expected (subset of C01's ops)", "as C01", "as C01", "as C01 or less", "likely lower than C01 (fewer ops, passes) -- ESTIMATED", "must be written; no implementation", "as C01", "none", "LOW"),
            "C03": ("Vulkan compute", "expected", "parse", "one block per workgroup", "block in shared memory", "single pass -- ESTIMATED lower", "new bitstream + shader", "as C01", "none", "LOW"),
            "C04": ("Vulkan compute", "expected", "parse", "as C03", "as C03", "as C03", "port of a CUDA IDCT; no Vulkan reference", "as C01", "E004 (desktop only)", "LOW"),
            "C05": ("Vulkan", "expected", "as C01", "as C01", "as C01", "slightly lower than C01 -- ESTIMATED", "constant/step change in dwt shaders", "as C01", "none", "MEDIUM"),
            "C06": ("Vulkan", "expected", "as C01", "wider aprons", "as C01", "higher than C01 -- ESTIMATED", "shader rewrite", "as C01", "none", "LOW"),
            "C07": ("Vulkan", "expected", "as C01", "per level", "1.33x", "UNKNOWN", "new bitstream", "as C01", "none", "LOW"),
            "C08": ("Vulkan", "UNKNOWN", "UNKNOWN", "matrix per block", "basis", "UNKNOWN", "everything", "UNKNOWN", "none", "LOW"),
            "C09": ("Vulkan compute (FFmpeg)", "UNKNOWN", "Golomb parse", "UNKNOWN", "UNKNOWN", "UNKNOWN", "porting FFmpeg's decoder into the client", "UNKNOWN", "E009", "LOW"),
            "C10": ("MediaCodec", "runs (MEASURED)", "none", "video block", "surfaces", "coolest block (video zone 6 C below CPU)", "HEVC 8x slower than AVC", "MediaCodec latency", "E103", "HIGH")}
    for c in CANDIDATES:
        f = feas[c[0]]
        A(f"## {c[0]}\n\nRequired compute API: {f[0]}\nExpected mobile GPU compatibility: {f[1]}\nCPU requirements: {f[2]}\nGPU requirements: {f[3]}\nMemory requirements: {f[4]}\nExpected thermal implications: {f[5]}\nImplementation obstacles: {f[6]}\nKnown Android/XR constraints: {f[7]}\nEvidence: {f[8]}\nConfidence: {f[9]}\n")
    A("No desktop CUDA/Vulkan timing has been extrapolated to the headset anywhere in this report.\n")

    # ---------- 18
    A("---\n\n# 18. SHORTLIST FOR IMPLEMENTATION\n")
    sl = [("C05", "cheapest change to a working decoder: same passes, shift-only lifting", f"5/3 loses <= {(summ['cdf97']['psnr_y_kodak_mean'] - summ['cdf53']['psnr_y_kodak_mean']) if t_ok('cdf53') else float('nan'):.2f} dB (lab) and removes every multiply from the iDWT; on the Adreno this changes decode time by an amount that is UNKNOWN because the fence, not arithmetic, dominates", "pyrowave (MIT) shaders dwt_common.h / idwt.*", "new lifting constants and 2-step path in the dwt shaders; encoder side same", "arithmetic only (ESTIMATED small)", "fence-bound decoder shows no change", "A/B on device: GPU decode + fence with 9/7 vs 5/3 at the same cap"),
          ("C02", "fewest passes of any multi-level transform if levels are fused in-tile", f"Haar costs {(summ['cdf97']['psnr_y_kodak_mean'] - summ['haar']['psnr_y_kodak_mean']) if t_ok('haar') else float('nan'):.2f} dB (lab) at equal bytes; if a fused single-pass inverse cuts the fragment path's 20 render passes to a handful, the fence should fall", "none; write inside PyroWave's decoder", "new fused iDWT shader; encoder DWT trivial", "passes and synchronisation", "quality loss visible in text/UI (synthetic panel result)", "device timing of a fused Haar iDWT vs current 9/7 fragment path"),
          ("C04", "single-pass block decoder with the best block-transform quality in the lab", "a 16x16 or 32x32 IDCT in registers reconstructs in one dispatch; the quality gap to 9/7 is measured in section 14", "GPUJPEG IDCT (BSD-2) as reference for a Vulkan port", "new coefficient container + IDCT shader", "passes (1) and no inter-level sync", "block artefacts at low bpp; container work", "device timing of an IDCT-only decoder on the same frames"),
          ("C03", "add/sub only, single pass", "WHT trades bytes for zero multiplies; the lab shows how many bytes", "none", "as C04 with butterflies", "arithmetic and passes", "quality gap at equal bytes", "same as C04"),
          ("C01", "not a new codec: force PyroWave's compute path on the Adreno", "the driver check forces the fragment path (3 passes/level) 'for tiled mobile GPUs'; our fence data suggests passes, not ALU, cost the time; the compute path halves passes", "pyrowave `device_prefers_fragment_path`", "a build/runtime flag (PYROWAVE_FORCE_COMPUTE exists in pyrowave_android)", "half the intermediate traffic", "compute path may be slower or broken on Adreno (the author says not recommended)", "device A/B of the two paths at the same cap")]
    for cid, why, hyp, impl, work, adv, risk, exp in sl:
        A(f"## Candidate {cid}\n\nReason to test: {why}\nSpecific hypothesis: {hyp}\nOpen-source implementation: {impl}\nRequired integration work: {work}\nExpected decoder advantage: {adv}\nMain risk: {risk}\nExperiment needed to falsify hypothesis: {exp}\n")

    # ---------- 19
    A("---\n\n# 19. REJECTED / DEFERRED APPROACHES\n")
    A("| Candidate | Status | Reason | Evidence |\n|---|---|---|---|")
    A(f"| C06 Daubechies | DEFERRED | quality {summ['db4']['psnr_y_kodak_mean']:.2f} dB (db4) is within noise of 9/7 at ~2x the multiplies and wider aprons; no decoder-side gain | lab rd.csv, sec. 10 |" if t_ok("db4") else "| C06 | INSUFFICIENT EVIDENCE | lab not run | |")
    A(f"| C07 Laplacian | DEFERRED | over-complete: 4/3 coefficients and more traffic for {summ['lap']['psnr_y_kodak_mean']:.2f} dB | lab rd.csv, sec. 9 |" if t_ok("lap") else "| C07 | INSUFFICIENT EVIDENCE | | |")
    A("| C08 fixed PCA/KLT (spatial) | INSUFFICIENT EVIDENCE | not run; colour PCA measured only (section 20) | colour.csv |")
    A("| C09 VC-2 | DEFERRED | no timings, Vulkan decoder status unclear, EXTREME integration | E009 |")
    A("| C10 HEVC | REJECTED | 82 ms decode on device | E103 |")
    A("| DSP/HVX offload | REJECTED | cDSP not reachable from an app (no node; aDSP DAC-blocked) | tools/dspprobe/README.md |")

    # ---------- 20
    A("\n---\n\n# 20. NEW MATHEMATICAL DIRECTIONS\n")
    A("Name: Colour decorrelation by offline PCA basis (measured)\nMathematical basis: eigenvectors of the RGB covariance per source\nWhy potentially relevant: entropy after transform vs YCbCr/YCoCg (colour.csv)\nExpected decoder complexity: 9 mult + 6 add per pixel (3x3 matrix)\nExpected bandwidth behavior: see section 21 table\nExisting implementation: none needed\nEvidence: MEASURED (colour.csv)\nRecommended action: adopt only if H_sum falls below YCoCg's and the 3x3 cost is accepted\n")
    A("Name: Encoder-side preconditioning (measured)\nMathematical basis: I' = F_theta(I), C = T(I'), decoder unchanged\nWhy potentially relevant: fewer coefficient bits at the same quantiser, zero decoder cost\nExpected decoder complexity: 0 extra\nExpected bandwidth behavior: precond.csv (bytes at fixed delta) -- HYPOTHESIZED to save 5-20 %\nExisting implementation: cv2 filters\nEvidence: MEASURED (precond.csv)\nRecommended action: see section 21 values\n")
    A("Name: Multi-level fusion of short wavelets in one tile (HYPOTHESIZED)\nMathematical basis: 2-tap (Haar) or 4-tap (5/3) support lets several inverse levels complete inside a 32x32 tile with aprons\nWhy potentially relevant: passes and barriers, which the device data says are the cost\nExpected decoder complexity: same arithmetic, 1 pass\nExpected bandwidth behavior: intermediate traffic -> 0\nExisting implementation: none\nEvidence: HYPOTHESIZED\nRecommended action: prototype inside PyroWave's decoder\n")

    # ---------- 21 (unknowns) with measured side tables
    A("---\n\n# 21. CRITICAL UNKNOWNS\n")
    A("Supporting MEASURED tables from the lab (sections 10-14 of the research prompt):\n")
    if sp:
        A("Sparsity P(eps) and PSNR after thresholding, kodak_kodim08 (MEASURED):\n\n| transform | eps=2: sparsity / psnr | eps=8 | eps=32 |\n|---|---|---|---|")
        for t in sorted({r["transform"] for r in sp}):
            cells = []
            for e in ("2", "8", "32"):
                r = next((r for r in sp if r["transform"] == t and r["source"] == "kodak_kodim08" and r["eps"] == e), None)
                cells.append(f"{float(r['sparsity']):.3f} / {float(r['psnr_y']):.1f}" if r else "UNKNOWN")
            A(f"| {t} | " + " | ".join(cells) + " |")
    if pr:
        A("\nPrecision (MEASURED, PSNR of the fp16/int16 reconstruction vs the fp32 one, kodak_kodim08):\n\n| transform | fp16 storage | fp16 math | int16 |\n|---|---|---|---|")
        for t in sorted({r["transform"] for r in pr}):
            cells = []
            for p in ("fp16_storage", "fp16_math", "int16"):
                r = next((r for r in pr if r["transform"] == t and r["source"] == "kodak_kodim08" and r["precision"] == p), None)
                cells.append(f"{float(r['psnr_vs_fp32']):.1f} dB" if r else "UNKNOWN")
            A(f"| {t} | " + " | ".join(cells) + " |")
    if co:
        A("\nColour (MEASURED): channel correlations and zero-order entropy sums (bins of 1.0) per space. YCbCr709's chroma is scaled by 1/1.8556 and 1/1.5748, which lowers its raw entropy by log2 of those factors (1.55 bits); the corrected column adds that back (DERIVED) so the spaces are compared at equal scale:\n\n| source | rho_RG / rho_RB / rho_GB | RGB H_sum | YCbCr709 raw | YCbCr709 scale-corrected | YCoCg | Y,R-G,B-G | PCA_offline |\n|---|---|---|---|---|---|---|---|")
        import math
        corr = math.log2(1.8556) + math.log2(1.5748)
        for src in sorted({r["source"] for r in co}):
            rr = {r["space"]: r for r in co if r["source"] == src}
            any_r = next(iter(rr.values()))
            ycc = float(rr["YCbCr709"]["H_sum"]) if "YCbCr709" in rr else None
            A(f"| {src} | {any_r['rho_RG']} / {any_r['rho_RB']} / {any_r['rho_GB']} | {float(rr['RGB']['H_sum']):.2f} | {ycc:.2f} | {ycc + corr:.2f} | " + " | ".join(f"{float(rr[s]['H_sum']):.2f}" if s in rr else "UNKNOWN" for s in ("YCoCg", "Y_R-G_B-G", "PCA_offline")) + " |")
    if pc:
        A("\nPreconditioning (MEASURED, cdf97 at the delta that met 416,667 B unfiltered):\n\n| source | prefilter | bytes at fixed delta | PSNR vs original | PSNR vs original at the cap |\n|---|---|---:|---:|---:|")
        for r in pc:
            if r["transform"] == "cdf97":
                A(f"| {r['source']} | {r['prefilter']} | {int(r['bytes_bitplane']):,} | {float(r['psnr_vs_original']):.2f} | {float(r['psnr_vs_original_at_cap']):.2f} |")
    if bv:
        A("\nMarginal value of precision per band, cdf97 at 416,667 B (MEASURED, dPSNR per extra KB, kodak_kodim08):\n\n| band | dPSNR/KB |\n|---|---:|")
        for r in sorted([r for r in bv if r["source"] == "kodak_kodim08" and r["dpsnr_per_kb"]], key=lambda r: -float(r["dpsnr_per_kb"]))[:8]:
            A(f"| {r['band']} | {float(r['dpsnr_per_kb']):.4f} |")
    A("")
    for i, (u, why, meas, mini) in enumerate((
            ("Decode time of any non-PyroWave transform on the Adreno 740.", "The entire decoder-cost side of the Pareto analysis is a model until one exists.", "GPU timestamps of a candidate inverse inside PyroWave's decoder on device.", "Swap 9/7 lifting for 5/3 in the dwt shaders, rebuild libpyrowave for Android, A/B at 416,667 B on the existing harness."),
            ("Whether PyroWave's compute path beats its fragment path on the Adreno.", "It halves the passes the device data says cost the time.", "PYROWAVE_FORCE_COMPUTE A/B on device.", "Env/property flag in pyroclient, one worn cell each."),
            ("How much of the submit->fence time is contention with the runtime compositor rather than decode.", "If it is contention, no transform change helps; scheduling does.", "Decode on a dedicated queue vs the runtime's, with GPU timestamps at queue submit and at completion.", "Queue-family experiment in libpyroclient."),
            ("Real sizes under PyroWave's exact 4x2 packing (and under an entropy coder) for C02-C07.", "Every non-C01 byte figure is a model.", "Encode the lab's quantised coefficients with pyrowave's packer, or with rANS.", "Export quantised bands and feed the C encoder's packetize path."),
            ("Behaviour on real game content and stereo pairs.", "All sources are left=right and either tiled Kodak or synthetic.", "Lossless captures from a game via the pre-encode tap.", "Install one VR title; tap 10 frames.")), 1):
        A(f"{i}.\nUNKNOWN: {u}\nWHY IT MATTERS: {why}\nWHAT MEASUREMENT RESOLVES IT: {meas}\nMINIMUM EXPERIMENT REQUIRED: {mini}\n")

    # ---------- 22
    A("---\n\n# 22. NEXT EXPERIMENTS\n")
    for eid, q, h, inp, ctrl, var, meas, fals, eff in (
            ("X01", "Does the compute path beat the fragment path on the Adreno 740?", "passes, not ALU, set the fence; compute path halves passes", "live stream at X60-400-90", "fragment path (today)", "PYROWAVE_FORCE_COMPUTE", "GPU decode, submit->fence p50/p95/p99, fps, thermal", "fence not lower by > 1 ms, or corruption", "hours (flag + rebuild)"),
            ("X02", "Does 5/3 lifting reduce device decode time at equal bytes?", "no multiplies -> lower ALU time; fence unchanged", "same", "9/7", "5/3 shaders", "same + PSNR-Y vs encoder input", "GPU decode not lower by > 0.5 ms", "1-2 days (shaders both ends)"),
            ("X03", "Does a fused multi-level Haar iDWT cut passes enough to move the fence?", "one pass per tile beats 20 render passes", "same", "9/7 fragment", "fused Haar decoder shader", "same", "fence not lower, or quality loss > lab-predicted", "days"),
            ("X04", "How many bytes do C02-C07 really need under PyroWave's exact packer?", "the lab model is within 10 % of the real packer", "lab quantised bands", "C01 via its encoder", "transform", "actual bytes from pyrowave packetize", "model error > 10 %", "1 day"),
            ("X05", "Is decode-queue contention with the compositor a large part of the fence?", "a dedicated queue lowers fence variance", "live stream", "shared queue", "queue family", "fence p95/p99 distribution", "no change in p95", "1 day")):
        A(f"EXPERIMENT ID: {eid}\nQUESTION: {q}\nHYPOTHESIS: {h}\nINPUT: {inp}\nCONTROL: {ctrl}\nVARIABLE: {var}\nMEASUREMENTS: {meas}\nFALSIFICATION CONDITION: {fals}\nESTIMATED IMPLEMENTATION EFFORT: {eff}\n")

    # ---------- 23
    A("---\n\n# 23. MACHINE-READABLE SUMMARY\n")
    cands_json = []
    for c in CANDIDATES:
        cid = c[0]
        ts = [t for t, k in TRANSFORM_TO_CANDIDATE.items() if k == cid and t in summ]
        best = max(ts, key=lambda t: summ[t]["psnr_y_kodak_mean"]) if ts else None
        cands_json.append({"id": cid, "name": c[1], "transform": c[2], "repository": c[4], "license": c[5],
                           "decode_ms_measured": 3.7 if cid == "C01" else (14.8 if cid == "C10" else None),
                           "decode_ms_derived": None,
                           "quality_metric": "psnr_y_lab_kodak_mean_2560_416667B" if best else "",
                           "quality_value": round(summ[best]["psnr_y_kodak_mean"], 2) if best else None,
                           "bytes_per_frame": 416667 if best else None,
                           "cpu_gpu_parallelism": "same-frame possible per block/band" if cid not in ("C09", "C10") else "UNKNOWN",
                           "memory_traffic": None,
                           "evidence_class": c[12], "confidence": feas[cid][9],
                           "status": "baseline" if cid == "C01" else ("rejected" if cid == "C10" else ("shortlist" if cid in ("C02", "C03", "C04", "C05") else "deferred"))})
    js = {"baseline": {"codec": "PyroWave", "transform": "CDF 9/7 irreversible, 5 levels, raw bit-planes", "resolution_per_eye": "2560x2560 (research) / 2131x2304 (live)",
                       "fps": 90, "bitrate_mbps": 400, "decode_ms": 3.7, "quality_metric": "psnr_y_dashboard_600mbps", "quality_value": 57.45},
          "candidates": cands_json,
          "shortlist": ["C05", "C02", "C04", "C03", "C01 compute path"],
          "pareto_confirmed": ["C01 vs C10 on device decode latency"],
          "pareto_potential": [p["name"] for p in front],
          "critical_unknowns": ["mobile decode timing for any non-C01 transform", "compute vs fragment path on Adreno", "fence contention share", "real packer sizes for C02-C07", "game content and stereo"],
          "next_experiments": ["X01", "X02", "X03", "X04", "X05"]}
    A("```json\n" + json.dumps(js, indent=2) + "\n```\n")

    # ---------- 24
    A("---\n\n# 24. RESEARCH INTEGRITY RULES\n")
    A(INTEGRITY_RULES + "\n")

    report = "\n".join(L)
    (out_dir / "REPORT.md").write_text(report, encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(js, indent=2), encoding="utf-8")
    with open(out_dir / "evidence.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["evidence_id", "candidate", "source", "hardware", "resolution", "input", "quality_setting", "size_or_bitrate", "encode_time", "decode_time", "quality_metric", "quality_value", "what_was_timed", "evidence_class"])
        for e in EVIDENCE:
            w.writerow(e)
    return report


def lint(report_path):
    text = Path(report_path).read_text(encoding="utf-8")
    heads = [l for l in text.splitlines() if l.startswith("# ")]
    nums = [int(h.split(".")[0][2:]) for h in heads if h[2:].split(".")[0].isdigit()]
    assert nums == list(range(0, 25)), f"headings {nums}"
    js = json.loads(text.split("```json\n")[1].split("\n```")[0])
    ids = {c[0] for c in CANDIDATES}
    assert {c["id"] for c in js["candidates"]} == ids
    return True


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--lab", required=True); p.add_argument("--matrix", required=True); p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    render(a.lab, a.matrix, a.out)
    lint(Path(a.out) / "REPORT.md")
    print("ok")


if __name__ == "__main__":
    main()
