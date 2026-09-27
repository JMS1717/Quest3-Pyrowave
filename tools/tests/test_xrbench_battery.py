import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import battery

# Verbatim from buffering1/B-10/liveness/battery-start.txt. Note the contradiction
# this module exists to surface: status 2 is CHARGING and AC is true, yet the level was falling.
SAMPLE = """Current Battery Service state:
  AC powered: true
  USB powered: false
  Wireless powered: false
  Dock powered: false
  Max charging current: 0
  Max charging voltage: 0
  Charge counter: 1536000
  status: 2
  health: 2
  present: true
  level: 16
  scale: 100
  voltage: 0
  temperature: 430
  technology: Li-ion
"""


def test_parses_the_fields_that_matter():
    s = battery.parse(SAMPLE)
    assert s["level"] == 16
    assert s["temperature_c"] == 43.0
    assert s["ac_powered"] is True
    assert s["charging"] is True          # status 2 == BATTERY_STATUS_CHARGING


def test_a_malformed_dump_does_not_raise():
    # this runs around a benchmark segment; a odd dumpsys must not fail an otherwise good run
    s = battery.parse("nonsense\nlevel: not-a-number\n")
    assert s["level"] is None and s["charging"] is None


def test_drain_rate_is_positive_when_losing_charge():
    # 18% to 11% across 18 minutes, measured
    assert round(battery.drain_per_hour(18, 11, 18 * 60), 1) == 23.3


def test_drain_rate_is_negative_when_actually_gaining():
    assert battery.drain_per_hour(40, 50, 3600) == -10.0


def test_drain_rate_needs_a_real_interval():
    assert battery.drain_per_hour(18, 11, 0) is None
    assert battery.drain_per_hour(None, 11, 600) is None


def test_charging_while_draining_is_the_condition_worth_flagging():
    # the whole point. The headset reported CHARGING on AC for the entire day while losing ~20%/h,
    # because a USB 3 port supplies 4.5 W against a streaming draw of roughly 10-15 W.
    assert battery.is_losing_ground({"charging": True}, drain_per_hour=23.3) is True
    assert battery.is_losing_ground({"charging": True}, drain_per_hour=-5.0) is False
    # unplugged and draining is expected, not a fault to report
    assert battery.is_losing_ground({"charging": False}, drain_per_hour=23.3) is False


def test_the_floor_is_high_enough_to_finish_a_segment():
    # a segment plus its cool-down is a few minutes; at the measured ~20%/h that is ~2%, so the
    # floor needs margin above zero rather than being a last gasp
    assert battery.ABORT_BELOW_PCT >= 15


# --- wired adb is the configuration that displaces the battery pack ---


def test_a_tcp_serial_reads_as_wireless():
    assert battery.is_wireless_adb("192.0.2.10:5555") is True
    assert battery.is_wireless_adb("adb-<serial>-abc._adb-tls-connect._tcp") is True


def test_a_usb_serial_reads_as_wired():
    # the headset's hardware serial, which is what a USB connection reports
    assert battery.is_wireless_adb("<serial>") is False
    assert battery.is_wireless_adb("") is False


def test_wired_adb_is_worth_warning_about():
    # The tester's report: eight hours of normal use never dropped below 80%, but a benchmark day
    # with the USB-C cable attached lost ~20%/hour. The battery pack has its own dedicated
    # connector and is not displaced by the cable, so the mechanism is unknown -- but the
    # correlation is strong enough to warn on, and wireless adb avoids the question entirely.
    assert battery.wired_adb_warning("<serial>") is not None
    assert "battery" in battery.wired_adb_warning("<serial>").lower()
    assert battery.wired_adb_warning("192.0.2.10:5555") is None
