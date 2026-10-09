"""Encoder rate weighting (docs/ENCODER-CSF.md): encode the same Catmull-filtered quality-scene
crops at the live per-eye cap with PYROWAVE_CPD_NYQUIST[:PYROWAVE_CHROMA_CSF] variants, score at
the source size.

  python tools/downsample/csf_study.py <pc tools dir> <out dir> default,11:1.6 1000,1500 [crops]

STUDY_FULL=1 encodes the full 3072x3216 crop (no downsample) at the 120 Hz cap, and
STUDY_STREAM=WxH (e.g. 2592x2784) a supersampled stream at the 120 Hz cap. Both add
display-referred scores ('d_' keys): source and decode both resampled with the live Catmull-Rom
filter to 2080x2208, the panel's rows, and PSNR-HVS-M at its pixels per degree.
"""
import json, os, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.downsample.codec_study import resample, to_ycbcr, upsample
from tools.quest3.quality_scene import build_world
from tools.quest3.quality_score import box420, psnr, psnr_hvs_m, read_y4m, run, write_y4m

tools = Path(sys.argv[1]); out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
variants = sys.argv[3].split(',')  # cpd[:chroma[:aq_offset/aq_strength]], 'default' = unset
mbps_list = [float(x) for x in sys.argv[4].split(',')]
crops = int(sys.argv[5]) if len(sys.argv) > 5 else 2
sw, sh, tw, th, hz = 3072, 3216, 2080, 2208, 207
full = os.environ.get('STUDY_FULL') == '1' or bool(os.environ.get('STUDY_STREAM'))
dw, dh = tw, th  # display-referred size
if full:
    tw, th = (int(v) for v in os.environ.get('STUDY_STREAM', f'{sw}x{sh}').split('x'))
    hz = 120
world = build_world()[:, :, ::-1]
wh, ww = world.shape[:2]
tall = np.concatenate([np.concatenate([world, world], 1)] * 2, 0)
windows = [tall[(k * 1331) % wh:(k * 1331) % wh + sh, (k * 2477) % ww:(k * 2477) % ww + sw] for k in range(crops)]
refs = [to_ycbcr(w.astype(np.float32)) for w in windows]


def box_mean(a, r):
    c = np.cumsum(np.cumsum(np.pad(a, ((r + 1, r), (r + 1, r)), mode='edge'), 0), 1)
    k = 2 * r + 1
    return (c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)


# Smooth regions (gradients, skies, the scene's soft blobs): where coarse-band quantization shows as
# Haar blocks. Local luma standard deviation over 31x31 source pixels below 3 code values.
masks = []
for y0, _, _ in refs:
    y0 = y0.astype(np.float64)
    std = np.sqrt(np.maximum(box_mean(y0 * y0, 15) - box_mean(y0, 15) ** 2, 0))
    masks.append(std < 3.0)
# The live filter is Catmull-Rom; STUDY_FILTER picks another codec_study kernel (lanczos3, ...).
filt = os.environ.get('STUDY_FILTER', 'catmull')
frames = [to_ycbcr(resample(w, tw, th, filt)) for w in windows]
planes = [(y, box420(cb), box420(cr)) for y, cb, cr in frames]
inp = out / 'input.y4m'
write_y4m(inp, planes, tw, th, '420jpeg', hz)
ppd = sh / 99.0
for mbps in mbps_list:
    cap = int(mbps * 1e6 / 8 / hz / 2) // 4 * 4
    for v in variants:
        # STUDY_WAVELET=53 studies CDF 5/3 (the PC tools' PYROWAVE_WAVELET); Haar by default.
        env = dict(os.environ, PYROWAVE_WAVELET=os.environ.get('STUDY_WAVELET', 'haar'))
        cpd, chroma, aq, lf = (v.split(':') + ['', '', ''])[:4]
        for key in ('PYROWAVE_CPD_NYQUIST', 'PYROWAVE_CHROMA_CSF', 'PYROWAVE_AQ', 'PYROWAVE_LF_BOOST'):
            env.pop(key, None)
        if cpd != 'default': env['PYROWAVE_CPD_NYQUIST'] = cpd
        if chroma: env['PYROWAVE_CHROMA_CSF'] = chroma
        if aq: env['PYROWAVE_AQ'] = aq.replace('/', ',')  # offset/strength (wavelet_quant.comp)
        if lf: env['PYROWAVE_LF_BOOST'] = lf.replace('/', ',')  # level/factor
        enc, dec = out / 'tmp.pyrowave', out / f"dec-{mbps:g}-{v.replace(':', '_').replace('/', '-')}.y4m"
        run([str(tools / 'pyrowave-encode.exe'), str(inp), str(enc), str(cap)], env)
        run([str(tools / 'pyrowave-decode.exe'), str(enc), str(dec)], env)
        decoded, _ = read_y4m(dec, len(planes))
        per = []
        for (y, cb, cr), (y0, cb0, cr0) in zip(decoded, refs):
            yu, cbu, cru = upsample(y, sw, sh), upsample(cb, sw, sh), upsample(cr, sw, sh)
            m = masks[len(per)]
            smooth = lambda a, b: float(10 * np.log10(255 ** 2 / max(np.mean((a[m].astype(np.float64) - b[m]) ** 2), 1e-9)))
            d = {}
            if full:
                # The filter is linear, so resampling the Y/Cb/Cr planes equals resampling RGB.
                to_d = lambda planes: [np.clip(np.rint(resample(q, dw, dh, 'catmull')), 0, 255).astype(np.uint8) for q in planes]
                ref_d, dec_d = to_d((y0, cb0, cr0)), to_d((yu, cbu, cru))
                d = {'d_hvs': psnr_hvs_m(ref_d[0], dec_d[0], dh / 99.0), 'd_y': psnr(ref_d[0], dec_d[0]),
                     'd_cb': psnr(ref_d[1], dec_d[1]), 'd_cr': psnr(ref_d[2], dec_d[2])}
            per.append({**d, 'hvs': psnr_hvs_m(y0, yu, ppd), 'y': psnr(y0, yu), 'cb': psnr(cb0, cbu), 'cr': psnr(cr0, cru),
                        'y_smooth': smooth(y0, yu), 'c_smooth': (smooth(cb0, cbu) + smooth(cr0, cru)) / 2,
                        'smooth_frac': float(m.mean())})
        row = {'mbps': mbps, 'filter': filt, 'variant': v, 'bytes': enc.stat().st_size, **{k: round(float(np.mean([p[k] for p in per])), 3) for k in per[0]}}
        print(json.dumps(row), flush=True)
