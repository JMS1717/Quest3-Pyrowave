"""Raw pipe: NVENC H.264 straight to our receiver app, no ALVR in the path.

  python -m xrbench.rawpipe --wifi-ip <headset ip> --runs wifi:400 wifi:800 adb:400 adb:800

PC: FFmpeg generates a full-frame random-noise test pattern (worst case for the encoder and the
link), encodes it with h264_nvenc in ultra-low-latency CBR, and this script frames each access
unit ([u32 length][u64 pts_us][data], after a 16-byte "XRW1" header) onto one TCP connection.
Headset: com.xrwired.receiver decodes with c2.qti.avc.decoder.low_latency and logs one XRSTAT line
per second (output fps, input Mbps, decode latency percentiles). Transports: Wi-Fi (direct TCP to
the headset) or USB (adb forward). Results: <out>/<label>.json plus a summary table.
"""
import argparse
import json
import os
import re
import socket
import statistics
import struct
import subprocess
import threading
import time
from pathlib import Path

H264_MAX_MBPS = 1000          # High profile level 6.2 bitrate cap; NVENC refuses more ("Invalid Level")
RECEIVER = "com.xrwired.receiver"          # flat SurfaceView client
RECEIVER_XR = "com.xrwired.receiverxr"     # our OpenXR client: projection layer, one view per eye


def receiver_package(vr):
    return RECEIVER_XR if vr else RECEIVER

RECEIVER_PORT = 45100
XRSTAT = re.compile(r"XRSTAT (.*)$")


def access_units(stream, chunk=256 * 1024):
    """Split an Annex B H.264 byte stream into access units at each access unit delimiter (NAL 9)."""
    buf = bytearray()
    while True:
        data = stream.read(chunk)
        buf.extend(data or b"")
        starts = []
        i = buf.find(b"\x00\x00\x01\x09")
        while i >= 0:
            starts.append(i - 1 if i > 0 and buf[i - 1] == 0 else i)   # include a 4-byte start code
            i = buf.find(b"\x00\x00\x01\x09", i + 4)
        if not data:                                   # end of stream: flush everything
            bounds = starts + [len(buf)]
            for a, b in zip(bounds, bounds[1:]):
                yield bytes(buf[a:b])
            return
        for a, b in zip(starts, starts[1:]):           # only units whose end has been seen
            yield bytes(buf[a:b])
        if starts:
            del buf[:starts[-1]]


# Stream codec ids: the receiver needs the view count and the bit depth before it configures a decoder.
CODEC_IDS = {"avc": 0, "mvhevc": 1, "mvhevc10": 2, "hevc": 3, "hevc10": 4}


def mvhevc_clip_exe():
    """tools/mvhevc_clip build: XRBENCH_MVHEVC_CLIP, else mvhevc_clip/mvhevc_clip.exe beside the xrbench package."""
    return os.environ.get("XRBENCH_MVHEVC_CLIP") or str(Path(__file__).resolve().parents[1] / "mvhevc_clip" / "mvhevc_clip.exe")


def stream_header(width, height, fps, codec="avc"):
    """AVC keeps the original 16-byte "XRW1" header; other codecs send "XRW2" with a codec id (per-eye size
    for MV-HEVC)."""
    if codec == "avc":
        return b"XRW1" + struct.pack(">III", width, height, fps)
    return b"XRW2" + struct.pack(">IIII", width, height, fps, CODEC_IDS[codec])


def codec_for_coder(coder):
    """Encoder setting -> stream codec id name ("cavlc"/"cabac" are both H.264 entropy coders)."""
    return "avc" if coder in ("cavlc", "cabac") else coder


def length_records(stream):
    """Payloads of a [u32 big-endian length][bytes] file (mvhevc_clip output: one stereo AU each)."""
    while True:
        head = stream.read(4)
        if len(head) < 4:
            return
        yield stream.read(struct.unpack(">I", head)[0])


