# Experiment 3 — Fused Haar reconstruction topology on the Galaxy XR

Central question: can Haar's 2-tap, apron-free inverse let several reconstruction levels fuse into one dispatch, removing the inter-level LL images, their re-reads and the global barriers, and does that move completion time where cheaper arithmetic (Experiment 2) did not? Reference: CDF 9/7 FP32 compute. Every number tagged MEASURED / DERIVED / ESTIMATED / UNKNOWN.

## 1. Experiment 1 conclusion carried forward

Compute 9/7 is the execution baseline: fence −13..−17 % against fragment with the GPU interval nearly unchanged (K >> 1); completion follows passes, barriers and traffic, not ops (MEASURED, Experiment 1).

## 2. Experiment 2 conclusion carried forward

CDF 5/3 FP16 at identical topology: GPU −2 %, fence 0 % at matched clock, logical bytes −4.9 %, +17.9 % bytes at matched PSNR-Y; the first −20 % was DVFS. 9/7 stays the reference; cheaper arithmetic and FP16 alone do not move completion (MEASURED, Experiment 2). GPU clock sampling is mandatory from here on.

## 3. CDF 9/7 compute reference

- Live: 15 iDWT dispatches (5 levels x 3 components), 6 global barriers, 4 inter-level LL images, ~2.27 full-frame-equivalent passes, 35,497,728 logical bytes/frame at 1984x896 4:4:4 (DERIVED). Standalone: Arm A below (MEASURED).

## 4. Haar mathematical definition

Lifting Haar in PyroWave's normalisation: forward `d = odd - even`, `a = even + d/2`; inverse `even = a - d/2`, `odd = even + d`; separable 2-D (columns then rows, order immaterial for a linear separable transform); unit DC gain, no K scaling; sequence-header code 2; quantiser gains DERIVED from the synthesis basis norms relative to 9/7 (HL/LH by level 0.989, 1.002, 0.956, 0.937, 0.932; HH 0.961, 1.034, 0.962, 0.930, 0.921; LL 0.943). Implemented as spec constant `HAAR` (id 2) in `dwt.comp`/`idwt.comp` (Arm B) and as `haar_fused.comp` in the standalone harness (Arm C).

## 5. H0/H1/H2/H3 topology analysis (DERIVED)

| topology | dispatches | global barriers | workgroup barriers | temp LL images | logical bytes/frame | vs 9/7 | full-frame passes | max shared | min lane utilisation |
|---|---|---|---|---|---|---|---|---|---|
| 9/7 compute | 15 | 6 | 15 | 4 | 35,497,728 | 0.0 % | 2.27 | 3,280 | - |
| H0 [1, 1, 1, 1, 1] | 15 | 6 | 15 | 4 | 33,747,840 | -4.9 % | 2.16 | 3,280 | 1.000 |
| H1 [2, 2, 1] | 9 | 4 | 13 | 2 | 32,331,264 | -8.9 % | 2.03 | 3,280 | 1.000 |
| H2 [3, 2] | 6 | 3 | 12 | 1 | 27,998,208 | -21.1 % | 1.62 | 2,048 | 0.250 |
| H3 [5] | 3 | 2 | 11 | 0 | 26,664,960 | -24.9 % | 1.50 | 2,048 | 0.016 |

See `topology.md` for the per-stage detail. The byte model counts dequant writes, every coefficient read once, inter-stage LL writes and re-reads, and the R8 output: fusion removes only the LL round trips, so no Haar topology reaches a 30 % byte cut; what fusion removes is barriers (6 -> 2) and full-frame passes (2.27 -> 1.50).

## 6. Shared-memory / register feasibility

- Device `maxComputeSharedMemorySize` = 32,768 B (MEASURED). Fused kernel: two 32x33 float16 tiles = 4,224 B shared, 64 lanes, one 32x32 output tile per workgroup; ESTIMATED ~16 registers/lane. H3's first level runs 1 of 64 lanes (1x1 -> 2x2 tile), H2's first stage 16 of 64: the known cost of deep fusion, ESTIMATED in Phase 0, measured below (C2 beats C3).

## 7. Logical memory-traffic model

