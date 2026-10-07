"""Analyze the client's per-frame critical-path trace (`debug.q3pw.frame_trace=1`).

The client logs `[Q3PW_TRACE] v1 dropped=N kind,t_us,id,a,b;...` (alvr/client_core/src/frame_trace.rs).
`t_us` is CLOCK_MONOTONIC; `id` is the frame's tracking timestamp (XrTime ns) for frame records.

  F first wired slice of a frame arrived          a=frame bytes  b=slice offset
  A complete frame handed to the decoder sink     a=frame bytes  b=1 on the stream socket
  P frame queued for decode                       a=order        b=bytes
  R queued frame replaced before decode (id=old)  a=order
  S decode started                                a=order
  D decode finished (fence observed)              a=GPU decode us  b=submit-to-fence us
  U publication attempted                         a=1 published    b=wait us
  O publication rejected: an newer frame was already published
  X published frame superseded before any selection (id=old)
  T frame taken by the render loop                a=us since publication
  E empty selection episode began (nothing ready)
  W xrWaitFrame returned                          id=predictedDisplayTime  a=period ns  b=shouldRender
  B xrBeginFrame returned
  G stream render started                         id=display time passed to render
  H eye draw submitted                            id=frame shown (0 = repeat)  a=copy ready
  Y eye-copy GPU completion observed              a=us
  N xrEndFrame returned                           id=displayTime  a=ok  b=streaming
  K clock calibration: t=monotonic before, id=XrTime, a=bracket ns

Usage: frame_trace.py <client.log> [--from EPOCH --to EPOCH] [--json out.json]
"""
import argparse
import bisect
import json
import re
import statistics
import sys
from collections import Counter, defaultdict

LINE = re.compile(r'^\s*(\d+\.\d+)\s.*\[Q3PW_TRACE\] v1 dropped=(\d+) (.*)$')


def parse(lines):
    """Returns (records sorted by time, dropped count, wall-minus-monotonic offset in seconds)."""
    records, dropped, offset = [], 0, None
    for line in lines:
        m = LINE.match(line)
        if not m:
            continue
        dropped += int(m.group(2))
        last = None
        for item in m.group(3).split(';'):
            if not item:
                continue
            kind, t, ident, a, b = item.split(',')
            last = (kind, int(t), int(ident), int(a), int(b))
            records.append(last)
        if last:
            # Records are logged after they happen, so the smallest gap bounds the offset.
            gap = float(m.group(1)) - last[1] / 1e6
            offset = gap if offset is None else min(offset, gap)
    records.sort(key=lambda r: r[1])
    return records, dropped, offset


def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def dist(values):
    if not values:
        return None
    return {'n': len(values), 'p5': pct(values, 0.05), 'p50': pct(values, 0.5), 'p90': pct(values, 0.9),
            'p95': pct(values, 0.95), 'max': max(values)}


