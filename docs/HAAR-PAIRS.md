# Dedicated Haar kernel experiment

Experimental `.42` candidate: pinned shader regeneration passed, all generated
source hashes and program dimensions verified. Matching APK/server builds, exact default/candidate GPU readbacks and short
live screens passed. Sustained performance and headset quality remain unaccepted.
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
may erase the expected row-wise advantage. The Quest results below distinguish stage cost from whole-frame delivery.

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
was deployed by them. Windows matching builds were pending at that checkpoint and subsequently passed;
candidates remain off by default. [Sanitized proof](../results/HAAR-PAIRS-GPU-2026-10-05.json).

## Native 120 Hz live screens

The matching `.42` [cloud build](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37263669309)
passed every required job; signed APK, packaged native identities, Windows version
and hashes were reviewed before deployment. Three stationary-chart comparisons
used native 2080×2208/eye, 120 Hz, 1000 Mbps, 4:2:0, no foveation, LOW decode queue
and the existing synchronous GLES eye copy. Source coverage, client activation,
effective wait settings, clock alignment and thermal status were verified.

With stage diagnostics enabled, the first 8-second screen's row kernels reduced
inverse-transform averages to about 2.8–2.9 ms versus 3.2–3.6 ms in controls.
The 12-second ADDAAD repeat measured 128-row at 2.80–2.97 ms versus baseline
3.13–3.33 ms. Dequantization remained roughly 2.6–3.0 ms; whole-frame GPU/completion
times and delivery rates overlapped. All row arms ended at 599 MHz, controls at
640 MHz. These are endpoint clocks, not continuous samples or evidence of a
particular governor cause.

Final ADDA comparison, **stage diagnostics disabled**, 12 seconds per arm:

| Kernel | Submissions/s | Eye completions/s | GPU decode p50/p95 ms | Decode-to-fence p50/p95 ms | Payload p50 Mbps |
| --- | --- | --- | --- | --- | --- |
| 0 | 116.34 | 116.38 | 5.97 / 6.87 | 8.06 / 9.09 | 1013 |
| 128-row | 117.55 | 118.04 | 5.72 / 6.74 | 7.79 / 8.65 | 1014 |
| 128-row | 117.28 | 117.12 | 5.81 / 6.78 | 7.98 / 8.89 | 1013 |
| 0 | 116.46 | 116.77 | 6.50 / 7.26 | 8.05 / 9.36 | 1013 |

The diagnostic-off candidate was slightly faster in these two blocks, but p1
remained near 60 FPS: occasional missed 120 Hz intervals persist. Earlier repeats
overlap. **Keep the original kernel as default**; 128-row is an optional headroom
experiment, not a sustained 120 FPS fix. Further tests should follow a meaningful
architectural or kernel change, rather than repeating the same short screens.

Battery temperature stayed 33–39°C across the groups, thermal status0, AC powered.
Those short measurements do not establish sustained thermals. Estimated ALVR
latency is not optical motion-to-photon, and event counts are not unique optical
fresh-frame delivery. Each group restored temporary properties, proximity and
saved settings with empty restoration errors; Virtual Desktop stayed registered.
[Sanitized full metrics, hashes and limitations](../results/HAAR-PAIRS-LIVE-2026-10-05.json).
