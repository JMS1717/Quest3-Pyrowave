"""Measure how much of a PyroWave frame is zero, per wavelet band, from a saved .wave (CPU only).

The Quest decoder's dequant pass writes every coefficient of every band, zero or not, and the
inverse transform reads them all back. Bands, 32x32 blocks and 8x8 sub-blocks that the bitstream
leaves empty are pure memory traffic. This reports their share so a zero-skipping or fused
dequant+Haar kernel can be sized from real frames instead of guesses.

    python -m tools.quest3.block_occupancy frame.wave [--json]

Layout follows the pinned decoder: pyrowave_common.cpp init_block_meta (levels 4..0, then
component, then band; 4:2:0 has no level-0 chroma) and pyrowave_common.hpp BitstreamHeader.
"""
import argparse
import json
import struct
from pathlib import Path

LEVELS = 5
ALIGNMENT = 1 << LEVELS
MIN_SIZE = 4 << LEVELS
BAND_NAMES = ('LL', 'HL', 'LH', 'HH')


def _align(value, alignment):
    return (value + alignment - 1) & ~(alignment - 1)


def band_layout(width, height, chroma444):
    """Return [(component, level, band, w, h, first_block, block_count, blocks_x)] in bitstream order."""
    aligned_w = max(_align(width, ALIGNMENT), MIN_SIZE)
    aligned_h = max(_align(height, ALIGNMENT), MIN_SIZE)
    layout, offset = [], 0
    for level in range(LEVELS - 1, -1, -1):
        for component in range(3):
            if level == 0 and component != 0 and not chroma444:
                continue
            for band in range(0 if level == LEVELS - 1 else 1, 4):
                w, h = (aligned_w // 2) >> level, (aligned_h // 2) >> level
                bx, by = (w + 31) // 32, (h + 31) // 32
                layout.append((component, level, band, w, h, offset, bx * by, bx))
                offset += bx * by
    return layout


def read_wave(path):
    """Return (width, height, chroma444, [frame bytes]) from the harness .wave container."""
    data = Path(path).read_bytes()
    if len(data) < 44 or data[:8] != b'PYROWAVE':
        raise ValueError('not a .wave file')
    params = struct.unpack_from('<8i', data, 8)
    width, height, chroma = params[0], params[1], params[3]
    frames, pos = [], 40
    while pos + 4 <= len(data):
        (size,) = struct.unpack_from('<I', data, pos)
        pos += 4
        if size == 0 or pos + size > len(data):
            raise ValueError('truncated frame')
        frames.append(data[pos:pos + size])
        pos += size
    if not frames:
        raise ValueError('no frame')
    return width, height, chroma == 1, frames


def parse_frame(frame, width, height, chroma444):
    layout = band_layout(width, height, chroma444)
    total_blocks = layout[-1][5] + layout[-1][6]
    present = {}
    pos = 0
    while pos + 8 <= len(frame):
        ballot, word1, word2 = struct.unpack_from('<HHI', frame, pos)
        if word1 >> 15:  # extended: sequence header, 8 bytes
            seq_w = (struct.unpack_from('<I', frame, pos)[0] & 0x3fff) + 1
            seq_h = ((struct.unpack_from('<I', frame, pos)[0] >> 14) & 0x3fff) + 1
            if (seq_w, seq_h) != (width, height):
                raise ValueError(f'sequence header {seq_w}x{seq_h} does not match {width}x{height}')
            pos += 8
            continue
        payload_words = word1 & 0xfff
        block_index = word2 >> 8
        if payload_words < 2 or pos + 4 * payload_words > len(frame):
            raise ValueError('malformed packet')
        if block_index >= total_blocks:
            raise ValueError('block index out of range')
        present.setdefault(block_index, (ballot, 4 * payload_words))
        pos += 4 * payload_words
    if pos != len(frame):
        raise ValueError('trailing bytes')
    return layout, present


def summarize(layout, present):
    bands, coeffs_total, coeffs_zero8 = [], 0, 0
    for component, level, band, w, h, first, count, bx in layout:
        sub_valid = sub_nonzero = blocks_present = payload = 0
        zero8_coeffs = 0
        for i in range(count):
            x, y = i % bx, i // bx
            for sub in range(16):
                sx, sy = x * 4 + (sub & 3), y * 4 + (sub >> 2)
                if sx * 8 >= w or sy * 8 >= h:
                    continue
                sub_valid += 1
                cw, ch = min(8, w - sx * 8), min(8, h - sy * 8)
                entry = present.get(first + i)
                if entry and entry[0] >> sub & 1:
                    sub_nonzero += 1
                else:
                    zero8_coeffs += cw * ch
            if first + i in present:
                blocks_present += 1
                payload += present[first + i][1]
        coeffs = w * h
        coeffs_total += coeffs
        coeffs_zero8 += zero8_coeffs
        bands.append({
            'component': ('Y', 'Cb', 'Cr')[component],
            'level': level, 'band': BAND_NAMES[band], 'coefficients': coeffs,
            'blocks_32x32': count, 'blocks_present': blocks_present,
            'subblocks_8x8': sub_valid, 'subblocks_nonzero': sub_nonzero,
            'zero_coefficient_share': zero8_coeffs / coeffs, 'payload_bytes': payload,
            'bits_per_coefficient': 8 * payload / coeffs})
    level0 = [b for b in bands if b['level'] == 0 and b['band'] != 'LL']
    level0_coeffs = sum(b['coefficients'] for b in level0)
    return {
        'coefficients': coeffs_total,
        'coefficients_in_empty_8x8': coeffs_zero8,
        'empty_share': coeffs_zero8 / coeffs_total,
        'level0_high_share_of_all': level0_coeffs / coeffs_total,
        'level0_high_empty_share': (sum(b['zero_coefficient_share'] * b['coefficients'] for b in level0) / level0_coeffs)
        if level0_coeffs else 0.0,
        'payload_bytes': sum(b['payload_bytes'] for b in bands),
        'bands': bands,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('wave')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    width, height, chroma444, frames = read_wave(args.wave)
    reports = [summarize(*parse_frame(f, width, height, chroma444)) for f in frames]
    if args.json:
        print(json.dumps({'width': width, 'height': height, 'chroma444': chroma444, 'frames': reports}, indent=1))
        return 0
    for index, r in enumerate(reports):
        print(f'frame {index}: {width}x{height} {"4:4:4" if chroma444 else "4:2:0"} '
              f'{r["payload_bytes"]} B, {8 * r["payload_bytes"] / r["coefficients"]:.3f} bits/coefficient')
        print(f'  coefficients in empty 8x8 sub-blocks: {100 * r["empty_share"]:.1f}% of all; '
              f'level-0 high bands are {100 * r["level0_high_share_of_all"]:.1f}% of all and '
              f'{100 * r["level0_high_empty_share"]:.1f}% empty')
        for b in r['bands']:
            print(f'  {b["component"]:>2} L{b["level"]} {b["band"]}: {b["coefficients"]:>8} coeff, '
                  f'{b["blocks_present"]:>5}/{b["blocks_32x32"]:<5} blocks, '
                  f'{100 * b["zero_coefficient_share"]:5.1f}% empty, {b["bits_per_coefficient"]:.3f} bpc')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
