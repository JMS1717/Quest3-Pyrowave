# Fresh-frame loss at native 120 Hz

**Measured on Quest 3 with interleaved live screens: a bounded wait while a frame
is decoding recovers about 4–8 displayed FPS. `.31` enables it by default.**

Native screens decode about 120 complete frames/s but copy only about 104–113
fresh frames/s into the eyes. This page records where those frames go, the
`.30` controls used to test fixes within one session, and what they showed.

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

## Live interleaved screens (`.30`)

Three sessions on the reviewed `.30` pair
([results](../results/LIVE-FRESHNESS-AB-2026-10-04.json)) switched the controls
below every 5 s inside one client process, rotating the order each cycle and
excluding 1.5 s after each switch. Source: stationary normalized chart; encode
2080 × 2208 per eye (decoded 4160 × 2208 verified); runtime rate confirmed by
the client.

| 120 Hz / 1000 Mbps / 4:2:0 | Displayed target FPS | Lost frames/s | Paired cycles vs no wait |
| --- | --- | --- | --- |
| Session 1: no wait | 108.3 | 11.8 | — |
| Session 1: wait 1500 µs | 114.7 | 5.4 | 7/7 better, +6.4 |
| Session 1: wait 3000 µs | 116.1 | 4.0 | 7/7 better, +7.8 |
| Session 1: ready-FD publication, no wait | 87.2 | 32.9 | 0/7 better, −21.1 |
| Session 2: no wait | 107.2 | 12.8 | — |
| Session 2: wait 2000 µs | 110.1 | 9.9 | 7/10 better, +2.9 |
| Session 2: wait 3000 µs | 111.4 | 8.6 | 10/10 better, +4.3 |
| Session 2: wait 4000 µs | 113.2 | 6.9 | 10/10 better, +6.0 |

- **Bounded wait:** gains grow with the budget. The remaining loss is mostly
  frames whose packet had not arrived, which the wait deliberately ignores.
- **Latency:** ALVR's estimated pipeline latency is quantized to display periods
  and drifted between states over tens of seconds regardless of the wait. No
  latency change attributable to the wait was observed. This is not a
  motion-to-photon measurement.
- **Ready-FD early publication** lowered displayed FPS in every comparison and
  pushed sessions into the higher vsync-queue state. It stays opt-in and is not
  recommended; its [GPU correctness probe](../results/READY-FENCE-GPU-2026-10-04.json)
  still passed.
- **Saved 144 Hz / 2000 Mbps / 4:4:4** (3072 × 3216 source): decode took 16–19 ms
  per frame, so about 74 frames/s were displayed and about 70/s lost. The wait
  (capped at 3472 µs) was neutral there. That configuration is decode-bound.

## `.31` default verified

| Session on `.31` | Displayed target FPS | Lost frames/s | Paired cycles |
| --- | --- | --- | --- |
| 120 Hz / 1000 Mbps / 4:2:0, property unset (logged 4000 µs) | 114.5 | 5.6 | 10/10 better, +4.1 |
| 120 Hz / 1000 Mbps / 4:2:0, `frame_wait_us=0` | 110.4 | 9.7 | — |
| 144 Hz / 1000 Mbps / 4:2:0, property unset | 133.9 | 11.3 | 5/10, +0.6 (neutral) |
| 144 Hz / 1000 Mbps / 4:2:0, `frame_wait_us=0` | 133.3 | 11.9 | — |

At 144 Hz the runtime rate was confirmed and 4:2:0 at 1000 Mbps displayed about
134 frames/s, against about 74 with 2000 Mbps / 4:4:4. This is a short
stationary screen, not sustained 144 Hz acceptance.

## Controls

| Property | Read | Effect |
| --- | --- | --- |
| `debug.q3pw.frame_wait_us=N` | about once per second | After an empty selection, wait up to N µs (max 4000, at most half a frame) **only while a complete frame is decoding**. `.31`: unset means 4000; `0` disables. `.30`: unset means 0 |
| `debug.q3pw.fresh_probe=1` | client start | Log `[Q3PW_FRESH]` about once per second |
| `debug.q3pw.ready_fd_active=0` | about once per second | With `ready_fd=1` at start, switch to synchronous publication on the same decoder |

`[Q3PW_FRESH]` reports frames taken, empty selection episodes, and histograms
of publication-to-selection margin (`margin`) and of how long after an empty
selection the next frame was published (`late`). It also reports whether that
late frame was then taken (`late_taken`) or superseded first (`late_superseded`).
Bucket edges are 0.5, 1, 2, 3, 4 and 8 ms. Repeated polls during one wait count
as a single empty episode. The in-flight flag is approximate with the
experimental two-worker decoder.

Because these controls change without restarting the client, an A/B can
alternate every few seconds in one session and one thermal state.
`tools/quest3/freshness.py windows` groups windows by configuration and skips
the first window after each switch. This replaces restart-based 15-second
OFF/ON/OFF screens, whose controls varied by up to 14 fresh FPS. The earlier
[1000 µs screen](../results/FRAME-WAIT-1000-LIVE-2026-10-02.json) could not
resolve a gain of this size.

Not yet shown: gameplay, perceptual smoothness, optical latency, or sustained
thermal behavior with the default wait.

## Overnight continuation: packet-arrival grace (`.36`, experimental)

An October 4/5 takeover baseline on the reviewed `.34` pair measured 118.21,
118.05 and 117.96 displayed target FPS in three 15-second blocks. Each block
used native encode, runtime120, 1000 Mbps, 4:2:0, LOW decode priority and the
default 4000 µs wait. No image cache or compositor filter was enabled. Source
was the normalized stationary chart at 2080x2208 per eye. This confirms the
short-screen baseline; it does not establish sustained gameplay acceptance.
See [baseline evidence](../results/OVERNIGHT-BASELINE-2026-10-04.json).

The remaining misses can include a packet arriving just after selection, when
no decode is yet in flight. Candidate `.36` adds **default-off**
`debug.q3pw.packet_grace_us`: allow up to 1000 µs for a packet to enter decode
while the ready queue is empty. Once decoding starts, the existing wait can
continue. Grace stays **inside** the current wait budget, capped at 4000 µs and
half a display period; `frame_wait_us=0` disables both. It does not introduce a
new frame queue, bypass a buffer lease or change producer synchronization.
OS scheduling can still overshoot a sleep, as with the existing wait.

The property is re-read about once per second for same-session comparisons.
`[Q3PW_FRESH]` logs `packet_grace_us`; the parser separates those configurations
and discards their transition windows. Malformed, negative or overflowing
values disable grace. Excessive valid values clamp to 1000 µs.

This is a hypothesis, **not a measured improvement**. After matching build and
correctness review, compare 0/250/500/1000 µs at unchanged source, bitrate,
chroma, refresh, queue priority, overlay and thermal conditions. Reject it if
fresh FPS/pacing, client completion, compositor lead or latency regress. A
stationary-head total estimate can change because of pose-history matching;
compare client-side stages and matched game-time estimates separately.
