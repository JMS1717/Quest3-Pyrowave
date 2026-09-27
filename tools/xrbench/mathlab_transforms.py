"""Transforms for the mathematical compression research, in plain numpy.

Every transform here is exposed as a pair `forward(x) -> coeffs, inverse(coeffs) -> x` on a 2-D
float32 array, plus a `bands(coeffs)` view that names the coefficient groups a quantiser would
treat separately. Wavelets are lifting or filter-bank implementations (no pywt on the PC);
the 9/7 uses PyroWave's own constants (pyrowave `shaders/dwt_common.h`). Block transforms are
orthonormal so a uniform quantiser step means the same reconstruction error everywhere; the
biorthogonal wavelets are given per-band synthesis gains (`band_gains`) so the same holds for
them after weighting. Nothing here is optimised for speed; it is a laboratory, not a decoder.
"""
import numpy as np

# PyroWave's CDF 9/7 lifting constants (shaders/dwt_common.h:37-42).
ALPHA = -1.586134342059924
BETA = -0.052980118572961
GAMMA = 0.882911075530934
DELTA = 0.443506852043971
K = 1.230174104914001

LEVELS = 5


# ---- 1-D lifting along the last axis, symmetric (whole-sample) extension ------------------------

def _sym(x, axis, pad):
    return np.pad(x, [(0, 0)] * (x.ndim - 1) + [(pad, pad)] if axis == -1 else None, mode="reflect")


def _lift_forward_97(x):
    """x: (..., n) with n even. Returns (low, high) each (..., n/2)."""
    e, o = x[..., 0::2].copy(), x[..., 1::2].copy()
    def nxt(a):  # a[i+1] with symmetric extension at the end
        return np.concatenate([a[..., 1:], a[..., -1:]], axis=-1)
    def prv(a):  # a[i-1] with symmetric extension at the start
        return np.concatenate([a[..., :1], a[..., :-1]], axis=-1)
    o += ALPHA * (e + nxt(e))
    e += BETA * (o + prv(o))
    o += GAMMA * (e + nxt(e))
    e += DELTA * (o + prv(o))
    return e / K, o * K


def _lift_inverse_97(low, high):
    e, o = low * K, high / K
    def nxt(a):
        return np.concatenate([a[..., 1:], a[..., -1:]], axis=-1)
    def prv(a):
        return np.concatenate([a[..., :1], a[..., :-1]], axis=-1)
    e -= DELTA * (o + prv(o))
    o -= GAMMA * (e + nxt(e))
    e -= BETA * (o + prv(o))
    o -= ALPHA * (e + nxt(e))
    out = np.empty(low.shape[:-1] + (low.shape[-1] * 2,), dtype=low.dtype)
    out[..., 0::2], out[..., 1::2] = e, o
    return out


def _lift_forward_53(x):
    e, o = x[..., 0::2].copy(), x[..., 1::2].copy()
    nxt = lambda a: np.concatenate([a[..., 1:], a[..., -1:]], axis=-1)
    prv = lambda a: np.concatenate([a[..., :1], a[..., :-1]], axis=-1)
    o -= 0.5 * (e + nxt(e))
    e += 0.25 * (o + prv(o))
    return e, o


def _lift_inverse_53(low, high):
    e, o = low.copy(), high.copy()
    nxt = lambda a: np.concatenate([a[..., 1:], a[..., -1:]], axis=-1)
    prv = lambda a: np.concatenate([a[..., :1], a[..., :-1]], axis=-1)
    e -= 0.25 * (o + prv(o))
    o += 0.5 * (e + nxt(e))
    out = np.empty(low.shape[:-1] + (low.shape[-1] * 2,), dtype=low.dtype)
    out[..., 0::2], out[..., 1::2] = e, o
    return out


def _lift_forward_haar(x):
    e, o = x[..., 0::2], x[..., 1::2]
    r = np.sqrt(np.float32(2.0))
    return (e + o) / r, (e - o) / r


def _lift_inverse_haar(low, high):
    r = np.sqrt(np.float32(2.0))
    e, o = (low + high) / r, (low - high) / r
    out = np.empty(low.shape[:-1] + (low.shape[-1] * 2,), dtype=low.dtype)
    out[..., 0::2], out[..., 1::2] = e, o
    return out


def _lift_forward_haar_lift(x):
    """Haar in PyroWave's lifting normalisation (Experiment 3): d = odd - even, a = even + d/2.
    Unit DC gain like CDF 5/3, no K scaling, integer-friendly; this is what the shader does."""
    e, o = x[..., 0::2], x[..., 1::2]
    d = o - e
    a = e + 0.5 * d
    return a, d


