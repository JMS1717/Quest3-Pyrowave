# Reviewed results

Sanitized measurement records, one file per experiment. Each JSON keeps the build, settings,
per-window numbers, the decision and its limits. The topic doc named after each line explains it.
Raw captures, logs and device identifiers stay private, outside the repo.

Most files are short screens: they can reject a candidate but do not prove sustained FPS, thermals,
perceptual quality or optical latency. See the [scorecard](../docs/WHOLE-STACK-SCORECARD.md) for
the current state.

**October 7 work has no file here.** The 207 Hz frame trace, Decoder V2 at 690 MHz, the 207 Hz
bitrate sweeps, parallel wired video and Wi-Fi results are recorded in
[FRAME-TRACE.md](../docs/FRAME-TRACE.md), [DECODER-V2.md](../docs/DECODER-V2.md),
[BITRATE.md](../docs/BITRATE.md) and [WIRELESS.md](../docs/WIRELESS.md).

## October 6

| File | Contents | Doc |
| --- | --- | --- |
| [HAAR32-2026-10-06.json](HAAR32-2026-10-06.json) | Multilevel Haar (haar32): exactness gates, standalone schedules and 207 Hz live cells | [HAAR32.md](../docs/HAAR32.md) |
| [HIGH-REFRESH-2026-10-06.json](HIGH-REFRESH-2026-10-06.json) | 120-240 Hz and above-native resolution screens, panel modes, GPU level and other levers | [HIGH-REFRESH.md](../docs/HIGH-REFRESH.md) |
| [FUSED-DEQUANT-HAAR-GPU-2026-10-06.json](FUSED-DEQUANT-HAAR-GPU-2026-10-06.json) | PR #8 fused dequant + level-0 Haar: correct, no GPU or fence gain; kept off | [HIGH-REFRESH.md](../docs/HIGH-REFRESH.md) |

## October 5

| File | Contents | Doc |
| --- | --- | --- |
| [PR-9-ACCEPTANCE-2026-10-05.json](PR-9-ACCEPTANCE-2026-10-05.json) | `.55` integration windows at 120 Hz and the 207 Hz no-decode cadence probe (206.9 FPS) | [PR-9-REVIEW.md](../docs/PR-9-REVIEW.md) |
| [FUSE-COLOR-GPU-2026-10-05.json](FUSE-COLOR-GPU-2026-10-05.json) | Fused final colour: standalone exact-pixel check on the Quest | [FUSE-COLOR.md](../docs/FUSE-COLOR.md) |
| [FUSE-COLOR-LIVE-2026-10-05.json](FUSE-COLOR-LIVE-2026-10-05.json) | Fused final colour: live A/B at 120 Hz, no gain | [FUSE-COLOR.md](../docs/FUSE-COLOR.md) |
| [HAAR-PAIRS-GPU-2026-10-05.json](HAAR-PAIRS-GPU-2026-10-05.json) | Dedicated Haar-pair kernels: standalone GPU correctness | [HAAR-PAIRS.md](../docs/HAAR-PAIRS.md) |
| [HAAR-PAIRS-LIVE-2026-10-05.json](HAAR-PAIRS-LIVE-2026-10-05.json) | Haar-pair kernels live: lower iDWT stage time, no delivery gain; kept optional | [HAAR-PAIRS.md](../docs/HAAR-PAIRS.md) |
| [ASYNC-LOW-NATIVE120-2026-10-05.json](ASYNC-LOW-NATIVE120-2026-10-05.json) | LOW priority + async eye copy at 120 Hz: less CPU wait, no delivery gain | [ASYNC-LOW.md](../docs/ASYNC-LOW.md) |
| [RELEASE-LOW-NATIVE120-2026-10-05.json](RELEASE-LOW-NATIVE120-2026-10-05.json) | LOW priority + release fence at 120 Hz: no delivery or p1 gain | [RELEASE-LOW.md](../docs/RELEASE-LOW.md) |
| [PAYLOAD-NATIVE120-2026-10-05.json](PAYLOAD-NATIVE120-2026-10-05.json) | 600-1000 Mbit/s payload isolation at 120 Hz; 1000 kept | [BITRATE.md](../docs/BITRATE.md) |
| [PRODUCER-OPPORTUNITY-NATIVE120-2026-10-05.json](PRODUCER-OPPORTUNITY-NATIVE120-2026-10-05.json) | Packet arrival vs decode opportunity at 120 Hz | [PRODUCER-OPPORTUNITY.md](../docs/PRODUCER-OPPORTUNITY.md) |
| [PRODUCER-PRERECORD-GPU-2026-10-05.json](PRODUCER-PRERECORD-GPU-2026-10-05.json) | Pre-recorded decode (`.46`): standalone native correctness | [PRODUCER-PRERECORD.md](../docs/PRODUCER-PRERECORD.md) |
| [PRODUCER-PRERECORD-47-2026-10-05.json](PRODUCER-PRERECORD-47-2026-10-05.json) | Pre-recorded decode `.47`: build review and native proofs | [PRODUCER-PRERECORD.md](../docs/PRODUCER-PRERECORD.md) |
| [PRODUCER-PRERECORD-48-2026-10-05.json](PRODUCER-PRERECORD-48-2026-10-05.json) | Pre-recorded decode `.48` live: active, no FPS gain, worse completion tails | [PRODUCER-PRERECORD.md](../docs/PRODUCER-PRERECORD.md) |
| [PUBLICATION-EVENT-LIVE-2026-10-05.json](PUBLICATION-EVENT-LIVE-2026-10-05.json) | Event-driven publication wait: ran without fallback, no consistent gain | [PUBLICATION-EVENT.md](../docs/PUBLICATION-EVENT.md) |
| [SURFACE-CHART-2026-10-05.json](SURFACE-CHART-2026-10-05.json) | Static Surface image: orientation and lifecycle pass; GLES video kept | [SURFACE-CHART.md](../docs/SURFACE-CHART.md) |

