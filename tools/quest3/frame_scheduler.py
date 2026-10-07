"""Offline replay and simulation of the client's 207 Hz frame scheduler (no headset).

The model has two independent halves:

- **Producer.** Complete frames arrive (`A`), wait in the decoder's latest-only input slot
  (a newer arrival replaces an undecoded one, `R`), decode on one worker (`S`..`D`) and publish
  (`U`) after a CPU wake/publication overhead. Decode-phase shaping (policy C) may delay a decode
  start by a bounded amount.
- **Consumer.** Each display period `k` starts when xrWaitFrame returns at `W_k` with a fixed
  predicted display time `W_k + display_lead`. The render loop selects at most one frame in
  `[W_k + begin, W_k + max_select]`; a selection after `render_deadline` misses the period.

A frame is *fresh* when it is the first display of a newer frame. A published frame that is never
displayed is *superseded*. A period that shows no new frame is *empty*. Display order never goes
backwards and every queue is bounded by construction (one decoder input slot, one pending output,
at most one held/jitter frame), so `stale` must stay 0.

Policies (all bounded; none adds more than `max_extra_us` of selection delay):

  latest      current #15: take the newest at W+begin; if nothing is ready and a decode is in
              flight, wait for it up to the selection budget (period/2 by default)
  hold        #15 `frame_hold_us`: a superseded pending frame is held and shown first while it is
              younger than the hold limit (the 6 ms opt-in)
  deadline    A: like latest, but also waits for an in-flight decode whose predicted completion is
              inside the deadline even when an older frame is pending; the newer frame is shown
  phase       B: learns the publication phase against W and moves the selection point inside
              [begin, begin + max_extra] to just after the publication mass
  shape       C: delays a decode start (<= max_extra) when its predicted publication would land
              just before the selection point while another frame is waiting
  jitter      D: delays the visibility of an early publication (<= max_extra) to restore the
              learned cadence; late frames are never delayed
  phase+jitter, phase+deadline: combinations

Inputs: a synthetic trace (`--synthetic`, seeded, with arrival jitter, server drops, clock drift
and decode-time spread) or a `[Q3PW_TRACE]` log replayed through `frame_trace.parse`
(arrivals, decode durations, publication overheads and xrWaitFrame times/periods).

Outputs per policy: fresh/s, superseded/s, empty/s, residence p50/p90/p95 (publication to
selection), frame age at display, selection delay, queue depth, stale count, the publication
phase histogram and the count of delays that bought nothing.

Usage:
  frame_scheduler.py --synthetic [--seconds 10 --seed 1] [--policy all] [--json out.json]
  frame_scheduler.py --log client.log [--policy all]
  frame_scheduler.py --search [--seeds 8] [--max-extra-us 2000]
"""
import argparse
import bisect
import json
import math
import random
import sys
from dataclasses import dataclass, field, replace

PERIOD_207_US = 1e6 / 207


@dataclass
class Config:
    period_us: float = PERIOD_207_US
    begin_us: float = 150.0            # W -> first possible selection (xrBeginFrame return)
    select_budget_us: float = 2415.0   # #15 default: 4000 us capped at period/2
    render_deadline_us: float = 2800.0  # latest selection that still makes the period
    display_lead_us: float = 2 * PERIOD_207_US  # W -> predicted display time
    max_extra_us: float = 2000.0       # bound on any added delay (selection, visibility, decode)
    hold_us: float = 6000.0
    phase_guard_us: float = 250.0      # B: margin after the learned publication phase
    phase_bins: int = 48
    phase_decay: float = 0.98
    phase_warmup: int = 32
    jitter_slack_us: float = 600.0     # D: spacing kept below the learned cadence
    shape_window_us: float = 1200.0    # C: collision window before the selection point
    deadline_margin_us: float = 300.0  # A: predicted completion must beat the deadline by this


@dataclass
class Frame:
    order: int
    arrival: float
    decode_us: float
    overhead_us: float
    start: float = math.nan
    done: float = math.nan
    published: float = math.nan
    visible: float = math.nan
    replaced: bool = False
    shaped_us: float = 0.0
    jitter_us: float = 0.0
    shown_at: int = -1
    selected_at: float = math.nan


