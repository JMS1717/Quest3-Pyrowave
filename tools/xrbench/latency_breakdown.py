"""Where the motion-to-photon milliseconds actually go, per segment.

    python -m xrbench.latency_breakdown <run_dir> [<run_dir> ...]

The project set a 60 ms bar and nothing measured clears it -- the best runs are 89-98 ms.
Decode and network together are only about a quarter of that, so the interesting part is the rest,
and `run_analysis` never printed it: ALVR reports seven pipeline stages and the summary only ever
surfaced three.

The stages ARE a partition: `statistics.rs` derives `network_latency` as the total minus all the
others, so they sum by construction and the residual should be ~0. An earlier version of this file
printed only six of the eight and called the rest "unaccounted", which made the two server-side
stages look like latency nobody could explain.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alvr_events_summary import load_events, summarize

from . import plan as planmod

# Ordered roughly as a frame experiences them.
# Every stage ALVR reports, in the order a frame meets them. These DO partition the total:
# statistics.rs defines network_latency as the total minus all the others, so the residual below
# should be ~0. Showing only six of them (the earlier version of this file) made the two
# server-side stages look like unexplained latency.
STAGES = ("game_time_ms", "server_compositor_ms", "encoder_ms", "network_ms", "decoder_ms",
          "decoder_queue_ms", "client_compositor_ms", "vsync_queue_ms")


def breakdown(segment_dir):
    """Per-stage means for one segment, or None if it produced no telemetry."""
    segment_dir = Path(segment_dir)
    events = segment_dir / "telemetry" / "events.json"
    if not events.exists():
        return None
    stats = summarize(load_events(events))
    if not stats.get("graph_events"):
        return None
    meta = json.loads((segment_dir / "meta.json").read_text())
    total = (stats.get("total_ms") or {}).get("mean")
    row = {"label": meta["segment"]["label"], "total_ms": total,
           "verdict": planmod.latency_verdict(total),
           "buffering": meta["segment"].get("buffering"),
           "hz": meta["segment"].get("hz")}
    for stage in STAGES:
        row[stage] = (stats.get(stage) or {}).get("mean")
    accounted = sum(row[s] for s in STAGES if row[s] is not None)
    # Should be ~0: the stages partition the total by construction. Anything large here means a
    # stage is missing from STAGES or the capture is inconsistent -- not that the pipeline is
    # spending time somewhere uninstrumented.
    row["residual_ms"] = None if total is None else total - accounted
    return row


DIST_KEYS = ("min", "p50", "mean", "p95", "p99", "max")


def distribution(segment_dir):
    """Full distribution of total latency plus the stages, for one segment.

    The pacing sweep asks whether total latency moves at all when pacing changes, or whether the
    stages merely rearrange underneath a fixed total. That is a question about the whole
    distribution -- a mean can stay put while the tail moves, and it is the tail that misses
    display deadlines.
    """
    segment_dir = Path(segment_dir)
    events = segment_dir / "telemetry" / "events.json"
    if not events.exists():
        return None
    stats = summarize(load_events(events))
    if not stats.get("graph_events"):
        return None
    meta = json.loads((segment_dir / "meta.json").read_text())
    out = {"label": meta["segment"]["label"],
           "buffering": meta["segment"].get("buffering"),
           "frame_pacing": meta["segment"].get("frame_pacing"),
           "pacing_headroom_us": meta["segment"].get("pacing_headroom_us"),
           "frames": stats["graph_events"]}
    for name in ("total_ms",) + STAGES:
        out[name] = {k: (stats.get(name) or {}).get(k) for k in DIST_KEYS}
    return out


def main():
    if "--dist" in sys.argv:
        args = [a for a in sys.argv[1:] if a != "--dist"]
        for run in args:
            run = Path(run)
            order = [s["label"] for s in json.loads((run / "plan.json").read_text())]
            for label in order:
                if not (run / label / "meta.json").exists():
                    continue
                d = distribution(run / label)
                if not d:
                    continue
                print(f"\n=== {d['label']}  buffering={d['buffering']} "
                      f"pacing={d['frame_pacing']} headroom={d['pacing_headroom_us']}us "
                      f"frames={d['frames']}")
                print("  ".join(f"{h:>20}" if h == "stage" else f"{h:>8}"
                                for h in ("stage",) + DIST_KEYS))
                for name in ("total_ms",) + STAGES:
                    vals = d[name]
                    print("  ".join([f"{name:>20}"]
                                    + [_fmt(vals[k]).rjust(8) for k in DIST_KEYS]))
        return

    rows = []
    for run in sys.argv[1:]:
        run = Path(run)
        order = [s["label"] for s in json.loads((run / "plan.json").read_text())]
        for label in order:
            if (run / label / "meta.json").exists():
                row = breakdown(run / label)
                if row:
                    rows.append(row)
    if not rows:
        print("no telemetry found")
        return

    head = ["label", "hz", "buffering", "total_ms", "verdict"] + list(STAGES) + ["residual_ms"]
    widths = {h: max(len(h), *(len(_fmt(r.get(h))) for r in rows)) for h in head}
    print("  ".join(h.rjust(widths[h]) for h in head))
    for r in rows:
        print("  ".join(_fmt(r.get(h)).rjust(widths[h]) for h in head))
    print(f"\nbar: {planmod.LATENCY_BUDGET_MS:.0f} ms. The stages partition the total -- ALVR "
          "derives network_ms as the remainder -- so residual_ms should be ~0.")
    print("vsync_queue_ms is the runtime's own prediction lead (predicted_display_time - now at "
          "submit),\nnot ALVR buffering: it measured 24.5-27.4 ms flat across 60/72/90 Hz, where a "
          "queue counted\nin vsyncs would scale with the frame period. No ALVR setting reaches it.")


def _fmt(value):
    if value is None:
        return "-"
    return f"{value:.2f}" if isinstance(value, float) else str(value)


if __name__ == "__main__":
    main()
