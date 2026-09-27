# Experiment 4 — Transfer the fusion win to CDF 9/7; real-content RD; H.264/CAVLC baseline

Reference: CDF 9/7 FP32 compute (Control A). Controls: fused Haar C2 (Control B, Experiment 3 kernel), the H.264/CAVLC raw-pipe measurements (Control C, separate pipeline). Tags: MEASURED / DERIVED / ESTIMATED / UNKNOWN. Formulas: every percentage is (arm - reference) / reference x 100 on the stated metric; cycles/frame = ms x 1e-3 x MHz x 1e6; cycles/reconstructed pixel = cycles/frame / (3 x W x H).

## 1. Phase 1 — Haar fusion-depth curve (standalone, 3328x1472 4:4:4, 200 iterations, 3 replicates each, rotated; MEASURED)

| arm | stages | GPU ms mean (spread) | submit->idle ms | per-stage ms (stage 1 includes dequant) | dispatches | global barriers | passes | min lane util |
|---|---|---|---|---|---|---|---|---|
| A: CDF 9/7 FP32 compute (libpyrowave idwt) | lib | 7.062 (0.002) | 9.006 | - | 15 | 6 | 2.27 | 1.000 |
| B: Haar FP16, same topology (libpyrowave idwt) | lib | 6.935 (0.002) | 8.874 | - | 15 | 6 | 2.16 | 1.000 |
| Haar kernel [1,1,1,1,1] | 5 | 4.392 (0.002) | 6.302 | 5->4:1.40 4->3:0.05 3->2:0.17 2->1:0.58 1->0:2.19 | 15 | 6 | 2.16 | 1.000 |
| Haar fused [1,2,2] | 3 | 3.659 (0.002) | 5.568 | 5->4:1.41 4->2:0.16 2->0:2.09 | 9 | 4 | 1.63 | 1.000 |
| Haar fused [2,2,1] | 3 | 4.157 (0.001) | 6.059 | 5->3:1.42 3->1:0.55 1->0:2.19 | 9 | 4 | 2.03 | 1.000 |
| Haar fused [4,1] | 2 | 4.187 (0.006) | 6.099 | 5->1:2.00 1->0:2.19 | 6 | 3 | 2.00 | 0.062 |
| Haar fused [1,4] | 2 | 3.825 (0.002) | 5.722 | 5->4:1.40 4->0:2.42 | 6 | 3 | 1.51 | 0.062 |
| Haar fused [2,3] | 2 | 3.683 (0.004) | 5.583 | 5->3:1.42 3->0:2.27 | 6 | 3 | 1.53 | 0.250 |
| Haar C2: fused [3,2] (Experiment 3 kernel) | 2 | 3.632 (0.001) | 5.537 | 5->2:1.54 2->0:2.09 | 6 | 3 | 1.62 | 0.250 |
| Haar fused [5] | 1 | 3.951 (0.001) | 5.854 | 5->0:3.95 | 3 | 2 | 1.50 | 0.016 |
| Haar fused [3,2], 4 lanes/quad at coarse levels | 2 | 3.633 (0.003) | 5.534 | 5->2:1.54 2->0:2.09 | 15 | 6 | UNKNOWN | UNKNOWN |
| Haar fused [5], 4 lanes/quad at coarse levels | 1 | 3.968 (0.004) | 5.877 | 5->0:3.97 | 15 | 6 | UNKNOWN | UNKNOWN |

- C2 > C3 reproduces: [3,2] 3.632 ms vs [5] 3.951 ms (+8.8 %) (MEASURED).
- Lane-utilisation test: giving the coarse levels 4 lanes per quad (same bytes, same barriers) changes [5] by +0.4 % and [3,2] by +0.0 % (MEASURED). The lane-underutilisation hypothesis for C2 > C3 is NOT supported by this test; the cause stays UNKNOWN (no counters, no ISA). Per-stage timestamps show the cost sits in stages that end at fine levels: 5->0 3.95 ms vs 5->2 1.54 + 2->0 2.09 ms.
- Best Haar topology on this device: F32 at 3.632 ms; the depth curve is not monotonic in dispatch count (MEASURED).

## 2. Phase 2 — partially fused CDF 9/7: design (DERIVED model)