- H0: coefficient reads 14,207,424 B, LL writes 3,541,440 B, LL re-reads 3,541,440 B, output 5,332,992 B, total 33,747,840 B (18.98 B/px, 3.04 GB/s at 90 Hz) (DERIVED; physical DRAM traffic UNKNOWN)
- H2: coefficient reads 11,332,608 B, LL writes 666,624 B, LL re-reads 666,624 B, output 5,332,992 B, total 27,998,208 B (15.75 B/px, 2.52 GB/s at 90 Hz) (DERIVED; physical DRAM traffic UNKNOWN)
- H3: coefficient reads 10,665,984 B, LL writes 0 B, LL re-reads 0 B, output 5,332,992 B, total 26,664,960 B (15.00 B/px, 2.40 GB/s at 90 Hz) (DERIVED; physical DRAM traffic UNKNOWN)

## 8. Structural continuation gate

- H0: bytes -4.9 %, barriers -0.0 %, passes -4.5 %, feasible True -> **NOT STRONG ENOUGH**
- H1: bytes -8.9 %, barriers -33.3 %, passes -10.3 %, feasible True -> **NOT STRONG ENOUGH**
- H2: bytes -21.1 %, barriers -50.0 %, passes -28.3 %, feasible True -> **NOT STRONG ENOUGH**
- H3: bytes -24.9 %, barriers -66.7 %, passes -33.8 %, feasible True -> **PROCEED**

Proceed: H3. H2 (barriers −50 %, passes −28 %) sits just under the bar and was carried as a diagnostic because the fused kernel takes the fusion depth as a parameter (project direction: test both when feasible).

## 9. Equal-byte RD results (Control A, offline, MEASURED)

| source | 9/7 bytes | Haar bytes | PSNR-Y 9/7 | PSNR-Y Haar | dPSNR-Y | PSNR-HVS 9/7 / Haar | SSIM 9/7 / Haar | VMAF 9/7 / Haar |
|---|---|---|---|---|---|---|---|---|
| kodak_kodim04 | 416,544 | 416,628 | 30.55 | 28.80 | -1.75 | 30.25 / 28.18 | 0.8924 / 0.8553 | 76.2 / 55.0 |
| kodak_kodim07 | 416,612 | 416,616 | 29.30 | 27.05 | -2.25 | 28.36 / 26.08 | 0.9052 / 0.8717 | 72.1 / 50.8 |
| kodak_kodim08 | 416,600 | 416,612 | 21.42 | 20.32 | -1.10 | 21.58 / 20.39 | 0.8049 / 0.7732 | 56.3 / 38.8 |
| kodak_kodim19 | 416,596 | 416,648 | 26.90 | 25.69 | -1.21 | 28.54 / 26.95 | 0.8913 / 0.8682 | 71.8 / 55.8 |
| synthetic_panel | 416,588 | 416,616 | 51.75 | 49.10 | -2.65 | 57.49 / 59.06 | 0.9989 / 0.9995 | 97.0 / 96.6 |

All 325 matched cells: Kodak mean dPSNR-Y -1.39 dB, panel -3.34 dB.

## 10. PSNR-Y-matched RD results (Control B, offline, MEASURED; never 'equal quality')

| source | 9/7 bytes | 9/7 PSNR-Y | Haar bytes at match | Haar PSNR-Y | extra bytes | Mbps at 90 Hz (9/7 -> Haar) | PSNR-HVS 9/7 / Haar | SSIM 9/7 / Haar |
|---|---|---|---|---|---|---|---|---|
| kodak_kodim04 | 416,544 | 30.55 | 981,736 | 30.55 | +135.7 % | 300 -> 707 | 30.25 / 33.06 | 0.8924 / 0.9055 |
| kodak_kodim07 | 416,612 | 29.30 | 963,516 | 29.32 | +131.3 % | 300 -> 694 | 28.36 / 32.20 | 0.9052 / 0.9143 |
| kodak_kodim08 | 416,600 | 21.42 | 699,152 | 21.43 | +67.8 % | 300 -> 503 | 21.58 / 23.32 | 0.8049 / 0.8073 |
| kodak_kodim19 | 416,596 | 26.90 | 740,220 | 26.91 | +77.7 % | 300 -> 533 | 28.54 / 29.47 | 0.8913 / 0.8933 |
| synthetic_panel | 416,588 | 51.75 | 460,204 | 51.80 | +10.5 % | 300 -> 331 | 57.49 / 62.72 | 0.9989 / 0.9997 |

Kodak mean extra bytes 103.1 %; panel 10.5 %.

## 11. Kodak

Natural imagery: Haar gives up 1.39 dB PSNR-Y at equal bytes and needs 103.1 % more bytes (about 2x) to match 9/7's PSNR-Y; PSNR-HVS, SSIM and VMAF all fall with it at equal bytes (MEASURED).

## 12. Synthetic panel

