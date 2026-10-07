"""Encoder rate weighting (docs/ENCODER-CSF.md): encode the same Catmull-filtered quality-scene
crops at the live per-eye cap with PYROWAVE_CPD_NYQUIST[:PYROWAVE_CHROMA_CSF] variants, score at
the source size.

  python tools/downsample/csf_study.py <pc tools dir> <out dir> default,11:1.6 1000,1500 [crops]
"""
import json, os, sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.downsample.codec_study import resample, to_ycbcr, upsample
from tools.quest3.quality_scene import build_world
from tools.quest3.quality_score import box420, psnr, psnr_hvs_m, read_y4m, run, write_y4m

tools = Path(sys.argv[1]); out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
variants = sys.argv[3].split(',')  # cpd values, 'default' = unset; optional ':chroma' suffix
mbps_list = [float(x) for x in sys.argv[4].split(',')]
crops = int(sys.argv[5]) if len(sys.argv) > 5 else 2
sw, sh, tw, th, hz = 3072, 3216, 2080, 2208, 207
world = build_world()[:, :, ::-1]
wh, ww = world.shape[:2]
tall = np.concatenate([np.concatenate([world, world], 1)] * 2, 0)
windows = [tall[(k * 1331) % wh:(k * 1331) % wh + sh, (k * 2477) % ww:(k * 2477) % ww + sw] for k in range(crops)]
refs = [to_ycbcr(w.astype(np.float32)) for w in windows]
frames = [to_ycbcr(resample(w, tw, th, 'catmull')) for w in windows]
planes = [(y, box420(cb), box420(cr)) for y, cb, cr in frames]
inp = out / 'input.y4m'
write_y4m(inp, planes, tw, th, '420jpeg', hz)
ppd = sh / 99.0
for mbps in mbps_list:
    cap = int(mbps * 1e6 / 8 / hz / 2) // 4 * 4
    for v in variants:
        env = dict(os.environ, PYROWAVE_WAVELET='haar')
        cpd, _, chroma = v.partition(':')
        env.pop('PYROWAVE_CPD_NYQUIST', None); env.pop('PYROWAVE_CHROMA_CSF', None)
        if cpd != 'default': env['PYROWAVE_CPD_NYQUIST'] = cpd
        if chroma: env['PYROWAVE_CHROMA_CSF'] = chroma
        enc, dec = out / 'tmp.pyrowave', out / 'tmp-dec.y4m'
        run([str(tools / 'pyrowave-encode.exe'), str(inp), str(enc), str(cap)], env)
        run([str(tools / 'pyrowave-decode.exe'), str(enc), str(dec)], env)
        decoded, _ = read_y4m(dec, len(planes))
        per = []
        for (y, cb, cr), (y0, cb0, cr0) in zip(decoded, refs):
            yu, cbu, cru = upsample(y, sw, sh), upsample(cb, sw, sh), upsample(cr, sw, sh)
            per.append({'hvs': psnr_hvs_m(y0, yu, ppd), 'y': psnr(y0, yu), 'cb': psnr(cb0, cbu), 'cr': psnr(cr0, cru)})
        row = {'mbps': mbps, 'variant': v, 'bytes': enc.stat().st_size, **{k: round(float(np.mean([p[k] for p in per])), 3) for k in per[0]}}
        print(json.dumps(row), flush=True)
