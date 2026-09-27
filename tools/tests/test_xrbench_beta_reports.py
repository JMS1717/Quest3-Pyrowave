"""xrbench.beta_reports pools the dashboard's "Export PyroWave Test Report" files: it checks each
against the schema and for anything identifying, then flattens them into one CSV row each."""
import copy
import csv
import json

import pytest

from xrbench import beta_reports as br

REPORT = {
    "schema_version": 1,
    "report_id": "0123456789abcdef",
    "kind": "PyroWave beta test report",
    "settings": {
        "profile": "PyroWave 4:4:4 (Recommended)", "codec": "PyroWave 4:4:4", "chroma": "4:4:4",
        "render_per_eye": [2131, 2304], "render_scale_percent": 60.0, "refresh_hz": 90.0,
        "bitrate_mbps": 400,
        "pyrowave": {"transport": "UDP", "wavelet": "CDF 9/7", "decode_path": "compute"},
        "foveated_encoding": {"enabled": True, "center_size": [0.2, 0.178], "edge_ratio": [3.0, 4.0],
                              "follow_gaze": True},
        "advanced_changes": [],
    },
    "encoded_size": [1984, 896],
    "system": {"gpu": "NVIDIA GeForce RTX 3090", "gpu_driver": "32.0.15.6094",
               "cpu": "AMD Ryzen 9 5950X 16-Core Processor", "os": "Windows 11 (26100)",
               "streamer_version": "20.13.0-pyro.1", "headset_model": "SM-I610"},
    "window": {"seconds": 60, "frames": 5400},
    "fps": {"median": 90.0, "p1_low": 72.0, "mean": 89.5},
    "motion_to_photon_ms": {"mean": 58.1, "p50": 57.9, "p95": 63.0, "p99": 66.2},
    "stage_means_ms": {"encoder": 7.1},
    "pyrowave_gpu_decode_ms": {"mean": 3.2, "p50": 3.2, "p95": 3.9, "p99": 4.3},
    "pyrowave_fence_ms": {"mean": 4.9, "p50": 4.8, "p95": 6.1, "p99": 7.0},
    "frame_counts": {"complete": 5380, "partial": 8, "skipped": 2, "dropped": 10, "superseded": 1,
                     "late_packets": 40, "decode_failures": 0},
    "bitrate_mbps_achieved": 398.2,
    "packets_lost": 0,
    "headset_start": {"thermal_status": 0, "battery_temperature_c": 31.0, "battery_level": 0.8},
    "headset_end": {"thermal_status": 2, "battery_temperature_c": 36.5, "battery_level": 0.62},
    "subjective": {"chroma_444_difference": "clear", "sharpness_1_to_5": 4, "smoothness_1_to_5": 5,
                   "link": "Wi-Fi 6E", "band": "6 GHz", "router_model": "Some Router AX11000",
                   "notes": "static Home, seated"},
    "per_frame": {"motion_to_photon_ms": [57.9], "client_fps": [90.0], "gpu_decode_ms": [3.2],
                  "fence_ms": [4.8]},
}


def test_a_clean_report_validates():
    assert br.validate(REPORT) == []


def test_missing_sections_and_wrong_schema_are_rejected():
    bad = copy.deepcopy(REPORT)
    bad["schema_version"] = 2
    del bad["fps"]
    problems = br.validate(bad)
    assert any("schema_version" in p for p in problems)
    assert any("fps" in p for p in problems)


@pytest.mark.parametrize("leak", ["192.0.2.20", "aa:bb:cc:dd:ee:ff", "me@example.com",
                                  r"C:\Users\someone", "SERIAL0123", "5050.client.alvr"])
def test_identifying_text_is_rejected(leak):
    bad = copy.deepcopy(REPORT)
    bad["subjective"]["notes"] = f"worked fine {leak}"
    assert any("identifying" in p for p in br.validate(bad)), leak


def test_chroma_and_versions_are_not_mistaken_for_addresses():
    assert br.identifying("4:4:4 and 4:2:0 on 20.13.0-pyro.1 driver 32.0.15.6094 SM-I610") == []


def test_flatten_gives_one_row_with_the_pooled_columns():
    row = br.flatten(REPORT)
    assert row["gpu"] == "NVIDIA GeForce RTX 3090"
    assert row["chroma"] == "4:4:4"
    assert row["render_scale_percent"] == 60.0
    assert row["encoded"] == "1984x896"
    assert row["transport"] == "UDP"
    assert row["link"] == "Wi-Fi 6E" and row["band"] == "6 GHz"
    assert (row["decode_p50_ms"], row["decode_p95_ms"], row["decode_p99_ms"]) == (3.2, 3.9, 4.3)
    assert (row["fence_p50_ms"], row["fence_p99_ms"]) == (4.8, 7.0)
    assert (row["fps_median"], row["fps_p1_low"]) == (90.0, 72.0)
    assert row["mtp_p50_ms"] == 57.9
    assert row["dropped"] == 10 and row["late_packets"] == 40
    assert (row["thermal_start"], row["thermal_end"]) == (0, 2)
    assert row["chroma_444_difference"] == "clear"
    assert row["sharpness"] == 4 and row["smoothness"] == 5
    assert row["advanced_changes"] == ""
    assert "per_frame" not in row


def test_main_pools_a_folder_and_skips_bad_reports(tmp_path, capsys):
    (tmp_path / "pyrowave-report-a.json").write_text(json.dumps(REPORT))
    second = copy.deepcopy(REPORT)
    second["report_id"] = "fedcba9876543210"
    second["settings"]["render_scale_percent"] = 70.0
    (tmp_path / "pyrowave-report-b.json").write_text(json.dumps(second))
    leaky = copy.deepcopy(REPORT)
    leaky["subjective"]["notes"] = "my pc is 198.51.100.5"
    (tmp_path / "pyrowave-report-c.json").write_text(json.dumps(leaky))
    out = tmp_path / "pooled.csv"
    assert br.main([str(tmp_path), "--out", str(out)]) == 0
    rows = list(csv.DictReader(out.open(newline="", encoding="utf-8")))
    assert [r["render_scale_percent"] for r in rows] == ["60.0", "70.0"]
    printed = capsys.readouterr().out
    assert "2 reports pooled" in printed and "1 rejected" in printed
    assert "198.51.100.5" not in printed, "the rejection message must not repeat the leak"
