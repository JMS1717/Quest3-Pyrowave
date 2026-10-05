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
chart covered six restarted-client blocks in ABBAAB order: three12-second
blocks each with the diagnostic disabled/enabled, at120Hz,1000Mbps,4:2:0,
Haar/Compute and LOW queue priority. Foveation and experimental filters stayed
off. Read-only device/PC clock alignment was applied to process-specific logs.

| Measurement | Probe off | Probe on |
| --- | ---: | ---: |
| Mean submission events / second | 116.64 | 116.11 |
| Mean completed direct eye copies / second | 116.83 | 116.48 |
| Median block p1 instantaneous client FPS | 60.00 | 60.00 |
| Median block payload bitrate | 1008.6Mbps | 1013.3Mbps |

Each enabled block yielded12 post-startup stage intervals. The three block
means were2.74/2.65/2.81ms for Dequant and3.56/3.16/3.16ms for iDWT. Both
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
