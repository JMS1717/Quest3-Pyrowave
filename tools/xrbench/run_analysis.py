"""Analyze a pulled sweep folder on the Mac:  python -m xrbench.run_analysis <run_dir>

Per segment: frame pacing from clip.mp4, image quality from still_scene_*.png, ALVR telemetry from
telemetry/events.json. Writes results.json and results.csv into the run folder.
"""
import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np

from alvr_ffe_calc import FoveationConfig, encoded_eye_size
from alvr_events_summary import load_events, summarize
from . import analyze as a
from . import plan as planmod
from . import patterns as p

# Measured on this headset: dumpsys display reports 7104x3840 (3552x3840 per eye) and the client's
# ViewsConfig reports 94.4 x 105.1 deg. Together these are what make "sharper" a number rather than
# an impression -- px/deg is directly comparable to what the panel can physically resolve.
PANEL_WIDTH_PER_EYE = 3552
FOV_H_DEG = 94.4


def clip_pacing(clip_path, stream_fps):
    capture = cv2.VideoCapture(str(clip_path))
    counters, stamps = [], []
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        stamps.append(capture.get(cv2.CAP_PROP_POS_MSEC))
        panel = a.rectify(frame)
        counters.append(None if panel is None else p.decode_counter(panel))
    capture.release()
    if len(stamps) < 2:
        return None
    capture_fps = (len(stamps) - 1) * 1000.0 / (stamps[-1] - stamps[0])
    result = a.sequence_metrics(counters, stream_fps=stream_fps, capture_fps=capture_fps)
    result["capture_fps"] = capture_fps
    return result


def still_quality(segment_dir, label):
    static = p.build_static(label)
    rows = [r for r in (a.analyze_still(cv2.imread(str(path)), static)
                        for path in sorted(segment_dir.glob("still_scene_*.png"))) if r is not None]
    if not rows:
        return None
    median = lambda values: float(np.median(values))
    quality = {
        "stills": len(rows),
        "psnr_overall": median([r["psnr"]["overall"] for r in rows]),
        "ssim_overall": median([r["ssim"]["overall"] for r in rows]),
        "detail_sharpness": median([r["sharpness"]["detail"] for r in rows]),
    }
    for region in a.FIDELITY_REGIONS:
        values = [r["psnr"][region] for r in rows if region in r["psnr"]]
        if values:
            quality[f"psnr_{region}"] = median(values)
    for ramp in rows[0]["banding"]:
        quality[f"{ramp}_step_ratio"] = median([r["banding"][ramp]["step_ratio"] for r in rows])
        quality[f"{ramp}_levels"] = median([r["banding"][ramp]["effective_levels"] for r in rows])
    best = max(rows, key=lambda r: r["psnr"]["overall"])
    cv2.imwrite(str(segment_dir / "panel_rectified.png"), best["panel"])
    return quality


def display_latency_summary(segment_dir, label):
    """Staleness of the headset image at each screenshot (stills.csv + frames.csv, same clock)."""
    frames_csv, stills_csv = segment_dir / "frames.csv", segment_dir / "stills.csv"
    if not (frames_csv.exists() and stills_csv.exists()):
        return None
    with open(frames_csv) as handle:
        submitted = {int(r["frame"]): float(r["t_submitted"]) for r in csv.DictReader(handle)}
    samples = []
    with open(stills_csv) as handle:
        for row in csv.DictReader(handle):
            image = cv2.imread(str(segment_dir / row["still"]))
            panel = a.rectify(image) if image is not None else None
            counter = p.decode_counter(panel) if panel is not None else None
            lat = a.display_latency(counter, float(row["t_request"]), submitted)
            if lat:
                samples.append(lat)
    if not samples:
        return None
    return {"samples": len(samples),
            "median_ms": float(np.median([x["latency_ms"] for x in samples])),
            "max_ms": float(max(x["latency_ms"] for x in samples)),
            "max_frames_behind": int(max(x["frames_behind"] for x in samples))}


