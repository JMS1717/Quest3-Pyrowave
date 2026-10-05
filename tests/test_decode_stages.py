import unittest
from tools.quest3.decode_stages import parse


def line(text, t=11, pid=42):
    return f'{t:.3f} {pid} 44 I pyroclient: {text}'


def stage(count, name='Dequant', value='4.000', **kwargs):
    return line(f'[Q3PW_DECODE_STAGE] complete={count} {name}: {value} ms per frame', **kwargs)


class StageTests(unittest.TestCase):
    def setup_line(self):
        return line('[Q3PW_DECODE_STAGE_SETUP] enabled=1 interval_decodes=120', t=8)

    def test_averages_exclude_startup_and_foreign_process(self):
        report = parse([self.setup_line(), stage(120, value='100'), stage(240),
                        stage(360, value='6.000'), stage(240, 'iDWT', '1.000'),
                        stage(480, value='99', pid=43)], 10, 12, 42)
        self.assertEqual(report['status'], 'parsed')
        self.assertEqual(report['stages']['Dequant']['intervals'], 2)
        self.assertEqual(report['stages']['Dequant']['mean_of_interval_averages_ms'], 5)
        self.assertFalse(report['performance_acceptance'])

    def test_disabled_missing_or_bad_record_invalidates(self):
        for lines in ([stage(240)], [self.setup_line(), stage(121)],
                      [self.setup_line(), stage(240), stage(240)],
                      [self.setup_line(), stage(240, value='NaN')]):
            r = parse(lines, 10, 12, 42)
            self.assertEqual(r['status'], 'invalid_records')
            self.assertIsNone(r['stages'])

    def test_interval_and_setup_state(self):
        r = parse([self.setup_line(), stage(240, t=9),
                   line('[Q3PW_DECODE_STAGE_SETUP] enabled=0 interval_decodes=120', t=10)], 10, 12, 42)
        self.assertEqual(r['status'], 'no_stage_records')
        with self.assertRaises(ValueError):
            parse([], 12, 10, 42)
        with self.assertRaises(ValueError):
            parse([], 10, 12, True)
