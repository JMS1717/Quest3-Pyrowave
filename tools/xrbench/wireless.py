"""Run the benchmark over Wi-Fi adb, so the headset never needs the USB-C cable.

Two reasons, and the first is the expensive one.

**Battery.** Measured: ordinary use holds the headset above 80% for eight hours, while
a benchmark day with the cable attached lost about 20% an hour and killed it mid-sweep, taking
four runs down. The external battery pack has its own dedicated connector and is not displaced by
the cable, so the mechanism is not established -- but the correlation is strong and the cost is a
dead headset, and going wireless sidesteps the question entirely.

**Reach.** The PC is in another room. Wired adb means walking there to plug in; wireless
means the sweep can be driven from anywhere.

Nothing measurable is given up. Wi-Fi beat every USB transport tested -- 1124 Mbps against RNDIS's
453 and an adb tunnel's 1102 with 60 ms of network time -- so only the `rndis` and `adb` transport
arms become unavailable, and both were already dead ends.

One-time setup, which does need a cable once:

    adb tcpip 5555                      # or enable Wireless debugging in Developer options
    adb connect <headset-ip>:5555

`adb tcpip` is forgotten on reboot; the Developer-options "Wireless debugging" toggle survives it.
"""

PORT = 5555


def target(ip):
    """`ip:5555`, or the value unchanged if it already carries a port."""
    return ip if ":" in ip else f"{ip}:{PORT}"


def is_wireless(serial):
    """Whether an adb serial came from a network connection rather than USB."""
    return bool(serial) and (":" in serial or "_adb" in serial)


def parse_devices(adb_devices_output):
    """Usable device serials from `adb devices`, newest-state only.

    Entries in any state other than `device` are dropped. adb keeps stale rows after the headset
    sleeps or drops Wi-Fi, and treating an `offline` row as live is how a sweep ends up failing
    every segment with no client attached. Emulators are excluded here as well."""
    serials = []
    for line in adb_devices_output.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 2 and parts[1] == "device" and not parts[0].startswith("emulator-"):
            serials.append(parts[0])
    return serials


def prefer_wireless(serials):
    """Pick a device, preferring a wireless one over the cable.

    Order matters: when both are attached, adb may list either first, and silently choosing the
    USB one is what costs the battery. A wired device is still returned when it is all there is --
    a wired run beats no run."""
    usable = [s for s in serials if not s.startswith("emulator-")]
    if not usable:
        return None
    return next((s for s in usable if is_wireless(s)), usable[0])
