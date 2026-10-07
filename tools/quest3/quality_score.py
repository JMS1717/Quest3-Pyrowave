"""Score PyroWave image quality on dumped encoder input, the way a Quest 3 viewer sees it.

Input is an ALVR_PYROWAVE_DUMP clip: full-range 4:4:4 planes exactly as the live encoder
received them, before the server's 2x2 chroma box average. For each configuration the clip is
reduced to 4:2:0 the same way the server does it, encoded at the per-frame byte cap that the
bitrate and refresh rate imply, decoded, upsampled the way the Quest conversion shader samples
chroma (bilinear, centred), and compared with the 4:4:4 source. Chroma loss from 4:2:0 therefore
counts as error, because the player sees it.

Metrics (all on full-range 8-bit planes, higher is better):
  hvs     PSNR-HVS-M luma, with upstream PyroWave's CSF evaluated at the stream's pixels per
          degree (Quest 3 stream: 2208 px over ~99 deg vertical, ~22 px/deg) instead of a monitor
          viewing distance. Weights errors by visibility; the headline number.
  y, cb, cr   plain PSNR per plane.
  edge_c  PSNR of Cb+Cr restricted to pixels near strong luma or chroma edges: colour bleed.
  block   luma blockiness: mean absolute step across 8-pixel boundaries divided by the step
          inside blocks, decoded minus source (0 = no added block edges).

Usage:
  python -m tools.quest3.quality_score <clip.y4m> --out <dir> --mbps 500 700 1000 2000 \
      --hz 207 --wavelet haar 53 97 [--live <clip.y4m.cpu.wave>] [--frames 2]
"""
import argparse
import json
import math
import os
import subprocess
from pathlib import Path

import cv2
import numpy as np

# pyrowave-encode / pyrowave-decode from the PC PyroWave build (tools/windows/build_pyrowave_pc.cmd).
TOOLS = Path(os.environ.get('PYROWAVE_PC_TOOLS',
                            Path(__file__).resolve().parents[3] / 'workspace/research/pyrowave/build-pc/Release'))
WAVELET_ENV = {'haar': 'haar', '53': '53', '97': '97'}


