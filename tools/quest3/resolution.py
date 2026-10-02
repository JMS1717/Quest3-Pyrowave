"""Independent per-eye geometry controls for ALVR's existing compositor path."""
import json
from pathlib import Path

RENDER_FIELD = 'emulated_headset_view_resolution'
ENCODE_FIELD = 'transcoding_view_resolution'
PROFILE_FILE = Path(__file__).resolve().parents[2] / 'presets/resolution-comparison.json'


def profiles():
    return json.loads(PROFILE_FILE.read_text(encoding='utf-8'))['profiles']


def validate_eye(size):
    if (not isinstance(size, (list, tuple)) or len(size) != 2 or
            any(type(v) is not int or not 32 <= v <= 8192 for v in size)):
        raise ValueError('Per-eye width and height must be integers from 32 to 8192')
    return list(size)


def aligned_eye(size):
    return [(v + 31) // 32 * 32 for v in validate_eye(size)] if size is not None else None


def assignments(render_eye=None, encode_eye=None):
    """Omitted geometry stays untouched; explicit height avoids aspect inference."""
    values = {}
    for field, size in ((RENDER_FIELD, render_eye), (ENCODE_FIELD, encode_eye)):
        if size is None:
            continue
        width, height = validate_eye(size)
        base = f'session_settings.video.{field}'
        values.update({base + '.variant': 'Absolute', base + '.Absolute.width': width,
                       base + '.Absolute.height.set': True,
                       base + '.Absolute.height.content': height})
    if not values:
        raise ValueError('Specify --render-eye, --encode-eye or --profile')
    return values


def absolute_eye(setting):
    """Scale/inferred height needs current headset capabilities; leave it unknown."""
    if not setting or setting.get('variant') != 'Absolute':
        return None
    value = setting.get('Absolute', {})
    height = value.get('height', {})
    if not isinstance(height, dict) or not height.get('set'):
        return None
    size = [value.get('width'), height.get('content')]
    if any(type(v) is not int or v <= 0 for v in size):
        return None
    # The patched server rounds both per-eye axes UP to 32 pixels.
    return [(v + 31) // 32 * 32 for v in size]


def evidence(settings, telemetry=()):
    """Distinguish requested sizes, negotiated OpenVR sizes and actual decode size.

    This verifies geometry only, never submitted game textures, quality or FPS.
    PyroWave telemetry reports the full side-by-side encoded frame, not one eye.
    """
    o = settings.get('openvr', {})
    requested_render = absolute_eye(settings.get('configured_render_view_resolution'))
    requested_encode = absolute_eye(settings.get('configured_view_resolution'))
    render = [o.get('target_eye_resolution_width'), o.get('target_eye_resolution_height')]
    encode = [o.get('eye_resolution_width'), o.get('eye_resolution_height')]
    mismatches = []
    known = lambda size: all(type(v) is int and v > 0 for v in size)
    for name, requested, negotiated in (('render', requested_render, render),
                                        ('encode', requested_encode, encode)):
        if requested and known(negotiated) and requested != negotiated:
            mismatches.append(f'{name}_size_pending_restart_or_negotiation')
    decoded = []
    if settings.get('codec') == 'PyroWave':
        for item in telemetry:
            p = item.get('pyrowave') or {}
            size = [p.get('encoded_width'), p.get('encoded_height')]
            if known(size) and size not in decoded:
                decoded.append(size)
        if known(encode) and any(size != [encode[0] * 2, encode[1]] for size in decoded):
            mismatches.append('decoded_frame_differs_from_negotiated_encode')
    complete = (requested_render is not None and requested_encode is not None and
                known(render) and known(encode) and
                settings.get('codec') == 'PyroWave' and bool(decoded) and
                o.get('enable_foveated_encoding') is False)
    return {'status': 'mismatch' if mismatches else 'verified' if complete else 'unknown',
            'mismatches': mismatches, 'aligned_requested_render_eye': requested_render,
            'aligned_requested_encode_eye': requested_encode,
            'negotiated_render_eye': render, 'negotiated_encode_eye': encode,
            'observed_encoded_stereo_frames': decoded,
            'pc_source_pixel_ratio': render[0] * render[1] / (encode[0] * encode[1])
                if known(render) and known(encode) else None,
            'scope': 'Geometry only; SteamVR recommendation is not proof of game texture size, image quality or sustained performance.'}
