"""Block steps for csf_study.py outputs (docs/ENCODER-CSF.md section 5): the mean absolute step of the
luma coding error across 8/16/32-pixel boundaries over the mean step inside them, where the encoder
input is flat (smoothed Laplacian below 0.5), and the flat-area luma PSNR after the error is
resampled to the panel's 2080x2208 (d_flat).

  python tools/downsample/block_steps.py <csf_study out dir>
"""
import sys
from pathlib import Path
import numpy as np
import cv2
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.quest3.quality_score import read_y4m
from tools.downsample.codec_study import resample

out = Path(sys.argv[1])
inp, _ = read_y4m(out / 'input.y4m', 2)
for dec_path in sorted(out.glob('dec-*.y4m')):
    dec, _ = read_y4m(dec_path, len(inp))
    res = {n: [] for n in (8, 16, 32)}
    dsm = []
    for (y0, _, _), (y1, _, _) in zip(inp, dec):
        a = y0.astype(np.float32)
        e = y1.astype(np.float32) - a
        lap = np.abs(cv2.Laplacian(cv2.GaussianBlur(a, (5, 5), 1.0), cv2.CV_32F))
        flat = cv2.erode((lap < 0.5).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        for n in res:
            sx = np.abs(np.diff(e, axis=1)); fx = flat[:, 1:] & flat[:, :-1]
            sy = np.abs(np.diff(e, axis=0)); fy = flat[1:, :] & flat[:-1, :]
            bx = np.zeros(sx.shape[1], bool); bx[n - 1::n] = True
            by = np.zeros(sy.shape[0], bool); by[n - 1::n] = True
            at = np.concatenate([sx[:, bx][fx[:, bx]], sy[by, :][fy[by, :]]])
            ins = np.concatenate([sx[:, ~bx][fx[:, ~bx]], sy[~by, :][fy[~by, :]]])
            res[n].append((at.mean(), ins.mean()))
        # Display-referred: error resampled to the panel, RMS over flat pixels there.
        ed = resample(np.dstack([e] * 3) + 128, 2080, 2208, 'catmull')[:, :, 0] - 128
        fd = cv2.resize(flat.astype(np.uint8), (2080, 2208), interpolation=cv2.INTER_NEAREST).astype(bool)
        dsm.append(10 * np.log10(255 ** 2 / max(np.mean(ed[fd] ** 2), 1e-9)))
    cells = ' '.join(f'{n}px {np.mean([a for a, _ in v]):.3f}/{np.mean([b for _, b in v]):.3f}={np.mean([a for a, _ in v]) / np.mean([b for _, b in v]):.2f}' for n, v in res.items())
    print(f'{dec_path.name:32s} flat {flat.mean():.2f} {cells} d_flat {np.mean(dsm):.2f}', flush=True)
