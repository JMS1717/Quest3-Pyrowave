"""Summarize .24 CPU-wall overlay diagnostics from a saved epoch-format logcat.

No device access. Input must cover the capture interval and be tied to the client
process; log absence is never evidence of zero overlay overhead.
"""
import argparse
import json
import math
import re
from pathlib import Path
from .bench import distribution

PREFIX = re.compile(r'^\s*(\d+(?:\.\d+)?)\s+(\d+)\s+\d+\s+[VDIWEF]\s+')
DRAW = ('text_cpu_ms', 'acquire_wait_cpu_ms', 'renderer_submit_cpu_ms', 'release_cpu_ms')
LOOP = ('overlay_mean_cpu_ms', 'overlay_max_cpu_ms')
MARKERS = ('Q3PW_OVERLAY_DRAW', 'Q3PW_LOOP', 'Q3PW_OVERLAY_CONTROL', 'Q3PW_OVERLAY')


def summarize(lines, start, end, pid):
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        raise ValueError('Need a finite increasing epoch capture interval')
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError('Need the recorded client process ID')
    draws = []
    loops = []
    changes = []
    previous_control = None
    previous_control_time = -1
    invalid = 0
    foreign = 0
    for line in lines:
        marker = re.search(r'\[(' + '|'.join(MARKERS) + r')\]', line)
        if not marker:
            continue
        prefix = PREFIX.match(line)
        if not prefix:
            invalid += 1
            continue
        timestamp, process = float(prefix[1]), int(prefix[2])
        if process != pid:
            foreign += 1
            continue
        if not math.isfinite(timestamp):
            invalid += 1
            continue
        values = dict(re.findall(r'(\w+)=([^\s]+)', line[marker.end():]))
        name = marker[1]
        if name == 'Q3PW_OVERLAY_CONTROL' and timestamp < start:
            if timestamp >= previous_control_time:
                previous_control = values.get('mode')
                previous_control_time = timestamp
            continue
        if not start <= timestamp <= end:
            continue
        if name in ('Q3PW_OVERLAY_CONTROL', 'Q3PW_OVERLAY'):
            changes.append({'epoch_s': timestamp, 'marker': name, 'mode': values.get('mode'),
                            'visible': values.get('visible'), 'forced': values.get('forced')})
            continue
        fields = DRAW if name == 'Q3PW_OVERLAY_DRAW' else LOOP
        try:
            row = {key: float(values[key]) for key in fields}
            if not all(math.isfinite(v) and v >= 0 for v in row.values()):
                raise ValueError('Invalid timing')
            if name == 'Q3PW_LOOP':
                samples = int(values['samples'])
                if samples <= 0 or row[LOOP[1]] < row[LOOP[0]]:
                    raise ValueError('Invalid loop window')
                row['samples'] = samples
                loops.append(row)
            else:
                draws.append(row)
        except (KeyError, ValueError, OverflowError):
            invalid += 1
    samples = sum(row['samples'] for row in loops)
    return {
        'status': 'invalid_records' if invalid else ('parsed' if draws or loops else 'no_timing_records'),
        'interval_epoch_s': [start, end],
        'invalid_marker_records': invalid, 'ignored_foreign_process_records': foreign,
        'control_before_interval': previous_control, 'visibility_records_in_interval': changes,
        'redraws': len(draws), 'redraw_cpu_ms': {field: distribution([r[field] for r in draws]) for field in DRAW},
        'loop_windows': len(loops), 'loop_frames': samples,
        'overlay_update_mean_cpu_ms': sum(r[LOOP[0]] * r['samples'] for r in loops) / samples if samples else None,
        'overlay_update_max_cpu_ms': max((r[LOOP[1]] for r in loops), default=None),
        'scope': 'CPU-wall diagnostics only. Loop samples describe windows ending in this interval and can overlap its start. Redraw samples describe updates logged in this interval. No GPU completion, FPS, thermal, visibility acceptance or performance gain is inferred. Missing samples remain unknown.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--start', type=float, required=True, help='Capture start Unix seconds')
    parser.add_argument('--end', type=float, required=True, help='Capture end Unix seconds')
    parser.add_argument('--pid', type=int, required=True, help='Client PID recorded for this capture')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.log.read_text(encoding='utf-8').splitlines(), args.start, args.end, args.pid)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return 0 if result['status'] == 'parsed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
