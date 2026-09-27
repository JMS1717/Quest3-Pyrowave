import json
import math

import numpy as np
import pytest

from xrbench import exp2


def planes(fill, shape=(4, 6)):
    return [np.full(shape, fill, np.uint8) for _ in range(3)]


def test_compare_planes_reports_exact_max_and_count_and_inf_psnr_for_identical():
    a = planes(100)
    r = exp2.compare_planes(a, planes(100))
    assert r["Y"]["max_abs"] == 0 and r["Y"]["differing"] == 0 and r["all"]["psnr"] == math.inf
    b = planes(100)
    b[0][0, 0] = 103          # one luma sample off by 3
    b[2][1, 1] = 99           # one Cr sample off by 1
    r = exp2.compare_planes(a, b)
    assert (r["Y"]["max_abs"], r["Y"]["differing"]) == (3, 1)
    assert (r["Cr"]["max_abs"], r["Cr"]["differing"]) == (1, 1)
    assert r["Cb"]["differing"] == 0
    assert r["all"]["max_abs"] == 3 and r["all"]["differing"] == 2 and r["all"]["count"] == 72
    assert r["Y"]["mse"] == pytest.approx(9 / 24)
    assert r["Y"]["psnr"] == pytest.approx(10 * math.log10(255 ** 2 / (9 / 24)))


def test_compare_planes_refuses_mismatched_geometry():
    with pytest.raises(ValueError):
        exp2.compare_planes(planes(1), planes(1, (4, 8)))


def test_fp16_classification_bands():
    assert exp2.fp16_class(70.0, 1) == "PASS"
    assert exp2.fp16_class(65.0, 1) == "PASS"
    assert exp2.fp16_class(64.9, 1) == "PASS WITH DEVIATION"
    assert exp2.fp16_class(70.0, 2) == "PASS WITH DEVIATION"
    assert exp2.fp16_class(56.0, 1) == "PASS WITH DEVIATION", "56 dB is never reported as 70 dB"
    assert exp2.fp16_class(54.9, 1) == "FAIL"
    assert exp2.fp16_class(70.0, 3) == "FAIL"
    assert exp2.fp16_class(math.inf, 0) == "PASS"


def test_transform_gate_matches_experiment_1():
    assert exp2.transform_gate(60.0, 2) == "PASS"
    assert exp2.transform_gate(55.0, 1) == "FAIL"
    assert exp2.transform_gate(60.0, 3) == "FAIL"


def test_attribution_splits_transform_and_precision():
    r = exp2.attribution(7.0, 6.8, 6.5)
    assert r["transform_ms"] == pytest.approx(0.2)
    assert r["precision_ms"] == pytest.approx(0.3)
    assert r["combined_ms"] == pytest.approx(0.5)
    assert r["combined_pct"] == pytest.approx(100 * 0.5 / 7.0)


def test_bytes_per_coefficient_follows_pyrowave_precision_modes():
    assert [exp2.bytes_per_coefficient(l, 0) for l in range(5)] == [2, 2, 2, 2, 2]
    assert [exp2.bytes_per_coefficient(l, 1) for l in range(5)] == [2, 2, 4, 4, 4]
    assert [exp2.bytes_per_coefficient(l, 2) for l in range(5)] == [4, 4, 4, 4, 4]


def test_band_shape_halves_per_level():
    assert exp2.band_shape(64, 32, 0) == (32, 16)
    assert exp2.band_shape(64, 32, 4) == (2, 1)
    assert exp2.band_shape(65, 33, 0) == (33, 17)


