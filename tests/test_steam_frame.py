"""Frame streaming geometry invariants; runtime and headset checks are separate."""
import unittest
from tools.quest3.foveation import configured_axis, forward, inverse


class SteamFrameFoveationTests(unittest.TestCase):
    def test_gaze_motion_never_changes_the_encoded_allocation(self):
        for size in (1728, 2176, 2688, 3264):
            expected = configured_axis(size, .5, 0, 2)['encoded']
            for shift in (-.89, -.63, -.17, 0, .37, .89):
                a = configured_axis(size, .5, shift, 2)
                self.assertEqual(a['encoded'], expected)
                self.assertGreater(a['lo'], 0)
                self.assertLess(a['hi'], 1)
                self.assertAlmostEqual(a['hi'] - a['lo'], .5)
        # Panel size aligned to the transport's 32-pixel requirement: 43.75% fewer pixels.
        self.assertEqual(configured_axis(2176, .5, 0, 2)['encoded'], 1632)
        self.assertEqual((1632 / 2176) ** 2, .5625)

    def test_moving_regions_remain_monotonic_and_invert_the_server_warp(self):
        for shift in (-.89, -.47, 0, .38, .89):
            a = configured_axis(2176, .5, shift, 2)
            coordinates = [inverse(n / 1024, a) for n in range(1025)]
            self.assertTrue(all(b > x for x, b in zip(coordinates, coordinates[1:])))
            for u in [n / 128 for n in range(129)] + [a['lo'], a['hi']]:
                self.assertAlmostEqual(forward(inverse(u, a), a), u, places=9)

    def test_an_old_frame_cannot_be_reconstructed_with_the_latest_gaze(self):
        old = configured_axis(2176, .5, -.7, 2)
        latest = configured_axis(2176, .5, .7, 2)
        self.assertGreater(abs(inverse(.5, old) - inverse(.5, latest)), .1)
        self.assertAlmostEqual(forward(inverse(.5, old), old), .5)
