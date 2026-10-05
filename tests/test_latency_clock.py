import os
import tempfile
import unittest
from unittest.mock import patch

from tools.quest3.control import latency_stamp
from tools.quest3.latency_clock import MODULO, latency_ms, read_pairs, stamp_text, summarize


class LatencyClockTests(unittest.TestCase):
    def test_text_matches_five_digit_stamp(self):
        self.assertEqual(stamp_text(7), '00007')
        self.assertEqual(stamp_text(123456789), '56789')

    def test_latency_wraps_at_modulo(self):
        self.assertEqual(latency_ms(50040, 50000), 40)
        self.assertEqual(latency_ms(15, MODULO - 20), 35)
        self.assertEqual(latency_ms(50040, 50000, monitor_lag_ms=6.5), 46.5)
        with self.assertRaises(ValueError):
            latency_ms(MODULO, 0)

    def test_summary_rejects_misreads_and_names_scope(self):
        pairs = [(1040, 1000), (2038, 2000), (3042, 3000), (4000, 4100)]
        result = summarize(pairs, monitor_lag_ms=5)
        self.assertEqual(result['readings'], 4)
        self.assertEqual(result['rejected'], 1)  # lens ahead of monitor: wraps to ~100 s
        self.assertEqual(result['median_ms'], 45)
        self.assertEqual(result['max_ms'], 47)
        self.assertIn('composition-to-photon', result['measures'])

    def test_csv_skips_header_and_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'readings.csv')
            with open(path, 'w') as handle:
                handle.write('monitor,lens\n# frame 120\n1040,1000\n\n2038,2000\n')
            self.assertEqual(read_pairs(path), [(1040, 1000), (2038, 2000)])


class LatencyStampToggleTests(unittest.TestCase):
    def test_toggle_writes_and_verifies(self):
        state = {'session_settings': {'video': {'pyrowave': {'latency_stamp': False}}}}
        def write(values):
            self.assertEqual(values, {'session_settings.video.pyrowave.latency_stamp': True})
            state['session_settings']['video']['pyrowave']['latency_stamp'] = True
        with patch('tools.quest3.control.set_values', side_effect=write), \
                patch('tools.quest3.control.session', side_effect=lambda: state):
            result = latency_stamp(True)
        self.assertEqual(result['latency_stamp'], True)
        self.assertFalse(result['previous'])
        self.assertTrue(result['steamvr_restart_required'])

    def test_older_server_rejected_before_mutation(self):
        state = {'session_settings': {'video': {'pyrowave': {}}}}
        with patch('tools.quest3.control.set_values') as write, \
                patch('tools.quest3.control.session', return_value=state):
            with self.assertRaises(ValueError):
                latency_stamp(True)
            write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
