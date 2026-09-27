"""Mathematical compression research lab: transforms x quantisation x size models, offline.

Runs on the PC (numpy, cv2, matplotlib; ffmpeg/libvmaf for PSNR-HVS through
`rdmatrix.score`). Nothing here touches the live pipeline. Every number it writes is MEASURED on
the five 2560x2560 sources unless the column name says otherwise:

- `bits_bitplane`  DERIVED: bits a PyroWave-style raw bit-plane packing would need for the
                   quantised coefficients (per 8x8 group: planes = bit length of max |q|, one sign
                   bit per non-zero, a 16-bit ballot + 8-byte header per 32x32 block, 4 control
                   bits per non-empty 8x8). A model of pyrowave's bitstream, not its encoder.
- `bits_entropy`   DERIVED: zero-order entropy of the quantised coefficients per band -- a floor
                   any entropy coder could approach.
- `psnr_y`, `ssim_y`  MEASURED in numpy on luma (BT.709 full range), against the source.
- `psnr_hvs`       MEASURED by libvmaf through ffmpeg, for the RD points only.

Stages: `rd` (rate-distortion at the byte caps), `sparsity`, `precision`, `colour`, `precond`,
`bandvalue`; `all` runs them in order and writes CSVs into --out.
"""
import csv
import json
import os
import sys
import time
from pathlib import Path

import functools

import cv2
import numpy as np

print = functools.partial(print, flush=True)  # the run log is redirected to a file

from . import mathlab_transforms as T

TRANSFORMS = ("cdf97", "cdf53", "haar", "db2", "db4", "lap",
              "wht8", "wht16", "wht32", "wht64", "dct8", "dct16", "dct32")
CAPS = (150_000, 200_000, 250_000, 300_000, 350_000, 400_000, 416_667, 450_000, 500_000, 550_000)
RESOLUTIONS = (1920, 2560)
REFERENCE_BYTES = 416_667


# ---- sources ------------------------------------------------------------------------------------

def load_luma(png_path, resolution=None):
    """BT.709 full-range luma of a source PNG, float32 0..255, resized (lanczos) if asked."""
    bgr = cv2.imread(str(png_path))
    if bgr is None:
        raise FileNotFoundError(png_path)
    if resolution and bgr.shape[0] != resolution:
        bgr = cv2.resize(bgr, (resolution, resolution), interpolation=cv2.INTER_LANCZOS4)
    rgb = bgr[:, :, ::-1].astype(np.float32)
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


# ---- metrics ------------------------------------------------------------------------------------

def psnr(a, b, peak=255.0):
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return float("inf") if mse == 0 else 10.0 * np.log10(peak * peak / mse)


def ssim(a, b, peak=255.0):
    """Wang et al. SSIM on luma with an 11x11 Gaussian (sigma 1.5), mean over the image."""
    a = a.astype(np.float64); b = b.astype(np.float64)
    c1, c2 = (0.01 * peak) ** 2, (0.03 * peak) ** 2
    g = cv2.getGaussianKernel(11, 1.5)
    k = (g @ g.T).astype(np.float64)
    f = lambda x: cv2.filter2D(x, -1, k, borderType=cv2.BORDER_REFLECT)
    mu_a, mu_b = f(a), f(b)
    s_aa, s_bb, s_ab = f(a * a) - mu_a ** 2, f(b * b) - mu_b ** 2, f(a * b) - mu_a * mu_b
    num = (2 * mu_a * mu_b + c1) * (2 * s_ab + c2)
    den = (mu_a ** 2 + mu_b ** 2 + c1) * (s_aa + s_bb + c2)
    return float(np.mean(num / den))


# ---- quantisation and size models ---------------------------------------------------------------

def quantise(bands, delta, gains):
    """Uniform mid-tread quantiser per band with the step scaled by the band's synthesis gain, so
    every band contributes the same reconstruction MSE per unit step. Returns (q_int, recon)."""
    q, rec = {}, {}
    for b, c in bands.items():
        step = delta / gains[b]
        qi = np.rint(c / step).astype(np.int32)
        q[b] = qi
        rec[b] = (qi * step).astype(np.float32)
    return q, rec