def analyze_segment(segment_dir):
    meta = json.loads((segment_dir / "meta.json").read_text())
    seg = meta["segment"]
    clip = segment_dir / "clip.mp4"
    events = segment_dir / "telemetry" / "events.json"
    telemetry = summarize(load_events(events)) if events.exists() else None
    gaze_stats = read_gaze_stats(segment_dir)
    geometry = segment_geometry(seg)
    return {
        "label": seg["label"], "stack": seg["stack"], "transport": seg.get("transport", "wifi"),
        "codec": seg["codec"], "mbps": seg["mbps"],
        "eye": seg["eye"], "hz": seg["hz"], "foveation": seg.get("foveation"),
        "buffering": seg.get("buffering"), "note": seg.get("note", ""),
        # 2e axes. `.get` with the pre-2e default throughout, so runs captured before these
        # existed still analyse rather than raising.
        "workload": seg.get("workload", "control"),
        "eye_h": seg.get("eye_h") or seg["eye"],
        "center_size_x": seg.get("center_size_x", 0.45),
        "center_size_y": seg.get("center_size_y", 0.40),
        "gaze": seg.get("gaze", True),
        "render_scale": seg.get("render_scale", 1.0),
        **geometry,
        "gaze_stats": gaze_stats,
        "likely_worn": gaze_stats and gaze_stats.get("likely_worn"),
        "clamp_fraction": gaze_stats and gaze_stats.get("clamp_fraction"),
        "staleness_p95": gaze_stats and gaze_stats.get("staleness_p95"),
        "status": meta.get("status"), "watchdog_wakes": meta.get("watchdog_wakes"),
        "thermal_end": meta.get("thermal_end"),
        "pacing": clip_pacing(clip, seg["hz"]) if clip.exists() else None,
        "quality": still_quality(segment_dir, seg["label"]),
        "telemetry": telemetry if telemetry and telemetry["graph_events"] else None,
        "latency": display_latency_summary(segment_dir, seg["label"]),
        "scene_fps": scene_fps(segment_dir),
    }


def read_gaze_stats(segment_dir):
    """The gaze summary sweep.py wrote for this segment, or None for a run captured before it."""
    path = Path(segment_dir) / "gaze_stats.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except ValueError:
        return None


def segment_geometry(seg):
    """Angular resolution and encoded size for a segment.

    `centre_px_per_deg` is the sampling density inside the full-resolution centre, which is 1:1
    with the render resolution -- so it is directly comparable to the panel's own 37.6 px/deg, and
    `panel_fraction` says how close this preset gets. `encoded_mpx` is what the decoder actually
    has to chew, which is the budget the whole preset ladder is built around."""
    width = seg["eye"]
    height = seg.get("eye_h") or width
    config = None
    if seg.get("foveation"):
        config = FoveationConfig(
            center_size_x=seg.get("center_size_x", 0.45), center_size_y=seg.get("center_size_y", 0.40),
            edge_ratio_x=seg.get("edge_ratio_x", 3.0), edge_ratio_y=seg.get("edge_ratio_y", 4.0))
    enc_w, enc_h = encoded_eye_size(width, height, config)
    return {
        "centre_px_per_deg": width / FOV_H_DEG,
        "panel_fraction": width / PANEL_WIDTH_PER_EYE,
        "encoded_width": enc_w, "encoded_height": enc_h,
        "encoded_mpx": enc_w * enc_h / 1e6,
    }


