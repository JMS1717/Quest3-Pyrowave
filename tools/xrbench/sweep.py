"""Unattended benchmark sweep on the PC: ALVR 20.13 configurations, then Virtual Desktop.

  python -m xrbench.sweep --client-ip <headset wifi ip> --client-host <alvr hostname> [--only W1-400]
  python -m xrbench.sweep --dry-run          # print the plan and timing, touch nothing

Per segment: configure -> restart SteamVR -> confirm Wi-Fi streaming -> settle -> test scene with
headset screen recording + lossless stills + ALVR telemetry -> SteamVR Home load -> cool-down.
Output: <out>/<label>/ (meta.json, frames.csv, clip.mp4, still_*.png, telemetry/, logcat.txt).
"""
import argparse
import csv
import datetime as dt
import io
import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import gazelog
from . import matrix as matrixmod
from . import paths
from . import plan as planmod
from . import battery as batterymod
from . import thermal as thermalmod
from . import wireless as wirelessmod

# Code (this package and the scripts beside it) comes from CODE; the ALVR install, adb, backups,
# runs and corpus live under the runtime root HERE (see paths.py for how it is found).
CODE = paths.CODE
HERE = paths.ROOT
ADB = HERE / r"android-tools\platform-tools\adb.exe"
ALVR_ROOT = HERE / "ALVR-20.13.0"
ALVR_DASHBOARD = ALVR_ROOT / "ALVR Dashboard.exe"
ALVR_PACKAGE = "alvr.client.galaxy2013"
VD_PACKAGE = "virtualdesktop.android"
VD_STREAMER = Path(r"C:\Program Files\Virtual Desktop Streamer\VirtualDesktop.Streamer.exe")
STEAMVR_SETTINGS = Path(r"C:\Program Files (x86)\Steam\config\steamvr.vrsettings")
API = "http://127.0.0.1:8082/api/dashboard-request"
CLIP_SECONDS = 18
STILLS_IN_SCENE = 2
STILLS_IN_GAME = 2
CAPTURE_FPS = 36          # headset screenrecord samples every 2nd display frame (measured)
SERIAL = os.environ.get("XRBENCH_SERIAL", "")   # empty: first physical device adb sees


def log(msg):
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def adb_devices():
    out = subprocess.run([str(ADB), "devices"], capture_output=True, text=True, timeout=30).stdout
    return wirelessmod.parse_devices(out)


def connect_wireless(ip, attempts=3):
    """Attach the headset over Wi-Fi so the sweep needs no USB cable. True if it is reachable.

    Safe to call when already connected -- `adb connect` is idempotent."""
    if not ip:
        return False
    dest = wirelessmod.target(ip)
    for attempt in range(1, attempts + 1):
        subprocess.run([str(ADB), "connect", dest], capture_output=True, text=True, timeout=30)
        if dest in adb_devices():
            return True
        if attempt < attempts:
            time.sleep(2)
    return False


def device_serial():
    """XRBENCH_SERIAL, else a device adb reports -- wireless preferred over the cable.

    Preference matters rather than being a nicety: when both are attached adb may list either
    first, and silently taking the USB one is what once drained the headset."""
    global SERIAL
    if not SERIAL:
        chosen = wirelessmod.prefer_wireless(adb_devices())
        if not chosen:
            raise RuntimeError("no headset visible to adb")
        SERIAL = chosen
    return SERIAL


def reconnect_if_dropped():
    """Re-attach a wireless headset that has fallen out of `adb devices`, and report whether it is
    back. Wi-Fi adb drops on sleep or a roam, and a sweep that keeps going blind fails every
    remaining segment with no client."""
    global SERIAL
    if SERIAL and SERIAL in adb_devices():
        return True
    if SERIAL and wirelessmod.is_wireless(SERIAL):
        log(f"adb lost {SERIAL}, reconnecting")
        if connect_wireless(SERIAL):
            return True
    SERIAL = ""
    try:
        device_serial()
        return True
    except RuntimeError:
        return False


