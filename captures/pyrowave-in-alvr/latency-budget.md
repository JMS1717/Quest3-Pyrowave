# Latency budget

> **The earlier version of this document projected 58–70 ms for PyroWave and is INVALID.** It is
> preserved as a falsified hypothesis at the end, because the way it was wrong is the most useful
> thing in here.

## The definition everything follows from

ALVR computes, in `client_core/src/statistics.rs`:

```rust
total_pipeline_latency = now.saturating_duration_since(input_acquired) + vsync_queue
// where vsync_queue = predicted_display_time - now, sampled at submit
```

Substituting:

```
total_latency = predicted_display_time - input_acquired
```

**No stage duration appears.** The telemetry stages partition that interval, but shortening one
does not shorten the interval. A frame that finishes early waits longer before display.

```
BEFORE                decode ████████████████ 16 ms
                      wait                    █████████████████████ 25 ms

AFTER FASTER DECODE   decode █████ 5 ms
                      wait         ████████████████████████████████ 36 ms

same input_acquired, same predicted_display_time, same total
```

## Measured H.264 baseline (four successful segments)

| stage | B-15 best run | range across runs |
|---|---|---|
| game_time | 5.36 | 4.4 – 18.4 |
| server_compositor | 0.33 | 0.2 – 0.3 |
| encoder | 13.12 | 6.2 – 13.1 |
| network | 11.88 | 9.6 – 12.2 |
| decoder | 15.95 | 15.4 – 16.0 |
| decoder_queue | 5.36 | 5.4 – 11.9 |
| client_compositor | 1.51 | 1.3 – 1.5 |
| vsync_queue | 26.56 | 21.4 – 28.0 |
| **total** | **80.07** | 80.0 – 84.8 |

Residual **0.00 ms** — the stages partition the total exactly, because `statistics.rs` derives
`network` as the remainder. `game_time` is the game's own render time; it is not "unaccounted"
latency and must not be described that way.

## vsync_queue is not ALVR buffering

It is `predicted_display_time - now` at submit: the OpenXR runtime's remaining prediction lead.
Measured **24.5–27.4 ms across 60, 72 and 90 Hz** — nearly flat, where a queue counted in vsync
intervals would scale substantially with the frame period.

Treat this ~25 ms as Android XR runtime behaviour and a **boundary condition**, not a target.
`max_buffering_frames` does not reach it. **"Reduce vsync_queue" is not the objective.**

`decoder_queue` (5.4–11.9 ms) is a different thing and is where real scheduling opportunity is
more likely to live. Keep the two conceptually separate.

## What PyroWave actually provides

Measured on the Adreno 740 at 3328x1472:

| | best | mean |
|---|---|---|
| decode 4:2:0 | 3.64 ms | 5.08 ms |
| decode 4:4:4 | 6.23 ms | 8.19 ms |
| YCbCr→RGBA 4:2:0 | 1.92 ms | 2.91 ms |
| YCbCr→RGBA 4:4:4 | 1.80 ms | 2.59 ms |

Decode is **resolution-bound, not bitrate-bound** — 100/300/600/800 Mbps give 4.91/5.12/5.26/5.53
ms best, so 8x the data costs 0.62 ms, and the conversion pass is flat at ~1.46 ms. **Higher
bitrate costs almost nothing in decoder latency**; the practical ceiling will be network jitter,
loss and retransmission rather than the decoder.

**The correct claim is not** "PyroWave reduces total latency by 10–20 ms".

**It is:** PyroWave substantially reduces processing time and creates roughly 11 ms of additional
deadline headroom. Whether that headroom becomes motion-to-photon improvement depends entirely on
whether the pacing system uses it to acquire tracking and begin frame production **later**.

## The actual target

Not "reduce predicted_display_time". The question is:

> How late can `input_acquired` move toward `predicted_display_time` while still reliably
> delivering the frame before the display deadline?

```
NOW       tracking ●──────────────────────────────────● display     ~80 ms
WANTED                  tracking ●────────────────────● display     <60 ms
```

Later tracking is doubly good: shorter latency *and* a fresher pose in the frame.

## Next experiment: the pacing sweep — this is the decision gate

Use the **working H.264 path**. Sweep `max_buffering_frames` and the other pacing controls, hold
everything else fixed, and capture **distributions** (min, median, mean, p95, p99, max) rather
than means alone, for all eight stages and the total.

**Outcome A — total latency moves.** Lower buffering pushes `input_acquired` later,
`predicted_display_time` is unchanged, total falls. ALVR already has a mechanism that spends
processing headroom, and the route is: PyroWave + pacing tuning → below 60 ms.

**Outcome B — total latency does not move.** Stages rearrange (`decoder_queue` down,
`vsync_queue` up) and total stays near 80 ms. Then ALVR's pacing does not exploit headroom at all,
and the next engineering target is the scheduler itself, not the codec.

**Do not spend further effort shaving codec milliseconds until this is known.**

One instrumentation note: `input_acquired` and `predicted_display_time` are not in
`GraphStatistics`. Their *difference* is `total_pipeline_latency_s`, which is what the decision
turns on, so the sweep can answer the question as it stands. Capturing the absolute timestamps
would need a client-side patch and is only worth it if the result is ambiguous.

---

## Superseded: the 58–70 ms projection (falsified)

The earlier version modelled the pipeline as if the stages were an additive chain:

```
game + encode + network + decode + queues + display = total
```

and subtracted PyroWave's measured savings from the measured total, giving 58.0–70.1 ms with one
run of four clearing the bar.

**That method is invalid.** Since `total = predicted_display_time - input_acquired`, replacing a
16 ms decoder with a 5 ms one does not remove 11 ms from the total — it moves 11 ms into
`vsync_queue`, where the frame simply waits longer for the same display deadline.

It also claimed `vsync_queue` was made of buffering and that faster decode would make a buffering
cut safe, reclaiming ~25 ms. `vsync_queue` is the runtime's prediction lead and no ALVR setting
reaches it. The buffering argument applies to `decoder_queue`, which is 5–12 ms, not 25.

Worth keeping because the error is instructive: every number feeding the projection was correctly
measured, and the conclusion was still wrong, because the *model* relating them was wrong. A
measurement is only as good as the arithmetic it is poured into.