def test_logical_bytes_is_smaller_at_precision_0_and_scales_with_pixels():
    p0 = exp2.logical_bytes(1984, 896, True, 0)
    p1 = exp2.logical_bytes(1984, 896, True, 1)
    p2 = exp2.logical_bytes(1984, 896, True, 2)
    assert p0["total_bytes"] < p1["total_bytes"] < p2["total_bytes"]
    # the finest two levels dominate, so precision 0 saves only the coarse levels' R32F
    assert 0.90 < p0["total_bytes"] / p1["total_bytes"] < 1.0
    assert p0["bytes_per_second"] == pytest.approx(p0["total_bytes"] * 90)
    assert p0["bytes_per_pixel"] == pytest.approx(p0["total_bytes"] / (1984 * 896))
    # a 4:2:0 frame has fewer chroma coefficients than 4:4:4
    assert exp2.logical_bytes(1984, 896, False, 0)["total_bytes"] < p0["total_bytes"]
    # hand check on a tiny frame at precision 0, luma only terms: 8x8, 5 levels
    # level 0: band 4x4=16 coeffs: dequant 3*16*2=96, read 4*16*2=128, write out 8*8=64
    # level 1: band 2x2=4: dequant 24, read 32, write LL0 4x4*2=32
    # level 2: band 1x1=1: dequant 6, read 8, write LL1 2x2*2=8
    # level 3: band 1x1: dequant 6, read 8, write LL2 1*2=2
    # level 4: band 1x1: dequant (3+1)*1*2=8, read 8, write LL3 2
    luma = 96 + 128 + 64 + 24 + 32 + 32 + 6 + 8 + 8 + 6 + 8 + 2 + 8 + 8 + 2
    assert exp2.logical_bytes(8, 8, True, 0)["total_bytes"] == 3 * luma


def test_phase3_trigger_lists_every_condition_met():
    deltas = {"gpu decode": {"mean": -2.0, "p95": -1.0, "p99": -6.0}, "submit->fence": {"mean": 1.0, "p95": 2.0, "p99": 3.0}}
    hits = exp2.phase3_trigger(deltas, logical_delta_pct=-8.0, dropped_pct=0.0, stale_pct=0.0,
                               end_temp_gap_c=1.0, matched_starts=True, extra_bytes_pct=30.0)
    assert hits == ["gpu decode p99 -6.0 %"]
    hits = exp2.phase3_trigger({}, logical_delta_pct=-25.0, dropped_pct=12.0, stale_pct=None,
                               end_temp_gap_c=2.5, matched_starts=True, extra_bytes_pct=12.0, contradiction=True)
    assert len(hits) == 5
    assert exp2.phase3_trigger({}, None, None, None, 3.0, False, None) == []


def test_bisect_cap_finds_the_smallest_cap_reaching_the_target():
    calls = []
    def q(cap):
        calls.append(cap)
        return 30.0 + (cap - 300_000) / 20_000       # +1 dB per 20 KB, monotone
    cap, qual, evals = exp2.bisect_cap(q, 35.0, 300_000, 500_000, tol_db=0.01)
    assert abs(cap - 400_000) <= 200 and qual >= 35.0
    assert len(evals) == len(calls) <= 14
    # target beyond the range: returns hi and its (short) quality, never extrapolates
    cap, qual, _ = exp2.bisect_cap(q, 99.0, 300_000, 500_000)
    assert cap == 500_000 and qual < 99.0
    # target already met at lo
    cap, qual, _ = exp2.bisect_cap(q, 10.0, 300_000, 500_000)
    assert cap == 300_000


def test_equal_bytes_table_pairs_rows_by_source_and_cap(tmp_path):
    r97 = [{"source": "kodim08", "resolution": 2560, "cap_bytes": 416667, "actual_bytes": 416600, "scaled_psnr_y": 30.0,
            "scaled_psnr_hvs": 33.0, "scaled_ssim_all": 0.9, "scaled_vmaf": 80.0},
           {"source": "kodim08", "resolution": 2304, "cap_bytes": 416667, "actual_bytes": 416600, "scaled_psnr_y": 31.0}]
    r53 = [{"source": "kodim08", "resolution": 2560, "cap_bytes": 416667, "actual_bytes": 416500, "scaled_psnr_y": 29.7,
            "scaled_psnr_hvs": 32.5, "scaled_ssim_all": 0.89, "scaled_vmaf": 79.0}]
    t = exp2.equal_bytes_table(r97, r53)
    assert len(t) == 1 and t[0]["d_psnr_y"] == pytest.approx(-0.3) and t[0]["bytes53"] == 416500