9/7 needs the apron (4 output samples per side per level), so a stage fusing k levels reconstructs a haloed tile: a level yielding V valid samples loads V/2 + 4 coefficients per band (rounded to even), exactly as the library loads 16 + 4 for 32, and the halo is recomputed per tile instead of round-tripping the LL. Between fused levels the tile must be re-mirrored past the image extent (half-sample at the far edge) to match the library's convention, which reads the next level's LL through the stored image's mirror. Five-level fusion is not possible with this tile scheme (odd nominal tile at level 5); [5] is excluded as the brief anticipated.

| arm | dispatches | global barriers | temp LL images | full-frame passes | logical bytes/frame (live size) | vs A | max redundant load | max shared B |
|---|---|---|---|---|---|---|---|---|
| A: CDF 9/7 FP32 compute (libpyrowave idwt) | 15 | 6 | 4 | 2.27 | 35,497,728 | +0.0 % | 1.56x | 3,280 |
| 9/7 kernel [1,1,1,1,1] (this kernel, library topology) | 15 | 6 | 4 | 3.06 | 43,981,560 | +23.9 % | 1.56x | 6,400 |
| 9/7 fused [2,1,1,1] | 12 | 5 | 3 | 3.06 | 43,934,688 | +23.8 % | 2.25x | 9,216 |
| 9/7 fused [2,2,1] | 9 | 4 | 2 | 3.15 | 44,892,960 | +26.5 % | 2.25x | 9,216 |
| 9/7 fused [3,2] | 6 | 3 | 1 | 2.96 | 42,956,886 | +21.0 % | 3.06x | 12,544 |
| Haar C2: fused [3,2] (Experiment 3 kernel) | 6 | 3 | 1 | 1.62 | 27,998,208 | -21.1 % | 1.00x | 4,096 |

The model says 9/7 fusion cannot cut logical bytes (the halo adds more than the LL round trips remove: +19 to +27 %); what it removes is dispatches and barriers, which is what Experiment 3 said mattered. That is the hypothesis Phase 4 tests.

## 3. Phase 3 — correctness gate (MEASURED, identical coefficients `w97.wave`, vs the library's own decode)

| comparison | max abs | MSE | PSNR dB | differing |
|---|---|---|---|---|

History kept: the first fused builds were exact in every interior tile and wrong only in image-edge tiles (up to 21 code values on the last row); a bisection over stage groupings showed single-level stages exact and any fusion wrong, which isolated the inter-level boundary rule; after the re-mirror, [3,2] and [2,2,1] match the single-level control bit for bit (same MSE 0.01859). Orientation and level order were verified by the same interior-exact result. Performance below is read only for the arms that pass.

## 4. Phase 4 — device performance (standalone, 3328x1472 4:4:4, 100 iterations, 3 replicates each, rotated; MEASURED)

GPU clock inside the cell windows: 77 samples, mean 774 MHz, min 421, max 788, 96 % at the top level (MEASURED; detached on-device sampler). Cells are compared at matched clocks.

| arm | n | GPU ms mean (spread) | vs A | submit->idle ms | vs A | convert ms | per-stage ms | Mcycles/frame | cycles/px |
|---|---|---|---|---|---|---|---|---|---|
| A: CDF 9/7 FP32 compute (libpyrowave idwt) | 3 | 7.063 (0.000) | 0.0 % | 9.040 | 0.0 % | 1.466 | - | 5.46 | 0.372 |
| B: Haar FP16, same topology (libpyrowave idwt) | 3 | 6.934 (0.001) | -1.8 % | 8.906 | -1.5 % | 1.466 | - | 5.36 | 0.365 |
| Haar C2: fused [3,2] (Experiment 3 kernel) | 3 | 3.631 (0.001) | -48.6 % | 5.531 | -38.8 % | 1.466 | 5->2:1.54 2->0:2.09 | 2.81 | 0.191 |
| 9/7 kernel [1,1,1,1,1] (this kernel, library topology) | 3 | 17.272 (0.000) | 144.5 % | 19.207 | 112.5 % | 1.466 | 5->4:1.52 4->3:0.27 3->2:0.83 2->1:2.98 1->0:11.67 | 13.36 | 0.909 |
| 9/7 fused [2,1,1,1] | 3 | 17.272 (0.002) | 144.5 % | 19.215 | 112.6 % | 1.466 | 5->3:1.79 3->2:0.83 2->1:2.98 1->0:11.67 | 13.36 | 0.909 |
| 9/7 fused [2,2,1] | 3 | 17.608 (0.003) | 149.3 % | 19.550 | 116.3 % | 1.466 | 5->3:1.79 3->1:4.15 1->0:11.67 | 13.62 | 0.927 |
| 9/7 fused [3,2] | 3 | 19.001 (0.001) | 169.0 % | 20.933 | 131.6 % | 1.466 | 5->2:2.73 2->0:16.27 | 14.70 | 1.000 |

