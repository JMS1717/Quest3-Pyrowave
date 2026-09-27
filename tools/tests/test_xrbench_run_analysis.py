import json
import shutil
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import patterns as p
from xrbench import run_analysis as ra
from test_xrbench_analyze import fake_capture

FIXTURE_EVENTS = Path(__file__).parent / "fixtures" / "events_sample.json"


def write_clip(path, static, counters, fps=36):
    frames = [cv2.resize(fake_capture(p.render_frame(static, n), jpeg_quality=90), (1000, 800))
              for n in counters]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (1000, 800))
    for f in frames:
        writer.write(f)
    writer.release()


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp("run")
    for label, stack, counters in (("A01-H400", "alvr", list(range(0, 80, 2))),
                                   ("V01-VD", "vd", [0, 2, 4, 4, 6, 9, 11, 13, 15, 17])):
        seg = root / label
        seg.mkdir()
        static = p.build_static(label)
        write_clip(seg / "clip.mp4", static, counters)
        cv2.imwrite(str(seg / "still_scene_0.png"), fake_capture(p.render_frame(static, 500)))
        cv2.imwrite(str(seg / "still_scene_1.png"), fake_capture(p.render_frame(static, 900)))
        (seg / "meta.json").write_text(json.dumps({
            "segment": {"label": label, "stack": stack, "codec": "H264", "mbps": 400, "eye": 2560,
                        "hz": 72, "foveation": True, "buffering": 2.0, "note": ""},
            "status": "ok", "watchdog_wakes": 0, "thermal_end": 1}))
        if stack == "alvr":
            (seg / "telemetry").mkdir()
            shutil.copy(FIXTURE_EVENTS, seg / "telemetry" / "events.json")
    return root


@pytest.fixture(scope="module")
def results(run_dir):
    return ra.analyze_run(run_dir)


def test_one_result_per_segment_in_folder_order(results):
    assert [r["label"] for r in results] == ["A01-H400", "V01-VD"]


def test_clip_pacing_uses_measured_capture_rate(results):
    alvr = results[0]["pacing"]
    assert alvr["capture_fps"] == pytest.approx(36, abs=0.5)
    assert alvr["irregular"] == 0 and alvr["unreadable"] == 0


def test_clip_pacing_finds_hold_and_skip_in_vd(results):
    vd = results[1]["pacing"]
    assert vd["repeated"] == 1       # 4 shown twice
    assert vd["irregular"] >= 2      # the hold and the +3 jump


def test_still_quality_is_summarized(results):
    q = results[0]["quality"]
    assert q["stills"] == 2
    assert q["psnr_overall"] > 25 and 0 < q["ssim_overall"] <= 1
    assert "gradient_gray_step_ratio" in q and "detail_sharpness" in q


def test_alvr_telemetry_attached_vd_has_none(results):
    assert results[0]["telemetry"]["decoder_ms"]["mean"] == pytest.approx(55.17, abs=0.01)
    assert results[1]["telemetry"] is None


def test_results_written_as_json_and_csv(run_dir, results):
    ra.write_outputs(run_dir, results)
    data = json.loads((run_dir / "results.json").read_text())
    assert len(data) == 2
    header = (run_dir / "results.csv").read_text().splitlines()[0]
    assert header.startswith("label,workload,stack,codec,mbps,eye,eye_h,hz")
    # the 2e axes and the angular-resolution columns must be present, since correlating "looks
    # sharper" with a configuration is the entire point of the preset ladder
    for column in ("centre", "gaze", "px_per_deg", "panel_pct", "encoded_mpx", "clamp_pct"):
        assert column in header.split(","), column


def test_display_latency_summarized_when_frame_and_still_logs_exist(tmp_path):
    seg = tmp_path / "W1-400"
    seg.mkdir()
    static = p.build_static("W1-400")
    cv2.imwrite(str(seg / "still_scene_0.png"), fake_capture(p.render_frame(static, 10)))
    (seg / "frames.csv").write_text("frame,t_wait_done,t_submitted\n" +
                                    "".join(f"{n},{1.0 + n / 72:.6f},{1.0 + n / 72:.6f}\n" for n in range(40)))
    (seg / "stills.csv").write_text(f"still,t_request,t_done\nstill_scene_0.png,{1.0 + 30 / 72:.6f},9\n")
    (seg / "meta.json").write_text(json.dumps({"segment": {"label": "W1-400", "stack": "alvr", "codec": "H264",
        "mbps": 400, "eye": 2560, "hz": 72, "transport": "wifi"}, "status": "ok"}))
    r = ra.analyze_segment(seg)
    assert r["transport"] == "wifi"
    assert abs(r["latency"]["median_ms"] - 20 / 72 * 1000) < 1
    assert r["latency"]["max_frames_behind"] == 20


