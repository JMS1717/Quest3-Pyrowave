"""Read opt-in Granite decode stage averages from saved logcat; no hardware access."""
import argparse
import math
import re
import statistics
from pathlib import Path
from .overlay import PREFIX


def parse(lines, start, end, pid):
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
        raise ValueError('Need a finite increasing epoch interval')
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError('Need recorded client PID')
    enabled = False
    records = {}
    malformed = 0
    for line in lines:
        if '[Q3PW_DECODE_STAGE' not in line:
            continue
        prefix = PREFIX.match(line)
        if not prefix:
            malformed += 1
            continue
        stamp, process = float(prefix[1]), int(prefix[2])
        if process != pid or stamp > end:
            continue
        if '[Q3PW_DECODE_STAGE_SETUP]' in line:
            setup = re.search(r'enabled=([01]) interval_decodes=120', line)
            if setup:
                enabled = setup[1] == '1'
            else:
                malformed += 1
            continue
        if stamp < start:
            continue
        match = re.search(r'\[Q3PW_DECODE_STAGE\] complete=(\d+) (Dequant|iDWT(?: fragment)?): ([\d.]+) ms per frame\s*$', line)
        if not match:
            malformed += 1
            continue
        count, tag, value = int(match[1]), match[2], float(match[3])
        if not enabled or count < 120 or count % 120 or not math.isfinite(value):
            malformed += 1
            continue
        if count == 120:  # Startup/frame-context population; not a steady interval.
            continue
        key = (count, tag)
        if key in records:
            malformed += 1
            continue
        records[key] = value
    tags = sorted({tag for _, tag in records})
    groups = {tag: [v for (_, t), v in records.items() if t == tag] for tag in tags}
    result = {'status': 'invalid_records' if malformed else 'parsed' if groups else 'no_stage_records',
        'malformed_records': malformed, 'stages': {tag: {'intervals': len(v),
            'mean_of_interval_averages_ms': statistics.mean(v), 'min_average_ms': min(v),
            'max_average_ms': max(v)} for tag, v in groups.items()} if not malformed else None,
        'records': [{'complete': n, 'stage': tag, 'average_ms': v} for (n, tag), v in sorted(records.items())] if not malformed else None,
        'scope': 'Existing Granite stage GPU averages per frame context, reported every120 completed decodes. Delayed context collection and preemption can affect averages. Startup interval omitted. Not per-frame percentile, completion, optical latency or FPS evidence.',
        'performance_acceptance': False}
    return result


def main():
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path)
    parser.add_argument('--start', type=float, required=True)
    parser.add_argument('--end', type=float, required=True)
    parser.add_argument('--pid', type=int, required=True)
    args = parser.parse_args()
    report = parse(args.log.read_text(encoding='utf-8', errors='replace').splitlines(), args.start, args.end, args.pid)
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'parsed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