def adb(*args, check=True, capture=True, timeout=120):
    result = subprocess.run([str(ADB), "-s", device_serial(), *args], capture_output=capture, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: {result.stderr.decode(errors='replace')}")
    return result.stdout.decode(errors="replace") if capture else ""


def powershell(script, *args, timeout=300):
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"{script.name} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def alvr_request(body):
    import urllib.request
    data = json.dumps(body).encode()
    req = urllib.request.Request(API, data=data, headers={"X-ALVR": "true", "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


STEAMVR_PROCESSES = ("vrmonitor", "vrserver", "vrcompositor", "vrdashboard", "vrwebhelper",
                     "vrstartup", "steamtours")


def api_up():
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:8082/api/version", headers={"X-ALVR": "true"})
        urllib.request.urlopen(req, timeout=3).read()
        return True
    except Exception:
        return False


def port_in_use(port=8082):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True).stdout
    return any(f":{port} " in line and "LISTENING" in line for line in out.splitlines())


def edit_steamvr_settings(section, values):
    settings = json.loads(STEAMVR_SETTINGS.read_text(encoding="utf-8-sig"))
    settings.setdefault(section, {}).update(values)
    STEAMVR_SETTINGS.write_text(json.dumps(settings, indent=3))


LOCKED_RENDER_SCALE = {"supersampleManualOverride": True, "supersampleScale": 1.0}

# Applied by force_render_scale(), which runs inside restart_steamvr() -- so like the env vars,
# this is set before a segment's restart rather than during the run.
CURRENT_RENDER_SCALE = 1.0


def set_render_scale(scale):
    global CURRENT_RENDER_SCALE
    CURRENT_RENDER_SCALE = float(scale)


def force_render_scale(scale=None):
    """Pin SteamVR's render resolution. Only called while SteamVR is stopped; the original file is
    restored at the end of the sweep.

    1.0 means the app renders at exactly the resolution the streamer encodes, with no
    supersample-then-downscale resample softening the test pattern -- the right default for
    measurement. Above 1.0 the app renders higher and SteamVR downsamples into the encode
    resolution, which costs GPU on the host but *not* a single encoded pixel, so it cannot move
    decode time. That is the Giant Screen supersampling experiment: buy antialiasing, thin
    geometry and text stability from the RTX 3090's spare headroom rather than from the Galaxy
    XR's scarce decode budget."""
    if scale is None:
        scale = CURRENT_RENDER_SCALE
    edit_steamvr_settings("steamvr", dict(LOCKED_RENDER_SCALE, supersampleScale=float(scale)))
    # The dashboard opens after every SteamVR start, covers the panel and dims the scene behind it
    # (scaling every code value); OpenVR has no call to close it, so disable it for the benchmark.
    edit_steamvr_settings("dashboard", {"enableDashboard": False})


def is_valid_json(path):
    try:
        json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return True
    except (OSError, ValueError):
        return False


def newest_valid_json(paths):
    valid = [p for p in paths if is_valid_json(p)]
    return max(valid, key=lambda p: Path(p).stat().st_mtime) if valid else None


def stop_steamvr(grace_seconds=12):
    """Ask ALVR to quit SteamVR cleanly, then force-kill leftovers. A bare taskkill can land while
    ALVR is rewriting session.json (it saves right after a client connects), leaving an empty file;
    the next start then silently runs on default settings with the headset untrusted."""
    if api_up():
        try:
            alvr_request("ShutdownSteamvr")
        except Exception:
            pass
        deadline = time.time() + grace_seconds
        while time.time() < deadline and "vrserver.exe" in subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq vrserver.exe"], capture_output=True, text=True).stdout:
            time.sleep(1)
    for name in STEAMVR_PROCESSES:
        subprocess.run(["taskkill", "/F", "/IM", f"{name}.exe"], capture_output=True)


SWEEP_MARKERS = ("xrbench.sweep", r"xrbench\sweep.py", "xrbench/sweep.py")

# Flagged in the per-run process record, never stopped: the owner's rule is that PC
# apps are normal and stay; the headset is the measured device and must be clean. The flag is so
# a strange game_time can be judged against what the PC was doing at the time.
NONESSENTIAL_PC_PROCESSES = (
    "EpicGamesLauncher.exe", "EOSOverlayRenderer-Win64-Shipping.exe", "EpicWebHelper.exe",
    "GlassWire.exe", "GWCtlSrv.exe",
    "PAD.Console.Host.exe", "PAD.Robot.exe",
    "Discord.exe", "chrome.exe", "msedge.exe", "firefox.exe",
    "OneDrive.exe", "Spotify.exe",
)

# Samsung and Qualcomm ship services under several prefixes; seen on this headset:
# com.quicinc (voice), com.skms (Samsung KMS agent), com.wssyncmldm (Samsung FOTA).
PLATFORM_PACKAGE_PREFIXES = ("com.android", "com.google", "com.samsung", "com.qualcomm", "com.sec",
                             "com.qti", "android", "com.osp", "com.quicinc", "com.skms", "com.wssyncmldm")


def third_party_packages(ps_output):
    """Package names in `ps -A -o NAME` output that are not the platform's: what to stop.

    Anything reverse-domain that is not Android, Google, Samsung or Qualcomm. The client under
    test is included on purpose -- it is relaunched fresh by the segment anyway."""
    found = set()
    for line in ps_output.splitlines():
        name = line.strip()
        if not name or name == "NAME" or "." not in name:
            continue
        if name.startswith(PLATFORM_PACKAGE_PREFIXES):
            continue
        if name.startswith(("com.", "org.", "io.", "net.", "dev.", "app.", "alvr")):
            found.add(name)
    return sorted(found)


# Known packages, stopped even if the ps listing is unavailable.
HEADSET_STOP_PACKAGES = (
    "alvr.client.galaxy2013", "alvr.client.dev", "alvr.client.stable", "alvr.client.stabletest",
    "virtualdesktop.android",
    "com.valvesoftware.steamlink", "com.valvesoftware.steamlinkvr",
    "com.xrwired.receiver", "com.xrwired.receiverxr",
)


def nonessential_pids(process_rows):
    """PIDs of known non-essential PC processes, by image name, case-insensitive."""
    names = {n.lower() for n in NONESSENTIAL_PC_PROCESSES}
    return sorted(row["pid"] for row in process_rows if (row.get("name") or "").lower() in names)


def process_report(process_rows, cpu_seconds):
    """One line per process, busiest first, flagging the known non-essentials.

    Written into every run directory so a strange number can be judged against what else the
    machine was doing at the time, instead of guessed at afterwards."""
    names = {n.lower() for n in NONESSENTIAL_PC_PROCESSES}
    rows = sorted(process_rows, key=lambda r: -cpu_seconds.get(r["pid"], 0.0))
    lines = []
    for r in rows:
        name = r.get("name") or "?"
        flag = "  nonessential" if name.lower() in names else ""
        lines.append(f"{name:<40} pid {r['pid']:>6}  cpu {cpu_seconds.get(r['pid'], 0.0):8.1f} s{flag}")
    return "\n".join(lines)


def stray_sweep_pids(process_rows, own_pid):
    """PIDs of other sweeps already running on this machine.

    Matching needs both the name and the command line. The command line alone catches the cmd.exe
    that ssh wraps us in, which quotes our own invocation verbatim -- so every remote run would
    refuse to start. The name alone catches every unrelated Python on the box.
    """
    found = [row["pid"] for row in process_rows
             if "python" in (row.get("name") or "").lower()
             and row.get("pid") != own_pid
             and any(marker in (row.get("cmdline") or "") for marker in SWEEP_MARKERS)]
    return sorted(found)


def running_processes():
    """(pid, name, cmdline) for every visible process, or [] if the query fails.

    `tasklist` cannot show command lines and `wmic` is gone from current Windows; CIM is what
    survives both. CommandLine comes back empty for processes we cannot open, which is normal and
    must not be an error -- a preflight that raises here would block every run.
    """
    script = ("Get-CimInstance Win32_Process | Select-Object ProcessId,Name,CommandLine,UserModeTime,KernelModeTime | "
              "ConvertTo-Csv -NoTypeInformation")
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    rows = []
    for row in csv.DictReader(io.StringIO(result.stdout)):
        try:
            pid = int(row.get("ProcessId") or 0)
        except ValueError:
            continue
        try:
            cpu = (int(row.get("UserModeTime") or 0) + int(row.get("KernelModeTime") or 0)) / 1e7
        except ValueError:
            cpu = 0.0
        rows.append({"pid": pid, "name": row.get("Name"), "cmdline": row.get("CommandLine"), "cpu_s": cpu})
    return rows


def preflight_clean(client_ip=None, out_root=None):
    """Put the PC and the headset in a known-idle state before the first segment.

    Added at the owner's instruction, after a sweep died mid-run without tearing down,
    two more were started over the top of it, and the headset sat decoding three ALVR streams at
    once until someone found it cooking. Every guard in this harness until now was per-segment; nothing
    ever asked what was already running when a run began.

    Refusing on a live sweep rather than killing it is deliberate. Two sweeps fight over
    session.json, steamvr.vrsettings and port 8082, so whichever survives produces measurements
    taken under settings it did not write -- worse than no data, because it looks like data.
    """
    strays = stray_sweep_pids(running_processes(), os.getpid())
    if strays:
        raise RuntimeError(
            f"another xrbench sweep is already running (pid {', '.join(str(p) for p in strays)}). "
            f"Two sweeps share session.json, steamvr.vrsettings and port 8082, so both produce "
            f"junk. Stop it first, or wait for it to finish.")

    log("preflight: clearing anything left running")
    stop_steamvr()
    for name in ("ALVR Dashboard.exe", "VirtualDesktop.Streamer.exe"):
        subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True)
    rows = running_processes()
    if out_root is not None:
        Path(out_root).mkdir(parents=True, exist_ok=True)
        (Path(out_root) / "pc_processes.txt").write_text(
            process_report(rows, {r["pid"]: r.get("cpu_s", 0.0) for r in rows}))
    try:
        if client_ip:
            connect_wireless(client_ip)
        if adb_devices():
            running = third_party_packages(adb("shell", "ps -A -o NAME", check=False))
            if out_root is not None:
                (Path(out_root) / "headset_processes.txt").write_text("\n".join(running) + "\n")
            for package in sorted(set(running) | set(HEADSET_STOP_PACKAGES)):
                adb("shell", "am", "force-stop", package, check=False)
            if running:
                log(f"preflight: stopped on the headset: {', '.join(running)}")
            cpu_c = thermal_zones()["cpu_max_c"]
            log("preflight: headset clients stopped, cpu "
                + (f"{cpu_c:.1f} C" if cpu_c is not None else "unreadable"))
        else:
            log("preflight: no headset over adb yet; PC side cleared")
    except Exception as exc:  # a headset that is asleep or unplugged must not block the PC cleanup
        log(f"preflight: headset cleanup skipped ({exc})")


def ensure_session_file():
    session_path = ALVR_ROOT / "session.json"
    if is_valid_json(session_path):
        return
    backup = newest_valid_json(sorted((HERE / "backups").glob("alvr2013-session-*.json")))
    if backup is None:
        raise RuntimeError("session.json is corrupt and no valid backup exists")
    shutil.copy2(backup, session_path)
    log(f"session.json was corrupt; restored {backup.name}")


ZONE_DUMP = ('for d in /sys/class/thermal/thermal_zone*; '
             'do echo "$(cat $d/temp) $(cat $d/type)"; done')


def thermal_zones():
    """Per-family headset temperatures in C, or an empty summary if the read fails.

    `thermal_status()`'s 0-3 severity is too coarse to compare segments: it cannot say whether a
    run started warm or how far it climbed. Measured on this headset the CPU runs ~6 C hotter than
    the video block, so the decoder is not what throttles -- which is only visible in degrees."""
    try:
        return thermalmod.parse_zones(adb("shell", ZONE_DUMP, check=False))
    except Exception as exc:
        log(f"thermal zones unavailable: {exc}")
        return thermalmod.parse_zones("")


def warn_if_wired():
    """Say so, once per run, if the headset is on the USB-C cable rather than wireless adb."""
    try:
        warning = batterymod.wired_adb_warning(device_serial())
    except Exception:
        return
    if warning:
        log(f"WARNING: {warning}")


def battery_state():
    """Current battery level, charge status and temperature, or empty fields if unavailable."""
    try:
        return batterymod.parse(adb("shell", "dumpsys", "battery", check=False))
    except Exception as exc:
        log(f"battery state unavailable: {exc}")
        return batterymod.parse("")


def check_battery(state):
    """Log the battery position before a segment, and refuse to start one that cannot finish.

    Raises below `ABORT_BELOW_PCT`. A headset that dies mid-segment costs the whole run rather
    than one cell, and once it died at 12:12 and took four sweeps down with it."""
    level = state.get("level")
    if level is None:
        return
    note = "charging" if state.get("charging") else "on battery"
    log(f"battery {level}% ({note}, {state.get('temperature_c')} C)")
    if level < batterymod.ABORT_BELOW_PCT:
        raise RuntimeError(
            f"battery {level}% is below the {batterymod.ABORT_BELOW_PCT}% floor; "
            "charge the headset before continuing")


def crash_log_size():
    """Byte length of ALVR's crash_log.txt, or 0 if absent.

    crash_log.txt accumulates across every segment of a sweep, so a per-segment view needs the
    length before the segment and the delta after. Byte offset rather than timestamp because the
    log only carries HH:MM:SS, which is ambiguous across midnight and across a restart."""
    crash = ALVR_ROOT / "crash_log.txt"
    return crash.stat().st_size if crash.exists() else 0


def save_gaze_stats(out_dir, offset):
    """Write this segment's slice of crash_log.txt, plus a parsed summary, into the segment dir.

    The summary is the objective half of the foveation question: how far the centre had to travel,
    how stale the gaze was, and how often the shift sat at the clamp unable to follow the eye any
    further. Best-effort -- a missing or truncated log must never fail a segment that otherwise
    streamed fine."""
    try:
        crash = ALVR_ROOT / "crash_log.txt"
        if not crash.exists():
            return
        with crash.open("rb") as handle:
            handle.seek(min(offset, crash.stat().st_size))
            text = handle.read().decode("utf-8", errors="replace")
        stats = gazelog.parse(text)
        (out_dir / "gaze_log.txt").write_text(text, errors="replace")
        (out_dir / "gaze_stats.json").write_text(json.dumps(stats, indent=2))
        # An unworn headset still reports gaze -- plausible angles, non-null poses -- it just never
        # moves. Say so loudly: the segment's numbers are real but the run is worthless, and that
        # is not visible from fps or decode time.
        if stats["likely_worn"] is False:
            log(f"WARNING: gaze never moved (yaw span {stats['yaw_span']:.1f} deg, "
                f"peak staleness {stats['staleness_max']:.1f} deg) -- headset was almost "
                f"certainly not being worn, so this segment does not count")
        elif stats["likely_worn"] is None and stats["gaze_samples"] == 0:
            log("WARNING: no gaze reached the server for this segment")
    except Exception as exc:
        log(f"gaze stats unavailable: {exc}")


def crash_log_mark():
    """Where ALVR's crash_log.txt ends now; alvr_parse_errors_since reads only what follows."""
    crash = ALVR_ROOT / "crash_log.txt"
    return crash.stat().st_size if crash.exists() else 0


def alvr_parse_errors_since(mark):
    """Session-parse errors ALVR logged after `mark` (a crash_log_mark). The log accumulates across
    days and stamps lines with the time of day only, so comparing times let an error from a
    previous evening fail healthy cells; reading only the appended bytes cannot."""
    crash = ALVR_ROOT / "crash_log.txt"
    if not crash.exists():
        return []
    with open(crash, "rb") as f:
        size = f.seek(0, 2)
        f.seek(mark if mark <= size else 0)       # a shorter file was rotated: read it all
        text = f.read().decode(errors="replace")
    return [l for l in text.splitlines() if "Error on parsing session config" in l]


def steamvr_task_for(codec):
    """The scheduled task that launches SteamVR for this codec. PyroWave itself comes from the
    session now; its launcher arms the runtime dump trigger and clears the bitstream tap and the
    encoder dump, so research captures stay opt-in per segment."""
    return "XRWiredSteamVRPyroClean" if codec == "PyroWave" else "XRWiredSteamVR"


def restart_steamvr(timeout=90, task="XRWiredSteamVR"):
    """Headless SteamVR restart. ALVR's RestartSteamvr request needs the Dashboard window, and a
    fast relaunch can collide with the old instance on port 8082 (the ALVR API then panics while
    streaming carries on), so stop cleanly, wait for the port to free, check session.json,
    relaunch, wait for the API, and refuse to continue if ALVR could not read its session."""
    stop_steamvr()
    deadline = time.time() + 30
    while port_in_use() and time.time() < deadline:
        time.sleep(1)
    if port_in_use():
        raise RuntimeError("port 8082 still in use 30 s after stopping SteamVR")
    force_render_scale()
    ensure_session_file()
    mark = crash_log_mark()
    subprocess.run(["powershell", "-NoProfile", "-Command", f"Start-ScheduledTask {task}"], check=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if api_up():
            errors = alvr_parse_errors_since(mark)
            if errors:
                raise RuntimeError(f"ALVR started without its session (running on defaults): {errors[-1]}")
            return
        time.sleep(2)
    raise RuntimeError(f"ALVR API not up {timeout}s after starting SteamVR")


class AwakeWatchdog(threading.Thread):
    """The Galaxy XR sometimes sleeps (xr_doff) while worn; wake it whenever that happens."""

    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.wakes = 0

    def run(self):
        while not self.stop.wait(4):
            try:
                if "mWakefulness=Asleep" in adb("shell", "dumpsys power | grep mWakefulness=", check=False):
                    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP", check=False)
                    self.wakes += 1
                    log("watchdog: headset was asleep, sent wake")
            except Exception as exc:  # never let the watchdog kill the sweep
                log(f"watchdog error: {exc}")


TAP_ENV = "ALVR_BITSTREAM_TAP"


def tap_base_path(out_dir, label):
    """Where the bitstream tap writes for one segment. The tap appends its own
    `-<timestamp>.h264` / `.idx` per stream session, so this is a prefix, not a filename."""
    return Path(out_dir) / label / "tap"


GAZE_ENV = "ALVR_GAZE_FOVEATION"
HEADROOM_ENV = "ALVR_PACING_HEADROOM_US"
DELAY_ENV = "ALVR_PACING_DELAY_US"
PHASE_ENV = "ALVR_PHASE_LOCK"


def set_phase_lock_env(enabled):
    """Phase-lock the server's virtual vsync to the headset, or leave it free-running.

    Same timing constraint as the pacing switches: read once per driver process at start."""
    if sys.platform != "win32":
        return
    value = repr("1") if enabled else "$null"
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"[Environment]::SetEnvironmentVariable('{PHASE_ENV}',{value},'User')"],
                   capture_output=True)


