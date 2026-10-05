# Fixed-vector dequant payload experiment

Default-off proposal after the `.42` Haar screens: dequantization still costs
roughly 2.6–3.0 ms. Its bit-plane expansion updates eight local-array elements
per plane. Replace that expansion with two fixed four-component register vectors
and disjoint bitwise ORs, then reconstruct exactly the same mat2x4 order and
nonzero +0.5 correction. Payload offsets, loads, sign scan/order, quantization,
image stores, workgroups and synchronization remain unchanged.

The bit-depth fields permit at most18 planes, so unsigned-to-float values stay
positive and exactly representable; the baseline's signed conversion agrees.
The compiler might already scalarize/unroll the original loops. Vector GLSL is
not evidence of vector GPU execution or a speedup. This is an explicit experiment,
not a codec redesign or quality change.

`debug.q3pw.dequant_vector=1`, read at decoder creation, requests the candidate;
unset/other values retain the baseline. Standalone: `PYROWAVE_VECTOR_PAYLOAD=1`.
`[Q3PW_DEQUANT_VECTOR]` reports activation. Keep batching and fused Haar off;
first compare with the original Haar shader, then combine only if independently
useful. Keep4:2:0, native120, no foveation and matching APK/server.

CPU tests cover every byte at each legal bit position, all depths0–18, matrix
placement, half-step and sign-consumption order. They are arithmetic checks, not
shader execution. Require pinned shader regeneration/manifest review, full
matching builds and exact small/native GPU readbacks before deployment. Only
then compare short interleaved stage/total timing, delivered-frame proxies, pacing
and thermals. No speedup, live acceptance or default promotion has been established.

## Build preparation

The `.43` source embeds the output of [pinned shader regeneration](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37266155789)
at proposal `5e83fd7`. All generated source/binary manifest hashes and the
storage3 × vector2 program dimensions were checked. The original dequant program
SPIR-V is **byte-identical in all three storage modes** to `.42`; the candidate
is a separate program. A fresh pinned codec tree plus the committed CDF/Quest
patches reconstructs the source, generated header and manifest exactly after
normalizing Windows checkout line endings. 104 CPU regressions and six lightweight
GLSL variants passed. Matching native builds and GPU correctness remain required.

## Quest GPU correctness

The `.43` Android client and regressions passed. All six standalone cases matched
the saved RGBA reference **exactly**: baseline, vector-only and vector plus128-row,
each at512×320 and4160×2208 stereo. Current-process/time-bounded logs verified both
selectors, and each decode and GPU readback completed. Persistent properties
were unchanged. Default precision1, Haar/Compute, limited-range4:2:0 fixtures.

These are cold single-decode checks on an asleep headset, not performance tests
or arbitrary-content/headset-quality acceptance. No APK was installed. Matching
Windows review was pending at this checkpoint; candidates remain off by default.
[Sanitized GPU proof](../results/DEQUANT-VECTOR-GPU-2026-10-05.json).