@dataclass
class Trace:
    arrivals: list            # (arrival_us, decode_us, overhead_us)
    waits: list               # W return times (us)
    period_us: float
    source: str = 'synthetic'
    meta: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------- traces

def lognormal_from_quantiles(rng, p50, p90):
    sigma = math.log(p90 / p50) / 1.2815515655446004
    return p50 * math.exp(rng.gauss(0.0, sigma))


def synthetic(seconds=10.0, seed=1, display_hz=207.0, source_hz=206.3, arrival_jitter_us=450.0,
              spike_rate=0.01, spike_us=2500.0, drift_ppm=40.0, decode_p50=2670.0, decode_p90=3500.0,
              prep_us=450.0, wake_us=180.0, wait_jitter_us=40.0, phase_us=None):
    """Arrivals at the server's cadence (frames it drops are skipped), network jitter and rare
    spikes, decode-time spread from the measured Haar p50/p90, and a W grid at the display rate
    with a small clock drift against the server."""
    rng = random.Random(seed)
    period = 1e6 / display_hz
    server_period = 1e6 / display_hz * (1 + drift_ppm * 1e-6)
    drop_p = max(0.0, 1.0 - source_hz / display_hz)
    phase = rng.uniform(0, period) if phase_us is None else phase_us
    arrivals, t, n = [], 20_000.0 + phase, 0
    end = seconds * 1e6
    while t < end:
        if rng.random() >= drop_p:
            jitter = abs(rng.gauss(0, arrival_jitter_us))
            if rng.random() < spike_rate:
                jitter += rng.uniform(0.3, 1.0) * spike_us
            prep = prep_us * (0.8 + 0.4 * rng.random())
            gpu = lognormal_from_quantiles(rng, decode_p50, decode_p90)
            wake = wake_us + rng.expovariate(1 / 80.0)
            arrivals.append((t + jitter, prep + gpu, wake))
        n += 1
        t = 20_000.0 + phase + n * server_period
    arrivals.sort()
    waits = [20_000.0 + k * period + rng.gauss(0, wait_jitter_us) for k in range(int(end / period) + 1)]
    return Trace(arrivals, waits, period, 'synthetic', {'seed': seed, 'source_hz': source_hz})