# --- 2e: workload axes, angular resolution and gaze stats ---


def _segment_dir(tmp_path, seg, gaze_stats=None):
    d = tmp_path / seg["label"]
    (d / "telemetry").mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"segment": seg, "status": "ok"}))
    if gaze_stats is not None:
        (d / "gaze_stats.json").write_text(json.dumps(gaze_stats))
    return d


SEG = {"label": "Q-SCREEN-BEST", "stack": "alvr", "codec": "H264", "mbps": 800,
       "eye": 3520, "eye_h": 3840, "hz": 60, "foveation": True, "transport": "wifi",
       "workload": "screen", "center_size_x": 0.20, "center_size_y": 0.178,
       "edge_ratio_x": 3.0, "edge_ratio_y": 4.0, "gaze": True, "render_scale": 1.0,
       "buffering": 2.0, "note": "screen best"}


def test_carries_the_workload_axes_through(tmp_path):
    result = ra.analyze_segment(_segment_dir(tmp_path, SEG))
    assert result["workload"] == "screen"
    assert result["center_size_x"] == 0.20
    assert result["gaze"] is True
    assert result["render_scale"] == 1.0
    assert result["eye_h"] == 3840


def test_reports_angular_resolution_against_the_panel(tmp_path):
    # the number that makes a preset comparable to what the display can physically show, and the
    # only way to correlate "it looks sharper" with a configuration
    result = ra.analyze_segment(_segment_dir(tmp_path, SEG))
    assert result["centre_px_per_deg"] == pytest.approx(3520 / 94.4, rel=1e-3)
    assert result["panel_fraction"] == pytest.approx(3520 / 3552, rel=1e-3)


def test_reports_encoded_pixels_so_the_decode_budget_is_visible(tmp_path):
    result = ra.analyze_segment(_segment_dir(tmp_path, SEG))
    assert result["encoded_mpx"] == pytest.approx(1664 * 1472 / 1e6, rel=1e-3)


def test_square_legacy_segments_still_analyse(tmp_path):
    legacy = {"label": "W1-400", "stack": "alvr", "codec": "H264", "mbps": 400,
              "eye": 2560, "hz": 72, "foveation": True, "transport": "wifi"}
    result = ra.analyze_segment(_segment_dir(tmp_path, legacy))
    assert result["workload"] == "control"        # the default
    assert result["eye_h"] == 2560                # square
    assert result["centre_px_per_deg"] == pytest.approx(2560 / 94.4, rel=1e-3)


def test_folds_in_gaze_stats_when_present(tmp_path):
    stats = {"clamp_fraction": 0.12, "staleness_p95": 6.7, "gaze_samples": 40}
    result = ra.analyze_segment(_segment_dir(tmp_path, SEG, gaze_stats=stats))
    assert result["gaze_stats"]["clamp_fraction"] == 0.12
    assert result["clamp_fraction"] == 0.12
    assert result["staleness_p95"] == 6.7


def test_missing_gaze_stats_is_not_an_error(tmp_path):
    result = ra.analyze_segment(_segment_dir(tmp_path, SEG))
    assert result["gaze_stats"] is None
    assert result["clamp_fraction"] is None


def test_a_clamp_fraction_of_zero_is_reported_as_zero_not_as_missing():
    # 0% clamping is a *result* -- it means the foveation centre never hit its limit, which is
    # exactly what a gaze-off arm or a generous centre should show. Rendering it as an empty cell
    # would read as "not measured" and quietly hide the answer.
    column = dict(ra.CSV_COLUMNS)["clamp_pct"]
    assert column({"clamp_fraction": 0.0}) == 0.0
    assert column({"clamp_fraction": 0.12}) == pytest.approx(12.0)
    assert column({"clamp_fraction": None}) is None
    assert column({}) is None


def test_panel_fraction_of_zero_is_likewise_not_swallowed():
    column = dict(ra.CSV_COLUMNS)["panel_pct"]
    assert column({"panel_fraction": 0.0}) == 0.0
    assert column({"panel_fraction": 0.99}) == pytest.approx(99.0)
    assert column({}) is None
