import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import thermal as th

# `for d in /sys/class/thermal/thermal_zone*; do echo "$(cat $d/temp) $(cat $d/type)"; done`
SAMPLE = """76000 cpu-1-1-0
75700 cpuss-1
69900 video
71600 gpuss-0
71600 ddr
67900 camera-0
73300 sys-therm-0
"""


def test_groups_zones_and_reports_the_hottest_of_each():
    t = th.parse_zones(SAMPLE)
    assert t["cpu_max_c"] == 76.0
    assert t["video_c"] == 69.9
    assert t["gpu_max_c"] == 71.6
    assert t["ddr_c"] == 71.6


def test_reports_which_group_is_hottest():
    # measured right after a heavy sweep: CPU 76.0 against video 69.9, so the decode
    # block is not what throttles -- reducing pixels would not have helped
    t = th.parse_zones(SAMPLE)
    assert t["hottest_group"] == "cpu"
    assert t["hottest_c"] == 76.0


def test_ignores_malformed_lines_rather_than_failing_a_segment():
    t = th.parse_zones("76000 cpu-1-1-0\ngarbage\n\n-  weird\n69900 video\n")
    assert t["cpu_max_c"] == 76.0 and t["video_c"] == 69.9


def test_empty_input_is_not_an_error():
    t = th.parse_zones("")
    assert t["cpu_max_c"] is None and t["hottest_group"] is None


def test_rise_between_two_samples():
    before = th.parse_zones("50000 cpu-1-1-0\n48000 video\n")
    after = th.parse_zones("76000 cpu-1-1-0\n69900 video\n")
    rise = th.rise(before, after)
    assert rise["cpu_max_c"] == 26.0
    assert rise["video_c"] == 21.9
