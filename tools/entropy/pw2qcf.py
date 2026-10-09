"""Parse one complete PyroWave codec frame (debug.q3pw.dump_frames) into quantized levels (QCF1).

Usage: pw2qcf.py <frame.pw> <width> <height> <420|444> <out.qcf>
Replicates wavelet_dequant.comp: per 32x32 block an 8-byte header {u16 ballot; u16 payload_words:12,
sequence:3, extended:1; u32 quant_code:8, block_index:24}, u16 control words and u8 q_bits per coded
8x8, then bit-plane bytes per 4x2 sub-block (q_bits + 2-bit control planes, MSB plane first), then
sign bits of the nonzero values, LSB first. Levels are the integer magnitudes, before the per-8x8
quant scale. Prints the byte split of the raw format and the order-0 entropy of the side information
(quant code per 32x32 block, quant scale per coded 8x8) that a coefficient coder must still carry.
"""
import collections
import math
import struct
import sys

import numpy as np

LEVELS = 5


def band_layout(width, height, chroma420):
    align = 1 << LEVELS
    aw = max((width + align - 1) // align * align, 4 << LEVELS)
    ah = max((height + align - 1) // align * align, 4 << LEVELS)
    bands = []  # (component, level, band, w, h, bx32, by32, block_offset)
    offset = 0
    for level in range(LEVELS - 1, -1, -1):
        for c in range(3):
            if level == 0 and c != 0 and chroma420:
                continue
            for band in range(0 if level == LEVELS - 1 else 1, 4):
                w, h = (aw // 2) >> level, (ah // 2) >> level
                bx, by = (w + 31) // 32, (h + 31) // 32
                bands.append((c, level, band, w, h, bx, by, offset))
                offset += bx * by
    return bands, offset


def main():
    path, width, height, chroma, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
    chroma420 = chroma == '420'
    data = open(path, 'rb').read()
    bands, total = band_layout(width, height, chroma420)
    block_band = np.zeros(total, np.int32)
    for i, b in enumerate(bands):
        bx, by, off = b[5], b[6], b[7]
        block_band[off:off + bx * by] = i
    coef = [np.zeros((b[6] * 32, b[5] * 32), np.int32) for b in bands]
    seen = np.zeros(total, bool)
    split = dict(seq=0, header=0, control=0, qbits=0, planes=0, signs=0, pad=0, dup=0)
    quant_codes, quant_scales = collections.Counter(), collections.Counter()
    pos = 0
    while pos + 8 <= len(data):
        w0, w1 = struct.unpack_from('<II', data, pos)
        if (w0 >> 31) & 1:  # extended: sequence header
            split['seq'] += 8
            pos += 8
            continue
        ballot = w0 & 0xffff
        words = (w0 >> 16) & 0xfff
        block = w1 >> 8
        size = words * 4
        if seen[block]:
            split['dup'] += size
            pos += size
            continue
        seen[block] = True
        blk = data[pos:pos + size]
        pos += size
        n = bin(ballot).count('1')
        split['header'] += 8
        split['control'] += 2 * n
        split['qbits'] += n
        bi = block_band[block]
        c, level, band, w, h, bx, by, off = bands[bi]
        local = block - off
        x32, y32 = (local % bx) * 32, (local // bx) * 32
        controls = struct.unpack_from(f'<{n}H', blk, 8)
        qbits = blk[8 + 2 * n:8 + 3 * n]
        quant_codes[w1 & 0xff] += 1
        quant_scales.update(b >> 4 for b in qbits)
        byte = 8 + 3 * n
        nz_order = []  # (y, x) in sign order
        values = {}
        k = 0
        for lb in range(16):
            if not (ballot >> lb) & 1:
                continue
            cw, qb = controls[k], qbits[k] & 0xf
            k += 1
            x8, y8 = x32 + 8 * (lb % 4), y32 + 8 * (lb // 4)
            for sb in range(8):
                planes = qb + ((cw >> (2 * sb)) & 3)
                if cw == 0:
                    planes = 0
                mags = [0] * 8
                for q in range(planes - 1, -1, -1):
                    p = blk[byte]
                    byte += 1
                    for b in range(8):
                        mags[b] |= ((p >> b) & 1) << q
                values[(lb, sb)] = (x8 + 4 * (sb >> 2), y8 + 2 * (sb & 3), mags)
        split['planes'] += byte - 8 - 3 * n
        # Sign order: thread local_index = lb * 8 + sb (block_x, block_y bits above sb), values i then j.
        sign_bit = byte * 8
        for lb in range(16):
            for sb in range(8):
                if (lb, sb) not in values:
                    continue
                x0, y0, mags = values[(lb, sb)]
                for i in range(4):
                    for j in range(2):
                        m = mags[i * 2 + j]
                        if m:
                            s = (blk[sign_bit >> 3] >> (sign_bit & 7)) & 1
                            sign_bit += 1
                            coef[bi][y0 + j, x0 + i] = -m if s else m
        sign_bytes = (sign_bit + 7) // 8 - byte
        split['signs'] += sign_bytes
        split['pad'] += size - byte - sign_bytes
    assert pos == len(data), (pos, len(data))
    print(f'{len(data)} bytes, {int(seen.sum())}/{total} blocks; split ' +
          ', '.join(f'{k} {v} ({100 * v / len(data):.1f}%)' for k, v in split.items()))
    with open(out, 'wb') as f:
        f.write(b'QCF1' + struct.pack('<I', len(bands)))
        for (c, level, band, w, h, bx, by, off), q in zip(bands, coef):
            rel = level - 1 if (c and chroma420) else level
            cls = (0 if c == 0 else 13) + (0 if band == 0 else 1 + 3 * min(rel, 3) + band - 1)
            q = np.clip(q, -32767, 32767).astype('<i2')
            f.write(struct.pack('<III', cls, q.shape[1], q.shape[0]))
            f.write(q.tobytes())
    nz = sum(int((q != 0).sum()) for q in coef)
    mx = max(int(np.abs(q).max()) for q in coef)
    entropy = lambda c: -sum(v * math.log2(v / sum(c.values())) for v in c.values()) / 8
    print(f'side information: quant codes {entropy(quant_codes):.0f} bytes, quant scales {entropy(quant_scales):.0f} bytes')
    print(f'{len(bands)} bands, {sum(q.size for q in coef)} coefficients, {nz} nonzero, max |level| {mx}')


if __name__ == '__main__':
    main()
