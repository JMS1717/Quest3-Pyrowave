# PR #9: signed build and Quest 3 integration screen

October 5, 2026. The signed `.55` build from `2b289f8` was reviewed, installed and
tested on Quest 3. [All five signed-build jobs passed](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37365361691):
155 Python regressions, 67 production publication tests, fused-color correctness,
Android and Windows builds. Certificate, APK/server hashes, compiled feature
markers and native-library provenance were checked before installation.
[Sanitized window measurements](../results/PR-9-ACCEPTANCE-2026-10-05.json).

## Merge decision

Keep the independent game-render/stream controls, the fixed overlay startup,
Compact/Full/Hidden modes, optional peripheral profiles and diagnostics. Keep
foveation, fused color, LPAC, eye invalidation and thread hints off by default.
Retain synchronous decoding and eye completion, bounded latest-frame selection
and the existing 4 ms render selection wait.

**Restore Bilinear as the default downsample filter.** Adaptive is still selectable,
but its short screen had weaker tail pacing. Fresh installs, sessions missing the
new setting and the native server fallback all use the previous single tap.
An explicitly saved Adaptive selection is preserved. The production default and
legacy-session migration tests now require Bilinear. This is the only runtime
source change after the tested `2b289f8`; shader and decoder code are unchanged.
Amended `9da8560` passed [all five signed CI jobs](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37375168471),
including the actual fresh/legacy Bilinear migration test. Its matching
pair/signature/hashes were reviewed and all three native decoder libraries exactly
match tested `2b289f8`. PR #9 merged as `aee7a69`. The amended pair is available
but not deployed; the tested `2b289f8` pair remains installed.

## Live options comparison

Eight 12-second windows, 3 seconds settling each, in Control / Invalidate / Hints /
Hidden / Hidden / Hints / Invalidate / Control order. Source textures were explicitly
2080×2208 per eye, runtime 120 Hz, 1000 Mbps USB/TCP, 4:2:0, no foveation, one
LOW-priority HaarCompute worker and direct eye copies. Client restarts separated
windows; they were not one continuous sustained test.

| Option | Unique fresh FPS, mean | Decode completion p50, ms | CPU eye render p50, ms | Eye GPU timer mean, ms |
| --- | ---: | ---: | ---: | ---: |
| Control / Compact | 119.09 | 7.98 | 1.69 | 0.68 |
| Eye invalidation | 119.65 | 7.95 | 1.82 | 0.65 |
| Thread hints | 119.09 | 7.98 | 1.70 | 0.67 |
| Hidden overlay | 118.71 | 7.97 | 1.65 | 0.69 |

No repeatable delivery or latency gain warrants promoting invalidation or hints.
The runtime accepted the frame-loop hint; worker hints returned
`ERROR_ANDROID_THREAD_SETTINGS_FAILURE_KHR`, with their scheduler/affinity
unchanged. This is a graceful unsupported path, not evidence of boosted workers.
Compact rendered correctly and hiding it did not improve delivered FPS in these
windows. Off-thread rasterization removes text work from the render loop, but
this screen does not establish zero compositor cost. The long-hold settings-menu
Apply/restart path still needs a controlled human acceptance test.

## 207 Hz runtime cadence

With decoding and streaming disabled, the corrected probe produced **3104 frames
in 15 seconds: 206.9 FPS**, period 4,830,944 ns, zero skipped slots, stalls or late
waits. Wait p50/p99 were 4875/5839 µs. A first attempt retained an Android 120 Hz
override and is excluded; a successful rate request alone was insufficient.
The temporary override and probe were restored with readback.

This proves short **runtime cadence**, not 207 Hz PCVR or a 4.83 ms decode budget.
The runtime rejected the 240 Hz request. Optical latency was not measured.

## Peripheral profiles

Reconstructed output remains 2080×2208 per eye. Off/Light/Balanced/Strong/Off were
screened, two 12-second windows per cell. Owner interaction changed overlay modes
in the first Strong and return-control cells, including one blank capture; those
four windows are excluded. Clean Strong and return-control repeats replace them.

