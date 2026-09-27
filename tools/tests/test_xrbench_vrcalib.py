import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import vrcalib as vc


def test_headset_fps_from_xrstat_log():
    log = ("XRSTAT out_fps=71.9 in_mbps=398.2 dec_p50=6.10 dec_p95=9.80 dec_max=14.20 in_wait_ms=0.3 in_flight=1\n"
           "XRSTAT out_fps=72.1 in_mbps=399.0 dec_p50=6.00 dec_p95=9.50 dec_max=13.00 in_wait_ms=0.3 in_flight=1\n")
    fps = vc.headset_fps(log)
    assert 71.5 <= fps <= 72.5


def test_runtime_latency_from_log():
    log = ("frames=72 encode=3.9ms sent=50 decoded_ms=12.6\n"
           "frames=144 encode=4.1ms sent=122 decoded_ms=14.7\n")
    lat = vc.runtime_latency(log)
    assert lat["encode_ms"] == 4.1 and lat["decoded_ms"] == 14.7


def test_preset_configures_the_cfg_lines():
    lines = vc.cfg_lines({"mbps": 400, "codec": "hevc10", "eye_w": 1424, "eye_h": 1664})
    assert "mbps=400" in lines and "codec=hevc10" in lines and "eye_w=1424" in lines


def test_recommend_picks_low_med_high_from_viable_runs():
    results = [
        {"name": "h264-200", "mbps": 200, "codec": "h264", "fps": 72, "photon_ms": 30,
         "banding_levels": 150, "crush_floor": 4, "gamma": 1.0, "dropped": 0},
        {"name": "h264-400", "mbps": 400, "codec": "h264", "fps": 72, "photon_ms": 34,
         "banding_levels": 205, "crush_floor": 2, "gamma": 1.0, "dropped": 0},
        {"name": "hevc10-500", "mbps": 500, "codec": "hevc10", "fps": 72, "photon_ms": 40,
         "banding_levels": 500, "crush_floor": 0, "gamma": 1.0, "dropped": 0},
        {"name": "hevc10-900", "mbps": 900, "codec": "hevc10", "fps": 55, "photon_ms": 80,
         "banding_levels": 900, "crush_floor": 0, "gamma": 1.0, "dropped": 12},  # not viable (fps/drops)
    ]
    rec = vc.recommend(results)
    assert rec["low"]["name"] == "h264-200"                 # lowest-bitrate viable
    assert rec["high"]["name"] == "hevc10-500"              # best quality viable (10-bit, most levels)
    assert rec["high"]["name"] != "hevc10-900"              # the non-viable one is excluded
    assert rec["med"] in results


def test_recommend_handles_no_viable_runs():
    results = [{"name": "x", "mbps": 800, "codec": "h264", "fps": 40, "photon_ms": 120,
                "banding_levels": 10, "crush_floor": 30, "gamma": 1.0, "dropped": 50}]
    assert vc.recommend(results) == {}


# ---- the PC and the headset come from the environment, not from one machine's addresses ----

PC_ENV = ("XRBENCH_PC_SSH", "XRBENCH_PC_RUNTIME", "XRBENCH_HEADSET_IP")


@pytest.fixture
def reloaded(monkeypatch):
    """vrcalib re-imported under the given environment; restored afterwards."""
    def load(**env):
        for key in PC_ENV:
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(vc)
    yield load
    monkeypatch.undo()
    importlib.reload(vc)


def _no_remote_calls(monkeypatch, module):
    def refuse(*a, **k):
        raise AssertionError("must not reach the PC or the headset")
    monkeypatch.setattr(module.subprocess, "run", refuse)


def test_the_pc_and_the_headset_come_from_the_environment(reloaded):
    m = reloaded(XRBENCH_PC_SSH="bench@pc.example", XRBENCH_PC_RUNTIME=r"D:\rt", XRBENCH_HEADSET_IP="192.0.2.10")
    assert m.PC_SSH == "bench@pc.example" and m.SSH[-1] == "bench@pc.example"
    assert m.CFG == r"D:\rt\xrwired.cfg" and m.PATTERN == r"D:\rt\calib_pattern.raw"
    assert m.HEADSET_IP == "192.0.2.10"


def test_main_refuses_to_run_without_the_pc(reloaded, monkeypatch, tmp_path):
    m = reloaded(XRBENCH_HEADSET_IP="192.0.2.10")
    _no_remote_calls(monkeypatch, m)
    monkeypatch.setattr(sys, "argv", ["vrcalib", "--adb", "adb", "--mode", "perf", "--out", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as exit_:
        m.main()
    assert exit_.value.code == 2
    assert not (tmp_path / "out").exists()


def test_main_needs_a_headset_ip_when_the_environment_has_none(reloaded, monkeypatch, tmp_path):
    m = reloaded(XRBENCH_PC_SSH="bench@pc.example", XRBENCH_PC_RUNTIME=r"D:\rt")
    _no_remote_calls(monkeypatch, m)
    monkeypatch.setattr(sys, "argv", ["vrcalib", "--adb", "adb", "--mode", "perf", "--out", str(tmp_path / "out")])
    with pytest.raises(SystemExit) as exit_:
        m.main()
    assert exit_.value.code == 2
    assert not (tmp_path / "out").exists()
