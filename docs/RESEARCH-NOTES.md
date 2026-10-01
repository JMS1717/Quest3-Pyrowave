# Research inputs and Quest priorities

The supplied `REPORT.md` is the upstream transform survey, not a Quest performance
report. Its lab caps cover one 2560×2560 luma plane; its Galaxy XR live measurements
cover a much smaller foveated 1984×896 stereo 4:4:4 frame. Our full-panel Quest frame
is 4160×2208 stereo 4:2:0. Byte budgets, PSNR and decode times cannot be transferred
between those operating points.

Our source lock already pins [Terminal-ennui's integration](https://github.com/Terminal-ennui/galaxy-xr-alvr-pyrowave-444/tree/54cc8519bddceff57e159e6f39914e373b512809),
which is still its main-branch revision as checked on 2026-10-01. The integration
includes follow-up experiments that resolve some of the survey's unknowns:

| Experiment | Upstream finding | Quest decision |
|---|---|---|
| [Compute vs fragment](https://github.com/Terminal-ennui/galaxy-xr-alvr-pyrowave-444/blob/main/captures/decoder-experiments/exp1-compute-vs-fragment/REPORT.md) | Compute improved Galaxy completion time; not a universal GPU rule | Keep both selectable. Quest Compute readbacks are more accurate; our short full-panel Fragment test still exceeded 8.33 ms to completion. |
| [CDF 5/3 + FP16](https://github.com/Terminal-ennui/galaxy-xr-alvr-pyrowave-444/blob/main/captures/decoder-experiments/exp2-cdf53-fp16/REPORT.md) | No material equal-clock speedup; same pass structure | Existing compute-only CDF 5/3 remains an experiment, not a new default. |
| [Fused Haar](https://github.com/Terminal-ennui/galaxy-xr-alvr-pyrowave-444/blob/main/captures/decoder-experiments/exp3-fused-haar/REPORT.md) | Faster standalone reconstruction, content-dependent compression cost | Candidate for a separate measured branch after frame delivery is stable. Transform/header agreement and exact reconstruction tests are prerequisites. |
| [Fused CDF 9/7](https://github.com/Terminal-ennui/galaxy-xr-alvr-pyrowave-444/blob/main/captures/decoder-experiments/exp4-fused-97/REPORT.md) | Tested implementation regressed; halo work outweighed dispatch savings | Do not assume fewer dispatches are faster. Keep the library's compute path as the baseline. |
| [Haar corpus](https://github.com/Terminal-ennui/galaxy-xr-alvr-pyrowave-444/blob/main/captures/decoder-experiments/exp5-haar-worn/REPORT.md) | Live fused-Haar fence and most gameplay classes remain unknown | No claim of equal game quality or live latency benefit. |

These are upstream findings, not independently reproduced Quest results. Galaxy
hardware-codec results also do not rank Quest H.264/HEVC/AV1 or Virtual Desktop.
Encoder pre-blurring is not enabled: the survey's natural-image gains came with a
large synthetic text/UI loss. FP16 arithmetic is not enabled for CDF 9/7 merely
because FP16 storage is safe.

Next Quest experiments prioritize complete frames and bounded queues, then
post-`xrWaitFrame` polling of the newest ready frame without a blocking decode wait.
Measure skipped/superseded frames alongside completion time and display pacing.
Keep buffer leases and GPU completion ordering until explicit fence-based ownership
can replace them safely. A dedicated queue or conversion/reconstruction fusion needs
device measurements; desktop GPU timings cannot establish a Quest latency win.

Full-panel 120 Hz requires completion below 8.33 ms with margin for the compositor.
Our current full-panel Compute captures exceed that budget. Raising bitrate can
improve compression quality but cannot remove fixed GPU work. PyroWave Auto therefore
uses network/encoder feedback and ignores the hardware decoder latency limiter,
which otherwise compounded reductions when the requested latency was below fixed
decode cost. See [live observations](../results/LIVE-2026-10-01.md).
