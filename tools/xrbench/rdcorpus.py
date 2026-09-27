"""Experiment 4 real-content corpus scoring: PyroWave CDF 9/7 vs Haar on lossless multi-frame
clips dumped from the encoder's input (1984x896 side-by-side 4:4:4 full range at the operating
point), at the frame's native size. Per-frame rate-distortion at a set of byte caps, per-clip
aggregates and temporal stability, and a PSNR-Y-matched control per clip. Classes are reported
separately; aggregation across classes is a second, explicit step."""
import csv
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

from . import rdmatrix
from . import exp2

CAPS = (150_000, 200_000, 250_000, 300_000, 350_000, 416_667, 500_000, 600_000)
WAVELETS = ("97", "haar")


def read_y4m_header(path):
    with open(path, "rb") as f:
        header = f.readline().decode("ascii", "replace")
    w = int(re.search(r" W(\d+)", header).group(1))
    h = int(re.search(r" H(\d+)", header).group(1))
    if "C444" not in header:
        raise ValueError(f"{path}: expected C444")
    return header, w, h


def clip_frames(path):
    """Number of FRAMEs in a 4:4:4 8-bit multi-frame y4m (from the file size)."""
    header, w, h = read_y4m_header(path)
    frame_bytes = len(b"FRAME\n") + 3 * w * h
    size = os.path.getsize(path) - len(header.encode("ascii"))
    return size // frame_bytes


def extract_frame(path, index, out):
    """Write frame `index` of a multi-frame y4m as a single-frame y4m (same header)."""
    header, w, h = read_y4m_header(path)
    frame_bytes = len(b"FRAME\n") + 3 * w * h
    with open(path, "rb") as f, open(out, "wb") as o:
        f.seek(len(header.encode("ascii")) + index * frame_bytes)
        data = f.read(frame_bytes)
        if not data.startswith(b"FRAME"):
            raise ValueError(f"{path}: frame {index} has no FRAME tag")
        o.write(header.encode("ascii"))
        o.write(data)
    return out


def mbps(cap_bytes, fps):
    return cap_bytes * 8 * fps / 1e6