def set_pacing_delay_env(micros):
    """Release SteamVR this many microseconds after the virtual vsync, or 0 for stock behaviour.

    Same timing constraint as the headroom switch: `PACING_DELAY` is a `Lazy<Duration>` read once
    per driver process, so it must be set before SteamVR starts."""
    if sys.platform != "win32":
        return
    value = "$null" if not micros else repr(str(int(micros)))
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"[Environment]::SetEnvironmentVariable('{DELAY_ENV}',{value},'User')"],
                   capture_output=True)


def set_pacing_headroom_env(micros):
    """Release SteamVR this many microseconds before the virtual vsync, or 0 for stock behaviour.

    Same mechanism and same timing constraint as the gaze switch: `PACING_HEADROOM` in the patched
    server is a `Lazy<Duration>` read once per driver process, so it must be set before SteamVR
    starts rather than during a run."""
    if sys.platform != "win32":
        return
    value = "$null" if not micros else repr(str(int(micros)))
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"[Environment]::SetEnvironmentVariable('{HEADROOM_ENV}',{value},'User')"],
                   capture_output=True)


def set_gaze_env(enabled):
    """Switch gaze-driven foveation off for a segment, or back on.

    Same mechanism and same timing constraint as the tap: `GAZE_FOVEATION_ENABLED` in the patched
    server is a `Lazy<bool>` read once per driver process, so this must be set before SteamVR
    starts, not during a run. "0" pins the centre to the static configured value, which is the
    gaze-off arm of the 2e A/B."""
    if sys.platform != "win32":
        return
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"[Environment]::SetEnvironmentVariable('{GAZE_ENV}',"
                    f"{'$null' if enabled else repr('0')},'User')"],
                   capture_output=True)


_SEGMENT_TAP = None
_SEGMENT_DUMP = (None, 0, 0)      # (path, count, start) last given to set_dump_env


def segment_tap():
    """The tap base path of the current segment, for pyro_env.cmd (the PyroWave launcher clears the
    User-scope variable, so the tap only reaches vrserver through that file)."""
    return _SEGMENT_TAP


def set_tap_env(base_path):
    """Point the ALVR server's tap at `base_path`, or switch it off when None.

    The server runs inside vrserver, launched from the logged-in session by a scheduled task, so
    the setting has to be in the user environment before SteamVR starts -- which is why this is
    called around the per-segment restart rather than once at the top.

    Switching it off matters more than switching it on: the client reconnects after a sweep ends
    and streaming resumes, so a tap left enabled keeps writing. One 33 s segment at 400 Mbps is
    about 2.3 GB, and an unattended run would fill the disk."""
    global _SEGMENT_TAP
    if sys.platform != "win32":
        return
    value = str(base_path) if base_path else ""
    _SEGMENT_TAP = value or None
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"[Environment]::SetEnvironmentVariable('{TAP_ENV}',"
                    f"{repr(value) if value else '$null'},'User')"],
                   capture_output=True)
    # the PyroWave launcher only sees pyro_env.cmd, so it must follow the tap both ways
    write_pyro_env(*_SEGMENT_DUMP, tap=_SEGMENT_TAP)


BLANK_STREAM_FRACTION = 0.10   # of the requested bitrate; a real stream lands near 1.0


class BlankStream(RuntimeError):
    """The capture finished but carried no picture. A host-level fault, not a bad configuration,
    so the sweep stops rather than spending its remaining segments recording the same nothing."""


def median_bitrate_mbps(events):
    """Median encoded bitrate over a capture's GraphStatistics events, or None if it has none."""
    rates = [e["event_type"]["data"]["bitrate_bps"] / 1e6 for e in events
             if e.get("event_type", {}).get("id") == "GraphStatistics"
             and e["event_type"]["data"].get("bitrate_bps") is not None]
    return statistics.median(rates) if rates else None


def blank_stream_reason(events, requested_mbps, min_fraction=BLANK_STREAM_FRACTION):
    """Why this capture cannot be trusted, or None if the stream actually carried a picture.

    When the SteamVR compositor and the ALVR driver fail to hand over the shared D3D11 sync
    texture ("AcquireSync FAILED with WAIT_TIMEOUT" in vrcompositor.txt), ALVR encodes an
    uninitialised surface: every frame is a flat colour, the segment still finishes and reports
    ok, and every number it produced is meaningless. Seen once, where four days of uptime
    left 0.19 Mbps against a 400 Mbps request; a reboot restored 398 Mbps. Nothing else in the
    run notices, so check the bitrate before believing the capture."""
    median = median_bitrate_mbps(events)
    if median is None:
        return "capture has no GraphStatistics events (ALVR produced no frame statistics)"
    if median < requested_mbps * min_fraction:
        return (f"stream carried no picture: {median:.2f} Mbps median against a "
                f"{requested_mbps} Mbps request (< {min_fraction:.0%}); "
                f"check vrcompositor.txt for AcquireSync timeouts")
    return None


