import unittest
from tools.quest3.bench import parse_capabilities,plan,summarise,distribution

class BenchTests(unittest.TestCase):
    def test_unprobed_rates_are_not_declared_unsupported(self):
        caps=parse_capabilities('[Q3PW_CAPS] model=Quest3 rates=[72.0, 90.0, 120.0] runtime=true')
        p=plan(caps,repeats=1)
        self.assertEqual([r['requested_hz'] for r in p['skipped']],[144,207,240])
        self.assertEqual(len(p['cells']),45)
        self.assertTrue(all(r['status']=='not_confirmed' for r in p['skipped']))
        self.assertTrue(all(c['requested_hz'] in (72,90,120) for c in p['cells']))
        baseline=[c for c in p['cells'] if c['codec']=='PyroWave'
                  and c['requested_hz']==72 and c['mbps']==400]
        self.assertEqual({c['decode_path'] for c in baseline},{'Compute','Fragment'})
        self.assertTrue(all(c['chroma']=='420' and c['transport']=='Tcp' for c in baseline))
    def test_probe_can_confirm_non_enumerated_rates(self):
        caps=parse_capabilities('[Q3PW_CAPS] model=Quest3 rates=[90.0,120.0] runtime=true\n'
            '[Q3PW_PROBE] request=207 confirmed=true runtime_hz=Some(207.0) period_ns=4830918\n'
            '[Q3PW_CAPS] model=Quest3 rates=[90.0,120.0,207.0] runtime=true source=probe')
        self.assertEqual(caps['source'],'request_and_frame_period')
        self.assertEqual(caps['probe_results'],[{'requested_hz':207,'confirmed':True}])
        self.assertIn(207,{c['requested_hz'] for c in plan(caps,1)['cells']})
    def test_newest_capability_record_wins(self):
        caps=parse_capabilities('[Q3PW_CAPS] model=Quest3 rates=[90.0] runtime=true\n[Q3PW_CAPS] model=Quest3 rates=[90.0, 144.0] runtime=true')
        self.assertEqual(caps['rates_hz'],[90,144])
    def test_missing_and_fallback_capabilities_refused(self):
        with self.assertRaises(ValueError):parse_capabilities('debug.oculus.refreshRate=240')
        with self.assertRaises(ValueError):plan({'rates_hz':[90], 'refresh_extension':False})
    def test_no_frames_is_failure_not_zero_latency(self):
        r=summarise([])
        self.assertEqual(r['status'],'no_stream_frames');self.assertIsNone(r['metrics']['encoder_ms'])
    def test_pipeline_units_and_counter_delta(self):
        events=[{'event_type':{'id':'GraphStatistics','data':{'encoder_s':.002,'client_fps':90,'target_timestamp_ns':100}}},
                {'event_type':{'id':'StatisticsSummary','data':{'packets_lost_total':100}}},
                {'event_type':{'id':'StatisticsSummary','data':{'packets_lost_total':105}}}]
        r=summarise(events,90)
        self.assertEqual(r['metrics']['encoder_ms']['p50'],2);self.assertEqual(r['packet_loss_delta'],5)
        self.assertIsNone(r['optical_motion_to_photon_ms'])
    def test_percentiles_filter_invalid_numbers(self):
        self.assertEqual(distribution([None,float('nan'),1,3])['p50'],2)

if __name__=='__main__':unittest.main()
