import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import plan as pl


def test_the_budget_is_sixty_milliseconds():
    assert pl.LATENCY_BUDGET_MS == 60.0


def test_a_config_inside_the_budget_passes():
    assert pl.latency_verdict(42.0) == "pass"
    assert pl.latency_verdict(60.0) == "pass"       # the bar itself is acceptable


def test_a_config_over_the_budget_fails_however_good_its_other_numbers():
    # the whole point of a hard bar: 72 fps with zero packet loss is still a fail at 98 ms
    assert pl.latency_verdict(60.1) == "fail"
    assert pl.latency_verdict(98.39) == "fail"


def test_a_segment_with_no_telemetry_has_no_verdict():
    # a failed capture must not be silently recorded as passing
    assert pl.latency_verdict(None) is None


def test_everything_measured_before_the_bar_existed_fails_it():
    # recorded so the size of the gap is not forgotten: these are the best runs to date, all of
    # them well over. Decode (14.8) plus network (11.0) is only a quarter of it, so the bar cannot
    # be reached by changing geometry or transport -- it needs the queuing budget attacked.
    for measured in (89.67, 93.64, 98.39, 152.99, 533.82):
        assert pl.latency_verdict(measured) == "fail"
