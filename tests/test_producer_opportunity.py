import unittest
from tools.quest3.producer_opportunity import FIELDS, parse


def line(t=11, pid=42, **changes):
    values = dict(zip(FIELDS, (120, 70, 40, 20, 10, 400000, 8000, 960000, 5)))
    values.update(changes)
    return f'{t:.3f} {pid} 44 I ALVR: [Q3PW_PRODUCER_PROBE] ' + ' '.join(
        f'{key}={values[key]}' for key in FIELDS)


class ProducerOpportunity(unittest.TestCase):
    def test_aggregate_sums_counts_but_takes_max_of_maxima(self):
        r = parse([line(), line(t=11.5)], 10, 12, 42)
        self.assertEqual(r['status'], 'parsed')
        self.assertEqual(r['totals']['calls'], 240)
        self.assertEqual(r['totals']['overlap_max_us'], 8000)
        self.assertAlmostEqual(r['mean_possible_overlap_us_per_call'], 800000 / 240)
        self.assertFalse(r['performance_acceptance'])

    def test_stale_pid_and_half_open_window_are_excluded(self):
        r = parse([line(t=9), line(t=10), line(t=12), line(pid=43)], 10, 12, 42)
        self.assertEqual(r['interval_records'], 1)

    def test_after_return_has_no_overlap_and_empty_packet_is_valid(self):
        for values in ({'pending': 0, 'arrived_after': 0}, {'pending': 50, 'arrived_after': 50}):
            r = parse([line(ready_before=0, arrived_during=0, overlap_sum_us=0,
                            overlap_max_us=0, **values)], 10, 12, 42)
            self.assertEqual(r['status'], 'parsed')
            self.assertEqual(r['mean_possible_overlap_us_per_call'], 0)

    def test_contradictory_counter_or_timing_invalidates_whole_result(self):
        for changes in ({'calls': 119}, {'pending': 121}, {'pending': 69},
                        {'headroom_at_snapshot': 121}, {'overlap_sum_us': 960001},
                        {'overlap_max_us': 400001}, {'overlap_max_us': 1000},
                        {'ready_before': 0, 'arrived_during': 0, 'arrived_after': 70}):
            with self.subTest(changes=changes):
                r = parse([line(), line(**changes)], 10, 12, 42)
                self.assertEqual(r['status'], 'invalid_records')
                self.assertIsNone(r['totals'])

    def test_malformed_rows_and_missing_identity_fail_closed(self):
        for row in ('[Q3PW_PRODUCER_PROBE] calls=120', line() + ' unexpected=1'):
            self.assertEqual(parse([row], 10, 12, 42)['status'], 'invalid_records')
        self.assertEqual(parse([], 10, 12, 42)['status'], 'no_producer_probe_records')
        for start, end, pid in ((12, 10, 42), (float('nan'), 12, 42), (10, 12, True), (10, 12, 0)):
            with self.assertRaises(ValueError): parse([], start, end, pid)
