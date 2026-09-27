"""The picture the PC streams when we want numbers instead of opinions.

The driver can send this pattern in place of the game image, and the headset client reads back the
pixels it is about to display. Comparing the two tells us exactly what the pipeline does to colour:
a gamma mistake bends the grey ramp, clipping flattens its ends, and the smooth gradient band shows
how many distinct levels survive - which is what banding actually is.

Layout (fractions of the frame, identical in both eyes):
  top third      solid patches in a row: black, 25%, 50%, 75%, white, red, green, blue
  middle third   horizontal 0 -> 255 grey gradient (banding)
  bottom third   vertical 0 -> 255 grey gradient plus a mid-grey surround (blocking/dither)
"""
import re
from dataclasses import dataclass

import numpy as np

PATCH_ROW = 1 / 6          # vertical centre of the patch row, as a fraction of the height
GRADIENT_ROW = 1 / 2       # vertical centre of the horizontal gradient band


@dataclass(frozen=True)
class Patch:
    name: str
    rgb: tuple
    slot: int              # 0..7, left to right


PATCHES = (
    Patch("black", (0, 0, 0), 0),
    Patch("grey25", (64, 64, 64), 1),
    Patch("grey50", (128, 128, 128), 2),
    Patch("grey75", (192, 192, 192), 3),
    Patch("white", (255, 255, 255), 4),
    Patch("red", (255, 0, 0), 5),
    Patch("green", (0, 255, 0), 6),
    Patch("blue", (0, 0, 255), 7),
)


def patch_center(patch, width, height):
    """Pixel at the middle of a patch, where a sample is safely inside it."""
    return int((patch.slot + 0.5) * width / len(PATCHES)), int(PATCH_ROW * height)


def gradient_row(height):
    return int(GRADIENT_ROW * height)