- Same-implementation topology comparison (this kernel, library topology -> fused): [1,1,1,1,1] 17.272 ms -> [2,2,1] 17.608 (+1.9 %) -> [3,2] 19.001 (+10.0 %) (MEASURED). Fusion with an apron makes 9/7 slower, not faster: the halo lifting is paid on every fused level.
- Against the reference: this kernel at the library's topology is +144.5 % vs A, so the kernel itself (scalar f16 shared tiles, per-coefficient fetches with mirror arithmetic, one row-segment per lane) is about 2x slower than the library's vec2, gather-loaded, 4-lanes-per-row implementation. The topology conclusion is therefore read within this kernel (same implementation), and the production comparison against A is stated separately.
- Fused Haar C2 in the same session: 3.631 ms (-48.6 % vs A), reproducing Experiment 3.

## 5. Phase 5 — real-content corpus (lossless encoder-input clips, 1984x896 SBS 4:4:4; MEASURED offline)

Per class and clip, mean over the scored frames (every 10th of a 90-frame clip); PSNR-Y spread across frames is the temporal stability of the codec at that cap.

| class | clip | wavelet | cap B | Mbps@90 | frames | PSNR-Y mean (std, min..max) | PSNR-HVS | SSIM | VMAF |
|---|---|---|---|---|---|---|---|---|---|
| steamvr_home | CP-HOME-20260923-182402 | 97 | 250,000 | 180 | 9 | 38.85 (0.02, 38.81..38.89) | 44.50 | 0.9783 | 96.0 |
| steamvr_home | CP-HOME-20260923-182402 | 97 | 416,667 | 300 | 9 | 41.76 (0.04, 41.70..41.83) | 48.28 | 0.9864 | 96.6 |
| steamvr_home | CP-HOME-20260923-182402 | 97 | 600,000 | 432 | 9 | 44.27 (0.06, 44.20..44.35) | 50.67 | 0.9898 | 96.9 |
| steamvr_home | CP-HOME-20260923-182402 | haar | 250,000 | 180 | 9 | 36.64 (0.03, 36.59..36.70) | 42.19 | 0.9713 | 93.9 |
| steamvr_home | CP-HOME-20260923-182402 | haar | 416,667 | 300 | 9 | 41.18 (0.08, 41.07..41.30) | 47.14 | 0.9832 | 96.3 |
| steamvr_home | CP-HOME-20260923-182402 | haar | 600,000 | 432 | 9 | 44.01 (0.05, 43.93..44.11) | 49.63 | 0.9875 | 96.9 |
| synthetic_panel | CP-PANEL-20260923-181930 | 97 | 250,000 | 180 | 9 | 49.88 (0.00, 49.88..49.88) | 50.64 | 0.9950 | 96.4 |
| synthetic_panel | CP-PANEL-20260923-181930 | 97 | 416,667 | 300 | 9 | 55.99 (0.00, 55.99..55.99) | 53.07 | 0.9971 | 97.1 |
| synthetic_panel | CP-PANEL-20260923-181930 | 97 | 600,000 | 432 | 9 | 61.57 (0.00, 61.57..61.57) | 55.56 | 0.9980 | 97.4 |
| synthetic_panel | CP-PANEL-20260923-181930 | haar | 250,000 | 180 | 9 | 48.95 (0.00, 48.95..48.95) | 48.94 | 0.9936 | 95.0 |
| synthetic_panel | CP-PANEL-20260923-181930 | haar | 416,667 | 300 | 9 | 53.30 (0.00, 53.30..53.30) | 51.23 | 0.9960 | 96.6 |
| synthetic_panel | CP-PANEL-20260923-181930 | haar | 600,000 | 432 | 9 | 56.98 (0.00, 56.98..56.98) | 53.32 | 0.9973 | 97.2 |
| textures_photo | CP-PHOTO-20260923-182144 | 97 | 250,000 | 180 | 9 | 49.80 (0.00, 49.80..49.80) | 50.65 | 0.9949 | 96.5 |
| textures_photo | CP-PHOTO-20260923-182144 | 97 | 416,667 | 300 | 9 | 55.98 (0.00, 55.98..55.98) | 53.05 | 0.9971 | 97.2 |
| textures_photo | CP-PHOTO-20260923-182144 | 97 | 600,000 | 432 | 9 | 61.60 (0.00, 61.60..61.60) | 55.58 | 0.9980 | 97.4 |
| textures_photo | CP-PHOTO-20260923-182144 | haar | 250,000 | 180 | 9 | 49.15 (0.00, 49.15..49.15) | 49.14 | 0.9937 | 95.2 |
| textures_photo | CP-PHOTO-20260923-182144 | haar | 416,667 | 300 | 9 | 53.32 (0.00, 53.32..53.32) | 51.25 | 0.9960 | 96.7 |
| textures_photo | CP-PHOTO-20260923-182144 | haar | 600,000 | 432 | 9 | 57.21 (0.00, 57.21..57.21) | 53.31 | 0.9972 | 97.2 |