def _lift_inverse_haar_lift(low, high):
    e = low - 0.5 * high
    o = e + high
    out = np.empty(low.shape[:-1] + (low.shape[-1] * 2,), dtype=low.dtype)
    out[..., 0::2], out[..., 1::2] = e, o
    return out


# Orthogonal Daubechies via periodised filter banks (perfect reconstruction).
DB_LO = {
    "db2": np.array([0.48296291314469025, 0.836516303737469, 0.22414386804185735, -0.12940952255092145]),
    "db4": np.array([0.23037781330885523, 0.7148465705525415, 0.6308807679295904, -0.02798376941698385,
                     -0.18703481171888114, 0.030841381835986965, 0.032883011666982945, -0.010597401784997278]),
}


def _db_forward(x, name):
    lo = DB_LO[name].astype(x.dtype)
    hi = (lo[::-1] * np.array([(-1) ** k for k in range(len(lo))], dtype=x.dtype))
    n = x.shape[-1]
    xp = np.concatenate([x, x[..., :len(lo) - 1]], axis=-1)  # periodic
    L = sum(lo[k] * xp[..., k:k + n] for k in range(len(lo)))[..., 0::2]
    H = sum(hi[k] * xp[..., k:k + n] for k in range(len(hi)))[..., 0::2]
    return L, H


def _db_inverse(low, high, name):
    lo = DB_LO[name].astype(low.dtype)
    hi = (lo[::-1] * np.array([(-1) ** k for k in range(len(lo))], dtype=low.dtype))
    n = low.shape[-1] * 2
    up_l = np.zeros(low.shape[:-1] + (n,), low.dtype); up_l[..., 0::2] = low
    up_h = np.zeros_like(up_l); up_h[..., 0::2] = high
    # periodic full convolution with time-reversed filters, aligned to the forward analysis
    out = np.zeros_like(up_l)
    for k in range(len(lo)):
        out += lo[k] * np.roll(up_l, k, axis=-1) + hi[k] * np.roll(up_h, k, axis=-1)
    return out


WAVELETS = {
    "haar": (_lift_forward_haar, _lift_inverse_haar),
    "haar_lift": (_lift_forward_haar_lift, _lift_inverse_haar_lift),
    "cdf53": (_lift_forward_53, _lift_inverse_53),
    "cdf97": (_lift_forward_97, _lift_inverse_97),
    "db2": (lambda x: _db_forward(x, "db2"), lambda l, h: _db_inverse(l, h, "db2")),
    "db4": (lambda x: _db_forward(x, "db4"), lambda l, h: _db_inverse(l, h, "db4")),
}


def dwt2(x, name, levels=LEVELS):
    """Multi-level separable 2-D DWT. Returns {'LL5': a, 'HL1': .., 'LH1': .., 'HH1': .., ...}."""
    fwd, _ = WAVELETS[name]
    bands = {}
    ll = x.astype(np.float32)
    for lvl in range(1, levels + 1):
        L, H = fwd(ll)                      # along columns (last axis = x)
        LL, LH = fwd(np.swapaxes(L, -1, -2))
        HL, HH = fwd(np.swapaxes(H, -1, -2))
        bands[f"LH{lvl}"] = np.swapaxes(LH, -1, -2)
        bands[f"HL{lvl}"] = np.swapaxes(HL, -1, -2)
        bands[f"HH{lvl}"] = np.swapaxes(HH, -1, -2)
        ll = np.swapaxes(LL, -1, -2)
    bands[f"LL{levels}"] = ll
    return bands


def idwt2(bands, name, levels=LEVELS):
    _, inv = WAVELETS[name]
    ll = bands[f"LL{levels}"]
    for lvl in range(levels, 0, -1):
        LLt = np.swapaxes(ll, -1, -2)
        L = np.swapaxes(inv(LLt, np.swapaxes(bands[f"LH{lvl}"], -1, -2)), -1, -2)
        H = np.swapaxes(inv(np.swapaxes(bands[f"HL{lvl}"], -1, -2), np.swapaxes(bands[f"HH{lvl}"], -1, -2)), -1, -2)
        ll = inv(L, H)
    return ll


# ---- Laplacian pyramid --------------------------------------------------------------------------

_BINOMIAL = np.array([1, 4, 6, 4, 1], dtype=np.float32) / 16.0


def _conv1d_reflect(x, kernel, axis):
    r = len(kernel) // 2
    xp = np.pad(x, [(r, r) if a == axis else (0, 0) for a in range(x.ndim)], mode="reflect")
    out = np.zeros_like(x)
    for k, w in enumerate(kernel):
        sl = [slice(None)] * x.ndim
        sl[axis] = slice(k, k + x.shape[axis])
        out += w * xp[tuple(sl)]
    return out


def _blur(x):
    return _conv1d_reflect(_conv1d_reflect(x, _BINOMIAL, 0), _BINOMIAL, 1)


