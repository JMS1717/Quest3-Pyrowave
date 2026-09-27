# Pacing sweep: the decision gate

The question: does ALVR's pacing convert processing headroom into lower motion-to-photon latency,
or does it merely redistribute time among the telemetry stages?

**Answer: Outcome B. It redistributes.** Panel native, 600 Mbps, everything fixed except the two
pacing controls, ~2000 frames per arm.

| arm | buffering | pacing | total mean | total p50 | decoder_queue | vsync_queue |
|---|---|---|---|---|---|---|
| Q-CONTROL | 2.0 | on | 78.11 | 77.53 | 11.82 | 21.29 |
| P-ON-20 | 2.0 | on | **78.18** | 81.21 | 8.12 | 25.28 |
| P-ON-10 | 1.0 | on | **81.09** | 81.93 | 6.55 | 28.22 |
| P-OFF-20 | 2.0 | off | 92.37 | 93.02 | 13.97 | 28.71 |
| P-OFF-10 | 1.0 | off | 97.28 | 97.72 | 7.92 | 30.77 |

## Halving the buffer moved time sideways, not down

From 2.0 to 1.0 buffered frames with pacing on:

- `decoder_queue` **-1.57 ms** (8.12 → 6.55)
- `vsync_queue` **+2.94 ms** (25.28 → 28.22)
- `total` **+2.91 ms** (78.18 → 81.09) — *worse*

This is exactly the predicted failure mode. The frame stops waiting in the decoder queue and
starts waiting for the display deadline instead. `input_acquired` does not move, so
`predicted_display_time - input_acquired` does not move, and the total is unchanged but for noise
in the wrong direction.

## Pacing off is not a latency option

+14.2 ms at buffering 2.0 and +16.2 ms at 1.0. `server_compositor` jumps from 0.28 ms to
**5.82 ms**, and `network` p99 blows out to 40.5 ms. This matches the earlier finding that
free-running overshoots its bitrate target; it is not a trade worth revisiting.

## Two things the tails show that the means hide

**`vsync_queue` saturates.** Its max is 32.3-33.8 ms in every arm and its p99 is 28.6-33.5,
while its min is ~15 ms. It is clamped by the runtime's prediction horizon, which is what a
boundary condition looks like — not a queue that can be drained.

**The pipeline already delivers under the bar on its fastest frames.** P-ON-20 has a total min of
**52.23 ms** against a p50 of 81.21. The fast path exists; nothing reaches it consistently. That
is a scheduling result, not a throughput one, and it is the most encouraging number here.

## The third knob: pacing headroom

`pacing_headroom_us` shortens the server's wait for vsync, shifting when frame production starts —
a different mechanism from buffering, and the one closest to "start the frame later". Same
geometry, buffering fixed at 1.5.

| headroom | total | game_time | server_compositor | decoder_queue | vsync_queue |
|---|---|---|---|---|---|
| 0 us | 85.40 | 17.64 | 0.25 | 3.91 | 27.01 |
| 2000 | 80.54 | 5.34 | 2.13 | 6.72 | 27.19 |
| 4000 | 91.53 | 7.42 | 4.35 | 11.36 | 31.01 |
| 6000 | 84.02 | 6.22 | 5.30 | 12.06 | 28.11 |

No monotonic improvement, and the headroom reappears elsewhere: `server_compositor` climbs
0.25 → 5.30 ms with it, almost exactly the sleep that was removed, and `decoder_queue` climbs
3.91 → 12.06. Sideways again.

This arm is noisier than the buffering one and `game_time` confounds it badly — H-0 carries 17.64
ms of it against 5-7 ms elsewhere. Netting `game_time` out gives 67.8 / 75.2 / 84.1 / 77.8, which
if anything trends the wrong way. The honest reading is not "headroom hurts by X" but **no setting
of any of the three knobs produced a total below the control**, across roughly ten arms.

## What this means for PyroWave

PyroWave's ~22 ms of processing saving at p50 (encoder 12.93 → 0.25, decoder 16.79 → ~6.8) remains
real, and remains **headroom rather than latency** until something spends it. This sweep shows the
two obvious candidates do not: buffering moves time between queues, and pacing-off is harmful.

So codec work is not the bottleneck any more. Three independent controls — buffered frames,
frame pacing on/off, and pacing headroom — all redistribute time without moving the total. The
target is whether frame production and tracking acquisition can start later relative to the
display deadline, and nothing currently exposed does that.

## Caveats

`game_time` varied across arms (4.87 to 16.33 ms), so it is a confound. It does not explain the
buffering result — between P-ON-20 and P-ON-10 it moved only 0.63 ms against a 2.91 ms total
change — but it does inflate P-OFF-10.

Every segment logged "gaze never moved", so these are static-scene numbers with the headset resting
rather than worn.

A methodological note for any repeat: `game_time` is an uncontrolled input that swings 0.7 to 109
ms and differs several-fold between arms. It is SteamVR's own render time and nothing in the sweep
fixes it. Any future pacing comparison should either hold the rendered content constant or report
totals net of `game_time`, otherwise the noise is comparable to the effect being measured.
