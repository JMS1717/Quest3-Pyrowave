# LOW-priority release-fence handoff

**Keep release fences off and synchronous direct eye copies as the default.**
This October 5 `.44` screen retested the GPU handoff with LOW decode priority
and the 4 ms selection wait, which the original `.26` comparison did not use.
It removed approximately 1 ms of CPU wait, but did not improve delivery or p1.

## Correctness prerequisite

The original standalone reuse diagnostic calls `pyroclient_create_ex`, which
requests default queue priority. Setting the app's LOW property does not affect
that constructor. Its small reuse check passed, but it was correctly rejected
as LOW-priority evidence; no native-size LOW claim was made from that run.

The diagnostic-only [change](https://github.com/JMS1717/Quest3-Pyrowave/commit/91579aca9f5da4dfe7068a79116ac5209f165aae)
adds a final `default|low` argument and calls the existing prioritized constructor.
Omitting the argument preserves the old behavior. No APK renderer or native
library changed. [Diagnostic Actions](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37278237706)
passed, and all three downloaded native libraries exactly match reviewed `.44`.

At both 512×320 and native 4160×2208 stereo, three queued GLES A reads survived
reuse of their slot by Vulkan B writes and matched their references exactly.
All six exported fences were initially unsignaled. Process-specific logs confirm
three GPU imports per size and `requested=low applied=1`. This proves the tested
ordering/reuse, not optical quality or performance.

## Short live comparison

Matching `.44` builds and production tests passed. Native libraries remain
byte-identical to GPU-verified `.42`. Source/encode were 2080×2208/eye, runtime
120 Hz, 1000 Mbps, 4:2:0, Haar/Compute, no foveation, LOW decode and one USB/TCP
worker. Selection wait was 4 ms; async, ready, handoff and event experiments
were off. ABBA used client restarts, 3 seconds settling and 12 seconds measurement.
The continuous source was the same native quality chart and changing 10 Hz counter.

| Mode | Eye completions/s | Client FPS p1 | Eye-render CPU p50/p95 ms | Decode-to-fence p50 ms | Copy deferrals |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sync | 117.89 | 60.00 | 1.61 / 1.98 | 7.96 | 0 |
| Release FD | 116.59 | 60.00 | 0.58 / 0.76 | 7.99 | 0 |
| Release FD | 117.60 | 60.00 | 0.56 / 0.73 | 7.98 | 0 |
| Sync | 117.18 | 60.00 | 1.61 / 2.01 | 7.95 | 0 |

Both candidate windows have 12 increasing Vulkan-import records (720→2040)
and 12 increasing active GLES export/observer records. Completed-eye observations
are positive, copies exclusively direct, with no recorded import/export/attachment
or observer fallback. Zero copy deferrals confirm the next selection was not
blocked by the prior GL observer. This establishes the actual GPU handoff.

Nevertheless, completions overlap controls and p1 remains near 60. Native
decode-to-fence medians are about 7.95–7.99 ms. CPU waiting fell from ~1.61 to
0.56–0.58 ms; neither removing that wait nor the copy-ready dependency establishes
120 fresh delivery. Poll-observed ~8.21 ms is wall time quantized by later polling,
not GPU draw duration or measured extra optical latency. No unchanged repeat or
long acceptance run is justified by this result.

All four private images retained upright text, correct LEFT/RIGHT mapping and
matching advancing source counters 154/505/852/1201 in both eyes. GPU endpoints
were 640 MHz, battery 33–35°C, thermal status zero, AC powered. Settings, proximity
and driver state restored without errors, preserving Virtual Desktop. Screenshots
and restarts may perturb subsequent blocks; stationary latency estimates are
not optical motion-to-photon. Endpoint counter matches do not establish continuous
image/pose correctness or human headset acceptance.

**Next:** audit bounded asynchronous producer resources and presentation scheduling.
A GPU fence safely protects ownership, but a three-slot ring still permits latest
frame superseding. Avoid repeating bitrate or fence-property sweeps without a
new mechanism. [Sanitized metrics and package/GPU provenance](../results/RELEASE-LOW-NATIVE120-2026-10-05.json).
