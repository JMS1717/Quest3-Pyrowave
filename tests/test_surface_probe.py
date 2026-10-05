import unittest
from tools.quest3.surface_probe import parse


def line(text, t=11, pid=42):
    return f'{t:.3f} {pid} 44 I pyroclient: {text}'


CREATED = '[Q3PW_SURFACE_WSI] created=true vendor=0x5143 device=0x730 family=0 extent=64x32 images=3 format=37 usage=0x10 max_extent=8192x8192 min_images=2 max_images=0 submitted=false'
DONE = '[Q3PW_SURFACE_PROBE] native_result=0 destroyed=true submitted=false keep_gles=true'


class SurfaceEvidenceTests(unittest.TestCase):
    def test_process_and_interval_boundaries(self):
        r = parse([line(CREATED), line(DONE, t=12), line(DONE, pid=43),
                   line(DONE, t=9)], 10, 13, 42)
        self.assertEqual(r['status'], 'creation_verified')
        self.assertEqual(r['wsi']['extent'], [64, 32])
        self.assertFalse(r['performance_acceptance'])

    def test_partial_duplicate_or_reordered_never_passes(self):
        for rows in ([line(CREATED)], [line(DONE)], [line(CREATED), line(DONE), line(DONE)],
                     [line(CREATED, t=12), line(DONE, t=11)]):
            self.assertEqual(parse(rows, 10, 13, 42)['status'], 'unverified_or_failed')

    def test_native_failure_and_bad_extent_never_pass(self):
        for rows in ([line(CREATED), line(DONE.replace('result=0', 'result=-14'))],
                     [line(CREATED.replace('64x32', '4096x32')), line(DONE)],
                     [line(CREATED), line(DONE), line('[Q3PW_SURFACE_PROBE] unsupported keep_gles=true')]):
            self.assertEqual(parse(rows, 10, 13, 42)['status'], 'unverified_or_failed')

    def test_no_evidence_is_unknown_and_invalid_interval_rejected(self):
        self.assertEqual(parse([], 10, 13, 42)['status'], 'no_probe_records')
        with self.assertRaises(ValueError):
            parse([], float('nan'), 13, 42)
        with self.assertRaises(ValueError):
            parse([], 10, 13, True)
