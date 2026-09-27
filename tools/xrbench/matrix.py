"""Per-cell report for the Home baselines and the panel-aspect resolution x bitrate matrix.

One row per segment folder, joining what the sweep already records: meta.json (segment, battery,
thermal zones), hottest.json (thermal status), gpufreq.csv (GPU clock), telemetry/events.json
(ALVR per-frame statistics), logcat.txt (the PyroWave receiver's periodic decode/fence reports and
cumulative frame counters), and quality.json when objective quality was scored. For Home cells the
receiver numbers are taken between the XRBENCH logcat markers only, so the primer scene and the
switch to Home are not counted. Every row carries its scene: Home results are Home results, never
gameplay results.

    python -m xrbench.matrix <run dir> [<run dir> ...] --out matrix.csv
"""
import argparse
import csv
import json
import re
import statistics
import sys
from pathlib import Path

MARK_START = "xrbench measure start"
MARK_END = "xrbench measure end"
PANEL_WIDTH = 3552
_COUNTER = re.compile(r"PYROWAVE-UDP: frames complete (\d+) partial (\d+) skipped (\d+) dropped (\d+) "
                      r"superseded (\d+) \| stale pk (\d+)")
_READY = re.compile(r"pyroclient: ready: (\d+x\d+)")


def window_text(text):
    """The logcat between the measure markers, and whether markers were found."""
    start = text.find(MARK_START)
    end = text.find(MARK_END, start + 1) if start >= 0 else -1
    if start < 0 or end < 0:
        return text, False
    return text[start:end], True


def receiver_counts(text):
    """Receiver frame counters: inside the Home window the difference between its first and last
    report (the counters are cumulative from client start); otherwise the final totals."""
    body, windowed = window_text(text)
    rows = [tuple(int(x) for x in m.groups()) for m in _COUNTER.finditer(body)]
    if not rows:
        return None
    last = rows[-1]
    first = rows[0] if windowed else (0,) * len(last)
    d = [b - a for a, b in zip(first, last)]
    return {"complete": d[0], "partial": d[1], "skipped": d[2], "dropped": d[3], "stale_packets": d[5],
            "windowed": windowed}


