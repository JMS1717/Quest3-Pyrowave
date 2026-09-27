"""UDP loss / jitter / CPU test, PC -> headset, no codec in the path.

  python -m xrbench.udptest --ip <headset wifi ip> --rates 100 300 600 --seconds 10 --out udp-runs

Decides whether PyroWave can go UDP here. The receiver (tools/udptest/udprecv, pushed to
/data/local/tmp) counts MTU-sized datagrams, sequence gaps and arrival jitter and reports its own
CPU time; this side paces the sender to a target rate and samples the headset's /proc/stat and
thermal zones around each run. Everything the decision turns on is computed by the pure
functions below, which are unit-tested.

Frames: the sender emits each frame's datagrams as a burst every 1/fps, the way an encoder does,
tagged with frame id / index / count. The receiver judges each frame against a deadline measured
from its own first packet (the clocks are unrelated) and reports how many frames were complete in
time, how long assembly took, and how many packets arrived for frames already past their deadline
-- the ones a real transport should drop on sight instead of recovering.

Why 1400-byte datagrams: that is what fits an MTU without IP fragmentation, which is what a real
UDP transport would send. The per-packet cost on the headset's CPU is the thing being measured,
so the packet size must be the real one.
"""
import argparse
import json
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

from . import paths

HERE = paths.ROOT
ADB = HERE / r"android-tools\platform-tools\adb.exe"
RECEIVER_LOCAL = paths.CODE / "udptest" / "udprecv-android"
RECEIVER_DEVICE = "/data/local/tmp/udprecv"
PORT = 45200
PAYLOAD = 1400
FPS = 90
HEADER = 24
MAGIC = b"XRWU"


def packets_per_second(mbps, payload_bytes):
    """Datagrams per second for a target rate: the number the headset CPU has to service."""
    if mbps <= 0 or payload_bytes <= 0:
        raise ValueError(f"bad rate {mbps} Mbps / payload {payload_bytes} B")
    return mbps * 1e6 / 8 / payload_bytes


