"""tools/quest3/frame_trace.py on a synthetic trace: rates, supersede and empty accounting."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools' / 'quest3'))
import frame_trace  # noqa: E402

PERIOD_US = 5000


def synthetic():
    recs = [('K', 0, 1_000_000_000, 2000, 0)]
    # 20 display periods; frame k normally publishes 1 ms before selection k. Frame 5 publishes
    # 0.5 ms after selection 5, which finds nothing (empty), and frame 6 then supersedes it.
    for k in range(20):
        sel = 10_000 + k * PERIOD_US
        recs.append(('W', sel - 200, 1_000_000_000 + (sel + 20_000) * 1000, PERIOD_US * 1000, 1))
        recs.append(('G', sel, 0, 0, 0))
        ident = 7_000_000 + k
        pub = sel + 500 if k == 5 else sel - 1000
        if k == 5:
            recs.append(('E', sel + 1, 0, 0, 0))
        recs += [('A', pub - 3000, ident, 100, 0), ('S', pub - 2500, ident, k, 0), ('D', pub - 10, ident, 2000, 2400),
                 ('U', pub, ident, 1, 0)]
    recs.sort(key=lambda r: r[1])
    out, last_pub = [], None
    for r in recs:
        out.append(r)
        if r[0] == 'U':
            if last_pub is not None:
                out.append(('X', r[1], last_pub, 0, 0))
            last_pub = r[2]
        if r[0] == 'G' and last_pub is not None and r[1] != 10_000 + 5 * PERIOD_US:
            out.append(('T', r[1] + 1, last_pub, 100, 0))
            last_pub = None
    return out


class FrameTraceTests(unittest.TestCase):
    def test_parse_reads_batched_lines(self):
        line = '   1791377321.500  1 2 I Q3PW    : [Q3PW_TRACE] v1 dropped=2 S,1000,7,1,0;D,3000,7,2000,2400;'
        records, dropped, offset = frame_trace.parse([line, 'unrelated'])
        self.assertEqual(dropped, 2)
        self.assertEqual(records[1], ('D', 3000, 7, 2000, 2400))
        self.assertAlmostEqual(offset, 1791377321.5 - 0.003, places=6)

    def test_counts_supersede_and_empty(self):
        out = frame_trace.analyze(synthetic())
        span = out['span_s']
        unique = out['unique_per_s']
        self.assertAlmostEqual(unique['superseded'] * span, 1, delta=0.05)
        self.assertAlmostEqual(unique['empty_episodes'] * span, 1, delta=0.05)
        self.assertAlmostEqual(unique['published'] * span, 20, delta=0.05)
        self.assertEqual(out['xr_minus_mono_ms'], 1000 - 0.001)
        self.assertEqual(out['empty_cause']['decoding_at_selection'], 1)
        self.assertAlmostEqual(out['publications_per_selection_interval']['2'] * 19, 1, delta=0.01)


if __name__ == '__main__':
    unittest.main()