def nvenc_clip_command(width, height, fps, mbps, frames, output, coder="mvhevc", preset=1, noise=8):
    """mvhevc_clip.exe: MV-HEVC (two views, HQ tuning is the only one it accepts) or single-view HEVC.
    `mbps` is the rate for the whole stream; NVENC applies its target per view, so MV-HEVC gets half."""
    codec = "mvhevc" if coder.startswith("mvhevc") else "hevc"
    ten_bit = coder.endswith("10")
    per_view = mbps // 2 if codec == "mvhevc" else mbps
    return ([mvhevc_clip_exe(), "--codec", codec, "--w", str(width), "--h", str(height), "--fps", str(fps),
             "--mbps", str(per_view), "--frames", str(frames), "--preset", str(preset), "--noise", str(noise),
             "--tuning", "hq" if codec == "mvhevc" else "ull", "--out", str(output)]
            + (["--10bit"] if ten_bit else []))


def mvhevc_clip_command(width, height, fps, mbps, frames, output, preset=1, ten_bit=False, noise=8):
    return nvenc_clip_command(width, height, fps, mbps, frames, output,
                              coder="mvhevc10" if ten_bit else "mvhevc", preset=preset, noise=noise)


def frame_record(access_unit, pts_us):
    return struct.pack(">IQ", len(access_unit), pts_us) + access_unit


def parse_xrstat(text):
    rows = []
    for line in text.splitlines():
        m = XRSTAT.search(line)
        if not m:
            continue
        row = {}
        for field in m.group(1).split():
            key, _, value = field.partition("=")
            row[key] = float(value) if "." in value or key != "in_flight" else int(value)
        rows.append(row)
    return rows


def parse_health(thermal_text, battery_text):
    """Headset thermal status, AP (SoC) temperature and battery level from dumpsys thermalservice / battery."""
    grab = lambda pattern, text, cast: (lambda m: cast(m.group(1)) if m else None)(re.search(pattern, text))
    return {"thermal_status": grab(r"Thermal Status: (\d+)", thermal_text, int),
            "ap_c": grab(r"mValue=([\d.]+), mType=\d+, mName=AP,", thermal_text, float),
            "battery": grab(r"level: (\d+)", battery_text, int)}


def summarize_xrstat(rows, skip_first=2):
    rows = rows[skip_first:] if len(rows) > skip_first + 2 else rows
    if not rows:
        return None
    mean = lambda k: statistics.fmean(r[k] for r in rows)
    return {"seconds": len(rows), "out_fps": mean("out_fps"), "in_mbps": mean("in_mbps"),
            "dec_p50_median": statistics.median(r["dec_p50"] for r in rows),
            "dec_p95_max": max(r["dec_p95"] for r in rows), "dec_max": max(r["dec_max"] for r in rows),
            "in_wait_ms": mean("in_wait_ms"), "in_flight_mean": mean("in_flight")}


def clip_name(width, height, fps, mbps, coder, slices=1):
    if coder.startswith("mvhevc") or coder.startswith("hevc"):
        return f"{coder}_{width}x{height}_{fps}fps_{mbps}mbps.mvhevc"
    return f"clip_{width}x{height}_{fps}_{mbps}_{coder}" + (f"_s{slices}" if slices > 1 else "") + ".h264"


def ffmpeg_command(width, height, fps, mbps, source="noise", output=None, frames=None, coder="cavlc", slices=1):
    """h264_nvenc ultra-low-latency CBR. Live (-re, to stdout) unless output/frames are given, in
    which case it encodes a clip offline as fast as the GPU allows. CAVLC by default: with CABAC
    the headset's AVC decoder topped out near 355 Mbps on noise."""
    mbps = min(mbps, H264_MAX_MBPS)
    pattern = f"testsrc2=s={width}x{height}:r={fps}" + (",noise=alls=40:allf=t+u" if source == "noise" else "")
    live = output is None
    return (["ffmpeg", "-hide_banner", "-loglevel", "error"] + (["-re"] if live else []) +
            ["-f", "lavfi", "-i", pattern] + ([] if live else ["-frames:v", str(frames)]) +
            ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull", "-rc", "cbr", "-coder", coder,
             "-b:v", f"{mbps}M", "-maxrate", f"{mbps}M", "-bufsize", f"{int(mbps * 1000 / fps)}k",
             "-zerolatency", "1", "-delay", "0", "-bf", "0", "-g", str(fps), "-forced-idr", "1",
             "-aud", "1"] + (["-slices", str(slices)] if slices > 1 else []) +
            ["-bsf:v", "dump_extra", "-f", "h264"] + (["pipe:1"] if live else ["-y", output]))


