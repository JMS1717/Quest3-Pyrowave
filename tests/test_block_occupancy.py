import struct
import tempfile
import unittest
from pathlib import Path
from tools.quest3.block_occupancy import band_layout, main, parse_frame, read_wave, summarize


def packet(block_index, ballot, payload_words=3):
    header = struct.pack('<HHI', ballot, payload_words, block_index << 8)
    return header + bytes(4 * payload_words - 8)


def sequence(width, height):
    first = (width - 1) | (height - 1) << 14 | 1 << 31
    return struct.pack('<II', first, 0)


class OccupancyTests(unittest.TestCase):
    def test_layout_matches_decoder_order(self):
        layout = band_layout(256, 128, chroma444=False)
        # Level 4 has LL+3 bands for 3 components, levels 3..1 three bands each, level 0 luma only.
        self.assertEqual(len(layout), 12 + 9 * 3 + 3)
        self.assertEqual(layout[0][:5], (0, 4, 0, 8, 4))
        self.assertEqual(layout[-1][:5], (0, 0, 3, 128, 64))
        self.assertTrue(all(c == 0 for c, level, *_ in layout if level == 0))
        offsets = [entry[5] for entry in layout]
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(len(band_layout(256, 128, chroma444=True)), 12 + 9 * 3 + 9)

    def test_minimum_size_and_counts(self):
        layout = band_layout(64, 64, chroma444=True)
        self.assertEqual(layout[-1][3:5], (64, 64))  # 128 minimum image, halved

    def test_empty_frame_is_all_zero(self):
        layout, present = parse_frame(sequence(256, 128), 256, 128, False)
        report = summarize(layout, present)
        self.assertEqual(report['empty_share'], 1.0)
        self.assertEqual(report['payload_bytes'], 0)

    def test_one_full_block_and_one_partial(self):
        layout = band_layout(256, 128, chroma444=False)
        hh0 = layout[-1]
        first = hh0[5]
        frame = sequence(256, 128) + packet(first, 0xffff) + packet(first + 1, 0x0001, 4)
        layout, present = parse_frame(frame, 256, 128, False)
        report = summarize(layout, present)
        band = report['bands'][-1]
        self.assertEqual((band['blocks_present'], band['subblocks_nonzero']), (2, 17))
        self.assertEqual(band['subblocks_8x8'], 16 * 8)
        self.assertAlmostEqual(band['zero_coefficient_share'], 1 - 17 / 128)
        self.assertEqual(report['payload_bytes'], 28)
        self.assertAlmostEqual(report['level0_high_share_of_all'], 0.5)  # 75% of luma, luma 2/3 at 4:2:0

    def test_duplicate_packet_counts_once_and_errors(self):
        first = band_layout(256, 128, False)[-1][5]
        frame = sequence(256, 128) + packet(first, 1) + packet(first, 0xffff)
        layout, present = parse_frame(frame, 256, 128, False)
        self.assertEqual(present[first][0], 1)
        with self.assertRaises(ValueError):
            parse_frame(sequence(255, 128), 256, 128, False)
        with self.assertRaises(ValueError):
            parse_frame(packet(10 ** 6, 1), 256, 128, False)
        with self.assertRaises(ValueError):
            parse_frame(packet(first, 1)[:-1], 256, 128, False)

    def test_wave_container_round_trip(self):
        frame = sequence(256, 128) + packet(band_layout(256, 128, False)[-1][5], 3)
        data = b'PYROWAVE' + struct.pack('<8i', 256, 128, 0, 0, 0, 72, 1, 0) + struct.pack('<I', len(frame)) + frame
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'f.wave'
            path.write_bytes(data)
            self.assertEqual(read_wave(path)[:3], (256, 128, False))
            self.assertEqual(main([str(path), '--json']), 0)
            path.write_bytes(data[:-1])
            with self.assertRaises(ValueError):
                read_wave(path)


if __name__ == '__main__':
    unittest.main()
