"""PC clock and reading analysis for the optical latency stamp (docs/OPTICAL-LATENCY.md).

show:     full-screen millisecond clock on the PC monitor, in the server stamp's time base
          (Windows performance counter, modulo 100000). Run it on the streaming PC.
analyze:  latency from CSV rows of "monitor,lens" values read off slow-motion video frames.

The stamp is drawn when the server composes a frame, so this measures composition-to-photon:
encode, transport, decode, presentation and display. It excludes game render and tracking
uplink. The monitor shows each clock value late by its own display lag, so pass that lag
with --monitor-lag-ms (it is added to every reading)."""
import argparse
import csv
import math
import sys
import time

MODULO = 100000


def clock_ms():
    """floor(QPC * 1000 / frequency) on Windows, matching FrameRender::DrawLatencyStamp."""
    if sys.platform == 'win32':
        import ctypes
        counter, frequency = ctypes.c_int64(), ctypes.c_int64()
        ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(counter))
        ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(frequency))
        return counter.value * 1000 // frequency.value
    # Not the server's time base; only for trying the window elsewhere.
    return int(time.perf_counter() * 1000)


def stamp_text(ms):
    return f'{ms % MODULO:05d}'


def latency_ms(monitor, lens, monitor_lag_ms=0.0):
    """Lens value is older than the monitor value; differences wrap at MODULO."""
    for value in (monitor, lens):
        if type(value) is not int or not 0 <= value < MODULO:
            raise ValueError(f'Readings must be integers from 0 to {MODULO - 1}')
    return (monitor - lens) % MODULO + monitor_lag_ms


def percentile(values, fraction):
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def summarize(pairs, monitor_lag_ms=0.0, max_plausible_ms=1000):
    values = [latency_ms(m, l, monitor_lag_ms) for m, l in pairs]
    kept = [v for v in values if v <= max_plausible_ms + monitor_lag_ms]
    if not kept:
        raise ValueError('No plausible readings')
    return {'readings': len(values), 'rejected': len(values) - len(kept),
            'median_ms': percentile(kept, 0.5), 'p90_ms': percentile(kept, 0.9),
            'min_ms': min(kept), 'max_ms': max(kept), 'monitor_lag_ms': monitor_lag_ms,
            'measures': 'composition-to-photon (excludes game render and tracking uplink)'}


def read_pairs(path):
    pairs = []
    with open(path, newline='') as handle:
        for row in csv.reader(handle):
            if not row or row[0].strip().lower() in ('monitor', '#') or row[0].startswith('#'):
                continue
            pairs.append((int(row[0]), int(row[1])))
    return pairs


def show():  # pragma: no cover - interactive window
    import tkinter
    winmm = None
    if sys.platform == 'win32':
        import ctypes
        winmm = ctypes.windll.winmm
        winmm.timeBeginPeriod(1)  # 1 ms timer for this process while the clock runs
    root = tkinter.Tk()
    root.configure(background='black')
    root.attributes('-fullscreen', True)
    root.bind('<Escape>', lambda _event: root.destroy())
    height = root.winfo_screenheight()
    label = tkinter.Label(root, text='00000', fg='white', bg='black',
                          font=('Consolas', -int(height * 0.3), 'bold'))
    label.pack(expand=True)
    tkinter.Label(root, text='PC clock (ms mod 100000)  Esc to exit', fg='gray60', bg='black',
                  font=('Consolas', -int(height * 0.03))).pack(side='bottom')

    def tick():
        label.configure(text=stamp_text(clock_ms()))
        root.after(1, tick)

    tick()
    try:
        root.mainloop()
    finally:
        if winmm is not None:
            winmm.timeEndPeriod(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('show')
    a = sub.add_parser('analyze')
    a.add_argument('readings', help='CSV of monitor,lens values (header and # lines ignored)')
    a.add_argument('--monitor-lag-ms', type=float, default=0.0)
    args = parser.parse_args()
    if args.cmd == 'show':
        show()
        return
    import json
    print(json.dumps(summarize(read_pairs(args.readings), args.monitor_lag_ms), indent=2))


if __name__ == '__main__':
    main()