def analyze(records, window=None):
    if window:
        records = [r for r in records if window[0] <= r[1] < window[1]]
    if not records:
        return {'error': 'no trace records in window'}
    t0, t1 = records[0][1], records[-1][1]
    span = (t1 - t0) / 1e6
    by_kind = defaultdict(list)
    for r in records:
        by_kind[r[0]].append(r)
    counts = {k: len(v) for k, v in sorted(by_kind.items())}
    rate = {k: round(n / span, 2) for k, n in counts.items()}

    # XrTime -> monotonic offset from calibration pairs (ns).
    cal = [r[2] - (r[1] * 1000 + r[3] // 2) for r in by_kind['K']]
    xr_minus_mono = statistics.median(cal) if cal else None

    frames = defaultdict(dict)
    for kind, t, ident, a, b in records:
        if kind in 'FAPRSDUOXT' and ident:
            f = frames[ident]
            f.setdefault(kind, t)
            if kind == 'D':
                f['gpu_us'], f['fence_us'] = a, b
            if kind == 'U':
                f['published'] = a
            if kind == 'T':
                f['margin_us'] = a

    def span_us(a, b):
        return [f[b] - f[a] for f in frames.values() if a in f and b in f]

    out = {'span_s': round(span, 3), 'records': len(records), 'rates_per_s': rate,
           'xr_minus_mono_ms': None if xr_minus_mono is None else round(xr_minus_mono / 1e6, 3),
           'calibration_bracket_us': dist([r[3] / 1000 for r in by_kind['K']])}

    # Throughput per stage, unique frames.
    unique = lambda kind: len({r[2] for r in by_kind[kind]}) / span
    published = {r[2] for r in by_kind['U'] if r[3] == 1}
    out['unique_per_s'] = {
        'arrived': round(unique('A'), 2), 'decode_started': round(unique('S'), 2),
        'decoded': round(unique('D'), 2), 'published': round(len(published) / span, 2),
        'taken': round(unique('T'), 2), 'superseded': round(unique('X'), 2),
        'replaced_before_decode': round(unique('R'), 2), 'publish_rejected': round(unique('O'), 2),
        'display_periods': round(len(by_kind['W']) / span, 2), 'empty_episodes': round(len(by_kind['E']) / span, 2),
        'eye_draws_fresh': round(sum(1 for r in by_kind['H'] if r[2]) / span, 2),
        'eye_draws_repeat': round(sum(1 for r in by_kind['H'] if not r[2]) / span, 2),
    }

    # Stage durations (us).
    out['stage_us'] = {
        'slices_first_to_complete': dist(span_us('F', 'A')),
        'arrival_to_queue': dist(span_us('A', 'P')),
        'queue_to_decode_start': dist(span_us('P', 'S')),
        'decode_wall': dist(span_us('S', 'D')),
        'gpu_decode': dist([f['gpu_us'] for f in frames.values() if 'gpu_us' in f]),
        'submit_to_fence': dist([f['fence_us'] for f in frames.values() if 'fence_us' in f]),
        'decode_end_to_publish': dist(span_us('D', 'U')),
        'publish_to_take': dist([f['margin_us'] for f in frames.values() if f.get('margin_us', -1) >= 0]),
        'arrival_to_take': dist(span_us('A', 'T')),
    }
    arrivals = [r[1] for r in by_kind['A']]
    out['interarrival_us'] = dist([b - a for a, b in zip(arrivals, arrivals[1:])])
    pubs = sorted(f['U'] for i, f in frames.items() if f.get('published') == 1)
    out['interpublish_us'] = dist([b - a for a, b in zip(pubs, pubs[1:])])

    # Render loop: each W starts a display period; G/T/E/H/N follow on the render thread.
    waits = by_kind['W']
    period_ns = statistics.median([r[3] for r in waits]) if waits else None
    selections = [r[1] for r in by_kind['G']]
    out['render_loop_us'] = {
        'wait_to_render_start': None, 'render_start_to_eye_submit': None, 'eye_submit_to_end_return': None,
    }
    if waits:
        loop = defaultdict(list)
        times = {k: [r[1] for r in by_kind[k]] for k in 'GHN'}
        for w in waits:
            nxt = {k: times[k][i] for k in 'GHN'
                   for i in [bisect.bisect_left(times[k], w[1])] if i < len(times[k])}
            if len(nxt) == 3 and nxt['G'] < w[1] + 20000:
                loop['wait_to_render_start'].append(nxt['G'] - w[1])
                if nxt['H'] >= nxt['G']:
                    loop['render_start_to_eye_submit'].append(nxt['H'] - nxt['G'])
                if nxt['N'] >= nxt['H']:
                    loop['eye_submit_to_end_return'].append(nxt['N'] - nxt['H'])
        out['render_loop_us'] = {k: dist(v) for k, v in loop.items()}
        out['period_ms'] = round(period_ns / 1e6, 4)
        if xr_minus_mono is not None:
            # Time from xrWaitFrame return to the predicted display time, on one clock.
            out['wait_return_to_display_ms'] = dist([round((r[2] - xr_minus_mono - r[1] * 1000) / 1e6, 3) for r in waits])
            # Frame age at display: predicted display time minus the frame's tracking timestamp.
            shown = [(r[1], r[2]) for r in by_kind['H'] if r[2]]
            wt = [w[1] for w in waits]
            ages = []
            for t, ident in shown:
                i = bisect.bisect_right(wt, t) - 1
                if i >= 0:
                    ages.append(round((waits[i][2] - ident) / 1e6, 3))
            out['frame_age_at_display_ms'] = dist(ages)

    # Publication phase relative to the next selection (G): how close to the edge frames land.
    phase = []
    for t in pubs:
        i = bisect.bisect_left(selections, t)
        if i < len(selections):
            phase.append(selections[i] - t)
    out['publish_before_next_selection_us'] = dist(phase)
    if period_ns:
        per = period_ns / 1000
        hist = Counter(min(int(p / per * 10), 9) for p in phase)
        out['publish_phase_hist_tenths_of_period'] = [hist.get(i, 0) for i in range(10)]

    # Outcome per published frame and what happened in each selection interval.
    sel_pubs = Counter()
    for t in pubs:
        i = bisect.bisect_left(selections, t)
        sel_pubs[i] += 1
    n_int = max(len(selections) - 1, 1)
    occupancy = Counter(sel_pubs.get(i, 0) for i in range(1, len(selections)))
    out['publications_per_selection_interval'] = {str(k): round(v / n_int, 4) for k, v in sorted(occupancy.items())}

    # Superseded frames: where they were published relative to the selection they missed.
    sup = []
    for i, f in frames.items():
        if 'X' in f and f.get('published') == 1:
            j = bisect.bisect_left(selections, f['U'])
            prev = selections[j - 1] if j > 0 else None
            sup.append({'after_prev_selection_us': None if prev is None else f['U'] - prev,
                        'replaced_after_us': f['X'] - f['U']})
    out['superseded_detail'] = {
        'after_prev_selection_us': dist([s['after_prev_selection_us'] for s in sup if s['after_prev_selection_us'] is not None]),
        'replaced_after_us': dist([s['replaced_after_us'] for s in sup]),
    }
    # Empty selections: how long until the next publication.
    empties = [r[1] for r in by_kind['E']]
    nxt_pub = []
    for t in empties:
        i = bisect.bisect_left(pubs, t)
        if i < len(pubs):
            nxt_pub.append(pubs[i] - t)
    out['empty_to_next_publish_us'] = dist(nxt_pub)
    # Arrival of the frame that was then published late: was the packet late or the decode?
    late = []
    for t in empties:
        i = bisect.bisect_left(pubs, t)
        if i < len(pubs):
            f = next((f for f in frames.values() if f.get('U') == pubs[i]), None)
            if f and 'A' in f and 'S' in f:
                late.append({'arrived_before_empty_us': t - f['A'], 'decode_started_before_empty_us': t - f['S']})
    out['empty_cause'] = {
        'packet_arrived_after_selection': sum(1 for x in late if x['arrived_before_empty_us'] < 0),
        'decoding_at_selection': sum(1 for x in late if x['arrived_before_empty_us'] >= 0),
        'arrived_before_empty_us': dist([x['arrived_before_empty_us'] for x in late]),
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log')
    ap.add_argument('--from', dest='start', type=float, help='wall-clock epoch seconds')
    ap.add_argument('--to', dest='end', type=float)
    ap.add_argument('--json')
    args = ap.parse_args()
    lines = open(args.log, encoding='utf-8', errors='replace').read().splitlines()
    records, dropped, offset = parse(lines)
    window = None
    if args.start is not None and offset is not None:
        window = (int((args.start - offset) * 1e6), int(((args.end or 1e12) - offset) * 1e6))
    result = {'dropped_records': dropped, **analyze(records, window)}
    text = json.dumps(result, indent=1)
    if args.json:
        open(args.json, 'w').write(text)
    print(text)


if __name__ == '__main__':
    sys.exit(main())
