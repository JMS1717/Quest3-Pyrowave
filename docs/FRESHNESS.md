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

Before deployment, the reviewed `.35` native libraries (inherited by `.36`)
passed default conversion GPU readback at 512x320 and native stereo4160x2208:
both were byte-identical to the existing asymmetric golden references. This
checks that the optional chroma-filter addition preserves default pixels; it
does not validate Catmull-Rom quality or its performance.
[GPU regression evidence](../results/DEFAULT-CONVERT-GPU-2026-10-04.json).

### Measured packet-grace result

On the reviewed `.36` pair, four rotating 8-second blocks per arm in one client
session gave the following rates. All arms kept native resolution, runtime120,
1000 Mbps, 4:2:0, LOW priority and the default 4000 µs wait. The source interval
covered every block and effective grace settings were verified from probe logs.

| Packet grace | Submission events/s (wall time) | Direct eye completions/s | Client instantaneous FPS p1 |
| --- | ---: | ---: | ---: |
| 0 µs | 117.59 | 117.64 | 60.00 |
| 250 µs | 117.49 | 117.46 | 60.00 |
| 500 µs | 117.68 | 117.76 | 60.00 |
| 1000 µs | 117.17 | 117.15 | 60.00 |

**Keep grace disabled.** The 500 µs difference is within control variation;
1000 µs reduced average delivery. No arm removed missed display periods. Actual
payload medians were about 1008–1010 Mbps. Battery temperature stayed 37–40 °C
with thermal status0. These are short stationary screens, not sustained or
gameplay acceptance. [All blocks and limitations](../results/PACKET-GRACE-LIVE-2026-10-04.json).

### Timestamp interpretation correction

ALVR's target timestamps identify tracking poses; they are not a consecutive
encoded-frame counter. This capture contains occasional duplicates and
sub-half-period target gaps from tracking jitter. The legacy
`displayed_target_fps` field is an event-rate proxy over target time;
`lost_target_frames_per_s` is a rounded-gap estimate, **not an exact dropped-frame
count**. Older tables using those fields should be read with that qualification.
The offline parser now reports duplicate IDs, sub-half-period gaps and the rate
of distinct tracking IDs separately. Distinct IDs still do not prove unique
video contents. Compare submission wall-time rate, matching direct-completion
counters and instantaneous client FPS/pacing together; none measures optical FPS.

The optional freshness windows count source-order selections independently of
tracking IDs. `selected_source_frame_rate_fps` divides those counts by actual
log-time intervals between consecutive unchanged-configuration windows, excluding
transitions. It measures queue selections, which can precede a failed eye copy;
it must be compared with completion counters and does not prove optical delivery.

The `.35` surface probe also looked only in `ExtensionSet.other`, which excludes
extensions known to the Rust bindings. Its all-false log cannot rule out Android
surface swapchains. `.37` reads the three named fields from the pinned openxr
bindings; this is a diagnostic correction, with existing rendering unchanged.

## 207 Hz with mode 5 (October 7)

Owner settings (3072x3216 rendered, 2080x2208 per eye streamed, 700 Mbps, Haar mode 5, 60 deg/s
pan, headset awake). Fresh FPS sits at 193-197 with 10-14 lost/s. `debug.q3pw.fresh_probe=1` over
one 12 s block, per ~1 s window:

| taken | superseded | empty selections | late frames taken after a wait |
| ---: | ---: | ---: | ---: |
| 194-202 | 5-11 | 0-9 | 0-9 |

Taken plus superseded is about 207, so every frame arrives and is decoded; the loss is real and
on the client: about 8 frames/s finish decoding in the same display period as their successor,
and a few periods find nothing ready. Most frames are published 2-3 ms before selection. Arrival
bunching comes from transit and decode jitter (network p50 2.2 / p90 3.0 ms, decode p50 2.65 /
p90 3.5 ms), not from the server's send rate: making that steady with `ALVR_PACING_SPIN_US`
changed nothing ([HIGH-REFRESH.md](HIGH-REFRESH.md)). Neither did removing the render-thread
wait (release fence) or preempting decode (LOW priority). Displaying every frame would need a
one-frame queue, about 4.8 ms more latency against the 30 ms goal, so it is not pursued.

**The frame hold at 120 Hz (October 8, `.90`).** The owner's setup (120 Hz, 2000 Mbit/s, 4:4:4,
CDF 5/3, wired) runs 109-111 fresh FPS; per second about 108 frames are taken, 7.7 superseded and
17.8 selections find nothing. The decoder, not the link, sets that pace (corrected after a frame
trace on `.92`, [below](#where-120-hz--2000--444-loses-frames-92-october-8)).
`debug.q3pw.frame_hold_us=10000`, ABBA, 12 s blocks: 110.8 against 110.7 fresh FPS. Superseded
frames fell only from 7.7 to 7.1 a second, while ALVR's latency estimate rose by about 5 ms (its
decoder queue by 0.5-1.3 ms). Rejected. At 1500 Mbit/s the same setup holds about 116.

### Where 120 Hz / 2000 / 4:4:4 loses frames (`.92`, October 8)

`debug.q3pw.frame_trace=1`, the owner's setup (wired, CDF 5/3, 4:4:4, 2080x2208, maximum GPU
clock), two 12 s blocks per bitrate, p50 (p90) ms:

| | 2000 Mbit/s | 1500 Mbit/s |
|---|---|---|
| Fresh FPS | 113.4, 109.4 | 111.4, 113.1 |
| Frames arrived per second | 120.2, 117.8 | 120.0, 120.0 |
| Frames decoded per second | 119.1, 114.5 | 119.5, 119.8 |
| Display periods rendered per second | 117.0, 114.5 | 119.9, 119.4 |
| First slice to complete frame | 4.6 (5.8), 5.4 (9.3) | 3.4 (4.7), 3.4 (5.6) |
| Queued to decode start | 2.4 (6.2), 2.5 (6.1) | 0.0 (3.5), 0.0 (3.1) |
| Decode start to fence | 8.0 (8.5), 8.1 (8.8) | 7.1 (7.9), 6.6 (8.0) |
| GPU decode | 5.2, 5.7 | 4.9, 4.8 |
| Arrival to taken by the render loop | 14.1, 14.2 | 9.1, 10.2 |

- **The link keeps up:** frames arrive at 120 a second, and a 2.08 MB frame crosses USB in about
  5 ms (about 3.5 Gbit/s). The earlier 7-8 ms figure was ALVR's network stage, which includes
  more than the transfer.
- **The decoder doesn't:** at 2000 its wall time per frame (8.0-8.1 ms) is the whole period, so
  frames queue 2.4 ms for it, and 187 of 191 and 219 of 231 empty selections found a frame still
  decoding. The headset GPU is full: the render loop also misses 3-6 display periods a second.
- **At 1500** the decoder keeps up and the render loop holds every period. The frames lost there
  (about 7 a second) are publication phase: a frame finishing just after a selection.
- **The cost of 2000 in latency** is about 5 ms on the client (arrival to taken). The frame age
  at display differed by more (57.6 and 61.6 against 41.5 and 43.2 ms), but most of that was the
  test scene's game stage, which flips between two modes from one connection to the next
  ([LATENCY.md](LATENCY.md#the-game-stage-has-two-modes-in-the-test-scene-october-8)).
- So at 120 Hz 4:4:4, more than 1500 Mbit/s buys detail (offline about +0.2 dB) at the cost of a
  saturated headset GPU. A faster 4:4:4 decode, or fewer bytes per frame for the same detail
  (entropy coding), are what would make 2000 pay.