Contradictory evidence preserved: at equal bytes the panel's PSNR-Y falls -2.65 dB with Haar, but PSNR-HVS rises (57.49 -> 59.06) and SSIM rises (0.9989 -> 0.9995); at matched PSNR-Y the panel needs only 10.5 % more bytes against ~2x on Kodak. Synthetic/game-like content is a separate evidence class; Haar's edge-preserving behaviour on text and hard edges is real on the HVS/SSIM metrics and absent on PSNR-Y (MEASURED).

## 13. RD continuation gate

**STOP** — Kodak extra bytes +103.1 % > 50 % and no compelling panel advantage. The device microbenchmark had already run in parallel with this gate (the RD run and the device work share no resource), so its result is reported below as measured; the gate governs the live-integration decision, not the reporting.

## 14. Device pre-checks and correctness gate (MEASURED)

- shaderFloat16 = 1, maxComputeSharedMemorySize = 32,768 B, 64-lane workgroups; Haar stream + 9/7 decoder rejected on device (`push_packet -> -2`).
| comparison | max abs | MSE | PSNR dB | differing |
|---|---|---|---|---|
| A: 9/7 FP32 device vs PC 9/7 | 1 | 0.02408 | 64.31 | 353,946 / 14,696,448 |
| B: Haar FP16 same topology vs PC Haar | 1 | 0.01532 | 66.28 | 225,111 / 14,696,448 |
| C3: fused H3 vs PC Haar | 1 | 0.02221 | 64.66 | 326,437 / 14,696,448 |
| C3 vs B (identical coefficients) | 1 | 0.01307 | 66.97 | 192,090 / 14,696,448 |
| C2 vs B (identical coefficients) | 1 | 0.01307 | 66.97 | 192,090 / 14,696,448 |
| C3 vs C2 | 0 | 0.00000 | inf | 0 / 14,696,448 |

Structural comparison validity: **VALID** (B, C2 and C3 within 1 code value; C2 and C3 bit-identical). The fused kernel's band orientation was established empirically: of the four LH/HL x transposed hypotheses only the natural one matches B (66.97 dB); the others give 25 dB / 14 dB.

## 15. Arm A raw results: CDF 9/7 FP32 compute (libpyrowave idwt, precision 1) (MEASURED, standalone, 3328x1472 4:4:4, 200 iterations per cell)

- A: 2 cells; GPU reconstruction (T0->T2, dequant + iDWT) mean 7.063 ms (cell spread 0.002), best 7.023; convert 1.466 ms; submit->idle wall (decode + convert + CPU) mean 8.997 ms (spread 0.000), best 8.871

## 16. Arm B raw results: Haar FP16 same topology (libpyrowave idwt, precision 0) (MEASURED, standalone, 3328x1472 4:4:4, 200 iterations per cell)

- B: 2 cells; GPU reconstruction (T0->T2, dequant + iDWT) mean 6.938 ms (cell spread 0.001), best 6.895; convert 1.466 ms; submit->idle wall (decode + convert + CPU) mean 8.878 ms (spread 0.005), best 8.775

## 17. Arm C raw results: fused Haar FP16 (harness kernel) (MEASURED, standalone, 3328x1472 4:4:4, 200 iterations per cell)

- C3: 2 cells; GPU reconstruction (T0->T2, dequant + iDWT) mean 3.744 ms (cell spread 0.004), best 3.705; convert 1.466 ms; submit->idle wall (decode + convert + CPU) mean 5.640 ms (spread 0.008), best 5.511
- C2: 2 cells; GPU reconstruction (T0->T2, dequant + iDWT) mean 3.493 ms (cell spread 0.003), best 3.453; convert 1.466 ms; submit->idle wall (decode + convert + CPU) mean 5.393 ms (spread 0.011), best 5.271

## 18. GPU clock, cycles/frame and cycles/reconstructed pixel

- On-device trace during the sampled passes: 571 samples over 625 s, clock mean 788 MHz, min 788, max 788, 100 % at the top level, thermal_pwrlevel max 0, hottest 66.7-66.8 C (MEASURED). The final wall-timed pass has no trace of its own (the sampler collided with adb); its GPU times reproduce the sampled pass to 0.01 ms, so its clock state is taken as the same 788 MHz and marked INHERITED.
| arm | GPU ms | Mcycles/frame | cycles/reconstructed pixel |
|---|---|---|---|
| A | 7.063 | 5.57 | 0.38 |
| B | 6.938 | 5.47 | 0.37 |
| C3 | 3.744 | 2.95 | 0.20 |
| C2 | 3.493 | 2.75 | 0.19 |

