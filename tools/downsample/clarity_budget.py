"""Clarity budget (docs/PLAN.md 2.2): where the image loses sharpness between the game and the panel.

The game renders at 3072x3216 per eye. The panel shows about 25 pixels per degree at the lens centre,
so the best the viewer can see is the render filtered straight to that density (Lanczos-3): the
"ideal". Each stage of the stream is added in turn and scored against the ideal at the panel's
density, so the drop from one row to the next is what that stage costs:

  1. stream size: Catmull-Rom (the live Adaptive filter) to the stream, 4:4:4, uncoded, shown with a
     Lanczos-3 resample (a perfect display path)
  2. display resampling: the same, shown with bilinear resampling (the eye pass and the compositor)
  3. 4:2:0: the server's 2x2 box, bilinear chroma upsample on the headset
  4. quantization: PyroWave at the live per-eye byte cap, decoded in FP32 (PYROWAVE_PRECISION=2)
  5. FP16: the same bitstream decoded as the headset does (PYROWAVE_PRECISION=1)
  6. display alternatives for the first point: a perfect resample (the bound), a Catmull-Rom
     upscale into a larger eye swapchain before the compositor's bilinear, and sharpening

Metrics at the display size: hvs (PSNR-HVS-M luma at the panel's pixels per degree), y, cb, cr
(plain PSNR) and de (mean CIE76 colour difference, Lab).

Usage:
  python -m tools.downsample.clarity_budget --out <dir> [--streams 2080x2208 2592x2784]
      [--points 120:1500 120:2000 207:1000] [--wavelet 53] [--ppd 25]
"""
import argparse
import json
import os
from pathlib import Path

import cv2
import numpy as np

from tools.downsample.codec_study import resample, to_ycbcr
from tools.quest3.quality_scene import build_world
from tools.quest3.quality_score import TOOLS, WAVELET_ENV, box420, psnr, psnr_hvs_m, read_y4m, run, write_y4m


def to_u8(p):
    return np.clip(np.rint(p), 0, 255).astype(np.uint8)


def cas(y, peak):
    """The eye shader's contrast-adaptive sharpening of luma (present_ycbcr.glsl, Q3PW_SHARPEN)."""
    f = y.astype(np.float32) / 255
    p = np.pad(f, 1, mode='edge')
    n, s, w, e = p[:-2, 1:-1], p[2:, 1:-1], p[1:-1, :-2], p[1:-1, 2:]
    mn = np.minimum.reduce([f, n, s, w, e])
    mx = np.maximum.reduce([f, n, s, w, e])
    amp = np.sqrt(np.clip(np.minimum(mn, 1 - mx) / np.maximum(mx, 1 / 256), 0, 1))
    wgt = -amp * peak
    return to_u8(np.clip((f + wgt * (n + s + w + e)) / (1 + 4 * wgt), 0, 1) * 255)


