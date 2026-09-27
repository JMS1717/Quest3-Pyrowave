# Where ALVR attributes each millisecond in the current configuration

Every stage ALVR reports, mapped to the subsystem it most plausibly belongs to. **This is an
attribution of one pipeline's 80 ms, not a measurement of what each component physically
requires.** The compute stages (encode, decode, compose) correspond fairly closely to hardware
work; `network`, `decoder_queue` and `vsync_queue` are increasingly abstract timing categories
and must not be read as immutable hardware costs. Measured figures are
the B-15 panel-native baseline (600 Mbps, 3552x3840/eye, 72 Hz).

| # | stage | ms | machine | silicon block | kind of work |
|---|---|---|---|---|---|
| 1 | `game_time` | 5.4 (0.7–109) | PC | **RTX 3090 shader cores** + host CPU | the game's own render |
| 2 | `server_compositor` | 0.33 | PC | **RTX 3090 shader cores** (D3D11) | compose, FFE warp, colour |
| 3 | `encoder` | 13.1 (6.2–13.1) | PC | **NVENC ASIC** — fixed-function, *not* the shader cores | H.264 encode |
| 4 | `network` | 11.9 | both + air | packetisation, socket buffering, TCP scheduling, NIC → AP → radio, receiver assembly | transit + software |
| 5 | `decoder` | 16.0 | headset | **Adreno 740 video block** — fixed-function, *not* the shader cores | H.264 decode |
| 6 | `decoder_queue` | 5.4 (5.4–11.9) | headset | **no silicon** — client RAM, Kryo CPU | waiting |
| 7 | `client_compositor` | 1.5 | headset | **Adreno 740 shader cores** (GLES) | staging blit, foveation inverse warp |
| 8 | `vsync_queue` | 26.6 (21.4–28.0) | headset | **no silicon** — the runtime's prediction lead *as this client uses it* | waiting |
| | **total** | **80.07** | | | |

## The headline: only ~37 ms of 80 is anyone computing

| category | ms | share |
|---|---|---|
| PC compute (1+2+3) | 18.8 | 23% |
| transit (4) | 11.9 | 15% |
| headset compute (5+7) | 17.5 | 22% |
| **waiting (6+8)** | **32.0** | **40%** |

PC and headset compute are almost exactly balanced — 18.8 against 17.5 ms. Neither side is the
bottleneck. **The largest single category is waiting, and it does no work at all.**

That is the same conclusion the pacing sweep reached from the other direction, now visible in the
hardware: there is no silicon to optimise for 40% of the budget.

## Three separate blocks on the Qualcomm side

This matters for "can the Qualcomm portion be handled better", because the headset is not one
processor:

- **Video block** (fixed-function decode). Runs MediaCodec's `c2.qti.*` decoders. Owns stage 5.
- **Adreno shader cores** (the GPU proper). Owns stage 7, and would own decode under PyroWave.
- **Kryo CPU cores**. Own the client logic, TCP, and MediaCodec orchestration. Own stage 6's
  bookkeeping.

Measured thermals show these are genuinely distinct: during a sweep the CPU read **76.0 °C
(peak 83.5)** while the `video` zone read **69.9 °C** — the decode block runs ~6 °C cooler than the
CPU, and per-segment the CPU rose 3x faster (+6.8 vs +2.3 °C). **The CPU is the thermal hotspot,
not the decoder.**

## What PyroWave moves, in hardware terms

It does not make an existing block faster. It **moves two stages onto different silicon**:

| stage | today | under PyroWave | measured |
|---|---|---|---|
| 3 encode | NVENC ASIC | **RTX 3090 shader cores** (Vulkan compute) | 13.1 → 0.25 ms |
| 5 decode | Adreno video block | **Adreno shader cores** (Vulkan compute) | 16.0 → 5.3 ms |
| 7 compositor | Adreno shader cores | unchanged, **+1.5 ms** conversion pass on GLES | 1.5 → ~3.0 ms |