UDP_PORT = 45101


def udp_hello(width, height, fps):
    return b"XRH1" + struct.pack(">III", width, height, fps)


def udp_chunks(access_unit, pts, chunk):
    """Datagrams for one frame: 24-byte header (XRU1, pts, frame length, offset, chunk length,
    flags) plus up to `chunk` bytes. chunk 1400 avoids IP fragmentation; up to ~65000 lets the IP
    layer fragment (Virtual Desktop style: fewer syscalls, one datagram per large piece)."""
    total = len(access_unit)
    return [struct.pack(">4sQIIHH", b"XRU1", pts, total, off, min(chunk, total - off), 0)
            + access_unit[off:off + chunk] for off in range(0, total, chunk)]


ACK_DECODED, ACK_DISPLAYED, ACK_PHOTONS, ACK_POSE = 1, 2, 3, 4
ACK_EXTRA_BYTES = {ACK_PHOTONS: 8, ACK_POSE: 84}   # after the 9-byte kind+pts prefix


def parse_udp_ack(datagram):
    """(kind, pts) from an "XRA1" u8 kind u64 pts datagram, or None."""
    if len(datagram) >= 13 and datagram[:4] == b"XRA1":
        return struct.unpack(">BQ", datagram[4:13])
    return None


RECEIVER_MODES = {   # run-spec token -> Qualcomm c2.qti.avc.decoder.low_latency vendor parameter
    "fence": "vendor.qti-ext-output-sw-fence-enable.value=1",
    "early": "vendor.qti-ext-dec-early-notify.value=1",
    "slicedel": "vendor.qti-ext-dec-slice-delivery-mode.value=1",
}


def vr_extras(submit_margin=0, display_hz=0):
    """Launch extras for the OpenXR client: late-latch margin and a requested display refresh rate."""
    return ((["--ei", "submit_margin", str(submit_margin)] if submit_margin else [])
            + (["--ef", "display_hz", str(float(display_hz))] if display_hz else []))


def receiver_extras(modes):
    """(vendor keys string, slice_input) for the receiver's launch extras."""
    vendor = ",".join(RECEIVER_MODES[m] for m in modes if m in RECEIVER_MODES)
    return vendor, "slicedel" in modes


def slice_variant(modes):
    """slicev1 / slicev2 tokens select the receiver's slice-feeding convention (default 0)."""
    return next((int(m[-1]) for m in modes if m in ("slicev1", "slicev2")), 0)


def read_acks(stream, on_ack):
    """Receiver sends one record per event: u8 kind + u64 pts, where kind is 1 = decoded (handed to
    the display pipeline), 2 = displayed (MediaCodec's frame-rendered callback, flat panel). Kind 3
    (the VR client) adds an i64: nanoseconds from the ack until the runtime puts the frame on the
    display. Calls on_ack(kind, pts, perf_counter, ahead_ns)."""
    while True:
        try:
            data = stream.read(9)
            extra = ACK_EXTRA_BYTES.get(data[0], 0) if len(data) == 9 else 0
            if extra:
                data += stream.read(extra)
        except OSError:                                # connection closed at the end of a run
            return
        if len(data) < 9 + extra:
            return
        now = time.perf_counter()
        if data[0] == ACK_POSE:                        # head pose: meant for the driver, not for us
            continue
        if data[0] == ACK_PHOTONS:
            kind, pts, ahead = struct.unpack(">BQq", data)
            on_ack(kind, pts, now, ahead)
        else:
            kind, pts = struct.unpack(">BQ", data)
            on_ack(kind, pts, now, 0)


def ack_latency(sent, acks, skip_first=36):
    """Send -> decoded-on-headset latency per frame, entirely on the PC clock (includes the tiny
    ack return trip). skip_first drops the decoder warm-up frames."""
    keys = sorted(sent)[skip_first:]
    lat = sorted((acks[k] - sent[k]) * 1000 for k in keys if k in acks)
    if not lat:
        return {"frames": 0, "missing": len(keys)}
    pick = lambda q: lat[min(len(lat) - 1, int(len(lat) * q))]
    return {"frames": len(lat), "missing": len(keys) - len(lat), "p50_ms": statistics.median(lat),
            "p95_ms": pick(0.95), "max_ms": lat[-1], "min_ms": lat[0]}