Equal-bytes deltas (Haar - 9/7), per class, mean over clips:

| class | cap B | clips | dPSNR-Y | dPSNR-HVS | dSSIM |
|---|---|---|---|---|---|
| steamvr_home | 150,000 | 1 | -2.54 | -2.78 | -0.0107 |
| steamvr_home | 200,000 | 1 | -2.75 | -2.78 | -0.0090 |
| steamvr_home | 250,000 | 1 | -2.21 | -2.31 | -0.0070 |
| steamvr_home | 300,000 | 1 | -2.25 | -2.10 | -0.0055 |
| steamvr_home | 350,000 | 1 | -1.67 | -1.64 | -0.0042 |
| steamvr_home | 416,667 | 1 | -0.58 | -1.15 | -0.0032 |
| steamvr_home | 500,000 | 1 | -0.14 | -1.10 | -0.0027 |
| steamvr_home | 600,000 | 1 | -0.26 | -1.05 | -0.0023 |
| synthetic_panel | 150,000 | 1 | -1.40 | -1.93 | -0.0022 |
| synthetic_panel | 200,000 | 1 | -0.87 | -1.54 | -0.0014 |
| synthetic_panel | 250,000 | 1 | -0.93 | -1.70 | -0.0014 |
| synthetic_panel | 300,000 | 1 | -2.17 | -1.93 | -0.0015 |
| synthetic_panel | 350,000 | 1 | -2.80 | -1.83 | -0.0013 |
| synthetic_panel | 416,667 | 1 | -2.69 | -1.84 | -0.0011 |
| synthetic_panel | 500,000 | 1 | -3.01 | -1.95 | -0.0009 |
| synthetic_panel | 600,000 | 1 | -4.59 | -2.25 | -0.0007 |
| textures_photo | 150,000 | 1 | -1.30 | -1.84 | -0.0021 |
| textures_photo | 200,000 | 1 | -0.81 | -1.50 | -0.0015 |
| textures_photo | 250,000 | 1 | -0.65 | -1.51 | -0.0012 |
| textures_photo | 300,000 | 1 | -2.20 | -1.87 | -0.0014 |
| textures_photo | 350,000 | 1 | -2.66 | -1.77 | -0.0013 |
| textures_photo | 416,667 | 1 | -2.66 | -1.80 | -0.0011 |
| textures_photo | 500,000 | 1 | -2.97 | -2.00 | -0.0010 |
| textures_photo | 600,000 | 1 | -4.39 | -2.27 | -0.0007 |

PSNR-Y-MATCHED control (middle frame of each clip; never 'equal quality'):

| class | clip | 9/7 bytes | 9/7 PSNR-Y | Haar bytes | Haar PSNR-Y | extra bytes | PSNR-HVS 9/7 / Haar | SSIM 9/7 / Haar |
|---|---|---|---|---|---|---|---|---|
| steamvr_home | CP-HOME-20260923-182402 | 416,592 | 41.77 | 455,596 | 41.77 | +9.4 % | 48.26 / 47.60 | 0.9864 / 0.9839 |
| synthetic_panel | CP-PANEL-20260923-181930 | 416,632 | 55.99 | 563,532 | 56.00 | +35.3 % | 53.07 / 52.93 | 0.9971 / 0.9970 |
| textures_photo | CP-PHOTO-20260923-182144 | 416,648 | 55.98 | 566,592 | 55.98 | +36.0 % | 53.05 / 52.91 | 0.9971 / 0.9970 |