def lab(y, cb, cr):
    yf, u, v = y.astype(np.float32), cb.astype(np.float32) - 128, cr.astype(np.float32) - 128
    bgr = np.clip(np.stack([yf + 1.8556 * u, yf - 0.1873 * u - 0.4681 * v, yf + 1.5748 * v], -1), 0, 255)
    return cv2.cvtColor(bgr / 255, cv2.COLOR_BGR2LAB)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True)
    ap.add_argument('--source', type=int, nargs=2, default=[3072, 3216])
    ap.add_argument('--streams', nargs='+', default=['2080x2208', '2592x2784'])
    ap.add_argument('--points', nargs='+', default=['120:1500', '120:2000', '207:1000'],
                    help='hz:mbps; 207 Hz points are skipped for streams above 2080x2208')
    ap.add_argument('--wavelet', default='53')
    ap.add_argument('--ppd', type=float, default=25.0, help='panel pixels per degree at the lens centre')
    ap.add_argument('--fov-deg', type=float, default=99.0, help='vertical field of view of the eye')
    ap.add_argument('--crops', type=int, default=2)
    ap.add_argument('--display', nargs='*', default=['lanczos3', 'up1.25+bilinear', 'up1.5+bilinear', 'cas50+bilinear',
                                                     'cas100+bilinear', 'cas50+up1.25+bilinear'],
                    help='display paths scored on the first point (see show())')
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sw, sh = args.source
    dh = round(args.ppd * args.fov_deg)
    dw = round(dh * sw / sh)
    world = build_world()[:, :, ::-1]  # BGR -> RGB
    wh, ww = world.shape[:2]
    tall = np.concatenate([np.concatenate([world, world], 1)] * 2, 0)
    windows = [tall[(k * 1331) % wh:(k * 1331) % wh + sh, (k * 2477) % ww:(k * 2477) % ww + sw]
               for k in range(args.crops)]
    ideal = [to_ycbcr(resample(win, dw, dh, 'lanczos3')) for win in windows]
    ideal_lab = [lab(*p) for p in ideal]
    results = []

    def show(planes, kernel):
        """Stream planes (chroma at stream or half size) to the display size. kernel is the display
        resampling, optionally after '+'-joined steps on the stream: casNN (the Sharpening setting
        NN), linK (a linear cross sharpen, K/100 per neighbour) and up<f> (a Catmull-Rom upscale by f into a larger eye swapchain)."""
        y = planes[0]
        h, w = y.shape
        cb, cr = (cv2.resize(c, (w, h), interpolation=cv2.INTER_LINEAR) if c.shape != y.shape else c
                  for c in planes[1:])
        planes = [y, cb, cr]
        *steps, kernel = kernel.split('+')
        for step in steps:
            if step.startswith('cas'):
                planes[0] = cas(planes[0], 0.125 + 0.075 * int(step[3:]) / 100)
            elif step.startswith('lin'):
                # A linear cross sharpen with weight k/100 per neighbour (CAS without the adaptivity).
                k = int(step[3:]) / 100
                f = planes[0].astype(np.float32)
                q = np.pad(f, 1, mode='edge')
                planes[0] = to_u8(f + k * (4 * f - q[:-2, 1:-1] - q[2:, 1:-1] - q[1:-1, :-2] - q[1:-1, 2:]))
            elif step.startswith('up'):
                f = float(step[2:])
                planes = [to_u8(resample(p, round(w * f), round(h * f), 'catmull')) for p in planes]
        return [to_u8(resample(p, dw, dh, kernel)) for p in planes]

    def score(frames, kernel, stream, stage, extra=None):
        per = []
        for planes, ref, ref_lab in zip(frames, ideal, ideal_lab):
            y, cb, cr = show(planes, kernel)
            per.append({'hvs': psnr_hvs_m(ref[0], y, args.ppd), 'y': psnr(ref[0], y), 'cb': psnr(ref[1], cb),
                        'cr': psnr(ref[2], cr), 'de': float(np.linalg.norm(ref_lab - lab(y, cb, cr), axis=-1).mean())})
        row = dict(extra or {}, stream=stream, stage=stage, **{k: float(np.mean([p[k] for p in per])) for k in per[0]})
        results.append(row)
        print(json.dumps({k: round(v, 3) if isinstance(v, float) else v for k, v in row.items()}), flush=True)

    for stream in args.streams:
        tw, th = map(int, stream.split('x'))
        full = [to_ycbcr(resample(win, tw, th, 'catmull')) for win in windows]
        score(full, 'lanczos3', stream, '1 stream size (4:4:4, uncoded, ideal display)')
        score(full, 'bilinear', stream, '2 + bilinear display resampling')
        sub = [(y, box420(cb), box420(cr)) for y, cb, cr in full]
        score(sub, 'bilinear', stream, '3 + 4:2:0')
        inp, enc, dec = out / 'input.y4m', out / 'tmp.pyrowave', out / 'tmp-dec.y4m'
        for point in args.points:
            hz, mbps = map(float, point.split(':'))
            if hz > 120 and tw > 2080:
                continue
            write_y4m(inp, sub, tw, th, '420jpeg', int(hz))
            cap = int(mbps * 1e6 / 8 / round(hz) / 2) // 4 * 4
            env = dict(os.environ, PYROWAVE_WAVELET=WAVELET_ENV[args.wavelet], PYROWAVE_PRECISION='2')
            run([str(TOOLS / 'pyrowave-encode.exe'), str(inp), str(enc), str(cap)], env)
            extra = {'hz': hz, 'mbps': mbps, 'bytes_cap': cap}
            for precision, stage in (('2', '4 + quantization (FP32 decode)'), ('1', '5 + FP16 decode (headset)')):
                run([str(TOOLS / 'pyrowave-decode.exe'), str(enc), str(dec)], dict(env, PYROWAVE_PRECISION=precision))
                decoded = read_y4m(dec, len(sub))[0]
                score(decoded, 'bilinear', stream, f'{stage} {int(hz)} Hz {int(mbps)} Mbps', extra)
            if point == args.points[0]:
                # Display-path alternatives for the headset decode of the first point.
                for kernel in args.display:
                    score(decoded, kernel, stream, f'6 display {kernel} {int(hz)} Hz {int(mbps)} Mbps', extra)
        for p in (inp, enc, dec):
            p.unlink(missing_ok=True)
    (out / 'results.json').write_text(json.dumps({'source': args.source, 'display': [dw, dh], 'ppd': args.ppd,
                                                  'results': results}, indent=1))


if __name__ == '__main__':
    main()
