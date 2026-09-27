# Experiment 1 — PyroWave compute path vs fragment path on the Galaxy XR

Central question: is the current PyroWave latency a property of CDF 9/7, or of how 9/7 is executed on the Adreno? One variable: fragment vs compute reconstruction of identical bytes. All numbers MEASURED on device unless tagged.

## 1. Correctness gate

Phase 1 (MEASURED on device, standalone `pyrowave_android`, identical `score.wave`, 3328x1472 4:4:4, 1,041,656 B, 200 iterations, run twice per path):

| plane | fragment vs compute: max abs | MSE | PSNR | differing pixels |
|---|---:|---:|---:|---:|
| Y | 4 | 0.156 | 56.20 dB | 764,174 / 4,898,816 (15.6 %) |
| Cb | 1 | 0.027 | 63.80 dB | 132,935 |
| Cr | 1 | 0.033 | 62.93 dB | 162,121 |

Against the PC reference decode (`pyrotap\ref444.y4m`): fragment Y 55.79 dB (max 4), compute Y 64.16 dB (max 1); chroma both ~64-66 dB. The compute path is the more accurate of the two; the difference is the fragment path's FP16 render-target intermediates (`vert`/`horiz` R16F), i.e. expected floating-point implementation behaviour, not a reconstruction fault. GATE: PASS (differences <= 4 code values, > 55 dB between paths, quality not materially changed and, if anything, in compute's favour).

Standalone timing in the same runs (MEASURED, T2 = GPU decode, 200 iterations): fragment best/mean 9.402/9.446 ms then 9.383/9.446; compute 7.021/7.077 then 7.041/7.076. Convert: fragment 2.734 ms, compute 1.466 ms. Headset hottest zone 66.7 C before, 66.8 C after.


## 2. Workload identity verification

- PF-1: decode path `fragment` (forced); eye 2131x2304, 400 Mbps, 90 Hz, centre 0.2, buffering 1.5; receiver {'complete': 4247, 'partial': 27, 'skipped': 27, 'dropped': 19, 'superseded': 19, 'stale_packets': 2594, 'foreign': 0, 'decode_fail': 0}
- PC-1: decode path `compute` (forced); eye 2131x2304, 400 Mbps, 90 Hz, centre 0.2, buffering 1.5; receiver {'complete': 4218, 'partial': 44, 'skipped': 23, 'dropped': 35, 'superseded': 23, 'stale_packets': 9979, 'foreign': 0, 'decode_fail': 0}
- PF-2: decode path `fragment` (forced); eye 2131x2304, 400 Mbps, 90 Hz, centre 0.2, buffering 1.5; receiver {'complete': 4240, 'partial': 23, 'skipped': 25, 'dropped': 32, 'superseded': 19, 'stale_packets': 6654, 'foreign': 0, 'decode_fail': 0}
- PC-2: decode path `compute` (forced); eye 2131x2304, 400 Mbps, 90 Hz, centre 0.2, buffering 1.5; receiver {'complete': 4157, 'partial': 82, 'skipped': 27, 'dropped': 54, 'superseded': 48, 'stale_packets': 16521, 'foreign': 0, 'decode_fail': 0}

Identical configuration across cells: YES; arms proven by logcat: YES. Encoded bytes: same server, same session file, same cap (server log `encoder ready: 1984x896`); the byte stream is regenerated per cell, not replayed (the live pipeline has no replay), so bytes are equal by configuration and rate control, not bit-identical (DERIVED).

## 3. Fragment baseline regression

- PF-1: GPU decode mean 3.35 ms vs historical 3.7 ms (window 3.33-4.07): PASS
- PF-2: GPU decode mean 3.31 ms vs historical 3.7 ms (window 3.33-4.07): OUTSIDE WINDOW

## 4. PF-1 raw results

- gpu decode ms (min / mean / p50 / p95 / p99 / max): 2.74 / 3.35 / 3.26 / 4.52 / 4.73 / 5.26 (6 reports)
- convert ms (min / mean / p50 / p95 / p99 / max): 0.70 / 0.89 / 0.70 / 1.76 / 2.24 / 2.77 (6 reports)
- submit->fence ms (min / mean / p50 / p95 / p99 / max): 4.34 / 5.84 / 5.63 / 7.94 / 8.45 / 234.79 (6 reports)
- receiver: {'complete': 4247, 'partial': 27, 'skipped': 27, 'dropped': 19, 'superseded': 19, 'stale_packets': 2594, 'foreign': 0, 'decode_fail': 0}
- thermal: CPU 52.6 -> 68.9 C; GPU 48.5 -> 64.5 C; DDR 48.9 -> 65.5 C; hottest 66.837 -> 68.9 C (pre-launch sample 66.849 C, post 69.9 C); status start 0 end 1

