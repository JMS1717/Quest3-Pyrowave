"""Inspect bounded publication wait counters in saved logcat; no hardware access."""
import argparse
import json
import math
import re
from pathlib import Path
from .runtime_logs import PREFIX

MARKER = '[Q3PW_EVENT_WAIT]'
FIELDS = ('requested', 'calls', 'condvar_waits', 'pending_seen', 'fallback_calls')
RECORD = re.compile(r'requested=([01]) calls=(\d+) condvar_waits=(\d+) pending_seen=(\d+) fallback_calls=(\d+)\s*$')


def parse(lines, start, end, pid):
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        raise ValueError('Need a finite increasing epoch interval')
    if type(pid) is not int or not 0 < pid < 2**31:
        raise ValueError('Need the recorded client PID')
    groups = {}
    malformed = 0
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
        if r['pending_seen'] > r['calls'] or (r['requested'] == 0 and any(r[k] for k in FIELDS[1:])):
            malformed += 1
            continue
        group = groups.setdefault(r['requested'], {'requested': bool(r['requested']), 'interval_records': 0,
                                                  **{k: 0 for k in FIELDS[1:]}})
        group['interval_records'] += 1
        for key in FIELDS[1:]:
            group[key] += r[key]
    return {
        'status': 'invalid_records' if malformed else 'parsed' if groups else 'no_event_wait_records',
        'malformed_records': malformed,
        'configurations': None if malformed else [groups[k] for k in sorted(groups)],
        'scope': 'Per-second counter intervals selected by their log timestamps and client PID. Intervals may straddle capture edges. Condvar calls can exceed eligible calls after spurious or decode-start wakes. Pending is CPU publication, not buffer leasing or GPU completion.',
        'performance_acceptance': False,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('log', type=Path)
    ap.add_argument('--start', required=True, type=float)
    ap.add_argument('--end', required=True, type=float)
    ap.add_argument('--pid', required=True, type=int)
    args = ap.parse_args()
    result = parse(args.log.read_text(encoding='utf-8', errors='replace').splitlines(), args.start, args.end, args.pid)
    print(json.dumps(result, indent=2))
    return 1 if result['status'] == 'invalid_records' else 0


if __name__ == '__main__':
    raise SystemExit(main())
