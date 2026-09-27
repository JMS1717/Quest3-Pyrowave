import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xrbench import wireless


def test_target_is_the_standard_adb_tcp_port():
    assert wireless.target("192.0.2.10") == "192.0.2.10:5555"
    # already a target: left alone rather than doubled
    assert wireless.target("192.0.2.10:5555") == "192.0.2.10:5555"


def test_wireless_is_preferred_when_both_are_attached():
    # the whole point: the cable is what drains the headset, so if a wireless device is available
    # the harness must not silently pick the USB one just because adb listed it first
    chosen = wireless.prefer_wireless(["<serial>", "192.0.2.10:5555"])
    assert chosen == "192.0.2.10:5555"


def test_usb_is_still_used_when_there_is_nothing_else():
    # a wired run is worse for the battery but better than no run at all
    assert wireless.prefer_wireless(["<serial>"]) == "<serial>"


def test_no_devices_gives_nothing():
    assert wireless.prefer_wireless([]) is None


def test_emulators_are_never_chosen():
    assert wireless.prefer_wireless(["emulator-5554"]) is None
    assert wireless.prefer_wireless(["emulator-5554", "<serial>"]) == "<serial>"


def test_device_lines_are_parsed_from_adb_output():
    out = ("List of devices attached\n"
           "<serial>\tdevice\n"
           "192.0.2.10:5555\tdevice\n"
           "192.0.2.200:5555\toffline\n"
           "emulator-5554\tdevice\n")
    assert wireless.parse_devices(out) == ["<serial>", "192.0.2.10:5555"]


def test_an_offline_entry_is_not_a_usable_device():
    # adb keeps stale entries after the headset sleeps or drops Wi-Fi; treating one as live is how
    # a sweep ends up failing every segment with no client
    assert wireless.parse_devices("List of devices attached\n1.2.3.4:5555\toffline\n") == []
