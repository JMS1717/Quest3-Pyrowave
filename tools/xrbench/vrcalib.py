"""Calibration + benchmark sweep for the OpenXR runtime path (no SteamVR).

For each setting (bitrate x codec, 8-bit H.264 vs 10-bit HEVC), stream the calibration pattern through
our runtime to the headset, read back what the panel shows (receiver-xr's XRPIX probe), and record
frame rate, latency and image quality (gamma, banding, black crush). Then recommend low/med/high
presets from the viable runs. The measurement analysis is in vrpattern.py.

Orchestrated from the Mac: the PC side (runtime cfg, the calib app) over ssh to the PC, the
headset (receiver-xr, logcat) over adb. The pure logic here (parsing, preset selection, recommend) is
unit-tested; main() wires it to ssh/adb.
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import time
from pathlib import Path

from . import vrpattern as vp

# The sweep grid. 8-bit H.264 is the low-latency baseline; 10-bit HEVC targets banding/black-crush.
DEFAULT_SWEEP = [
    {"name": "h264-200", "mbps": 200, "codec": "h264", "eye_w": 1424, "eye_h": 1664},
    {"name": "h264-400", "mbps": 400, "codec": "h264", "eye_w": 1424, "eye_h": 1664},
    {"name": "h264-600", "mbps": 600, "codec": "h264", "eye_w": 1424, "eye_h": 1664},
    {"name": "hevc10-300", "mbps": 300, "codec": "hevc10", "eye_w": 1424, "eye_h": 1664},
    {"name": "hevc10-500", "mbps": 500, "codec": "hevc10", "eye_w": 1424, "eye_h": 1664},
    {"name": "hevc10-800", "mbps": 800, "codec": "hevc10", "eye_w": 1424, "eye_h": 1664},
]

# Viability gates: a preset must sustain the frame rate and stay under the latency budget with no drops.
MIN_FPS = 70.0
MAX_PHOTON_MS = 50.0


def headset_fps(logcat_text):
    """Median out_fps from receiver-xr XRSTAT lines."""
    vals = [float(m) for m in re.findall(r"out_fps=([\d.]+)", logcat_text)]
    return round(statistics.median(vals), 1) if vals else 0.0


def headset_mbps(logcat_text):
    """Median actual in_mbps the headset received (did the encoder reach the target bitrate?)."""
    vals = [float(m) for m in re.findall(r"in_mbps=([\d.]+)", logcat_text)]
    return round(statistics.median(vals), 0) if vals else 0.0


def runtime_latency(runtime_log_text):
    """Last encode/decoded/photon milliseconds from the runtime's periodic 'frames=' log lines."""
    out = {"encode_ms": None, "decoded_ms": None, "photon_ms": None}
    enc = re.findall(r"encode=([\d.]+)", runtime_log_text)
    dec = re.findall(r"decoded_ms=([\d.]+)", runtime_log_text)
    pho = re.findall(r"photons_ms=([\d.]+)", runtime_log_text)
    if enc:
        out["encode_ms"] = float(enc[-1])
    if dec:
        out["decoded_ms"] = float(dec[-1])
    if pho:
        out["photon_ms"] = float(pho[-1])
    return out


def cfg_lines(preset):
    """xrwired.cfg body for a preset (headset_ip/port/fps come from the base config the caller keeps)."""
    return "\n".join(f"{k}={preset[k]}" for k in ("mbps", "codec", "eye_w", "eye_h") if k in preset)


def quality_score(r):
    """Higher is better image quality: more surviving grey levels, less black crush, 10-bit bonus."""
    return r["banding_levels"] - 2 * (r.get("crush_floor") or 0) + (60 if "10" in r["codec"] else 0)


def latency_ms(r):
    """Prefer measured photon latency; fall back to send->decoded when photon acks were absent."""
    return r.get("photon_ms") or r.get("decoded_ms") or 1e9


def viable(r):
    gamma_ok = r.get("gamma") is None or 0.9 <= r["gamma"] <= 1.1   # perf runs carry no colour
    return (r["fps"] >= MIN_FPS and latency_ms(r) <= MAX_PHOTON_MS and r.get("dropped", 0) == 0 and gamma_ok)