So PyroWave vacates both fixed-function blocks and puts the work on general-purpose shader cores
at both ends. Net compute: PC 18.8 → 5.9 ms, headset 17.5 → 8.3 ms.

**The thermal consequence is the part to watch.** Decode moves off the coolest block on the
headset and onto the GPU. GPU sensors read **85.9–86.6 °C** during PyroWave bench runs, against a
throttle point near 83 °C. The video block being idle does not help, because the heat simply
appears somewhere else — and somewhere hotter. That is a real cost that the latency numbers do not
show.

## Where the opportunity is, by block

**Fixed-function blocks are not the problem.** NVENC at 13.1 ms is slow for an ASIC but PyroWave
already beats it by 50x on shader cores. The Adreno video block at 16.0 ms is the single largest
compute stage, and PyroWave beats it by 3x. Both are addressed.

**The shader cores have headroom at both ends.** The 3090 spends 5.7 ms on game + composite;
PyroWave encode adds 0.25. The Adreno spends 1.5 ms compositing; PyroWave decode adds 5.3. Neither
GPU is near saturation — the Adreno's constraint is thermal, not throughput.

**The Wi-Fi link is probably not the constraint, but `network` is not airtime.** 11.9 ms against a
measured link of 860–978 Mbps carrying ~600 Mbps is far more than the radio needs; the stage
also holds packetisation, TCP scheduling, sender and receiver buffering and frame assembly, and
the transport is TCP at very high bitrate. Which of those dominates is unmeasured. And decode is resolution-bound rather than bitrate-bound
(8x the bits cost 0.62 ms), so bitrate is nearly free on both the radio and the decoder.

**The 32 ms of waiting has no hardware owner.** `vsync_queue` belongs to the Android XR
compositor's prediction horizon — it saturates at 32.3–33.8 ms in every arm and no ALVR setting
reaches it. `decoder_queue` is ALVR's own buffering and moves when buffering changes, but the
pacing sweep showed that moving it just relocates the wait rather than removing it.

## What this does and does not say

In this configuration, ALVR attributes roughly:

```
transit 11.9  +  waiting 32.0  =  43.9 ms
```

to stages that involve no compute. Against a 60 ms bar that would leave ~16 ms for all the
compute, which PyroWave already fits inside (14.2 ms) — so *within ALVR's current scheduling*,
faster codecs alone cannot reach the bar, and the pacing sweep showed the same from the other side.

**It is not a floor.** An earlier draft called it one; that overreached. `vsync_queue` is
`predicted_display_time − now` at submit, which is how far ahead *this client* targets a display
time. Nothing here proves that every streaming architecture on this headset must wait ~26 ms
before photons, and there is a report of another Galaxy XR PCVR implementation at 30–40 ms
(definition and conditions not yet established — see `latency-budget.md`). Until that comparison
is made, the 43.9 ms is an observation about ALVR, not a property of the Galaxy XR.

Three things this table also does not distinguish, and should:

- **processing latency** — time the work genuinely takes;
- **scheduling lead** — how far ahead the runtime and client deliberately target;
- **effective motion-to-photon** — the age of the newest head pose that actually reaches the panel,
  which late-stage reprojection can make younger than the game frame.

ALVR's total is the second. The 60 ms bar is about the third. They are not the same number.

## Caveats

The block attributions for MediaCodec (video block) and NVENC (dedicated ASIC) are inferred from
the decoder names (`c2.qti.avc.decoder`), the separate `video` thermal zone, and vendor
architecture rather than from direct measurement of which die area is active. The conclusion does
not depend on them being exactly right: what matters is that decode and encode run on blocks
distinct from the shader cores that PyroWave would use, which the thermal zones do demonstrate.

`game_time` is an uncontrolled input swinging 0.7–109 ms; the 5.4 ms figure is one run's mean and
should not be treated as a property of the system.

None of the numbers here establish a minimum for Galaxy XR PCVR streaming. They establish where
one implementation spends its time.
