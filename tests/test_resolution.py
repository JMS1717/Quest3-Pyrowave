import copy
import unittest
from unittest.mock import patch
from tools.quest3.control import resolution
from tools.quest3.resolution import assignments, profiles, evidence, RENDER_FIELD, ENCODE_FIELD
from tools.quest3.budget import frame_budget


def absolute(width, height):
    return {'variant':'Absolute', 'Absolute':{'width':width, 'height':{'set':True, 'content':height}}}


def settings(render=(3072,3216), encode=(2080,2208)):
    return {'codec':'PyroWave', 'configured_render_view_resolution':absolute(*render),
            'configured_view_resolution':absolute(*encode),
            'openvr':{'target_eye_resolution_width':(render[0]+31)//32*32, 'target_eye_resolution_height':(render[1]+31)//32*32,
                      'eye_resolution_width':encode[0], 'eye_resolution_height':encode[1],
                      'enable_foveated_encoding':False}}


class ResolutionTests(unittest.TestCase):
    def test_profile_applies_both_sizes_with_explicit_height(self):
        current={'session_settings':{'video':{RENDER_FIELD:absolute(2080,2208),
                                             ENCODE_FIELD:absolute(2080,2208)}}}
        def write(values):
            for path,value in values.items():
                node=current
                fields=path.split('.')
                for field in fields[:-1]:node=node[field]
                node[fields[-1]]=value
        with patch('tools.quest3.control.session', side_effect=lambda:copy.deepcopy(current)), \
                patch('tools.quest3.control.set_values',side_effect=write):
            result=resolution(profile='supersampled3072')
        self.assertEqual(current['session_settings']['video'][RENDER_FIELD],absolute(3072,3216))
        self.assertEqual(current['session_settings']['video'][ENCODE_FIELD],absolute(2080,2208))
        self.assertEqual(result['render_eye_aligned'],[3072,3232])
        self.assertEqual(result['encode_eye_aligned'],[2080,2208])

    def test_source_only_preserves_encode_and_other_video_settings(self):
        current={'session_settings':{'video':{RENDER_FIELD:absolute(2080,2208),
                 ENCODE_FIELD:absolute(2080,2208), 'preferred_fps':120,
                 'pyrowave':{'wavelet':{'variant':'Haar'}, 'chroma_444':False},
                 'bitrate':{'mode':{'variant':'ConstantMbps','ConstantMbps':1000}},
                 'foveated_encoding':{'enabled':False}}}}
        original=copy.deepcopy(current)
        def write(values):
            for path, value in values.items():
                node=current
                fields=path.split('.')
                for field in fields[:-1]:node=node[field]
                node[fields[-1]]=value
        with patch('tools.quest3.control.session', side_effect=lambda:copy.deepcopy(current)), \
                patch('tools.quest3.control.set_values', side_effect=write):
            result=resolution(render_eye=[3072,3216])
        self.assertEqual(current['session_settings']['video'][RENDER_FIELD], absolute(3072,3216))
        current['session_settings']['video'][RENDER_FIELD]=original['session_settings']['video'][RENDER_FIELD]
        self.assertEqual(current, original)
        self.assertEqual(result['previous_resolution_settings'][ENCODE_FIELD], absolute(2080,2208))
        self.assertTrue(result['steamvr_restart_required'])
        self.assertFalse(result['sustained_performance_verified'])

    def test_encode_only_does_not_reset_source_size(self):
        values=assignments(encode_eye=[2080,2208])
        self.assertTrue(all(ENCODE_FIELD in key for key in values))

    def test_invalid_or_ambiguous_geometry_rejected_before_any_api_call(self):
        for kwargs in ({}, {'render_eye':[3072.5,3216]}, {'encode_eye':[0,2208]},
                       {'render_eye':[True,3216]}, {'render_eye':[8224,3216]},
                       {'profile':'unknown'}, {'profile':'native2080','encode_eye':[2080,2208]}):
            with patch('tools.quest3.control.session') as read, \
                    patch('tools.quest3.control.set_values') as write:
                with self.assertRaises(ValueError):resolution(**kwargs)
                read.assert_not_called();write.assert_not_called()

    def test_rejected_server_readback_is_not_success(self):
        current={'session_settings':{'video':{RENDER_FIELD:absolute(2080,2208), ENCODE_FIELD:absolute(2080,2208)}}}
        with patch('tools.quest3.control.session', return_value=current), \
                patch('tools.quest3.control.set_values'):
            with self.assertRaises(RuntimeError):resolution(render_eye=[3072,3216])

    def test_profiles_share_decode_workload_and_payload_budget(self):
        p=profiles()
        self.assertEqual(p['native2080']['render_eye'], [2080,2208])
        self.assertEqual(p['supersampled3072']['render_eye'], [3072,3216])
        a=frame_budget(*p['native2080']['encode_eye'],120,1000)
        b=frame_budget(*p['supersampled3072']['encode_eye'],120,1000)
        self.assertEqual(a,b)
        self.assertEqual(b['raw_bytes_per_frame'],13_777_920)

    def test_decoupled_contract_checks_stereo_decode_not_source(self):
        e=evidence(settings(), [{'pyrowave':{'encoded_width':4160,'encoded_height':2208}}])
        self.assertEqual(e['status'],'verified')
        self.assertAlmostEqual(e['pc_source_pixel_ratio'],3072*3232/(2080*2208))
        self.assertEqual(e['aligned_requested_render_eye'],[3072,3232])

    def test_old_native_geometry_is_still_valid(self):
        self.assertEqual(evidence(settings(render=(2080,2208)),
            [{'pyrowave':{'encoded_width':4160,'encoded_height':2208}}])['status'],'verified')

    def test_pending_restart_and_wrong_decode_are_detected(self):
        s=settings();s['openvr']['target_eye_resolution_width']=2080
        self.assertIn('render_size_pending_restart_or_negotiation',evidence(s)['mismatches'])
        s=settings()
        self.assertIn('decoded_frame_differs_from_negotiated_encode',evidence(s,
            [{'pyrowave':{'encoded_width':6144,'encoded_height':3216}}])['mismatches'])

    def test_missing_and_scale_evidence_remain_unknown(self):
        self.assertEqual(evidence(settings())['status'],'unknown')
        s=settings();s['configured_view_resolution']={'variant':'Scale','Scale':1.0}
        self.assertEqual(evidence(s,[{'pyrowave':{'encoded_width':4160,'encoded_height':2208}}])['status'],'unknown')

    def test_native_panel_request_uses_server_padding(self):
        s=settings(render=(2080,2208));s['configured_view_resolution']=absolute(2064,2208)
        self.assertEqual(evidence(s,[{'pyrowave':{'encoded_width':4160,'encoded_height':2208}}])['status'],'verified')
