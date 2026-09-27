# PyroWave over UDP: bitrate ladder and receiver fixes

Panel-native geometry (3552x3840/eye, centre 0.20, gaze on), 72 Hz, PyroWave 4:4:4 over UDP.
`xrbench-runs\pyro-ladder` on the PC; events.json per segment copied here.

## Ladder, first receiver build (16:35)

| Mbps | fps | fps min | decoder ms | total ms | lost | worn |
|---|---|---|---|---|---|---|
| 100 | 71.8 | 14 | 12.8 | **77** | 0 | yes |
| 200 | 71.8 | 12 | 13.6 | 375 | 0 | part |
| 300 | 71.7 | 36 | 13.8 | 460 | 0 | no |
| 400 | 64.8 | 36 | 16.5 | 539 | 0 | no |
| 500 | 64.2 | 36 | 16.8 | 470 | 0 | no |
| 600 | 69.3 | 36 | 14.8 | 396 | 0 | no |

Only the 100 Mbps total is a latency measurement. It is the best cell measured on this project:

| stage | ms | |
|---|---|---|
| encoder 3.8, network 3.2, decoder 12.8, compositors 1.5 | **21.3** | work |
| game_time 16.9, decoder_queue 7.6, vsync_queue 31.6 | **56.1** | vsync-aligned waits |

Against H.264 at the same cell, encode fell 13.1 -> 3.8 and decode 16.0 -> 12.8, and game_time
rose 5.4 -> 16.9 and vsync_queue 26.6 -> 31.6: the server's frame pacing holds the pose sample at a
fixed distance ahead of display, so a faster pipeline waits longer at both ends. The 60 ms bar is
now a pacing problem, not a codec problem.

## Three defects in the first UDP receiver, all fixed in the 19:00 build

1. **Stereo did not fuse.** The per-eye foveation centre travels in the TCP `VideoPacketHeader`;
   UDP sent none, so the client's inverse warp sat at a fixed centre while the server foveated
   around each eye's gaze. Fix: the centre is in every `PyroWavePacketHeader` (32 bytes now).
   Wearer's verdict after the fix: "clear, 3D is perfect, no jitter".
2. **Latency plateaued at 450-600 ms with the headset at rest** (an unworn H.264 segment sits at a
   flat 98 ms, so this was the receiver, not the wearer). The receiver decoded every frame in
   arrival order with no way to skip, so once behind by any amount the 16 MB socket buffer filled
   and stayed full: the plateau was 600 ms at 200 Mbps and ~480 at 300, a fixed byte queue. Fix:
   drain the socket after every read and skip a completed frame when a newer one is already
   queued (`skip_for_newer`).
3. **The receiver's counters never reached any log.** Once connected, the client's `info!` lines
   are forwarded to the server, and the server did not record them either. Fix: the receiver
   writes straight to logcat under tag `PYROWAVE-UDP`.

Also in that build: `XR_EXT_performance_settings`, requesting SUSTAINED_HIGH for CPU and GPU.
Confirmed from `DynamicPolicyManager`: votes went from POWER_SAVING to `CPU HIGH, GPU HIGH`.

## Verify segment, 300 Mbps, worn (19:19, `pyro-verify2`)

| stage | mean | p95 |
|---|---|---|
| encoder | 7.7 | 11.1 |
| network | 7.5 | 12.1 |
| decoder | 16.4 | 20.5 |
| vsync_queue | 36.2 | 42.4 |
| game_time | 11.6 | 20.5 |
| **total** | **82.2** | 94.2 |

Total by tenth: 75 76 74 77 85 87 87 87 87 86 90 -- bounded, no plateau. 0 packets lost.

Receiver counters: 2229 decoded, **639 skipped**, 11 dropped, 0 decode failures. GPU decode
7-9 ms, but submit->fence 12-18 ms against the 13.9 ms frame period, at thermal status 3
(83.8 C). 22 % of frames are being skipped because the hot GPU cannot fence a frame inside a
period. The receiver is now showing the problem instead of hiding it; the fence time is the next
number to attack and it is a thermal one.

## Two test-method findings

