# Dedicated Haar kernel experiment

Experimental branch only: pinned shader regeneration is pending. Do not build or
install this branch until its generated header and source manifest match.
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