def _down(x):
    return _blur(x)[0::2, 0::2]


def _up(x, shape):
    out = np.zeros(shape, dtype=np.float32)
    out[0::2, 0::2] = x
    return _blur(out) * 4.0


def lap_forward(x, levels=LEVELS):
    bands = {}
    cur = x.astype(np.float32)
    for lvl in range(1, levels + 1):
        nxt = _down(cur)
        bands[f"D{lvl}"] = cur - _up(nxt, cur.shape)
        cur = nxt
    bands[f"B{levels}"] = cur
    return bands


def lap_inverse(bands, levels=LEVELS):
    cur = bands[f"B{levels}"]
    for lvl in range(levels, 0, -1):
        cur = bands[f"D{lvl}"] + _up(cur, bands[f"D{lvl}"].shape)
    return cur


# ---- block transforms (orthonormal) -------------------------------------------------------------

def _wht_matrix(n):
    h = np.array([[1.0]])
    while h.shape[0] < n:
        h = np.block([[h, h], [h, -h]])
    return (h / np.sqrt(n)).astype(np.float32)


def _dct_matrix(n):
    """Orthonormal DCT-II basis as rows: m[k, i] = c_k * cos(pi * (2i + 1) * k / (2n))."""
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    m = np.cos(np.pi * (2 * i + 1) * k / (2 * n)) * np.sqrt(2.0 / n)
    m[0] /= np.sqrt(2.0)
    return m.astype(np.float32)


def block_forward(x, kind, n):
    """Blockwise 2-D transform; returns (H/n, W/n, n, n) coefficients, (i, j) = frequency index."""
    m = _wht_matrix(n) if kind == "wht" else _dct_matrix(n)
    h, w = x.shape
    b = x.astype(np.float32).reshape(h // n, n, w // n, n).transpose(0, 2, 1, 3)
    return np.einsum("ik,abkl,jl->abij", m, b, m, optimize=True)


def block_inverse(c, kind, n):
    m = _wht_matrix(n) if kind == "wht" else _dct_matrix(n)
    b = np.einsum("ki,abkl,lj->abij", m, c, m, optimize=True)
    hb, wb = b.shape[:2]
    return b.transpose(0, 2, 1, 3).reshape(hb * n, wb * n)


def block_bands(c, n):
    """Group block coefficients by frequency index (i, j): each group is a 'band' of one
    coefficient per block, so band-wise quantisation is possible like a wavelet."""
    return {f"f{i}_{j}": c[:, :, i, j] for i in range(n) for j in range(n)}


def block_unbands(bands, n, shape_blocks):
    c = np.zeros(shape_blocks + (n, n), np.float32)
    for i in range(n):
        for j in range(n):
            c[:, :, i, j] = bands[f"f{i}_{j}"]
    return c


# ---- unified interface --------------------------------------------------------------------------

class Transform:
    """name: haar|haar_lift|cdf53|cdf97|db2|db4|lap|wht8|wht16|wht32|wht64|dct8|dct16|dct32|dct64"""

    def __init__(self, name):
        self.name = name
        if name in WAVELETS:
            self.kind = "wavelet"
        elif name == "lap":
            self.kind = "lap"
        elif name.startswith("wht") or name.startswith("dct"):
            self.kind = name[:3]
            self.n = int(name[3:])
        else:
            raise ValueError(name)

    def forward(self, x):
        if self.kind == "wavelet":
            return dwt2(x, self.name)
        if self.kind == "lap":
            return lap_forward(x)
        c = block_forward(x, self.kind, self.n)
        self._blocks = c.shape[:2]
        return block_bands(c, self.n)

    def inverse(self, bands):
        if self.kind == "wavelet":
            return idwt2(bands, self.name)
        if self.kind == "lap":
            return lap_inverse(bands)
        return block_inverse(block_unbands(bands, self.n, self._blocks), self.kind, self.n)

    def band_gains(self, shape):
        """L2 norm of the reconstruction from a unit coefficient in each band (synthesis gain),
        so that quantising band b with step delta/gain_b gives the same MSE per coefficient
        everywhere. Orthonormal transforms return 1.0 for every band."""
        if self.kind in ("wht", "dct"):
            probe = np.zeros(shape, np.float32)
            return {b: 1.0 for b in self.forward(probe)}
        zero = self.forward(np.zeros(shape, np.float32))
        gains = {}
        for b, arr in zero.items():
            z = {k: np.zeros_like(v) for k, v in zero.items()}
            cy, cx = arr.shape[0] // 2, arr.shape[1] // 2
            z[b][cy, cx] = 1.0
            gains[b] = float(np.sqrt(np.sum(self.inverse(z).astype(np.float64) ** 2)))
        return gains

    def coefficient_count(self, bands):
        return sum(int(v.size) for v in bands.values())