def test_parse_arm_reads_the_last_wavelet_and_precision_lines():
    text = ("09-23 I pyroclient: pyro precision requested 0, effective 0; shaderFloat16=1 storageBuffer16BitAccess=1 shaderInt16=1\n"
            "09-23 I pyroclient: decode path compute (forced)\n09-23 I pyroclient: wavelet CDF 5/3\n")
    assert exp2.parse_arm(text) == ("5/3", "0", "0", 1)
    text2 = "pyroclient: pyro precision requested 0, effective 1 (no shaderFloat16, FP16 math unavailable); shaderFloat16=0 x\n"
    assert exp2.parse_arm(text2) == (None, "0", "1 (no shaderFloat16, FP16 math unavailable)", 0)
    assert exp2.parse_arm("") == (None, None, None, None)


def test_regression_row_and_pair_deltas():
    a = {"summary": {"complete": 900, "partial": 50, "skipped": 10, "dropped": 40, "stale_packets": 100, "decode_fail": 0},
         "meta": {"packets_lost": 0}, "thermal_rise": 3.0}
    b = {"summary": {"complete": 950, "partial": 30, "skipped": 0, "dropped": 20, "stale_packets": 50, "decode_fail": 0},
         "meta": {"packets_lost": 1}, "thermal_rise": 4.5}
    ra = exp2.regression_row(a)
    assert ra["frames"] == 1000 and ra["dropped_pct"] == pytest.approx(4.0) and ra["stale_per_frame"] == pytest.approx(0.1)
    d = exp2.pair_regression_deltas(a, b)
    assert d["dropped_pct"] == pytest.approx(-50.0) and d["stale_pct"] == pytest.approx(-50.0) and d["thermal_rise_c"] == pytest.approx(1.5)
    empty = {"summary": None, "meta": {}, "thermal_rise": None}
    assert exp2.regression_row(empty)["dropped_pct"] is None
    assert exp2.pair_regression_deltas(empty, empty) == {"dropped_pct": None, "stale_pct": None, "thermal_rise_c": None}


def test_outcome_bands():
    assert exp2.outcome(-8.0, -8.0, -25.0, 10.0)[0] == "C"
    assert exp2.outcome(-8.0, -8.0, -5.0, 30.0)[0] == "D"
    assert exp2.outcome(-8.0, -8.0, -5.0, 10.0)[0] == "A"
    assert exp2.outcome(-8.0, -1.0, -5.0, 10.0)[0] == "B"
    assert exp2.outcome(-1.0, -1.0, -5.0, 10.0)[0] == "E"
    assert exp2.outcome(-1.0, 6.0, -5.0, 10.0)[0] == "F"
    assert exp2.outcome(-8.0, -8.0, -25.0, 10.0, quality_ok=False)[0] == "F"
    assert exp2.outcome(None, None, None, None)[0] == "E"


def _write_cell(root, label, wavelet, precision, gpu, fence, start_c, end_c, dropped=10, stale=5):
    d = root / label
    d.mkdir(parents=True)
    lines = [f"pyroclient: pyro precision requested {precision}, effective {precision}; shaderFloat16=1 storageBuffer16BitAccess=1 shaderInt16=1",
             "pyroclient: decode path compute (forced)", f"pyroclient: wavelet CDF {wavelet}"]
    for _ in range(2):
        lines.append(f"PYROWAVE-UDP: gpu decode ms: min {gpu - 1:.2f} mean {gpu:.2f} p50 {gpu:.2f} p95 {gpu + 1:.2f} p99 {gpu + 2:.2f} max {gpu + 3:.2f}")
        lines.append("PYROWAVE-UDP: convert ms: min 0.50 mean 0.60 p50 0.60 p95 0.80 p99 0.90 max 1.00")
        lines.append(f"PYROWAVE-UDP: submit->fence ms: min {fence - 1:.2f} mean {fence:.2f} p50 {fence:.2f} p95 {fence + 1:.2f} p99 {fence + 2:.2f} max {fence + 3:.2f}")
    lines.append(f"PYROWAVE-UDP: frames complete 2800 partial 10 skipped 5 dropped {dropped} superseded 0; stale packets {stale} foreign 0 decode failures 0")
    (d / "logcat.txt").write_text("\n".join(lines) + "\n")
    (d / "meta.json").write_text(json.dumps({"segment": {"eye": 2131, "eye_h": 2304, "mbps": 400, "hz": 90, "center_size_x": 0.2, "buffering": 1.5},
                                             "fps_mean": 90.0, "fps_median": 90.0, "fps_min": 85.0, "packets_lost": 0, "cpu_start": 40.0, "cpu_end": 45.0}))
    (d / "hottest.json").write_text(json.dumps({"start_c": start_c, "status_start": 0, "end_c": end_c, "status_end": 1}))


