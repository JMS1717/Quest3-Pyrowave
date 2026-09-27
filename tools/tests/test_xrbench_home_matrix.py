"""Static SteamVR Home as the benchmark scene, the Home baselines, the panel-aspect resolution x
bitrate matrix, and the per-cell report joiner."""
import json
from pathlib import Path

import pytest

from alvr_ffe_calc import encoded_eye_size
from xrbench import matrix
from xrbench import plan as pl
from xrbench import sweep


def encoded_sbs(seg):
    w, h = encoded_eye_size(*seg.eye_size(), seg.foveation_config())
    return 2 * w, h


# --- plans -------------------------------------------------------------------------------------

def operating_point():
    return next(s for s in pl.pyro_res_plan() if s.label == "X60-400-90")


def test_home_baselines_alternate_400_and_300_three_times_then_sustain():
    segs = pl.pyro_home_baseline_plan()
    assert [s.label for s in segs] == ["HB400-1", "HB300-1", "HB400-2", "HB300-2", "HB400-3", "HB300-3", "SHB400"]
    op = operating_point()
    for s in segs:
        assert s.scene == "home" and s.decode_path == "compute" and s.codec == "PyroWave"
        assert (s.eye, s.eye_h, s.hz) == (op.eye, op.eye_h, 90)
        assert (s.center_size_x, s.center_size_y, s.edge_ratio_x, s.edge_ratio_y) == \
               (op.center_size_x, op.center_size_y, op.edge_ratio_x, op.edge_ratio_y)
    assert segs[-1].scene_seconds == 300 and segs[-1].mbps == 400


def test_matrix_ladder_reproduces_the_measured_anchors():
    sizes = {s.label.split("-")[0]: encoded_sbs(s) for s in pl.pyro_matrix_plan()}
    assert sizes["PM100"] == (3328, 1472)
    assert sizes["PM90"] == (3008, 1344)
    assert sizes["PM80"] == (2624, 1184)          # measured: ALVR renders 2816, not 2842
    assert sizes["PM70"] == (2304, 1056)          # measured: ALVR renders 2464, not 2486
    assert sizes["PM60"] == (1984, 896)          # the measured operating point


def test_matrix_varies_only_scale_and_bitrate():
    varying = {"label", "eye", "eye_h", "mbps", "note"}
    segs = pl.pyro_matrix_plan()
    ref = {k: v for k, v in segs[0].__dict__.items() if k not in varying}
    for s in segs:
        assert {k: v for k, v in s.__dict__.items() if k not in varying} == ref, s.label
    assert ref["scene"] == "home" and ref["decode_path"] == "compute" and ref["hz"] == 90


def test_matrix_covers_each_cell_once_interleaved_with_a_closing_reference():
    segs = pl.pyro_matrix_plan()
    main, closing = segs[:-1], segs[-1]
    cells = [(int(s.label.split("-")[0][2:]), s.mbps) for s in main]
    assert sorted(cells) == sorted((p, m) for p in (100, 90, 80, 70, 60) for m in (300, 400))
    assert all(a[0] != b[0] for a, b in zip(cells, cells[1:])), "same resolution twice in a row"
    assert all(a[1] != b[1] for a, b in zip(cells, cells[1:])), "bitrate should alternate"
    assert closing.label == "PM60-400-R" and (closing.eye, closing.mbps) == (main[0].eye, 400)


def test_matrix_fits_the_encoder_limits():
    pl.validate(pl.pyro_matrix_plan())
    pl.validate(pl.pyro_home_baseline_plan())


# --- Home runner in the sweep -----------------------------------------------------------------

class FakePopen:
    launched = []

    def __init__(self, cmd, **kw):
        FakePopen.launched.append(cmd)

    def wait(self, timeout=None):
        return 0