def photon_latency(sent, acks, skip_first=36):
    """Send -> light on the headset display: the PC's own send-to-ack time plus the nanoseconds the VR
    runtime says remain before that frame is shown. acks maps pts -> (ack perf_counter, ahead_ns)."""
    keys = sorted(sent)[skip_first:]
    lat = sorted((acks[k][0] - sent[k]) * 1000 + acks[k][1] / 1e6 for k in keys if k in acks)
    if not lat:
        return {"count": 0, "missing": len(keys)}
    pick = lambda q: lat[min(len(lat) - 1, int(len(lat) * q))]
    return {"count": len(lat), "missing": len(keys) - len(lat), "p50_ms": statistics.median(lat),
            "p95_ms": pick(0.95), "max_ms": lat[-1], "min_ms": lat[0]}


def paced_schedule(frames, fps, count, t0, flood=False):
    """(send-by time, access unit) for `count` frames looping over a pre-encoded clip. Flood mode
    has no deadlines: it measures what the link and decoder can take."""
    for n in range(count):
        yield (None if flood else t0 + n / fps), frames[n % len(frames)]


def encode_clip(path, mbps, width=3264, height=1408, fps=72, seconds=6, coder="cavlc", slices=1, preset=1):
    if coder.startswith("mvhevc") or coder.startswith("hevc"):
        if not path.exists():
            result = subprocess.run(nvenc_clip_command(width, height, fps, mbps, fps * seconds, path, coder=coder,
                                                       preset=preset), capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(f"{coder} encode {mbps} Mbps failed: {(result.stdout + result.stderr).strip()}")
        with open(path, "rb") as handle:
            return list(length_records(handle))
    if not path.exists():
        result = subprocess.run(ffmpeg_command(width, height, fps, mbps, output=str(path), frames=fps * seconds,
                                               coder=coder, slices=slices),
                                capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"encode {mbps} Mbps failed: {result.stderr.strip().splitlines()[-1:]}")
    with open(path, "rb") as handle:
        return list(access_units(handle))


def run_once(label, transport, host, mbps, seconds, out_dir, width=3264, height=1408, fps=72, flood=False,
             coder="cavlc", slices=1, udp_chunk=1400, modes=(), preset=1, vr=False, submit_margin=0,
             display_hz=0):
    from . import sweep as s          # PC-only helpers (adb, eye timer)
    codec = codec_for_coder(coder)
    if codec != "avc" and transport == "udp":
        raise ValueError("the UDP path only carries AVC")
    name = clip_name(width, height, fps, mbps, coder, slices) if codec == "avc" else \
        clip_name(width, height, fps, mbps, coder).replace(".mvhevc", f"_p{preset}.mvhevc")
    clip = encode_clip(out_dir / name, mbps, width, height, fps, coder=coder, slices=slices, preset=preset)
    clip_mbps = sum(map(len, clip)) * 8 * fps / len(clip) / 1e6
    health = lambda: parse_health(s.adb("shell", "dumpsys", "thermalservice", check=False) or "",
                                  s.adb("shell", "dumpsys", "battery", check=False) or "")
    health_start = health()
    s.reset_eye_timer()
    receiver = receiver_package(vr)
    s.adb("shell", "am", "force-stop", receiver, check=False)
    s.adb("logcat", "-c", check=False)
    vendor, slice_input = receiver_extras(modes)
    extras = (["--es", "vendor", vendor] if vendor else []) + (["--ez", "slice_input", "true"] if slice_input else [])
    if slice_input:
        extras += ["--ei", "slice_variant", str(slice_variant(modes))]
    extras += vr_extras(submit_margin, display_hz) if vr else []
    s.adb("shell", "am", "start", "-n", f"{receiver}/.MainActivity", *extras, check=False)
    port = RECEIVER_PORT
    if transport == "adb":
        s.adb("forward", f"tcp:{port}", f"tcp:{port}")
        host = "127.0.0.1"
    time.sleep(4)
    sent_bytes = frames = 0
    block_s = late = 0.0
    sent_at, acked_at, displayed_at, photons_at = {}, {}, {}, {}

    def record_ack(kind, pts, t, ahead_ns=0):
        if kind == ACK_PHOTONS:
            photons_at[pts] = (t, ahead_ns)
        elif kind == ACK_DECODED:
            acked_at[pts] = t
        else:
            displayed_at[pts] = t
    try:
        if transport == "udp":
            conn = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8 << 20)
            conn.connect((host, UDP_PORT))
            for _ in range(5):
                conn.send(udp_hello(width, height, fps))
                time.sleep(0.1)
            time.sleep(1.0)                            # decoder start on the headset

            def udp_acks():
                while True:
                    try:
                        data = conn.recv(64)
                    except OSError:
                        return
                    ack = parse_udp_ack(data)
                    if ack is not None:
                        record_ack(ack[0], ack[1], time.perf_counter(), 0)
            threading.Thread(target=udp_acks, daemon=True).start()
            send_frame = lambda au, pts: [conn.send(g) for g in udp_chunks(au, pts, udp_chunk)]
        else:
            conn = socket.create_connection((host, port), timeout=10)
            conn.settimeout(None)                      # connect timeout only; acks may pause
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.sendall(stream_header(width, height, fps, codec))
            threading.Thread(target=read_acks, daemon=True, args=(conn.makefile("rb"), record_ack)).start()
            send_frame = lambda au, pts: conn.sendall(frame_record(au, pts))
        t0 = time.perf_counter()
        for deadline, au in paced_schedule(clip, fps, int(seconds * fps * (4 if flood else 1)), t0, flood):
            if deadline is not None:
                wait = deadline - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
                else:
                    late += -wait
            pts = frames * 1_000_000 // fps
            t = time.perf_counter()
            send_frame(au, pts)
            sent_at[pts] = time.perf_counter()         # last byte handed to the socket
            block_s += sent_at[pts] - t
            sent_bytes += len(au)
            frames += 1
            if time.perf_counter() - t0 >= seconds:
                break
        elapsed = time.perf_counter() - t0
        time.sleep(0.5)                                # let the last acks arrive
        conn.close()
    finally:
        if transport == "adb":
            s.adb("forward", "--remove", f"tcp:{port}", check=False)
    time.sleep(1.5)
    log_text = s.adb("logcat", "-d", "-s", "XRWired:I", "XRWiredXR:I", check=False)
    rows = parse_xrstat(log_text)
    drops = [int(m) for m in re.findall(r"dropped_frames=(\d+)", log_text)]
    result = {"label": label, "vr": vr, "submit_margin": submit_margin, "display_hz": display_hz,
              "transport": transport, "target_mbps": mbps, "clip_mbps": clip_mbps, "coder": coder,
              "eye": f"{width}x{height}", "preset": preset if codec != "avc" else None, "slices": slices, "udp_chunk": udp_chunk if transport == "udp" else None, "modes": list(modes),
              "flood": flood, "seconds": round(elapsed, 2), "sent_frames": frames,
              "sent_fps": frames / elapsed, "sent_mbps": sent_bytes * 8 / elapsed / 1e6,
              "send_block_ms_per_frame": block_s * 1000 / max(frames, 1), "late_ms_total": late * 1000,
              "send_to_decoded": ack_latency(sent_at, acked_at),
              "send_to_displayed": ack_latency(sent_at, displayed_at),
              "send_to_photons": photon_latency(sent_at, photons_at),
              "udp_dropped_frames": max(drops) if drops else None,
              "decoder_output_format": re.findall(r"Output: (.*)", log_text)[-1:],
              "xr_loop": re.findall(r"XRLOOP (.*)", log_text)[-1:],
              "decoder_vendor_params": sorted(set(re.findall(r"XRVENDOR (\S+ \S+ type=\S+)", log_text))),
              "wifi_low_latency_lock": "lock held=true" in log_text,
              "health_start": health_start, "health_end": health(),
              "headset": summarize_xrstat(rows), "xrstat_rows": rows}
    (out_dir / f"{label}.json").write_text(json.dumps(result, indent=2))
    return result