- A **disconnected RDP session** leaves SteamVR rendering green (the harness reports "the host is
  not delivering a picture"). `qwinsta` showed the `game` session `Disc` and an empty console;
  `tscon 1 /dest:console` reattaches it without a reboot.
- Every segment reaches thermal status 3-4 at 84-93 C within ~90 s; the harness cools to ~71 C
  between cells. Decode-bound numbers from hot cells are not comparable with the cool first cell.

## Late start sweep (19:29, `pyro-latestart`): the server's frame start does not move the total

PyroWave UDP, 200 Mbps, 72 Hz, worn throughout. `pacing_delay_us` releases SteamVR that many
microseconds after the virtual vsync.

| delay ms | total | game_time p50 | decoder | dec queue | vsync queue | fps mean | fps p50 |
|---|---|---|---|---|---|---|---|
| 0 | 82.1 | 15.9 | 13.3 | 5.8 | 35.1 | 71.5 | 72 |
| 4 | 84.0 | 17.8 | 16.3 | 1.1 | 38.0 | 63.1 | 72 |
| 8 | 81.7 | 15.5 | 13.6 | 4.0 | 36.6 | 70.3 | 72 |
| 12 | 83.7 | 16.2 | 16.2 | 1.1 | 39.0 | 63.1 | 72 |
| 16 | 86.3 | **31.8** | 17.9 | 1.3 | 36.8 | 42.6 | **36** |
| 20 | 85.6 | 30.0 | 18.0 | 1.1 | 37.3 | 39.0 | 36 |

Flat at 82-84 ms across 0-12 ms of delay, then at 16 ms (past the 13.9 ms frame period) the
server misses every other vsync and fps halves. There is no knee to sit before: the delay is
absorbed one for one, mostly by `vsync_queue` (35 -> 39) and `decoder_queue` shrinking to nothing.
So `input_acquired` is not being moved later relative to display by starting the server later --
the client's presentation lands on the same vsync and the runtime's prediction lead is what sets
the interval. The 35-39 ms `vsync_queue` is the runtime's lead, roughly two frame periods plus
phase, and no ALVR-side pacing control reaches it.

The `fps mean` dips at 4 and 12 ms are heat (decoder 16 ms, 500+ frames skipped by the
receiver), not the delay: 0 and 8 ms ran cooler and held 70+.

**Conclusion:** at 72 Hz the pipeline's floor is ~82 ms and it is set by the client runtime's
prediction lead plus one server frame. The remaining levers are on the client side: refresh rate
(a two-frame lead is 22 ms at 90 Hz against 28 at 72, see `panel-native-holds-at-90hz`), and
anything that shortens the runtime's lead itself.

## Phase lock (19:56-20:12): null, and the reason is the finding

Server-side controller (`ALVR_PHASE_LOCK=1`) steering the virtual vsync from the client's
per-frame `video_decoder_queue` (early) and lost `vsync_queue` (late). 200 Mbps, worn.

| cell | lock | total | decoder | dec queue | vsync queue | fps |
|---|---|---|---|---|---|---|
| OFF-1 | off | 86.0 | 16.3 | 1.1 | 39.2 | 62.9 |
| ON-1 | on (walking) | 77.3 | 13.6 | 3.8 | 34.8 | 70.7 |
| ON-2 | on (walking) | 79.3 | 13.7 | 3.5 | 36.1 | 70.1 |
| OFF-2 | off | 81.9 | 16.5 | 1.1 | 38.5 | 62.6 |
| ON-2 | on (gated) | 81.1 | 14.3 | 1.4 | 36.7 | 68.4 |

Gated on vs off: 81.1 vs 81.9. The 77/79 cells were cool (decoder 13.6), the off cells hot
(16.4); heat, not the lock. The controller's own log showed decoder_queue ~1 ms and lateness 0
whatever it did to the clock (it moved it -119 ms). **The client loop is arrival-driven**: it
blocks for the decoded frame and submits at once; the runtime hands out the next slot from there.
Phase between server and headset clocks cannot matter in that regime, which is also why the
late-start sweep was flat.

## Unworn control (20:22): unattended latency runs are valid

Sensor covered, headset on the desk, same cell as OFF-1/2: total 77.2, decoder 13.3, fps 71.7,
0 lost. Inside the worn range (cool end). The waiting redistributes (decoder_queue 8.0,
vsync_queue 31.9 vs 1.1/38.5 worn) with the same sum. Latency sweeps no longer need a wearer;
the "gaze never moved" warning is about foveation stats only.

## Early poll (20:30-20:39): frame in hand before xrWaitFrame -- also null

Client polls for the decoded frame *before* xrWaitFrame (`debug.xrwired.early_poll`), so the
runtime's predicted display time is chosen with the frame in hand. Unattended, 200 Mbps.

| cell | total | game_time | decoder | dec queue | vsync queue | fps |
|---|---|---|---|---|---|---|
| OFF-1 | 85.6 | 18.9 | 13.4 | 5.0 | 34.8 | 71.0 |
| ON-1 | 86.6 | 18.2 | 13.1 | 9.3 | 31.3 | 71.1 |
| OFF-2 | 82.8 | 13.3 | 13.5 | 3.7 | 36.0 | 70.3 |
| ON-2 | 77.5 | 12.5 | 13.2 | 8.1 | 29.3 | 71.1 |

Consistent structural shift both rounds -- vsync_queue down 3.5-6.7 ms, decoder_queue up ~4.3 --
and a net of +1.0 / -5.3 ms: noise. The runtime paces xrWaitFrame to its own vsync schedule and
assigns the display slot from when the call *returns*, so holding the frame across the call only
moves the wait from one side of it to the other.

## Where this leaves the 60 ms bar at 72 Hz

Three pacing levers (late start, phase lock, early poll) each moved waiting between stages and
none moved the total. What the total is made of, at 72 Hz, cool headset:

| | ms | movable by ALVR? |
|---|---|---|
| work: encode 3.8, network 3.2, decode ~13, compositors 1.5 | ~21 | yes (decode: heat, async) |
| runtime lead, xrWaitFrame return -> display | ~30 | no (Google runtime) |
| one server frame period (SteamVR renders per vsync tick) | ~14 | only via refresh rate |
| tracking sample quantisation (client sends at 3x refresh) | ~4-5 | small |
| **floor** | **~70** | measured 77-86 |

Reaching 60 at 72 Hz needs the runtime's lead to shrink, which nothing exposed does. The one
lever whose arithmetic reaches 60 is **90 Hz**: period 11.1 ms cuts the lead (~2.2 periods) to
~24 and the server frame to ~11, giving ~21+24+11+4 = ~60 -- if decode fits 11 ms, which on a
hot GPU it does not (fence 12-18 ms at status 3). See `panel-native-holds-at-90hz`.

## 72 vs 90 Hz (20:44-20:53, `pyro-hz`, unattended): 90 Hz is gated by decode heat

| cell | display actually at | total | decoder | vsync queue | client fps p50 | receiver skipped |
|---|---|---|---|---|---|---|
| H72-100 | 72 | 86.2 | 13.2 | 29.5 | 72 | -- |
| H90-100 | **72** (switch never latched) | 80.0 | 13.3 | 31.1 | 72 | 811 / 4300 |
| H72-200 | 72 | 79.0 | 13.0 | 29.8 | 72 | -- |
| H90-200 | **90** for ~55 s | 82.3 | **17.1** | 36.2 | **45** | 1354 / 3600 |

`DynamicPolicyManager` traced live through H90-200: the OpenXR vote went to 90 at 20:51:12 and
the display followed ("Refresh rate: 90.0") until the client was stopped at 20:52:10. So the
runtime does grant 90 Hz. In H90-100 the vote never latched and the display stayed at 72 (fps
trace flat at 72 all cell) while the server sent 90/s, hence the skips.

At a real 90 Hz the client presented a median 45 fps: decode was 17.1 ms against an 11.1 ms
period (thermal status 3 from 20:51:38), the receiver skipped 38 % of frames, and the runtime's
lead grew to 36 ms rather than shrinking. **90 Hz cannot be evaluated for latency until decode
fits the period on a hot GPU.** The GPU work itself is 6-9 ms; the fence is what balloons.

Cheapest lever on decode time: **4:2:0 chroma** (the stream is 4:4:4 today). Half the chroma
samples is roughly a third less decode work and a smaller bitstream at the same bitrate; both
ends already carry the `chroma444` flag in the DecoderConfig blob, so it is a config A/B, not a
build. Then re-run this sweep.

## Resolution for bitrate (21:00-21:09): 60 % linear, and the first cell under 60 ms

The project's spec: render 60 % linear (2131x2304/eye, 36 % of the panel's pixels), gaze-foveated
centre 0.20 as always, so the encoded frame is **1984x896** (18 % of the rendered pixels, ~6 % of
a full-panel unfoveated frame). 4:4:4, worn.