def test_home_runner_primes_home_then_measures_between_log_markers(monkeypatch, tmp_path):
    events = []
    FakePopen.launched = []
    monkeypatch.setattr(sweep.subprocess, "Popen", lambda cmd, **kw: (events.append(("scene", cmd)), FakePopen(cmd, **kw))[1])
    monkeypatch.setattr(sweep, "adb", lambda *a, **k: events.append(("adb", a)))
    monkeypatch.setattr(sweep, "start_telemetry", lambda out, seconds: events.append(("telemetry", seconds)) or "tele")
    monkeypatch.setattr(sweep, "record_clip", lambda *a, **k: events.append(("clip",)))
    monkeypatch.setattr(sweep, "screenshot", lambda *a, **k: events.append(("still",)))
    monkeypatch.setattr(sweep.time, "sleep", lambda s: None)
    seg = pl.pyro_home_baseline_plan()[0]
    assert sweep.run_home_with_captures(seg, tmp_path, telemetry=True) == "tele"
    kinds = [e[0] for e in events]
    primer = events[kinds.index("scene")][1]
    assert primer[primer.index("--seconds") + 1] == str(sweep.HOME_PRIMER_SECONDS)
    start = next(i for i, e in enumerate(events) if e[0] == "adb" and matrix.MARK_START in " ".join(e[1]))
    end = next(i for i, e in enumerate(events) if e[0] == "adb" and matrix.MARK_END in " ".join(e[1]))
    assert kinds.index("scene") < start < kinds.index("telemetry") < end
    assert "scene" not in kinds[start:], "no scene app may run inside the Home window"
    assert events[kinds.index("telemetry")][1] == seg.scene_seconds - 5


def test_scene_runner_follows_the_segment():
    home = pl.pyro_home_baseline_plan()[0]
    assert sweep.scene_runner(home) is sweep.run_home_with_captures
    assert sweep.scene_runner(operating_point()) is sweep.run_scene_with_captures


# --- report joiner ------------------------------------------------------------------------------

def counter(c, p, s, d, stale):
    return (f"I PYROWAVE-UDP: frames complete {c} partial {p} skipped {s} dropped {d} superseded 0 | "
            f"stale pk {stale} foreign 0 decode fail 0 | last decode 3.0 convert 0.5 submit->fence 5.0 ms (mean 5.0)")


def test_window_counts_only_what_happened_between_the_markers():
    text = "\n".join([counter(100, 5, 1, 3, 1000),
                      f"I XRBENCH: {matrix.MARK_START} HB400-1",
                      counter(800, 9, 2, 10, 3000),
                      counter(1500, 12, 4, 30, 9000),
                      f"I XRBENCH: {matrix.MARK_END} HB400-1",
                      counter(2000, 40, 9, 99, 20000)])
    got = matrix.receiver_counts(text)
    assert got == {"complete": 700, "partial": 3, "skipped": 2, "dropped": 20, "stale_packets": 6000, "windowed": True}


def test_without_markers_the_counters_are_the_cumulative_totals():
    text = "\n".join([counter(100, 5, 1, 3, 1000), counter(900, 7, 2, 8, 4000)])
    got = matrix.receiver_counts(text)
    assert got["dropped"] == 8 and got["complete"] == 900 and got["windowed"] is False


def test_one_percent_low_is_the_mean_of_the_slowest_hundredth():
    fps = [90.0] * 990 + [30.0] * 10
    assert matrix.one_percent_low(fps) == pytest.approx(30.0)
    assert matrix.one_percent_low([90.0, 45.0]) == pytest.approx(45.0)


def test_quality_comes_from_the_cell_and_is_labelled_by_scene(tmp_path):
    (tmp_path / "quality.json").write_text(json.dumps({"psnr_y": 41.2, "ssim": 0.98, "vmaf": 90.1, "frames": 30}))
    q = matrix.quality(tmp_path)
    assert q == {"psnr_y": 41.2, "ssim": 0.98, "vmaf": 90.1, "quality_frames": 30}
    assert matrix.quality(tmp_path / "missing") == {"psnr_y": None, "ssim": None, "vmaf": None, "quality_frames": 0}


REAL_CELL = Path(__file__).resolve().parents[3] / "runs" / "20260925-iso-7-old" / "PC-1"   # <workspace>\runs


