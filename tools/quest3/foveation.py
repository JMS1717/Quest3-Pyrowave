"""Fixed ALVR spatial encoding model for the PyroWave profiles; no headset access or game rendering change."""
import argparse
import json
import math

# Fixed, non-gaze profiles. Must match alvr_session::pyrowave_foveation (quest3-alvr.patch).
# Only light has in-headset screens; balanced and strong are decode-budget candidates.
# Values were chosen so float32 (client, Rust) and mixed float (server, C++) sizing agree
# for every 8-pixel eye size from 512 to 4096; see tests/test_foveation.py.
PROFILES = {'light': (0.8, 1.5), 'balanced': (0.68, 1.75), 'strong': (0.6, 2.0)}


def profile_values(profile='light'):
    if profile not in PROFILES:
        raise ValueError(f'Profile must be one of {sorted(PROFILES)}')
    return PROFILES[profile]


def axis(size, profile='light'):
    if type(size) is not int or not 32 <= size <= 8192:
        raise ValueError('Expanded axis must be an integer from 32 to 8192')
    CENTER, EDGE = profile_values(profile)
    center = 1 - math.ceil((size - CENTER * size) / (EDGE * 2)) * (EDGE * 2) / size
    scale = center + (1 - center) / EDGE
    encoded = math.ceil(scale * size / 32) * 32
    c0 = (1 - center) * 0.5
    c1 = (EDGE - 1) * c0 / EDGE
    c2 = (EDGE - 1) * center + 1
    lo, hi = c0, 1 - c0
    loc, hic = c0 / c2, 1 - c0 / c2
    al = c2 * (1 - EDGE) / (EDGE * loc)
    bl = (c1 + c2 * loc) / loc
    ar = c2 * (EDGE - 1) / (EDGE * (1 - hic))
    br = (c2 - EDGE * c1 - 2 * EDGE * c2 + c2 * EDGE * (1 - hic) + EDGE) / (EDGE * (1 - hic))
    cr = (c2 * EDGE - c2) * (c1 - hic + c2 * hic) / (EDGE * (1 - hic) ** 2)
    return {'expanded': size, 'encoded': encoded, 'ratio': scale * size / encoded,
            'center': center, 'edge': EDGE, 'c1': c1, 'c2': c2, 'lo': lo, 'hi': hi,
            'al': al, 'bl': bl, 'ar': ar, 'br': br, 'cr': cr}


def encoded_eye(eye, profile='light'):
    if len(eye) != 2:
        raise ValueError('Need two per-eye dimensions')
    return [axis(v, profile)['encoded'] for v in eye]


def inverse(u, a):
    """Expanded logical coordinate -> encoded eye coordinate, before right-eye mirror."""
    if u < a['lo']:
        value = (-a['bl'] + math.sqrt(max(0, a['bl'] ** 2 + 4 * a['al'] * u))) / (2 * a['al'])
    elif u > a['hi']:
        value = (-a['br'] + math.sqrt(max(0, a['br'] ** 2 - 4 * (a['cr'] - a['ar'] * u)))) / (2 * a['ar'])
    else:
        value = (u - a['c1']) * a['edge'] / a['c2']
    return value * a['ratio']


def forward(u, a):
    """Encoded eye coordinate -> expanded logical source coordinate (server warp)."""
    x = u / a['ratio']
    lo, hi = a['lo'] / a['c2'], 1 - (1 - a['hi']) / a['c2']
    center = x * a['c2'] / a['edge'] + a['c1']
    if x < lo:
        g = x / lo
        return g * center + (1 - g) * x * a['c2']
    if x > hi:
        g = (1 - x) / (1 - hi)
        return g * center + (1 - g) * ((x - 1) * a['c2'] + 1)
    return center


def stereo_uv(u, v, eye, expanded=(2080, 2208), profile='light'):
    if eye not in (0, 1):
        raise ValueError('Eye must be left=0 or right=1')
    x = inverse(1 - u if eye else u, axis(expanded[0], profile))
    return ((1 - x if eye else x) + eye) * 0.5, inverse(v, axis(expanded[1], profile))


def math_report(eye=(2080, 2208), hz=120, mbps=1000, profile='light'):
    if not all(math.isfinite(v) and v > 0 for v in (hz, mbps)):
        raise ValueError('Need positive finite refresh and bitrate')
    encoded = encoded_eye(eye, profile)
    ratio = encoded[0] * encoded[1] / (eye[0] * eye[1])
    raw_bytes = 2 * encoded[0] * encoded[1] * 3 // 2
    payload = mbps * 1e6 / 8 / hz
    return {'mode': profile, 'expanded_eye': list(eye), 'encoded_eye': encoded,
            'center_fraction_aligned': [axis(v, profile)['center'] for v in eye],
            'edge_ratio': profile_values(profile)[1],
            'encoded_pixel_ratio': ratio, 'encoded_pixel_savings_percent': 100 * (1 - ratio),
            'hz': hz, 'target_mbps': mbps, 'payload_bytes_per_frame': payload,
            'raw_420_bytes_per_frame': raw_bytes, 'raw_to_payload_ratio': raw_bytes / payload,
            'scope': 'Pixel/payload model, not measured traffic, speedup or perceptual acceptance.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--eye', nargs=2, type=int, default=(2080, 2208))
    parser.add_argument('--hz', type=float, default=120)
    parser.add_argument('--mbps', type=float, default=1000)
    parser.add_argument('--profile', choices=sorted(PROFILES), default='light')
    args = parser.parse_args()
    print(json.dumps(math_report(args.eye, args.hz, args.mbps, args.profile), indent=2))
