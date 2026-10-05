"""Validate saved producer scheduling observations; no hardware access."""
import argparse
import json
import math
import re
from pathlib import Path
from .runtime_logs import PREFIX

MARKER = '[Q3PW_PRODUCER_PROBE]'
FIELDS = ('calls', 'pending', 'ready_before', 'arrived_during', 'arrived_after',
          'overlap_sum_us', 'overlap_max_us', 'call_sum_us', 'headroom_at_snapshot')
RECORD = re.compile(' '.join(f'{name}=(\\d+)' for name in FIELDS))


def parse(lines, start, end, pid):
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        raise ValueError('Need a finite increasing epoch interval')
    if type(pid) is not int or not 0 < pid < 2**31:
        raise ValueError('Need the recorded client PID')
    total = dict.fromkeys(FIELDS, 0)
    records = malformed = 0
    for line in lines:
        if MARKER not in line:
            continue
        prefix = PREFIX.match(line)
        if not prefix:
            malformed += 1
            continue
        if int(prefix[2]) != pid or not start <= float(prefix[1]) < end:
            continue
        match = RECORD.fullmatch(line.split(MARKER, 1)[1].strip())
        if not match:
            malformed += 1
            continue
        r = dict(zip(FIELDS, map(int, match.groups())))
        eligible = r['ready_before'] + r['arrived_during']
        if (r['calls'] != 120 or eligible + r['arrived_after'] != r['pending'] or
                r['pending'] > r['calls'] or r['headroom_at_snapshot'] > r['calls'] or
                r['overlap_max_us'] > r['overlap_sum_us'] or
                r['overlap_sum_us'] > r['call_sum_us'] or
                r['overlap_sum_us'] > r['overlap_max_us'] * eligible):
            malformed += 1
            continue
        records += 1
        for key in FIELDS:
            if key == 'overlap_max_us':
                total[key] = max(total[key], r[key])
            else:
                total[key] += r[key]
    valid = records > 0 and malformed == 0
    return {
        'status': 'invalid_records' if malformed else 'parsed' if records else 'no_producer_probe_records',
        'interval_records': records,
        'malformed_records': malformed,
        'totals': total if valid else None,
        'mean_possible_overlap_us_per_call': total['overlap_sum_us'] / total['calls'] if valid else None,
        'scope': '120-call aggregates selected by log timestamp and PID can straddle capture edges. Latest-packet availability bounds native-call wall time, not GPU waits or optical latency. Output headroom is a separate post-return snapshot, not a reservation or a joint overlap observation.',
        'performance_acceptance': False,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('log', type=Path)
    ap.add_argument('--start', required=True, type=float)
    ap.add_argument('--end', required=True, type=float)
    ap.add_argument('--pid', required=True, type=int)
    args = ap.parse_args()
    result = parse(args.log.read_text(encoding='utf-8', errors='replace').splitlines(),
                   args.start, args.end, args.pid)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'parsed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
