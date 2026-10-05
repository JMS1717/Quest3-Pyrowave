"""Fresh-frame loss analysis from saved captures. No headset access.

`gaps` reads dashboard GraphStatistics submission events. Target timestamps are
tracking/pose identifiers, not a consecutive video frame counter: they can jitter
or repeat. Rounded target gaps estimate missing slots; they do not count actual
dropped or optically displayed frames. Decoder queue is publication-to-selection.

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
    unique = len({r['target_timestamp_ns'] for r in rows})
    return {
        'status': 'parsed',
        'frames': len(rows),
        'span_s': span,
        'displayed_target_fps': (len(rows) - 1) / span,
        'unique_target_timestamp_rate_fps': (unique - 1) / span,
        'duplicate_target_timestamps': len(rows) - unique,
        'sub_half_period_intervals': steps.count(0),
        'lost_target_frames_per_s': lost / span,
        'step_histogram': {str(s): steps.count(s) for s in sorted(set(steps))},
        'percentiles_ms': {k: [_pct(ms(rows, k), p) for p in (5, 50, 95)] for k in keys},
        'median_ms_before_and_after_gaps': {k: [near(before, k), near(after, k)] for k in keys},
        'scope': 'Legacy displayed_target_fps is GraphStatistics event rate over target time, not unique or optical FPS. Target timestamps identify tracking poses and may jitter/repeat. lost_target_frames_per_s is a rounded-gap estimate, not an exact drop counter. Sub-half-period gaps make rounded loss differ from refresh minus event rate. Use wall-time submission and matching direct-completion counters too. Decoder queue is publication-to-selection, not optical latency.',
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
    previous_stamp = None
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
        elapsed = stamp - previous_stamp if previous_stamp is not None else None
        previous_stamp = stamp
        if elapsed is not None and elapsed <= 0:
            malformed += 1
            continue
        if settled < settle_windows:
            continue
        g = groups.setdefault(config, {'windows': 0, 'margin': [0] * 7, 'late': [0] * 7,
                                       'timed_windows': 0, 'timed_taken': 0, 'interval_s': 0.0,
                                       **{k: 0 for k in counts}})
        g['windows'] += 1
        if elapsed is not None and settled > 0:
            g['timed_windows'] += 1
            g['timed_taken'] += counts['taken']
            g['interval_s'] += elapsed
        for k, v in counts.items():
            g[k] += v
        g['margin'] = [a + b for a, b in zip(g['margin'], margin)]
        g['late'] = [a + b for a, b in zip(g['late'], late)]
    result = []
    for (wait_us, ready, packet_grace_us), g in sorted(groups.items()):
        n = g['windows']
        result.append({'wait_us': wait_us, 'ready_active': bool(ready), 'packet_grace_us': packet_grace_us, **g,
                       'selected_source_frame_rate_fps': g['timed_taken'] / g['interval_s'] if g['interval_s'] > 0 else None,
                       'taken_per_window': g['taken'] / n, 'superseded_per_window': g['superseded'] / n,
                       'late_taken_fraction': g['late_taken'] / max(1, g['late_taken'] + g['late_superseded'])})
    return {'status': 'invalid_records' if malformed else 'parsed' if result else 'no_probe_records',
            'configurations': result if not malformed else None, 'edges_us': list(EDGES_US),
            'malformed_records': malformed, 'foreign_process_records': foreign,
            'scope': 'Counts are publications and source-order selections, not optical FPS or accepted eye copies. selected_source_frame_rate_fps uses actual log-time intervals only between consecutive same-configuration windows; transition/unbounded intervals are excluded. Native completion and wall-time submission proxies remain separate.',
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