## October 4

| File | Contents | Doc |
| --- | --- | --- |
| [LIVE-FRESHNESS-AB-2026-10-04.json](LIVE-FRESHNESS-AB-2026-10-04.json) | Bounded half-frame selection wait made default in `.31`; ready-FD stays opt-in | [FRESHNESS.md](../docs/FRESHNESS.md) |
| [FRESHNESS-RETRO-2026-10-04.json](FRESHNESS-RETRO-2026-10-04.json) | Retrospective fresh-frame loss analysis of saved captures | [FRESHNESS.md](../docs/FRESHNESS.md) |
| [OVERNIGHT-BASELINE-2026-10-04.json](OVERNIGHT-BASELINE-2026-10-04.json) | Three short stationary-chart baseline blocks | [FRESHNESS.md](../docs/FRESHNESS.md) |
| [PACKET-GRACE-LIVE-2026-10-04.json](PACKET-GRACE-LIVE-2026-10-04.json) | Packet grace wait: no repeatable gain; default 0 | [FRESHNESS.md](../docs/FRESHNESS.md) |
| [READY-FENCE-GPU-2026-10-04.json](READY-FENCE-GPU-2026-10-04.json) | Ready-fence GPU correctness probe on the Quest | [READY-FENCE-EXPERIMENT.md](../docs/READY-FENCE-EXPERIMENT.md) |
| [DECODE-PRIORITY-AB-2026-10-04.json](DECODE-PRIORITY-AB-2026-10-04.json) | LOW decode queue priority A/B at 120 and 144 Hz | [DECODE-PRIORITY.md](../docs/DECODE-PRIORITY.md) |
| [DECODE-STAGE-GPU-2026-10-04.json](DECODE-STAGE-GPU-2026-10-04.json) | Decode stage timer calibration, standalone | [DECODE-STAGE-PROBE.md](../docs/DECODE-STAGE-PROBE.md) |
| [DECODE-STAGE-LIVE-2026-10-04.json](DECODE-STAGE-LIVE-2026-10-04.json) | Decode stages live: dequant and inverse transform both material | [DECODE-STAGE-PROBE.md](../docs/DECODE-STAGE-PROBE.md) |
| [CHROMA-FILTER-GPU-2026-10-04.json](CHROMA-FILTER-GPU-2026-10-04.json) | Chroma filter conversion cost, standalone | [CHROMA-FILTER.md](../docs/CHROMA-FILTER.md) |
| [DEFAULT-CONVERT-GPU-2026-10-04.json](DEFAULT-CONVERT-GPU-2026-10-04.json) | Default conversion pixel regression, standalone | [CHROMA-FILTER.md](../docs/CHROMA-FILTER.md) |
| [LAYER-FILTER-AB-2026-10-04.json](LAYER-FILTER-AB-2026-10-04.json) | Compositor supersample + sharpen layer filter A/B at 120 Hz | [COMPOSITOR-FILTER.md](../docs/COMPOSITOR-FILTER.md) |
| [HEALTH-POLL-OVERHEAD-2026-10-04.json](HEALTH-POLL-OVERHEAD-2026-10-04.json) | Harness health-poll interval overhead: no gain from polling less; 2 s kept | none |
| [SURFACE-CAPS-2026-10-04.json](SURFACE-CAPS-2026-10-04.json) | Android Surface and Vulkan WSI capabilities advertised by the runtime | [SURFACE-PROBE.md](../docs/SURFACE-PROBE.md) |
| [SURFACE-CREATION-2026-10-04.json](SURFACE-CREATION-2026-10-04.json) | Surface/Vulkan WSI creation-only screen | [SURFACE-PROBE.md](../docs/SURFACE-PROBE.md) |
| [SURFACE-CHART-2026-10-04.json](SURFACE-CHART-2026-10-04.json) | First static Surface image: lifecycle passed, orientation rejected | [SURFACE-CHART.md](../docs/SURFACE-CHART.md) |

