import unittest
from tools.quest3.surface_chart import FACTS,parse


def records(pid=42):
    rows = []
    for i,k in enumerate(('instance_dependencies','device_feature','prepared','visible_write',
                          'enqueue','layer','fences','stopping','retired','producer_destroyed')):
        message = ('[Q3PW_SURFACE_CHART] present_result=0 image=2 extent=64x32 format=37 one_shot=true'
                   if k == 'enqueue' else FACTS[k])
        rows.append(f'{10+i:.3f} {pid} 44 I pyroclient: {message}')
    return rows


class SurfaceLifecycleTests(unittest.TestCase):
    def test_complete_lifecycle_not_optical_acceptance(self):
        r = parse(records()+records(43),9,21,42)
        self.assertEqual(r['status'],'one_shot_lifecycle_verified')
        self.assertEqual(r['image_index'],2)
        self.assertFalse(r['performance_acceptance'])
        self.assertFalse(r['optical_presentation_verified'])

    def test_render_without_present_retirement_never_passes(self):
        for key in ('retired','stopping','producer_destroyed','device_feature'):
            lines = [l for l in records() if FACTS[key] not in l]
            self.assertEqual(parse(lines,9,21,42)['status'],'incomplete_or_failed')

    def test_shutdown_fence_waits_prove_retirement_without_prior_poll(self):
        lines = [l for l in records() if FACTS['fences'] not in l]
        r = parse(lines,9,21,42)
        self.assertEqual(r['status'],'one_shot_lifecycle_verified')
        self.assertFalse(r['nonblocking_poll_observed_both_fences'])

    def test_duplicate_enqueue_failure_or_late_visible_state_rejected(self):
        for lines in (records()+[records()[4]],
                      records()+['20.000 42 44 E pyroclient: [Q3PW_SURFACE_CHART] retire_failed=present preserve_inflight=true'],
                      [l.replace('13.000','20.000') for l in records()]):
            self.assertEqual(parse(lines,9,21,42)['status'],'incomplete_or_failed')

    def test_capture_must_include_startup_and_orderly_close(self):
        self.assertEqual(parse(records(),14,21,42)['status'],'incomplete_or_failed')
        self.assertEqual(parse(records(),9,18,42)['status'],'incomplete_or_failed')
        with self.assertRaises(ValueError):
            parse([],21,9,42)
