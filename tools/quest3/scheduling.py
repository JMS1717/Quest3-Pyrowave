"""Read optional frame-selection/CPU-loop windows from saved epoch logcat.

No headset access. These windows describe CPU scheduling, not optical display.
"""
import argparse
import json
import math
import re
from pathlib import Path
from .overlay import PREFIX

OUTCOMES = ('held', 'immediate', 'late', 'empty', 'copy_pending', 'no_decoder')
LOOP_FIELDS = ('wait_mean_ms', 'end_mean_ms', 'overlay_mean_cpu_ms',
               'wait_call_mean_ms', 'begin_mean_ms', 'render_mean_ms',
               'frame_work_mean_ms')


def summarize(lines, start, end, pid):
    if not all(math.isfinite(v) for v in (start, end)) or not 0 <= start < end:
        raise ValueError('Need a finite increasing epoch interval')
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError('Need the recorded client process ID')
    selection, loops = [], []
    malformed = foreign = 0
    for line in lines:
        marker = re.search(r'\[(Q3PW_SELECTION|Q3PW_LOOP)\]', line)
        if not marker:
            continue
        prefix = PREFIX.match(line)
        if not prefix:
            malformed += 1
            continue
        timestamp, process = float(prefix[1]), int(prefix[2])
        if process != pid:
            foreign += 1
            continue
        if not start <= timestamp <= end:
            continue
        values = dict(re.findall(r'(\w+)=([^\s]+)', line[marker.end():]))
        try:
            count_key = 'slots' if marker[1] == 'Q3PW_SELECTION' else 'samples'
            count = int(values[count_key])
            if not 0 < count <= 1_000_000:
                raise ValueError('Invalid sample count')
            row = {'logged_at_epoch_s': timestamp, count_key: count}
            if marker[1] == 'Q3PW_SELECTION':
                row.update({key: int(values[key]) for key in OUTCOMES})
                if any(row[key] < 0 for key in OUTCOMES) or sum(row[k] for k in OUTCOMES) != count:
                    raise ValueError('Outcomes must partition render slots')
                fields = ('select_mean_us', 'select_max_us')
            else:
                fields = tuple(k for k in LOOP_FIELDS if k in values)
                if not all(k in fields for k in LOOP_FIELDS[:3]):
                    raise ValueError('Missing loop measurements')
            row.update({key: float(values[key]) for key in fields})
            if any(not math.isfinite(row[k]) or row[k] < 0 for k in fields):
                raise ValueError('Invalid CPU duration')
            if marker[1] == 'Q3PW_SELECTION':
                if row['select_mean_us'] > row['select_max_us']:
                    raise ValueError('Mean exceeds maximum')
                selection.append(row)
            else:
                loops.append(row)
        except (ValueError, KeyError, OverflowError):
            malformed += 1
    usable = not malformed
    counts = {k: sum(w[k] for w in selection) for k in OUTCOMES}
    slots = sum(w['slots'] for w in selection)
    def weighted(rows, field, count):
        valid = [w for w in rows if field in w]
        n = sum(w[count] for w in valid)
        return sum(w[field] * w[count] for w in valid) / n if n and usable else None
    return {'status': 'invalid_records' if malformed else 'parsed' if selection else
            'legacy_loop_only' if loops else 'no_probe_records',
            'selection_windows': selection, 'loop_windows': loops,
            'logged_selection_slots': slots, 'outcome_counts': counts if usable else None,
            'selection_mean_cpu_us': weighted(selection, 'select_mean_us', 'slots'),
            'loop_mean_cpu_ms': {k: weighted(loops, k, 'samples') for k in LOOP_FIELDS},
            'malformed_records': malformed, 'foreign_process_records': foreign,
            'scope': 'Whole logged windows ending inside the interval; boundary windows can start before it. Counts are render selections, not unique frames or optical FPS. CPU phases overlap producer GPU work and must not be added to GPU decode time.',
            'performance_acceptance': False, 'optical_latency_measured': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--start', type=float, required=True)
    parser.add_argument('--end', type=float, required=True)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.log.read_text(encoding='utf-8', errors='replace').splitlines(),
                       args.start, args.end, args.pid)
    args.out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['status'] in ('parsed', 'legacy_loop_only') else 2


if __name__ == '__main__':
    raise SystemExit(main())