def thermal_status():
    out = adb("shell", "dumpsys thermalservice | grep 'Thermal Status'", check=False)
    try:
        return int(out.strip().split(":")[-1])
    except ValueError:
        return None


def session():
    return json.loads((ALVR_ROOT / "session.json").read_text(encoding="utf-8-sig"))


def wait_alvr_streaming(host, expected_ip, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        client = session().get("client_connections", {}).get(host, {})
        if client.get("connection_state") == "Streaming":
            if str(client.get("current_ip")) != expected_ip:
                raise RuntimeError(f"streaming from {client.get('current_ip')}, not {expected_ip}")
            forwards = adb("forward", "--list", check=False)
            if expected_ip != "127.0.0.1" and (":9943" in forwards or ":9944" in forwards):
                raise RuntimeError("ADB stream forwards present; stream would not use the intended transport")
            return
        time.sleep(3)
    raise RuntimeError(f"ALVR client not streaming after {timeout}s")


def screenshot(path):
    with open(path, "wb") as handle:
        subprocess.run([str(ADB), "-s", device_serial(), "exec-out", "screencap", "-p"], stdout=handle, timeout=60, check=True)


def record_clip(path, seconds):
    """Best-effort headset screen recording; True if a clip landed.

    screenrecord needs a hardware encoder, and the decoder is already using that silicon: at
    800 Mbps and 2560/eye it cannot allocate one and fails with no message. The clip
    is the least valuable capture anyway -- it samples every second display frame, so at 72 Hz it
    cannot show frame pacing, and latency comes from ALVR's telemetry instead. Losing it must not
    cost the telemetry and stills recorded alongside it."""
    remote = "/sdcard/Download/xrbench_clip.mp4"
    try:
        adb("shell", "screenrecord", "--time-limit", str(seconds), "--bit-rate", "100000000", remote,
            timeout=seconds + 30)
        adb("pull", remote, str(path))
        adb("shell", "rm", remote, check=False)
        return True
    except Exception as exc:
        log(f"clip capture skipped ({exc}); telemetry and stills are unaffected")
        return False


def start_telemetry(out_dir, seconds):
    """ALVR statistics capture for `seconds` (xrbench/telemetry_capture.ps1) into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).with_name("telemetry_capture.ps1")
    return subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                             "-Out", str(out_dir), "-Seconds", str(seconds),
                             "-Session", str(ALVR_ROOT / "session.json"), "-Adb", str(ADB),
                             "-Serial", device_serial()],
                            stdout=open(out_dir / "telemetry_stdout.txt", "w"), stderr=subprocess.STDOUT)


def telemetry_wait_seconds(scene_seconds):
    """How long to wait for the telemetry capture after the scene: the capture runs for the
    scene's length and then pulls its files, so a fixed 120 s cut the five-minute sustained
    cell off mid-capture."""
    return scene_seconds + 120


def run_scene_with_captures(seg, out_dir, telemetry):
    scene = subprocess.Popen([sys.executable, "-m", "xrbench.scene_app", "--label", seg.label,
                              "--seconds", str(seg.scene_seconds), "--out", str(out_dir)]
                             + (["--photo", seg.photo] if seg.photo else []),
                             cwd=str(CODE), stdout=open(out_dir / "scene_stdout.txt", "w"),
                             stderr=subprocess.STDOUT)
    time.sleep(3)                                     # scene visible and stable
    telemetry_proc = start_telemetry(out_dir / "telemetry", seg.scene_seconds - 5) if telemetry else None
    time.sleep(2)
    record_clip(out_dir / "clip.mp4", CLIP_SECONDS)
    with open(out_dir / "stills.csv", "w") as timing:
        timing.write("still,t_request,t_done\n")
        for i in range(STILLS_IN_SCENE):
            t_request = time.perf_counter()   # same QueryPerformanceCounter clock as frames.csv
            screenshot(out_dir / f"still_scene_{i}.png")
            timing.write(f"still_scene_{i}.png,{t_request:.6f},{time.perf_counter():.6f}\n")
    scene.wait(timeout=seg.scene_seconds + 60)
    return telemetry_proc


# Static SteamVR Home as the measured scene. SteamVR does not bring Home up when launched by the
# scheduled task, but it does launch Home when an app exits (Experiment 5): a short synthetic scene
# primes it. The measured window is bracketed by logcat markers so the report can take the
# receiver's cumulative counters from inside it only (xrbench.matrix).
HOME_PRIMER_SECONDS = 8
HOME_SETTLE_SECONDS = 15


def log_marker(text):
    adb("shell", "log", "-t", "XRBENCH", f"'{text}'", check=False)


def run_home_with_captures(seg, out_dir, telemetry):
    out_dir = Path(out_dir)
    (out_dir / "primer").mkdir(parents=True, exist_ok=True)
    primer = subprocess.Popen([sys.executable, "-m", "xrbench.scene_app", "--label", f"{seg.label}-primer",
                               "--seconds", str(HOME_PRIMER_SECONDS), "--out", str(out_dir / "primer")],
                              cwd=str(CODE), stdout=open(out_dir / "primer_stdout.txt", "w"),
                              stderr=subprocess.STDOUT)
    primer.wait(timeout=HOME_PRIMER_SECONDS + 60)   # fails harmlessly when Home already holds the scene slot
    time.sleep(HOME_SETTLE_SECONDS)                   # Home loads after the primer exits
    log_marker(f"{matrixmod.MARK_START} {seg.label}")
    started = time.perf_counter()
    telemetry_proc = start_telemetry(out_dir / "telemetry", seg.scene_seconds - 5) if telemetry else None
    time.sleep(2)
    if getattr(seg, "quality_frames", 0):
        # lossless encoder input of the frames the tap is recording, for objective quality
        # (xrbench.quality); the readback stalls the encoder, so these cells are quality cells
        source = out_dir / "quality" / "source.y4m"
        source.parent.mkdir(parents=True, exist_ok=True)
        trigger_dump(source, seg.quality_frames)
        done = wait_for_dump(source, seg.quality_frames)
        log(f"quality dump {'complete' if done else 'INCOMPLETE'}: {seg.quality_frames} frames -> {source}")
    record_clip(out_dir / "clip.mp4", CLIP_SECONDS)
    with open(out_dir / "stills.csv", "w") as timing:
        timing.write("still,t_request,t_done\n")
        for i in range(STILLS_IN_SCENE):
            t_request = time.perf_counter()
            screenshot(out_dir / f"still_home_{i}.png")
            timing.write(f"still_home_{i}.png,{t_request:.6f},{time.perf_counter():.6f}\n")
    time.sleep(max(0.0, seg.scene_seconds - (time.perf_counter() - started)))
    log_marker(f"{matrixmod.MARK_END} {seg.label}")
    return telemetry_proc


DUMP_TRIGGER_DIR = HERE / "corpus" / "_trigger"     # the launcher's ALVR_PYROWAVE_DUMP_TRIGGER


def trigger_dump(path, frames):
    """Ask the running encoder for `frames` lossless frames at `path` (it polls every 8 frames)."""
    DUMP_TRIGGER_DIR.mkdir(parents=True, exist_ok=True)
    (DUMP_TRIGGER_DIR / "trigger.txt").write_text(f"{path} {frames}\n")


def wait_for_dump(path, frames, timeout=90):
    """True once the dump's .frames.csv sidecar lists `frames` frames."""
    sidecar = Path(str(path) + ".frames.csv")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if sidecar.exists() and len(sidecar.read_text().strip().splitlines()) - 1 >= frames:
            return True
        time.sleep(1)
    return False


def scene_runner(seg):
    """What a segment measures: static SteamVR Home, or the synthetic scene app."""
    return run_home_with_captures if getattr(seg, "scene", "synthetic") == "home" else run_scene_with_captures


# Lines of the headset log worth keeping per segment; everything else is dropped. XRBENCH carries
# the Home measure-window markers (run_home_with_captures), without which the receiver's counters
# cannot be windowed.
LOGCAT_KEEP = ("Decoder saturation", "Waiting for IDR", "AMediaCodec format", "display period",
               "recv buffer", "software fallback", "Dropped video packet", "foveat", "named decoder",
               "Named decoder", "PYROWAVE", "pyroclient", "[PERF]", "[EARLYPOLL]", "decode path",
               "Performance level app votes", "XRBENCH")


def filter_logcat(text):
    return "\n".join(l for l in text.splitlines() if any(k in l for k in LOGCAT_KEEP))


def run_home_load(seg, out_dir):
    """After the scene exits SteamVR falls back to Home: realistic content, stills only."""
    time.sleep(15)
    for i in range(STILLS_IN_GAME):
        screenshot(out_dir / f"still_home_{i}.png")
        time.sleep(max(0, seg.game_seconds / STILLS_IN_GAME - 7))


# Samples defining "the recent past" when deciding the temperature has stopped falling.
COOLDOWN_FLOOR_WINDOW = 3
# Improvement between consecutive windows below which cooling is judged finished, in degrees.
COOLDOWN_FLOOR_PROGRESS_C = 0.5
# Measured: with SteamVR up the headset holds ~73 C merely awake and the zone readings
# swing about 2 C sample to sample, so 74 C is the coldest figure it actually reaches.
COOLDOWN_TARGET_C = 74.0


def at_thermal_floor(samples, window=COOLDOWN_FLOOR_WINDOW,
                     progress_c=COOLDOWN_FLOOR_PROGRESS_C):
    """True once the CPU temperature has stopped falling, so further waiting buys nothing.

    Compares the minimum of the last `window` readings against the minimum of the `window` before
    it. Minima rather than means because the readings swing about 2 C sample to sample, and a
    single upward spike must not make a settled headset look like it is still cooling.

    This exists so an absolute threshold can never again be set below what the hardware reaches:
    the refresh2 run chased 72.0 C for its full 180 s cap and ended at 72.6, exactly where it had
    already been at 90 s. A relative test is self-calibrating -- it works at the ~73 C floor with
    SteamVR running and at the ~50 C one without it -- where any fixed number is a guess about
    ambient temperature.
    """
    if len(samples) < window * 2:
        return False        # too little history to tell a plateau from a pause
    recent = min(samples[-window:])
    earlier = min(samples[-window * 2:-window])
    return recent > earlier - progress_c


def cooled_enough(status, history, target_status=2, target_cpu_c=COOLDOWN_TARGET_C,
                  window=COOLDOWN_FLOOR_WINDOW):
    """Whether a cool-down can stop: thermal status low enough, and the CPU as cold as it will get.

    Judged on the minimum of the last `window` readings rather than the latest one. The zone
    readings swing about 2 C sample to sample, so a single spike should neither hold a cold
    headset back nor -- via `at_thermal_floor` -- release a hot one early.
    """
    if status >= target_status:
        return False
    if not history:
        return True                  # no thermal data at all; status is the only signal there is
    return min(history[-window:]) <= target_cpu_c or at_thermal_floor(history)


def cooldown(max_wait=180, target_status=2, target_cpu_c=COOLDOWN_TARGET_C):
    """Let the headset shed heat between segments, with the client stopped.

    Stopping the client matters more than the waiting does. Measured: with the ALVR
    client still connected and decoding, the CPU shed about **1 C per minute**; force-stopped, it
    shed **4 C per minute** (75.7 -> 71.6 C in sixty seconds, thermal status 2 -> 1). The previous
    version only slept, so every cool-down ran at a quarter speed while the thing generating the
    heat carried on.

    Waits down to `target_status` rather than merely out of "severe". Decode timings drift with
    temperature -- a segment measured hot is not comparable with one measured cold -- and leaving
    at status 3 exactly is enough to be back in severe within seconds.

    The next segment restarts SteamVR and reconnects the client, so stopping it here costs nothing.
    """
    # Always stop the client, even when the status already looks fine: the status is a coarse
    # four-step severity and can read 1 anywhere between 55 and 75 C. Segments that start 20 C
    # apart are not comparable, which is what made the first transport comparison ambiguous --
    # a cell starting at 48 C "rose" 24.5 C while one starting at 72 C "rose" 2.0 C, and both
    # ended at the same 73-74 C.
    #
    # The target is the fast path for a genuinely cold headset; `at_thermal_floor` is what ends a
    # normal cool-down, once the temperature stops falling. Measured, the headset holds
    # ~73 C merely awake with SteamVR up and only fell to 48-54 C with SteamVR stopped entirely,
    # so a target below ~74 C is unreachable mid-sweep and burns the whole cap for nothing.
    adb("shell", "am", "force-stop", ALVR_PACKAGE, check=False)

    deadline = time.time() + max_wait
    history = []
    while time.time() < deadline:
        status = thermal_status() or 0
        cpu = thermal_zones().get("cpu_max_c")
        if cpu is not None:
            history.append(cpu)
        if cooled_enough(status, history, target_status, target_cpu_c):
            break
        log(f"cool-down: thermal status {status}, cpu {cpu} C, client stopped")
        time.sleep(10)


RNDIS_ADAPTER_MATCH = "Remote NDIS"


def _ps(command):
    return subprocess.run(["powershell", "-NoProfile", "-Command", command],
                          capture_output=True, text=True).stdout.strip()


def headset_ipv4(interface):
    out = adb("shell", "ip", "-o", "-4", "addr", "show", interface, check=False)
    for token in out.split():
        if "/" in token and token[0].isdigit():
            return token.split("/")[0]
    return None


class Transport:
    """Where the stream flows. Wi-Fi: headset wlan0. RNDIS: the headset's USB tethering network
    (NCM is broken in Galaxy XR firmware I610UEU2AZF3: its ncm,adb gadget links ncm.0 but only
    ncm.gs6 is created). While on RNDIS, headset Wi-Fi is off so the stream can only use USB."""

    def __init__(self, wifi_ip):
        self.wifi_ip = wifi_ip
        self.current = None
        self.rndis_ip = None
        self.link_results = {}

    def ip(self, transport):
        return {"wifi": self.wifi_ip, "rndis": self.rndis_ip, "adb": "127.0.0.1"}[transport]

    def enter(self, transport):
        if transport == self.current:
            return self.ip(transport)
        # Both USB transports need the cable, which wireless adb deliberately does without. Say so
        # plainly rather than failing later inside svc/netsh with something unrecognisable.
        if transport in ("rndis", "adb") and wirelessmod.is_wireless(device_serial()):
            raise RuntimeError(
                f"transport '{transport}' needs the USB cable, but adb is connected over Wi-Fi "
                f"({device_serial()}). Wi-Fi measured faster than both anyway -- 1124 Mbps against "
                "RNDIS 453 and the adb tunnel's 1102 at 60 ms network time.")
        if self.current == "adb":
            for port in (9943, 9944):
                adb("forward", "--remove", f"tcp:{port}", check=False)
        if transport == "wifi":
            adb("shell", "svc", "wifi", "enable", check=False)
            time.sleep(8)
        elif transport == "adb":
            for port in (9943, 9944):
                adb("forward", f"tcp:{port}", f"tcp:{port}")
            adb("shell", "svc", "wifi", "disable", check=False)
            time.sleep(3)
            if headset_ipv4("wlan0"):
                raise RuntimeError("headset Wi-Fi still has an address; stream would not be USB-only")
        else:
            self._enter_rndis()
        self.current = transport
        log(f"transport {transport}: headset at {self.ip(transport)}")
        try:
            if transport == "adb":
                adb("forward", "tcp:5001", "tcp:5001")
            self.link_results[transport] = link_test(self.ip(transport))
            if transport == "adb":
                adb("forward", "--remove", "tcp:5001", check=False)
            log(f"link test {transport}: {self.link_results[transport]}")
        except Exception as exc:
            self.link_results[transport] = {"error": str(exc)}
            log(f"link test {transport} failed: {exc}")
        return self.ip(transport)

    def _enter_rndis(self):
        adb("shell", "svc", "usb", "setFunctions", "rndis", check=False, timeout=30)
        deadline = time.time() + 45
        while time.time() < deadline:
            time.sleep(2)
            try:
                self.rndis_ip = headset_ipv4("usb0")
            except Exception:
                self.rndis_ip = None
            up = _ps(f"(Get-NetAdapter | ? {{ $_.InterfaceDescription -match '{RNDIS_ADAPTER_MATCH}' -and $_.Status -eq 'Up' }}).Name")
            if self.rndis_ip and up:
                break
        else:
            raise RuntimeError("RNDIS network did not come up (no usb0 address or no Windows adapter)")
        alias = up.splitlines()[0]
        # Route changes need admin; the sweep runs unelevated in the desktop session (SteamVR must),
        # so the elevated XRWiredNetFix task applies them (see tools/xrbench_netfix.ps1).
        runs = HERE / "xrbench-runs"
        result_path = runs / "netfix-result.json"
        result_path.unlink(missing_ok=True)
        (runs / "netfix-request.json").write_text(json.dumps({"ip": self.rndis_ip, "alias": alias}))
        subprocess.run(["powershell", "-NoProfile", "-Command", "Start-ScheduledTask XRWiredNetFix"], check=True)
        deadline = time.time() + 30
        while not result_path.exists() and time.time() < deadline:
            time.sleep(1)
        if not result_path.exists():
            raise RuntimeError("XRWiredNetFix did not report back")
        time.sleep(0.5)
        result = json.loads(result_path.read_text(encoding="utf-8-sig"))
        log(f"netfix: headset via {result['routed_via']}, internet via {result['internet_via']}, errors {result['errors']}")
        if result["routed_via"] != alias:
            raise RuntimeError(f"route to headset {self.rndis_ip} goes via {result['routed_via']}, not {alias}")
        if result["internet_via"] == alias:
            raise RuntimeError("PC internet would route through the headset")
        adb("shell", "svc", "wifi", "disable", check=False)
        time.sleep(3)
        if headset_ipv4("wlan0"):
            raise RuntimeError("headset Wi-Fi still has an address; stream would not be USB-only")

    def restore(self):
        """Wi-Fi back on. The headset stays in RNDIS USB mode on purpose: switching back to the
        default USB mode re-enumerates ADB on an interface Windows adb does not pick up (seen
        in practice), which would strand the control channel. Replugging the cable resets it."""
        if self.current == "adb":
            for port in (9943, 9944):
                adb("forward", "--remove", f"tcp:{port}", check=False)
        if self.current in ("rndis", "adb"):
            adb("shell", "svc", "wifi", "enable", check=False)
            log("restored headset Wi-Fi (USB left in RNDIS mode; replug to return to default)")


def reset_eye_timer():
    """With the wear sensor covered the headset sleeps 90 s after it stops seeing eyes (warning at
    60 s). A sleep/wake cycle restarts that countdown, so every measurement starts a fresh window."""
    adb("shell", "input", "keyevent", "KEYCODE_SLEEP", check=False)
    time.sleep(2)
    adb("shell", "input", "keyevent", "KEYCODE_WAKEUP", check=False)
    deadline = time.time() + 8
    while time.time() < deadline:
        if "mWakefulness=Awake" in adb("shell", "dumpsys power | grep mWakefulness=", check=False):
            return
        time.sleep(1)
    raise RuntimeError("headset did not wake (is the wear sensor covered or the headset worn?)")


def link_test(ip, seconds=6, port=5001):
    """Raw TCP throughput PC -> headset, no ALVR: toybox nc sink on the headset."""
    import socket
    sink = subprocess.Popen([str(ADB), "-s", device_serial(), "shell", f"toybox nc -l -p {port} > /dev/null"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.5)
    chunk = bytes(bytearray((i * 7919) & 0xFF for i in range(1 << 20)))  # 1 MiB, not compressible by links
    sent = 0
    try:
        with socket.create_connection((ip, port), timeout=5) as conn:
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20)
            start = time.perf_counter()
            while time.perf_counter() - start < seconds:
                conn.sendall(chunk)
                sent += len(chunk)
            elapsed = time.perf_counter() - start
    finally:
        sink.kill()
        adb("shell", "pkill -f 'nc -l -p 5001'", check=False)
    return {"ip": ip, "seconds": round(elapsed, 2), "mbytes": round(sent / 1e6, 1),
            "mbps": round(sent * 8 / elapsed / 1e6, 1)}


EARLY_POLL_PROP = "debug.xrwired.early_poll"


def set_early_poll_prop(enabled):
    """Device property the patched client reads once at start: "0" restores the stock loop order."""
    adb("shell", "setprop", EARLY_POLL_PROP, "1" if enabled else "0", check=False)


DECODE_PATH_PROP = "debug.xrwired.decode_path"
DUMP_ENV = "ALVR_PYROWAVE_DUMP"
DUMP_COUNT_ENV = "ALVR_PYROWAVE_DUMP_COUNT"
DUMP_FRAME_ENV = "ALVR_PYROWAVE_DUMP_FRAME"


def dump_path(label, klass, root=None):
    """Where a segment's lossless clip lands: <corpus>/<class>/<label>-<stamp>.y4m."""
    root = Path(root or planmod.CORPUS_ROOT)
    return root / (klass or "unclassified") / f"{label}-{dt.datetime.now():%Y%m%d-%H%M%S}.y4m"


def set_dump_env(path, count, start):
    """Point the server's encoder-input dump at `path` for `count` consecutive frames from
    encoder frame `start`, or clear it (None). Read once per driver process, like the tap."""
    global _SEGMENT_DUMP
    if sys.platform != "win32":
        return
    _SEGMENT_DUMP = (path, count, start) if path else (None, 0, 0)
    def setenv(name, value):
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"[Environment]::SetEnvironmentVariable('{name}',{repr(str(value)) if value is not None else '$null'},'User')"],
                       capture_output=True)
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        setenv(DUMP_ENV, path); setenv(DUMP_COUNT_ENV, count); setenv(DUMP_FRAME_ENV, start)
    else:
        setenv(DUMP_ENV, None); setenv(DUMP_COUNT_ENV, None); setenv(DUMP_FRAME_ENV, None)
    write_pyro_env(path, count, start, tap=segment_tap())