def _phase0():
    cmp = {p: {"max_abs": 1, "mse": 0.02, "psnr": 65.1, "differing": 100, "count": 10000} for p in ("Y", "Cb", "Cr", "all")}
    bad = {p: {"max_abs": 3, "mse": 0.6, "psnr": 50.3, "differing": 5000, "count": 10000} for p in ("Y", "Cb", "Cr", "all")}
    xf = {p: {"max_abs": 54, "mse": 0.3, "psnr": 53.4, "differing": 1500, "count": 10000} for p in ("Y", "Cb", "Cr", "all")}
    row = {"source": "kodak_kodim08", "cap_bytes": 416667, "bytes97": 416600, "bytes53": 416640, "psnr_y97": 21.42, "psnr_y53": 21.14,
           "d_psnr_y": -0.28, "psnr_hvs97": 25.0, "psnr_hvs53": 24.7, "ssim97": 0.66, "ssim53": 0.659, "vmaf97": 30.0, "vmaf53": 29.0}
    return {"device": {"A_vs_pc97": cmp, "B_vs_pc53": cmp, "C_vs_pc53": cmp, "C_vs_B_fp16gate": cmp, "D_vs_A_97fp16": bad,
                       "A_vs_B_transform": xf, "pc97_vs_pc53": xf},
            "standalone": {"geometry": "3328x1472 4:4:4", "iterations": 200, "A_ms": 7.057, "B_ms": 6.879, "C_ms": 6.815, "convert_ms": 1.466},
            "equal_bytes": {"at_416667": [row], "cells": 330, "mean_d_psnr_y": -0.4, "kodak_d_psnr_y": -0.3, "panel_d_psnr_y": -1.5, "min_d_psnr_y": -2.0, "max_d_psnr_y": 0.1},
            "gains": [{"source": "kodak_kodim08", "gains": "CDF 5/3, native 5/3", "bytes": 416644, "psnr_y": 21.14, "psnr_hvs": 25.0, "ssim": 0.8, "vmaf": 30.0}],
            "match": [{"source": "kodak_kodim08", "bytes97": 416600, "psnr_y97": 21.42, "bytes53": 450000, "psnr_y53": 21.43, "extra_pct": 8.0,
                       "psnr_hvs97": 25.0, "psnr_hvs53": 25.1, "ssim97": 0.66, "ssim53": 0.67, "vmaf97": 30.0, "vmaf53": 31.0}],
            "spirv": {"fp16_v0": {"f16_arith": 37, "f32_arith": 12, "fconvert": 14, "f32_note": "texel coordinate scaling"},
                      "fp16_v1": {"f16_arith": 0, "f32_arith": 49, "fconvert": 37}},
            "negatives": "all rejected", "fp16_runtime": "confirmed", "encoded": (1984, 896)}


def test_render_produces_the_22_items_in_order(tmp_path):
    run = tmp_path / "run"
    _write_cell(run, "P97-1", "9/7", 1, 3.2, 4.9, 60.0, 66.0)
    _write_cell(run, "P53-1", "5/3", 0, 3.0, 4.5, 61.0, 66.5, dropped=8)
    _write_cell(run, "P97-2", "9/7", 1, 3.25, 4.95, 62.0, 67.0)
    _write_cell(run, "P53-2", "5/3", 0, 3.05, 4.55, 61.5, 67.0, dropped=12)
    out = tmp_path / "REPORT.md"
    text = exp2.render(run, out, _phase0())
    heads = [l for l in text.splitlines() if l.startswith("## ")]
    nums = [int(h.split(".")[0][3:]) for h in heads]
    assert nums == list(range(1, 23)), nums
    assert "arm proven by logcat: wavelet CDF 5/3, precision 0 -> 0" in text
    assert "THERMALLY MATCHED" in text
    assert "Pairs agree on GPU and fence: YES" in text
    assert "NOT RUN" in text
    assert "PASS" in text and "physical" in text.lower()
    assert out.exists()


