# Fresh-frame loss at native 120 Hz

**Analysis and an opt-in diagnostic build. No frame-rate gain is claimed.**

Native screens decode about 120 complete frames/s but copy only about 104–113
fresh frames/s into the eyes. This page records where those frames go and the
`.30` controls designed to measure and test fixes within one session.

## What saved captures show

[`tools/quest3/freshness.py gaps`](../tools/quest3/freshness.py) reads the
per-frame GraphStatistics saved with each screen. Six `.28` captures
([results](../results/FRESHNESS-RETRO-2026-10-04.json)) at 2080 × 2208 per
eye, 120 Hz, 4:2:0, 1000 Mbps USB/TCP and Haar/compute show:

| Observation | Value |
| --- | --- |
| Displayed target FPS vs published fresh FPS | identical within 0.1 FPS (method check) |
| Lost target frames | 8.4–19.8 per second |
| Publication-to-selection margin of displayed frames | median 1.3–2.1 ms, 5th percentile 0.2–0.6 ms |
| Network / decoder time next to a gap | same as typical frames |

For Quest PyroWave the render loop takes the newest frame once, right after
`xrWaitFrame`, without blocking. Frames are published only 1–2 ms before that
instant, while network plus decode varies by about ±1.5 ms. A frame that misses
the instant causes an **empty selection** (the previous image repeats), and the
next publication then **supersedes** it. That pairing matches the `.27` probe:
199 empty selections in 15 s against about 12 superseded frames/s. Loss is a
timing-edge effect, not slow decoding or transfer spikes.

Why the margin stays near the edge in every capture is not established. The
server phase lock (`ALVR_PHASE_LOCK`) was off. It would also be blind here: it
sees only displayed frames, and its lateness signal assumes a render loop that
blocks for the decoder.

The [ready fence](READY-FENCE-EXPERIMENT.md) publishes about one decode time
earlier and makes GLES wait on the GPU instead. That shifts the timing edge
rather than removing jitter and adds a render-thread GPU wait, so its effect
must be measured, not assumed. Its [GPU correctness probe](../results/READY-FENCE-GPU-2026-10-04.json)
passed on Quest 3.

## `.30` diagnostic controls

All are default off and keep existing behavior when unset.

| Property | Read | Effect |
| --- | --- | --- |
| `debug.q3pw.fresh_probe=1` | client start | Log `[Q3PW_FRESH]` about once per second |
| `debug.q3pw.frame_wait_us=N` | about once per second | After an empty selection, wait up to N µs (max 4000, at most half a frame) **only while a complete frame is decoding** |
| `debug.q3pw.ready_fd_active=0` | about once per second | With `ready_fd=1` at start, switch to synchronous publication on the same decoder |

`[Q3PW_FRESH]` reports frames taken, empty selection episodes, and histograms
of publication-to-selection margin (`margin`) and of how long after an empty
selection the next frame was published (`late`). It also reports whether that
late frame was then taken (`late_taken`) or superseded first (`late_superseded`).
Bucket edges are 0.5, 1, 2, 3, 4 and 8 ms. Repeated polls during one wait count
as a single empty episode. The in-flight flag is approximate with the
experimental two-worker decoder.

Because the wait budget and ready switch change without restarting the client,
an A/B can alternate every few seconds in one session and one thermal state.
`tools/quest3/freshness.py windows` groups windows by configuration and skips
the first window after each switch. This replaces restart-based 15-second
OFF/ON/OFF screens whose controls varied by up to 14 fresh FPS.

A wait helps only if late frames are typically late by less than the budget and
the render loop has slack; it can increase motion-to-photon latency for those
frames by up to the budget. Promotion would need repeated interleaved gains,
pacing and latency checks, then sustained gameplay and thermal validation.
