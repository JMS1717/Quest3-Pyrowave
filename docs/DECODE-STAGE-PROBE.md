# Decode stage diagnostic (`.38`)

Set `debug.q3pw.decode_stages=1` before starting the client to report existing
Granite **Dequant** and **iDWT** GPU interval averages. Unset or any other value
keeps reporting off. The bitstream, shaders, queue priority, buffer ownership,
completion waits and native timing ABI are unchanged.

After every120 successful GPU completions, the decoder worker calls the pinned
PyroWave performance-report API and resets its collected intervals. It adds no
GPU timestamp queries. `[Q3PW_DECODE_STAGE_SETUP]` confirms the request;
`[Q3PW_DECODE_STAGE] complete=N TAG: VALUE ms per frame` carries each stage.
Missing reports are unknown, not zero cost. The API may also query memory budget;
the bridge logs only the two decoder stage tags.

These are **averages per Granite frame context**, collected with a delay; they
are not per-frame percentiles. Scheduling/preemption can affect the intervals.
The first120-decode interval includes startup and is excluded by the reader.
Reporting itself can delay publication: compare off/on/off before interpreting
live performance. This diagnostic should identify whether entropy/dequantization
or inverse transform deserves the next optimization; it is not a speedup.

Read a saved epoch log without accessing the headset:

```text
python -m tools.quest3.decode_stages client.log --start START_EPOCH --end END_EPOCH --pid RECORDED_PID
```

The reader requires activation for the recorded process, rejects malformed,
duplicate or unsupported intervals, and reports stage averages without inventing
percentiles. Complete source coverage, matching artifacts, completion counters,
frame pacing, payload and thermal checks remain necessary for live comparisons.
No optical latency or sustained120Hz acceptance is implied.

## Standalone calibration

The cloud-built `.38` Android component passed exact small/native default RGBA
readback. A native-size off/on/off screen then completed240 measured decodes
after10 warmups per arm, with every final GPU readback byte-identical to the
existing asymmetric reference. The enabled probe reported dequantization4.356ms
and inverse transform4.766ms in its one post-startup interval. Reporting did not
meaningfully change the control completion times (~12.2ms).

The headset was asleep without compositor load; clocks were not sampled.
These costs do **not** establish the awake120Hz budget. They confirm that both
stages contribute and that the diagnostic functions. Android client/tests and
component hash/certificate/library consistency passed; full Windows/terminal CI
review was pending at collection, and no APK was deployed by this standalone
screen. [Calibration evidence](../results/DECODE-STAGE-GPU-2026-10-04.json).

## Awake VR comparison

The matching `.38` APK/server passed all cloud jobs, artifact hash/certificate
checks and default GPU readback before deployment. A continuous native-size
chart covered six restarted-client blocks in ABBAAB order: three 12-second
blocks each with the diagnostic disabled/enabled, at 120 Hz, 1000 Mbps, 4:2:0,
Haar/Compute and LOW queue priority. Foveation and experimental filters stayed
off. Read-only device/PC clock alignment was applied to process-specific logs.

| Measurement | Probe off | Probe on |
| --- | ---: | ---: |
| Mean submission events / second | 116.64 | 116.11 |
| Mean completed direct eye copies / second | 116.83 | 116.48 |
| Median block p1 instantaneous client FPS | 60.00 | 60.00 |
| Median block payload bitrate | 1008.6Mbps | 1013.3Mbps |

Each enabled block yielded 12 post-startup stage intervals. The three block
means were 2.74/2.65/2.81 ms for Dequant and 3.56/3.16/3.16 ms for iDWT. Both
stages warrant attention; inverse transform was usually larger in this screen.
These delayed frame-context averages include GPU scheduling and do not describe
percentiles or necessarily sum to the current-frame decode total.

Keep reporting **off by default**. The small rate difference could reflect
reporting overhead or restart/phase variation; this experiment establishes no
speedup. Battery temperature was34–36°C with thermal status0; clocks were not
sampled. Mean rates near117 do not override the missed periods visible in p1.
Stationary pose-history estimates cannot establish a causal total-latency
change or optical motion-to-photon. Source/process coverage and restoration
passed. [All blocks and limitations](../results/DECODE-STAGE-LIVE-2026-10-04.json).

## Block occupancy (`.127`, October 9)

With the probe on, the client also logs `[Q3PW_BLOCK_STATS]` every 120 decodes. It comes from
`pyrowave_decoder_get_block_stats` (`patches/pyrowave-block-stats.patch`), which counts each
32x32 block's ballot of coded 8x8 sub-blocks as packets arrive, on the CPU. Entry `cNLM` is
component N (0 luma) and level M (0 finest); each shows the percentage of 32x32 blocks with nothing
coded, then the percentage of 8x8 sub-blocks coded.

Live, wired, full size (3072x3216 per eye), Haar 4:2:0, 1500 Mbps, 120 Hz, the harness scene
(116.3 and 116.7 fresh FPS; the counting doesn't change them):

| Level | Luma: empty 32x32 / coded 8x8 | Cb | Cr |
| --- | --- | --- | --- |
| 0 (finest) | 0 % / 45 % | (4:2:0 has no level 0) | |
| 1 | 0 % / 55 % | 0 % / 30 % | 0 % / 33 % |
| 2 | 0 % / 70 % | 0 % / 46 % | 0 % / 42 % |
| 3 | 0 % / 90 % | 0 % / 87 % | 0 % / 86 % |
| 4 | 0 % / 81 % | 0 % / 81 % | 0 % / 81 % |

- No 32x32 block arrives empty at any level, so skipping empty blocks in the decoder would save
  nothing in this scene.
- At the 8x8 level, 55 % of the finest luma detail and 67-70 % of the finest chroma detail is
  uncoded. The dequant already writes zeros there without decoding. Skipping those zeros in the
  iDWT as well would remove part of the traffic that the fused dequant + level-0 Haar experiment
  removed in full, and that gave no speedup ([HIGH-REFRESH.md](HIGH-REFRESH.md)). The PLAN's
  "entropy-skip empty blocks" candidate is dropped.
