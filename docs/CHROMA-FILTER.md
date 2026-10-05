# Optional chroma reconstruction

The `.35` native conversion shader supports `debug.q3pw.chroma_filter=catmull`.
The default remains bilinear. This affects reconstructed 4:2:0 chroma, not source
render resolution, encoded sample count or codec design.

An off/on/off standalone Quest3 GPU screen at stereo4160×2208, Haar/Compute,
4:2:0 measured 40 frames after10 warmups per arm:

| Filter | Conversion p50 | Decode p50 | Completion p50 |
| --- | ---: | ---: | ---: |
| Bilinear | 0.982 ms | 5.229 ms | 7.806 ms |
| Catmull-Rom | 6.063 ms | 5.204 ms | 12.422 ms |
| Bilinear repeat | 0.980 ms | 5.259 ms | 7.805 ms |

**Keep full Catmull-Rom off for the120Hz goal.** Its conversion cost exceeds
the entire remaining frame budget. Decode and control conversion costs were
similar, and thermal status stayed0 at37°C; clocks were not sampled. This is
a standalone fixture screen without compositor load, not live FPS or optical
latency evidence. In-headset quality improvement has not been accepted.
[Sanitized measurements and limitations](../results/CHROMA-FILTER-GPU-2026-10-04.json).

Separately, the reviewed `.35` default native path passed exact GPU readback at
small and native sizes against prior asymmetric RGBA references.
[Default pixel regression](../results/DEFAULT-CONVERT-GPU-2026-10-04.json).