| | X60-600 (72 Hz, 600 Mbps) | **X60-400-90 (90 Hz, 400 Mbps)** |
|---|---|---|
| display actually at | 72 | **90** (vote latched at 21:08:12, held) |
| total ms mean / p50 / p95 | 80.0 / 76.3 / 90.2 | **60.1 / 59.3 / 63.1** |
| game_time | 18.2 | 14.0 |
| encoder ms | 13.7 | 6.8 |
| decoder stage ms | 13.6 | 10.8 |
| decoder_queue | 5.6 | 3.5 |
| vsync_queue | 28.2 | **22.4** |
| GPU decode ms min / mean / p95 / p99 | ~4.0 (last) | **2.5 / 3.7 / 4.5 / 5.2** |
| convert ms mean / p99 | 0.7 | 0.9 / 2.3 |
| submit->fence ms mean / p95 / p99 | -- | **6.4 / 8.3 / 8.9** |
| client fps mean / median / p1 low | 68 / 72 / -- | **89.1 / 90.0 / 45** (one dip) |
| partial / dropped / skipped | 132 / 206 / 64 | 85 / 92 / 39 |
| late (stale) packets | 37,557 | 24,489 |
| packets lost | 0 | 0 |
| thermal status peak | 3 (CPU 81 C) | **1**; GPU 40.7 -> 64.5 C, CPU 43.8 -> 69.9 C |