## October 2

| File | Contents | Doc |
| --- | --- | --- |
| [ASYNC-AND-CONVERSION-LIVE-2026-10-02.json](ASYNC-AND-CONVERSION-LIVE-2026-10-02.json) | Async eye copy, FP16 and compute conversion at 120 Hz; none adopted | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [BITRATE-USB-LIVE-2026-10-02.json](BITRATE-USB-LIVE-2026-10-02.json) | 1000 vs 1500 Mbit/s over USB at 120 Hz; 1000 kept | [BITRATE.md](../docs/BITRATE.md) |
| [COLOR-COPY-LIVE-2026-10-02.json](COLOR-COPY-LIVE-2026-10-02.json) | Early raw sRGB copy screen: no gain then | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [FRAGMENT-USAGE-NATIVE-2026-10-02.json](FRAGMENT-USAGE-NATIVE-2026-10-02.json) | Output buffer usage flags and conversion paths, standalone probes on fixed fixtures | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [GPU-READBACK-CI-2026-10-02.json](GPU-READBACK-CI-2026-10-02.json) | Software-GLES readback checks in CI | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [GPU-READBACK-QUEST-2026-10-02.json](GPU-READBACK-QUEST-2026-10-02.json) | GPU consumer readback verified on the Quest | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [OUTPUT-BRIDGE-LIVE-2026-10-02.json](OUTPUT-BRIDGE-LIVE-2026-10-02.json) | Output bridge allocation, caching and async copy screens | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [OUTPUT-SLOT-LOSS-2026-10-02.json](OUTPUT-SLOT-LOSS-2026-10-02.json) | Retrospective analysis: loss after decode, in output publication | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [PACING-LIVE-2026-10-02.json](PACING-LIVE-2026-10-02.json) | Copy handoff and display timestamp screens at 120 Hz | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [PRE-WAIT-CI-2026-10-02.json](PRE-WAIT-CI-2026-10-02.json) | Pre-wait selection build checks in CI | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [FRAME-SELECTION-LIVE-2026-10-02.json](FRAME-SELECTION-LIVE-2026-10-02.json) | Pre-wait poll and frame wait screens; both kept off then | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [FRAME-WAIT-1000-LIVE-2026-10-02.json](FRAME-WAIT-1000-LIVE-2026-10-02.json) | 1000 µs frame wait screen; default stayed 0 then | [FRESHNESS.md](../docs/FRESHNESS.md) |
| [FRAME-SCHEDULING-LIVE-2026-10-02.json](FRAME-SCHEDULING-LIVE-2026-10-02.json) | Frame scheduling diagnostic screen | [FRAME-SCHEDULING.md](../docs/FRAME-SCHEDULING.md) |
| [EYE-GPU-CI-2026-10-02.json](EYE-GPU-CI-2026-10-02.json) | Eye-draw GPU timer build checks in CI | [VULKAN-PRESENTATION.md](../docs/VULKAN-PRESENTATION.md) |
| [EYE-GPU-LIVE-2026-10-02.json](EYE-GPU-LIVE-2026-10-02.json) | Eye-draw GPU timer calibration on the Quest | [VULKAN-PRESENTATION.md](../docs/VULKAN-PRESENTATION.md) |
| [RELEASE-FENCE-CI-2026-10-02.json](RELEASE-FENCE-CI-2026-10-02.json) | Release fence build checks in CI | [RELEASE-FENCE-EXPERIMENT.md](../docs/RELEASE-FENCE-EXPERIMENT.md) |
| [RELEASE-FENCE-GPU-2026-10-02.json](RELEASE-FENCE-GPU-2026-10-02.json) | Release fence GPU reuse correctness, standalone | [RELEASE-FENCE-EXPERIMENT.md](../docs/RELEASE-FENCE-EXPERIMENT.md) |
| [RELEASE-FENCE-LIVE-2026-10-02.json](RELEASE-FENCE-LIVE-2026-10-02.json) | Release fence live at 120 Hz: no fresh-frame gain; default off | [RELEASE-FENCE-EXPERIMENT.md](../docs/RELEASE-FENCE-EXPERIMENT.md) |
| [LIGHT-FOVEATION-CI-2026-10-02.json](LIGHT-FOVEATION-CI-2026-10-02.json) | Light foveation build and offline checks in CI | [LIGHT-FOVEATION.md](../docs/LIGHT-FOVEATION.md) |
| [LIGHT-FOVEATION-LIVE-2026-10-02.json](LIGHT-FOVEATION-LIVE-2026-10-02.json) | Light foveation short live screen; kept optional, default off | [LIGHT-FOVEATION.md](../docs/LIGHT-FOVEATION.md) |
| [OVERLAY-CONTROL-CI-2026-10-02.json](OVERLAY-CONTROL-CI-2026-10-02.json) | Overlay control build checks in CI | [OVERLAY.md](../docs/OVERLAY.md) |
| [OVERLAY-LIVE-2026-10-02.json](OVERLAY-LIVE-2026-10-02.json) | Overlay shown vs hidden: no repeatable gain; overlay stays on | [OVERLAY.md](../docs/OVERLAY.md) |
| [RENDER-ENCODE-LIVE-2026-10-02.json](RENDER-ENCODE-LIVE-2026-10-02.json) | Separate PC render and stream sizes work with a larger source | [RENDER-ENCODE-RESOLUTION.md](../docs/RENDER-ENCODE-RESOLUTION.md) |
| [MANUAL-PLAYTEST-2026-10-02.json](MANUAL-PLAYTEST-2026-10-02.json) | Short idle/Home usability screens per rate; not a game benchmark | [PLAYTEST-2026-10-02.md](../docs/PLAYTEST-2026-10-02.md) |
| [BENCHMARK-PROVENANCE-CI-2026-10-02.json](BENCHMARK-PROVENANCE-CI-2026-10-02.json) | Benchmark tool provenance checks in CI; no performance claim | [BENCHMARKING.md](../docs/BENCHMARKING.md) |

