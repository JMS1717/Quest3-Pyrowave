"""entropy_qr.py <input.y4m>: cost of a GPU-friendly block coder against PyroWave's raw packing.

Luma only, with entropy_model.py's transform, quantizer and costings. The coder ("QR") is built to
decode with one GPU thread per 32x32 block and table lookups:

  - Each 32x32 block of a band is an independent bit stream. Its byte length goes in a table
    (16 bits per non-empty block, one bit per empty one), so blocks decode in parallel.
  - Inside a block, one bit per 8x8 marks it non-empty; then, in non-empty 8x8s, 2x2 quads in raster order. A quad's 4-bit significance pattern is coded with a
    prefix code chosen by context: the largest magnitude of the quad to the left and of the quad
    above (each clamped to 3), within the block. 16 contexts per band class.
  - Each nonzero coefficient: |q| - 1 with a Rice code whose parameter is chosen by the sum of
    those two neighbour maxima (bucketed), then a raw sign bit. Unary runs over 15 escape to a
    16-bit literal.
  - Code tables are per frame (two-pass): prefix code lengths and Rice parameters go in a header.

Prints, per quantizer step, the bytes for raw, the context bound (ctx) and QR, then the PSNR each
reaches at the live byte caps.
"""
import heapq, math, sys
import numpy as np

import os

import entropy_model as em

# Variant letters (QR_VARIANT): P = parent coefficient in the pattern context, N = number of
# nonzeros in the quad in the Rice context, C = Rice context from the coefficient's own left and
# up neighbours instead of the neighbouring quads' maxima. R = static rANS instead of prefix and
# Rice codes: patterns at their per-context entropy, magnitudes as a 16-symbol class (below) at
# its per-context entropy plus raw low bits, 12-bit frequencies (16 per context) in the header.
VARIANT = os.environ.get('QR_VARIANT', '')

KBUCKETS = [1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 128]


def huffman_lengths(counts, max_len=12):
    """Code lengths for symbol counts (zeros get no code); length-limited by flattening."""
    syms = [(c, i) for i, c in enumerate(counts) if c > 0]
    lengths = [0] * len(counts)
    if len(syms) == 1:
        lengths[syms[0][1]] = 1
        return lengths
    heap = [(c, n, [i]) for n, (c, i) in enumerate(syms)]
    heapq.heapify(heap)
    n = len(heap)
    while len(heap) > 1:
        c1, _, s1 = heapq.heappop(heap)
        c2, _, s2 = heapq.heappop(heap)
        for i in s1 + s2:
            lengths[i] += 1
        n += 1
        heapq.heappush(heap, (c1 + c2, n, s1 + s2))
    if max(lengths) > max_len:
        # crude limit: flatten counts and retry
        return huffman_lengths([c ** 0.5 if c else 0 for c in counts], max_len)
    return lengths


def mag_class(m):
    """16-symbol magnitude alphabet for rANS: m < 4 directly; then exponent e >= 2 and the bit
    below the leading one (two classes per exponent), e - 1 raw low bits; class 15 escapes to 16
    raw bits."""
    m = np.asarray(m, np.int64)
    e = np.floor(np.log2(np.maximum(m, 1))).astype(np.int64)
    sub = (m >> np.maximum(e - 1, 0)) & 1
    cls = np.where(m < 4, m, 4 + 2 * (e - 2) + sub)
    extra = np.where(m < 4, 0, e - 1)
    esc = cls >= 15
    return np.where(esc, 15, cls), np.where(esc, 16, extra)


def entropy_bits(counts):
    counts = np.asarray(counts, np.float64)
    c = counts[counts > 0]
    return float(-(c * np.log2(c / c.sum())).sum()) if c.size else 0.0


def rice_bits(m, k):
    """Bits of Rice(k) for nonnegative ints m, with escape over 15 unary ones."""
    q = m >> k
    return np.where(q < 15, q + 1 + k, 15 + 16)


