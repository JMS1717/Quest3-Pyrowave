# Whole-stack scorecard, October 7

State of the stack against the owner's target in [AGENTS.md](../AGENTS.md). Every number below
comes from a repo document, linked in each section.

- **Released:** v0.1.0-alpha.9, built from `.63` ([RELEASE-alpha.9.md](RELEASE-alpha.9.md)).
  alpha.8 is the rollback.
- **On main:** `.64` (merge `d27cc68`). It adds numbered wired video slices, a 1 s writer timeout
  and a skip for packed frames that cannot be converted. Nothing has been released from it yet.
  See [HANDOFF.md](HANDOFF.md#october-7-late-64-on-main-pr-18-merged-with-alpha9).

The claim categories are kept apart, as AGENTS.md requires. A short screen can reject a candidate.
It cannot prove sustained FPS, thermals, perceptual quality or latency.

## Target and status

| Target | Value | Best measured | Kind of evidence | Status |
| --- | --- | --- | --- | --- |
| PC render per eye | 3072x3216 | used in the October 7 screens at 207 Hz | short live screen | works with test scenes; no real game measured at that size and rate |
| Stream per eye | 2080x2208 | 2080x2208 at 207 Hz | short live screen | works |
| Refresh | 207 Hz | 207 Hz accepted on the native panel mode | runtime acceptance | accepted |
| Fresh FPS | about 200-207 | 194-197 (USB), 189-195 (Wi-Fi 6E); 199.5 with opt-in release_fd + 6 ms hold | short live screen | 3-13 below the target; sustained not measured |
| Bitrate | 1000-1500 Mbit/s | Haar over USB: 1500 costs about 4 fresh FPS against 1000; Wi-Fi limit between 1250 and 1500 | short live screen | 1000 works on USB and Wi-Fi; 1500 only on USB |
| Optical motion-to-photon | under 30 ms | not measured | none | **unestablished** |
| Foveation | none visible | none by default | configuration | met |
| Image quality | no pixelation or blocky colour against Virtual Desktop | Haar adds 8-px block edges; CDF 5/3 does not | offline metric, in-headset screenshots | **unestablished** against Virtual Desktop |

## Runtime acceptance

Sources: [REFRESH-RATES.md](REFRESH-RATES.md), [HIGH-REFRESH.md](HIGH-REFRESH.md),
[RELEASE-alpha.9.md](RELEASE-alpha.9.md).

- **Rates:** any whole rate from 144 to 240 Hz can be requested. A request counts only when
  `xrGetDisplayRefreshRateFB` and three consecutive `xrWaitFrame` periods match within 0.5 %.
- **Panel modes:** 72-207 Hz run the 4128x2208 panel mode (2064x2208 per eye). 240 Hz exists
  only as a 3104x1664 scaled mode (1552x1664 per eye), so 240 Hz results are not full-resolution
  results.
- **Switching:** the PC switches the panel over USB adb only. Over Wi-Fi the properties must be
  set by hand ([REFRESH-RATES.md](REFRESH-RATES.md#240-hz-developer-experiment)).
- **Cadence:** a no-decode cadence probe on `.55` held 206.9 FPS for 15 s at 207 Hz, with no
  skipped slots ([PR-9-REVIEW.md](PR-9-REVIEW.md)).
- **Known trap:** HorizonOS writes `debug.oculus.refreshRate=72` when a VR app starts with the
  property empty. Client relaunches sometimes come up at 72 or 90 Hz. Check the effective display
  period after every relaunch ([BITRATE.md](BITRATE.md#cdf-53-at-the-owners-690-mhz-gpu-clock-october-7)).

## Standalone decode budget

The 207 Hz frame period is 4.83 ms. Source: [DECODER-V2.md](DECODER-V2.md). All rows below are
`decoder_ab` runs on the Quest of a synthetic 4160x2208 4:2:0 frame at the 604 KB cap (1000 Mbit/s
at 207 Hz). No compositor, network or eye pass is included.

| Decoder | Clock | Total p50 |
| --- | --- | --- |
| Haar, haar32 mode 3, levels 0-3 packed | 690 MHz | 1.50-1.51 ms |
| CDF 5/3, V2 mode 3, levels 0-3 packed | 690 MHz | 1.94-1.96 ms |
| CDF 5/3, stock decoder | 599 MHz | 7.88 ms |

- Both shipping decoders fit the frame period alone, with room to spare.
- Mode 5 (packed YCbCr into the eye-copy buffer, the default) decodes at the same speed as mode 3
  standalone. Its gain is the conversion pass it removes.
- Live, decode takes longer than on the bench: Haar GPU decode p50 is 2.67 ms (p90 3.50 ms) at
  207 Hz. With the direct eye copy the live client also pays about 1.05-1.10 ms for the eye pass
  on the same GPU.

## Short live screens

10-12 s windows after a 3-5 s settle, 60 deg/s pan of `quality_scene`, 207 Hz, 2080x2208 per eye
from 3072x3216, 4:2:0, no foveation, mode 5. Fresh FPS is the client's `Q3PW_FRESH` taken count
or the frame trace's taken count, not VrApi FPS.

### Best configuration, USB

Haar, 1000 Mbit/s, two wired video connections, maximum GPU clock (690 MHz), direct eye copy
(`debug.q3pw.direct_eye_copy=1`) with its raw sRGB write. The direct eye copy is the default since `.117`;
with `debug.q3pw.direct_eye_copy=0` the client uses ALVR's staging renderer, and the eye-pass numbers below do not apply.
Source: [FRAME-TRACE.md](FRAME-TRACE.md) and the afternoon table in
[HANDOFF.md](HANDOFF.md#october-7-afternoon-207-hz-trace-and-scorecard).

| Measure | Value |
| --- | --- |
| Fresh FPS (valid blocks, memory clock 2736 MHz) | 194-197 |
| Published FPS | 203 |
| Display periods seen by the render loop | 195-200 per second |
| Superseded + empty selections | about 9 + 4 per second |
| Haar GPU decode | 2.67 ms p50, 3.50 ms p90 |
| Eye pass GPU | 1.05-1.10 ms p50 (was 1.28 ms) |
| Frame age at display (predicted display minus tracking time) | 30.4 ms p50 |
| ALVR latency estimate | about 30-33 ms (not motion-to-photon) |

Opt-in only: `release_fd` with `frame_hold_us=6000` reached 199.5 fresh FPS but raised ALVR's
latency estimate by 5.4 ms. It missed its gate and stays off.

### Bitrate, USB

Sources: [BITRATE.md](BITRATE.md#owner-settings-at-207-hz-where-bitrate-stops-paying-october-7)
(Haar) and
[BITRATE.md](BITRATE.md#cdf-53-at-the-owners-690-mhz-gpu-clock-october-7) (CDF 5/3, 690 MHz).

| Mbit/s | Haar fresh FPS | CDF 5/3 fresh FPS (mean) |
| --- | --- | --- |
| 700 | not run in this sweep | 185.1 |
| 1000 | 193.6-198.0 | 178.9 |
| 1500 | 190.6-193.8 | 164.2 |
| 2000 | 167.4-170.1 | not run |

- Haar decode barely changes with bitrate (2.78 to 3.00 ms). Its loss at 2000 Mbit/s is the link:
  a frame takes longer than 4.83 ms to arrive over adb.
- CDF 5/3 decode grows with bitrate (2.96 to 3.50-4.01 ms), and the GPU is 98 % busy, so each step
  up costs frames.
- Two parallel wired connections cut ALVR's network stage by about 0.7 ms at 1000 Mbit/s and 1.8 ms
  at 1500. Fresh FPS did not change, because decode set the rate.

### Wi-Fi 6E

Source: [WIRELESS.md](WIRELESS.md). `.63`, 6 GHz, PC on 2.5 GbE, Haar, GPU level 7 set by hand.

| Bitrate | Fresh FPS | ALVR network p50 / p99 |
| --- | --- | --- |
| 1000 Mbit/s | 189-195 | 6.1 / 10-13 ms |
| 1250 Mbit/s | 190-192 | 7.0-7.2 / 21-49 ms |
| 1500 Mbit/s | frames about 380 ms late; the link's limit was exceeded | |
| Auto (max 1500, 8 ms limit) | 197-200, partly at a lower memory clock | 4.4 ms; settles at about 550 Mbit/s |

USB network p50 is about 2.7 ms with two wired connections, so Wi-Fi adds about 3.4 ms.

### `.64` check

Local build of `9145530` over Wi-Fi 6E, 207 Hz, Haar, 1000 Mbit/s: 193.9 fresh FPS (memory clock
changed mid-block) and 190.6, network 6.2 ms p50, correct colours in both eyes. That matches
alpha.9 (190.0 and 196.5).

**Not checked on `.64`:** the USB parallel wired path that the slice numbering changes, and a USB
unplug/replug with two wired connections. USB adb was offline.

### Other profiles

| Profile | Stream per eye | Fresh FPS | Note |
| --- | --- | --- | --- |
| Native 120 Hz | 2064x2208 | about 119 | measured before the faster decoder |
| 240 Hz scaled panel | 1440x1536 | 223-230 | measured before the faster decoder; scaled panel |

### Where the last frames go at 207 Hz

From one 12 s frame-trace window ([FRAME-TRACE.md](FRAME-TRACE.md)):

- 206.3 frames arrived per second, 203.1 were decoded and published, 194.3 were shown fresh.
- The loss is after publication. `gl.finish` sometimes pushes a frame past its slot, so the loop
  saw 195 display periods instead of 207.
- Publications arrive with a 4.1-6.0 ms p5-p95 spread around the 4.83 ms period. About 8.8 per
  second are superseded and 4.0 selections per second find nothing.
- 44 of 48 empty selections had their packet on the headset and were still decoding.
- The memory clock moves between 2092, 2736 and 3196 MHz per session and shifts results by up to
  about 12 FPS. It cannot be pinned.

## Sustained gameplay

**Unestablished.**

- No sustained run at the 207 Hz profile, over USB or Wi-Fi. "(measured)" in the profile names
  means 10-12 s screens.
- Sustained thermals at the 690 MHz GPU clock are not measured. The longest 690 MHz windows were
  12 s, with the battery at 38-41 C ([HIGH-REFRESH.md](HIGH-REFRESH.md)).
- No real game has been measured at 207 Hz. The scenes were `quality_scene` and a stationary chart.
  A game must also render 3072x3216 at 207 Hz on the PC.

## Perceptual quality

**Unestablished against Virtual Desktop.**

- **Owner playtest (October 6):** the Haar stream looked "noticeably pixelated" next to Virtual
  Desktop, with blocky colour and colour bleed on edges ([DECODER-V2.md](DECODER-V2.md#why-haar-is-what-looks-pixelated)).
- **Offline metric**, 60 deg/s pan, 4:2:0, 207 Hz byte caps:

  | Mbit/s | Haar PSNR-HVS-M | CDF 5/3 PSNR-HVS-M | Haar added block edges |
  | --- | --- | --- | --- |
  | 700 | 16.9 | 19.0 | +1.74 |
  | 1000 | 18.6 | 20.8 | +1.19 |
  | 2000 | 23.5 | 25.8 | +0.51 |

  Haar is the only wavelet that adds 8-px block edges. The scene is dense everywhere, so compare
  rows only.
- **In-headset screenshots:** both decoders decode correctly. Haar's soft spheres step in blocks;
  CDF 5/3's are smooth.
- **Trade-off:** CDF 5/3 costs about 2 fresh FPS against Haar at 700 Mbit/s and 690 MHz, and more
  at higher bitrates. Haar stays the default wavelet.
- **Not done:** the planned offline comparison of CDF 5/3 and Haar at 1000, 1300 and 1500 Mbit/s
  (P3), and any side-by-side in-headset comparison with Virtual Desktop.

## Optical latency

**Unestablished. Nothing optical has been measured.**

- ALVR's latency figure is an estimate, not motion-to-photon. At 207 Hz, Haar, 1000 Mbit/s over
  USB it is about 30-33 ms ([BITRATE.md](BITRATE.md#owner-settings-at-207-hz-where-bitrate-stops-paying-october-7)
  gives 32.1 ms p50). Over Wi-Fi with Auto it was 28 ms.
- Frame age at display, measured on one clock by the frame trace, is 30.4 ms p50. It covers
  tracking time to predicted display time, not photons.
- Tooling exists: the server latency stamp and `tools/quest3/latency_clock.py`
  ([OPTICAL-LATENCY.md](OPTICAL-LATENCY.md)). It measures composition-to-photon. Motion-to-photon
  is larger by the game render and tracking uplink.
- A valid result needs a camera at 240 fps or more and at least 50 pairs, reported as median and
  p90 with the rate, bitrate and settings (P4).

## Not established

- Sustained 200+ fresh FPS at 207 Hz, in a game, with thermals.
- Optical motion-to-photon under 30 ms.
- Image quality on par with Virtual Desktop.
- `.64`'s USB parallel wired path and USB unplug/replug with two wired connections.
- Wi-Fi beyond one 6 GHz link: other routers, 5 GHz, a busy network.

## Earlier scorecard: October 5, 120 Hz

The previous version of this page covered `.48`-`.55` at 120 Hz, 2080x2208, 1000 Mbit/s, LOW decode
priority, on a stationary chart:

- About 117-118 unique displayed targets per second against 120.7 sent by the server.
- GPU decode about 5.9 ms p50 inside an 8.0 ms fence. Every steady-state empty wait expired while
  decode was still running (56 of 56 on `.50`).
- A 6 ms selection wait on `.51` did not move unique targets (117.7 against 117.9 per second) and
  roughly doubled compositor stale counts. The 4 ms default stayed.
- Fused final colour on `.54` was byte-exact but gave no gain (117.3-117.8 against 118.2). It stays
  off ([FUSE-COLOR.md](FUSE-COLOR.md)).
- The full `.55` integration results are in [PR-9-REVIEW.md](PR-9-REVIEW.md).

The faster decoders of October 6-7 ([HAAR32.md](HAAR32.md), [PRESENT-YCBCR.md](PRESENT-YCBCR.md),
[DECODER-V2.md](DECODER-V2.md)) replaced that 120 Hz bottleneck with the 207 Hz picture above.
