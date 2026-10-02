import unittest
from tools.quest3.scheduling import summarize


def line(body, when=11, pid=42):
    return f'{when:.3f} {pid} 44 I ALVR: {body}'


SELECTION = '[Q3PW_SELECTION] slots=120 held=0 immediate=108 late=0 empty=10 copy_pending=2 no_decoder=0 select_mean_us=5 select_max_us=20'
LOOP = '[Q3PW_LOOP] samples=120 wait_mean_ms=4 end_mean_ms=1 overlay_mean_cpu_ms=0.1'


class SchedulingTests(unittest.TestCase):
    def test_classify_empty_separately_from_copy_pending(self):
        r = summarize([line(SELECTION), line(LOOP)], 10, 12, 42)
        self.assertEqual(r['status'], 'parsed')
        self.assertEqual(r['outcome_counts']['empty'], 10)
        self.assertEqual(r['outcome_counts']['copy_pending'], 2)
        self.assertEqual(sum(r['outcome_counts'].values()), 120)
        self.assertFalse(r['performance_acceptance'])

    def test_weight_whole_windows_and_new_loop_fields(self):
        second = SELECTION.replace('slots=120', 'slots=240').replace('immediate=108', 'immediate=228').replace('select_mean_us=5', 'select_mean_us=8')
        r = summarize([line(SELECTION), line(second), line(LOOP + ' wait_call_mean_ms=3 begin_mean_ms=0.1 render_mean_ms=2 frame_work_mean_ms=6')], 10, 12, 42)
        self.assertEqual(r['selection_mean_cpu_us'], 7)
        self.assertEqual(r['loop_mean_cpu_ms']['render_mean_ms'], 2)

    def test_legacy_loop_reports_unknown_new_fields(self):
        r = summarize([line(LOOP)], 10, 12, 42)
        self.assertEqual(r['status'], 'legacy_loop_only')
        self.assertIsNone(r['loop_mean_cpu_ms']['render_mean_ms'])

    def test_filter_process_and_interval(self):
        r = summarize([line(SELECTION, 9), line(SELECTION, 13), line(SELECTION, pid=43)], 10, 12, 42)
        self.assertEqual(r['status'], 'no_probe_records')
        self.assertEqual(r['foreign_process_records'], 1)

    def test_bad_partition_invalidates_aggregate(self):
        for bad in (SELECTION.replace('empty=10', 'empty=11'), SELECTION.replace('held=0', 'held=-1')):
            r = summarize([line(SELECTION), line(bad)], 10, 12, 42)
            self.assertEqual(r['status'], 'invalid_records')
            self.assertIsNone(r['outcome_counts'])

    def test_unknown_nonfinite_or_negative_times_rejected(self):
        for value in ('unknown', 'nan', 'inf', '-1', '21'):
            r = summarize([line(SELECTION.replace('select_mean_us=5', 'select_mean_us=' + value))], 10, 12, 42)
            self.assertEqual(r['status'], 'invalid_records')

    def test_invalid_arguments(self):
        for start, end, pid in ((12, 10, 42), (10, float('inf'), 42), (10, 12, True), (10, 12, 0)):
            with self.assertRaises(ValueError):
                summarize([], start, end, pid)

    def test_malformed_prefix_not_silently_accepted(self):
        r = summarize([SELECTION], 10, 12, 42)
        self.assertEqual(r['status'], 'invalid_records')
