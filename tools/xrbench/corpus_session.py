"""Experiment 5 worn-capture console.

Holds one live PyroWave stream at the candidate operating point (no synthetic scene) while
the tester wears the headset and plays, and takes lossless 90-frame clips on demand through the
encoder's runtime trigger file, filing each under its content class with temporal statistics,
title and ROI in the corpus index. Nothing here touches codec behaviour.

Commands on stdin:
  title <text>            the running title / scene (stored with every following clip)
  clip <class> [frames]   take a clip (default 90 frames) for one of the eight classes or X_other
  roi <clip-stem> x y w h record a region of interest for a clip, before any codec comparison
  note <text>             free note into the index log
  status                  accepted clips per class against the minimums
  stop                    end the session
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

from . import plan as planmod
from . import rdcorpus
from . import sweep

CORPUS = Path(planmod.CORPUS_ROOT)
TRIGGER_DIR = CORPUS / "_trigger"
INDEX = CORPUS / "index.csv"
INDEX_COLUMNS = ("clip", "class", "title", "frames", "requested_frames", "path", "captured_at", "first_timestamp_ns", "last_timestamp_ns",
                 "mean_consecutive_diff", "identical_consecutive_frac", "pair_max", "mean_stride_diff", "temporal_class", "accepted", "roi", "note")


def trigger_line(path, frames):
    return f"{path} {frames}\n"


def next_clip_name(klass):
    existing = sorted((CORPUS / klass).glob(f"{klass}-*.y4m")) if (CORPUS / klass).exists() else []
    return f"{klass}-{len(existing) + 1:02d}-{dt.datetime.now():%Y%m%d-%H%M%S}"


def load_index():
    if not INDEX.exists():
        return []
    with open(INDEX, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_index(rows):
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    with open(INDEX, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=INDEX_COLUMNS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in INDEX_COLUMNS} for r in rows)


def status_lines(rows):
    out = []
    for klass in rdcorpus.CLASSES:
        n = sum(1 for r in rows if r["class"] == klass and str(r.get("accepted")) == "True")
        need = rdcorpus.CLASS_MINIMUM[klass]
        out.append(f"{klass:16s} {n}/{need} accepted" + ("" if n >= need else "  <- short"))
    return out


def freeze_index():
    """Copy the index with a hash before RD analysis: the corpus is fixed from here on."""
    data = INDEX.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    frozen = INDEX.with_name(f"index.frozen-{dt.datetime.now():%Y%m%d-%H%M%S}.csv")
    frozen.write_bytes(data)
    (frozen.with_suffix(".sha256")).write_text(digest + "\n")
    return frozen, digest


def wait_for_clip(path, frames, timeout=120):
    """The encoder appends one FRAME per rendered frame; done when .frames.csv has `frames` rows."""
    sidecar = Path(str(path) + ".frames.csv")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if sidecar.exists():
            rows = sidecar.read_text().strip().splitlines()
            if len(rows) - 1 >= frames:
                return True
        time.sleep(1)
    return False


def take_clip(klass, frames, title, note=""):
    if klass not in rdcorpus.CLASSES:
        return f"unknown class {klass}; one of {', '.join(rdcorpus.CLASSES)}"
    (CORPUS / klass).mkdir(parents=True, exist_ok=True)
    TRIGGER_DIR.mkdir(parents=True, exist_ok=True)
    stem = next_clip_name(klass)
    path = CORPUS / klass / f"{stem}.y4m"
    (TRIGGER_DIR / "trigger.txt").write_text(trigger_line(path, frames))
    sweep.log(f"trigger written for {stem} ({frames} frames)")
    if not wait_for_clip(path, frames):
        return f"TIMEOUT: {stem} did not reach {frames} frames (is the stream live?)"
    ts = rdcorpus.temporal_stats(path)
    accepted = rdcorpus.clip_accepted(klass, ts["temporal_class"])
    side = [l.split(",") for l in Path(str(path) + ".frames.csv").read_text().strip().splitlines()[1:]]
    row = {"clip": stem, "class": klass, "title": title, "frames": ts["frames"], "requested_frames": frames, "path": str(path),
           "captured_at": dt.datetime.now().isoformat(timespec="seconds"),
           "first_timestamp_ns": side[0][2] if side else "", "last_timestamp_ns": side[-1][2] if side else "",
           "mean_consecutive_diff": f"{ts['mean_consecutive_diff']:.4f}", "identical_consecutive_frac": f"{ts['identical_consecutive_frac']:.4f}",
           "pair_max": f"{ts['pair_max']:.4f}", "mean_stride_diff": f"{ts['mean_stride_diff']:.4f}", "temporal_class": ts["temporal_class"],
           "accepted": accepted, "roi": "", "note": note}
    rows = load_index()
    rows.append(row)
    save_index(rows)
    return (f"{stem}: {ts['frames']} frames, {ts['temporal_class']} (mean diff {ts['mean_consecutive_diff']:.2f}, "
            f"identical {100 * ts['identical_consecutive_frac']:.0f} %) -> {'ACCEPT' if accepted else 'REJECT (motion intrinsic to ' + klass + ')'}")


def set_roi(stem, box):
    rows = load_index()
    for r in rows:
        if r["clip"] == stem:
            r["roi"] = " ".join(str(int(v)) for v in box)
            save_index(rows)
            return f"roi for {stem}: {r['roi']}"
    return f"no clip {stem} in the index"


def hold_session(args):
    """Bring the stream up at the operating point and hold it while commands arrive."""
    base = next(s for s in planmod.pyro_res_plan() if s.label == "X60-400-90")
    seg = planmod.Segment("SESSION", "alvr", codec=base.codec, mbps=base.mbps, hz=base.hz, eye=base.eye, eye_h=base.eye_h,
                          workload=base.workload, packet_size=base.packet_size, center_size_x=base.center_size_x,
                          center_size_y=base.center_size_y, buffering=base.buffering, decode_path="compute")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    client_ip = args.client_ip
    if not sweep.api_up():
        sweep.restart_steamvr(task=sweep.steamvr_task_for(seg.codec))   # the configurator talks to ALVR's API
    (out_dir / "configure.txt").write_text(sweep.configure_alvr(seg, args.client_host, client_ip))
    sweep.set_gaze_env(seg.gaze)
    sweep.set_wavelet_env(seg.wavelet)
    sweep.set_decode_path_prop(seg.decode_path)
    sweep.set_pyro_precision_prop(seg.pyro_precision)
    sweep.set_dump_env(None, 0, 0)
    sweep.restart_steamvr(task=sweep.steamvr_task_for(seg.codec))
    time.sleep(5)
    watchdog = sweep.AwakeWatchdog()
    watchdog.start()
    clock = sweep.GpuClockSampler()
    clock.start()
    sweep.connect_with_applied_config(args, out_dir, client_ip, seg.codec)
    sweep.log("session live: put the headset on and launch a title; commands: title/clip/roi/note/status/stop")
    title = ""
    try:
        for line in sys.stdin:
            parts = line.strip().split()
            if not parts:
                continue
            cmd, rest = parts[0], parts[1:]
            if cmd == "stop":
                break
            elif cmd == "title":
                title = " ".join(rest); print(f"title = {title}", flush=True)
            elif cmd == "clip":
                frames = int(rest[1]) if len(rest) > 1 else 90
                print(take_clip(rest[0] if rest else "X_other", frames, title), flush=True)
            elif cmd == "roi" and len(rest) == 5:
                print(set_roi(rest[0], [float(v) for v in rest[1:]]), flush=True)
            elif cmd == "note":
                rows = load_index(); rows.append({"clip": "", "class": "", "note": " ".join(rest), "captured_at": dt.datetime.now().isoformat(timespec="seconds")}); save_index(rows)
                print("noted", flush=True)
            elif cmd == "status":
                print("\n".join(status_lines(load_index())), flush=True)
            else:
                print("commands: title <text> | clip <class> [frames] | roi <clip> x y w h | note <text> | status | stop", flush=True)
    finally:
        watchdog.stop.set()
        clock.finish(out_dir / "gpufreq.csv")
        sweep.set_dump_env(None, 0, 0)
        print("\n".join(status_lines(load_index())), flush=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start"); s.add_argument("--client-host", required=True); s.add_argument("--client-ip", required=True)
    s.add_argument("--out", default=str(sweep.HERE / "xrbench-runs" / f"corpus-session-{dt.datetime.now():%Y%m%d-%H%M}"))
    sub.add_parser("status")
    f = sub.add_parser("freeze")
    a = p.parse_args(argv)
    if a.cmd == "start":
        hold_session(a)
    elif a.cmd == "status":
        print("\n".join(status_lines(load_index())))
    elif a.cmd == "freeze":
        frozen, digest = freeze_index()
        print(f"frozen {frozen} sha256 {digest}")


if __name__ == "__main__":
    main()