def from_records(records):
    """Replay a frame_trace capture: A arrival, S->D decode wall, D->U overhead, W times/periods."""
    frames, waits, periods = {}, [], []
    for kind, t, ident, a, b in records:
        if kind == 'W':
            waits.append(float(t))
            if a > 0:
                periods.append(a / 1000)
        elif ident and kind in 'ASDU':
            frames.setdefault(ident, {}).setdefault(kind, float(t))
    decodes = [f['D'] - f['S'] for f in frames.values() if 'S' in f and 'D' in f]
    overheads = [f['U'] - f['D'] for f in frames.values() if 'D' in f and 'U' in f]
    median = lambda v, d: sorted(v)[len(v) // 2] if v else d
    dec_default, oh_default = median(decodes, 2700.0), median(overheads, 150.0)
    arrivals = []
    for f in frames.values():
        if 'A' not in f:
            continue
        dec = f['D'] - f['S'] if 'S' in f and 'D' in f else dec_default
        oh = f['U'] - f['D'] if 'D' in f and 'U' in f else oh_default
        arrivals.append((f['A'], dec, max(0.0, oh)))
    arrivals.sort()
    period = median(periods, PERIOD_207_US)
    return Trace(arrivals, sorted(waits), period, 'replay', {'frames': len(arrivals)})


# ----------------------------------------------------------------------------- producer

def produce(trace, cfg, policy):
    """Single decode worker with a latest-only input slot. Returns frames in arrival order."""
    frames = [Frame(i, a, d, o) for i, (a, d, o) in enumerate(trace.arrivals)]
    shaping = 'shape' in policy
    waits = trace.waits
    worker_free = -math.inf
    queued = None
    est = Estimator()

    def start(f, t):
        nonlocal worker_free
        if shaping:
            t = shape_start(f, t, est, cfg, waits, frames)
        f.start = t
        f.done = t + f.decode_us
        f.published = f.done + f.overhead_us
        worker_free = f.published
        est.observe(f.published - f.start)

    for f in frames:
        # Start whatever was queued before this arrival, if the worker freed up first.
        if queued is not None and worker_free <= f.arrival:
            start(queued, max(worker_free, queued.arrival))
            queued = None
        if worker_free <= f.arrival:
            start(f, f.arrival)
        else:
            if queued is not None:
                queued.replaced = True
            queued = f
    if queued is not None:
        start(queued, max(worker_free, queued.arrival))
    return frames


class Estimator:
    """Running decode-wall estimate (EWMA of mean and absolute deviation)."""

    def __init__(self):
        self.mean, self.dev, self.n = 3200.0, 400.0, 0

    def observe(self, wall):
        self.n += 1
        a = 0.1 if self.n > 10 else 1.0 / self.n
        self.dev += a * (abs(wall - self.mean) - self.dev)
        self.mean += a * (wall - self.mean)

    def p90(self):
        return self.mean + 1.6 * self.dev


def shape_start(f, t, est, cfg, waits, frames):
    """C: if the predicted publication lands in the window just before a selection point while an
    older frame has published since the previous selection point, delay the start (<= max_extra)
    so the older frame is shown first and this one lands just after the selection."""
    pred = t + est.mean
    k = bisect.bisect_right(waits, pred)
    if k >= len(waits):
        return t
    boundary = waits[k] + cfg.begin_us
    prev_boundary = waits[k - 1] + cfg.begin_us if k > 0 else -math.inf
    if not boundary - cfg.shape_window_us <= pred < boundary:
        return t
    # An older frame published (or will publish) after the previous selection point?
    waiting = any(prev_boundary < g.published < boundary for g in frames[max(0, f.order - 3):f.order]
                  if not math.isnan(g.published))
    if not waiting:
        return t
    delay = boundary + cfg.phase_guard_us - pred
    return t + delay if 0 < delay <= cfg.max_extra_us else t


# ----------------------------------------------------------------------------- consumer

class PhaseLearner:
    """B: decayed histogram of publication phase against the W grid; chooses the selection offset
    in [begin, begin + max_extra] that minimises expected residence (publication -> selection,
    modulo one period), which also keeps the boundary out of the publication mass."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.hist = [0.0] * cfg.phase_bins
        self.n = 0

    def observe(self, phase_us):
        c = self.cfg
        b = int(phase_us / c.period_us * c.phase_bins) % c.phase_bins
        self.hist = [h * c.phase_decay for h in self.hist]
        self.hist[b] += 1.0
        self.n += 1

    def offset(self):
        c = self.cfg
        if self.n < c.phase_warmup:
            return c.begin_us
        width = c.period_us / c.phase_bins
        hi = min(c.begin_us + c.max_extra_us, c.render_deadline_us)
        best, best_cost, s = c.begin_us, math.inf, c.begin_us
        while s <= hi + 1e-6:
            cost = 0.0
            for b, h in enumerate(self.hist):
                if h:
                    centre = (b + 0.5) * width
                    cost += h * ((s - centre - c.phase_guard_us) % c.period_us)
            # Waiting costs a little render time; prefer the earliest equally good point.
            cost += 1e-3 * (s - c.begin_us) * sum(self.hist)
            if cost < best_cost - 1e-9:
                best, best_cost = s, cost
            s += 100.0
        return best


def visible_times(frames, cfg, policy):
    """D: visibility delay for early publications; others become visible when published."""
    live = [f for f in frames if not f.replaced]
    if 'jitter' not in policy:
        for f in live:
            f.visible = f.published
        return live
    cadence, last_vis, n = None, None, 0
    last_pub = None
    for f in sorted(live, key=lambda f: f.published):
        vis = f.published
        if last_vis is not None and cadence is not None and n > 16:
            target = last_vis + cadence - cfg.jitter_slack_us
            vis = max(f.published, min(target, f.published + cfg.max_extra_us))
        f.visible = vis
        f.jitter_us = vis - f.published
        if last_pub is not None:
            gap = f.published - last_pub
            if gap < 3 * cfg.period_us:
                cadence = gap if cadence is None else cadence + 0.05 * (gap - cadence)
                n += 1
        last_pub, last_vis = f.published, vis
    return live


def consume(trace, frames, cfg, policy):
    live = sorted(visible_times(frames, cfg, policy), key=lambda f: f.visible)
    vis = [f.visible for f in live]
    starts = sorted((f.start, f.visible, f) for f in live)
    learner = PhaseLearner(cfg) if 'phase' in policy else None
    est = Estimator()
    last_order = -1
    held = None
    periods = []
    next_obs = 0  # publications fed to the phase learner / estimator, in visibility order
    pub_order = sorted(live, key=lambda f: f.published)
    waits = trace.waits

    for k, w in enumerate(waits):
        earliest = w + cfg.begin_us
        # Learn only from what was observable before this period's selection.
        while next_obs < len(pub_order) and pub_order[next_obs].published <= earliest:
            g = pub_order[next_obs]
            est.observe(g.published - g.start)
            if learner is not None:
                j = bisect.bisect_right(waits, g.published) - 1
                if j >= 0:
                    learner.observe(g.published - waits[j])
            next_obs += 1
        sel = learner.offset() if learner is not None else cfg.begin_us
        t = w + sel
        budget_end = w + min(cfg.begin_us + cfg.select_budget_us, cfg.render_deadline_us)
        if learner is not None:
            budget_end = max(budget_end, t)

        def ready(at):
            i = bisect.bisect_right(vis, at)
            cand = [f for f in live[max(0, i - 6):i] if f.order > last_order and f.shown_at < 0]
            return cand

        def in_flight(at):
            # A decode that has started and is not yet visible.
            return [f for s, v, f in starts if s <= at < v and f.order > last_order]

        cand = ready(t)
        chosen, outcome = None, 'empty'
        if 'hold' in policy:
            # #15 frame hold: held (older) frame first while younger than the limit.
            chosen, held, superseded = hold_select(cand, held, t, cfg)
            for f in superseded:
                f.shown_at = -2
            if chosen is None:
                flight = in_flight(t)
                if flight:
                    nxt = min(v for _, v, f in starts if f in flight)
                    if nxt <= budget_end:
                        t = nxt
                        chosen, held, superseded = hold_select(ready(t), held, t, cfg)
                        for f in superseded:
                            f.shown_at = -2
                    else:
                        t = budget_end
        else:
            if 'deadline' in policy and cand:
                newest = max(cand, key=lambda f: f.order)
                flight = [f for f in in_flight(t) if f.order > newest.order]
                if flight:
                    f = min(flight, key=lambda f: f.order)
                    predicted = f.start + est.p90()
                    if predicted <= budget_end - cfg.deadline_margin_us and f.visible <= budget_end:
                        t = max(t, f.visible)
                        cand = ready(t)
            if not cand:
                flight = in_flight(t)
                if flight:
                    nxt = min(f.visible for f in flight)
                    if nxt <= budget_end:
                        t = nxt
                        cand = ready(t)
                    else:
                        t = budget_end
            if cand:
                chosen = max(cand, key=lambda f: f.order)
                for f in cand:
                    if f is not chosen:
                        f.shown_at = -2
        late = t > w + cfg.render_deadline_us
        if chosen is not None:
            if late:
                # The render misses this period; the frame is shown one period later.
                chosen.shown_at, chosen.selected_at = k + 1, t
            else:
                chosen.shown_at, chosen.selected_at = k, t
            if chosen.order < last_order:
                raise AssertionError('display order went backwards')
            last_order = chosen.order
            outcome = 'fresh'
        periods.append({'k': k, 'w': w, 'select': t, 'offset': t - w, 'outcome': outcome,
                        'frame': chosen, 'late': late, 'depth': len(cand) + (held is not None)})
    return live, periods


def hold_select(cand, held, t, cfg):
    superseded = []
    cand = sorted(cand, key=lambda f: f.order)
    # Publication with a pending frame moves the older one to held; a newer held replaces it.
    if len(cand) >= 2:
        older = cand[:-1]
        if held is not None:
            superseded.append(held)
        superseded += older[:-1]
        held = older[-1]
        cand = cand[-1:]
    if held is not None:
        if t - held.visible <= cfg.hold_us:
            chosen = held
            held = cand[0] if cand else None
            return chosen, held, superseded
        superseded.append(held)
        held = None
    return (cand[0] if cand else None), held, superseded


# ----------------------------------------------------------------------------- metrics

def pct(values, q):
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(q * (len(s) - 1) + 0.5))]


def run(trace, cfg, policy):
    frames = produce(trace, cfg, policy)
    live, periods = consume(trace, frames, cfg, policy)
    span = (trace.waits[-1] - trace.waits[0]) / 1e6 if len(trace.waits) > 1 else 1.0
    shown = [f for f in live if f.shown_at >= 0]
    dropped = [f for f in live if f.shown_at < 0]
    # Ignore frames published after the last selection (the trace simply ended).
    last_sel = periods[-1]['select'] if periods else math.inf
    superseded = [f for f in dropped if f.visible <= last_sel]
    residence = [f.selected_at - f.published for f in shown]
    lead = cfg.display_lead_us
    age = [trace.waits[min(f.shown_at, len(trace.waits) - 1)] + lead - f.arrival for f in shown]
    delays = [p['offset'] - cfg.begin_us for p in periods]
    phases = [0] * 10
    for f in live:
        j = bisect.bisect_right(trace.waits, f.published) - 1
        if j >= 0:
            phases[min(9, int((f.published - trace.waits[j]) / trace.period_us * 10))] += 1
    unnecessary = 0
    for p in periods:
        if p['offset'] - cfg.begin_us > 50 and p['frame'] is None:
            unnecessary += 1   # waited and still showed nothing new
    for f in shown:
        if f.jitter_us > 0 or f.shaped_us > 0:
            j = bisect.bisect_right(trace.waits, f.published - cfg.begin_us) - 1
            if f.shown_at == max(j, 0) + 1 and f.jitter_us + f.shaped_us > 0:
                pass
    late = sum(1 for p in periods if p['late'])
    stale = 0
    last = -1
    for p in periods:
        if p['frame'] is not None:
            if p['frame'].order < last:
                stale += 1
            last = p['frame'].order
    q = lambda v: {'p50': r(pct(v, .5)), 'p90': r(pct(v, .9)), 'p95': r(pct(v, .95)), 'max': r(max(v) if v else None)}
    return {
        'policy': policy,
        'fresh_per_s': round(len(shown) / span, 2),
        'superseded_per_s': round(len(superseded) / span, 2),
        'empty_per_s': round(sum(1 for p in periods if p['outcome'] == 'empty') / span, 2),
        'replaced_before_decode_per_s': round(sum(1 for f in frames if f.replaced) / span, 2),
        'published_per_s': round(len(live) / span, 2),
        'late_render_per_s': round(late / span, 2),
        'residence_us': q(residence),
        'age_at_display_us': q(age),
        'selection_delay_us': q(delays),
        'added_visibility_us': q([f.jitter_us for f in live]),
        'decode_shaping_us': q([f.start - f.arrival for f in frames if not f.replaced]),
        'queue_depth_max': max((p['depth'] for p in periods), default=0),
        'stale': stale,
        'publish_phase_tenths': phases,
        'unnecessary_waits_per_s': round(unnecessary / span, 2),
    }


def r(v):
    return None if v is None else round(v, 1)


POLICIES = ('latest', 'hold', 'deadline', 'phase', 'shape', 'jitter', 'phase+jitter', 'phase+deadline')


# ----------------------------------------------------------------------------- search

def score(m, cfg):
    """Fresh frames first, then lower display age; bounded added delay is a hard constraint."""
    if m['stale'] or m['late_render_per_s'] > 0.5:
        return -math.inf
    return m['fresh_per_s'] - 0.002 * (m['age_at_display_us']['p50'] or 0)


def search(seeds=8, seconds=8.0, max_extra_us=2000.0, scenarios=None):
    scenarios = scenarios or {
        'nominal': {},
        'jittery': {'arrival_jitter_us': 800.0, 'spike_rate': 0.03},
        'slow_decode': {'decode_p50': 3000.0, 'decode_p90': 3900.0},
    }
    grid = []
    for policy in POLICIES:
        variants = [{}]
        if 'phase' in policy:
            variants = [{'phase_guard_us': g} for g in (100.0, 250.0, 500.0)]
        if 'jitter' in policy:
            variants = [dict(v, jitter_slack_us=s) for v in variants for s in (300.0, 600.0, 1000.0)]
        if policy == 'shape':
            variants = [{'shape_window_us': w} for w in (600.0, 1200.0)]
        if 'deadline' in policy:
            variants = [dict(v, deadline_margin_us=m) for v in variants for m in (0.0, 300.0)]
        for v in variants:
            grid.append((policy, v))
    results = []
    for policy, v in grid:
        cfg = replace(Config(max_extra_us=max_extra_us), **v)
        rows = []
        for name, kw in scenarios.items():
            for seed in range(seeds):
                trace = synthetic(seconds=seconds, seed=seed, **kw)
                rows.append((name, run(trace, cfg, policy)))
        def mean(key, sub=None, rows=rows):
            vals = [m[key][sub] if sub else m[key] for _, m in rows]
            vals = [x for x in vals if x is not None]
            return round(sum(vals) / len(vals), 2) if vals else None
        worst = min(score(m, cfg) for _, m in rows)
        results.append({
            'policy': policy, 'params': v,
            'fresh_per_s': mean('fresh_per_s'), 'superseded_per_s': mean('superseded_per_s'),
            'empty_per_s': mean('empty_per_s'), 'late_render_per_s': mean('late_render_per_s'),
            'residence_p50_us': mean('residence_us', 'p50'), 'residence_p95_us': mean('residence_us', 'p95'),
            'age_p50_us': mean('age_at_display_us', 'p50'), 'age_p95_us': mean('age_at_display_us', 'p95'),
            'selection_delay_p95_us': mean('selection_delay_us', 'p95'),
            'stale': sum(m['stale'] for _, m in rows),
            'mean_score': round(sum(score(m, cfg) for _, m in rows) / len(rows), 3),
            'worst_score': round(worst, 3) if worst != -math.inf else None,
            'per_scenario_fresh': {name: round(sum(m['fresh_per_s'] for n, m in rows if n == name) / seeds, 2)
                                   for name in scenarios},
        })
    results.sort(key=lambda x: -(x['mean_score'] if x['worst_score'] is not None else -1e9))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument('--synthetic', action='store_true')
    src.add_argument('--log')
    src.add_argument('--search', action='store_true')
    ap.add_argument('--seconds', type=float, default=10.0)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--seeds', type=int, default=8)
    ap.add_argument('--policy', default='all')
    ap.add_argument('--max-extra-us', type=float, default=2000.0)
    ap.add_argument('--render-deadline-us', type=float, default=2800.0)
    ap.add_argument('--json')
    args = ap.parse_args(argv)
    if args.max_extra_us > 2000.0:
        ap.error('--max-extra-us is bounded at 2000 (never a full frame period)')
    if args.search:
        out = search(seeds=args.seeds, seconds=min(args.seconds, 8.0), max_extra_us=args.max_extra_us)
    else:
        if args.log:
            sys.path.insert(0, __file__.rsplit('/', 1)[0])
            import frame_trace
            with open(args.log, encoding='utf-8', errors='replace') as fh:
                records, _, _ = frame_trace.parse(fh.read().splitlines())
            trace = from_records(records)
        else:
            trace = synthetic(seconds=args.seconds, seed=args.seed)
        cfg = Config(max_extra_us=args.max_extra_us, render_deadline_us=args.render_deadline_us,
                     period_us=trace.period_us)
        policies = POLICIES if args.policy == 'all' else args.policy.split(',')
        out = {'source': trace.source, 'meta': trace.meta, 'period_us': round(trace.period_us, 2),
               'results': [run(trace, cfg, p) for p in policies]}
    text = json.dumps(out, indent=1)
    if args.json:
        with open(args.json, 'w') as fh:
            fh.write(text)
    print(text)


if __name__ == '__main__':
    sys.exit(main())