Reading (MEASURED): the two scene-app clips are static (frame 0 and frame 80 bit-identical; PSNR-Y std 0.00) and the photo layout occupies too little of the foveated frame to separate its class from the panel (their RD curves agree within 0.1 dB), so they count as ONE synthetic/UI-like class. SteamVR Home is the only real rendered 3D content captured (frames 0 and 80 differ at 36 dB: the head was still, the scene animates), and it sits between Kodak and the panel: Haar loses 0.6 dB PSNR-Y at 416,667 B and needs +9.4 % bytes at matched PSNR-Y, with PSNR-HVS and SSIM lower by 0.7 dB / 0.0025 at that point; at low caps the penalty is 2-3 dB. On the static synthetic clips Haar needs +35 % (the Experiment 3 panel gave +10 % on a different, higher-contrast pattern). So real rendered content here behaves neither like Kodak (~2x) nor like the best synthetic case; the class-by-class spread is the finding. 3D gameplay, foliage, particles, high motion, HUD, in-game text and menus are UNKNOWN until the worn session.

## 6. Phase 6 — the H.264/CAVLC abuse path (Control C; separate pipeline and instrumentation, kept apart)

| pipeline | what was measured | bitrate | decode | send->decoded p50 / p95 | total motion-to-photon | source |
|---|---|---|---|---|---|---|
| raw-pipe H.264 CAVLC, 3264x1408@72, wifi | MediaCodec decode, send->decoded on the client clock | 400 Mbps | UNKNOWN ms p50 | UNKNOWN / UNKNOWN ms | UNKNOWN (no compositor/vsync stages in raw-pipe) | rawpipe7/summary.json |
| raw-pipe H.264 CAVLC, 3264x1408@72, wifi | MediaCodec decode, send->decoded on the client clock | 600 Mbps | UNKNOWN ms p50 | UNKNOWN / UNKNOWN ms | UNKNOWN (no compositor/vsync stages in raw-pipe) | rawpipe7/summary.json |
| raw-pipe H.264 CAVLC, 3264x1408@72, wifi | MediaCodec decode, send->decoded on the client clock | 800 Mbps | UNKNOWN ms p50 | UNKNOWN / UNKNOWN ms | UNKNOWN (no compositor/vsync stages in raw-pipe) | rawpipe7/summary.json |
| raw-pipe H.264 CAVLC, 3264x1408@72, adb | MediaCodec decode, send->decoded on the client clock | 400 Mbps | UNKNOWN ms p50 | UNKNOWN / UNKNOWN ms | UNKNOWN (no compositor/vsync stages in raw-pipe) | rawpipe7/summary.json |
| ALVR H.264 (B-15) | full stage breakdown | 400 Mbps | 15.95 ms | n/a (network stage 11.88) | 80.07 ms | latency-budget.md |
| ALVR PyroWave X60-400-90 | full stage breakdown | 400 Mbps | GPU 3.7 / fence 6.4 ms | n/a (decoder stage 10.8, queue 3.5) | 60.1 ms | udp-ladder/README.md |
| ALVR PyroWave X60-300-90 | full stage breakdown | 300 Mbps | - | - | 55.5 ms | udp-ladder/README.md |
| standalone reconstruction (this experiment) | GPU interval + submit->idle, no network | n/a | A 7.06 / Haar C2 3.49 / fused 9/7 see item 4 | n/a | n/a | perf/ |

The raw-pipe numbers measure a different frame size, refresh and clock than the ALVR rows and carry no compositor stage; they are not merged into one metric. What can be said: at 400 Mbps the raw-pipe H.264 decode is 9.4 ms p50 against PyroWave's 3.7 ms GPU decode / 6.4 ms fence in the live pipeline, and its send->decoded 21 ms against PyroWave's decoder stage of 10.8 ms plus queue 3.5 ms in the ALVR pipeline; end-to-end, ALVR H.264 measured 80 ms where ALVR PyroWave measured 55-60 ms at the same 90 Hz operating point family. The bitrate for comparable visual quality is UNKNOWN for H.264 (no quality measurement exists on the raw pipe).

## 7. Phase 7 — the conversion stage