| Profile | Encoded size per eye | Unique fresh FPS | GPU decode p50, ms | Completion p50, ms | Eye GPU timer, ms |
| --- | --- | ---: | ---: | ---: | ---: |
| Off, two control cells | 2080×2208 | 118.99 | 6.01 | 7.97 | 0.66 |
| Light | 1952×2080 | 119.42 | 5.99 | 7.98 | 1.06 |
| Balanced | 1824×1920 | 118.60 | 5.50 | 7.78 | 1.12 |
| Strong, clean repeat | 1664×1792 | 119.09 | 5.08 | 7.12 | 1.19 |

GPU reconstruction costs rise while decode pixels fall. Strong's measured
completion is **7.12 ms**, still above one 207 Hz slot; it is not a 207 Hz solution.
LEFT/RIGHT, orientation and matching source-pulse endpoints were checked in
captured images. Peripheral detail/aliasing changes are visible in the chart;
their noticeability in the lenses is not accepted. Foveation stays optional and off.

## Larger source and downsample filters

Explicit **3072×3216 per-eye source textures** were submitted in every window;
decode remained **2080×2208 per eye**. Four cells in Bilinear / Adaptive / Adaptive /
Bilinear order, two 12-second windows each, same chart and decoder configuration.
This compares filters at the larger size; it is not a new normalized native-versus-
supersampled source-quality experiment.

| Filter | Fresh FPS, mean | Nominal p1, median | Completion p50, ms | Estimated pipeline p50, ms | Actual payload p50, Mbps |
| --- | ---: | ---: | ---: | ---: | ---: |
| Bilinear | 119.46 | 89.63 | 7.96 | 49.81 | 1011.39 |
| Adaptive | 118.73 | 72.25 | 7.97 | 52.37 | 1007.76 |

Nominal p1 is the reciprocal p99 gap between unique target timestamps, not an
optical presentation percentile. Adaptive's four windows were 72.41 / 60.62 /
72.09 / 90.37 versus Bilinear's 89.85 / 89.46 / 89.59 / 89.67. The difference is
grounds for withholding its default promotion, not a sustained causal estimate.
Per-window estimated latency varied substantially in both filters. PC compositor
and encoder boundaries shift: their median values sum to about 3.2 ms for both;
the encoder column alone must not be called a 1 ms filter penalty.

Both filters retained upright correctly mapped eyes and matching pulse endpoints.
Adaptive smoothed the finest stripe patterns in captured images, but that does
not establish a noticeable in-headset benefit. Bilinear remains the safe default;
Adaptive is an explicit quality experiment with an easy rollback.

## Measurement limits and restoration

Unique fresh FPS de-duplicates GraphStatistics target timestamps. It does not
prove independently photographed distinct display frames. Counter deltas show
completed direct eye copies, zero staging fallback and zero decode failures in
all included windows. Synchronous GL completion remains; the asynchronous
completion observer is off and has **zero** samples. Eye GPU timers are separate.
ALVR latency estimates are not motion-to-photon measurements.

Battery samples were 27–38 °C during captures, thermal status 0; sparse GPU clocks
and battery temperature do not prove sustained thermal behavior. Source intervals,
client identity, clock alignment, negotiated geometry and active feature markers
were retained privately. Safety snapshots, exclusive locks and independent
restorers covered every phase. All temporary properties/touched settings were
restored with readback, physical proximity behavior restored, project registration
removed and Virtual Desktop registration preserved. The signed tested `.55`
installation and previous matching pairs remain available.

Next: controlled menu Apply/restart acceptance, a sustained game test at native
120, then a profile/filter comparison with tighter PC frame pacing and a true
optical capture. Do not infer that 207/high-resolution streaming or Virtual
Desktop quality/latency parity is reached.

PRs #4–#7 are marked merged. PR #8 subsequently added a separate fused-dequant
follow-up (`533dfc4`), which is not in this integration and remains draft against
main pending its own Adreno correctness and performance gates.