PYRO_ENV_CMD = HERE / "pyro_env.cmd"


def pyro_env_lines(path, count, start, tap=None):
    """The in-process overrides the clean SteamVR launcher `call`s: the scheduled task inherits a
    cached user environment and the launcher clears the dump and tap variables itself, so a
    User-scope variable never reaches vrserver. An empty list means the file is removed."""
    lines = []
    if path:
        lines += [f"set ALVR_PYROWAVE_DUMP={path}", f"set ALVR_PYROWAVE_DUMP_COUNT={count}", f"set ALVR_PYROWAVE_DUMP_FRAME={start}"]
    if tap:
        lines.append(f"set {TAP_ENV}={tap}")
    return lines


def write_pyro_env(path, count, start, target=None, tap=None):
    target = Path(target or PYRO_ENV_CMD)
    lines = pyro_env_lines(path, count, start, tap)
    if sys.platform != "win32" and target == PYRO_ENV_CMD:
        return
    if lines:
        target.write_text("@echo off\n" + "\n".join(lines) + "\n")
    elif target.exists():
        target.unlink()
PRECISION_PROP = "debug.xrwired.pyro_precision"
WAVELET_ENV = "ALVR_PYROWAVE_WAVELET"


def set_pyro_precision_prop(precision):
    """0 | 1 | 2 sets PyroWave's precision on the headset; anything else clears the property so
    the library's default applies. libpyroclient reads it once at create (Experiment 2)."""
    value = str(precision) if precision in (0, 1, 2) else ""
    adb("shell", "setprop", PRECISION_PROP, value if value else '""', check=False)