## 5. PC-1 raw results

- gpu decode ms (min / mean / p50 / p95 / p99 / max): 2.57 / 3.25 / 3.18 / 4.19 / 4.26 / 6.92 (6 reports)
- convert ms (min / mean / p50 / p95 / p99 / max): 0.53 / 0.59 / 0.54 / 1.18 / 1.84 / 1.86 (6 reports)
- submit->fence ms (min / mean / p50 / p95 / p99 / max): 3.71 / 4.99 / 4.78 / 6.34 / 6.72 / 221.37 (6 reports)
- receiver: {'complete': 4218, 'partial': 44, 'skipped': 23, 'dropped': 35, 'superseded': 23, 'stale_packets': 9979, 'foreign': 0, 'decode_fail': 0}
- thermal: CPU 67.2 -> 69.2 C; GPU 63.5 -> 65.5 C; DDR 63.8 -> 65.8 C; hottest 67.2 -> 69.2 C (pre-launch sample 66.926 C, post 77.4 C); status start 1 end 1

## 6. Pair 1 deltas (Compute - Fragment) / Fragment x 100

- gpu decode: mean -3.2 %, p50 -2.5 %, p95 -7.3 %, p99 -9.9 %
- submit->fence: mean -14.6 %, p50 -15.2 %, p95 -20.2 %, p99 -20.5 %

## 7. PF-2 raw results

- gpu decode ms (min / mean / p50 / p95 / p99 / max): 2.43 / 3.31 / 3.24 / 4.58 / 4.74 / 6.90 (6 reports)
- convert ms (min / mean / p50 / p95 / p99 / max): 0.70 / 0.72 / 0.70 / 1.40 / 1.53 / 2.17 (6 reports)
- submit->fence ms (min / mean / p50 / p95 / p99 / max): 4.35 / 5.61 / 5.36 / 7.20 / 7.66 / 231.21 (6 reports)
- receiver: {'complete': 4240, 'partial': 23, 'skipped': 25, 'dropped': 32, 'superseded': 19, 'stale_packets': 6654, 'foreign': 0, 'decode_fail': 0}
- thermal: CPU 68.2 -> 70.9 C; GPU 64.8 -> 66.9 C; DDR 65.8 -> 67.5 C; hottest 68.2 -> 70.9 C (pre-launch sample 66.926 C, post 77.7 C); status start 1 end 1

## 8. PC-2 raw results

- gpu decode ms (min / mean / p50 / p95 / p99 / max): 2.57 / 3.24 / 3.19 / 3.74 / 4.09 / 6.72 (6 reports)
- convert ms (min / mean / p50 / p95 / p99 / max): 0.53 / 0.63 / 0.54 / 1.75 / 1.80 / 1.88 (6 reports)
- submit->fence ms (min / mean / p50 / p95 / p99 / max): 3.82 / 4.90 / 4.82 / 6.13 / 6.42 / 224.97 (6 reports)
- receiver: {'complete': 4157, 'partial': 82, 'skipped': 27, 'dropped': 54, 'superseded': 48, 'stale_packets': 16521, 'foreign': 0, 'decode_fail': 0}
- thermal: CPU 70.9 -> 70.6 C; GPU 67.2 -> 66.5 C; DDR 67.9 -> 67.2 C; hottest 70.9 -> 70.6 C (pre-launch sample 68.6 C, post 80.8 C); status start 1 end 1

## 9. Pair 2 deltas (Compute - Fragment) / Fragment x 100

- gpu decode: mean -1.9 %, p50 -1.5 %, p95 -18.3 %, p99 -13.7 %
- submit->fence: mean -12.6 %, p50 -10.0 %, p95 -14.9 %, p99 -16.2 %

## 10. Thermal matching status

- Pair 1: PF start 66.849 C, PC start 66.926 C, difference 0.1 C -> THERMALLY MATCHED
- Pair 2: PF start 66.926 C, PC start 68.6 C, difference 1.7 C -> THERMALLY MATCHED

