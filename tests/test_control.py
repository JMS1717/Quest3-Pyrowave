import unittest
from unittest.mock import patch
from tools.quest3.control import apply


class ControlTests(unittest.TestCase):
    def apply_offline(self, **kwargs):
        current = {}
        def write(values):
            for path, value in values.items():
                node = current
                fields = path.split('.')
                for field in fields[:-1]:
                    node = node.setdefault(field, {})
                node[fields[-1]] = value
        with patch('tools.quest3.control.set_values', side_effect=write), \
                patch('tools.quest3.control.session', return_value=current):
            result = apply('PyroWave', 400, 72, 'Auto',
                           {'refresh_extension': True, 'rates_hz': [72]}, **kwargs)
        return result, current['session_settings']

    def test_default_is_reliable_420_without_performance_claim(self):
        result, settings = self.apply_offline()
        self.assertEqual(settings['video']['pyrowave']['transport']['variant'], 'Tcp')
        self.assertEqual(settings['connection']['stream_protocol']['variant'], 'Tcp')
        self.assertFalse(settings['video']['pyrowave']['chroma_444'])
        self.assertTrue(result['settings_verified'])
        self.assertFalse(result['sustained_performance_verified'])

    def test_udp_requires_explicit_selection(self):
        result, settings = self.apply_offline(transport='Udp')
        self.assertEqual(result['transport'], 'Udp')
        self.assertEqual(settings['video']['pyrowave']['transport']['variant'], 'Udp')

    def test_unknown_transport_rejected_before_mutation(self):
        with self.assertRaises(ValueError):
            self.apply_offline(transport='Unknown')

    def test_wavelet_is_explicit_and_preserved(self):
        result, settings = self.apply_offline(wavelet='Cdf53')
        self.assertEqual(result['wavelet'], 'Cdf53')
        self.assertEqual(settings['video']['pyrowave']['wavelet']['variant'], 'Cdf53')

    def test_fragment_cdf53_rejected_before_mutation(self):
        with patch('tools.quest3.control.set_values') as write:
            with self.assertRaises(ValueError):
                apply('PyroWave', 1000, 120, 'Fragment',
                      {'refresh_extension':True, 'rates_hz':[120]}, wavelet='Cdf53')
            write.assert_not_called()
