"""Which PC downsample filter looks best in the headset after PyroWave coding?

The game renders above the stream size (owner settings: 3072x3216 per eye into a 2080x2208 stream)
and the composition pass filters it down (frame_downsample.hlsl). A sharper filter keeps more
detail but spends more of the byte cap on high frequencies and aliases more; a softer one is
cheaper to code but blurrier. This scores each candidate end to end on the quality_scene world,
cropped at the source size, 1:1 as the scene submits it:

    source RGB -> filter to the stream size -> YCbCr (BT.709 full range) -> 2x2 chroma box ->
    pyrowave-encode at the live per-eye byte cap -> decode -> bilinear back to the source size

and compares that with the source itself, so no filter is the reference. Metrics:
  hvs   PSNR-HVS-M luma at the source's pixels per degree (3216 px over ~99 deg): what detail
        survives, weighted by visibility.
  y, cb, cr   plain PSNR at the source size.
  hvs_s       PSNR-HVS-M at the stream size against the Lanczos-3 downsample of the source:
              the in-stream view (biased towards Lanczos-3; shown for reference only).

Usage:
  python -m tools.downsample.codec_study --out <dir> [--mbps 700 1000] [--wavelet haar 53]
"""
import argparse
import json
import math
import os
from pathlib import Path

import numpy as np

from tools.quest3.quality_scene import build_world
from tools.quest3.quality_score import TOOLS, WAVELET_ENV, box420, psnr, psnr_hvs_m, read_y4m, run, write_y4m


def catmull_rom(x):
    x = np.abs(x)
    return np.where(x < 1, (1.5 * x - 2.5) * x * x + 1,
                    np.where(x < 2, ((-0.5 * x + 2.5) * x - 4) * x + 2, 0.0))


def mitchell(x, b=1 / 3, c=1 / 3):
    x = np.abs(x)
    near = ((12 - 9 * b - 6 * c) * x ** 3 + (-18 + 12 * b + 6 * c) * x ** 2 + (6 - 2 * b)) / 6
    far = ((-b - 6 * c) * x ** 3 + (6 * b + 30 * c) * x ** 2 + (-12 * b - 48 * c) * x + (8 * b + 24 * c)) / 6
    return np.where(x < 1, near, np.where(x < 2, far, 0.0))


def lanczos(a):
    def k(x):
        x = np.abs(x)
        return np.where(x < a, np.sinc(x) * np.sinc(x / a), 0.0)
    return k


def box(x):
    # Area average: each source texel weighted by its overlap with the output pixel (x in output
    # pixels from the centre, texel width 1/scale handled by the caller's sampling density).
    return np.where(np.abs(x) < 0.5, 1.0, np.where(np.abs(x) == 0.5, 0.5, 0.0))


# name: (kernel, support radius in output pixels). The live filters: 'bilinear' is the single
# bilinear tap (render_downsample_filter Bilinear), 'catmull' the footprint-widened Catmull-Rom
# (Adaptive).
KERNELS = {
    'catmull': (catmull_rom, 2.0),
    'mitchell': (mitchell, 2.0),
    'lanczos2': (lanczos(2), 2.0),
    'lanczos3': (lanczos(3), 3.0),
    'box': (box, 0.5),
}


def weight_matrix(n_in, n_out, name):
    """Rows: output pixels; columns: source texels. Footprint-widened kernel, clamped edges,
    normalised rows, as frame_downsample.hlsl (direct_sample in reference.py) computes it."""
    scale = n_in / n_out
    m = np.zeros((n_out, n_in), np.float64)
    centres = (np.arange(n_out) + 0.5) * scale
    if name == 'bilinear':
        x = centres - 0.5
        i = np.floor(x).astype(int)
        f = x - i
        rows = np.arange(n_out)
        np.add.at(m, (rows, np.clip(i, 0, n_in - 1)), 1 - f)
        np.add.at(m, (rows, np.clip(i + 1, 0, n_in - 1)), f)
        return m
    kernel, radius = KERNELS[name]
    if name == 'box':
        # Exact overlap of [i, i+1] with the output footprint [c - s/2, c + s/2].
        for j, c in enumerate(centres):
            lo, hi = c - scale / 2, c + scale / 2
            for i in range(int(math.floor(lo)), int(math.ceil(hi))):
                overlap = min(hi, i + 1) - max(lo, i)
                if overlap > 0:
                    m[j, min(max(i, 0), n_in - 1)] += overlap
        return m / m.sum(1, keepdims=True)
    reach = radius * scale
    for j, c in enumerate(centres):
        idx = np.arange(int(math.floor(c - 0.5 - reach)), int(math.floor(c - 0.5 + reach)) + 2)
        w = kernel((idx + 0.5 - c) / scale)
        np.add.at(m[j], np.clip(idx, 0, n_in - 1), w)
    return m / m.sum(1, keepdims=True)


