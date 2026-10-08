import unittest
from unittest.mock import patch
from tools.quest3.control import foveation
import math
import struct
from tools.quest3.foveation import PROFILES, axis, encoded_eye, forward, inverse, stereo_uv, math_report, full_density_deg


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
            for dim in (0, 1):
                a = axis(size, dim=dim)
                for u in [n / 4096 for n in range(4097)] + [a['lo'], a['hi']]:
                    self.assertAlmostEqual(forward(inverse(u, a), a), u, places=9)

    def test_monotonic_map_and_full_density_center(self):
        a = axis(2080)
        values = [inverse(n / 1000, a) for n in range(1001)]
        self.assertTrue(all(b > x for x, b in zip(values, values[1:])))
        # One source pixel remains one encoded pixel in the center.
        self.assertAlmostEqual((inverse(.5 + 1 / 2080, a) - inverse(.5, a)) * a['encoded'], 1)
        # Centre pixels land a whole number of quarter pixels from decoded pixels, so the packed
        # draw can read the centre unfiltered (direct_eye.rs foveation_regions).
        for profile in PROFILES:
            for dim, size in enumerate((2080, 2208)):
                a = axis(size, profile, dim)
                offset = (inverse(.5, a) * a['encoded'] - .5 * size) * 4
                self.assertAlmostEqual(offset, round(offset), places=6, msg=(profile, dim))

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
            foveation_center_shift_x=.889, foveation_center_shift_y=-.889,
            foveation_edge_ratio_x=1.5, foveation_edge_ratio_y=1.5)
        r = evidence(s, [{'pyrowave': {'encoded_width': 3904, 'encoded_height': 2080}}])
        self.assertEqual(r['status'], 'verified')
        self.assertEqual(r['expected_decode_eye'], [1952, 2080])
        s['openvr']['foveation_center_size_x'] = .800000011920929
        self.assertEqual(evidence(s, [{'pyrowave': {'encoded_width': 3904, 'encoded_height': 2080}}])['status'], 'verified')
        s['openvr']['foveation_center_shift_x'] = 0
        self.assertEqual(evidence(s)['status'], 'mismatch')


def _f32(x):
    return struct.unpack('f', struct.pack('f', x))[0]


def _rust_encoded(size, center, edge):
    # alvr/graphics/src/stream.rs foveated_encoding_shader_constants: all f32.
    w, c, e = _f32(size), _f32(center), _f32(edge)
    edge_size = _f32(w - _f32(c * w))
    aligned = _f32(1 - _f32(_f32(math.ceil(_f32(edge_size / _f32(e * 2))) * _f32(e * 2)) / w))
    scale = _f32(aligned + _f32(_f32(1 - aligned) / e))
    return math.ceil(_f32(_f32(scale * w) / 32)) * 32


def _cpp_encoded(size, center, edge):
    # server_openvr/cpp/platform/win32/FFR.cpp CalculateFoveationVars: float storage, double literals.
    w, c, e = _f32(size), _f32(center), _f32(edge)
    edge_size = _f32(w - _f32(c * w))
    aligned = _f32(1. - math.ceil(edge_size / (e * 2.)) * (e * 2.) / w)
    scale = _f32(aligned + (1. - aligned) / e)
    return math.ceil(_f32(_f32(scale * w) / _f32(32.))) * 32


def _rust_shift_steps(size, center, shift, edge):
    # stream.rs foveated_encoding_dynamic_params: all f32.
    w, c, s, e = _f32(size), _f32(center), _f32(shift), _f32(edge)
    edge_size = _f32(w - _f32(c * w))
    aligned = _f32(1 - _f32(_f32(math.ceil(_f32(edge_size / _f32(e * 2))) * _f32(e * 2)) / w))
    edge_aligned = _f32(w - _f32(aligned * w))
    return math.ceil(_f32(_f32(s * edge_aligned) / _f32(e * 2)))


def _cpp_shift_steps(size, center, shift, edge):
    # FFR.cpp CalculateFoveationVars: float storage, double literals.
    w, c, s, e = _f32(size), _f32(center), _f32(shift), _f32(edge)
    edge_size = _f32(w - _f32(c * w))
    aligned = _f32(1. - math.ceil(edge_size / (e * 2.)) * (e * 2.) / w)
    edge_aligned = _f32(w - _f32(aligned * w))
    return math.ceil(_f32(s * edge_aligned) / (e * 2.))


