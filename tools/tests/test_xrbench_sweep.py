import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import sweep


def graph_events(bitrates_mbps):
    return [{"timestamp": "12:00:00.000",
             "event_type": {"id": "GraphStatistics",
                            "data": {"bitrate_bps": mbps * 1e6, "client_fps": 72.0}}}
            for mbps in bitrates_mbps]


def test_median_bitrate_ignores_non_graph_events():
    events = graph_events([300, 400, 500]) + [{"event_type": {"id": "StatisticsSummary", "data": {}}}]
    assert sweep.median_bitrate_mbps(events) == 400


def test_median_bitrate_is_none_without_graph_statistics():
    assert sweep.median_bitrate_mbps([{"event_type": {"id": "StatisticsSummary", "data": {}}}]) is None


def test_healthy_stream_has_no_blank_reason():
    # measured post-reboot: 398 Mbps median against a 400 Mbps request
    assert sweep.blank_stream_reason(graph_events([371, 398, 426]), 400) is None


def test_blank_stream_is_reported_with_both_numbers():
    # measured pre-reboot: a wedged D3D sync texture encoded a flat colour
    reason = sweep.blank_stream_reason(graph_events([0.18, 0.19, 0.20]), 400)
    assert reason is not None
    assert "0.19" in reason and "400" in reason


def test_capture_without_any_statistics_is_reported():
    reason = sweep.blank_stream_reason([], 400)
    assert reason is not None
    assert "no GraphStatistics" in reason


def test_threshold_is_a_fraction_of_the_requested_bitrate():
    # a stream far below the request is blank; one merely short of it is not
    assert sweep.blank_stream_reason(graph_events([20]), 400) is not None
    assert sweep.blank_stream_reason(graph_events([200]), 400) is None


def test_blank_stream_is_caught_by_the_segment_error_handler():
    # the sweep's per-segment `except Exception` must record it like any other failure,
    # then stop the run rather than spend the remaining segments on the same nothing
    assert issubclass(sweep.BlankStream, RuntimeError)


def test_record_clip_reports_failure_instead_of_losing_the_segment(monkeypatch, tmp_path):
    # screenrecord competes with the hardware decoder; at 800 Mbps / 2560-per-eye it fails to
    # allocate an encoder. The clip is the least valuable capture (36 fps against a 72 Hz
    # stream), so losing it must not cost the telemetry and stills alongside it.
    def boom(*args, **kwargs):
        raise RuntimeError("screenrecord failed")
    monkeypatch.setattr(sweep, "adb", boom)
    assert sweep.record_clip(tmp_path / "clip.mp4", 2) is False


def test_record_clip_reports_success(monkeypatch, tmp_path):
    monkeypatch.setattr(sweep, "adb", lambda *a, **k: "")
    assert sweep.record_clip(tmp_path / "clip.mp4", 2) is True


# --- bitstream tap control ---

def test_tap_base_path_is_inside_the_segment_directory(tmp_path):
    base = sweep.tap_base_path(tmp_path / "run", "H26-800")
    assert base.parent == tmp_path / "run" / "H26-800"
    assert base.name.startswith("tap")


def test_tap_base_path_has_no_suffix_because_the_tap_adds_its_own(tmp_path):
    # the tap appends -<timestamp>.h264 / .idx per session, so a suffix here would collide
    assert sweep.tap_base_path(tmp_path, "A20-400").suffix == ""


def test_set_tap_env_is_a_no_op_off_windows(monkeypatch):
    # the sweep only ever runs on the PC, but the tests run on the Mac; setting a Windows
    # user environment variable there must not blow up the suite
    calls = []
    monkeypatch.setattr(sweep.subprocess, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(sweep.sys, "platform", "darwin")
    sweep.set_tap_env(None)
    assert calls == []


def test_set_tap_env_clears_with_an_empty_value(monkeypatch):
    seen = []
    monkeypatch.setattr(sweep.sys, "platform", "win32")
    monkeypatch.setattr(sweep.subprocess, "run",
                        lambda cmd, **k: seen.append(" ".join(cmd)))
    sweep.set_tap_env(None)
    assert seen and "ALVR_BITSTREAM_TAP" in seen[0]


def test_steamvr_task_follows_the_codec():
    from xrbench import sweep as sw
    assert sw.steamvr_task_for("PyroWave") == "XRWiredSteamVRPyroClean"
    assert sw.steamvr_task_for("H264") == "XRWiredSteamVR"
    assert sw.steamvr_task_for("Hevc") == "XRWiredSteamVR"


def test_telemetry_wait_scales_with_the_scene_length():
    import xrbench.sweep as sw
    assert sw.telemetry_wait_seconds(33) == 153
    assert sw.telemetry_wait_seconds(300) == 420


def test_pyro_precision_prop_sets_a_level_or_clears_it(monkeypatch):
    calls = []
    monkeypatch.setattr(sweep, "adb", lambda *a, **k: calls.append(a))
    sweep.set_pyro_precision_prop(0)
    sweep.set_pyro_precision_prop(-1)
    assert calls == [("shell", "setprop", sweep.PRECISION_PROP, "0"),
                     ("shell", "setprop", sweep.PRECISION_PROP, '""')]


def test_gpu_clock_sample_parses_the_five_fields():
    row = sweep.parse_gpu_clock_sample("788000000\n55 %\n0\n80800\n")
    assert row == {"cur_freq_hz": 788000000, "busy_pct": 55, "thermal_pwrlevel": 0, "hottest_c": 80.8}
    assert sweep.parse_gpu_clock_sample("garbage") is None
    assert sweep.parse_gpu_clock_sample("") is None


def test_gpu_clock_sampler_records_rows_and_writes_csv(monkeypatch, tmp_path):
    monkeypatch.setattr(sweep, "adb", lambda *a, **k: "421000000\n12 %\n1\n66900\n")
    s = sweep.GpuClockSampler(interval_s=0.01)
    s.start()
    import time as _t
    _t.sleep(0.08)
    s.finish(tmp_path / "gpufreq.csv")
    text = (tmp_path / "gpufreq.csv").read_text()
    lines = text.strip().splitlines()
    assert lines[0] == "t,cur_freq_hz,busy_pct,thermal_pwrlevel,hottest_c"
    assert len(lines) >= 3 and lines[1].endswith(",421000000,12,1,66.9")


def test_dump_path_files_clips_under_their_class(tmp_path):
    p = sweep.dump_path("CP-PANEL", "synthetic_panel", root=tmp_path)
    assert p.parent == tmp_path / "synthetic_panel" and p.name.startswith("CP-PANEL-") and p.suffix == ".y4m"
    assert sweep.dump_path("X", "", root=tmp_path).parent.name == "unclassified"


def test_pyro_env_file_carries_the_dump_settings_and_is_removed_when_off(tmp_path):
    f = tmp_path / "pyro_env.cmd"
    sweep.write_pyro_env(r"C:\corpus\a\x.y4m", 90, 200, target=f)
    assert f.read_text().splitlines() == ["@echo off", r"set ALVR_PYROWAVE_DUMP=C:\corpus\a\x.y4m", "set ALVR_PYROWAVE_DUMP_COUNT=90", "set ALVR_PYROWAVE_DUMP_FRAME=200"]
    sweep.write_pyro_env(None, 0, 0, target=f)
    assert not f.exists()
    assert sweep.pyro_env_lines(None, 0, 0) == []
