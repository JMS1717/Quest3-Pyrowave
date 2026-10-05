import unittest
from tools.quest3.freshness import gaps, graph_rows, windows

PERIOD_NS = 1e9 / 120


def frame(index, queue_ms=1.5, network_ms=4.0, decoder_ms=7.0):
    return {'event': {'event_type': {'id': 'GraphStatistics', 'data': {
        'target_timestamp_ns': int(1_000_000_000 + index * PERIOD_NS),
        'decoder_queue_s': queue_ms / 1e3, 'network_s': network_ms / 1e3, 'decoder_s': decoder_ms / 1e3}}}}


def line(body, when=11, pid=42):
    return f'{when:.3f} {pid} 44 I ALVR: {body}'


def fresh(wait_us=0, ready=0, taken=110, empty=10, late_taken=4, late_superseded=6, superseded=7):
    margin = [taken - 10, 5, 5, 0, 0, 0, 0]
    late = [3, 3, 2, 1, 1, 0, 0]
    return (f'[Q3PW_FRESH] wait_us={wait_us} ready={ready} taken={taken} empty={empty} '
            f'late_taken={late_taken} late_superseded={late_superseded} superseded={superseded} '
            f'margin={margin} late={late} edges_us=[500, 1000, 2000, 3000, 4000, 8000]')


class GapTests(unittest.TestCase):
    def test_counts_missing_target_frames(self):
        indices = [i for i in range(241) if i not in (10, 50, 51)]
        r = gaps(graph_rows([frame(i) for i in indices]), 120)
        self.assertEqual(r['status'], 'parsed')
        self.assertEqual(r['step_histogram'], {'1': 235, '2': 1, '3': 1})
        self.assertAlmostEqual(r['lost_target_frames_per_s'], 1.5, places=6)
        self.assertAlmostEqual(r['displayed_target_fps'], 237 / 2, places=6)
        self.assertFalse(r['performance_acceptance'])

    def test_reports_neighbour_timings_and_ignores_other_events(self):
        events = [frame(i, network_ms=9.0 if i == 21 else 4.0) for i in range(40) if i != 20]
        events.append({'event': {'event_type': {'id': 'StatisticsSummary', 'data': {}}}})
        r = gaps(graph_rows(events), 120)
        self.assertEqual(r['frames'], 39)
        self.assertEqual(r['median_ms_before_and_after_gaps']['network_s'], [4.0, 9.0])

    def test_invalid_inputs(self):
        self.assertEqual(gaps([], 120)['status'], 'insufficient_frames')
        with self.assertRaises(ValueError):
            gaps(graph_rows([frame(0), frame(1)]), 0)
        with self.assertRaises(ValueError):
            gaps(graph_rows([frame(1), frame(1)]), 120)


class WindowTests(unittest.TestCase):
    def test_groups_alternating_configurations_and_skips_switch_windows(self):
        log = [line(fresh(0, 0), 10), line(fresh(0, 0), 11), line(fresh(2000, 0, taken=118), 12),
               line(fresh(2000, 0, taken=118), 13), line(fresh(0, 0), 14), line(fresh(0, 0), 15)]
        r = windows(log, 9, 16, 42)
        self.assertEqual(r['status'], 'parsed')
        base, wait = r['configurations']
        self.assertEqual((base['wait_us'], base['windows'], base['taken']), (0, 2, 220))
        self.assertEqual((wait['wait_us'], wait['windows'], wait['taken']), (2000, 1, 118))
        self.assertAlmostEqual(base['late_taken_fraction'], 0.4)

    def test_filters_process_and_interval(self):
        r = windows([line(fresh(), 8), line(fresh(), 11, pid=43)], 9, 16, 42, settle_windows=0)
        self.assertEqual(r['status'], 'no_probe_records')
        self.assertEqual(r['foreign_process_records'], 1)

    def test_packet_grace_switch_is_separate_and_excludes_transition(self):
        grace = fresh().replace('ready=0', 'ready=0 packet_grace_us=500')
        log = [line(fresh(), 10), line(fresh(), 11), line(grace, 12), line(grace, 13)]
        r = windows(log, 9, 16, 42)
        self.assertEqual(r['status'], 'parsed')
        self.assertEqual([(g['packet_grace_us'], g['windows']) for g in r['configurations']], [(0, 1), (500, 1)])

    def test_inconsistent_or_malformed_windows_invalidate(self):
        bad = fresh().replace('taken=110', 'taken=111', 1)
        for record in (line(bad), line(fresh().replace('margin=[', 'margin=[1, ')), fresh()):
            r = windows([record], 9, 16, 42, settle_windows=0)
            self.assertEqual(r['status'], 'invalid_records')
            self.assertIsNone(r['configurations'])

    def test_invalid_arguments(self):
        for start, end, pid in ((12, 10, 42), (10, float('nan'), 42), (10, 12, False)):
            with self.assertRaises(ValueError):
                windows([], start, end, pid)
