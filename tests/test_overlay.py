import unittest
from tools.quest3.overlay import summarize


def line(t, marker, body, pid=123):
    return f'{t} {pid} 456 E alvr: [{marker}] {body}'


class OverlayTests(unittest.TestCase):
    def test_capture_window_and_process_exclude_other_trial(self):
        draw = 'text_cpu_ms=1 acquire_wait_cpu_ms=2 renderer_submit_cpu_ms=3 release_cpu_ms=4'
        rows = [line(9, 'Q3PW_OVERLAY_CONTROL', 'mode=forced_visible'),
                line(9.5, 'Q3PW_OVERLAY_DRAW', draw), line(10, 'Q3PW_OVERLAY_DRAW', draw),
                line(11, 'Q3PW_OVERLAY_DRAW', draw, pid=999), line(21, 'Q3PW_OVERLAY_DRAW', draw)]
        r = summarize(rows, 10, 20, 123)
        self.assertEqual(r['redraws'], 1)
        self.assertEqual(r['redraw_cpu_ms']['renderer_submit_cpu_ms']['p50'], 3)
        self.assertEqual(r['ignored_foreign_process_records'], 1)
        self.assertEqual(r['control_before_interval'], 'forced_visible')

    def test_loop_mean_is_weighted_and_peak_is_preserved(self):
        rows = [line(11, 'Q3PW_LOOP', 'samples=120 overlay_mean_cpu_ms=1 overlay_max_cpu_ms=12'),
                line(12, 'Q3PW_LOOP', 'samples=60 overlay_mean_cpu_ms=4 overlay_max_cpu_ms=20')]
        r = summarize(rows, 10, 20, 123)
        self.assertEqual(r['overlay_update_mean_cpu_ms'], 2)
        self.assertEqual(r['overlay_update_max_cpu_ms'], 20)
        self.assertEqual(r['loop_frames'], 180)
        self.assertIsNone(r['redraw_cpu_ms']['text_cpu_ms'])

    def test_invalid_or_missing_diagnostics_cannot_report_zero_cost(self):
        for body in ('samples=0 overlay_mean_cpu_ms=1 overlay_max_cpu_ms=2',
                     'samples=120 overlay_mean_cpu_ms=2 overlay_max_cpu_ms=1',
                     'samples=120 overlay_mean_cpu_ms=NaN overlay_max_cpu_ms=3',
                     'samples=120 overlay_mean_cpu_ms=-1 overlay_max_cpu_ms=3',
                     'samples=120 overlay_mean_cpu_ms=1'):
            r = summarize([line(12, 'Q3PW_LOOP', body)], 10, 20, 123)
            self.assertEqual(r['status'], 'invalid_records')
            self.assertIsNone(r['overlay_update_mean_cpu_ms'])
        r = summarize([], 10, 20, 123)
        self.assertEqual(r['status'], 'no_timing_records')
        self.assertIsNone(r['overlay_update_max_cpu_ms'])

    def test_visibility_changes_remain_visible_and_do_not_prove_acceptance(self):
        r = summarize([line(9, 'Q3PW_OVERLAY_CONTROL', 'mode=forced_visible'),
                       line(11, 'Q3PW_OVERLAY_CONTROL', 'mode=forced_hidden'),
                       line(11, 'Q3PW_OVERLAY', 'visible=false forced=true')], 10, 20, 123)
        self.assertEqual(len(r['visibility_records_in_interval']), 2)
        self.assertEqual(r['visibility_records_in_interval'][1]['visible'], 'false')
        self.assertEqual(r['status'], 'no_timing_records')

    def test_invalid_capture_intervals_or_process_are_rejected(self):
        for start, end, pid in ((20, 10, 123), (10, float('inf'), 123), (10, 20, 0),
                                (10, 20, True), (-1, 20, 123)):
            with self.assertRaises(ValueError):
                summarize([], start, end, pid)
