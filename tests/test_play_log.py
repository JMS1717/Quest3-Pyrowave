import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from tools.quest3 import play_log


def event(kind, data):
    return {'event_type': {'id': kind, 'data': data}}


def log(text):
    return event('Log', {'severity': 'Error', 'content': text})


class PlayLogTests(unittest.TestCase):
    def test_seconds_are_aggregated_and_rates_derived(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            recorder = play_log.Recorder(out)
            for i in range(3):
                recorder.handle(event('GraphStatistics', {
                    'video_packet_bytes': 1000 * (i + 1), 'total_pipeline_latency_s': 0.030 + 0.001 * i,
                    'vsync_queue_s': 0.010}), 100.1 + 0.1 * i)
            recorder.handle(event('HeadsetTelemetry', {'pyrowave': {'completed_eye_copies': 1000}}), 100.5)
            recorder.handle(event('HeadsetTelemetry', {'pyrowave': {'completed_eye_copies': 1200}}), 101.5)
            recorder.handle(log('client at 10.1.2.3 [Q3PW_AUDIO] silent_batches=3 gap_ms_max=40'), 101.6)
            recorder.close(102.0)
            with (out / 'seconds.csv').open() as f:
                rows = list(csv.DictReader(f))
            self.assertEqual([r['frames'] for r in rows], ['3', '0'])
            self.assertEqual(rows[0]['bytes_mean'], '2000')
            self.assertEqual(rows[0]['vsync_queue_ms'], '10.0')
            self.assertEqual(rows[1]['completed_eye_copies_per_s'], '200.0')
            text = (out / 'log.txt').read_text()
            self.assertIn('<ip>', text)
            self.assertNotIn('10.1.2.3', text)

    def test_report_reads_forwarded_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            events = out / 'events.jsonl'
            lines = [
                {'recv_epoch': 120.0, 'event': log('[Q3PW_UDP_SEND] frames=1000 repeated_timestamps=10 datagrams=9')},
                {'recv_epoch': 121.0, 'event': log('[Q3PW_PRESENT] presents=207 timing_new=150 skipped=0')},
                {'recv_epoch': 122.0, 'event': log('Client c: [Error] [Q3PW_TRANSPORT] complete=10 dropped=2')},
                {'recv_epoch': 150.0, 'event': log('Client c: [Error] [Q3PW_TRANSPORT] complete=99 dropped=5')},
            ]
            events.write_text('\n'.join(json.dumps(l) for l in lines))
            play_log.replay(events, out / 'rec')
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                play_log.report(out / 'rec', 0)
            header, row = buffer.getvalue().strip().splitlines()
            cells = dict(zip(header.split(' | '), row.split(' | ')))
            self.assertEqual(cells['repeat %'], '1.00')
            self.assertEqual(cells['game fps'], '150')
            self.assertEqual(cells['video drops'], '3')


if __name__ == '__main__':
    unittest.main()