def encode_decode_score(frame_y4m, cap, wavelet, tools, workdir):
    """One cell at the frame's native size: encode under `cap`, decode, score vs the frame."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    wave = workdir / "cell.wave"
    decoded = workdir / "decoded.y4m"
    env = dict(os.environ, PYROWAVE_WAVELET=wavelet)
    t0 = time.perf_counter()
    subprocess.run([tools["encode"], str(frame_y4m), str(wave), str(cap)], check=True, capture_output=True, env=env)
    t1 = time.perf_counter()
    subprocess.run([tools["decode"], str(wave), str(decoded)], check=True, capture_output=True, env=env)
    t2 = time.perf_counter()
    scores = rdmatrix.score(tools["ffmpeg"], decoded, frame_y4m, workdir)
    return {"actual_bytes": rdmatrix.bytes_per_frame(wave) if hasattr(rdmatrix, "bytes_per_frame") else os.path.getsize(wave) - 44,
            "encode_s": round(t1 - t0, 3), "decode_s": round(t2 - t1, 3), **scores}


COLUMNS = ("class", "clip", "frame", "wavelet", "cap_bytes", "actual_bytes", "mbps_90hz", "psnr_y", "psnr_hvs", "ssim", "vmaf",
           "encode_s", "decode_s")


def score_clip(clip_path, klass, tools, workdir, caps=CAPS, every=10, fps=90.0, log=print):
    """Rows for every `every`-th frame of the clip at every cap and wavelet."""
    n = clip_frames(clip_path)
    Path(workdir).mkdir(parents=True, exist_ok=True)
    rows = []
    for fi in range(0, n, every):
        frame = extract_frame(clip_path, fi, Path(workdir) / "frame.y4m")
        for cap in caps:
            for wv in WAVELETS:
                r = encode_decode_score(frame, cap, wv, tools, Path(workdir) / "cell")
                row = {"class": klass, "clip": Path(clip_path).stem, "frame": fi, "wavelet": wv, "cap_bytes": cap,
                       "actual_bytes": r["actual_bytes"], "mbps_90hz": round(mbps(cap, fps), 1),
                       "psnr_y": r.get("psnr_y"), "psnr_hvs": r.get("psnr_hvs"), "ssim": r.get("ssim_all", r.get("ssim")),
                       "vmaf": r.get("vmaf"), "encode_s": r["encode_s"], "decode_s": r["decode_s"]}
                rows.append(row)
                log(f"{klass} {Path(clip_path).stem} f{fi} {wv} {cap}: {row['actual_bytes']} B psnr_y {row['psnr_y']} hvs {row['psnr_hvs']}")
    return rows


def match_clip(clip_path, klass, tools, workdir, frame_index=None, target_cap=416_667, log=print):
    """PSNR-Y-matched control on one frame of the clip: Haar's cap bisected to reach 9/7's PSNR-Y
    at `target_cap` (never called equal quality)."""
    n = clip_frames(clip_path)
    Path(workdir).mkdir(parents=True, exist_ok=True)
    fi = n // 2 if frame_index is None else frame_index
    frame = extract_frame(clip_path, fi, Path(workdir) / "frame.y4m")
    ref = encode_decode_score(frame, target_cap, "97", tools, Path(workdir) / "cell")
    cache = {}
    def q(cap):
        if cap not in cache:
            cache[cap] = encode_decode_score(frame, cap, "haar", tools, Path(workdir) / "cell")
            log(f"  {klass} {Path(clip_path).stem} f{fi} haar cap {cap}: psnr_y {cache[cap]['psnr_y']} (target {ref['psnr_y']})")
        return cache[cap]["psnr_y"]
    cap, qual, evals = exp2.bisect_cap(q, ref["psnr_y"], target_cap, 1_200_000, tol_db=0.02)
    m = cache[cap]
    return {"class": klass, "clip": Path(clip_path).stem, "frame": fi, "bytes97": ref["actual_bytes"], "psnr_y97": ref["psnr_y"],
            "bytes_haar": m["actual_bytes"], "psnr_y_haar": m["psnr_y"], "extra_pct": (m["actual_bytes"] - ref["actual_bytes"]) / ref["actual_bytes"] * 100.0,
            "reached": qual >= ref["psnr_y"], "psnr_hvs97": ref.get("psnr_hvs"), "psnr_hvs_haar": m.get("psnr_hvs"),
            "ssim97": ref.get("ssim_all", ref.get("ssim")), "ssim_haar": m.get("ssim_all", m.get("ssim")), "vmaf97": ref.get("vmaf"), "vmaf_haar": m.get("vmaf")}


def summarise(rows):
    """Per (class, clip, wavelet, cap): mean and temporal spread of PSNR-Y across the scored
    frames, mean HVS/SSIM/VMAF; then per class the 9/7 - Haar delta at each cap."""
    groups = {}
    for r in rows:
        if r["psnr_y"] is None:
            continue
        groups.setdefault((r["class"], r["clip"], r["wavelet"], r["cap_bytes"]), []).append(r)
    out = []
    for (k, c, wv, cap), rs in sorted(groups.items()):
        ys = [r["psnr_y"] for r in rs]
        out.append({"class": k, "clip": c, "wavelet": wv, "cap_bytes": cap, "frames": len(rs),
                    "psnr_y_mean": statistics.mean(ys), "psnr_y_std": statistics.pstdev(ys) if len(ys) > 1 else 0.0,
                    "psnr_y_min": min(ys), "psnr_y_max": max(ys),
                    "psnr_hvs_mean": statistics.mean(r["psnr_hvs"] for r in rs if r["psnr_hvs"] is not None) if any(r["psnr_hvs"] is not None for r in rs) else None,
                    "ssim_mean": statistics.mean(r["ssim"] for r in rs if r["ssim"] is not None) if any(r["ssim"] is not None for r in rs) else None,
                    "vmaf_mean": statistics.mean(r["vmaf"] for r in rs if r["vmaf"] is not None) if any(r["vmaf"] is not None for r in rs) else None,
                    "bytes_mean": statistics.mean(r["actual_bytes"] for r in rs)})
    return out


def class_deltas(summary):
    """Per class and cap: mean over clips of (Haar - 9/7) PSNR-Y, HVS, SSIM at equal bytes."""
    by = {}
    for s in summary:
        by.setdefault((s["class"], s["clip"], s["cap_bytes"]), {})[s["wavelet"]] = s
    acc = {}
    for (k, c, cap), d in by.items():
        if "97" in d and "haar" in d:
            e = acc.setdefault((k, cap), {"d_psnr_y": [], "d_hvs": [], "d_ssim": [], "clips": 0})
            e["d_psnr_y"].append(d["haar"]["psnr_y_mean"] - d["97"]["psnr_y_mean"])
            if d["haar"]["psnr_hvs_mean"] is not None and d["97"]["psnr_hvs_mean"] is not None:
                e["d_hvs"].append(d["haar"]["psnr_hvs_mean"] - d["97"]["psnr_hvs_mean"])
            if d["haar"]["ssim_mean"] is not None and d["97"]["ssim_mean"] is not None:
                e["d_ssim"].append(d["haar"]["ssim_mean"] - d["97"]["ssim_mean"])
            e["clips"] += 1
    return {f"{k}@{cap}": {"class": k, "cap_bytes": cap, "clips": e["clips"],
                           "d_psnr_y": statistics.mean(e["d_psnr_y"]) if e["d_psnr_y"] else None,
                           "d_hvs": statistics.mean(e["d_hvs"]) if e["d_hvs"] else None,
                           "d_ssim": statistics.mean(e["d_ssim"]) if e["d_ssim"] else None}
            for (k, cap), e in sorted(acc.items())}


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--corpus", required=True, help="root with <class>/<clip>.y4m")
    p.add_argument("--tools", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--every", type=int, default=10)
    p.add_argument("--work", default=None)
    p.add_argument("--index", default=None, help="Experiment 5: frozen index.csv; runs analyze_corpus over its accepted clips")
    a = p.parse_args(argv)
    if a.index:
        tools_dir = Path(a.tools)
        tools = {"encode": str(tools_dir / "pyrowave-encode.exe"), "decode": str(tools_dir / "pyrowave-decode.exe"), "ffmpeg": "ffmpeg"}
        analyze_corpus(a.corpus, a.index, tools, a.out, every=a.every, log=lambda s: print(s, flush=True))
        return
    tools_dir = Path(a.tools)
    tools = {"encode": str(tools_dir / "pyrowave-encode.exe"), "decode": str(tools_dir / "pyrowave-decode.exe"), "ffmpeg": "ffmpeg"}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    work = Path(a.work or out / "work")
    rows, matches = [], []
    for klass_dir in sorted(Path(a.corpus).iterdir()):
        if not klass_dir.is_dir():
            continue
        for clip in sorted(klass_dir.glob("*.y4m")):
            print(f"== {klass_dir.name} / {clip.name}: {clip_frames(clip)} frames", flush=True)
            rows += score_clip(clip, klass_dir.name, tools, work, every=a.every, log=lambda s: print(s, flush=True))
            matches.append(match_clip(clip, klass_dir.name, tools, work, log=lambda s: print(s, flush=True)))
            with open(out / "frames.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=COLUMNS); w.writeheader(); w.writerows(rows)
            (out / "match.json").write_text(json.dumps(matches, indent=1))
    summary = summarise(rows)
    (out / "summary.json").write_text(json.dumps({"clips": summary, "class_deltas": class_deltas(summary), "match": matches}, indent=1))
    print("done", flush=True)




# ---- Experiment 5: temporal classification, gates, frame-level matches, descriptors, ROI --------

import numpy as np

CLASSES = ("A_gameplay", "B_rotation", "C_translation", "D_hifreq", "E_foliage", "F_particles", "G_hud_text", "H_dark_gradient", "X_other")
CLASS_MINIMUM = {"A_gameplay": 4, "B_rotation": 2, "C_translation": 2, "D_hifreq": 3, "E_foliage": 3, "F_particles": 3, "G_hud_text": 3, "H_dark_gradient": 3, "X_other": 0}
# motion intrinsic to the class: these classes reject clips below the stated temporal class
MOTION_REQUIRED = {"B_rotation": ("ACTIVE", "HIGH_MOTION"), "C_translation": ("ACTIVE", "HIGH_MOTION"),
                   "F_particles": ("LOW_MOTION", "ACTIVE", "HIGH_MOTION"), "A_gameplay": ("LOW_MOTION", "ACTIVE", "HIGH_MOTION")}
TEMPORAL_THRESHOLDS = {"static_identical_frac": 0.95, "static_diff": 0.25, "low_diff": 1.0, "active_diff": 4.0}   # frozen
CAPS5 = (150_000, 200_000, 250_000, 300_000, 350_000, 416_667, 500_000, 600_000, 700_000, 800_000, 1_000_000)
GATE_BANDS = ((15.0, "STRONG PASS"), (25.0, "PASS"), (50.0, "CONDITIONAL"))


def read_frame_luma(path, index):
    header, w, h = read_y4m_header(path)
    frame_bytes = len(b"FRAME\n") + 3 * w * h
    with open(path, "rb") as f:
        f.seek(len(header.encode("ascii")) + index * frame_bytes + len(b"FRAME\n"))
        return np.frombuffer(f.read(w * h), np.uint8).reshape(h, w)


def temporal_class(mean_diff, identical_frac, t=TEMPORAL_THRESHOLDS):
    if identical_frac >= t["static_identical_frac"] or mean_diff < t["static_diff"]:
        return "STATIC"
    if mean_diff < t["low_diff"]:
        return "LOW_MOTION"
    if mean_diff < t["active_diff"]:
        return "ACTIVE"
    return "HIGH_MOTION"


def temporal_stats(clip_path, stride=10):
    """Consecutive-frame luma statistics for every frame of the clip (MEASURED, deterministic):
    mean |diff| per consecutive pair, the fraction of bit-identical consecutive pairs, the
    per-pair series (min/median/max) and the mean |diff| between frames `stride` apart."""
    n = clip_frames(clip_path)
    prev = read_frame_luma(clip_path, 0).astype(np.int16)
    diffs, identical = [], 0
    lumas = {0: prev}
    for i in range(1, n):
        cur = read_frame_luma(clip_path, i).astype(np.int16)
        d = float(np.mean(np.abs(cur - prev)))
        diffs.append(d)
        if d == 0.0:
            identical += 1
        if i % stride == 0:
            lumas[i] = cur
        prev = cur
    strided = [float(np.mean(np.abs(lumas[i] - lumas[i - stride]))) for i in lumas if i - stride in lumas]
    mean_diff = float(np.mean(diffs)) if diffs else 0.0
    frac = identical / len(diffs) if diffs else 1.0
    return {"frames": n, "mean_consecutive_diff": mean_diff, "identical_consecutive_frac": frac,
            "pair_min": min(diffs) if diffs else 0.0, "pair_median": float(np.median(diffs)) if diffs else 0.0,
            "pair_max": max(diffs) if diffs else 0.0, "mean_stride_diff": float(np.mean(strided)) if strided else 0.0,
            "temporal_class": temporal_class(mean_diff, frac)}


def clip_accepted(klass, tclass):
    """Reject only when motion is intrinsic to the class (the project's rule)."""
    need = MOTION_REQUIRED.get(klass)
    return True if need is None else tclass in need


def gate_for(extra_pct):
    """Haar RD gate classification, thresholds frozen: <=15 STRONG PASS, <=25 PASS, <=50 CONDITIONAL,
    >50 FAIL, None/unreached = UNKNOWN."""
    if extra_pct is None or (isinstance(extra_pct, float) and extra_pct != extra_pct):
        return "UNKNOWN"
    for limit, name in GATE_BANDS:
        if extra_pct <= limit:
            return name
    return "FAIL"


def match_frame(frame_y4m, tools, workdir, target_cap=416_667, hi=1_200_000, hi_max=2_000_000, log=print):
    """Haar bytes needed to reach 9/7's PSNR-Y at `target_cap` on ONE frame; extends the search
    bound once to `hi_max`; an unreached target is reported as bound=True (">BOUND"), never clipped."""
    ref = encode_decode_score(frame_y4m, target_cap, "97", tools, Path(workdir) / "cell")
    cache = {}
    def q(cap):
        if cap not in cache:
            cache[cap] = encode_decode_score(frame_y4m, cap, "haar", tools, Path(workdir) / "cell")
        return cache[cap]["psnr_y"]
    cap, qual, evals = exp2.bisect_cap(q, ref["psnr_y"], target_cap, hi, tol_db=0.02)
    if qual < ref["psnr_y"] and hi < hi_max:
        cap, qual, evals2 = exp2.bisect_cap(q, ref["psnr_y"], hi, hi_max, tol_db=0.02)
        evals += evals2
    m = cache[cap]
    reached = qual >= ref["psnr_y"]
    return {"bytes97": ref["actual_bytes"], "psnr_y97": ref["psnr_y"], "bytes_haar": m["actual_bytes"], "psnr_y_haar": m["psnr_y"],
            "extra_bytes": m["actual_bytes"] - ref["actual_bytes"], "extra_pct": (m["actual_bytes"] - ref["actual_bytes"]) / ref["actual_bytes"] * 100.0,
            "reached": reached, "bound": not reached, "evaluations": len(evals),
            "psnr_hvs97": ref.get("psnr_hvs"), "psnr_hvs_haar": m.get("psnr_hvs"), "ssim97": ref.get("ssim_all"), "ssim_haar": m.get("ssim_all"),
            "vmaf97": ref.get("vmaf"), "vmaf_haar": m.get("vmaf")}


def overhead_distribution(rows):
    """min / median / mean / p90 / p95 / max of per-frame extra_pct (bound rows counted as +inf,
    reported separately), and the frame producing the maximum."""
    vals = [(r["extra_pct"] if not r.get("bound") else float("inf"), r.get("frame")) for r in rows]
    if not vals:
        return None
    finite = [v for v, _ in vals if v != float("inf")]
    arr = np.array(finite) if finite else np.array([])
    worst = max(vals, key=lambda x: x[0])
    out = {"n": len(vals), "n_bound": sum(1 for v, _ in vals if v == float("inf")),
           "min": float(arr.min()) if finite else None, "median": float(np.median(arr)) if finite else None,
           "mean": float(arr.mean()) if finite else None, "p90": float(np.percentile(arr, 90)) if finite else None,
           "p95": float(np.percentile(arr, 95)) if len(finite) >= 9 else None,
           "max": (float(worst[0]) if worst[0] != float("inf") else None), "max_frame": worst[1], "max_is_bound": worst[0] == float("inf")}
    return out


def source_descriptors(luma, prev_luma=None):
    """Deterministic, descriptive source statistics of one luma plane (before compression)."""
    x = luma.astype(np.float32)
    hist = np.bincount(luma.ravel(), minlength=256).astype(np.float64)
    p = hist / hist.sum()
    p = p[p > 0]
    entropy = float(-(p * np.log2(p)).sum())
    gx = np.zeros_like(x); gy = np.zeros_like(x)
    gx[:, 1:-1] = (x[:, 2:] - x[:, :-2]) * 0.5
    gy[1:-1, :] = (x[2:, :] - x[:-2, :]) * 0.5
    grad = np.hypot(gx, gy)
    h, w = x.shape
    hb, wb = (h // 8) * 8, (w // 8) * 8
    blocks = x[:hb, :wb].reshape(hb // 8, 8, wb // 8, 8).transpose(0, 2, 1, 3).reshape(-1, 64)
    var = blocks.var(axis=1)
    f = np.fft.rfft2(x - x.mean())
    power = np.abs(f) ** 2
    fy = np.fft.fftfreq(h)[:, None]; fx = np.fft.rfftfreq(w)[None, :]
    radius = np.sqrt(fy ** 2 + fx ** 2)
    hf = float(power[radius > 0.25].sum() / power.sum()) if power.sum() > 0 else 0.0
    return {"luma_entropy_bits": entropy, "gradient_energy": float(grad.mean()), "edge_density": float((grad > 32).mean()),
            "temporal_diff": float(np.mean(np.abs(x - prev_luma.astype(np.float32)))) if prev_luma is not None else None,
            "hf_energy_frac": hf, "local_variance": float(var.mean()), "flat_frac": float((var < 4).mean()), "mean_luma": float(x.mean())}


def roi_psnr(ref_luma, dec_luma, box):
    """PSNR-Y inside and outside an (x, y, w, h) box."""
    x, y, w, h = box
    mask = np.zeros(ref_luma.shape, bool); mask[y:y + h, x:x + w] = True
    d = (ref_luma.astype(np.float64) - dec_luma.astype(np.float64)) ** 2
    def psnr(m):
        mse = d[m].mean() if m.any() else 0.0
        return float(10 * np.log10(255.0 ** 2 / mse)) if mse > 0 else float("inf")
    return {"inside": psnr(mask), "outside": psnr(~mask), "box": list(box)}


def spearman(xs, ys):
    """Spearman rank correlation, exploratory only; None below 5 pairs."""
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None and np.isfinite(x) and np.isfinite(y)]
    if len(pairs) < 5:
        return None
    a = np.array([p[0] for p in pairs]); b = np.array([p[1] for p in pairs])
    ra = a.argsort().argsort().astype(float); rb = b.argsort().argsort().astype(float)
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def analyze_corpus(corpus_root, index_csv, tools, out_dir, every=10, log=print):
    """Experiment 5 analysis over the accepted clips of a frozen index: RD sweep rows
    (`frames.csv`, `rd_summary.json`), per-frame matched-PSNR-Y rows with source descriptors
    (`frame_matches.json`) and ROI rows where the index has a box (`roi.json`)."""
    import csv as _csv
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    index = list(_csv.DictReader(open(index_csv, newline="", encoding="utf-8")))
    rows, matches, rois = [], [], []
    for r in index:
        if not r.get("clip") or str(r.get("accepted")) != "True":
            continue
        clip = Path(r["path"])
        klass = r["class"]
        log(f"== {klass} / {clip.name}")
        rows += score_clip(clip, klass, tools, work, caps=CAPS5, every=every, log=log)
        n = clip_frames(clip)
        prev = None
        for fi in range(0, n, every):
            frame = extract_frame(clip, fi, work / "frame.y4m")
            luma = read_frame_luma(clip, fi)
            desc = source_descriptors(luma, prev)
            prev = luma
            m = match_frame(frame, tools, work, log=log)
            matches.append({"class": klass, "clip": r["clip"], "frame": fi, **m, **desc})
            extra = ">BOUND" if m["bound"] else f"{m['extra_pct']:+.1f} %"
            log(f"  match f{fi}: {extra}")
            (out / "frame_matches.json").write_text(json.dumps(matches, indent=1))
        if r.get("roi"):
            box = tuple(int(v) for v in r["roi"].split())
            fi = n // 2
            frame = extract_frame(clip, fi, work / "frame.y4m")
            ref_luma = read_frame_luma(clip, fi)
            for wv in WAVELETS:
                for cap in (416_667,):
                    encode_decode_score(frame, cap, wv, tools, work / "cell")
                    _, _, planes = rdmatrix.read_y4m(work / "cell" / "decoded.y4m")
                    rr = roi_psnr(ref_luma, planes[0], box)
                    rois.append({"class": klass, "clip": r["clip"], "frame": fi, "wavelet": wv, "cap_bytes": cap, **rr})
            (out / "roi.json").write_text(json.dumps(rois, indent=1))
        with open(out / "frames.csv", "w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=COLUMNS); w.writeheader(); w.writerows(rows)
    summary = summarise(rows)
    (out / "rd_summary.json").write_text(json.dumps({"clips": summary, "class_deltas": class_deltas(summary)}, indent=1))
    # ROI deltas (Haar - 9/7 inside the box) feed the report's perceptual warnings
    by = {}
    for rr in rois:
        by.setdefault((rr["clip"], rr["cap_bytes"]), {})[rr["wavelet"]] = rr
    deltas = [{"clip": c, "cap_bytes": cap, "roi_delta_inside": d["haar"]["inside"] - d["97"]["inside"], "roi_delta_outside": d["haar"]["outside"] - d["97"]["outside"]}
              for (c, cap), d in by.items() if "97" in d and "haar" in d]
    (out / "roi.json").write_text(json.dumps(rois + deltas, indent=1))
    log("done")
    return rows, matches, rois


if __name__ == "__main__":
    main()