def entropy_bits(q):
    """Zero-order entropy in bits, summed over bands."""
    total = 0.0
    for arr in q.values():
        vals, counts = np.unique(arr, return_counts=True)
        p = counts / arr.size
        total += float(-(p * np.log2(p)).sum() * arr.size)
    return total


def bitplane_bits(q, group=8, block=32):
    """PyroWave-style raw bit-plane cost of quantised bands (see module doc)."""
    total = 0
    for arr in q.values():
        h, w = arr.shape
        H = (h + block - 1) // block * block
        W = (w + block - 1) // block * block
        a = np.zeros((H, W), np.int64)
        a[:h, :w] = np.abs(arr)
        s = np.zeros((H, W), np.int64)
        s[:h, :w] = (arr != 0)
        # per 8x8 group: max magnitude, count of non-zeros
        g = a.reshape(H // group, group, W // group, group)
        gmax = g.max(axis=(1, 3))
        nz = s.reshape(H // group, group, W // group, group).sum(axis=(1, 3))
        planes = np.where(gmax > 0, np.floor(np.log2(np.maximum(gmax, 1))).astype(np.int64) + 1, 0)
        group_bits = planes * group * group + nz + np.where(planes > 0, 4, 0)
        # per 32x32 block: header + ballot only when any group is non-empty
        bg = block // group
        blk = group_bits.reshape(H // block, bg, W // block, bg).sum(axis=(1, 3))
        total += int(blk.sum() + (64 + 16) * int((blk > 0).sum()))
    return total


def find_delta_for_bytes(bands, gains, target_bytes, cost=bitplane_bits, lo=0.05, hi=512.0, iters=22):
    """Bisection on the quantiser step so the size model meets the byte cap (cost is monotone
    non-increasing in delta). Returns (delta, q, recon, bits)."""
    best = None
    for _ in range(iters):
        mid = np.sqrt(lo * hi)
        q, rec = quantise(bands, mid, gains)
        bits = cost(q)
        if bits > target_bytes * 8:
            lo = mid
        else:
            hi = mid
            best = (mid, q, rec, bits)
    if best is None:
        q, rec = quantise(bands, hi, gains)
        best = (hi, q, rec, cost(q))
    return best


# ---- experiments --------------------------------------------------------------------------------

def rd_experiment(sources, out_csv, transforms=TRANSFORMS, caps=CAPS, resolutions=RESOLUTIONS,
                  tools=None, log=print):
    """Quality at exact byte caps under the bit-plane size model, per transform/source/resolution.
    Also records the entropy floor at the same quantiser and, when `tools` is given, PSNR-HVS by
    libvmaf for the 416,667 B point."""
    from .rdmatrix import write_y4m, score
    cols = ["source", "dataset", "resolution", "transform", "cap_bytes", "delta", "bits_bitplane",
            "bytes_bitplane", "bits_entropy", "bytes_entropy", "psnr_y", "ssim_y", "psnr_hvs",
            "nonzero_fraction", "coefficients", "seconds"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols); wr.writeheader()
        for src in sources:
            for res in resolutions:
                x = load_luma(src, res)
                for name in transforms:
                    t0 = time.perf_counter()
                    tr = T.Transform(name)
                    bands = tr.forward(x)
                    gains = tr.band_gains(x.shape)
                    n_coef = tr.coefficient_count(bands)
                    for cap in caps:
                        delta, q, rec, bits = find_delta_for_bytes(bands, gains, cap)
                        y = np.clip(tr.inverse(rec), 0, 255)
                        row = {"source": Path(src).stem, "dataset": "kodak" if Path(src).stem.startswith("kodak") else "synthetic",
                               "resolution": res, "transform": name, "cap_bytes": cap, "delta": round(float(delta), 5),
                               "bits_bitplane": bits, "bytes_bitplane": bits // 8,
                               "bits_entropy": round(entropy_bits(q)), "bytes_entropy": round(entropy_bits(q) / 8),
                               "psnr_y": round(psnr(x, y), 3), "ssim_y": round(ssim(x, y), 5), "psnr_hvs": "",
                               "nonzero_fraction": round(float(np.mean(np.concatenate([(v != 0).ravel() for v in q.values()]))), 5),
                               "coefficients": n_coef, "seconds": round(time.perf_counter() - t0, 1)}
                        if tools and cap == REFERENCE_BYTES:
                            work = Path(out_csv).parent / "work"; work.mkdir(exist_ok=True)
                            ref, dis = work / "ref.y4m", work / "dis.y4m"
                            g = np.repeat(x[:, :, None], 3, axis=2).astype(np.uint8)  # luma as grey RGB
                            write_y4m(ref, np.repeat(np.rint(x)[:, :, None], 3, 2).astype(np.uint8))
                            write_y4m(dis, np.repeat(np.rint(y)[:, :, None], 3, 2).astype(np.uint8))
                            s = score(tools.get("ffmpeg", "ffmpeg"), dis, ref, work)
                            row["psnr_hvs"] = s.get("psnr_hvs", "")
                        wr.writerow(row); f.flush()
                        log(f"{row['source']} {res} {name} {cap}: {row['bytes_bitplane']} B, psnr_y {row['psnr_y']}, ssim {row['ssim_y']}, entropy {row['bytes_entropy']} B")


def sparsity_experiment(sources, out_csv, transforms=TRANSFORMS, resolution=2560, log=print):
    """P(eps) = fraction of |gain-weighted c| < eps, and PSNR after zeroing them (no quantiser)."""
    eps_list = (0.5, 1, 2, 4, 8, 16, 32, 64)
    cols = ["source", "transform", "eps", "sparsity", "psnr_y", "energy_kept"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols); wr.writeheader()
        for src in sources:
            x = load_luma(src, resolution)
            for name in transforms:
                tr = T.Transform(name); bands = tr.forward(x); gains = tr.band_gains(x.shape)
                total_e = sum(float(np.sum((v.astype(np.float64) * gains[b]) ** 2)) for b, v in bands.items())
                for eps in eps_list:
                    kept, count, keep_e = {}, 0, 0.0
                    n = 0
                    for b, v in bands.items():
                        w = v * gains[b]
                        mask = np.abs(w) >= eps
                        kept[b] = np.where(mask, v, 0).astype(np.float32)
                        count += int((~mask).sum()); n += v.size
                        keep_e += float(np.sum((w[mask].astype(np.float64)) ** 2))
                    y = np.clip(tr.inverse(kept), 0, 255)
                    wr.writerow({"source": Path(src).stem, "transform": name, "eps": eps,
                                 "sparsity": round(count / n, 5), "psnr_y": round(psnr(x, y), 3),
                                 "energy_kept": round(keep_e / total_e, 6)})
                f.flush(); log(f"sparsity {Path(src).stem} {name} done")


def precision_experiment(sources, out_csv, resolution=2560, log=print):
    """Reconstruction error when coefficients are stored/computed in fp16 or int16 instead of fp32,
    at the reference cap's quantiser, for the transforms a decoder would actually run."""
    names = ("cdf97", "cdf53", "haar", "dct16", "wht16", "lap")
    cols = ["source", "transform", "precision", "psnr_vs_fp32", "psnr_vs_source", "max_abs_err"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols); wr.writeheader()
        for src in sources:
            x = load_luma(src, resolution)
            for name in names:
                tr = T.Transform(name); bands = tr.forward(x); gains = tr.band_gains(x.shape)
                delta, q, rec, _ = find_delta_for_bytes(bands, gains, REFERENCE_BYTES)
                y32 = np.clip(tr.inverse({b: v.astype(np.float32) for b, v in rec.items()}), 0, 255)
                for prec in ("fp16_storage", "fp16_math", "int16"):
                    if prec == "fp16_storage":
                        rb = {b: v.astype(np.float16).astype(np.float32) for b, v in rec.items()}
                        y = tr.inverse(rb)
                    elif prec == "fp16_math":
                        rb = {b: v.astype(np.float16) for b, v in rec.items()}
                        try:
                            y = tr.inverse({b: v.astype(np.float16) for b, v in rb.items()}).astype(np.float32)
                        except Exception:
                            y = tr.inverse({b: v.astype(np.float32) for b, v in rb.items()})
                    else:
                        scale = 32767.0 / max(float(max(np.abs(v).max() for v in rec.values())), 1e-6)
                        rb = {b: (np.rint(v * scale).astype(np.int16).astype(np.float32) / scale) for b, v in rec.items()}
                        y = tr.inverse(rb)
                    y = np.clip(np.nan_to_num(y.astype(np.float32)), 0, 255)
                    wr.writerow({"source": Path(src).stem, "transform": name, "precision": prec,
                                 "psnr_vs_fp32": round(psnr(y32, y), 3), "psnr_vs_source": round(psnr(x, y), 3),
                                 "max_abs_err": round(float(np.max(np.abs(y32 - y))), 3)})
                f.flush(); log(f"precision {Path(src).stem} {name} done")


def colour_experiment(sources, out_csv, resolution=2560, log=print):
    """Channel correlations and 8-bit zero-order entropies of RGB and of candidate colour
    transforms, per source, plus an offline PCA basis per source."""
    def ent(ch):
        h = np.bincount(np.clip(np.rint(ch).astype(np.int64) + 512, 0, 1535), minlength=1536)
        p = h[h > 0] / h.sum()
        return float(-(p * np.log2(p)).sum())
    cols = ["source", "rho_RG", "rho_RB", "rho_GB", "space", "H_c0", "H_c1", "H_c2", "H_sum",
            "inverse_ops_per_pixel", "pca_axes"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols); wr.writeheader()
        for src in sources:
            bgr = cv2.imread(str(src))
            if bgr.shape[0] != resolution:
                bgr = cv2.resize(bgr, (resolution, resolution), interpolation=cv2.INTER_LANCZOS4)
            rgb = bgr[:, :, ::-1].astype(np.float32).reshape(-1, 3)
            R, G, B = rgb[:, 0], rgb[:, 1], rgb[:, 2]
            c = np.corrcoef(rgb.T)
            spaces = {
                "RGB": (R, G, B, 0),
                "YCbCr709": (0.2126 * R + 0.7152 * G + 0.0722 * B, (B - (0.2126 * R + 0.7152 * G + 0.0722 * B)) / 1.8556,
                             (R - (0.2126 * R + 0.7152 * G + 0.0722 * B)) / 1.5748, 6),
                "YCoCg": ((R + 2 * G + B) / 4, R - B, G - (R + B) / 2, 4),
                "Y_R-G_B-G": ((R + 2 * G + B) / 4, R - G, B - G, 4),
            }
            cen = rgb - rgb.mean(axis=0)
            cov = cen.T @ cen / len(cen)
            w, v = np.linalg.eigh(cov)
            order = np.argsort(w)[::-1]
            v = v[:, order]
            pcs = cen @ v
            spaces["PCA_offline"] = (pcs[:, 0], pcs[:, 1], pcs[:, 2], 9)
            for space, (c0, c1, c2, ops) in spaces.items():
                h0, h1, h2 = ent(c0), ent(c1), ent(c2)
                wr.writerow({"source": Path(src).stem, "rho_RG": round(float(c[0, 1]), 4), "rho_RB": round(float(c[0, 2]), 4),
                             "rho_GB": round(float(c[1, 2]), 4), "space": space, "H_c0": round(h0, 3), "H_c1": round(h1, 3),
                             "H_c2": round(h2, 3), "H_sum": round(h0 + h1 + h2, 3), "inverse_ops_per_pixel": ops,
                             "pca_axes": json.dumps(np.round(v.T, 4).tolist()) if space == "PCA_offline" else ""})
            f.flush(); log(f"colour {Path(src).stem} done")


def precond_experiment(sources, out_csv, resolution=2560, log=print):
    """Encoder-side pre-filters before the transform: bytes at a fixed quantiser step (the step
    that met 416,667 B on the unfiltered image) and quality vs the ORIGINAL, for cdf97 and haar."""
    filters = {
        "none": lambda x: x,
        "gauss_0.5": lambda x: cv2.GaussianBlur(x, (0, 0), 0.5),
        "gauss_0.8": lambda x: cv2.GaussianBlur(x, (0, 0), 0.8),
        "bilateral_5_20": lambda x: cv2.bilateralFilter(x, 5, 20, 3),
    }
    cols = ["source", "transform", "prefilter", "delta", "bytes_bitplane", "psnr_vs_original", "psnr_vs_prefiltered",
            "bytes_bitplane_at_cap", "psnr_vs_original_at_cap"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols); wr.writeheader()
        for src in sources:
            x = load_luma(src, resolution)
            for name in ("cdf97", "haar"):
                tr = T.Transform(name); gains = tr.band_gains(x.shape)
                delta0, _, _, _ = find_delta_for_bytes(tr.forward(x), gains, REFERENCE_BYTES)
                for fname, fn in filters.items():
                    xf = fn(x.astype(np.float32))
                    bands = tr.forward(xf)
                    q, rec = quantise(bands, delta0, gains)
                    y = np.clip(tr.inverse(rec), 0, 255)
                    _, _, rec2, bits2 = find_delta_for_bytes(bands, gains, REFERENCE_BYTES)
                    y2 = np.clip(tr.inverse(rec2), 0, 255)
                    wr.writerow({"source": Path(src).stem, "transform": name, "prefilter": fname, "delta": round(float(delta0), 5),
                                 "bytes_bitplane": bitplane_bits(q) // 8, "psnr_vs_original": round(psnr(x, y), 3),
                                 "psnr_vs_prefiltered": round(psnr(xf, y), 3), "bytes_bitplane_at_cap": bits2 // 8,
                                 "psnr_vs_original_at_cap": round(psnr(x, y2), 3)})
                f.flush(); log(f"precond {Path(src).stem} {name} done")


def bandvalue_experiment(sources, out_csv, resolution=2560, log=print):
    """Marginal value of precision per band for cdf97 at the reference cap: refine one band's step
    by 2^-0.5 and record dPSNR / dKB (bit-plane model). Where extra precision buys the most."""
    cols = ["source", "band", "base_bytes", "base_psnr", "bytes_after", "psnr_after", "dpsnr_per_kb"]
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=cols); wr.writeheader()
        for src in sources:
            x = load_luma(src, resolution)
            tr = T.Transform("cdf97"); bands = tr.forward(x); gains = tr.band_gains(x.shape)
            delta, q, rec, bits = find_delta_for_bytes(bands, gains, REFERENCE_BYTES)
            base_psnr = psnr(x, np.clip(tr.inverse(rec), 0, 255))
            for b in bands:
                g2 = dict(gains); g2[b] = gains[b] * np.sqrt(2.0)
                q2, rec2 = quantise(bands, delta, g2)
                bits2 = bitplane_bits(q2)
                p2 = psnr(x, np.clip(tr.inverse(rec2), 0, 255))
                dkb = (bits2 - bits) / 8000.0
                wr.writerow({"source": Path(src).stem, "band": b, "base_bytes": bits // 8, "base_psnr": round(base_psnr, 3),
                             "bytes_after": bits2 // 8, "psnr_after": round(p2, 3),
                             "dpsnr_per_kb": round((p2 - base_psnr) / dkb, 4) if dkb > 0 else ""})
            f.flush(); log(f"bandvalue {Path(src).stem} done")


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("stage", choices=("rd", "sparsity", "precision", "colour", "precond", "bandvalue", "all"))
    p.add_argument("--sources", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--ffmpeg", default="ffmpeg")
    p.add_argument("--transforms", default=",".join(TRANSFORMS))
    p.add_argument("--resolutions", default=",".join(str(r) for r in RESOLUTIONS))
    args = p.parse_args(argv)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    sources = sorted(Path(args.sources).glob("*.png"))
    tr = tuple(args.transforms.split(","))
    res = tuple(int(r) for r in args.resolutions.split(","))
    stages = ("colour", "sparsity", "precision", "bandvalue", "precond", "rd") if args.stage == "all" else (args.stage,)
    for st in stages:
        t0 = time.perf_counter()
        if st == "rd":
            rd_experiment(sources, out / "rd.csv", transforms=tr, resolutions=res, tools={"ffmpeg": args.ffmpeg})
        elif st == "sparsity":
            sparsity_experiment(sources, out / "sparsity.csv", transforms=tr)
        elif st == "precision":
            precision_experiment(sources, out / "precision.csv")
        elif st == "colour":
            colour_experiment(sources, out / "colour.csv")
        elif st == "precond":
            precond_experiment(sources, out / "precond.csv")
        elif st == "bandvalue":
            bandvalue_experiment(sources, out / "bandvalue.csv")
        print(f"== {st} done in {time.perf_counter() - t0:.0f} s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
