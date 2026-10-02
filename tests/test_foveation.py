import unittest
from unittest.mock import patch
from tools.quest3.control import foveation
from tools.quest3.foveation import axis, forward, inverse, stereo_uv, math_report


class LightFoveationTests(unittest.TestCase):
    def test_native_geometry_and_420_budget(self):
        r = math_report()
        self.assertEqual(r['encoded_eye'], [1952, 2080])
        self.assertAlmostEqual(r['payload_bytes_per_frame'], 1_041_666.6666666666)
        self.assertEqual(r['raw_420_bytes_per_frame'], 12_180_480)
        self.assertGreater(r['encoded_pixel_savings_percent'], 11)
        self.assertLess(r['encoded_pixel_savings_percent'], 12)

    def test_dense_roundtrip_including_transition_boundaries(self):
        for size in (2080, 2208, 3072, 3232):
            a = axis(size)
            for u in [n / 4096 for n in range(4097)] + [a['lo'], a['hi']]:
                self.assertAlmostEqual(forward(inverse(u, a), a), u, places=9)

    def test_monotonic_map_and_full_density_center(self):
        a = axis(2080)
        values = [inverse(n / 1000, a) for n in range(1001)]
        self.assertTrue(all(b > x for x, b in zip(values, values[1:])))
        # One source pixel remains one encoded pixel in the center.
        self.assertAlmostEqual((inverse(.5 + 1 / 2080, a) - inverse(.5, a)) * a['encoded'], 1)

    def test_both_eyes_and_padding_mirrored_consistently(self):
        for u in (0, .1, .2, .5, .8, .9, 1):
            left, y = stereo_uv(u, .25, 0)
            right, yr = stereo_uv(1 - u, .25, 1)
            self.assertAlmostEqual(right, 1 - left)
            self.assertEqual(y, yr)
            self.assertTrue(0 <= left < .5)
            self.assertTrue(.5 < right <= 1)

    def test_invalid_geometry_and_rate(self):
        for value in (0, 8193, 2080.0, True):
            with self.assertRaises(ValueError):
                axis(value)
        for hz in (0, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                math_report(hz=hz)

    def test_older_server_cannot_accept_a_nonfunctional_toggle(self):
        with patch('tools.quest3.control.session', return_value={'session_settings': {'video': {'pyrowave': {}}}}), patch('tools.quest3.control.set_values') as write:
            with self.assertRaises(ValueError):
                foveation('light')
            write.assert_not_called()

    def test_toggle_readback_and_no_other_setting_changes(self):
        old = {'session_settings': {'video': {'pyrowave': {'light_foveated_encoding': False}}}}
        new = {'session_settings': {'video': {'pyrowave': {'light_foveated_encoding': True}}}}
        with patch('tools.quest3.control.session', side_effect=[old, new]), patch('tools.quest3.control.set_values') as write:
            result = foveation('light')
        write.assert_called_once_with({'session_settings.video.pyrowave.light_foveated_encoding': True})
        self.assertTrue(result['steamvr_restart_required'])
        self.assertFalse(result['perceptual_acceptance'])

    def test_geometry_checks_the_reduced_encoded_frame(self):
        from tools.quest3.resolution import evidence
        from tests.test_resolution import settings
        s = settings()
        s['light_foveated_encoding'] = True
        s['openvr'].update(enable_foveated_encoding=True,
            foveation_center_size_x=.8, foveation_center_size_y=.8,
            foveation_center_shift_x=0, foveation_center_shift_y=0,
            foveation_edge_ratio_x=1.5, foveation_edge_ratio_y=1.5)
        r = evidence(s, [{'pyrowave': {'encoded_width': 3904, 'encoded_height': 2080}}])
        self.assertEqual(r['status'], 'verified')
        self.assertEqual(r['expected_decode_eye'], [1952, 2080])
        s['openvr']['foveation_center_size_x'] = .800000011920929
        self.assertEqual(evidence(s, [{'pyrowave': {'encoded_width': 3904, 'encoded_height': 2080}}])['status'], 'verified')
        s['openvr']['foveation_center_shift_x'] = .1
        self.assertEqual(evidence(s)['status'], 'mismatch')