def test_render_reports_sustained_cells_when_present(tmp_path):
    run = tmp_path / "run"
    for l, w, p, g, f in (("P97-1", "9/7", 1, 3.2, 4.9), ("P53-1", "5/3", 0, 3.0, 4.5), ("P97-2", "9/7", 1, 3.2, 4.9), ("P53-2", "5/3", 0, 3.0, 4.5)):
        _write_cell(run, l, w, p, g, f, 60.0, 66.0)
    sus = tmp_path / "sus"
    _write_cell(sus, "S97", "9/7", 1, 3.3, 5.0, 60.0, 75.0, dropped=400)
    _write_cell(sus, "S53", "5/3", 0, 3.1, 4.6, 61.0, 76.0, dropped=450)
    text = exp2.render(run, tmp_path / "R.md", _phase0(), sustained_dir=sus)
    assert "S97 (CDF 9/7" in text and "S53 (CDF 5/3" in text and "Sustained deltas submit->fence" in text


def test_gpu_clock_summary_reports_mean_min_max_and_share_at_top(tmp_path):
    p = tmp_path / "gpufreq.csv"
    p.write_text("t,cur_freq_hz,busy_pct,thermal_pwrlevel,hottest_c\n"
                 "1.0,788000000,55,0,70.1\n2.0,788000000,60,0,70.5\n3.0,599000000,40,1,71.0\n4.0,421000000,30,1,71.2\n")
    s = exp2.gpu_clock_summary(p)
    assert s["samples"] == 4 and s["mean_mhz"] == pytest.approx((788 + 788 + 599 + 421) / 4)
    assert s["min_mhz"] == 421 and s["max_mhz"] == 788 and s["share_at_max_pct"] == pytest.approx(50.0)
    assert s["mean_busy_pct"] == pytest.approx(46.25)
    assert exp2.gpu_clock_summary(tmp_path / "missing.csv") is None
    assert exp2.gpu_cycles_per_frame(3.14, 788.0) == pytest.approx(3.14e-3 * 788e6)


def test_render_reports_the_gpu_clock_when_a_cell_sampled_it(tmp_path):
    run = tmp_path / "run"
    for l, w, p, g, f in (("P97-1", "9/7", 1, 4.0, 5.9), ("P53-1", "5/3", 0, 3.9, 5.8), ("P97-2", "9/7", 1, 4.0, 5.9), ("P53-2", "5/3", 0, 3.1, 4.7)):
        _write_cell(run, l, w, p, g, f, 66.0, 70.0)
    (run / "P53-2" / "gpufreq.csv").write_text("t,cur_freq_hz,busy_pct,thermal_pwrlevel,hottest_c\n1,788000000,50,0,70\n2,788000000,52,0,71\n")
    text = exp2.render(run, tmp_path / "R.md", _phase0())
    assert "GPU clock: mean 788 MHz" in text and "Mcycles/frame" in text
    assert "GPU clock: UNKNOWN (no gpufreq.csv for this cell)" in text
    assert "GPU DVFS" in text


def test_report_windows_and_window_clock_pair_reports_with_samples():
    import datetime
    text = ("09-23 14:40:42.704  1 1 I PYROWAVE-UDP: gpu decode ms: min 2.7 mean 2.89 p50 2.8 p95 3.4 p99 3.6 max 3.8\n"
            "09-23 14:40:42.705  1 1 I PYROWAVE-UDP: convert ms: min 0.5 mean 0.6 p50 0.6 p95 0.8 p99 0.9 max 1.0\n"
            "09-23 14:40:42.706  1 1 I PYROWAVE-UDP: submit->fence ms: min 3.9 mean 4.75 p50 4.3 p95 6.0 p99 6.1 max 200\n"
            "09-23 14:40:50.700  1 1 I PYROWAVE-UDP: gpu decode ms: min 2.7 mean 2.87 p50 2.8 p95 3.4 p99 3.5 max 3.6\n"
            "09-23 14:40:50.700  1 1 I PYROWAVE-UDP: submit->fence ms: min 3.9 mean 4.34 p50 4.2 p95 5.2 p99 5.3 max 6\n")
    w = exp2.report_windows(text)
    assert len(w) == 2 and w[0][1:] == (2.89, 4.75) and w[1][1:] == (2.87, 4.34)
    t0 = datetime.datetime(2026, 9, 23, 14, 40, 42).timestamp()
    assert w[0][0] == pytest.approx(t0 + 1, abs=1)
    rows = [(t0 - 6, {"cur_freq_hz": 788000000, "busy_pct": 50}), (t0 - 2, {"cur_freq_hz": 599000000, "busy_pct": 40}),
            (t0 + 30, {"cur_freq_hz": 421000000, "busy_pct": 10})]
    pts = exp2.window_clock(w, rows)
    assert len(pts) == 1 and pts[0]["clock_mhz"] == pytest.approx(693.5) and pts[0]["samples"] == 2 and pts[0]["gpu_ms"] == 2.89


