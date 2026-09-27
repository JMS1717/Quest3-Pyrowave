"""UDP loss / jitter / CPU test: the arithmetic the decision rests on.

The question is whether PyroWave can go UDP on this Wi-Fi: what fraction of MTU-sized datagrams
the headset loses at 100/300/600 Mbps, how much its CPU spends receiving them, and how much
jitter the link adds. The receiver on the headset reports raw counters; everything below turns
those into the numbers the decision is made on, and must be right before any headset time is
spent.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import udptest as ut


def test_packet_rate_follows_bits_on_the_wire():
    # 600 Mbps of 1400-byte datagrams is ~53.6k packets/s -- the number that worries the headset CPU
    assert round(ut.packets_per_second(600, 1400)) == 53571
    assert round(ut.packets_per_second(100, 1400)) == 8929


def test_packet_rate_rejects_nonsense():
    import pytest
    with pytest.raises(ValueError):
        ut.packets_per_second(0, 1400)
    with pytest.raises(ValueError):
        ut.packets_per_second(100, 0)


def test_burst_schedule_hits_the_rate_with_one_millisecond_ticks():
    # Windows timers wake ~every ms; the sender sends a burst per tick. 53.6 packets per ms
    # cannot be sent, so the schedule must alternate 53 and 54 and average correctly.
    per_tick = ut.burst_sizes(ut.packets_per_second(600, 1400), tick_s=0.001, ticks=1000)
    assert len(per_tick) == 1000
    assert set(per_tick) <= {53, 54}
    assert abs(sum(per_tick) - 53571) <= 1


def test_loss_is_expected_minus_received_not_gaps_seen():
    # 1000 sent (seq 0..999), 990 received, last seen 999 -> 1.0 %. Trailing loss (the tail
    # never arriving) must count too, so the sender's count is the denominator, not max seq.
    report = {"received": 990, "max_seq": 999, "bytes": 990 * 1400}
    assert ut.loss_percent(report, sent=1000) == 1.0


def test_loss_cannot_go_negative_on_duplicates():
    report = {"received": 1003, "max_seq": 999, "bytes": 0}
    assert ut.loss_percent(report, sent=1000) == 0.0


def test_headset_cpu_percent_from_proc_stat_deltas():
    # /proc/stat "cpu" lines: user nice system idle iowait irq softirq steal
    before = "cpu  1000 0 500 8000 100 50 50 0"
    after = "cpu  1600 0 900 8200 100 150 250 0"
    # busy delta = (1600-1000)+(900-500)+(150-50)+(250-50) = 1300; total delta = 1300+200 = 1500
    assert round(ut.cpu_percent(before, after), 2) == round(100 * 1300 / 1500, 2)


def test_headset_cpu_percent_handles_no_time_passing():
    line = "cpu  1000 0 500 8000 100 50 50 0"
    assert ut.cpu_percent(line, line) == 0.0


def test_jitter_summary_uses_arrival_minus_send_deltas():
    # The receiver reports the distribution of (arrival gap - send gap) in microseconds. Clocks
    # on the two machines differ, so only the *deltas* mean anything; the summary must not try
    # to read an absolute one-way latency out of it.
    report = {"jitter_us_p50": 120, "jitter_us_p99": 2400, "jitter_us_max": 9800}
    s = ut.summarise({"received": 1000, "max_seq": 999, "bytes": 1400000,
                      "cpu_user_us": 300000, "cpu_sys_us": 200000, "seconds": 10.0, **report},
                     sent=1000, mbps=100, headset_cpu_percent=12.5)
    assert s["loss_percent"] == 0.0
    assert s["jitter_ms_p99"] == 2.4
    assert s["receiver_cpu_percent_of_one_core"] == 5.0   # 0.5 s of CPU over 10 s
    assert s["headset_cpu_percent"] == 12.5
    assert "one_way_ms" not in s


# ---- frames and deadlines ----
# Generic jitter is not the question. The question is whether every packet a frame needs arrives
# before that frame's deadline; a packet for a frame whose deadline has passed should be dropped,
# not recovered. The receiver keys packets by frame and measures each packet's arrival age
# relative to the frame's first packet -- the only clock a receiver actually has.

def test_packets_per_frame_is_the_frame_budget_split_into_datagrams():
    # 600 Mbps at 90 fps is 833,333 bytes a frame: 596 datagrams of 1400 (last one short)
    assert ut.packets_per_frame(600, 90, 1400) == 596
    assert ut.packets_per_frame(100, 90, 1400) == 100


def test_packets_per_frame_never_zero():
    assert ut.packets_per_frame(1, 90, 1400) == 1


def test_frame_summary_reads_completeness_against_the_deadline():
    report = {"received": 5960, "max_seq": 5959, "bytes": 5960 * 1400, "seconds": 10.0,
              "cpu_user_us": 0, "cpu_sys_us": 0,
              "frames_seen": 900, "frames_complete": 897, "frames_complete_on_time": 880,
              "packets_late": 42, "packets_for_closed_frames": 3,
              "assembly_us_p50": 1800, "assembly_us_p99": 9500, "assembly_us_max": 31000,
              "deadline_us": 11111}
    s = ut.summarise(report, sent=5960, mbps=600, headset_cpu_percent=30.0)
    assert s["frames_on_time_percent"] == round(100 * 880 / 900, 2)
    assert s["frames_incomplete"] == 3
    assert s["packets_late"] == 42
    assert s["assembly_ms_p99"] == 9.5
    assert s["deadline_ms"] == 11.111


def test_frame_summary_survives_a_report_from_the_old_receiver():
    # a report without frame fields must still summarise, not crash the table
    report = {"received": 10, "max_seq": 9, "bytes": 14000, "seconds": 1.0,
              "cpu_user_us": 0, "cpu_sys_us": 0}
    s = ut.summarise(report, sent=10, mbps=1, headset_cpu_percent=0.0)
    assert s["frames_on_time_percent"] is None


# ---- the radio must be awake ----
# A bare receiver holds no Wi-Fi lock, so between 11 ms bursts the radio may doze; streaming
# clients (ALVR, vrlink, our own receiver app) hold WIFI_MODE_FULL_LOW_LATENCY and never let it.
# The test must run under the same condition, and prove it from dumpsys rather than assume it.

DUMPSYS_NO_LOCK = """Locks held:
Locks acquired: 0 full high perf, 9 full low latency
Locks released: 0 full high perf, 9 full low latency
Locks held:
Multicast Locks held:
"""

DUMPSYS_LOCK = """Locks held:
WifiLock{xrwired-receiver type=4 uid=10159 workSource=WorkSource{10159}}
Locks acquired: 0 full high perf, 10 full low latency
Locks released: 0 full high perf, 9 full low latency
Locks held:
WifiLock{xrwired-receiver type=4 uid=10159 workSource=WorkSource{10159}}
Multicast Locks held:
"""


def test_low_latency_lock_detected_by_tag_and_type():
    assert ut.wifi_low_latency_lock_held(DUMPSYS_LOCK, "xrwired-receiver") is True


def test_no_lock_when_none_held():
    assert ut.wifi_low_latency_lock_held(DUMPSYS_NO_LOCK, "xrwired-receiver") is False


def test_high_perf_lock_is_not_low_latency():
    # type=3 is WIFI_MODE_FULL_HIGH_PERF; the streaming clients use type=4 and so must the test
    text = DUMPSYS_LOCK.replace("type=4", "type=3")
    assert ut.wifi_low_latency_lock_held(text, "xrwired-receiver") is False


# ---- separating the sender's own spread from what the path added ----
# "Assembly" (first to last arrival of a frame) contains the time the sender took to emit the
# burst. Python sendto on Windows costs tens of microseconds a call, so at 600 Mbps a 596-packet
# frame may take longer to emit than a frame period. The receiver has every packet's send time,
# so it reports the per-frame send span and the summary subtracts it.

def test_added_delay_is_assembly_minus_sender_span():
    report = {"received": 100, "max_seq": 99, "bytes": 140000, "seconds": 1.0,
              "cpu_user_us": 0, "cpu_sys_us": 0,
              "frames_seen": 90, "frames_complete": 90, "frames_complete_on_time": 80,
              "packets_late": 0, "packets_for_closed_frames": 0,
              "assembly_us_p50": 3000, "assembly_us_p99": 12000, "assembly_us_max": 40000,
              "send_span_us_p50": 2500, "send_span_us_p99": 4000, "send_span_us_max": 6000,
              "deadline_us": 11111, "kernel_timestamps": 1}
    s = ut.summarise(report, sent=100, mbps=100, headset_cpu_percent=0.0)
    assert s["send_span_ms_p99"] == 4.0
    assert s["path_added_ms_p99"] == 8.0      # 12.0 - 4.0: what the air, driver and kernel added
    assert s["kernel_timestamps"] is True


def test_added_delay_absent_when_receiver_did_not_report_spans():
    report = {"received": 1, "max_seq": 0, "bytes": 1400, "seconds": 1.0, "cpu_user_us": 0, "cpu_sys_us": 0}
    s = ut.summarise(report, sent=1, mbps=1, headset_cpu_percent=0.0)
    assert s["path_added_ms_p99"] is None


# ---- the receiver thread itself ----
# With kernel timestamps the 100 Mbps link went from "57 % of frames on time" to 99.6 %: the
# packets were there, the app thread was not running. That lag between the kernel receiving a
# packet and the application reading it is a property of the headset's scheduler and the
# thread's priority, and a real transport has to plan for it, so it is reported on its own.

def test_app_lag_is_reported_in_milliseconds():
    report = {"received": 1, "max_seq": 0, "bytes": 1400, "seconds": 1.0, "cpu_user_us": 0, "cpu_sys_us": 0,
              "app_lag_us_p50": 80, "app_lag_us_p99": 12700, "app_lag_us_max": 198000, "rt_scheduling": 0}
    s = ut.summarise(report, sent=1, mbps=1, headset_cpu_percent=0.0)
    assert s["app_lag_ms_p99"] == 12.7
    assert s["app_lag_ms_max"] == 198.0
    assert s["rt_scheduling"] is False
