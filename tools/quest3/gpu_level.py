"""Explicit, reversible Quest 3 GPU level override (`debug.oculus.gpuLevel`).

Under its BOOST performance request the client gets GPU level 4, which the governor caps at 640 MHz
(and lowers at light load). Level 7 pins the Adreno 740 at its 690 MHz maximum. October 6 screens:
+4 to +9 fresh FPS where decode is GPU-bound (2560x2720 at 120 Hz, 1664x1760 at 207 Hz, 240 Hz),
nothing at native 120 Hz. Thermal protection is untouched and still throttles. Set it before
opening the app; restore it afterwards. Sustained thermals are not established.
"""
import argparse
import json
from pathlib import Path
from .refresh_scaling import Device

PROPERTY = 'debug.oculus.gpuLevel'
LEVELS = range(0, 8)


def enable(device, state, level):
    if int(level) not in LEVELS:
        raise ValueError(f'GPU level must be 0-7, got {level}')
    original = {'device': device.run('get-serialno').strip(), 'value': device.getprop(PROPERTY)}
    state.parent.mkdir(parents=True, exist_ok=True)
    with state.open('x', encoding='utf-8') as out:  # never overwrite the only rollback state
        json.dump(original, out, indent=2)
    device.run('shell', f"setprop {PROPERTY} '{int(level)}'")
    if device.getprop(PROPERTY) != str(int(level)):
        device.run('shell', f"setprop {PROPERTY} '{original['value']}'")
        raise RuntimeError('GPU level write rejected; original restored')
    return device.getprop(PROPERTY)


def restore(device, state):
    original = json.loads(state.read_text(encoding='utf-8'))
    if original.get('device') != device.run('get-serialno').strip():
        raise ValueError('Rollback state belongs to a different device')
    value = original['value']
    if value and not value.isdigit():
        raise ValueError('Unexpected saved value')
    device.run('shell', f"setprop {PROPERTY} '{value}'")
    if device.getprop(PROPERTY) != value:
        raise RuntimeError('GPU level restore rejected')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--adb', default='adb')
    parser.add_argument('--serial', help='Pin one device (adb -s)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    on = sub.add_parser('enable')
    on.add_argument('--state', required=True)
    on.add_argument('--level', type=int, default=7)
    sub.add_parser('restore').add_argument('--state', required=True)
    args = parser.parse_args()
    device = Device(args.adb, args.serial)
    if device.getprop('ro.product.model') != 'Quest 3':
        raise RuntimeError('Expected Quest 3')
    if args.command == 'enable':
        enable(device, Path(args.state), args.level)
    elif args.command == 'restore':
        restore(device, Path(args.state))
    print(json.dumps({'action': args.command, PROPERTY: device.getprop(PROPERTY)}))


if __name__ == '__main__':
    main()
