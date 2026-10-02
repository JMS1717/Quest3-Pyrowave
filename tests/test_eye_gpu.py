import unittest
from tools.quest3.eye_gpu import summarize


def line(time, body, pid=123, setup=False):
    return f'{time}.000 {pid} 456 I Q3PW: [Q3PW_EYE_GPU{"_SETUP" if setup else ""}] {body}'


class EyeGpuTests(unittest.TestCase):
    def test_weighted_mean_retains_windows_without_inventing_pooled_percentiles(self):
        rows = [line(9, 'requested=1 active=1 bits=64 slots=8', setup=True),
                line(12, 'samples=100 mean_ms=1 p50_ms=1 p95_ms=2 max_ms=3 invalid=0 skipped=1'),
                line(13, 'samples=50 mean_ms=4 p50_ms=3 p95_ms=5 max_ms=6 invalid=2 skipped=3')]
        result = summarize(rows, 10, 20, 123)
        self.assertEqual(result['status'], 'parsed')
        self.assertEqual(result['gpu_draw_mean_ms'], 2)
        self.assertEqual(result['gpu_draw_max_ms'], 6)
        self.assertEqual(result['invalid_gpu_queries'], 2)
        self.assertEqual(result['skipped_measurements'], 4)
        self.assertEqual(len(result['windows']), 2)
        self.assertFalse(result['performance_acceptance'])

    def test_missing_setup_wrong_process_and_interval_cannot_establish_activation(self):
        rows = [line(9, 'active=1', pid=321, setup=True),
                line(8, 'samples=1 mean_ms=1 p50_ms=1 p95_ms=1 max_ms=1 invalid=0 skipped=0'),
                line(21, 'active=1', setup=True)]
        result = summarize(rows, 10, 20, 123)
        self.assertEqual(result['status'], 'setup_not_verified')
        self.assertIsNone(result['gpu_draw_mean_ms'])

    def test_empty_disjoint_windows_are_unknown_not_zero_cost(self):
        result = summarize([line(9, 'active=1', setup=True),
                            line(12, 'samples=0 invalid=120 skipped=2 elapsed_ms=unknown')], 10, 20, 123)
        self.assertEqual(result['status'], 'no_valid_samples')
        self.assertIsNone(result['gpu_draw_max_ms'])
        self.assertEqual(result['invalid_gpu_queries'], 120)

    def test_bad_timings_and_probe_failure_refuse_aggregate(self):
        valid = line(12, 'samples=1 mean_ms=1 p50_ms=1 p95_ms=1 max_ms=1 invalid=0 skipped=0')
        for body in ('samples=1 mean_ms=NaN p50_ms=1 p95_ms=1 max_ms=1 invalid=0 skipped=0',
                     'samples=1 mean_ms=1 p50_ms=3 p95_ms=2 max_ms=4 invalid=0 skipped=0',
                     'samples=-1 invalid=0 skipped=0 elapsed_ms=unknown'):
            result = summarize([line(9, 'active=1', setup=True), valid, line(13, body)], 10, 20, 123)
            self.assertEqual(result['status'], 'invalid_records')
            self.assertIsNone(result['gpu_draw_mean_ms'])
        result = summarize([line(9, 'active=1', setup=True), valid,
                            line(13, 'active=0 reason=gl_error', setup=True)], 10, 20, 123)
        self.assertEqual(result['status'], 'disabled_during_interval')
        self.assertIsNone(result['gpu_draw_mean_ms'])

    def test_invalid_capture_inputs_refused(self):
        for start, end, pid in ((10, 10, 123), (float('nan'), 20, 123), (10, 20, 0), (10, 20, True)):
            with self.assertRaises(ValueError):
                summarize([], start, end, pid)
