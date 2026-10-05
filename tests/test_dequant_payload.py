"""Bit-plane/matrix contract for the opt-in shader's fixed-vector expansion.

CPU arithmetic oracle only. Pinned compilation and exact GPU readbacks remain
required; this does not execute a shader or establish GPU performance.
"""
import random
import unittest


def pack(coefficients, planes):
    return [sum(((value >> bit) & 1) << lane for lane, value in enumerate(coefficients))
            for bit in range(planes - 1, -1, -1)]


def vector_matrix(payload):
    lo, hi = [0] * 4, [0] * 4
    for bit, byte in zip(range(len(payload) - 1, -1, -1), payload):
        for lane in range(4):
            lo[lane] |= ((byte >> lane) & 1) << bit
            hi[lane] |= ((byte >> (lane + 4)) & 1) << bit
    lo = [float(value) + (0.5 if value else 0.0) for value in lo]
    hi = [float(value) + (0.5 if value else 0.0) for value in hi]
    return ((lo[0], lo[2], hi[0], hi[2]), (lo[1], lo[3], hi[1], hi[3]))


def expected_matrix(coefficients):
    return tuple(tuple(float(coefficients[row * 2 + col]) +
                       (0.5 if coefficients[row * 2 + col] else 0.0)
                       for row in range(4)) for col in range(2))


class PayloadContract(unittest.TestCase):
    def test_every_byte_at_every_legal_bit_position(self):
        # q_bits 0..15 plus two-bit local control 0..3 permits 0..18 planes.
        for bit in range(18):
            for byte in range(256):
                values = [((byte >> lane) & 1) << bit for lane in range(8)]
                self.assertEqual(vector_matrix(pack(values, 18)), expected_matrix(values))

    def test_zero_all_ones_and_random_maximum_depth(self):
        rng = random.Random(740)
        for planes in range(19):
            maximum = (1 << planes) - 1
            cases = [[0] * 8, [maximum] * 8,
                     [maximum if lane % 2 else 0 for lane in range(8)]]
            cases += [[rng.randrange(maximum + 1) for _ in range(8)] for _ in range(128)]
            for values in cases:
                self.assertEqual(vector_matrix(pack(values, planes)), expected_matrix(values))

    def test_matrix_order_preserves_sign_consumption(self):
        values = [0, 3, 7, 0, 0, 11, 13, 17]
        matrix = vector_matrix(pack(values, 5))
        original_sign_order = [lane for lane, value in enumerate(values) if value]
        actual_sign_order = [row * 2 + col for row in range(4) for col in range(2) if matrix[col][row]]
        self.assertEqual(actual_sign_order, original_sign_order)


if __name__ == '__main__':
    unittest.main()
