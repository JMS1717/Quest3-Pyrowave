# Dedicated Haar kernel experiment

Experimental `.42` candidate: pinned shader regeneration passed, all generated
source hashes and program dimensions verified. Matching native builds, default
and candidate GPU readbacks, then live performance gates are still required.
Defaults retain the original shader. Bitstream, reconstruction arithmetic,
precision, foveation and codec remain unchanged.

Before restart, `debug.q3pw.haar_pairs` selects `64-column`, `64-row` or `128-row`;
`0` restores the baseline. Standalone selector: `PYROWAVE_HAAR_PAIRS`. Invalid
values select the baseline. Disable fused Haar and batched dequant for comparisons;
fused Haar takes precedence when separately requested.

The first variant removes non-Haar branches/shared declarations while preserving
lane mapping. The second maps neighboring lanes along X while retaining group
transpose, bounds and coefficients. The third also changes thread count to128.
The existing driver may already remove unused shared allocations; tiled textures
may erase the expected row-wise advantage. No speedup or GPU-correctness claim yet.

Offline compilation and full/partial tile coverage checks passed. Require exact
small/native stereo GPU readbacks at baseline precision1 before live use, then
verified source/configuration and controlled short interleaved screens. Repeated
sustained pacing/latency/image checks follow any gain. Measure stage and total
completion time; counters cannot establish optical delivery. Retain4:2:0/noFFE/
native120 and keep candidates off by default. Preserve matching old binaries and
independent restoration.

## Quest GPU correctness screen

The `.42` Android client and regression build passed. Default, `64-column`,
`64-row` and `128-row` each matched the saved small/native stereo RGBA readbacks
exactly at default precision1, Haar/Compute, 4:2:0 with limited-range fixtures.
Current-process/time-window logcat records confirmed each candidate selector;
producer completion and GPU readback completion were verified. Headset properties
were unchanged. These were single-decode standalone checks on an asleep headset,
with cold pipelines, not awake VR timing or arbitrary-content acceptance. No APK
was deployed by them. Windows matching builds were still pending; candidates
remain off by default. [Sanitized proof](../results/HAAR-PAIRS-GPU-2026-10-05.json).
