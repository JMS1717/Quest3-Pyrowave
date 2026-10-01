import json
from pathlib import Path
import unittest
from tools.quest3.budget import frame_budget, profiles


class BudgetTests(unittest.TestCase):
    def test_native_120_1000(self):
        b = frame_budget(2064, 2208, 120, 1000)
        self.assertEqual(b["aligned_eye_resolution"], [2080, 2208])
        self.assertEqual(b["maximum_payload_bytes_per_frame"], 1_041_666)
        self.assertEqual(b["raw_bytes_per_frame"], 13_777_920)
        self.assertAlmostEqual(b["frame_ms"], 8.33333333)
        self.assertAlmostEqual(b["raw_to_payload_ratio"], 13.227, places=2)

    def test_subsampling_does_not_halve_fixed_bitrate(self):
        a = frame_budget(2064, 2208, 120, 1000, "420")
        b = frame_budget(2064, 2208, 120, 1000, "444")
        self.assertEqual(b["raw_bytes_per_frame"], 2 * a["raw_bytes_per_frame"])
        self.assertEqual(a["maximum_payload_bytes_per_frame"], b["maximum_payload_bytes_per_frame"])

    def test_every_profile_fits_transport_limits(self):
        config = json.loads((Path(__file__).parents[1] / "presets/quest3.json").read_text())
        for b in profiles(config):
            self.assertTrue(b["fits_pwu2_fragment_limit"])
            self.assertGreater(b["minimum_tcp_ethernet_mbps_at_cap"], b["mbps"])
            self.assertFalse(b["sustained_performance_verified"])

    def test_invalid_inputs(self):
        for args in [(0, 2208, 120, 1000), (2064, 2208, float("nan"), 1000),
                     (2064, 2208, 0, 1000), (2064, 2208, 120, 0)]:
            with self.assertRaises(ValueError):
                frame_budget(*args)