def set_wavelet_env(wavelet):
    """ALVR_PYROWAVE_WAVELET=53 selects CDF 5/3 on the server; cleared for 9/7. Read once per
    driver process like the other server switches, so it goes before the SteamVR restart."""
    if sys.platform != "win32":
        return
    value = repr("53") if wavelet == "53" else "$null"
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"[Environment]::SetEnvironmentVariable('{WAVELET_ENV}',{value},'User')"],
                   capture_output=True)


def research_overrides(seg):
    """The headset properties and server env vars a segment sets before its SteamVR restart. They
    override the session settings, which is how the research cells pinned the decode path, wavelet
    and gaze. A `settings_only` segment clears them all, so the session (configure_alvr_2013.ps1)
    alone configures the stream, as it does for a beta tester."""
    if getattr(seg, "settings_only", False):
        return {"decode_path_prop": "auto", "wavelet_env": "97", "gaze_env": True, "precision_prop": -1}
    return {"decode_path_prop": seg.decode_path, "wavelet_env": seg.wavelet, "gaze_env": seg.gaze,
            "precision_prop": seg.pyro_precision}


def set_decode_path_prop(path):
    """fragment | compute | auto: libpyroclient reads it once at create (Experiment 1)."""
    adb("shell", "setprop", DECODE_PATH_PROP, path if path in ("fragment", "compute") else "auto", check=False)


def hottest_zone_c():
    """Hottest thermal zone right now, in C, or None -- sampled immediately before a client launch
    so paired cells can be thermally matched (Experiment 1: <= 3 C between PF and PC)."""
    out = adb("shell", "cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1", check=False)
    try:
        return int(out.strip().splitlines()[-1]) / 1000.0
    except (ValueError, IndexError, AttributeError):
        return None


GPU_CLOCK_CMD = ("cat /sys/class/kgsl/kgsl-3d0/devfreq/cur_freq /sys/class/kgsl/kgsl-3d0/gpu_busy_percentage "
                 "/sys/class/kgsl/kgsl-3d0/thermal_pwrlevel 2>/dev/null; "
                 "cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1")


def parse_gpu_clock_sample(text):
    """{cur_freq_hz, busy_pct, thermal_pwrlevel, hottest_c} from the four lines GPU_CLOCK_CMD prints,
    or None if the device did not answer. The Adreno's msm-adreno-tz governor moves the clock
    with load (Experiment 2 found decode time shifting ~15 % mid-cell with nothing else changing),
    so completion time is only comparable between arms alongside the clock it ran at."""
    try:
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        freq = int(lines[0])
        busy = int(lines[1].split()[0])
        level = int(lines[2])
        hottest = int(lines[3]) / 1000.0
    except (ValueError, IndexError, AttributeError):
        return None
    return {"cur_freq_hz": freq, "busy_pct": busy, "thermal_pwrlevel": level, "hottest_c": hottest}


