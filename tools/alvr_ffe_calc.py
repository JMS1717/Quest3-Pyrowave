#!/usr/bin/env python3
"""Compute ALVR's foveated-encoding output size (port of FFR.cpp CalculateFoveationVars)."""
import argparse
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class FoveationConfig:
    center_size_x: float = 0.45
    center_size_y: float = 0.40
    center_shift_x: float = 0.0
    center_shift_y: float = 0.0
    edge_ratio_x: float = 3.0
    edge_ratio_y: float = 4.0


def _axis(target, center_size, edge_ratio):
    edge_size = target - center_size * target
    center_aligned = 1.0 - math.ceil(edge_size / (edge_ratio * 2.0)) * (edge_ratio * 2.0) / target
    scale = center_aligned + (1.0 - center_aligned) / edge_ratio
    return math.ceil(scale * target / 32.0) * 32


def alvr_render_size(eye_width, eye_height):
    """The per-eye render size ALVR actually uses: each side floored to a multiple of 32
    (measured in openvr_config.json: 3197/2842/2486/2131 wide became 3168/2816/2464/2112)."""
    return eye_width // 32 * 32, eye_height // 32 * 32


def encoded_eye_size(eye_width, eye_height, config):
    """Per-eye encoded size for a requested render size; config None means foveated encoding is off."""
    eye_width, eye_height = alvr_render_size(eye_width, eye_height)
    if config is None:
        return math.ceil(eye_width / 32.0) * 32, math.ceil(eye_height / 32.0) * 32
    return (_axis(eye_width, config.center_size_x, config.edge_ratio_x),
            _axis(eye_height, config.center_size_y, config.edge_ratio_y))


def fits_limit(eye_width, eye_height, config, limit=(4096, 2048)):
    width, height = encoded_eye_size(eye_width, eye_height, config)
    return width * 2 <= limit[0] and height <= limit[1]


def max_eye_size(config, limit=(4096, 2048), step=32, start=512, stop=8192):
    best = None
    for size in range(start, stop + 1, step):
        if fits_limit(size, size, config, limit):
            best = size
    return best


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("eye_width", type=int)
    parser.add_argument("eye_height", type=int, nargs="?")
    parser.add_argument("--center", type=float, nargs=2, default=(0.45, 0.40), metavar=("X", "Y"))
    parser.add_argument("--shift", type=float, nargs=2, default=(0.0, 0.0), metavar=("X", "Y"))
    parser.add_argument("--edge", type=float, nargs=2, default=(3.0, 4.0), metavar=("X", "Y"))
    parser.add_argument("--off", action="store_true", help="foveated encoding disabled")
    parser.add_argument("--limit", type=int, nargs=2, default=(4096, 2048), metavar=("W", "H"),
                        help="max side-by-side encode size (NVENC H.264: 4096 2048)")
    parser.add_argument("--fps", type=float, default=72.0)
    args = parser.parse_args()

    eye_height = args.eye_height or args.eye_width
    config = None if args.off else FoveationConfig(*args.center, *args.shift, *args.edge)
    width, height = encoded_eye_size(args.eye_width, eye_height, config)
    ok = fits_limit(args.eye_width, eye_height, config, tuple(args.limit))
    mpix = width * 2 * height * args.fps / 1e6
    print(f"per-eye {width}x{height}  side-by-side {width * 2}x{height}  "
          f"{mpix:.0f} Mpix/s @ {args.fps:g} Hz  {'OK' if ok else 'EXCEEDS'} "
          f"{args.limit[0]}x{args.limit[1]}")
    if config is not None:
        print(f"largest square eye size within limit: {max_eye_size(config, tuple(args.limit))}")


if __name__ == "__main__":
    main()
