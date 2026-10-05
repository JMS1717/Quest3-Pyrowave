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
