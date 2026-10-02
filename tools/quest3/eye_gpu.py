"""Read opt-in .25 GPU eye-draw timers from saved epoch logcat; no device access.

Percentiles are per query window. Logs cannot reconstruct pooled percentiles.
"""
import argparse
import json
import math
import re
from pathlib import Path
from .overlay import PREFIX


def summarize(lines, start, end, pid):
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        raise ValueError('Need a finite increasing epoch interval')
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError('Need the recorded client process ID')
    windows, setup = [], None
    setup_time = -1
    malformed = foreign = empty = invalid = skipped = 0
    disabled = False
    for line in lines:
        marker = re.search(r'\[(Q3PW_EYE_GPU(?:_SETUP)?)\]', line)
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
        if not math.isfinite(timestamp) or timestamp > end:
            continue
        values = dict(re.findall(r'(\w+)=([^\s]+)', line[marker.end():]))
        if marker[1].endswith('_SETUP'):
            if timestamp < start and timestamp >= setup_time:
                setup, setup_time = values, timestamp
            if start <= timestamp <= end and values.get('active') != '1':
                disabled = True
            continue
        if timestamp < start:
            continue
        try:
            counts = {key: int(values[key]) for key in ('samples', 'invalid', 'skipped')}
            if any(v < 0 or v > 1_000_000_000 for v in counts.values()):
                raise ValueError('Invalid counter')
            if counts['samples'] == 0:
                if values.get('elapsed_ms') != 'unknown':
                    raise ValueError('Empty measurement is unknown')
                empty += 1
            else:
                row = {key: float(values[key]) for key in ('mean_ms', 'p50_ms', 'p95_ms', 'max_ms')}
                if not all(math.isfinite(v) and v > 0 for v in row.values()):
                    raise ValueError('Invalid GPU elapsed time')
                if not (row['p50_ms'] <= row['p95_ms'] <= row['max_ms'] and row['mean_ms'] <= row['max_ms']):
                    raise ValueError('Inconsistent statistics')
                row.update(counts)
                windows.append(row)
            invalid += counts['invalid']
            skipped += counts['skipped']
        except (KeyError, ValueError, OverflowError):
            malformed += 1
    active = setup is not None and setup.get('active') == '1'
    samples = sum(w['samples'] for w in windows)
    status = ('disabled_during_interval' if disabled else 'setup_not_verified' if not active
              else 'invalid_records' if malformed else 'parsed' if samples else 'no_valid_samples')
    usable = status == 'parsed'
    return {'status': status, 'setup_before_interval': setup, 'windows': windows,
            'valid_samples': samples, 'empty_windows': empty,
            'invalid_gpu_queries': invalid, 'skipped_measurements': skipped,
            'malformed_records': malformed, 'foreign_process_records': foreign,
            'gpu_draw_mean_ms': sum(w['mean_ms'] * w['samples'] for w in windows) / samples if usable else None,
            'gpu_draw_max_ms': max(w['max_ms'] for w in windows) if usable else None,
            'scope': 'two GLES eye draws; window percentiles are not pooled percentiles',
            'performance_acceptance': False, 'optical_latency_measured': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('log', type=Path)
    p.add_argument('--start', type=float, required=True)
    p.add_argument('--end', type=float, required=True)
    p.add_argument('--pid', type=int, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    result = summarize(args.log.read_text(encoding='utf-8', errors='replace').splitlines(),
                       args.start, args.end, args.pid)
    args.out.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))
    return 0 if result['status'] == 'parsed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