def scene_fps(segment_dir):
    """Frames the scene app actually submitted per second (SteamVR throttles it under back-pressure)."""
    path = segment_dir / "frames.csv"
    if not path.exists():
        return None
    with open(path) as handle:
        times = [float(r["t_submitted"]) for r in csv.DictReader(handle)]
    return (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 else None


def analyze_run(run_dir):
    run_dir = Path(run_dir)
    return [analyze_segment(d) for d in sorted(run_dir.iterdir()) if (d / "meta.json").exists()]


def _scale(value, factor):
    """value * factor, preserving None -- and preserving a real 0.

    `x and x * 100` and `... or None` both collapse 0.0 into None, which turns a measured zero
    into an empty cell that reads as "not measured". A clamp fraction of zero is a result.
    """
    return None if value is None else value * factor


CSV_COLUMNS = [
    ("label", lambda r: r["label"]), ("workload", lambda r: r.get("workload")),
    ("stack", lambda r: r["stack"]), ("codec", lambda r: r["codec"]),
    ("mbps", lambda r: r["mbps"]), ("eye", lambda r: r["eye"]),
    ("eye_h", lambda r: r.get("eye_h")), ("hz", lambda r: r["hz"]),
    ("centre", lambda r: r.get("center_size_x")),
    ("gaze", lambda r: r.get("gaze")), ("ss", lambda r: r.get("render_scale")),
    ("px_per_deg", lambda r: r.get("centre_px_per_deg")),
    ("panel_pct", lambda r: _scale(r.get("panel_fraction"), 100)),
    ("encoded_mpx", lambda r: r.get("encoded_mpx")),
    ("worn", lambda r: r.get("likely_worn")),
    # Android thermal status at the end of the segment. >= 2 means the headset was throttling, and
    # decode timings drift with temperature -- a segment measured hot is not comparable with one
    # measured cold, which is why the thermal plan brackets a ladder with an identical control.
    ("thermal_end", lambda r: r.get("thermal_end")),
    ("clamp_pct", lambda r: _scale(r.get("clamp_fraction"), 100)),
    ("staleness_p95", lambda r: r.get("staleness_p95")),
    ("transport", lambda r: r.get("transport")),
    ("status", lambda r: r["status"]),
    ("decoder_ms", lambda r: r["telemetry"] and r["telemetry"]["decoder_ms"]["mean"]),
    ("total_ms", lambda r: r["telemetry"] and r["telemetry"]["total_ms"]["mean"]),
    # Against the project's 60 ms bar. Reported next to total_ms rather than replacing it, because the
    # margin matters as much as the verdict when nothing currently passes.
    ("latency_verdict", lambda r: planmod.latency_verdict(
        r["telemetry"] and r["telemetry"]["total_ms"]["mean"])),
    ("client_fps", lambda r: r["telemetry"] and r["telemetry"]["client_fps"]["mean"]),
    ("actual_mbps", lambda r: r["telemetry"] and r["telemetry"]["bitrate_mbps"]["mean"]),
    ("packets_lost", lambda r: r["telemetry"] and r["telemetry"]["packets_lost"]),
    ("scene_fps", lambda r: r.get("scene_fps")),
    ("latency_median_ms", lambda r: r.get("latency") and r["latency"]["median_ms"]),
    ("frames_behind_max", lambda r: r.get("latency") and r["latency"]["max_frames_behind"]),
    ("clip_irregular", lambda r: r["pacing"] and r["pacing"]["irregular"]),
    ("clip_misread", lambda r: r["pacing"] and r["pacing"].get("misread")),
    ("clip_repeated", lambda r: r["pacing"] and r["pacing"]["repeated"]),
    ("clip_skipped", lambda r: r["pacing"] and r["pacing"]["skipped"]),
    ("clip_unreadable", lambda r: r["pacing"] and r["pacing"]["unreadable"]),
    ("psnr_overall", lambda r: r["quality"] and r["quality"]["psnr_overall"]),
    ("ssim_overall", lambda r: r["quality"] and r["quality"]["ssim_overall"]),
    ("psnr_motion", lambda r: r["quality"] and r["quality"].get("psnr_motion")),
    ("detail_sharpness", lambda r: r["quality"] and r["quality"]["detail_sharpness"]),
    ("banding_gray", lambda r: r["quality"] and r["quality"]["gradient_gray_step_ratio"]),
    ("banding_dark", lambda r: r["quality"] and r["quality"]["gradient_dark_step_ratio"]),
    ("watchdog_wakes", lambda r: r["watchdog_wakes"]),
]


def write_outputs(run_dir, results):
    run_dir = Path(run_dir)
    (run_dir / "results.json").write_text(json.dumps(results, indent=2, default=float))
    with open(run_dir / "results.csv", "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([name for name, _ in CSV_COLUMNS])
        for r in results:
            row = []
            for _, get in CSV_COLUMNS:
                value = get(r)
                row.append(f"{value:.3f}" if isinstance(value, float) else ("" if value is None else value))
            writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    args = parser.parse_args()
    results = analyze_run(args.run_dir)
    write_outputs(args.run_dir, results)
    for r in results:
        print(r["label"], r["status"], "pacing", r["pacing"] and {k: r["pacing"][k] for k in ("irregular", "skipped", "repeated")},
              "psnr", r["quality"] and round(r["quality"]["psnr_overall"], 2))


if __name__ == "__main__":
    main()