def recommend(results):
    """Pick low/med/high presets from the viable runs: low = least bitrate, high = best quality,
    med = the middle-bitrate viable run."""
    good = [r for r in results if viable(r)]
    if not good:
        return {}
    by_bitrate = sorted(good, key=lambda r: r["mbps"])
    low = by_bitrate[0]
    high = max(good, key=quality_score)
    med = by_bitrate[len(by_bitrate) // 2]
    return {"low": low, "med": med, "high": high}


# ---- orchestration (ssh + adb); not unit-tested ------------------------------------------------

# From the environment: XRBENCH_PC_SSH (user@host of the PC, key auth), XRBENCH_PC_RUNTIME (the
# runtime folder on the PC, absolute, e.g. <workspace>\runtime) and XRBENCH_HEADSET_IP (optional;
# --headset-ip otherwise). main() refuses to run without the PC.
PC_SSH = os.environ.get("XRBENCH_PC_SSH", "")
SSH = ["ssh", "-q", "-o", "BatchMode=yes", "-i", str(Path.home() / ".ssh/id_rsa"), PC_SSH]
RUNTIME = os.environ.get("XRBENCH_PC_RUNTIME", "")
CFG = RUNTIME + r"\xrwired.cfg"
LOG = RUNTIME + r"\xrwired_runtime.log"
PATTERN = RUNTIME + r"\calib_pattern.raw"
HEADSET_IP = os.environ.get("XRBENCH_HEADSET_IP")


def _ssh(cmd):
    return subprocess.run(SSH + [cmd], capture_output=True, text=True).stdout


def _adb(adb, *args):
    return subprocess.run([adb, *args], capture_output=True, text=True).stdout


def run_preset(preset, adb, base_cfg, seconds, out_dir, mode="calib"):
    # 1. write cfg = base (ip/port/fps/ipd) + this preset's lines, via a scp'd file (robust)
    body = base_cfg.strip() + "\n" + cfg_lines(preset) + "\n"
    tmp = out_dir / "xrwired.cfg"
    tmp.write_text(body)
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-i", str(Path.home() / ".ssh/id_rsa"),
                    str(tmp), f"{PC_SSH}:{CFG.replace(chr(92), '/')}"], capture_output=True)
    ten_bit = preset["codec"] == "hevc10"
    # 2. restart receiver-xr with the pixel probe (and 10-bit swapchain when needed)
    _adb(adb, "shell", "am", "force-stop", "com.xrwired.receiverxr")
    _adb(adb, "logcat", "-c")
    _adb(adb, "shell", "input", "keyevent", "KEYCODE_WAKEUP")
    extras = ["--ei", "submit_margin", "4", "--ef", "display_hz", "72"]
    if mode == "calib":
        extras += ["--ez", "pixel_probe", "true"]   # perf mode leaves the probe off (readback stalls)
    if ten_bit:
        extras += ["--ez", "ten_bit", "true"]
    _adb(adb, "shell", "am", "start", "-n", "com.xrwired.receiverxr/.MainActivity", *extras)
    time.sleep(4)
    # 3. run the calib app through the runtime for `seconds`
    _ssh(f'del {LOG} 2>nul')
    frames = int(seconds * preset.get("fps", 72))
    # calib = static colour pattern; perf = the animated 3D room (self-moving cubes give the encoder
    # realistic, continuously-changing content without the wearer having to move their head).
    content = f"--calib {PATTERN}" if mode == "calib" else ""
    app_out = _ssh(f'cd /d {RUNTIME} && build\\xrw_room.exe xrwired_runtime.dll {content} --frames {frames}')
    pc_fps_vals = [float(m) for m in re.findall(r"([\d.]+) fps", app_out)]
    pc_fps = round(statistics.median(pc_fps_vals), 1) if pc_fps_vals else 0.0
    time.sleep(1)
    # 4. collect
    logcat = _adb(adb, "logcat", "-d", "-s", "XRWiredXR:I")
    runtime_log = _ssh(f'type {LOG}')
    if mode == "calib":
        samples = vp.parse_probe(logcat)
        depth = vp.probe_depth(logcat)
        cal = vp.calibration_report(samples, preset["eye_w"], preset["eye_h"], depth=depth,
                                    transfer="linear" if depth == 10 else "srgb")
    else:
        samples, depth = [], 10 if ten_bit else 8
        cal = {"gamma": None, "max_patch_error": None, "mean_patch_error": None, "banding_levels": None,
               "banding_max_step": None, "crush_floor": None, "dark_levels": None}
    lat = runtime_latency(runtime_log)
    drops = [int(m) for m in re.findall(r"dropped_frames=(\d+)", logcat)]
    result = {"name": preset["name"], "mbps": preset["mbps"], "codec": preset["codec"],
              "fps": headset_fps(logcat), "encode_ms": lat["encode_ms"], "decoded_ms": lat["decoded_ms"],
              "photon_ms": lat["photon_ms"], "pc_fps": pc_fps, "in_mbps": headset_mbps(logcat),
              "depth": depth, "dropped": max(drops) if drops else 0, **cal}
    (out_dir / f"{preset['name']}.json").write_text(json.dumps({"result": result, "samples": len(samples)},
                                                              indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--adb", required=True, help="path to adb (the headset on USB or wireless)")
    parser.add_argument("--mode", choices=("calib", "perf"), default="calib",
                        help="calib = colour/banding/black-crush (static pattern); perf = fps/latency "
                             "in the animated 3D room (self-moving content)")
    parser.add_argument("--seconds", type=float, default=8)
    parser.add_argument("--out", required=True)
    parser.add_argument("--headset-ip", default=HEADSET_IP, required=HEADSET_IP is None,
                        help="the headset's Wi-Fi address (default: XRBENCH_HEADSET_IP)")
    parser.add_argument("--port", type=int, default=45100)
    args = parser.parse_args()
    if not (PC_SSH and RUNTIME):
        parser.error("set XRBENCH_PC_SSH (user@host of the PC) and XRBENCH_PC_RUNTIME (its runtime folder)")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "calib":
        eye_w = max(p["eye_w"] for p in DEFAULT_SWEEP)
        eye_h = max(p["eye_h"] for p in DEFAULT_SWEEP)
        local_pattern = out_dir / "calib_pattern.raw"
        vp.write_raw(local_pattern, eye_w, eye_h)
        subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-i", str(Path.home() / ".ssh/id_rsa"),
                        str(local_pattern), f"{PC_SSH}:{PATTERN.replace(chr(92), '/')}"])

    base_cfg = f"headset_ip={args.headset_ip}\nport={args.port}\nfps=72\nipd=0.063"
    results = []
    for preset in DEFAULT_SWEEP:
        r = run_preset(preset, args.adb, base_cfg, args.seconds, out_dir, mode=args.mode)
        results.append(r)
        if args.mode == "perf":
            print(f"{r['name']:12} fps {r['fps']:4.0f}/{r['pc_fps']:4.0f} | dec {r['decoded_ms'] or 0:4.1f} "
                  f"pho {r['photon_ms'] or 0:5.1f}ms | got {r['in_mbps']:4.0f}/{r['mbps']} Mbps | "
                  f"drops {r['dropped']} | {r['depth']}bit", flush=True)
        else:
            print(f"{r['name']:12} fps {r['fps']:4.0f}/{r['pc_fps']:4.0f} | gamma {r['gamma']:.2f} "
                  f"patch {r['max_patch_error']} | band {r['banding_levels']:4d} crush {r['crush_floor']} "
                  f"dark {r['dark_levels']} | {r['depth']}bit", flush=True)
    (out_dir / "results.json").write_text(json.dumps(results, indent=2))
    rec = recommend(results)
    print("\nrecommended presets:")
    for tier in ("low", "med", "high"):
        if tier in rec:
            p = rec[tier]
            print(f"  {tier:4}: {p['name']} ({p['codec']} {p['mbps']} Mbps) "
                  f"fps {p['fps']} photon {p['photon_ms']}ms banding {p['banding_levels']} crush {p['crush_floor']}")
    (out_dir / "presets.json").write_text(json.dumps(rec, indent=2))


if __name__ == "__main__":
    main()