def build_pattern(width, height):
    """RGBA image (height, width, 4), uint8."""
    image = np.zeros((height, width, 4), dtype=np.uint8)
    image[:, :, 3] = 255
    third = height // 3

    for patch in PATCHES:                                   # solid patches across the top third
        start = patch.slot * width // len(PATCHES)
        end = (patch.slot + 1) * width // len(PATCHES)
        image[0:third, start:end, :3] = patch.rgb

    ramp = (np.arange(width) * 255 // max(1, width - 1)).astype(np.uint8)
    image[third:2 * third, :, 0] = ramp                     # horizontal grey ramp
    image[third:2 * third, :, 1] = ramp
    image[third:2 * third, :, 2] = ramp

    rows = np.arange(height - 2 * third)
    column = (rows * 255 // max(1, len(rows) - 1)).astype(np.uint8)
    image[2 * third:, :, 0] = column[:, None]               # vertical ramp
    image[2 * third:, :, 1] = column[:, None]
    image[2 * third:, :, 2] = column[:, None]
    return image


def expected_rgb(u, v, width, height):
    """The pattern's colour at normalised position (u, v) - what a probe sample should read back."""
    image = build_pattern(width, height)
    x = min(width - 1, max(0, int(u * width)))
    y = min(height - 1, max(0, int(v * height)))
    return tuple(int(c) for c in image[y, x][:3])


def write_raw(path, width, height):
    """Raw RGBA bytes, row major - what the driver uploads into its encode texture."""
    with open(path, "wb") as handle:
        handle.write(build_pattern(width, height).tobytes())
    return path


def write_stereo_raw(path, eye_width, height):
    """The full side-by-side frame the driver streams: the same pattern in each eye."""
    eye = build_pattern(eye_width, height)
    with open(path, "wb") as handle:
        handle.write(np.concatenate([eye, eye], axis=1).tobytes())
    return path


def parse_probe(text):
    """(u, v, (r, g, b)) for the most recent probe block: earlier ones can predate the first frame."""
    marker = text.rfind("XRPIX grid=")
    if marker >= 0:
        text = text[marker:]
    samples = []
    for match in re.finditer(r"XRPIX ([\d.]+) ([\d.]+) (\d+) (\d+) (\d+)", text):
        u, v, r, g, b = match.groups()
        samples.append((float(u), float(v), (int(r), int(g), int(b))))
    return samples


def probe_depth(text):
    """Bits per channel the client reported for its swapchain (8 unless it says otherwise)."""
    match = re.search(r"XRPIX grid=\d+ eye=\d+x\d+ depth=(\d+)", text)
    return int(match.group(1)) if match else 8


def srgb_to_linear(value):
    """sRGB code value 0..1 -> light 0..1."""
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def linear_to_srgb(value):
    """light 0..1 -> sRGB code value 0..1 (inverse of srgb_to_linear)."""
    value = max(0.0, value)
    return value * 12.92 if value <= 0.0031308 else 1.055 * (value ** (1 / 2.4)) - 0.055


def to_srgb8(grey, depth, transfer):
    """A panel readback (native `depth` code) -> the sRGB 0..255 input scale, so 8-bit and 10-bit
    swapchains are measured on the same axis (what we actually sent)."""
    full = (1 << depth) - 1
    norm = grey / full
    if transfer == "linear":
        norm = linear_to_srgb(norm)
    return norm * 255.0


def expected_stored(rgb, depth=8, transfer="srgb"):
    """What a probe should read back for a pattern colour, in the swapchain's own units.

    An sRGB swapchain stores the code values we sent. A linear one (10-bit, or an HDR buffer) stores
    light, so mid grey sits near a fifth of full scale rather than half - not a fault, just a
    different container.
    """
    full = (1 << depth) - 1
    if transfer == "linear":
        return tuple(int(round(full * srgb_to_linear(c / 255.0))) for c in rgb)
    return tuple(int(round(full * c / 255.0)) for c in rgb)


def colour_report(samples, eye_width, height, depth=8, transfer="srgb"):
    """How faithfully the patches came back, and whether the transfer curve is straight.

    `gamma` is the exponent that best maps what we sent to what the headset shows: 1.0 means the
    pipeline left the values alone. A stray sRGB encode lands near 0.45, and decoding twice near 2.2.
    """
    errors, sent, shown = [], [], []
    patch_v = PATCH_ROW
    for u, v, rgb in samples:
        if abs(v - patch_v) > 0.05:
            continue
        expected = expected_stored(expected_rgb(u, v, eye_width, height), depth, transfer)
        errors.append(max(abs(a - b) for a, b in zip(expected, rgb)))
        full = (1 << depth) - 1
        for a, b in zip(expected, rgb):
            if full * 0.03 <= a <= full * 0.97:     # ends are clipped by codecs, ignore them
                sent.append(a / full)
                shown.append(max(b, 1) / full)
    gamma = 1.0
    if len(sent) >= 2:
        logs_in = np.log(np.array(sent))
        logs_out = np.log(np.array(shown))
        usable = logs_in < -1e-6
        if usable.any():
            gamma = float(np.mean(logs_out[usable] / logs_in[usable]))
    return {"patches": len(errors), "max_patch_error": max(errors) if errors else None,
            "mean_patch_error": float(np.mean(errors)) if errors else None, "gamma": round(gamma, 3)}


def banding_report(samples, eye_width, height, depth=8, transfer="srgb"):
    """Distinct grey levels surviving along the ramp (on the sRGB 0..255 input scale) and the largest
    jump between neighbours. A clean ramp keeps ~256 levels with steps of 1; banding is few levels with
    big steps. Measured on the sRGB scale so 8-bit and 10-bit are comparable.
    """
    ramp = sorted(((u, rgb) for u, v, rgb in samples if abs(v - GRADIENT_ROW) <= 0.05), key=lambda s: s[0])
    greys = [to_srgb8(sum(rgb) / 3.0, depth, transfer) for _, rgb in ramp]
    if len(greys) < 2:
        return {"samples": len(greys), "levels": 0, "max_step": None}
    steps = [abs(b - a) for a, b in zip(greys, greys[1:])]
    return {"samples": len(greys), "levels": len({round(g) for g in greys}),
            "max_step": int(round(max(steps))), "mean_step": round(float(np.mean(steps)), 2)}


def black_crush_report(samples, eye_width, height, depth=8, transfer="srgb"):
    """How well shadow detail survives, from the dark end of the horizontal ramp (expected code 0..255).

    `crush_floor` is the highest expected code that still reads as pure black on the panel (everything
    at or below it is crushed to 0). `dark_levels` is how many distinct output codes appear in the
    darkest eighth (expected 0..32) - a clean pipeline keeps ~32, crushed blacks keep few.
    """
    ramp = sorted(((u, sum(rgb) / 3.0) for u, v, rgb in samples if abs(v - GRADIENT_ROW) <= 0.05),
                  key=lambda s: s[0])
    if not ramp:
        return {"dark_samples": 0, "dark_levels": 0, "crush_floor": None}
    crush_floor = 0
    dark = []
    for u, grey in ramp:
        expected8 = int(round(u * 255))          # pattern ramp is authored 0..255 across the width
        out8 = to_srgb8(grey, depth, transfer)   # readback -> sRGB input scale (transfer-aware)
        if expected8 <= 32:
            dark.append(round(out8))
        if round(out8) == 0:
            crush_floor = max(crush_floor, expected8)
    return {"dark_samples": len(dark), "dark_levels": len(set(dark)), "crush_floor": crush_floor}


def calibration_report(samples, eye_width, height, depth=8, transfer="srgb"):
    """One bundle of the calibration metrics: colour fidelity, banding and black crush."""
    colour = colour_report(samples, eye_width, height, depth, transfer)
    band = banding_report(samples, eye_width, height, depth, transfer)
    crush = black_crush_report(samples, eye_width, height, depth, transfer)
    return {"gamma": colour["gamma"], "max_patch_error": colour["max_patch_error"],
            "mean_patch_error": colour["mean_patch_error"], "banding_levels": band["levels"],
            "banding_max_step": band.get("max_step"), "crush_floor": crush["crush_floor"],
            "dark_levels": crush["dark_levels"]}