def parse_run_spec(spec, coder):
    """transport:mbps[:token...] -> run options. Tokens: flood, sN (slices), cBYTES (UDP chunk), receiver
    modes, mv / mv10 (MV-HEVC 8/10-bit, default 2560x1440 per eye), eWxH (eye/frame size), pN (NVENC preset)."""
    transport, mbps, *tokens = spec.split(":")
    mv = next((t for t in tokens if t in ("mv", "mv10")), None)
    coder = {"mv": "mvhevc", "mv10": "mvhevc10"}.get(mv, coder)
    size = next((t[1:] for t in tokens if re.fullmatch(r"e\d+x\d+", t)), None)
    width, height = map(int, size.split("x")) if size else ((2560, 1440) if mv else (3264, 1408))
    number = lambda prefix, default: next((int(t[len(prefix):]) for t in tokens
                                           if t.startswith(prefix) and t[len(prefix):].isdigit()), default)
    run = {"transport": transport, "mbps": int(mbps), "coder": coder, "width": width, "height": height,
           "vr": "vr" in tokens, "submit_margin": number("m", 0), "display_hz": number("hz", 0),
           "flood": "flood" in tokens, "slices": number("s", 1), "udp_chunk": number("c", 1400),
           "preset": number("p", 1), "modes": [t for t in tokens if t in RECEIVER_MODES or t in ("slicev1", "slicev2")]}
    run["label"] = (f"raw{'-vr' if run['vr'] else ''}-{transport}-{mbps}-{coder}" + (f"-e{size}" if size else "")
                    + (f"-m{run['submit_margin']}" if run["submit_margin"] else "")
                    + (f"-hz{run['display_hz']}" if run["display_hz"] else "")
                    + (f"-p{run['preset']}" if mv and run["preset"] != 1 else "")
                    + (f"-s{run['slices']}" if run["slices"] > 1 else "")
                    + (f"-c{run['udp_chunk']}" if transport == "udp" else "") + ("-flood" if run["flood"] else "")
                    + "".join(f"-{m}" for m in run["modes"]))
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wifi-ip", required=True)
    parser.add_argument("--runs", nargs="+", default=["wifi:400", "wifi:800", "adb:400", "adb:800"],
                        help="transport:mbps[:token...] (wifi | adb | udp; flood = unpaced; sN = N slices; "
                             "cBYTES = UDP chunk size; mv / mv10 = MV-HEVC 8/10-bit; eWxH = eye size; "
                             "pN = MV-HEVC NVENC preset; vr = our OpenXR client instead of the flat one; "
                             "fence / early / slicedel / slicev1 / slicev2)")
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--coder", default="cavlc", choices=("cavlc", "cabac"))
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for spec in args.runs:
        run = parse_run_spec(spec, args.coder)
        label = run.pop("label")
        try:
            r = run_once(label, run.pop("transport"), args.wifi_ip, run.pop("mbps"), args.seconds, out_dir, **run)
        except Exception as exc:
            r = {"label": label, "error": str(exc)}
        results.append(r)
        h = r.get("headset") or {}
        print(f"{label:22} clip {r.get('clip_mbps', 0):5.0f} | sent {r.get('sent_mbps', 0):6.0f} Mbps @ {r.get('sent_fps', 0):5.1f} fps | "
              f"headset in {h.get('in_mbps', 0):6.0f} Mbps out {h.get('out_fps', 0):5.1f} fps "
              f"decode p50 {h.get('dec_p50_median', 0):5.2f} ms | send->decoded p50 "
              f"{(r.get('send_to_decoded') or {}).get('p50_ms', 0):6.2f} p95 {(r.get('send_to_decoded') or {}).get('p95_ms', 0):6.2f} ms"
              f" | send->displayed p50 {(r.get('send_to_displayed') or {}).get('p50_ms', 0):6.2f}"
              f" p95 {(r.get('send_to_displayed') or {}).get('p95_ms', 0):6.2f}"
              f" | photons p50 {(r.get('send_to_photons') or {}).get('p50_ms', 0):6.2f}"
              f" p95 {(r.get('send_to_photons') or {}).get('p95_ms', 0):6.2f}"
              f" | {' '.join(r.get('xr_loop') or [])}"
              f" drops {r.get('udp_dropped_frames')} | health {(r.get('health_end') or {})} | "
              f"{r.get('error', '')}", flush=True)
    (out_dir / "summary.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