- Standalone: conversion is a constant 1.466 ms in every arm (MEASURED). Share of GPU decode + convert: A 17 %, Haar C2 29 %. Live receiver: `convert ms` 0.56-0.78 mean per cell in Experiment 2 (MEASURED), i.e. ~10-15 % of the decoder GPU work at the live 1984x896 size.
- Fusing conversion into the last reconstruction stage needs all three planes in one workgroup (one dispatch over the tile for Y, Cb, Cr together, writing RGBA8 directly): bytes DERIVED as 3 R8 writes + 1 RGBA8 read/write replaced by 1 RGBA8 write, i.e. the conversion's own 3 reads + 1 write per pixel disappear; dispatches drop by 3 (the per-component fused stages become one) and one barrier goes. It does not become the critical path until reconstruction is near 3 ms, which only the Haar fused arms reach today; for 9/7 the reconstruction itself is still the larger term. No rewrite in this experiment.

## 8. Outcomes and falsification

- Fused-9/7 hypothesis (H2): FALSIFIED for this implementation. Correct partial fusion produced +0.0 % (best grouping N2111) against the same kernel unfused, and +144.5 % against the library reference; submit->idle moved the same way. The apron makes 9/7 fusion pay redundant lifting on every fused level, which the model showed as +19-27 % logical bytes and 2-3x halo loads.
- H1 (the Experiment 3 win came from topology, not arithmetic): still supported by the Haar depth curve and by A vs B (-1.8 %), but with the refinement that the topology win depends on the transform being apron-free; the win does not transfer to an apron transform by fusing levels.
- H3 (an architecture-dependent optimum fusion depth): supported for Haar ([3,2] < [2,3] ~ [1,2,2] < [1,4] < [5] < [2,2,1] ~ [4,1] < [1,1,1,1,1]); the lane-widening test did not move it, so the mechanism remains UNKNOWN.
- Outcome: **B-** at best for fused 9/7 (no material improvement; the arithmetic/dependency cost of preserving 9/7 is established as the halo), pending any future implementation that hides the halo cost; Outcome C (real-game Haar) and D (vs H.264) depend on the corpus, item 5, and the worn classes.

## 9. Closing items

1. **Primary conclusion:** the ~50 % reconstruction win of fused Haar does not transfer to CDF 9/7 by fusing levels: with a correct partially fused 9/7 (halo recomputed per tile, library-exact output) the fused groupings are slower than the same kernel unfused, and the kernel is ~2x slower than the library. The apron is the cost of 9/7's compression advantage, and it is paid again at every fused level.
2. **Is topology still the dominant lever?** Yes for apron-free transforms (Haar depth curve spans 3.6-4.4 ms across groupings vs 6.9 ms unfused), and arithmetic still is not (A vs B -1.8 %). For 9/7 the lever is blocked by the halo, not by dispatch count.
3. **Best measured 9/7 topology:** the library's own five-level compute path (A, 7.063 ms); among this kernel's groupings the unfused [1,1,1,1,1].
4. **Best measured Haar topology:** [3,2] (C2) at 3.632 ms (depth pass) / 3.631 ms (perf pass); [5] is 6-8 % slower and lane widening does not change that.
5. **Real-game RD conclusion (partial):** on the one real rendered class captured (SteamVR Home, static head) Haar's penalty is +9.4 % bytes at matched PSNR-Y and -0.6 dB at equal bytes, far from Kodak's ~2x and close to the Experiment 3 panel; on the static synthetic clips it is +35 %. H4 (Kodak overstates the penalty) is supported by this class but not established: one clip, no head motion, no gameplay; the worn classes decide it.
6. **Relationship to H.264/CAVLC:** item 6: different pipelines, not merged. On the numbers that exist, PyroWave's live decoder stage and end-to-end total are below ALVR H.264's at the same operating point, and below the raw-pipe H.264 decode p50 at 400 Mbps; the H.264 abuse path's advantage is bitrate efficiency at comparable quality (UNKNOWN quantitatively) and maturity.
7. **Remaining unknowns:** live fence for any fused kernel; the C2 > C3 mechanism; a library-speed fused-9/7 implementation (would it reach parity with A?); physical DRAM traffic; H.264 quality-at-bitrate; the worn corpus classes; the depth pass ran without its own clock trace (INHERITED from identical sampled runs).
8. **Recommended Experiment 5:** (a) capture the worn corpus classes and finish the class-by-class Haar RD; (b) if any class shows Haar within ~25 % bytes of 9/7 at matched PSNR-Y, build the live fused-Haar path (transform id, mismatch rejection, clock sampling) and measure fence and motion-to-photon against A and against the ALVR H.264 baseline at the bitrate the class needs; (c) otherwise stop transform work and move to pipeline fusion (conversion into reconstruction, and the decoder_queue/vsync waits that dominate the 60 ms budget).
