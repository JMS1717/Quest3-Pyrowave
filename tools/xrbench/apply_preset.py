"""Apply one quality preset to ALVR, without running a benchmark:

    python -m xrbench.apply_preset Q-SCREEN-BEST
    python -m xrbench.apply_preset --list

The sweep answers whether a preset is *viable* -- fps, decode time, packet loss, how often the
foveation centre hit its clamp. It cannot answer whether the picture looks better, because the
still-capture quality path is broken (see patches/README.md) and because the benchmark's test panel
is a fixed 1600x1200 pixels, so it subtends a *smaller angle* as render resolution rises: 59 deg at
2560/eye but 43 deg at 3520/eye. Comparing presets on it would conflate sharpness with size.

So quality gets judged in real content. This applies a preset and gets out of the way.

The gaze switch is an environment variable read once per driver process, so it only takes effect
on the next SteamVR start -- which this does not do, deliberately: stopping SteamVR under a running
game is worse than asking.
"""
import argparse
import subprocess
import sys
from pathlib import Path

from . import paths
from . import plan as planmod

HERE = Path(__file__).resolve().parents[1]
GAZE_ENV = "ALVR_GAZE_FOVEATION"


def presets():
    return {s.label: s for s in planmod.quality_plan()}


def describe(segment):
    """One line: the settings, and what they buy in terms the panel can be compared against."""
    width, height = segment.eye_size()
    fov_h, panel_w = 94.4, 3552          # measured; see patches/README.md
    return (f"{segment.label:<17} {segment.workload:<15} {segment.hz} Hz  {segment.mbps:>3} Mbps  "
            f"{width}x{height}/eye  centre {segment.center_size_x:.2f}  "
            f"gaze {'on' if segment.gaze else 'OFF'}  ss {segment.render_scale:g}  "
            f"-> {width / fov_h:.1f} px/deg ({width / panel_w:.0%} of panel)")


def set_gaze_env(enabled):
    if sys.platform != "win32":
        return
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"[Environment]::SetEnvironmentVariable('{GAZE_ENV}',"
                    f"{'$null' if enabled else repr('0')},'User')"],
                   capture_output=True)


def apply(segment, client_host, client_ip):
    script = HERE / "configure_alvr_2013.ps1"
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
           *segment.configure_args(), "-ClientHostname", client_host, "-ClientWifiIp", client_ip,
           "-Root", str(paths.ROOT / "ALVR-20.13.0")]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"configure failed:\n{result.stdout}\n{result.stderr}")
    set_gaze_env(segment.gaze)
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("preset", nargs="?", help="preset label, e.g. Q-SCREEN-BEST")
    parser.add_argument("--list", action="store_true", help="list the presets and what they buy")
    parser.add_argument("--client-host", default="5050.client")
    parser.add_argument("--client-ip", default="")
    args = parser.parse_args()

    available = presets()
    if args.list or not args.preset:
        for segment in available.values():
            print(describe(segment))
        return
    if args.preset not in available:
        raise SystemExit(f"unknown preset {args.preset}; --list shows them")

    segment = available[args.preset]
    print(describe(segment))
    print(apply(segment, args.client_host, args.client_ip).strip())
    if not segment.gaze:
        print(f"{GAZE_ENV}=0 set: gaze foveation is OFF for this preset.")
    print("Restart SteamVR for the gaze setting to take effect; the rest applies on reconnect.")


if __name__ == "__main__":
    main()