def test_latency_vs_clock_recognises_one_shared_relationship():
    # same work (2.6 Mcycles) at different clocks: t = work / f
    def pts(clocks, work_mcycles, fence_extra=1.6):
        return [{"clock_mhz": c, "gpu_ms": work_mcycles / c, "fence_ms": work_mcycles / c + fence_extra} for c in clocks]
    a = pts([788, 750, 690, 599], 2600)
    b = pts([788, 730, 640, 545], 2600)
    r = exp2.latency_vs_clock(a, b)
    assert r["fit97"][2] > 0.999 and abs(r["resid53_on_97_ms"]) < 1e-6 and r["within_scatter"]
    assert abs(r["pooled_r2_vs_best_arm"]) < 1e-6
    # a transform that does 20 % less work sits on a different line
    c = pts([788, 730, 640, 545], 2080)
    r = exp2.latency_vs_clock(a, c)
    assert r["resid53_on_97_pct"] == pytest.approx(-20.0, abs=0.5) and not r["within_scatter"]
    assert exp2.latency_vs_clock([], [])["fit97"] is None


def test_render_includes_latency_vs_clock_when_windows_have_samples(tmp_path):
    import datetime
    run = tmp_path / "run"
    for l, w, p, g, f in (("P97-1", "9/7", 1, 4.0, 5.9), ("P53-1", "5/3", 0, 3.9, 5.8), ("P97-2", "9/7", 1, 4.0, 5.9), ("P53-2", "5/3", 0, 3.1, 4.7)):
        _write_cell(run, l, w, p, g, f, 66.0, 70.0)
        t0 = datetime.datetime(2026, 9, 23, 14, 40, 42).timestamp()
        lines = ["09-23 14:40:42.700  1 1 I PYROWAVE-UDP: gpu decode ms: min 2 mean %.2f p50 3 p95 4 p99 4 max 5" % g,
                 "09-23 14:40:42.700  1 1 I PYROWAVE-UDP: submit->fence ms: min 3 mean %.2f p50 5 p95 6 p99 6 max 9" % f,
                 "09-23 14:40:50.700  1 1 I PYROWAVE-UDP: gpu decode ms: min 2 mean %.2f p50 3 p95 4 p99 4 max 5" % (g * 1.3),
                 "09-23 14:40:50.700  1 1 I PYROWAVE-UDP: submit->fence ms: min 3 mean %.2f p50 5 p95 6 p99 6 max 9" % (f * 1.2)]
        with open(run / l / "logcat.txt", "a") as fh:
            fh.write("\n".join(lines) + "\n")
        (run / l / "gpufreq.csv").write_text("t,cur_freq_hz,busy_pct,thermal_pwrlevel,hottest_c\n%f,788000000,50,0,70\n%f,599000000,52,0,71\n" % (t0 - 3, t0 + 5))
    text = exp2.render(run, tmp_path / "R.md", _phase0())
    assert "### Latency vs GPU clock" in text and "| GPU decode mean | 4 | 4 |" in text


def test_pairs_agree_treats_sign_flips_inside_the_noise_band_as_agreement():
    assert exp2.pairs_agree(-0.5, -3.4, 1.8, -1.9)[0] is True
    assert exp2.pairs_agree(-1.2, -22.3, -1.3, -20.4)[0] is True
    assert exp2.pairs_agree(6.0, -8.0, -1.0, -2.0)[0] is False
    assert exp2.outcome(-2.0, -0.1, -4.9, None, quality_ok=None)[0] == "E"