class FoveationProfileTests(unittest.TestCase):
    def test_profile_geometry_at_native_quest_size(self):
        expected = {'light': ([1952, 2080], 11.59), 'balanced': ([1888, 1920], 21.07),
                    'strong': ([1856, 1792], 27.58)}
        for profile, (eye, savings) in expected.items():
            r = math_report(profile=profile, hz=207)
            self.assertEqual(r['encoded_eye'], eye)
            self.assertAlmostEqual(r['encoded_pixel_savings_percent'], savings, delta=0.01)
            self.assertEqual(r['mode'], profile)

    def test_client_server_and_model_sizes_agree_for_every_profile(self):
        # A disagreement would decode one size and un-warp another: misregistered eyes.
        for profile, p in PROFILES.items():
            for dim in (0, 1):
                center, shift, edge = p['center'][dim], p['shift'][dim], p['edge']
                for size in range(512, 4097, 8):
                    a = axis(size, profile, dim)
                    self.assertEqual(_rust_encoded(size, center, edge), a['encoded'], (profile, size))
                    self.assertEqual(_cpp_encoded(size, center, edge), a['encoded'], (profile, size))
                    # The shift is rounded to whole steps; a step apart would misplace the centre.
                    steps = round(a['shift'] * (1 - a['center']) * size / (2 * edge))
                    self.assertEqual(_rust_shift_steps(size, center, shift, edge), steps, (profile, dim, size))
                    self.assertEqual(_cpp_shift_steps(size, center, shift, edge), steps, (profile, dim, size))

    def test_roundtrip_and_full_density_center_for_every_profile(self):
        for profile in PROFILES:
            for size in (2080, 2208, 3072, 3232):
                for dim in (0, 1):
                    a = axis(size, profile, dim)
                    for u in [n / 1024 for n in range(1025)] + [a['lo'], a['hi']]:
                        self.assertAlmostEqual(forward(inverse(u, a), a), u, places=9)
            for dim in (0, 1):
                a = axis(2080, profile, dim)
                self.assertAlmostEqual((inverse(.5 + 1 / 2080, a) - inverse(.5, a)) * a['encoded'], 1)

    def test_both_eyes_see_the_middle_at_full_density(self):
        # The binocular overlap runs from 40 degrees left to 40 degrees right (each eye's nasal
        # limit). It must stay at full density in both eyes, and the vertical band must be
        # centred on straight ahead, not on the middle of the lopsided eye image.
        for profile in PROFILES:
            for eye in ((2080, 2208), (2592, 2784), (3072, 3216)):
                (outer, nasal), (up, down) = full_density_deg(eye, profile)
                self.assertLessEqual(outer, -40, (profile, eye))
                self.assertGreaterEqual(nasal, 39, (profile, eye))
                self.assertLess(abs(up + down), 1.5, (profile, eye))
                self.assertGreater(up, 35, (profile, eye))
                for dim in (0, 1):
                    a = axis(eye[dim], profile, dim)
                    self.assertTrue(0 < a['lo'] < a['hi'] < 1, 'every edge band keeps some width')

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            encoded_eye((2080, 2208), 'extreme')

    def test_profile_toggle_writes_both_settings_and_reads_back(self):
        old = {'session_settings': {'video': {'pyrowave': {'light_foveated_encoding': False,
               'foveation_profile': {'variant': 'Light'}}}}}
        new = {'session_settings': {'video': {'pyrowave': {'light_foveated_encoding': True,
               'foveation_profile': {'variant': 'Strong'}}}}}
        with patch('tools.quest3.control.session', side_effect=[old, new]), patch('tools.quest3.control.set_values') as write:
            result = foveation('light', 'strong')
        write.assert_called_once_with({'session_settings.video.pyrowave.light_foveated_encoding': True,
            'session_settings.video.pyrowave.foveation_profile.variant': 'Strong'})
        self.assertEqual((result['profile'], result['previous_profile']), ('strong', 'light'))

    def test_profile_requires_a_server_with_the_setting(self):
        old = {'session_settings': {'video': {'pyrowave': {'light_foveated_encoding': False}}}}
        with patch('tools.quest3.control.session', return_value=old), patch('tools.quest3.control.set_values') as write:
            with self.assertRaises(ValueError):
                foveation('light', 'strong')
            write.assert_not_called()

    def test_geometry_check_uses_the_negotiated_profile(self):
        from tools.quest3.resolution import evidence
        from tests.test_resolution import settings
        s = settings()
        s['light_foveated_encoding'] = True
        s['foveation_profile'] = 'strong'
        s['openvr'].update(enable_foveated_encoding=True,
            foveation_center_size_x=.77, foveation_center_size_y=.6,
            foveation_center_shift_x=.889, foveation_center_shift_y=-.471,
            foveation_edge_ratio_x=2.0, foveation_edge_ratio_y=2.0)
        r = evidence(s, [{'pyrowave': {'encoded_width': 3712, 'encoded_height': 1792}}])
        self.assertEqual(r['status'], 'verified')
        self.assertEqual(r['expected_decode_eye'], [1856, 1792])
        s['foveation_profile'] = 'light'
        self.assertEqual(evidence(s)['status'], 'mismatch')
