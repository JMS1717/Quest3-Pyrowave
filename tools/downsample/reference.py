"""CPU model of tools/downsample/frame_downsample.hlsl, one axis at a time.

The shader's kernel is separable and bilinear filtering is separable, so a 1-D model of the
axis weights reproduces the 2-D result exactly. Pure Python so the CPU test job needs no numpy.
"""
import math

MAX_SCALE = 3.0
MAX_PAIRS = 7


def catmull_rom(x):
    x = abs(x)
    if x < 1.0:
        return (1.5 * x - 2.5) * x * x + 1.0
    if x < 2.0:
        return ((-0.5 * x + 2.5) * x - 4.0) * x + 2.0
    return 0.0


def clamp(v, lo, hi):
    return min(max(v, lo), hi)


def make_pair(first, k, center, inv_scale, lo, hi):
    """Same branches as MakePair: [(position, weight)], positions in texel space."""
    ia = first + 2 * k
    ib = ia + 1
    wa = catmull_rom((ia + 0.5 - center) * inv_scale)
    wb = catmull_rom((ib + 0.5 - center) * inv_scale)
    ja, jb = clamp(ia, lo, hi), clamp(ib, lo, hi)
    if ja == jb:
        return [(ja + 0.5, wa + wb)]
    if wa * wb > 0.0:
        w = wa + wb
        return [(ja + 0.5 + wb / w, w)]
    return [(ja + 0.5, wa), (jb + 0.5, wb)]


def axis_taps(center, scale, lo, hi):
    """Fetches the shader issues along one axis for a footprint `scale` (texels/output pixel)."""
    scale = clamp(scale, 1.0, MAX_SCALE)
    first = math.floor(center - 0.5 - 2.0 * scale)
    last = math.floor(center - 0.5 + 2.0 * scale)
    pairs = min(int((last - first + 2) * 0.5), MAX_PAIRS)
    taps = []
    for k in range(pairs):
        taps.extend(t for t in make_pair(first, k, center, 1.0 / scale, lo, hi) if t[1] != 0.0)
    return taps


def bilinear(values, p):
    """Clamp-addressed linear fetch at texel-space position p (texel i spans [i, i + 1])."""
    x = p - 0.5
    i = math.floor(x)
    f = x - i
    n = len(values) - 1
    return (1.0 - f) * values[clamp(i, 0, n)] + f * values[clamp(i + 1, 0, n)]


def shader_sample(values, center, scale, lo=0, hi=None):
    hi = len(values) - 1 if hi is None else hi
    taps = axis_taps(center, scale, lo, hi)
    weight = sum(w for _, w in taps)
    return sum(w * bilinear(values, p) for p, w in taps) / weight


def direct_sample(values, center, scale, lo=0, hi=None):
    """Plain clamp-to-bounds Catmull-Rom convolution: what the pair trick must equal."""
    hi = len(values) - 1 if hi is None else hi
    scale = clamp(scale, 1.0, MAX_SCALE)
    first = math.floor(center - 0.5 - 2.0 * scale)
    last = math.floor(center - 0.5 + 2.0 * scale)
    total = weight = 0.0
    for i in range(first, last + 1):
        w = catmull_rom((i + 0.5 - center) / scale)
        total += w * values[clamp(i, lo, hi)]
        weight += w
    return total / weight


def legacy_sample(values, center):
    """The previous composition: one bilinear tap at the pixel center, no footprint."""
    return bilinear(values, center)


def resample(values, out_size, sampler):
    ratio = len(values) / out_size
    return [sampler(values, (j + 0.5) * ratio, ratio) for j in range(out_size)]