class GpuClockSampler(threading.Thread):
    """Samples the headset's GPU clock, busy percentage, thermal power level and hottest zone every
    `interval_s` for the life of a segment; `finish` stops it and writes gpufreq.csv."""

    def __init__(self, interval_s=2.0):
        super().__init__(daemon=True)
        self.interval_s = interval_s
        self.stop = threading.Event()
        self.rows = []

    def run(self):
        while not self.stop.is_set():
            try:
                row = parse_gpu_clock_sample(adb("shell", GPU_CLOCK_CMD, check=False, timeout=15))
                if row:
                    self.rows.append((time.time(), row))
            except Exception as exc:  # a sampling failure must never end the sweep
                log(f"gpu clock sampler error: {exc}")
            self.stop.wait(self.interval_s)

    def finish(self, path):
        self.stop.set()
        self.join(timeout=20)
        lines = ["t,cur_freq_hz,busy_pct,thermal_pwrlevel,hottest_c"]
        for t, r in self.rows:
            lines.append(f"{t:.1f},{r['cur_freq_hz']},{r['busy_pct']},{r['thermal_pwrlevel']},{r['hottest_c']:g}")
        Path(path).write_text("\n".join(lines) + "\n")


def relaunch_client():
    # A hung client counts as "running", so launching it again is a no-op; start it fresh.
    adb("shell", "am", "force-stop", ALVR_PACKAGE, check=False)
    time.sleep(1)
    adb("shell", "monkey", "-p", ALVR_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1", check=False)


def stream_is_live(out_dir, seconds=4):
    """True if ALVR's statistics feed carries per-frame stats, i.e. the client is really decoding.
    session.json can keep a stale "Streaming" state, so the file alone is not proof."""
    probe_dir = out_dir / "liveness"
    proc = start_telemetry(probe_dir, seconds)
    proc.wait(timeout=seconds + 60)
    events_path = probe_dir / "events.json"
    if not events_path.exists():
        return False
    try:
        events = json.loads(events_path.read_text(encoding="utf-8-sig") or "[]")
    except ValueError:
        return False
    return sum(1 for e in events if e.get("event_type", {}).get("id") == "GraphStatistics") > 0


def connect_live(args, client_ip, out_dir, attempts=3):
    for attempt in range(1, attempts + 1):
        reset_eye_timer()
        relaunch_client()
        wait_alvr_streaming(args.client_host, client_ip)
        time.sleep(2)
        if stream_is_live(out_dir):
            if attempt > 1:
                log(f"stream live on attempt {attempt}")
            return attempt
        log(f"attempt {attempt}: session says Streaming but no frame statistics; reconnecting")
    raise RuntimeError(f"stream not live after {attempts} attempts (no ALVR frame statistics)")


def connect_with_applied_config(args, out_dir, client_ip, codec="H264"):
    """ALVR decides codec, resolution, refresh rate and foveation when the client connects; if they
    differ from what the running driver was started with, it saves them and asks the Dashboard to
    restart SteamVR. Headless there is no Dashboard, so the stream would silently keep the previous
    run's settings. Connect once to let ALVR save the new openvr_config, restart SteamVR ourselves,
    reconnect, and require the config to be stable across the two connections."""
    reset_eye_timer()
    relaunch_client()
    wait_alvr_streaming(args.client_host, client_ip)
    first = session().get("openvr_config")
    restart_steamvr(task=steamvr_task_for(codec))
    attempts = connect_live(args, client_ip, out_dir)   # measured window starts at its eye reset
    (out_dir / "connect_attempts.txt").write_text(str(attempts))
    applied = session().get("openvr_config")
    (out_dir / "openvr_config.json").write_text(json.dumps(applied, indent=2))
    if applied != first:
        raise RuntimeError("ALVR stream config changed again after the SteamVR restart")
    return applied


def configure_alvr(seg, client_host, client_ip):
    """Apply a segment's ALVR settings: configure_alvr_2013.ps1 comes from the code, the ALVR install
    it edits (session.json) from the runtime root. Without -Root the script looks beside itself."""
    return powershell(CODE / "configure_alvr_2013.ps1", *seg.configure_args(), "-ClientHostname", client_host,
                      "-ClientWifiIp", client_ip, "-Root", str(ALVR_ROOT))


def run_alvr_segment(seg, args, out_dir, client_ip):
    if not api_up():
        restart_steamvr()
    (out_dir / "configure.txt").write_text(configure_alvr(seg, args.client_host, client_ip))
    adb("logcat", "-c", check=False)
    restart_steamvr(task=steamvr_task_for(seg.codec))  # most video settings need a SteamVR restart
    time.sleep(5)
    # Experiment 1 thermal matching: the hottest zone and status immediately before the client
    # launches, and again after the segment, so paired cells can be compared at their start.
    hottest = {"start_c": hottest_zone_c(), "status_start": thermal_status()}
    clock = GpuClockSampler()
    clock.start()
    connect_with_applied_config(args, out_dir, client_ip, seg.codec)
    time.sleep(2)
    telemetry = scene_runner(seg)(seg, out_dir, telemetry=True)
    if seg.game_seconds:
        run_home_load(seg, out_dir)
    if telemetry:
        telemetry.wait(timeout=telemetry_wait_seconds(seg.scene_seconds))
    clock.finish(out_dir / "gpufreq.csv")
    logcat = adb("logcat", "-d", check=False)
    (out_dir / "logcat.txt").write_text(filter_logcat(logcat))
    hottest.update({"end_c": hottest_zone_c(), "status_end": thermal_status()})
    (out_dir / "hottest.json").write_text(json.dumps(hottest))


def switch_to_vd(args, out_root):
    log("switching SteamVR from ALVR to Virtual Desktop")
    stop_steamvr()
    subprocess.run(["taskkill", "/F", "/IM", "ALVR Dashboard.exe"], capture_output=True)
    force_render_scale()
    edit_steamvr_settings("driver_alvr_server", {"enable": False})
    subprocess.Popen([str(VD_STREAMER)])
    adb("shell", "am", "force-stop", ALVR_PACKAGE, check=False)
    adb("shell", "monkey", "-p", VD_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1", check=False)
    log("ACTION NEEDED: in the headset, connect Virtual Desktop to the PC and press 'Launch SteamVR'")
    deadline = time.time() + 600
    while time.time() < deadline:
        if subprocess.run(["tasklist", "/FI", "IMAGENAME eq vrcompositor.exe"], capture_output=True,
                          text=True).stdout.count("vrcompositor.exe"):
            log("SteamVR is running under Virtual Desktop")
            time.sleep(20)
            return
        time.sleep(5)
    raise RuntimeError("Virtual Desktop did not start SteamVR within 10 minutes")


def restore(out_root):
    backup = out_root / "steamvr.vrsettings.original"
    if backup.exists():
        stop_steamvr()
        subprocess.run(["taskkill", "/F", "/IM", "VirtualDesktop.Streamer.exe"], capture_output=True)
        shutil.copy2(backup, STEAMVR_SETTINGS)
        # Owner decision: SteamVR render resolution stays locked at 100% outside the
        # benchmark too (render resolution is tested separately); everything else is restored.
        edit_steamvr_settings("steamvr", LOCKED_RENDER_SCALE)
        log("restored original steamvr.vrsettings (dashboard, ALVR driver); render scale kept at 100%")
    original = out_root / "alvr-session-original.json"
    if original.exists():
        shutil.copy2(original, ALVR_ROOT / "session.json")
        log("restored original ALVR 20.13 session.json")
    # Leave the owner streaming (normal settings) instead of stranded in the ALVR lobby.
    subprocess.run(["powershell", "-NoProfile", "-Command", "Start-ScheduledTask XRWiredSteamVR"])
    log("SteamVR restarted with the restored settings")
    try:
        relaunch_client()                        # a fresh client connects to the restored streamer
        log("ALVR client relaunched")
    except Exception as exc:
        log(f"ALVR client relaunch skipped: {exc}")


# --plan names -> plan builders
PLAN_BUILDERS = {"default": planmod.default_plan, "adb": planmod.adb_plan, "packet": planmod.packet_plan,
           "decoder": planmod.decoder_plan, "unfoveated": planmod.unfoveated_plan,
           "foveated": planmod.foveated_plan, "quality": planmod.quality_plan,
           "fullmotion": lambda: planmod.workload_plan("full"),
           "seated": lambda: planmod.workload_plan("seated"),
           "seatedquality": lambda: planmod.workload_plan("seated_quality"),
           "screen": lambda: planmod.workload_plan("screen"),
           "thermal": planmod.thermal_plan,
           "resolution": planmod.resolution_plan,
           "packetthermal": planmod.packet_thermal_plan,
           "link": planmod.link_plan,
           "buffering": planmod.buffering_plan,
           "pacing": planmod.pacing_plan,
           "headroom": planmod.headroom_plan,
           "latestart": planmod.latestart_plan,
           "pyro": planmod.pyro_plan,
           "pyro-latestart": planmod.pyro_latestart_plan,
           "pyro-phase": planmod.pyro_phase_plan,
           "pyro-earlypoll": planmod.pyro_earlypoll_plan,
           "pyro-hz": planmod.pyro_hz_plan,
           "pyro-res": planmod.pyro_res_plan,
           "pyro-ab": planmod.pyro_ab_plan,
           "pyro-live3": planmod.pyro_live3_plan,
           "pyro-path": planmod.pyro_path_plan,
           "pyro-home": planmod.pyro_home_baseline_plan,
           "pyro-matrix": planmod.pyro_matrix_plan,
           "pyro-matrix-quality": planmod.pyro_matrix_quality_plan,
    "beta-validation": planmod.beta_validation_plan,
           "pyro-53": planmod.pyro_53_plan,
           "corpus": planmod.corpus_plan,
           "upscaling": planmod.upscaling_plan,
           "transport": planmod.transport_thermal_plan,
           "transportverify": planmod.transport_verify_plan,
           "all": lambda: planmod.default_plan() + planmod.adb_plan()}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", help="default | adb | packet | decoder | unfoveated | foveated | all, "
                                       "or a plan JSON file")
    parser.add_argument("--client-ip", help="headset Wi-Fi IP (required for ALVR runs)")
    parser.add_argument("--client-host", help="ALVR client hostname as shown in session.json")
    parser.add_argument("--out", default=str(HERE / "xrbench-runs" / f"{dt.datetime.now():%Y%m%d-%H%M}"))
    parser.add_argument("--dump-label", default="", help="content class for this run's corpus clips (overrides the plan's)")
    parser.add_argument("--only", nargs="*", help="run just these segment labels")
    parser.add_argument("--automated", action="store_true",
                        help="only segments valid without a wearer: headset awake, wear sensor "
                             "covered. Excludes gaze-behaviour and subjective cells.")
    parser.add_argument("--attended", action="store_true",
                        help="only segments that need the headset actually worn")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--tap", action="store_true",
                        help="capture the encoded bitstream per segment (~2.3 GB per 33 s at "
                             "400 Mbps; needs the instrumented ALVR server)")
    args = parser.parse_args()

    presets = PLAN_BUILDERS
    if args.plan and args.plan in presets:
        segments = presets[args.plan]()
    else:
        segments = planmod.load(args.plan) if args.plan else planmod.default_plan()
    planmod.validate(segments)
    if args.only:
        segments = [s for s in segments if s.label in args.only]
    if args.automated:
        segments = planmod.filter_attendance(segments, wearer_present=False)
    if args.attended:
        segments = planmod.filter_attendance(segments, wearer_present=True)
    log(f"{len(segments)} segments, about {planmod.estimate_minutes(segments):.0f} min")
    # Attach over Wi-Fi first, so a cable attached for some other reason is not silently used.
    if args.client_ip and connect_wireless(args.client_ip):
        log(f"adb over Wi-Fi: {wirelessmod.target(args.client_ip)}")
    warn_if_wired()
    for s in segments:
        width, height = s.eye_size()
        log(f"  {s.label:<16} {s.stack:<4} {s.transport:<5} {s.codec:<6} {s.mbps:>5} Mbps "
            f"{width}x{height}/eye {s.hz} Hz fov={'on' if s.foveation else 'off'} "
            f"centre={s.center_size_x:.2f} gaze={'on' if s.gaze else 'OFF'} "
            f"ss={s.render_scale:g} buf={s.buffering}  {s.note}")
    if args.dry_run:
        return
    if any(s.stack == "alvr" for s in segments) and not (args.client_host and args.client_ip):
        parser.error("--client-host and --client-ip are required for ALVR segments")

    out_root = Path(args.out)
    # Before session.json is read, so a leftover Dashboard cannot rewrite it underneath us.
    preflight_clean(args.client_ip, out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    planmod.save(segments, out_root / "plan.json")
    shutil.copy2(ALVR_ROOT / "session.json", out_root / "alvr-session-original.json")
    # The live file may still hold benchmark values if an earlier run was interrupted, so prefer
    # the pristine copy taken before any xrbench run.
    pristine = HERE / "backups" / "steamvr.vrsettings.before-xrbench"
    shutil.copy2(pristine if pristine.exists() else STEAMVR_SETTINGS, out_root / "steamvr.vrsettings.original")
    watchdog = AwakeWatchdog()
    watchdog.start()
    transport = Transport(args.client_ip)
    results = []
    in_vd = False
    try:
        for seg in segments:
            out_dir = out_root / seg.label
            out_dir.mkdir(exist_ok=True)
            started = time.time()
            log(f"=== {seg.label}: {seg.note}")
            if not reconnect_if_dropped():
                raise RuntimeError("headset not reachable by adb; is it awake and on Wi-Fi?")
            zones_start = thermal_zones()
            battery_start = battery_state()
            check_battery(battery_start)
            status = "ok"
            blank_stream_seen = False
            try:
                if seg.stack == "vd":
                    if not in_vd:
                        switch_to_vd(args, out_root)
                        in_vd = True
                    run_scene_with_captures(seg, out_dir, telemetry=False)
                    if seg.game_seconds:
                        run_home_load(seg, out_dir)
                else:
                    client_ip = transport.enter(seg.transport)
                    # All three before the segment's SteamVR restart, so the server and
                    # SteamVR pick them up on start rather than mid-run.
                    set_tap_env(tap_base_path(out_root, seg.label) if args.tap else None)
                    overrides = research_overrides(seg)
                    set_gaze_env(overrides["gaze_env"])
                    set_pacing_headroom_env(seg.pacing_headroom_us)
                    set_pacing_delay_env(seg.pacing_delay_us)
                    set_phase_lock_env(seg.phase_lock)
                    set_early_poll_prop(seg.early_poll)
                    set_decode_path_prop(overrides["decode_path_prop"])
                    set_pyro_precision_prop(overrides["precision_prop"])
                    set_wavelet_env(overrides["wavelet_env"])
                    dump = dump_path(seg.label, args.dump_label or seg.dump_label) if seg.dump_frames else None
                    set_dump_env(dump, seg.dump_frames, seg.dump_start)
                    if dump:
                        log(f"corpus dump: {seg.dump_frames} frames from encoder frame {seg.dump_start} -> {dump}")
                    set_render_scale(seg.render_scale)
                    crash_offset = crash_log_size()
                    run_alvr_segment(seg, args, out_dir, client_ip)
                    save_gaze_stats(out_dir, crash_offset)
                    events_path = out_dir / "telemetry" / "events.json"
                    if events_path.exists():
                        events = json.loads(events_path.read_text(encoding="utf-8-sig"))
                        blank = blank_stream_reason(events, seg.mbps)
                        if blank:
                            raise BlankStream(blank)
                cooldown()
            except Exception as exc:
                status = f"failed: {exc}"
                blank_stream_seen = isinstance(exc, BlankStream)
                log(f"{seg.label} {status}")
                # Keep the evidence: headset log and ALVR's own log for this failure.
                (out_dir / "failure_logcat.txt").write_text(adb("logcat", "-d", check=False), errors="replace")
                crash = ALVR_ROOT / "crash_log.txt"
                if crash.exists():
                    shutil.copy2(crash, out_dir / "failure_alvr_crash_log.txt")
            zones_end = thermal_zones()
            battery_end = battery_state()
            battery_drain = batterymod.drain_per_hour(
                battery_start.get("level"), battery_end.get("level"), time.time() - started)
            if batterymod.is_losing_ground(battery_end, battery_drain):
                log(f"WARNING: battery says charging but fell {battery_drain:.0f}%/h -- "
                    "the supply is not keeping up with the draw")
            meta = {"segment": seg.__dict__, "status": status, "started": started,
                    "thermal_zones_start": zones_start, "thermal_zones_end": zones_end,
                    "thermal_rise_c": thermalmod.rise(zones_start, zones_end),
                    "battery_start": battery_start, "battery_end": battery_end,
                    # Positive means the level fell. Recorded per segment because the headset
                    # reports "charging" on any powered connection, and once it did so
                    # all day while losing ~20%/hour to an undersized USB 3 port.
                    "battery_drain_pct_per_hour": battery_drain,
                    "battery_losing_ground": batterymod.is_losing_ground(
                        battery_end, battery_drain),
                    "transport_ip": transport.ip(seg.transport) if seg.stack == "alvr" else None,
                    "seconds": round(time.time() - started), "capture_fps": CAPTURE_FPS,
                    "thermal_end": thermal_status(), "watchdog_wakes": watchdog.wakes}
            (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
            results.append((seg.label, status))
            if blank_stream_seen:
                log("stopping the sweep: the host is not delivering a picture, so every "
                    "remaining segment would record the same nothing")
                break
    finally:
        # Always, even on failure: the client reconnects once the sweep lets go and a tap left on
        # keeps writing until the disk is full. The gaze switch would otherwise silently pin the
        # next session's foveation centre, which is exactly the kind of stale state that looks
        # like a regression later.
        set_dump_env(None, 0, 0)
        set_tap_env(None)
        set_gaze_env(True)
        set_pacing_headroom_env(0)
        set_pacing_delay_env(0)
        set_render_scale(1.0)
        watchdog.stop.set()
        try:
            transport.restore()
        except Exception as exc:
            log(f"transport restore failed: {exc}")
        restore(out_root)
        (out_root / "summary.json").write_text(json.dumps(results, indent=2))
        (out_root / "link_tests.json").write_text(json.dumps(transport.link_results, indent=2))
        log(f"done: {sum(1 for _, s in results if s == 'ok')}/{len(results)} segments ok -> {out_root}")


if __name__ == "__main__":
    main()