def read_y4m(path, limit=None):
    with open(path, 'rb') as f:
        header = f.readline().decode().split()
        info = {t[0]: t[1:] for t in header[1:]}
        w, h = int(info['W']), int(info['H'])
        chroma = info.get('C', '420jpeg')
        # pyrowave-decode labels a 4:2:0 tap as C444 and doubles the magic; trust the size.
        body = os.path.getsize(path) - f.tell()
        full = body // (w * h * 3 + 6) * (w * h * 3 + 6) == body and body > 0
        cw, ch = (w, h) if chroma.startswith('444') and full else ((w + 1) // 2, (h + 1) // 2)
        frames = []
        while limit is None or len(frames) < limit:
            line = f.readline()
            if not line:
                break
            y = np.frombuffer(f.read(w * h), np.uint8).reshape(h, w)
            cb = np.frombuffer(f.read(cw * ch), np.uint8).reshape(ch, cw)
            cr = np.frombuffer(f.read(cw * ch), np.uint8).reshape(ch, cw)
            frames.append((y, cb, cr))
    return frames, header


def write_y4m(path, frames, w, h, chroma, fps=207):
    with open(path, 'wb') as f:
        f.write(f'YUV4MPEG2 W{w} H{h} F{fps}:1 Ip A1:1 XCOLORRANGE=FULL C{chroma}\n'.encode())
        for y, cb, cr in frames:
            f.write(b'FRAME\n')
            for p in (y, cb, cr):
                f.write(np.ascontiguousarray(p).tobytes())


def box420(c):
    """The server's centred 2x2 box average, stored to an R8 UNORM target (round to nearest)."""
    c = c.astype(np.float32)
    return np.clip(np.rint((c[0::2, 0::2] + c[1::2, 0::2] + c[0::2, 1::2] + c[1::2, 1::2]) / 4), 0, 255
                   ).astype(np.uint8)


def up420(c, w, h):
    """Bilinear with centred siting and clamped edges, as the Quest's sampler reads chroma."""
    return cv2.resize(c, (w, h), interpolation=cv2.INTER_LINEAR)


def psnr(a, b, mask=None):
    d = a.astype(np.float64) - b.astype(np.float64)
    mse = np.mean(d[mask] ** 2) if mask is not None else np.mean(d ** 2)
    return 99.0 if mse == 0 else 10 * math.log10(255.0 ** 2 / mse)


def _dct_matrix():
    m = np.zeros((8, 8))
    for k in range(8):
        for n in range(8):
            m[k, n] = math.sqrt((1 if k == 0 else 2) / 8) * math.cos(math.pi * (2 * n + 1) * k / 16)
    return m


DCT = _dct_matrix()


def _csf_tables(ppd):
    """Upstream PyroWave psnr.cpp, with nyquist taken from pixels per degree."""
    def csf_fn(cpd):
        return 2.6 * (0.0192 + 0.114 * cpd) * math.exp(-((0.114 * cpd) ** 1.1))
    nyquist = ppd / 2
    csf = np.zeros((8, 8))
    for y in range(8):
        for x in range(8):
            r = math.hypot((x + 0.5) / 8, (y + 0.5) / 8)
            csf[y, x] = 2.6 * csf_fn(r * nyquist)
    norm = (1 / csf.max()) ** 2
    mask = csf * csf * norm
    mask[0, 0] = 0
    inv = np.where(mask > 0, 1 / np.where(mask > 0, mask, 1), 0)
    return csf, mask, inv


def psnr_hvs_m(a, b, ppd):
    """Non-overlapping 8x8 PSNR-HVS-M on full-range luma (port of upstream psnr_hvs_m.comp)."""
    csf, maskc, inv = _csf_tables(ppd)
    h, w = (a.shape[0] // 8) * 8, (a.shape[1] // 8) * 8

    def blocks(img):
        x = img[:h, :w].astype(np.float64) / 255.0
        return x.reshape(h // 8, 8, w // 8, 8).transpose(0, 2, 1, 3).reshape(-1, 8, 8)

    def var(s, s2, n):
        return np.maximum(s2 * n - s * s, 0) / n / (n - 1)

    def maskeff(sig, dct):
        m = np.sum(dct ** 2 * maskc, axis=(1, 2))
        sub = sig.reshape(-1, 2, 4, 2, 4).transpose(0, 1, 3, 2, 4).reshape(-1, 4, 16)
        s, s2 = sub.sum(-1), (sub ** 2).sum(-1)
        pop = var(s.sum(-1), s2.sum(-1), 64)
        sub_var = var(s, s2, 16).sum(-1)
        pop = np.where(pop != 0, sub_var / np.where(pop != 0, pop, 1), 0)
        return np.sqrt(m * pop) / 32

    total = 0.0
    ba, bb = blocks(a), blocks(b)
    for i in range(0, len(ba), 65536):
        sa, sb = ba[i:i + 65536], bb[i:i + 65536]
        da, db = DCT @ sa @ DCT.T, DCT @ sb @ DCT.T
        mask = np.maximum(maskeff(sa, da), maskeff(sb, db))
        e = np.maximum(np.abs(da - db) - mask[:, None, None] * inv, 0) * csf
        total += float(np.sum(e ** 2))
    return 99.0 if total == 0 else 10 * math.log10(h * w / total)


def edge_mask(y, cb, cr):
    g = np.zeros(y.shape, np.float32)
    for p in (y, cb, cr):
        p = p.astype(np.float32)
        g = np.maximum(g, np.abs(cv2.Sobel(p, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(p, cv2.CV_32F, 0, 1)))
    return cv2.dilate((g > 120).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)


def blockiness(y):
    y = y.astype(np.float32)
    dx = np.abs(np.diff(y, axis=1))
    at = dx[:, 7::8].mean()
    inside = np.delete(dx, np.s_[7::8], axis=1).mean()
    return at / max(inside, 1e-6)


def score(src, dec, ppd):
    y0, cb0, cr0 = src
    y1, cb1, cr1 = dec
    w, h = y0.shape[1], y0.shape[0]
    if cb0.shape != y0.shape:
        cb0, cr0 = up420(cb0, w, h), up420(cr0, w, h)
    if cb1.shape != y1.shape:
        cb1, cr1 = up420(cb1, w, h), up420(cr1, w, h)
    m = edge_mask(y0, cb0, cr0)
    edge = 10 * math.log10(255.0 ** 2 / max(1e-12, (np.mean((cb0[m].astype(float) - cb1[m]) ** 2) +
                                                     np.mean((cr0[m].astype(float) - cr1[m]) ** 2)) / 2))
    return {'hvs': psnr_hvs_m(y0, y1, ppd), 'y': psnr(y0, y1), 'cb': psnr(cb0, cb1), 'cr': psnr(cr0, cr1),
            'edge_c': edge, 'block': float(blockiness(y1) - blockiness(y0))}


def flicker(src, dec):
    """PSNR of the frame-to-frame luma change, decoded vs source. A codec error that holds still
    cancels out; one that changes from frame to frame (shimmer, crawling edges, popping blocks on
    moving content) does not. Only meaningful on consecutive frames."""
    if len(src) < 2:
        return None
    err = []
    for i in range(1, len(src)):
        ds = src[i][0].astype(np.int16) - src[i - 1][0]
        dd = dec[i][0].astype(np.int16) - dec[i - 1][0]
        err.append(np.mean((ds - dd).astype(np.float64) ** 2))
    return 10 * math.log10(255.0 ** 2 / max(np.mean(err), 1e-12))


def run(cmd, env=None):
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode:
        raise RuntimeError(f'{cmd[0]} failed: {r.stderr[-600:]}')
    return r


def to_rgb(y, cb, cr):
    w, h = y.shape[1], y.shape[0]
    if cb.shape != y.shape:
        cb, cr = up420(cb, w, h), up420(cr, w, h)
    yf, u, v = y.astype(np.float32), cb.astype(np.float32) - 128, cr.astype(np.float32) - 128
    r = yf + 1.5748 * v
    g = yf - 0.1873 * u - 0.4681 * v
    b = yf + 1.8556 * u
    return np.clip(np.stack([b, g, r], -1), 0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('clip')
    ap.add_argument('--out', required=True)
    ap.add_argument('--mbps', type=float, nargs='+', default=[500, 700, 1000, 2000])
    ap.add_argument('--hz', type=float, default=207)
    ap.add_argument('--wavelet', nargs='+', default=['haar', '53', '97'])
    ap.add_argument('--chroma', nargs='+', default=['420'])
    ap.add_argument('--frames', type=int, default=0, help='0 = all')
    ap.add_argument('--ppd', type=float, default=2208 / 99.0)
    ap.add_argument('--live', help='Decode this live bitstream tap (.cpu.wave) with the Haar decoder too')
    ap.add_argument('--crop', type=int, nargs=4, metavar=('X', 'Y', 'W', 'H'),
                    help='Write side-by-side crops of this region for visual checks')
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    frames, header = read_y4m(args.clip, args.frames or None)
    src = frames
    h, w = src[0][0].shape
    results = []
    crops = {}

    def record(label, decoded, extra):
        per = [score(s, d, args.ppd) for s, d in zip(src, decoded)]
        mean = {k: float(np.mean([p[k] for p in per])) for k in per[0]}
        mean['hvs_min'] = float(min(p['hvs'] for p in per))
        row = dict(extra, **mean)
        if len(decoded) > 1 and label.split('-')[1] != 'uncoded':
            row['flicker'] = flicker(src, decoded)
        results.append(row)
        print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()}), flush=True)
        if args.crop:
            x, y, cw, ch = args.crop
            rgb = to_rgb(*decoded[0])[y:y + ch, x:x + cw]
            crops[label] = rgb

    if args.crop:
        x, y, cw, ch = args.crop
        crops['source'] = to_rgb(*src[0])[y:y + ch, x:x + cw]

    src_444 = src[0][1].shape == src[0][0].shape
    for chroma in args.chroma:
        if chroma == '444' and not src_444:
            raise SystemExit('4:4:4 configs need a 4:4:4 dump (capture with chroma_444 on)')
        planes = src if chroma == '444' or not src_444 else [(y, box420(cb), box420(cr)) for y, cb, cr in src]
        inp = out / f'input-{chroma}.y4m'
        write_y4m(inp, planes, w, h, '444' if chroma == '444' else '420jpeg', int(args.hz))
        record(f'{chroma}-uncoded', planes, {'config': f'{chroma} uncoded', 'mbps': None, 'wavelet': None})
        for wavelet in args.wavelet:
            for mbps in args.mbps:
                cap = int(mbps * 1e6 / 8 / round(args.hz)) // 4 * 4
                env = dict(os.environ, PYROWAVE_WAVELET=WAVELET_ENV[wavelet])
                enc, dec = out / 'tmp.pyrowave', out / 'tmp-dec.y4m'
                run([str(TOOLS / 'pyrowave-encode.exe'), str(inp), str(enc), str(cap)], env)
                run([str(TOOLS / 'pyrowave-decode.exe'), str(enc), str(dec)], env)
                decoded, _ = read_y4m(dec, len(planes))
                record(f'{chroma}-{wavelet}-{int(mbps)}', decoded,
                       {'config': f'{chroma} {wavelet}', 'mbps': mbps, 'wavelet': wavelet, 'bytes_cap': cap,
                        'coded_bytes': enc.stat().st_size // len(planes)})
    if args.live:
        dec = out / 'live-dec.y4m'
        run([str(TOOLS / 'pyrowave-decode.exe'), args.live, str(dec)], dict(os.environ, PYROWAVE_WAVELET='haar'))
        decoded, _ = read_y4m(dec, 1)
        src_one = src[:1]
        per = score(src_one[0], decoded[0], args.ppd)
        row = dict({'config': 'live bitstream tap', 'mbps': None, 'wavelet': 'haar'}, **per)
        results.append(row)
        print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()}), flush=True)
    (out / 'results.json').write_text(json.dumps({'clip': str(args.clip), 'ppd': args.ppd, 'hz': args.hz,
                                                  'frames': len(src), 'results': results}, indent=2))
    if crops:
        labels = list(crops)
        tiles = []
        for label in labels:
            img = cv2.resize(crops[label], None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
            cv2.putText(img, label, (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 3)
            cv2.putText(img, label, (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 1)
            tiles.append(img)
        cols = 4
        rows = [np.hstack(tiles[i:i + cols] + [np.zeros_like(tiles[0])] * (cols - len(tiles[i:i + cols])))
                for i in range(0, len(tiles), cols)]
        cv2.imwrite(str(out / 'crops.png'), np.vstack(rows))
    for p in out.glob('tmp*'):
        p.unlink()


if __name__ == '__main__':
    main()