def one_percent_low(values):
    """Mean of the slowest hundredth of the per-frame values (at least one)."""
    xs = sorted(values)
    if not xs:
        return None
    n = max(1, len(xs) // 100)
    return sum(xs[:n]) / n


def quality(cell_dir):
    p = Path(cell_dir) / "quality.json"
    if not p.exists():
        return {"psnr_y": None, "ssim": None, "vmaf": None, "quality_frames": 0}
    q = json.loads(p.read_text())
    return {"psnr_y": q.get("psnr_y"), "ssim": q.get("ssim"), "vmaf": q.get("vmaf"), "quality_frames": q.get("frames", 0)}


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def _gpu_mhz(path):
    p = Path(path)
    if not p.exists():
        return None
    rows = list(csv.DictReader(p.open(newline="", encoding="utf-8")))
    return sum(int(r["cur_freq_hz"]) for r in rows) / len(rows) / 1e6 if rows else None


def _telemetry(cell_dir):
    p = Path(cell_dir) / "telemetry" / "events.json"
    if not p.exists():
        return [], 0
    events = json.loads(p.read_text(encoding="utf-8"))
    graph = [e["event_type"]["data"] for e in events if e.get("event_type", {}).get("id") == "GraphStatistics"]
    # packets_lost_total is cumulative from stream start: count only what the capture saw, as
    # alvr_events_summary does
    lost = [int(e["event_type"]["data"].get("packets_lost_total") or 0) for e in events
            if e.get("event_type", {}).get("id") == "StatisticsSummary"]
    return graph, (lost[-1] - lost[0]) if lost else None


def cell_row(cell_dir):
    from alvr_ffe_calc import FoveationConfig, encoded_eye_size
    from . import exp1
    cell = Path(cell_dir)
    meta = json.loads((cell / "meta.json").read_text())
    seg = meta["segment"]
    eye, eye_h = seg["eye"], seg.get("eye_h") or seg["eye"]
    fov = FoveationConfig(seg["center_size_x"], seg["center_size_y"], 0.0, 0.0,
                          seg["edge_ratio_x"], seg["edge_ratio_y"]) if seg.get("foveation", True) else None
    pw, ph = encoded_eye_size(eye, eye_h, fov)
    logcat = (cell / "logcat.txt").read_text(errors="replace") if (cell / "logcat.txt").exists() else ""
    ready = _READY.findall(logcat)
    body, _ = window_text(logcat)
    rep = exp1.cell_stats(exp1.parse_report_lines(body))
    dec, fence = rep.get("gpu decode") or {}, rep.get("submit->fence") or {}
    counts = receiver_counts(logcat) or {}
    graph, lost = _telemetry(cell)

    def series(key, scale=1e3):
        return [g[key] * scale for g in graph if g.get(key) is not None]
    total, fps = series("total_pipeline_latency_s"), series("client_fps", 1.0)
    bitrate = series("bitrate_bps", 1e-6)
    hot = json.loads((cell / "hottest.json").read_text()) if (cell / "hottest.json").exists() else {}
    zones = meta.get("thermal_zones_end") or {}
    mean = lambda xs: statistics.fmean(xs) if xs else None
    return {
        "label": seg["label"], "scene": seg.get("scene", "synthetic"), "status": meta.get("status"),
        "pct_panel": round(100 * eye / PANEL_WIDTH), "render_per_eye": f"{eye}x{eye_h}", "mbps_target": seg["mbps"],
        "predicted": f"{2 * pw}x{ph}", "encoded": ready[-1] if ready else None,
        "bitrate_mbps_mean": mean(bitrate),
        "decode_min_ms": dec.get("min"), "decode_mean_ms": dec.get("mean"), "decode_p50_ms": dec.get("p50"),
        "decode_p95_ms": dec.get("p95"), "decode_p99_ms": dec.get("p99"),
        "fence_mean_ms": fence.get("mean"), "fence_p95_ms": fence.get("p95"), "fence_p99_ms": fence.get("p99"),
        "encoder_mean_ms": mean(series("encoder_s")), "decoder_mean_ms": mean(series("decoder_s")),
        "total_mean_ms": mean(total), "total_p50_ms": _pct(total, 0.5), "total_p95_ms": _pct(total, 0.95),
        "fps_mean": mean(fps), "fps_median": _pct(fps, 0.5), "fps_p1_low": one_percent_low(fps),
        "decoder_queue_mean_ms": mean(series("decoder_queue_s")), "vsync_queue_mean_ms": mean(series("vsync_queue_s")),
        "partial": counts.get("partial"), "dropped": counts.get("dropped"), "skipped": counts.get("skipped"),
        "late_packets": counts.get("stale_packets"), "counters_windowed": counts.get("windowed"),
        "packets_lost": lost, "gpu_mhz_mean": _gpu_mhz(cell / "gpufreq.csv"),
        "gpu_c_end": zones.get("gpu_max_c"), "cpu_c_end": zones.get("cpu_max_c"),
        "hottest_c_start": hot.get("start_c"), "hottest_c_end": hot.get("end_c"),
        "thermal_status_start": hot.get("status_start"), "thermal_status_end": hot.get("status_end"),
        "battery_start": (meta.get("battery_start") or {}).get("level"),
        "battery_end": (meta.get("battery_end") or {}).get("level"),
        "battery_drain_pct_per_hour": meta.get("battery_drain_pct_per_hour"),
        **quality(cell),
    }


def run_rows(run_dirs):
    rows = []
    for run in run_dirs:
        for meta in sorted(Path(run).glob("*/meta.json")):
            rows.append(cell_row(meta.parent))
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("runs", nargs="+")
    p.add_argument("--out", default="matrix.csv")
    a = p.parse_args(argv)
    rows = run_rows(a.runs)
    if not rows:
        sys.exit("no segment folders (*/meta.json) under the given runs")
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} cells -> {a.out}")


if __name__ == "__main__":
    main()
