"""Nothing may start a sweep on top of a machine that is already running one.

Written after the day when a sweep died mid-run without tearing down, two more were started
over the top of it, and the headset sat decoding three streams at once. The owner found it
cooking. Every earlier guard in this harness was per-segment; none of them looked at what was
already running when the run began.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import sweep as sw

SWEEP_CMD = r"C:\Python314\python.exe -m xrbench.sweep --plan latestart --out runs\x"


def rows(*entries):
    return [{"pid": pid, "name": name, "cmdline": cmd} for pid, name, cmd in entries]


def test_our_own_process_is_not_a_stray():
    assert sw.stray_sweep_pids(rows((4242, "python.exe", SWEEP_CMD)), own_pid=4242) == []


def test_a_second_sweep_is_a_stray():
    # exactly what happened: the first sweep never exited, a second was started anyway
    found = sw.stray_sweep_pids(rows((111, "python.exe", SWEEP_CMD),
                                     (4242, "python.exe", SWEEP_CMD)), own_pid=4242)
    assert found == [111]


def test_other_xrbench_tools_are_left_alone():
    # latency_breakdown and friends are read-only analysis; they touch neither SteamVR nor the
    # headset, and killing a colleague's analysis run would be its own kind of rude
    cmd = r"C:\Python314\python.exe -m xrbench.latency_breakdown xrbench-runs\20260922-1030"
    assert sw.stray_sweep_pids(rows((77, "python.exe", cmd)), own_pid=1) == []


def test_the_shell_that_launched_us_is_not_a_stray():
    # ssh runs us through cmd.exe, whose own command line quotes ours verbatim. Matching on the
    # command line alone would make every remote run refuse to start.
    wrapper = rf'cmd.exe /c "cd <workspace> && {SWEEP_CMD}"'
    assert sw.stray_sweep_pids(rows((9, "cmd.exe", wrapper)), own_pid=1) == []


def test_a_row_with_no_command_line_does_not_crash():
    # Win32_Process returns a null CommandLine for processes we cannot open, and there are always
    # some. A preflight that raises here would block every run on an ordinary machine.
    assert sw.stray_sweep_pids(rows((5, "python.exe", None)), own_pid=1) == []


def test_several_strays_come_back_in_pid_order():
    found = sw.stray_sweep_pids(rows((300, "python.exe", SWEEP_CMD),
                                     (100, "python.exe", SWEEP_CMD),
                                     (4242, "python.exe", SWEEP_CMD)), own_pid=4242)
    assert found == [100, 300]


def test_a_sweep_run_by_path_is_still_a_sweep():
    # `python xrbench\sweep.py` is the same program as `python -m xrbench.sweep`
    cmd = r"C:\Python314\python.exe xrbench\sweep.py --plan quality"
    assert sw.stray_sweep_pids(rows((88, "python.exe", cmd)), own_pid=1) == [88]


# ---- a fair baseline needs an empty machine, and a record of what was there ----
# The first PyroWave-vs-H.264 comparison ran with Epic's launcher and overlay, a
# per-packet network monitor and a desktop-automation host all live on the PC. game_time
# was unstable and nobody could say why. Two rules follow: known non-essentials are stopped at
# preflight, and every run directory records the process table so the rest can be judged later.

def test_known_nonessentials_are_picked_by_image_name():
    found = sw.nonessential_pids(rows((10, "EpicGamesLauncher.exe", "x"),
                                      (11, "EOSOverlayRenderer-Win64-Shipping.exe", "x"),
                                      (12, "GlassWire.exe", "x"),
                                      (13, "vrserver.exe", "x"),
                                      (14, "steam.exe", "x")))
    assert found == [10, 11, 12]


def test_the_things_a_run_needs_are_never_nonessential():
    # Steam and SteamVR are the system under test; the remote-desktop servers are how the owner
    # reaches the box; adb is how the harness reaches the headset.
    for name in ("steam.exe", "steamwebhelper.exe", "vrserver.exe", "vrcompositor.exe",
                 "rustdesk.exe", "tvnserver.exe", "adb.exe", "python.exe", "sshd.exe"):
        assert sw.nonessential_pids(rows((1, name, "x"))) == []


def test_process_report_is_sorted_by_cpu_and_names_the_offenders():
    report = sw.process_report(rows((1, "vrserver.exe", "x"), (2, "GlassWire.exe", "x"), (3, "dwm.exe", "x")),
                               cpu_seconds={1: 10.0, 2: 250.0, 3: 5.0})
    lines = report.splitlines()
    assert lines[0].startswith("GlassWire.exe")
    assert "nonessential" in lines[0]
    assert "nonessential" not in lines[1]


# ---- the headset must be empty of everything but the client under test ----
# Owner's rule: PC apps are normal and stay; the headset is the measured device and
# must be clean. Every third-party package found running is stopped at preflight and the list is
# recorded, so a comparison is between streams, not between whatever else the headset was doing.

PS_OUTPUT = """NAME
init
com.android.systemui
com.samsung.android.app.xr.home
com.google.android.gms
com.valvesoftware.steamlink
alvr.client.galaxy2013
com.xrwired.receiver
org.mozilla.firefox
io.github.something
com.qualcomm.qti.services
com.quicinc.voice.activation
com.skms.android.agent
com.wssyncmldm
"""


def test_third_party_packages_exclude_the_platform():
    assert sw.third_party_packages(PS_OUTPUT) == [
        "alvr.client.galaxy2013", "com.valvesoftware.steamlink", "com.xrwired.receiver",
        "io.github.something", "org.mozilla.firefox"]


def test_third_party_packages_is_empty_on_a_clean_headset():
    assert sw.third_party_packages("NAME\ninit\ncom.android.systemui\n") == []
