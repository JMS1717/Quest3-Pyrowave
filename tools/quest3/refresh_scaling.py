"""Explicit, reversible Quest 3 developer display-scaling experiment (240 Hz).

Measured October 6, 2026: the two properties are applied when they change while the display is
awake. A change made while the headset sleeps is not picked up later, and SurfaceFlinger's active
mode can report 240 while the panel stays at 120. `enable` and `restore` therefore wake the headset,
change the properties (stepping through the opposite values if they already hold the target), and
confirm the panel's mode from the kernel's `dsi_display_set_mode` log line. In the scaled mode the
panel runs 3104x1664 (1552x1664 per eye) at 240 Hz; the 4128x2208 modes stop at 207 Hz.
"""
import argparse
import json
import re
import time
from pathlib import Path
from .bench import adb_run

PROPERTIES = ('debug.oculus.forceDisplayScaling', 'debug.oculus.refreshRate')
# Rates above this exist only in the scaled panel mode.
NATIVE_MAX_HZ = 207.5
DSI = re.compile(r'dsi_display_set_mode.*hactive=(\d+), vactive=(\d+), fps=(\d+)')


def parse_panel_mode(text):
    """(width, height, fps) from the last kernel `dsi_display_set_mode` line in `text`, or None."""
    matches = DSI.findall(text)
    return tuple(int(x) for x in matches[-1]) if matches else None


class Device:
    def __init__(self, adb, serial=None):
        self.adb, self.serial = adb, serial

    def run(self, *args):
        return adb_run(self.adb, *(('-s', self.serial) if self.serial else ()), *args)

    def shell(self, command):
        return self.run('shell', command)

    def getprop(self, name):
        return self.run('shell', 'getprop', name).strip()

    def panel_mode(self):
        return parse_panel_mode(self.shell('logcat -d -t 200000 | grep dsi_display_set_mode | tail -1'))

    def awake(self):
        return 'mWakefulness=Awake' in self.shell('dumpsys power | grep mWakefulness=')


def values(device):
    return {name: device.getprop(name) for name in PROPERTIES}


def set_property(device, name, value):
    # Only fixed property names and validated numeric/empty values reach the remote shell.
    if name not in PROPERTIES or (value and not value.isdigit()):
        raise ValueError('Unexpected property/value; refusing remote shell command')
    device.run('shell', f"setprop {name} '{value}'")
    if device.getprop(name) != value:
        raise RuntimeError(f'Property write rejected: {name}')


def apply_mode(device, scaling, rate, accept, attempts=3, timeout_s=8.0, sleep=None, clock=None):
    """Leave the properties at (scaling, rate), changing them while awake until the panel's mode
    satisfies `accept(fps)`. Returns the last panel mode."""
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    mode = None
    device.shell('am broadcast -a com.oculus.vrpowermanager.automation_enable')
    try:
        for attempt in range(attempts):
            device.shell('am broadcast -a com.oculus.vrpowermanager.prox_close --ei duration 30000')
            device.shell('input keyevent KEYCODE_WAKEUP')
            deadline = clock() + 10
            while not device.awake() and clock() < deadline:
                sleep(0.3)
            sleep(2.0)
            if attempt or values(device) == dict(zip(PROPERTIES, (scaling, rate))):
                # Only a change is applied: step through the opposite state first.
                set_property(device, PROPERTIES[0], '' if scaling == '1' else '1')
                set_property(device, PROPERTIES[1], '120' if rate == '240' else '240')
                sleep(3.0)
            set_property(device, PROPERTIES[0], scaling)
            set_property(device, PROPERTIES[1], rate)
            deadline = clock() + timeout_s
            while clock() < deadline:
                mode = device.panel_mode()
                if mode is not None and accept(mode[2]):
                    return mode
                sleep(0.5)
        return mode
    finally:
        device.shell('am broadcast -a com.oculus.vrpowermanager.automation_disable')


def enable(device, state, rate='240'):
    original = {'model': device.getprop('ro.product.model'), 'device': device.run('get-serialno').strip(),
                'properties': values(device)}
    # Exclusive create: never overwrite the only rollback state.
    state.parent.mkdir(parents=True, exist_ok=True)
    with state.open('x', encoding='utf-8') as out:
        json.dump(original, out, indent=2)
    mode = apply_mode(device, '1', rate, lambda fps: abs(fps - float(rate)) < 0.5)
    if mode is None or abs(mode[2] - float(rate)) >= 0.5:
        back = original['properties']
        apply_mode(device, back[PROPERTIES[0]], back[PROPERTIES[1]], lambda fps: fps <= NATIVE_MAX_HZ)
        raise RuntimeError(f'Panel did not enter {rate} Hz (mode {mode}); properties restored')
    return mode


def restore(device, state):
    original = json.loads(state.read_text(encoding='utf-8'))
    if original.get('device') != device.run('get-serialno').strip() or set(original['properties']) != set(PROPERTIES):
        raise ValueError('Rollback state belongs to a different device or property set')
    back = original['properties']
    if back.get(PROPERTIES[0]) == '1':
        for name, value in back.items():
            set_property(device, name, value)
        return device.panel_mode()
    mode = apply_mode(device, back[PROPERTIES[0]], back[PROPERTIES[1]], lambda fps: fps <= NATIVE_MAX_HZ)
    if mode is None or mode[2] > NATIVE_MAX_HZ:
        raise RuntimeError(f'Properties restored but the panel reports {mode}; reboot the headset to clear it')
    return mode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', default='adb')
    parser.add_argument('--serial', help='Pin one device (adb -s)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    for action in ('enable', 'restore'):
        sub.add_parser(action).add_argument('--state', required=True)
    args = parser.parse_args()
    device = Device(args.adb, args.serial)
    model = device.getprop('ro.product.model')
    if model != 'Quest 3':
        raise RuntimeError(f'Expected Quest 3, found {model}')
    mode = None
    if args.command == 'enable':
        mode = enable(device, Path(args.state))
    elif args.command == 'restore':
        mode = restore(device, Path(args.state))
    mode = mode or device.panel_mode()
    print(json.dumps({'model': model, 'properties': values(device), 'action': args.command,
                      'panel': None if mode is None else {'width': mode[0], 'height': mode[1], 'hz': mode[2]}}))


if __name__ == '__main__':
    main()