def resample(img, out_w, out_h, name, cache={}):
    h, w = img.shape[:2]
    key = (w, h, out_w, out_h, name)
    if key not in cache:
        cache[key] = (weight_matrix(h, out_h, name).astype(np.float32), weight_matrix(w, out_w, name).astype(np.float32))
    my, mx = cache[key]
    f = img.astype(np.float32)
    if f.ndim == 2:
        return my @ f @ mx.T
    return np.stack([my @ f[..., k] @ mx.T for k in range(f.shape[2])], -1)


def to_ycbcr(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    planes = (y, (b - y) / 1.8556 + 128, (r - y) / 1.5748 + 128)
    return [np.clip(np.rint(p), 0, 255).astype(np.uint8) for p in planes]


def upsample(plane, w, h):
    """Bilinear, centred, clamped: how the stream pixels land back on a finer grid."""
    return np.clip(np.rint(resample(plane, w, h, 'bilinear')), 0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True)
    ap.add_argument('--source', type=int, nargs=2, default=[3072, 3216])
    ap.add_argument('--stream', type=int, nargs=2, default=[2080, 2208])
    ap.add_argument('--filters', nargs='+', default=['bilinear', 'catmull', 'mitchell', 'lanczos2', 'lanczos3', 'box'])
    ap.add_argument('--mbps', type=float, nargs='+', default=[700, 1000])
    ap.add_argument('--wavelet', nargs='+', default=['haar', '53'])
    ap.add_argument('--hz', type=float, default=207)
    ap.add_argument('--crops', type=int, default=2, help='source-size windows of the world to average')
    ap.add_argument('--fov-deg', type=float, default=99.0, help='vertical field of view of the eye')
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sw, sh = args.source
    tw, th = args.stream
    world = build_world()[:, :, ::-1]  # BGR -> RGB
    wh, ww = world.shape[:2]
    # Windows spread across the world; the scene wraps horizontally, so tile it once.
    wide = np.concatenate([world, world], 1)
    tall = np.concatenate([wide, wide], 0)
    windows = [tall[(k * 1331) % wh:(k * 1331) % wh + sh, (k * 2477) % ww:(k * 2477) % ww + sw] for k in range(args.crops)]
    ppd_source, ppd_stream = sh / args.fov_deg, th / args.fov_deg
    # The live cap is per frame for both eyes side by side; one eye gets half.
    results = []
    for name in args.filters:
        frames = []
        refs = []
        for win in windows:
            frames.append(to_ycbcr(resample(win, tw, th, name)))
            refs.append(to_ycbcr(win.astype(np.float32)))
        lanczos_ref = [to_ycbcr(resample(win, tw, th, 'lanczos3'))[0] for win in windows]

        def score(decoded, label, extra):
            per = []
            for (y, cb, cr), (y0, cb0, cr0), ly in zip(decoded, refs, lanczos_ref):
                yu, cbu, cru = upsample(y, sw, sh), upsample(cb, sw, sh), upsample(cr, sw, sh)
                per.append({'hvs': psnr_hvs_m(y0, yu, ppd_source), 'y': psnr(y0, yu), 'cb': psnr(cb0, cbu),
                            'cr': psnr(cr0, cru), 'hvs_s': psnr_hvs_m(ly, y, ppd_stream)})
            row = dict(extra, filter=name, config=label, **{k: float(np.mean([p[k] for p in per])) for k in per[0]})
            results.append(row)
            print(json.dumps({k: round(v, 3) if isinstance(v, float) else v for k, v in row.items()}), flush=True)

        planes = [(y, box420(cb), box420(cr)) for y, cb, cr in frames]
        score(planes, 'uncoded 420', {'mbps': None, 'wavelet': None})
        inp = out / f'input-{name}.y4m'
        write_y4m(inp, planes, tw, th, '420jpeg', int(args.hz))
        for wavelet in args.wavelet:
            for mbps in args.mbps:
                cap = int(mbps * 1e6 / 8 / round(args.hz) / 2) // 4 * 4
                env = dict(os.environ, PYROWAVE_WAVELET=WAVELET_ENV[wavelet])
                enc, dec = out / 'tmp.pyrowave', out / 'tmp-dec.y4m'
                run([str(TOOLS / 'pyrowave-encode.exe'), str(inp), str(enc), str(cap)], env)
                run([str(TOOLS / 'pyrowave-decode.exe'), str(enc), str(dec)], env)
                decoded, _ = read_y4m(dec, len(planes))
                score(decoded, f'{wavelet} {int(mbps)}', {'mbps': mbps, 'wavelet': wavelet, 'bytes_cap': cap})
        inp.unlink()
    (out / 'results.json').write_text(json.dumps({'source': args.source, 'stream': args.stream,
                                                  'ppd_source': ppd_source, 'results': results}, indent=1))


if __name__ == '__main__':
    main()
