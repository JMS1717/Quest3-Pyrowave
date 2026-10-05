import unittest
from tools.quest3.prerecord_probe import parse_row,deltas

ROW='[Q3PW_PRERECORD] completed=240 prepared=32 submitted=28 canceled=3 deferred=14 rows=120 arm_prepare=120 arm_control=0 queue_mean_ms=2.1 queue_max_ms=4.0 record_mean_ms=0.5 completion_mean_ms=8.3 gpu_mean_ms=5.8 max_gpu=1'
class PrerecordProbeTests(unittest.TestCase):
    def test_honest_completion_and_queue_fields(self):
        r=parse_row(ROW);self.assertTrue(r['comparison_usable']);self.assertEqual(r['arm'],'prepare');self.assertEqual(r['completion_mean_ms'],8.3);self.assertEqual(r['queue_mean_ms'],2.1)
    def test_mixed_switch_window_excluded(self):
        r=parse_row(ROW.replace('arm_prepare=120 arm_control=0','arm_prepare=61 arm_control=59'));self.assertFalse(r['comparison_usable']);self.assertEqual(r['arm'],'mixed')
    def test_invalid_counter_and_nonfinite_rows_rejected(self):
        for a,b in [('submitted=28','submitted=33'),('rows=120','rows=0'),('arm_prepare=120','arm_prepare=119'),('max_gpu=1','max_gpu=2'),('queue_mean_ms=2.1','queue_mean_ms=4.1'),('gpu_mean_ms=5.8','gpu_mean_ms=nan')]:self.assertIsNone(parse_row(ROW.replace(a,b)))
    def test_counter_reset_is_not_gain(self):
        r=parse_row(ROW);new=dict(r,completed=120);self.assertIsNone(deltas(r,new));new=dict(r,completed=360,prepared=38,submitted=33,canceled=4,deferred=14);self.assertEqual(deltas(r,new),dict(completed=120,prepared=6,submitted=5,canceled=1,deferred=0))
