import unittest
from tools.quest3.bench import parse_capabilities,plan,summarise,distribution,pyrowave_counter_window

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

    def test_eye_copy_paths_use_window_deltas_and_wall_timings(self):
        events=[{'event_type':{'id':'HeadsetTelemetry','data':{'pyrowave':p}}}
            for p in [
                {'direct_eye_copies':100,'staging_eye_copies':40,'eye_render_ms':[3.0],
                 'eye_acquire_wait_ms':[0.2],'eye_release_ms':[0.1]},
                {'direct_eye_copies':120,'staging_eye_copies':40,'eye_render_ms':[4.0]}]]
        r=summarise(events)
        self.assertEqual(r['eye_copy_counter_deltas'],{'direct_eye_copies':20,'staging_eye_copies':0,
            'completed_eye_copies':None,'pending_eye_copy_deferrals':None})
        self.assertEqual(r['eye_render_ms']['p50'],3.5)
        self.assertEqual(r['eye_acquire_wait_ms']['p50'],0.2)
        events.append({'event_type':{'id':'HeadsetTelemetry','data':{'pyrowave':{
            'direct_eye_copies':2,'staging_eye_copies':0}}}})
        self.assertIsNone(summarise(events)['eye_copy_counter_deltas']['direct_eye_copies'])
        self.assertIsNone(summarise([])['eye_copy_counter_deltas']['staging_eye_copies'])

    def test_submission_rate_exposes_missed_slots(self):
        from tools.quest3.bench import summarise
        events=[{'capture_elapsed_s':t,'event':{'event_type':{
            'id':'GraphStatistics','data':{'client_fps':fps}}}}
            for t,fps in [(0,120),(1/120,120),(2/120,120),(4/120,60)]]
        report=summarise(events,120)
        self.assertAlmostEqual(report['submitted_frame_rate_fps'],90)
        self.assertEqual(report['metrics']['client_fps']['p50'],120)
        self.assertFalse(report['sustained_requested_fps'])

    def test_nominal_fps_does_not_hide_sparse_submissions(self):
        events=[{'capture_elapsed_s':t,'event':{'event_type':{
            'id':'GraphStatistics','data':{'client_fps':120}}}}
            for t in (0,1/120,4/120)]
        r=summarise(events,120)
        self.assertEqual(r['metrics']['client_fps']['p01'],120)
        self.assertAlmostEqual(r['submitted_frame_rate_fps'],60)
        self.assertFalse(r['sustained_requested_fps'])

    def test_queued_copies_cannot_hide_slow_completion(self):
        events=[{'capture_elapsed_s':t,'event':{'event_type':{
            'id':'GraphStatistics','data':{'client_fps':120}}}}
            for t in (0,1/120,2/120)]
        events += [{'capture_elapsed_s':t,'event':{'event_type':{
            'id':'HeadsetTelemetry','data':{'pyrowave':{'completed_eye_copies':n,
                'direct_eye_copies':n+20,'staging_eye_copies':0,
                'pending_eye_copy_deferrals':d,'eye_completion_observed_ms':[8.5]}}}}}
            for t,n,d in [(0,10,1),(1,110,21)]]
        r=summarise(events,120)
        self.assertEqual(r['completed_eye_copy_rate_fps'],100)
        self.assertEqual(r['eye_copy_counter_deltas']['pending_eye_copy_deferrals'],20)
        self.assertFalse(r['sustained_requested_fps'])
        events[-1]['event']['event_type']['data']['pyrowave']['staging_eye_copies']=1
        self.assertIsNone(summarise(events)['completed_eye_copy_rate_fps'])
        events[-1]['event']['event_type']['data']['pyrowave']['staging_eye_copies']=0
        events[-1]['event']['event_type']['data']['pyrowave']['completed_eye_copies']=9
        self.assertIsNone(summarise(events)['completed_eye_copy_rate_fps'])

    def test_producer_and_consumer_rates_have_matching_endpoints(self):
        samples=[(t,{'complete':c,'superseded':s,'completed_eye_copies':e,
            'decode_failures':0}) for t,c,s,e in [(5,500,20,480),(6,620,27,593),(7,740,34,706)]]
        w=pyrowave_counter_window(samples)
        self.assertEqual(w['interval_s'],2)
        self.assertEqual(w['counter_deltas']['complete'],240)
        self.assertEqual(w['counter_rates_per_s']['complete'],120)
        self.assertEqual(w['counter_rates_per_s']['superseded'],7)
        self.assertEqual(w['counter_rates_per_s']['completed_eye_copies'],113)
        self.assertEqual(w['counter_deltas']['decode_failures'],0)
        self.assertIsNone(w['counter_deltas']['partial'])
        # A reset in the middle must not look valid after the counter catches up.
        samples[1][1]['complete']=1
        self.assertIsNone(pyrowave_counter_window(samples)['counter_deltas']['complete'])

    def test_invalid_telemetry_times_cannot_be_silently_excluded(self):
        for middle in (None,True,float('nan'),-1,0,3):
            samples=[(0,{'complete':0}),(middle,{'complete':120}),(2,{'complete':240})]
            w=pyrowave_counter_window(samples)
            self.assertEqual(w['counter_deltas']['complete'],240)
            self.assertIsNone(w['interval_s'])
            self.assertIsNone(w['counter_rates_per_s']['complete'])
        w=pyrowave_counter_window([(0,{'complete':False}),(1,{'complete':120})])
        self.assertIsNone(w['counter_deltas']['complete'])

    def test_short_rate_pass_is_not_sustained_acceptance(self):
        events=[{'capture_elapsed_s':i/120,'event':{'event_type':{
            'id':'GraphStatistics','data':{'client_fps':120}}}}
            for i in range(1801)]
        r=summarise(events,120)
        self.assertEqual(r['submission_rate_window_s'],15)
        self.assertTrue(r['requested_rate_screen_passed'])
        self.assertFalse(r['sustained_requested_fps'])

    def test_sustained_rate_check_needs_five_minutes_of_observed_frames(self):
        events=[{'capture_elapsed_s':i/120,'event':{'event_type':{
            'id':'GraphStatistics','data':{'client_fps':120}}}}
            for i in range(36001)]
        short=summarise(events[:-1],120)
        self.assertTrue(short['requested_rate_screen_passed'])
        self.assertFalse(short['sustained_requested_fps'])
        full=summarise(events,120)
        self.assertEqual(full['submission_rate_window_s'],300)
        self.assertTrue(full['sustained_requested_fps'])
        # A long but sparse submission stream still cannot pass at nominal 120.
        slow=summarise(events[::2],120)
        self.assertFalse(slow['requested_rate_screen_passed'])
        self.assertFalse(slow['sustained_requested_fps'])

    def test_missing_or_invalid_submission_times_invalidate_rate_window(self):
        for middle in (None,True,float('nan'),-1,0,3):
            events=[{'capture_elapsed_s':t,'event':{'event_type':{
                'id':'GraphStatistics','data':{'client_fps':120}}}}
                for t in (0,middle,2)]
            r=summarise(events,120)
            self.assertIsNone(r['submitted_frame_rate_fps'])
            self.assertIsNone(r['submission_rate_window_s'])
            self.assertFalse(r['requested_rate_screen_passed'])
            self.assertFalse(r['sustained_requested_fps'])

if __name__=='__main__':unittest.main()