def packets_per_frame(mbps, fps, payload_bytes):
    """Datagrams one frame needs at this rate: the frame's byte budget split into MTU pieces."""
    if mbps <= 0 or fps <= 0 or payload_bytes <= 0:
        raise ValueError(f"bad rate {mbps} Mbps / {fps} fps / payload {payload_bytes} B")
    frame_bytes = mbps * 1e6 / 8 / fps
    return max(1, int(-(-frame_bytes // payload_bytes)))


def burst_sizes(pps, tick_s, ticks):
    """How many datagrams to send on each timer tick so the average hits `pps` exactly.

    A millisecond timer cannot send 53.6 packets per tick, so the schedule carries the fraction
    forward and alternates 53 and 54. Bresenham, not rounding: rounding every tick would bias the
    rate by up to half a packet per tick, which at 1 ms ticks is ~0.5 % -- the size of the loss
    figures being measured."""
    per_tick = pps * tick_s
    out, carry = [], 0.0
    for _ in range(ticks):
        carry += per_tick
        n = int(carry)
        out.append(n)
        carry -= n
    return out


def loss_percent(report, sent):
    """Loss against what the sender sent, never against the highest sequence seen.

    A tail that never arrives is loss too; max_seq would hide it. Duplicates can push received
    above sent, which is not negative loss."""
    if sent <= 0:
        return 0.0
    lost = max(sent - report["received"], 0)
    return round(100.0 * lost / sent, 3)


def cpu_percent(stat_before, stat_after):
    """Whole-headset CPU busy % between two /proc/stat 'cpu' lines (all cores aggregated)."""
    def split(line):
        f = [int(x) for x in line.split()[1:]]
        idle = f[3] + (f[4] if len(f) > 4 else 0)
        return sum(f), idle
    t0, i0 = split(stat_before)
    t1, i1 = split(stat_after)
    total = t1 - t0
    if total <= 0:
        return 0.0
    return 100.0 * (total - (i1 - i0)) / total


def summarise(report, sent, mbps, headset_cpu_percent):
    """The row the decision is read from. No one-way latency: the clocks are unrelated."""
    secs = report.get("seconds") or 0.0
    cpu_us = report.get("cpu_user_us", 0) + report.get("cpu_sys_us", 0)
    return {
        "mbps_target": mbps,
        "sent": sent,
        "received": report["received"],
        "loss_percent": loss_percent(report, sent),
        "mbps_received": round(report["bytes"] * 8 / secs / 1e6, 1) if secs else None,
        "jitter_ms_p50": round(report.get("jitter_us_p50", 0) / 1000.0, 3),
        "jitter_ms_p99": round(report.get("jitter_us_p99", 0) / 1000.0, 3),
        "jitter_ms_max": round(report.get("jitter_us_max", 0) / 1000.0, 3),
        "receiver_cpu_percent_of_one_core": round(100.0 * cpu_us / 1e6 / secs, 2) if secs else None,
        "headset_cpu_percent": headset_cpu_percent,
        "gro_enabled": bool(report.get("gro_enabled", 0)),
        "syscalls": report.get("syscalls"),
        "frames_seen": report.get("frames_seen"),
        "frames_on_time_percent": (round(100.0 * report["frames_complete_on_time"] / report["frames_seen"], 2)
                                   if report.get("frames_seen") else None),
        "frames_incomplete": ((report["frames_seen"] - report["frames_complete"])
                              if report.get("frames_seen") is not None else None),
        "packets_late": report.get("packets_late"),
        "packets_for_closed_frames": report.get("packets_for_closed_frames"),
        "assembly_ms_p50": round(report["assembly_us_p50"] / 1000.0, 3) if "assembly_us_p50" in report else None,
        "assembly_ms_p99": round(report["assembly_us_p99"] / 1000.0, 3) if "assembly_us_p99" in report else None,
        "assembly_ms_max": round(report["assembly_us_max"] / 1000.0, 3) if "assembly_us_max" in report else None,
        "age_ms_p99": round(report["age_us_p99"] / 1000.0, 3) if "age_us_p99" in report else None,
        "deadline_ms": round(report["deadline_us"] / 1000.0, 3) if "deadline_us" in report else None,
        "send_span_ms_p99": round(report["send_span_us_p99"] / 1000.0, 3) if "send_span_us_p99" in report else None,
        # what the air, driver and kernel added on top of the sender's own burst emission
        "path_added_ms_p99": (round((report["assembly_us_p99"] - report["send_span_us_p99"]) / 1000.0, 3)
                              if "assembly_us_p99" in report and "send_span_us_p99" in report else None),
        "kernel_timestamps": bool(report["kernel_timestamps"]) if "kernel_timestamps" in report else None,
        # kernel-receive to app-read: the scheduler's share, which a real transport must plan for
        "app_lag_ms_p99": round(report["app_lag_us_p99"] / 1000.0, 3) if "app_lag_us_p99" in report else None,
        "app_lag_ms_max": round(report["app_lag_us_max"] / 1000.0, 3) if "app_lag_us_max" in report else None,
        "rt_scheduling": bool(report["rt_scheduling"]) if "rt_scheduling" in report else None,
    }


# ---- the sender (runs on the PC) ----

def send_frames(ip, port, mbps, seconds, fps=FPS, payload=PAYLOAD):
    """Emit one frame's worth of datagrams as a burst every 1/fps, for `seconds`. Returns (sent, frames).

    Header: magic, u32 seq, u64 send time (monotonic ns), u32 frame id, u16 index, u16 count.
    The burst is what an encoder hands a transport; smoothing it would hide exactly the queueing
    the test exists to see. A perf_counter spin holds the frame cadence against the OS timer."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 16 << 20)
    body = bytes(bytearray((i * 7919) & 0xFF for i in range(payload - HEADER)))
    per_frame = packets_per_frame(mbps, fps, payload)
    period = 1.0 / fps
    frames = int(seconds * fps)
    seq = 0
    start = time.perf_counter()
    for f in range(frames):
        for i in range(per_frame):
            hdr = MAGIC + struct.pack("<IQIHH", seq, time.perf_counter_ns(), f, i, per_frame)
            sock.sendto(hdr + body, (ip, port))
            seq += 1
        target = start + (f + 1) * period
        while time.perf_counter() < target:
            pass
    sock.close()
    return seq, frames


# ---- keeping the radio awake ----

WIFI_LOCK_APP = "com.xrwired.receiver"   # our own client: holds WIFI_MODE_FULL_LOW_LATENCY in onCreate, then idles
WIFI_LOCK_TAG = "xrwired-receiver"


def wifi_low_latency_lock_held(dumpsys_wifi_text, tag):
    """True if dumpsys wifi lists a held WifiLock with this tag and type=4 (FULL_LOW_LATENCY)."""
    import re
    for line in dumpsys_wifi_text.splitlines():
        if "WifiLock{" in line and tag in line and re.search(r"\btype=4\b", line):
            return True
    return False


# ---- headset side, over adb ----

def adb(*args, check=True, timeout=60):
    result = subprocess.run([str(ADB), *args], capture_output=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: {result.stderr.decode(errors='replace')}")
    return result.stdout.decode(errors="replace")


def proc_stat_cpu_line():
    return adb("shell", "head -1 /proc/stat").strip()


def hottest_zone_c():
    out = adb("shell", "for d in /sys/class/thermal/thermal_zone*; do t=$(cat $d/temp 2>/dev/null) && echo $t; done | sort -n | tail -1")
    try:
        return int(out.strip()) / 1000.0
    except ValueError:
        return None


def push_receiver():
    adb("push", str(RECEIVER_LOCAL), RECEIVER_DEVICE)
    adb("shell", f"chmod 755 {RECEIVER_DEVICE}")


def hold_wifi_lock():
    """Bring up our receiver app so the radio stays in low-latency mode, and prove it."""
    adb("shell", "am", "force-stop", WIFI_LOCK_APP, check=False)
    # am start, not monkey: monkey launches proved unreliable on this headset.
    adb("shell", "am", "start", "-n", f"{WIFI_LOCK_APP}/.MainActivity", check=False)
    for _ in range(10):
        time.sleep(1)
        if wifi_low_latency_lock_held(adb("shell", "dumpsys wifi"), WIFI_LOCK_TAG):
            return True
    return False


def release_wifi_lock():
    adb("shell", "am", "force-stop", WIFI_LOCK_APP, check=False)


def run_one(ip, mbps, seconds, gro, core, out_dir, args_deadline_us=11111, rt=False):
    """One rate: start the receiver, sample CPU/thermal, blast, collect."""
    label = f"udp-{mbps}{'-gro' if gro else ''}{'-rt' if rt else ''}"
    report_path = f"/data/local/tmp/{label}.json"
    extra = (" --gro" if gro else "") + (f" --core {core}" if core is not None else "") + (" --rt" if rt else "")
    # Receiver runs `seconds + 3` so it outlives the sender and catches the tail.
    proc = subprocess.Popen([str(ADB), "shell",
                             f"{RECEIVER_DEVICE} {PORT} {seconds + 3}{extra} --deadline-us {args_deadline_us} > {report_path}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    stat0, temp0 = proc_stat_cpu_line(), hottest_zone_c()
    sent, frames = send_frames(ip, PORT, mbps, seconds)
    proc.wait(timeout=seconds + 30)
    stat1, temp1 = proc_stat_cpu_line(), hottest_zone_c()
    lock_held = wifi_low_latency_lock_held(adb("shell", "dumpsys wifi"), WIFI_LOCK_TAG)
    raw = adb("shell", f"cat {report_path}").strip()
    report = json.loads(raw)
    row = summarise(report, sent, mbps, round(cpu_percent(stat0, stat1), 1))
    row.update({"frames_sent": frames, "wifi_lock_held": lock_held,
                "hottest_c_before": temp0, "hottest_c_after": temp1, "raw": report})
    (Path(out_dir) / f"{label}.json").write_text(json.dumps(row, indent=2))
    return row


def format_table(rows):
    head = (f"{'Mbps':>5} {'loss%':>6} {'rx Mbps':>8} {'frames':>6} {'on time%':>8} {'incompl':>7} "
            f"{'late pk':>7} {'asm p99':>7} {'snd p99':>7} {'add p99':>7} {'lag p99':>7} {'lag max':>7} {'rx cpu':>6} {'hs cpu':>6} {'gro':>4} {'C aft':>6}")
    lines = [head]
    def n(v, w, f):
        return f"{v:>{w}{f}}" if v is not None else f"{'-':>{w}}"
    for r in rows:
        lines.append(f"{r['mbps_target']:>5} {r['loss_percent']:>6.2f} {n(r['mbps_received'], 8, '.1f')} "
                     f"{n(r.get('frames_seen'), 6, 'd')} {n(r.get('frames_on_time_percent'), 8, '.1f')} "
                     f"{n(r.get('frames_incomplete'), 7, 'd')} {n(r.get('packets_late'), 7, 'd')} "
                     f"{n(r.get('assembly_ms_p99'), 7, '.2f')} {n(r.get('send_span_ms_p99'), 7, '.2f')} {n(r.get('path_added_ms_p99'), 7, '.2f')} {n(r.get('app_lag_ms_p99'), 7, '.2f')} {n(r.get('app_lag_ms_max'), 7, '.1f')} "
                     f"{n(r['receiver_cpu_percent_of_one_core'], 6, '.1f')} {r['headset_cpu_percent']:>6.1f} "
                     f"{'on' if r['gro_enabled'] else 'off':>4} {('yes' if r.get('wifi_lock_held') else 'no'):>4} {('rt' if r.get('rt_scheduling') else '-'):>3} {n(r.get('hottest_c_after'), 6, '.1f')}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ip", required=True, help="headset Wi-Fi address")
    parser.add_argument("--rates", type=int, nargs="+", default=[100, 300, 600])
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--gro", action="store_true", help="also run each rate with UDP_GRO on")
    parser.add_argument("--core", type=int, default=None, help="pin the receiver to this CPU")
    parser.add_argument("--rt", action="store_true", help="run the receiver thread SCHED_FIFO, as a transport would")
    parser.add_argument("--wifi-lock", action="store_true",
                        help="hold a low-latency Wi-Fi lock via our receiver app, as a streaming client would")
    parser.add_argument("--deadline-ms", type=float, default=1000.0 / FPS, help="per-frame deadline from its first packet")
    parser.add_argument("--out", default="udp-runs")
    args = parser.parse_args(argv)

    out_dir = Path(args.out); out_dir.mkdir(parents=True, exist_ok=True)
    push_receiver()
    if args.wifi_lock:
        if not hold_wifi_lock():
            print("could not confirm a low-latency Wi-Fi lock; refusing to run under an unknown radio state")
            return 1
        print("wifi lock: held (low latency)")
    rows = []
    for mbps in args.rates:
        for gro in ([False, True] if args.gro else [False]):
            row = run_one(args.ip, mbps, args.seconds, gro, args.core, out_dir, int(args.deadline_ms * 1000), args.rt)
            rows.append(row)
            print(format_table([row]).splitlines()[1], flush=True)
            time.sleep(5)  # let the radio and the CPU settle between rates
    if args.wifi_lock:
        release_wifi_lock()
    print()
    print(format_table(rows))
    (out_dir / "summary.json").write_text(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