@pytest.mark.skipif(not REAL_CELL.exists(), reason="the real runs live on the PC")
def test_cell_row_on_a_real_run():
    row = matrix.cell_row(REAL_CELL)
    assert row["label"] == "PC-1" and row["scene"] == "synthetic"
    assert row["encoded"] == "1984x896" and row["predicted"] == "1984x896"
    assert row["dropped"] == 49 and row["packets_lost"] == 0
    assert 395 < row["bitrate_mbps_mean"] < 410
    assert row["decode_mean_ms"] == pytest.approx(3.43, abs=0.01)
    assert row["fps_median"] == pytest.approx(90.0, abs=0.1) and row["fps_p1_low"] < row["fps_mean"]
    assert row["thermal_status_end"] == 3 and row["gpu_mhz_mean"] > 600
    for k in ("total_mean_ms", "total_p50_ms", "total_p95_ms", "encoder_mean_ms", "decoder_mean_ms",
              "decoder_queue_mean_ms", "vsync_queue_mean_ms", "fence_p99_ms", "battery_start", "cpu_c_end", "gpu_c_end"):
        assert row[k] is not None, k


def test_packet_loss_is_counted_inside_the_capture_not_since_stream_start(tmp_path):
    (tmp_path / "telemetry").mkdir()
    summary = lambda n: {"event_type": {"id": "StatisticsSummary", "data": {"packets_lost_total": n}}}
    (tmp_path / "telemetry" / "events.json").write_text(json.dumps([summary(5), summary(7), summary(9)]))
    assert matrix._telemetry(tmp_path)[1] == 4


def test_the_saved_logcat_keeps_the_measure_markers():
    # the harness saves a filtered logcat; the first live Home cells lost their
    # XRBENCH markers to that filter, so the receiver counts could not be windowed
    raw = "\n".join([f"09-26 21:42:40.100 1 2 I XRBENCH: {matrix.MARK_START} HB400-1",
                     "09-26 21:42:41.000 1 2 I PYROWAVE-UDP: frames complete 10 partial 0 skipped 0 dropped 0 superseded 0 | stale pk 0",
                     "09-26 21:42:41.500 1 2 I SomethingElse: noise",
                     f"09-26 21:43:13.100 1 2 I XRBENCH: {matrix.MARK_END} HB400-1"])
    kept = sweep.filter_logcat(raw)
    assert matrix.MARK_START in kept and matrix.MARK_END in kept and "PYROWAVE-UDP" in kept
    assert "SomethingElse" not in kept


def test_alvr_floors_the_render_size_to_32_before_foveating():
    # openvr_config.json of the panel-aspect matrix: requested 3197/2842/2486/2131 wide, ALVR rendered
    # 3168/2816/2464/2112; the encode size follows the floored render size
    from alvr_ffe_calc import FoveationConfig
    cfg = FoveationConfig(center_size_x=0.20, center_size_y=0.178)
    assert encoded_eye_size(2842, 3072, cfg) == encoded_eye_size(2816, 3072, cfg) == (1312, 1184)
    assert encoded_eye_size(2486, 2688, cfg) == (1152, 1056)


def test_home_runner_triggers_the_quality_dump_inside_the_window(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(sweep.subprocess, "Popen", lambda cmd, **kw: FakePopen(cmd, **kw))
    monkeypatch.setattr(sweep, "adb", lambda *a, **k: events.append(("adb", " ".join(a))))
    monkeypatch.setattr(sweep, "start_telemetry", lambda out, seconds: "tele")
    monkeypatch.setattr(sweep, "record_clip", lambda *a, **k: None)
    monkeypatch.setattr(sweep, "screenshot", lambda *a, **k: None)
    monkeypatch.setattr(sweep.time, "sleep", lambda s: None)
    monkeypatch.setattr(sweep, "trigger_dump", lambda path, n: events.append(("trigger", str(path), n)))
    monkeypatch.setattr(sweep, "wait_for_dump", lambda path, n, timeout=90: events.append(("wait", n)) or True)
    seg = pl.pyro_matrix_quality_plan()[0]
    sweep.run_home_with_captures(seg, tmp_path, telemetry=True)
    kinds = [e[0] for e in events]
    start = next(i for i, e in enumerate(events) if e[0] == "adb" and matrix.MARK_START in e[1])
    end = next(i for i, e in enumerate(events) if e[0] == "adb" and matrix.MARK_END in e[1])
    t = kinds.index("trigger")
    assert start < t < kinds.index("wait") < end
    assert events[t][1] == str(tmp_path / "quality" / "source.y4m") and events[t][2] == seg.quality_frames