Every stage moved the way the arithmetic said it would: vsync_queue 28 -> 22 (the runtime lead
at 11.1 ms periods), game_time 18 -> 14, and the fence is 6.4 ms mean against an 11.1 ms period
with a cool GPU. **Total 60.1 ms, the project's bar.** The tester's read of the 600 Mbps image:
"passable"; the 400 Mbps image is the tester's call (below).

Caveats, so this is not oversold:
- The headset started this cell at 41 C GPU after ten minutes idle; every other cell today
  started at ~70. Sustained-use thermals are the open question (`thermal_end` 1, GPU 64.5 C after
  90 s is encouraging, not settled).
- 24k late packets and 92 dropped frames say 400 Mbps is still near the per-frame transport
  limit at 90 Hz (4.4 Mbit per 11.1 ms period). Worth a 300 Mbps arm.
- The receiver's report line repeated ~100x per 720 frames (loop iterations that complete no
  frame); fixed in source (`last_report_frames`), not yet in the installed APK. The distribution
  lines above are from the first print of each report.

**Candidate operating point: 60 % linear, 400 Mbps, 4:4:4, 90 Hz, gaze-foveated centre 0.20.**

## 300 Mbps arm and the five-minute sustained run (22:00-22:13, unattended, sensor covered)

| | X60-400-90 (90 s, cool start) | **X60-300-90** (90 s) | **S60-400-90** (300 s, warm start 77 C) |
|---|---|---|---|
| total ms mean / p50 / p95 | 60.1 / 59.3 / 63.1 | **55.5 / 53.2 / 67.9** | 64.1 / 63.2 / 74.3 |
| total by tenth | 58..60 | -- | 58 65 65 64 65 65 65 65 67 63 |
| fps mean / median | 89.1 / 90 | 89.7 / 90 | 89.4 / 90, every tenth 89-90 |
| GPU decode mean / p99 | 3.7 / 5.2 | 3.7 | **3.8 / 4.7, flat for five minutes** |
| submit->fence mean / p99 | 6.4 / 8.9 | -- | 6.1 / 8.0 |
| partial / dropped | 85 / 92 of ~4,300 | **7 / 13 of ~3,600** | 332 / 331 of ~27,600 (1.2 %) |
| late packets | 24,489 | **1,225** | 75,176 |
| packets lost | 0 | 0 | 0 |
| hottest zone | 41 -> 65 C | 48 -> 65 C | 71 -> 78 C by 90 s, then **plateau 77-79 C**; status 2 from 90 s, 3 at 4.5 min |

**Sustained: it holds.** Ninety fps in every tenth of five minutes from a warm start, GPU decode
and fence unchanged from first minute to last, no packet loss. The cost of the warm start is
~4 ms of total (64 vs 60). The headset settles at 78 C, status 2-3 -- hotter than the cool cell,
cooler than every full-panel cell today (84-93 C, status 3-4).

**300 Mbps cleans the transport.** Dropped frames 92 -> 13, late packets 24k -> 1.2k, and 5 ms
off the total. At 4.4 vs 3.3 Mbit per 11.1 ms period the link (708-860 Mbps measured today) has
margin at 300 and almost none at 400. Image quality at 300 is untested by eye.

Operating point stands: **60 % linear, 4:4:4, 90 Hz, gaze centre 0.20, PyroWave over UDP**, at
300 Mbps if it looks as good as 400 did, else 400.

### 300 Mbps worn (22:26)

Total 64.3 ms mean (warm from the sustained run), fps 89.1 / 90 median, 0 lost, 71 dropped, 12.8k
late packets -- worn head motion costs some of the transport margin 300 had unworn. The tester's
read: 400 Mbps "looked good and smooth", 300 Mbps "decent and smooth". A shade behind, so the
operating point is **400 Mbps** when the link allows and 300 as the fallback; both hold 90 fps.
