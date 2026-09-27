#!/usr/bin/env python3
"""Summarize an ALVR events.json capture (from xrwired-benchmark-60.ps1) per pipeline stage."""
import argparse
import csv
import json
from pathlib import Path

STAGES_MS = {
    "total_ms": "total_pipeline_latency_s",
    # The two server-side stages. Omitting them made the breakdown's residual look like latency
    # nobody could account for, when it was simply these.
    "game_time_ms": "game_time_s",
    "server_compositor_ms": "server_compositor_s",
    "encoder_ms": "encoder_s",
    "network_ms": "network_s",
    "decoder_ms": "decoder_s",
    "decoder_queue_ms": "decoder_queue_s",
    "vsync_queue_ms": "vsync_queue_s",
    "client_compositor_ms": "client_compositor_s",
}


def load_events(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def _percentile(values, pct):
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct / 100.0
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _stats(values):
    # p99 as well as p95: pacing questions turn on the tail, where a late frame misses its display
    # deadline, and a mean hides exactly that.
    if not values:
        return {"mean": None, "p50": None, "p95": None, "p99": None, "min": None, "max": None}
    return {"mean": sum(values) / len(values), "p50": _percentile(values, 50),
            "p95": _percentile(values, 95), "p99": _percentile(values, 99),
            "min": min(values), "max": max(values)}


def summarize(events):
    graph = [e["event_type"]["data"] for e in events if e["event_type"]["id"] == "GraphStatistics"]
    summaries = [e["event_type"]["data"] for e in events
                 if e["event_type"]["id"] == "StatisticsSummary"]
    result = {name: _stats([g[key] * 1000.0 for g in graph if g.get(key) is not None])
              for name, key in STAGES_MS.items()}
    result["client_fps"] = _stats([g["client_fps"] for g in graph])
    result["server_fps"] = _stats([g["server_fps"] for g in graph])
    result["bitrate_mbps"] = _stats([g["bitrate_bps"] / 1e6 for g in graph
                                     if g.get("bitrate_bps") is not None])
    lost = [s["packets_lost_total"] for s in summaries]
    result["packets_lost"] = lost[-1] - lost[0] if lost else None
    result["graph_events"] = len(graph)
    result["summary_events"] = len(summaries)
    return result


def per_frame_rows(events):
    """One row per frame, keyed by the frame id, or [] if the capture has no frame ids.

    Stock ALVR emits GraphStatistics with no way to tell which frame each sample describes, so a
    capture can only ever be summarised, never joined to anything measured per frame -- image
    quality, encoded size, a tapped bitstream. `target_timestamp_ns` and `video_packet_bytes` come
    from patches/alvr-20.13.0-server-instrumentation.patch.

    Returning nothing for an unpatched capture is deliberate: rows without a key would look
    usable and quietly produce joins that are wrong."""
    rows = []
    for event in events:
        if event.get("event_type", {}).get("id") != "GraphStatistics":
            continue
        data = event["event_type"]["data"]
        if data.get("target_timestamp_ns") is None:
            continue
        row = {"target_timestamp_ns": data["target_timestamp_ns"],
               "video_packet_bytes": data.get("video_packet_bytes")}
        for name, key in STAGES_MS.items():
            if data.get(key) is not None:
                row[name] = data[key] * 1000.0
        for key in ("client_fps", "server_fps"):
            if data.get(key) is not None:
                row[key] = data[key]
        if data.get("bitrate_bps") is not None:
            row["bitrate_mbps"] = data["bitrate_bps"] / 1e6
        rows.append(row)
    return rows


def _fmt(value):
    return "" if value is None else f"{value:.2f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("events", help="path to events.json")
    parser.add_argument("--label", default="", help="run label for the CSV row")
    parser.add_argument("--csv", help="append a one-row summary to this CSV file")
    args = parser.parse_args()

    summary = summarize(load_events(args.events))
    metrics = [k for k, v in summary.items() if isinstance(v, dict)]
    print(f"{'metric':<22}{'mean':>9}{'p50':>9}{'p95':>9}{'p99':>9}{'min':>9}{'max':>9}")
    for name in metrics:
        s = summary[name]
        print(f"{name:<22}"
              + "".join(f"{_fmt(s[k]):>9}" for k in ("mean", "p50", "p95", "p99", "min", "max")))
    print(f"packets_lost {summary['packets_lost']}  frames {summary['graph_events']}  "
          f"summaries {summary['summary_events']}")

    if args.csv:
        header = ["label"] + [f"{m}_{k}" for m in metrics for k in ("mean", "p95")] + ["packets_lost"]
        row = [args.label] + [_fmt(summary[m][k]) for m in metrics for k in ("mean", "p95")]
        row.append(summary["packets_lost"])
        path = Path(args.csv)
        new_file = not path.exists()
        with path.open("a", newline="") as handle:
            writer = csv.writer(handle)
            if new_file:
                writer.writerow(header)
            writer.writerow(row)


if __name__ == "__main__":
    main()