class Coder:
    def __init__(self):
        self.pattern = {}   # (cls, ctx) -> counts[16]
        self.mag = {}       # (cls, kc) -> list of arrays of m
        self.fixed_bits = 0

    def add_band(self, q, cls, parent=None):
        a = np.abs(q).astype(np.int64)
        h, w = a.shape
        H, W = -(-h // 32) * 32, -(-w // 32) * 32
        p = np.zeros((H, W), np.int64)
        p[:h, :w] = a
        a = p
        # block table: 1 bit per block, 16 more per non-empty block
        blocks = a.reshape(H // 32, 32, W // 32, 32).max(axis=(1, 3))
        self.fixed_bits += blocks.size + 16 * int((blocks > 0).sum())
        nz = a > 0
        # inside a non-empty block, one bit per 8x8 says whether it has any nonzero; quads are
        # coded only in non-empty 8x8s
        b8 = a.reshape(H // 8, 8, W // 8, 8).max(axis=(1, 3)) > 0
        self.fixed_bits += 16 * int((blocks > 0).sum())
        live = np.repeat(np.repeat(b8, 4, 0), 4, 1)
        qm = a.reshape(H // 2, 2, W // 2, 2).max(axis=(1, 3))
        pat = (nz[0::2, 0::2] * 1 + nz[0::2, 1::2] * 2 + nz[1::2, 0::2] * 4 + nz[1::2, 1::2] * 8)
        left = np.zeros_like(qm)
        left[:, 1:] = qm[:, :-1]
        left[:, 0::16] = 0
        up = np.zeros_like(qm)
        up[1:, :] = qm[:-1, :]
        up[0::16, :] = 0
        ctx = np.minimum(left, 3) * 4 + np.minimum(up, 3)
        nctx = 16
        if 'P' in VARIANT:
            par = np.zeros_like(qm)
            if parent is not None:
                pa = np.abs(parent).astype(np.int64)[: qm.shape[0], : qm.shape[1]]
                par[: pa.shape[0], : pa.shape[1]] = pa
            ctx = ctx * 3 + np.minimum(par, 2)
            nctx = 48
        key = (ctx * 16 + pat)[live]
        cnt = np.bincount(key, minlength=nctx * 16).reshape(nctx, 16)
        for c in range(nctx):
            acc = self.pattern.setdefault((cls, c), np.zeros(16, np.int64))
            acc += cnt[c]
        # magnitudes of nonzero coefficients, context = bucket(left + up) of their quad
        kc = np.digitize(left + up, KBUCKETS)
        kc_full = np.repeat(np.repeat(kc, 2, 0), 2, 1)
        if 'C' in VARIANT:
            pa = np.pad(a, ((1, 0), (1, 0)))
            l1, u1 = pa[1:, :-1].copy(), pa[:-1, 1:].copy()
            l1[:, 0::32] = 0
            u1[0::32, :] = 0
            kc_full = np.digitize(2 * l1 + 2 * u1 + np.repeat(np.repeat(left + up, 2, 0), 2, 1), KBUCKETS)
        if 'N' in VARIANT:
            ns = nz.reshape(H // 2, 2, W // 2, 2).sum(axis=(1, 3))
            kc_full = kc_full * 4 + np.repeat(np.repeat(ns - 1, 2, 0), 2, 1).clip(0, 3)
        sel = nz & np.repeat(np.repeat(live, 2, 0), 2, 1)
        m = a[sel] - 1
        kk = kc_full[sel]
        for c in np.unique(kk):
            self.mag.setdefault((cls, int(c)), []).append(m[kk == c])
        self.fixed_bits += int(sel.sum())  # signs

    def bits(self):
        if 'R' in VARIANT:
            total = self.fixed_bits
            for counts in self.pattern.values():
                if counts.sum():
                    total += entropy_bits(counts) * 1.003 + 16 * 12
            for parts in self.mag.values():
                cls, extra = mag_class(np.concatenate(parts))
                total += entropy_bits(np.bincount(cls, minlength=16)) * 1.003 + int(extra.sum()) + 16 * 12
            return int(total)
        total = self.fixed_bits
        for counts in self.pattern.values():
            if counts.sum() == 0:
                continue
            lens = huffman_lengths(counts.tolist())
            total += int((np.array(lens) * counts).sum()) + 16 * 4  # lengths table
        for parts in self.mag.values():
            m = np.concatenate(parts)
            best = min(int(rice_bits(m, k).sum()) for k in range(0, 13))
            total += best + 4
        return total


def qr_cost_bits(qll, qb):
    c = Coder()
    c.add_band(qll, 'LL')
    for lev, trip in enumerate(qb):
        for k, b in enumerate(trip):
            c.add_band(b, (min(lev, 3), k), qb[lev + 1][k] if lev + 1 < len(qb) else None)
    return c.bits()


def main():
    img = em.read_y(sys.argv[1], 1)[0]
    norms = em.synthesis_norms()
    rows = []
    for st in [2 ** (e / 4) for e in range(int(os.environ.get('QR_E0', 0)), int(os.environ.get('QR_E1', 33)))]:
        ll, bands = em.dwt(img)
        qll = em.quantize(ll, st / norms['LL'])
        qb = [tuple(em.quantize(b, st / norms[(lev, k)]) for k, b in enumerate(trip)) for lev, trip in enumerate(bands)]
        raw = (em.raw_cost_bits(qll) + sum(em.raw_cost_bits(b) for trip in qb for b in trip)) / 8
        ctx = em.ctx_cost_bits(qb, qll) / 8
        qr = qr_cost_bits(qll, qb) / 8
        rec = em.idwt(em.dequantize(qll, st / norms['LL']),
                      [tuple(em.dequantize(b, st / norms[(lev, k)]) for k, b in enumerate(trip)) for lev, trip in enumerate(qb)])
        mse = ((np.clip(rec, 0, 255) - img) ** 2).mean()
        ps = 10 * math.log10(255 ** 2 / max(mse, 1e-9))
        rows.append((st, raw, ctx, qr, ps))
        print(f'step {st:7.2f} raw {raw / 1e3:8.1f} KB ctx {ctx / 1e3:8.1f} KB ({100 * (1 - ctx / raw):5.1f}%) '
              f'qr {qr / 1e3:8.1f} KB ({100 * (1 - qr / raw):5.1f}%) psnr {ps:6.2f}', flush=True)
    for label, mbps, hz in (('207/1000', 1000, 207), ('207/1500', 1500, 207), ('120/1500', 1500, 120), ('120/1000', 1000, 120)):
        cap = mbps * 1e6 / 8 / hz / 2 * 0.75 * img.shape[1] / 2080

        def at(idx):
            pts = sorted((r[idx], r[4]) for r in rows)
            for (c0, p0), (c1, p1) in zip(pts, pts[1:]):
                if c0 <= cap <= c1:
                    return p0 + (p1 - p0) * (cap - c0) / (c1 - c0)
            return float('nan')
        pr, pc, pq = at(1), at(2), at(3)
        print(f'{label}: luma cap {cap / 1e3:.0f} KB  raw {pr:.2f}  ctx {pc - pr:+.2f} dB  qr {pq - pr:+.2f} dB')


if __name__ == '__main__':
    main()