## 19. Structural comparison (DERIVED from source and the Phase 0 model)

| | A 9/7 compute | B Haar same topology | C3 fused H3 | C2 fused H2 |
|---|---|---|---|---|
| iDWT dispatches | 15 | 15 | 3 | 6 |
| global barriers (dequant->iDWT + inter-level) | 6 | 6 | 2 | 3 |
| workgroup barriers per tile | 3 per level (15) | 3 per level (15) | 6 (1 load + 5 levels) | 4 + 3 |
| temp LL images | 4 | 4 | 0 | 1 |
| full-frame-equivalent passes | 2.27 | 2.16 | 1.50 | 1.62 |
| logical bytes/frame at live size | 35,497,728 | 33,747,840 | 26,664,960 | 27,998,208 |
| shared memory / workgroup | 3,280 B | 3,280 B | 4,224 B | 4,224 B |
| arithmetic per level | 4 lifting steps + K, FP32 | 2-tap lifting, FP16 | 2-tap lifting, FP16 | 2-tap lifting, FP16 |

## 20. Primary Comparison 1: A (9/7 FP32) vs B (Haar FP16, same topology)

GPU -1.8 %, submit->idle -1.3 % (MEASURED). Extreme arithmetic simplification at unchanged topology changes completion by about 1-2 %, exactly as Experiment 2 predicted: ops/pixel is not the axis.

## 21. Primary Comparison 2: B vs C (topology only)

- B -> C3 (H3, one stage): GPU -46.0 %, submit->idle -36.5 % (MEASURED, matched clocks, replicated in 2 cells each with spread <= 0.004 ms).
- B -> C2 (H2, two stages): GPU -49.7 %, submit->idle -39.3 %.
- C3 -> C2: GPU -6.7 %: the two-stage form beats the single stage, consistent with H3's 1-of-64-lane coarse levels (ESTIMATED cause; not profiled).
- Against the reference: A -> C3 GPU -47.0 %, A -> C2 GPU -50.6 %; submit->idle A -> C2 -40.1 %.

Completion here is the standalone submit->queue-idle wall time (decode + 1.47 ms convert + CPU submit overhead), not the live receiver's submit->fence; the live fence is UNKNOWN for Arm C until a live path exists.

## 22. Fence vs passes / logical bytes (diagnostic, DERIVED x MEASURED)

| arm | full-frame passes | logical bytes/frame (live size) | global barriers | GPU ms | submit->idle ms |
|---|---|---|---|---|---|
| A | 2.27 | 35,497,728 | 6 | 7.063 | 8.997 |
| B | 2.16 | 33,747,840 | 6 | 6.938 | 8.878 |
| C3 | 1.50 | 26,664,960 | 2 | 3.744 | 5.640 |
| C2 | 1.62 | 27,998,208 | 3 | 3.493 | 5.393 |

Across B -> C2 -> C3 the GPU time tracks barriers and passes, not bytes: C3 has the fewest bytes and barriers yet is slower than C2, so the relationship is not a straight line in any one structural count. A diagnostic for this experiment only.

## 23. Outcome classification

**Outcome A** — fused Haar wins substantially (>= 10 % completion improvement, matched clocks, correct, replicated). Comparison 1 (A vs B) reproduced Experiment 2's prediction (arithmetic alone ~1-2 %); Comparison 2 shows topology moves both the GPU interval (about −50 %) and completion (about −40 %) at matched clocks with correct output.

## 24. Remaining unknowns

- Live submit->fence for the fused path: UNKNOWN (standalone wall time only).
- Physical DRAM traffic, occupancy and register allocation: UNKNOWN (no counters, no ISA); the C2 > C3 ordering is unexplained beyond the lane-utilisation estimate.
- Real lossless game frames as a third content class: not yet available.
- The final wall-timed pass has no clock trace of its own (INHERITED from the identical sampled pass).
- Haar's rate control uses PyroWave's bit-plane packer unchanged; a Haar-specific entropy or packing scheme could change Control B.

## 25. Live-integration decision

**NOT NOW** — latency gate met (C vs B -39.3 %), rate-distortion gate not met (Kodak +103.1 % bytes at matched PSNR-Y; panel +10.5 %): the project's call on the bandwidth trade. The trade on the table: about half the reconstruction time (and roughly −40 % standalone completion) for about 2x the bytes on natural content (400 -> ~810 Mbps at the operating point) or +10 % on panel-like content. No live path was built in this experiment.

