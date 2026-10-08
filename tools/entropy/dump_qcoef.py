"""dump_qcoef.py <input.y4m> <out.bin> <frame-bytes>: quantized wavelet coefficients of one frame,
for the entropy coder prototype (qrenc, qrbench).

Luma and both 4:2:0 chroma planes get entropy_model.py's 5-level CDF 5/3 and its norm-scaled
deadzone quantizer, with one step for the frame. The step is chosen so PyroWave's raw packing of
the frame would take <frame-bytes>, so the dump has as many coefficients to code as a live frame
at that rate (1500 Mbit/s at 120 Hz: 1562500).

Output (little-endian): b'QCF1', u32 band count, then per band u32 class, u32 width, u32 height
and width*height int16 coefficients in raster order, each band zero-padded to a multiple of 32. Classes: 0 luma LL, 1 + 3*min(level, 3) + k
for luma detail bands (level 0 finest, k = HL, LH, HH), and 13 + the same for chroma.
"""
import os, struct, sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'downsample'))
import entropy_model as em


def read_planes(path):
    with open(path, 'rb') as f:
        hdr = f.readline().split()
        w = int([t for t in hdr if t.startswith(b'W')][0][1:])
        h = int([t for t in hdr if t.startswith(b'H')][0][1:])
        f.readline()
        y = np.frombuffer(f.read(w * h), np.uint8).reshape(h, w).astype(np.float64)
        u = np.frombuffer(f.read(w * h // 4), np.uint8).reshape(h // 2, w // 2).astype(np.float64)
        v = np.frombuffer(f.read(w * h // 4), np.uint8).reshape(h // 2, w // 2).astype(np.float64)
    return y, u - 128, v - 128


def main():
    planes = read_planes(sys.argv[1])
    target = float(sys.argv[3])
    norms = em.synthesis_norms()
    # pad to a multiple of 32 (edge copies), as PyroWave pads 1104-row chroma
    planes = [np.pad(p, ((0, -p.shape[0] % 32), (0, -p.shape[1] % 32)), mode='edge') for p in planes]
    dw = [em.dwt(p) for p in planes]

    def quant(step):
        out = []
        for pi, (ll, bands) in enumerate(dw):
            base = 0 if pi == 0 else 13
            out.append((base, em.quantize(ll, step / norms['LL'])))
            for lev, trip in enumerate(bands):
                for k, b in enumerate(trip):
                    out.append((base + 1 + 3 * min(lev, 3) + k, em.quantize(b, step / norms[(lev, k)])))
        return out

    def raw_bytes(step):
        return sum(em.raw_cost_bits(q) for _, q in quant(step)) / 8

    lo, hi = 0.5, 512.0
    for _ in range(18):
        mid = (lo * hi) ** 0.5
        if raw_bytes(mid) > target:
            lo = mid
        else:
            hi = mid
    step = hi
    bands = quant(step)
    with open(sys.argv[2], 'wb') as f:
        f.write(b'QCF1' + struct.pack('<I', len(bands)))
        for cls, q in bands:
            # bands are coded in 32x32 blocks: pad the coarse ones with zeros
            q = np.pad(q, ((0, -q.shape[0] % 32), (0, -q.shape[1] % 32)))
            q = np.clip(q, -32767, 32767).astype('<i2')
            f.write(struct.pack('<III', cls, q.shape[1], q.shape[0]))
            f.write(q.tobytes())
    n = sum(q.size for _, q in bands)
    nz = sum(int((q != 0).sum()) for _, q in bands)
    print(f'step {step:.3f}: raw {raw_bytes(step):.0f} bytes, {len(bands)} bands, {n} coefficients, {nz} nonzero')


if __name__ == '__main__':
    main()