Pairs agree in sign on GPU and fence: YES (GPU mean deltas -3.2 % / -1.9 %, fence -14.6 % / -12.6 %).

## 11. Pooled Fragment statistics (secondary)

- Fragment gpu decode: mean 3.33, p50 3.25, p95 4.58, p99 4.74
- Fragment submit->fence: mean 5.73, p50 5.49, p95 7.94, p99 8.45

## 12. Pooled Compute statistics (secondary)

- Compute gpu decode: mean 3.25, p50 3.19, p95 4.19, p99 4.26
- Compute submit->fence: mean 4.95, p50 4.80, p95 6.34, p99 6.72

## 13. GPU savings, 14. Fence savings, 15. K = Fence_savings / GPU_savings (per pair, per statistic)

Noise floor per statistic = |PF-1 - PF-2| GPU decode: mean 0.05 ms, p50 0.02 ms, p95 0.06 ms, p99 0.01 ms

Pair 1 (THERMALLY MATCHED):
- mean: GPU savings +0.11 ms, fence savings +0.85 ms, K = 7.88 -> K > 1: fence improves more than GPU time -- barriers/traffic/stalls removed beyond the timestamp interval
- p50: GPU savings +0.08 ms, fence savings +0.85 ms, K = 10.69 -> K > 1: fence improves more than GPU time -- barriers/traffic/stalls removed beyond the timestamp interval
- p95: GPU savings +0.33 ms, fence savings +1.60 ms, K = 4.85 -> K > 1: fence improves more than GPU time -- barriers/traffic/stalls removed beyond the timestamp interval
- p99: GPU savings +0.47 ms, fence savings +1.73 ms, K = 3.68 -> K > 1: fence improves more than GPU time -- barriers/traffic/stalls removed beyond the timestamp interval

Pair 2 (THERMALLY MATCHED):
- mean: GPU savings +0.06 ms, fence savings +0.71 ms, K = 11.49 -> K > 1: fence improves more than GPU time -- barriers/traffic/stalls removed beyond the timestamp interval
- p50: GPU savings +0.05 ms, fence savings +0.54 ms, K = 10.70 -> K > 1: fence improves more than GPU time -- barriers/traffic/stalls removed beyond the timestamp interval
- p95: GPU savings +0.84 ms, fence savings +1.07 ms, K = 1.27 -> K > 1: fence improves more than GPU time -- barriers/traffic/stalls removed beyond the timestamp interval
- p99: GPU savings +0.65 ms, fence savings +1.24 ms, K = 1.91 -> K > 1: fence improves more than GPU time -- barriers/traffic/stalls removed beyond the timestamp interval

## 16. Structural pass/dispatch comparison (DERIVED from pyrowave source, checkout d2997ac)

