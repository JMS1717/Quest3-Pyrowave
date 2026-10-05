"""Fresh-frame loss analysis from saved captures. No headset access.

`gaps` reads dashboard GraphStatistics events (one per frame that reached the
client compositor). A target-timestamp step of two display periods means one
server frame never displayed. The decoder-queue time of displayed frames is the
margin between publication and the render loop's selection.

`windows` reads optional `[Q3PW_FRESH]` logcat windows (`debug.q3pw.fresh_probe=1`)
and groups them by the wait budget / ready-publication configuration active
when each window was logged, so alternating A/B blocks share one session.

Neither is optical display FPS or motion-to-photon latency.
"""
import argparse
import json
import math
import re
import statistics
from pathlib import Path
from .overlay import PREFIX

EDGES_US = (500, 1000, 2000, 3000, 4000, 8000)


def _pct(values, p):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(p / 100 * len(ordered)))] if ordered else None


def graph_rows(events):
    rows = []
    for event in events:
        kind = event.get('event', event).get('event_type', {})
        if kind.get('id') == 'GraphStatistics':
            rows.append(kind['data'])
    return sorted(rows, key=lambda r: r['target_timestamp_ns'])


def gaps(rows, refresh_hz):
    if not math.isfinite(refresh_hz) or refresh_hz <= 0:
        raise ValueError('Need a positive refresh rate')
    if len(rows) < 2:
        return {'status': 'insufficient_frames', 'frames': len(rows)}
    period = 1e9 / refresh_hz
    span = (rows[-1]['target_timestamp_ns'] - rows[0]['target_timestamp_ns']) / 1e9
    if span <= 0:
        raise ValueError('Target timestamps must increase')
    steps, before, after = [], [], []
    for a, b in zip(rows, rows[1:]):
        step = round((b['target_timestamp_ns'] - a['target_timestamp_ns']) / period)
        steps.append(step)
        if step >= 2:
            before.append(a)
            after.append(b)
    ms = lambda rs, k: [r[k] * 1e3 for r in rs]
    near = lambda rs, k: statistics.median(ms(rs, k)) if rs else None
    keys = ('network_s', 'decoder_s', 'decoder_queue_s')
    lost = sum(max(0, s - 1) for s in steps)
    return {
        'status': 'parsed',
        'frames': len(rows),
        'span_s': span,
        'displayed_target_fps': (len(rows) - 1) / span,
        'lost_target_frames_per_s': lost / span,
        'step_histogram': {str(s): steps.count(s) for s in sorted(set(steps))},
        'percentiles_ms': {k: [_pct(ms(rows, k), p) for p in (5, 50, 95)] for k in keys},
        'median_ms_before_and_after_gaps': {k: [near(before, k), near(after, k)] for k in keys},
        'scope': 'Displayed frames only: frames never selected report no statistics. Decoder queue is publication-to-selection margin, not optical latency.',
        'performance_acceptance': False, 'optical_latency_measured': False,
    }


def _histogram(text):
    values = [int(v) for v in re.findall(r'-?\d+', text)]
    if len(values) != len(EDGES_US) + 1 or any(v < 0 for v in values):
        raise ValueError('Histogram must have one count per bucket')
    return values


def windows(lines, start, end, pid, settle_windows=1):
    if not all(math.isfinite(v) for v in (start, end)) or not 0 <= start < end:
        raise ValueError('Need a finite increasing epoch interval')
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError('Need the recorded client process ID')
    groups, malformed, foreign, previous, settled = {}, 0, 0, None, 0
    for line in lines:
        marker = re.search(r'\[Q3PW_FRESH\]', line)
        if not marker:
            continue
        prefix = PREFIX.match(line)
        if not prefix:
            malformed += 1
            continue
        if int(prefix[2]) != pid:
            foreign += 1
            continue
        stamp = float(prefix[1])
        if not start <= stamp <= end:
            continue
        body = line[marker.end():]
        try:
            fields = dict(re.findall(r'(\w+)=(\d+)(?=\s|$)', body))
            margin = _histogram(re.search(r'margin=\[([^\]]*)\]', body)[1])
            late = _histogram(re.search(r'late=\[([^\]]*)\]', body)[1])
            config = (int(fields['wait_us']), int(fields['ready']), int(fields.get('packet_grace_us', 0)))
            counts = {k: int(fields[k]) for k in ('taken', 'empty', 'late_taken', 'late_superseded', 'superseded')}
            if sum(margin) != counts['taken'] or counts['late_taken'] + counts['late_superseded'] > sum(late) + 1:
                raise ValueError('Inconsistent window')
        except (TypeError, KeyError, ValueError):
            malformed += 1
            continue
        # The first window after a switch straddles both configurations.
        settled = settled + 1 if config == previous else 0
        previous = config
        if settled < settle_windows:
            continue
        g = groups.setdefault(config, {'windows': 0, 'margin': [0] * 7, 'late': [0] * 7,
                                       **{k: 0 for k in counts}})
        g['windows'] += 1
        for k, v in counts.items():
            g[k] += v
        g['margin'] = [a + b for a, b in zip(g['margin'], margin)]
        g['late'] = [a + b for a, b in zip(g['late'], late)]
    result = []
    for (wait_us, ready, packet_grace_us), g in sorted(groups.items()):
        n = g['windows']
        result.append({'wait_us': wait_us, 'ready_active': bool(ready), 'packet_grace_us': packet_grace_us, **g,
                       'taken_per_window': g['taken'] / n, 'superseded_per_window': g['superseded'] / n,
                       'late_taken_fraction': g['late_taken'] / max(1, g['late_taken'] + g['late_superseded'])})
    return {'status': 'invalid_records' if malformed else 'parsed' if result else 'no_probe_records',
            'configurations': result if not malformed else None, 'edges_us': list(EDGES_US),
            'malformed_records': malformed, 'foreign_process_records': foreign,
            'scope': 'Windows are about one second of render-loop selections; counts are publications and selections, not optical FPS.',
            'performance_acceptance': False, 'optical_latency_measured': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    g = sub.add_parser('gaps')
    g.add_argument('events', type=Path, help='events.jsonl with GraphStatistics')
    g.add_argument('--hz', type=float, default=120.0)
    w = sub.add_parser('windows')
    w.add_argument('log', type=Path)
    w.add_argument('--start', type=float, required=True)
    w.add_argument('--end', type=float, required=True)
    w.add_argument('--pid', type=int, required=True)
    args = parser.parse_args()
    if args.mode == 'gaps':
        events = []
        for line in args.events.read_text(encoding='utf-8').splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        result = gaps(graph_rows(events), args.hz)
    else:
        result = windows(args.log.read_text(encoding='utf-8', errors='replace').splitlines(),
                         args.start, args.end, args.pid)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'parsed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