## October 1

| File | Contents | Doc |
| --- | --- | --- |
| [LIVE-2026-10-01.md](LIVE-2026-10-01.md) | First short live observations after the PWU2 repair | [BITRATE.md](../docs/BITRATE.md) |
| [PWU2-BUILD.md](PWU2-BUILD.md) | PWU2 transport repair build candidate | [DEVELOPMENT-HISTORY.md](../docs/DEVELOPMENT-HISTORY.md) |
| [DECODE-2026-10-01.json](DECODE-2026-10-01.json) | Fused Haar and eye-image reuse: fused Haar regressed live; kept off | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [DECODE-HOME-2026-10-01.json](DECODE-HOME-2026-10-01.json) | 180 s SteamVR Home at 120 Hz over USB: 94.5 fresh FPS | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [BATCH-DEQUANT-2026-10-01.json](BATCH-DEQUANT-2026-10-01.json) | Batch dequantization: bit-identical, small standalone gain | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [BATCH-DEQUANT-LIVE-2026-10-01.json](BATCH-DEQUANT-LIVE-2026-10-01.json) | Batch dequantization live: no gain; kept off | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [DIRECT-EYE-LIVE-2026-10-01.json](DIRECT-EYE-LIVE-2026-10-01.json) | Direct eye copy live; optional, off by default then | [DECODE-PIPELINE.md](../docs/DECODE-PIPELINE.md) |
| [CHROMA-2026-10-01.json](CHROMA-2026-10-01.json) | 4:2:0 vs 4:4:4: 4:4:4 regressed FPS, pacing and latency; 4:2:0 default | [CHROMA.md](../docs/CHROMA.md) |

## September 30 (initial evidence)

| File | Contents | Doc |
| --- | --- | --- |
| [INITIAL-EVIDENCE.md](INITIAL-EVIDENCE.md) | First Quest 3 measurements, before the full-frame build | [DEVELOPMENT-HISTORY.md](../docs/DEVELOPMENT-HISTORY.md) |
| [CORRUPTION-FIX.md](CORRUPTION-FIX.md) | Full-resolution corruption repair | [ARCHITECTURE.md](../docs/ARCHITECTURE.md) |
| [decode-initial.json](decode-initial.json) | Offline same-bitstream GPU readback comparison | [INITIAL-EVIDENCE.md](INITIAL-EVIDENCE.md) |
| [network-first-pass.json](network-first-pass.json) | Network-only UDP bursts over Wi-Fi, no VR session | [INITIAL-EVIDENCE.md](INITIAL-EVIDENCE.md) |
| [refresh-initial.json](refresh-initial.json) | Refresh requests confirmed at 90, 120, 144 and 207 Hz; 240 rejected without display scaling | [INITIAL-EVIDENCE.md](INITIAL-EVIDENCE.md) |