| | fragment path (Adreno default) | compute path |
|---|---|---|
| dequant/unpack | <= 60 compute dispatches (per level x component x band), 1 barrier | same |
| inverse DWT | 3 render passes per level (2 vertical even/odd + 1 horizontal), each in 3 scissored draws, 5 levels = 15 passes / 45 draws; chroma 4:2:0 fix-up passes; layout barriers between vertical and horizontal and after each level; trailing FRAGMENT->COMPUTE barrier | 5 compute dispatches (H+V fused per level in shared memory), 1 compute->compute barrier per level |
| intermediate surfaces | per level: horiz[3] of w_l x h_l and vert[2][2] of w_l x 2h_l (luma R16F, chroma RG16F) | one wavelet image (alignedW/2 x alignedH/2, 12 layers, 5 mips) |
| full-res-equivalent memory passes (luma) | ~4 (ESTIMATED: ~2x compute path's intermediates) | ~2 |
| standalone T2 decode, 3328x1472 4:4:4 (MEASURED, pyrowave_android, 200 iters, twice) | 9.446 / 9.446 ms | 7.077 / 7.076 ms |
| standalone convert (MEASURED) | 2.734 ms | 1.466 ms |

## 17. Sustained results (Phase 3)

- SF (fragment, forced): gpu decode 2.42 / 3.48 / 3.41 / 4.79 / 5.20 / 7.08 (39 reports); convert 0.70 / 0.75 / 0.70 / 1.68 / 1.80 / 2.51 (39 reports); fence 4.08 / 5.76 / 5.68 / 7.39 / 8.08 / 235.56 (39 reports); receiver {'complete': 27287, 'partial': 342, 'skipped': 91, 'dropped': 360, 'superseded': 210, 'stale_packets': 100408, 'foreign': 0, 'decode_fail': 0}; thermal CPU 69.9 -> 71.3 C; GPU 65.5 -> 67.2 C; DDR 66.2 -> 67.9 C; hottest 69.9 -> 71.3 C (pre-launch sample 68.2 C, post 79.8 C); status start 1 end 1
- SC (compute, forced): gpu decode 2.57 / 3.16 / 2.87 / 4.15 / 4.30 / 4.96 (40 reports); convert 0.53 / 0.60 / 0.54 / 1.78 / 1.83 / 1.85 (40 reports); fence 3.70 / 4.80 / 4.70 / 6.38 / 6.96 / 222.60 (40 reports); receiver {'complete': 27655, 'partial': 572, 'skipped': 88, 'dropped': 485, 'superseded': 359, 'stale_packets': 162923, 'foreign': 0, 'decode_fail': 0}; thermal CPU 70.9 -> 71.3 C; GPU 67.9 -> 67.5 C; DDR 67.5 -> 68.2 C; hottest 70.9 -> 71.3 C (pre-launch sample 70.3 C, post 81.5 C); status start 1 end 1
- Sustained pair thermal matching: SF start 68.2 C, SC start 70.3 C, difference 2.1 C -> THERMALLY MATCHED; end SF 79.8 C vs SC 81.5 C
- Sustained deltas gpu decode: mean -9.1 %, p50 -15.8 %, p95 -13.4 %, p99 -17.3 %
- Sustained deltas submit->fence: mean -16.7 %, p50 -17.3 %, p95 -13.7 %, p99 -13.9 %
- Sustained K mean: GPU savings +0.32 ms, fence savings +0.96 ms, K = 3.04 -> K > 1: fence improves more than GPU time -- barriers/traffic/stalls removed beyond the timestamp interval
- Sustained K p50: GPU savings +0.54 ms, fence savings +0.98 ms, K = 1.82 -> K > 1: fence improves more than GPU time -- barriers/traffic/stalls removed beyond the timestamp interval
- Sustained K p95: GPU savings +0.64 ms, fence savings +1.01 ms, K = 1.58 -> K > 1: fence improves more than GPU time -- barriers/traffic/stalls removed beyond the timestamp interval
- Sustained K p99: GPU savings +0.90 ms, fence savings +1.12 ms, K = 1.24 -> K > 1: fence improves more than GPU time -- barriers/traffic/stalls removed beyond the timestamp interval

## 18. Outcome classification

Pooled mean deltas: GPU -2.6 %, fence -13.6 %. Band: MODERATE CONFIRMATION (5-20 % faster). Outcome **A (fence-side)**: fence improves substantially while the GPU-timestamp interval barely moves: the win is structural (passes, barriers, layout transitions, the convert pass after the trailing barrier), not arithmetic; per the brief, do not attribute it to reduced 9/7 arithmetic.
Primary evidence is the paired result: pairs agree; thermal status Pair 1 THERMALLY MATCHED, Pair 2 THERMALLY MATCHED.

## 19. Remaining uncertainties

- The live pipeline regenerates the byte stream per cell; bytes are equal by configuration, not replayed (the standalone Phase 1 decode is the bit-identical control).
- GPU timestamps bracket PyroWave's decode only; the fence covers decode + convert + queue wait.
- Two pairs; no distribution test is claimed.
- Fragment path uses FP16 intermediates (max 4 code values from compute): quality differs slightly in compute's favour, treated as not material.

## 20. Decision: does compute CDF 9/7 become the experimental baseline?

YES -- outcome A (fence-side). Compute becomes the EXECUTION baseline: replicated, thermally matched, correctness-clean, faster to completion with tighter tails and equal-or-better accuracy. It is NOT evidence that transform arithmetic matters: K >> 1 says the saving lives outside the decode-timestamp interval, so later transform experiments (5/3, Haar, DCT, WHT) must be judged on passes and barriers against compute 9/7, and small arithmetic reductions should be expected NOT to propagate. See the pair-level evidence above. Per the brief, GPU-time and fence-time conclusions are stated separately: compute changes inverse-decoder GPU execution by -2.6 % (pooled mean); compute changes submit->fence completion by -13.6 % (pooled mean).

