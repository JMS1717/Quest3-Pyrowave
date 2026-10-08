"""entropy_model.py <input.y4m> [frames]: what entropy coding would be worth over PyroWave's raw packing.

Luma only. 5-level CDF 5/3 (lifting, symmetric edges), deadzone quantizer with one step scaled
per band by its synthesis norm (equal MSE weight). The same quantized coefficients are costed two
ways:
  raw   PyroWave's layout (block_packing.comp): per active 8x8 a 3-byte control word; per 4x2
        group (q_bits + extra) planes x 8 coefficients, extra 0-3 over the 8x8's q_bits; one sign
        bit per nonzero; 8 bytes per active 32x32 block.
  ctx   static conditional entropy of min(|q|, 15) given band class and a context from the
        already-coded neighbours (left, up, up-left, up-right) and the parent band, plus
        Exp-Golomb bits above 15 and one sign bit per nonzero. A two-pass bound: an adaptive coder
        lands a few percent above it.
Then, at each byte cap, the step that fits under each costing and the PSNR it reconstructs at.
"""
import math, sys
import numpy as np

LEVELS = 5


def read_y(path, n):
    with open(path, 'rb') as f:
        hdr = f.readline().split()
        w = int([t for t in hdr if t.startswith(b'W')][0][1:]); h = int([t for t in hdr if t.startswith(b'H')][0][1:])
        out = []
        for _ in range(n):
            if not f.readline():
                break
            y = np.frombuffer(f.read(w * h), np.uint8).reshape(h, w)
            f.read(w * h // 2)
            out.append(y.astype(np.float64))
    return out


def fwd1(x, axis):
    x = np.moveaxis(x, axis, 0)
    e, o = x[0::2].copy(), x[1::2].copy()
    er = np.concatenate([e[1:], e[-1:]], 0)
    d = o - 0.5 * (e + er)
    dl = np.concatenate([d[:1], d[:-1]], 0)
    s = e + 0.25 * (dl + d)
    return np.moveaxis(s, 0, axis), np.moveaxis(d, 0, axis)


def inv1(s, d, axis):
    s = np.moveaxis(s, axis, 0); d = np.moveaxis(d, axis, 0)
    dl = np.concatenate([d[:1], d[:-1]], 0)
    e = s - 0.25 * (dl + d)
    er = np.concatenate([e[1:], e[-1:]], 0)
    o = d + 0.5 * (e + er)
    x = np.empty((s.shape[0] * 2,) + s.shape[1:])
    x[0::2] = e; x[1::2] = o
    return np.moveaxis(x, 0, axis)


def dwt(img):
    bands = []  # per level: (HL, LH, HH), finest first
    ll = img
    for _ in range(LEVELS):
        l, h = fwd1(ll, 1)
        ll_, lh = fwd1(l, 0)
        hl, hh = fwd1(h, 0)
        bands.append((hl, lh, hh))
        ll = ll_
    return ll, bands


def idwt(ll, bands):
    for hl, lh, hh in reversed(bands):
        l = inv1(ll, lh, 0)
        h = inv1(hl, hh, 0)
        ll = inv1(l, h, 1)
    return ll


def synthesis_norms(shape=(256, 256)):
    z = np.zeros(shape)
    ll, bands = dwt(z)
    norms = {}
    def norm(setter):
        ll0, b0 = dwt(np.zeros(shape))
        setter(ll0, b0)
        return math.sqrt((idwt(ll0, b0) ** 2).sum())
    def set_ll(l, b):
        l[l.shape[0] // 2, l.shape[1] // 2] = 1
    norms['LL'] = norm(set_ll)
    for lev in range(LEVELS):
        for k in range(3):
            def s(l, b, lev=lev, k=k):
                band = b[lev][k]; band[band.shape[0] // 2, band.shape[1] // 2] = 1
            norms[(lev, k)] = norm(s)
    return norms


def quantize(c, step):
    return np.sign(c) * np.floor(np.abs(c) / step)


def dequantize(q, step):
    return np.sign(q) * (np.abs(q) + 0.5) * step * (q != 0)


def bitlen(a):
    a = a.astype(np.int64)
    out = np.zeros(a.shape, np.int64)
    v = a.copy()
    while (v > 0).any():
        out += v > 0
        v >>= 1
    return out


def raw_cost_bits(q):
    """q: 2-D band of quantized ints (dims multiples of 8)."""
    a = np.abs(q).astype(np.int64)
    h, w = a.shape
    h8, w8 = h // 8 * 8, w // 8 * 8
    if h8 == 0 or w8 == 0:
        # tiny band (coarse level): pad to 8
        pad = np.zeros((max(8, h8 or 8), max(8, w8 or 8)), np.int64)
        pad[:h, :w] = a
        a = pad; h, w = a.shape; h8, w8 = h, w
    a = a[:h8, :w8]
    # 4x2 groups: 4 wide, 2 tall
    g = a.reshape(h8 // 2, 2, w8 // 4, 4).max(axis=(1, 3))
    gp = bitlen(g)  # planes per group
    # 8x8 = 4 group-rows x 2 group-cols
    gp8 = gp.reshape(h8 // 8, 4, w8 // 8, 2)
    mx = gp8.max(axis=(1, 3))
    qb = np.maximum(mx - 3, 0)
    planes = np.maximum(gp8, qb[:, None, :, None])
    mag_bits = (planes.sum(axis=(1, 3)) * 8)
    active = mx > 0
    bits = (mag_bits * active).sum() + active.sum() * 24
    # 32x32 headers
    act32 = active[: active.shape[0] // 4 * 4, : active.shape[1] // 4 * 4]
    if act32.size:
        act32 = act32.reshape(act32.shape[0] // 4, 4, act32.shape[1] // 4, 4).any(axis=(1, 3))
        bits += act32.sum() * 64
    bits += (a != 0).sum()  # signs
    return int(bits)


def ctx_cost_bits(qbands, qll):
    """Static conditional entropy with neighbour + parent context, per band class."""
    total = 0.0
    def ctx_of(a, parent):
        m = np.minimum(a, 15)
        p = np.pad(m, ((1, 0), (1, 1)))
        left, up, ul, ur = p[1:, :-2], p[:-1, 1:-1], p[:-1, :-2], p[:-1, 2:]
        s = 2 * left + 2 * up + ul + ur
        c = np.digitize(s, [1, 2, 3, 5, 8, 12, 20, 40])
        if parent is not None:
            pc = np.minimum(np.repeat(np.repeat(parent, 2, 0), 2, 1)[: a.shape[0], : a.shape[1]], 2)
            c = c * 3 + pc
        return c
    def entropy_bits(sym, ctx):
        bits = 0.0
        key = ctx.ravel() * 16 + sym.ravel()
        cnt = np.bincount(key, minlength=(ctx.max() + 1) * 16).reshape(-1, 16).astype(np.float64)
        tot = cnt.sum(1, keepdims=True)
        with np.errstate(divide='ignore', invalid='ignore'):
            p = np.where(cnt > 0, cnt / tot, 1)
        bits = -(cnt * np.log2(p)).sum()
        # model description: ~ 4 bits per nonzero count (generous)
        bits += (cnt > 0).sum() * 4
        return bits
    def escape_bits(a):
        big = a[a >= 15] - 15
        return (2 * np.floor(np.log2(big + 1)) + 1).sum() if big.size else 0
    a_ll = np.abs(qll).astype(np.int64)
    total += entropy_bits(np.minimum(a_ll, 15), ctx_of(a_ll, None)) + escape_bits(a_ll) + (a_ll != 0).sum()
    for lev in range(LEVELS - 1, -1, -1):
        for k in range(3):
            a = np.abs(qbands[lev][k]).astype(np.int64)
            parent = np.abs(qbands[lev + 1][k]).astype(np.int64) if lev + 1 < LEVELS else None
            total += entropy_bits(np.minimum(a, 15), ctx_of(a, parent)) + escape_bits(a) + (a != 0).sum()
    return total


def group_cost_bits(q, cls, acc):
    """PyroWave structure kept (control word, header, signs as raw); magnitudes coded with a static
    code per (band class, group plane count n): accumulate counts in acc[(cls, n)] -> histogram."""
    a = np.abs(q).astype(np.int64)
    h, w = a.shape
    if h < 8 or w < 8:
        pad = np.zeros((max(8, h), max(8, w)), np.int64); pad[:h, :w] = a; a = pad; h, w = a.shape
    h8, w8 = h // 8 * 8, w // 8 * 8
    a = a[:h8, :w8]
    g = a.reshape(h8 // 2, 2, w8 // 4, 4)
    gmax = g.max(axis=(1, 3))
    n = bitlen(gmax)  # group planes
    gp8 = n.reshape(h8 // 8, 4, w8 // 8, 2)
    mx = gp8.max(axis=(1, 3))
    active = mx > 0
    overhead = active.sum() * 24 + (a != 0).sum()
    act32 = active[: active.shape[0] // 4 * 4, : active.shape[1] // 4 * 4]
    if act32.size:
        overhead += act32.reshape(act32.shape[0] // 4, 4, act32.shape[1] // 4, 4).any(axis=(1, 3)).sum() * 64
    # every coefficient of a group with n planes: symbol = its magnitude (< 2^n)
    nn = np.repeat(np.repeat(n, 2, 0), 4, 1)
    for planes in np.unique(nn):
        if planes == 0:
            continue
        vals = a[nn == planes]
        key = (cls, int(min(planes, 12)))
        hist = acc.setdefault(key, {})
        # magnitudes above 255 rare: bucket by value
        u, c = np.unique(np.minimum(vals, 4095), return_counts=True)
        for v, k in zip(u.tolist(), c.tolist()):
            hist[v] = hist.get(v, 0) + k
    return overhead


def hist_bits(acc):
    bits = 0.0
    for hist in acc.values():
        c = np.array(list(hist.values()), np.float64)
        p = c / c.sum()
        bits += -(c * np.log2(p)).sum() + len(c) * 4
    return bits


def code(img, norms, step):
    ll, bands = dwt(img)
    qll = quantize(ll, step / norms['LL'])
    qb = [tuple(quantize(b, step / norms[(lev, k)]) for k, b in enumerate(trip)) for lev, trip in enumerate(bands)]
    raw = raw_cost_bits(qll) + sum(raw_cost_bits(b) for trip in qb for b in trip)
    ctx = ctx_cost_bits(qb, qll)
    acc = {}
    grp = group_cost_bits(qll, 'LL', acc)
    for lev, trip in enumerate(qb):
        for k, b in enumerate(trip):
            grp += group_cost_bits(b, (min(lev, 3), k), acc)
    grp += hist_bits(acc)
    code.last_group = grp / 8
    rec = idwt(dequantize(qll, step / norms['LL']),
               [tuple(dequantize(b, step / norms[(lev, k)]) for k, b in enumerate(trip)) for lev, trip in enumerate(qb)])
    mse = ((np.clip(rec, 0, 255) - img) ** 2).mean()
    return raw / 8, ctx / 8, 10 * math.log10(255 ** 2 / max(mse, 1e-9))


def main():
    path = sys.argv[1]
    frames = read_y(path, int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    norms = synthesis_norms()
    img = frames[0]
    steps = [2 ** (e / 4) for e in range(0, 33)]
    rows = []
    for st in steps:
        raw, ctx, ps = code(img, norms, st)
        grp = code.last_group
        rows.append((st, raw, ctx, ps, grp))
        print(f'step {st:7.2f} raw {raw / 1e3:8.1f} KB ctx {ctx / 1e3:8.1f} KB ({100 * (1 - ctx / raw):5.1f}%) '
              f'group {grp / 1e3:8.1f} KB ({100 * (1 - grp / raw):5.1f}%) psnr {ps:6.2f}', flush=True)
    # luma share of the per-eye cap: 4:2:0 has 1.5 samples per pixel; assume luma takes ~75% of bytes.
    for label, mbps, hz in (('207/1000', 1000, 207), ('207/1500', 1500, 207), ('120/1500', 1500, 120)):
        cap = mbps * 1e6 / 8 / hz / 2 * 0.75 * img.shape[1] / 2080  # per-eye cap; stereo frames hold two eyes
        def at(idx):
            # interpolate PSNR at cap for cost column idx (costs fall as step grows)
            pts = sorted(((r[idx], r[3]) for r in rows))
            for (c0, p0), (c1, p1) in zip(pts, pts[1:]):
                if c0 <= cap <= c1:
                    return p0 + (p1 - p0) * (cap - c0) / (c1 - c0)
            return float('nan')
        pr, pc, pg = at(1), at(2), at(4)
        print(f'{label}: luma cap {cap / 1e3:.0f} KB  raw psnr {pr:.2f}  ctx {pc:.2f} ({pc - pr:+.2f} dB)  group {pg:.2f} ({pg - pr:+.2f} dB)')


if __name__ == '__main__':
    main()
