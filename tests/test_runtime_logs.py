import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.quest3 import bench
from tools.quest3.runtime_logs import extract, parse_pid, process_evidence


def row(epoch, pid, message):
    return f'{epoch:.3f} {pid} 456 I q3pw: {message}'


class RuntimeLogTests(unittest.TestCase):
    def test_old_process_cannot_override_current_capabilities(self):
        log='\n'.join([
            row(100,111,'[Q3PW_CAPS] model=Quest3 rates=[72,120] runtime=true'),
            row(101,222,'[Q3PW_CAPS] model=Quest3 rates=[72,207,240] runtime=true'),
            '[Q3PW_CAPS] model=Quest3 rates=[240] runtime=true',
        ])
        records=extract(log,111)
        self.assertEqual(len(records),1)
        caps=bench.parse_capabilities('\n'.join(r['message'] for r in records))
        self.assertEqual(caps['rates_hz'],[72,120])

    def test_current_startup_probe_context_keeps_order(self):
        log='\n'.join([
            row(100,111,'[Q3PW_CAPS] model=Quest3 rates=[72,120] runtime=true'),
            row(101,111,'[Q3PW_PROBE] request=207 confirmed=true runtime_hz=Some(207.0) period_ns=4830918'),
            row(102,111,'[Q3PW_CAPS] model=Quest3 rates=[72,120,207] runtime=true source=probe'),
            row(103,222,'[Q3PW_CAPS] model=Quest3 rates=[72] runtime=true'),
        ])
        caps=bench.parse_capabilities('\n'.join(r['message'] for r in extract(log,111)))
        self.assertEqual(caps['source'],'request_and_frame_period')
        self.assertEqual(caps['probe_results'],[{'requested_hz':207,'confirmed':True}])

    def test_only_identified_diagnostics_are_retained(self):
        message='[Q3PW_EFFECTIVE] requested=Some(120.0) runtime_hz=Ok(120.0) period_ns=8333547'
        log='\n'.join([row(100,111,'unrelated app message'),row(101,111,message),
                       'not epoch format '+message,row(102,222,message)])
        self.assertEqual(extract(log,111),[{'epoch_s':101,'message':message}])
        with patch.object(bench,'adb_run',return_value=log) as adb:
            self.assertEqual(bench.runtime_evidence('adb',111),[message])
            self.assertIn('--pid=111',adb.call_args.args)
            self.assertIn('epoch',adb.call_args.args)

    def test_ambiguous_missing_and_invalid_pids_are_refused(self):
        for value in ('','0','-1','111 222','1.0','2147483648',None,True):
            with self.subTest(value=value),self.assertRaises(ValueError):parse_pid(value)
        self.assertEqual(parse_pid(' 111\n'),111)
        for pid in (None,True,0,-1,'111',2**31):
            with self.subTest(pid=pid),self.assertRaises(ValueError):extract('',pid)

    def test_restart_or_unavailable_endpoint_is_not_stable(self):
        self.assertTrue(process_evidence(111,111)['same_pid_during_capture'])
        for before,after in ((111,222),(111,None),(None,None),(True,True),(0,0)):
            self.assertFalse(process_evidence(before,after)['same_pid_during_capture'])

    def test_capture_refuses_missing_client_before_opening_stream(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'capture'
            args=SimpleNamespace(out=str(out),adb='adb')
            websocket=SimpleNamespace(create_connection=lambda *a,**k: self.fail('Unexpected stream access'))
            with patch.dict('sys.modules',{'websocket':websocket}), \
                 patch.object(bench,'snapshot',return_value={}), \
                 patch.object(bench,'client_build',return_value={}), \
                 patch.object(bench,'active_settings',return_value={}), \
                 patch.object(bench,'client_pid',side_effect=ValueError('No client')):
                self.assertEqual(bench.capture(args),1)
            report=json.loads((out/'report.json').read_text())
            self.assertEqual(report['status'],'client_process_unavailable')
            self.assertFalse(report['client_process']['same_pid_during_capture'])

    def test_capture_restart_invalidates_even_a_passing_rate_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            out=Path(folder)/'capture'
            args=SimpleNamespace(out=str(out),adb='adb',hz=120,seconds=0,events='unused')
            summary={'status':'measured','frames':1,'headset_telemetry':[],
                     'requested_rate_screen_passed':True,'sustained_requested_fps':True}
            websocket=SimpleNamespace(create_connection=lambda *a,**k: SimpleNamespace(close=lambda:None))
            with patch.dict('sys.modules',{'websocket':websocket}), \
                 patch.object(bench.threading,'Thread'), \
                 patch.object(bench,'snapshot',return_value={}), \
                 patch.object(bench,'client_build',return_value={}), \
                 patch.object(bench,'active_settings',return_value={'openvr':{'refresh_rate':120}}), \
                 patch.object(bench,'summarise',return_value=summary), \
                 patch.object(bench,'client_pid',side_effect=[111,222]), \
                 patch.object(bench,'runtime_evidence') as logs:
                self.assertEqual(bench.capture(args),1)
                logs.assert_not_called()
            report=json.loads((out/'report.json').read_text())
            self.assertEqual(report['status'],'client_process_changed_or_unavailable')
            self.assertFalse(report['requested_rate_screen_passed'])
            self.assertFalse(report['sustained_requested_fps'])
            self.assertEqual(report['runtime_evidence'],[])


if __name__=='__main__':unittest.main()
