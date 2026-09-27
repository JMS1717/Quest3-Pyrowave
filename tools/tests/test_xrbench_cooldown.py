import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import sweep as sw

# Verbatim from the 09:25 cool-down of refresh2, which burned its full 180 s cap
# chasing 72.0 C and finished at 72.6 -- the same temperature it had already reached at 90 s.
MEASURED_FLOOR_RUN = [74.7, 74.7, 77.4, 75.4, 74.3, 73.7, 73.0, 72.6,
                      76.0, 75.0, 73.3, 73.3, 73.7, 73.3, 73.0, 72.6]


def test_still_falling_is_not_the_floor():
    assert sw.at_thermal_floor([84.5, 80.1, 77.2, 75.0, 73.8, 72.9]) is False


def test_plateau_reads_as_the_floor():
    assert sw.at_thermal_floor([73.3, 73.0, 73.3, 73.1, 73.3, 73.0]) is True


def test_too_few_samples_is_never_the_floor():
    # concluding from two readings would stop cooling almost immediately every time
    assert sw.at_thermal_floor([73.0, 73.0]) is False
    assert sw.at_thermal_floor([]) is False


def test_an_upward_bounce_does_not_read_as_reheating():
    # the zone readings swing about 2 C sample to sample; taking the window minimum means a spike
    # cannot make a settled headset look like it is still cooling
    assert sw.at_thermal_floor([73.0, 73.2, 73.1, 76.0, 73.3, 73.2]) is True


def test_the_measured_run_would_have_stopped_early():
    # the whole point: this run waited out its 180 s cap to finish at 72.6 C, a temperature it had
    # already reached at 80 s. Samples are 10 s apart, so the index is the seconds saved.
    fired = next((n for n in range(1, len(MEASURED_FLOOR_RUN) + 1)
                  if sw.cooled_enough(1, MEASURED_FLOOR_RUN[:n])), None)
    assert fired is not None, "never stopped cooling"
    assert fired <= 8, f"stopped at sample {fired}, barely better than the 16-sample cap"


def test_a_spike_does_not_release_a_headset_that_is_still_cooling():
    # a single high reading lands in the recent window and can make a falling series look
    # plateaued. Comparing window minima is what prevents it -- this one is shedding ~4 C per
    # sample and must keep going despite the 95.0.
    assert sw.cooled_enough(1, [90.0, 88.0, 86.0, 95.0, 84.0, 82.0]) is False


def test_a_genuine_plateau_above_target_still_stops():
    # not a compromise: if the temperature has stopped falling, the cap would expire at this same
    # reading, so waiting it out costs minutes and buys nothing. The segment is recorded with its
    # thermal data either way, and analysis treats a hot start as suspect.
    assert sw.cooled_enough(1, [84.0, 83.5, 84.2, 83.8, 84.1, 83.6]) is True


def test_severe_thermal_status_blocks_stopping_however_cold_the_zones_read():
    assert sw.cooled_enough(3, [60.0, 60.0, 60.0, 60.0, 60.0, 60.0]) is False


def test_missing_thermal_data_falls_back_to_status_alone():
    # some devices give no usable zones; the cool-down must not then loop for its whole cap
    assert sw.cooled_enough(1, []) is True


def test_the_default_target_sits_above_the_measured_floor():
    # the bug being fixed: 72.0 C was below anything the headset reaches with SteamVR up, so the
    # threshold could never be met and every cool-down ran the full cap
    assert min(MEASURED_FLOOR_RUN) > 72.0
    assert sw.COOLDOWN_TARGET_C >= min(MEASURED_FLOOR_RUN)
