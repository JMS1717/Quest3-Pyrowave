"""Read the headset's battery state, and notice when "charging" is not actually charging.

The Galaxy XR spent a whole day reporting `status: 2` (CHARGING) with `AC powered: true` while its
level fell steadily -- 18% to 11% in eighteen minutes, and 80% to 50% over eighty-eight minutes in
an earlier session at a cool 30-33 C battery. So this is not the battery refusing charge when hot:
it is a supply that cannot keep up with the draw.

The cause is not yet known. Power reaches the headset from a 65 W wall charger via its external
battery pack, with a separate USB connection to the PC for adb -- so the supply is not an
undersized PC port, and with 65 W upstream it should charge comfortably. Every sample reports
`Max charging current: 0` and `voltage: 0`, which may be unpopulated fields on this device or may
mean power delivery is not negotiating. The open question is whether the pack can meet peak
streaming draw, or whether the chain above it is at fault; sampling idle versus streaming
separates those.

Android reports "charging" for any powered connection; it does not mean the level is rising. That
distinction is what cost a day's battery, so the harness now measures it rather than trusting the
status flag.
"""

# Android BatteryManager.BATTERY_STATUS_CHARGING.
_STATUS_CHARGING = 2

# Stop a sweep below this. A segment plus its cool-down is a few minutes, which at the measured
# ~20%/hour is about 2%, so the floor carries real margin rather than being a last gasp -- and a
# headset that dies mid-segment costs the whole run, not just that cell.
ABORT_BELOW_PCT = 20

# Treat the supply as losing ground above this. Small positive rates are sampling noise: `level`
# is an integer percent, so a short segment can show 1% of movement either way.
LOSING_GROUND_PCT_PER_HOUR = 2.0


def _int(text):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def parse(text):
    """Summarise `dumpsys battery`. Fields are None when absent or malformed rather than raising:
    this runs around a benchmark segment and must not fail an otherwise good run."""
    fields = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()

    level = _int(fields.get("level"))
    status = _int(fields.get("status"))
    millidegrees = _int(fields.get("temperature"))
    return {
        "level": level,
        "status": status,
        "charging": None if status is None else status == _STATUS_CHARGING,
        "ac_powered": fields.get("ac powered") == "true" if "ac powered" in fields else None,
        "usb_powered": fields.get("usb powered") == "true" if "usb powered" in fields else None,
        # tenths of a degree in dumpsys
        "temperature_c": None if millidegrees is None else millidegrees / 10.0,
    }


def drain_per_hour(before_level, after_level, seconds):
    """Percent of charge lost per hour. Negative means the battery actually gained.

    None when there is nothing to measure -- a missing reading, or an interval too short to divide
    by."""
    if before_level is None or after_level is None or not seconds:
        return None
    return (before_level - after_level) * 3600.0 / seconds


def is_losing_ground(state, drain_per_hour):
    """True when the headset says it is charging but the level is going down anyway.

    Reported separately from ordinary discharge because the two call for different responses: on
    battery, a falling level is expected; on a charger it means the supply is undersized and no
    amount of waiting will recover it."""
    if not state.get("charging") or drain_per_hour is None:
        return False
    return drain_per_hour > LOSING_GROUND_PCT_PER_HOUR


def is_wireless_adb(serial):
    """Whether an adb serial is a network connection rather than a USB one.

    `adb connect` yields "host:port"; mDNS pairing yields a name containing "_adb-tls". A USB
    device reports its bare hardware serial."""
    if not serial:
        return False
    return ":" in serial or "_adb" in serial


def wired_adb_warning(serial):
    """A warning when the headset is on the USB-C cable, or None when it is wireless.

    Measured: eight hours of ordinary use never took the headset below 80%, while a
    benchmark day with the cable attached lost about 20% an hour and eventually killed it
    mid-sweep. The external battery pack has its own dedicated connector and is not displaced by
    the cable, so *why* the cable costs this much is not established -- the leading guess is that
    the headset switches its power input to USB-C once a cable is present, abandoning the far
    stronger pack.

    The mechanism does not have to be settled to act on it: `adb connect <ip>:5555` keeps the
    harness working with no cable attached, and Wi-Fi already beat every USB transport measured
    (1124 Mbps against RNDIS 453 and an adb tunnel at 1102 with 60 ms of network time), so nothing
    of value is given up."""
    if is_wireless_adb(serial):
        return None
    return (f"adb is connected over USB ({serial}). A benchmark day on the cable drained the "
            "battery ~20%/hour and killed the headset mid-sweep, where ordinary use holds above "
            "80% for eight hours. Prefer 'adb tcpip 5555' and 'adb connect <ip>:5555'.")
