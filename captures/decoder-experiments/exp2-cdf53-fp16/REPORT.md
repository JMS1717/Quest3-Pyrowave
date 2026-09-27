# Experiment 2 — CDF 9/7 FP32 compute vs CDF 5/3 FP16 compute on the Galaxy XR

Central question: can CDF 5/3 with an all-FP16 inverse retain near-9/7 reconstruction quality while reducing data, logical intermediate bytes, synchronisation and GPU work to a completed frame, against the CDF 9/7 FP32 compute reference? Fragment is historical only. Every number is tagged MEASURED / DERIVED / ESTIMATED / UNKNOWN; physical DRAM traffic is UNKNOWN throughout (no counters).

## 1. Experiment 1 baseline carried forward

Compute 9/7 (`idwt.comp`, 5 levels fused H+V, 1 barrier per level) is the execution baseline (MEASURED, Experiment 1): against fragment, GPU decode mean -1.9 / -3.2 %, p99 -9.9 / -13.7 %, fence mean -12.6 / -14.6 %, p99 -16.2 / -20.5 % in the two short pairs; sustained 5 min GPU 3.48 -> 3.16 ms (-9.1 %), fence 5.76 -> 4.80 ms (-16.7 %); standalone 9.446 -> 7.077 ms; compute 64.16 dB / max 1 against the PC reference. K >> 1: the fence saving lived outside the decode timestamp interval, so ops/pixel is not the lever; passes, barriers, memory and precision are what Experiment 2 tests. Unresolved from Experiment 1 and carried as explicit regression metrics: sustained compute dropped ~485 vs ~360 frames of ~27,600 and showed more stale packets; ended ~1.7 C hotter.

## 2. 9/7 compute reference configuration

- Live: 2131x2304/eye render, encoded 1984x896 side-by-side foveated (centre 0.2), 4:4:4 full range, 400 Mbps PyroWave over UDP, 90 Hz, buffering 1.5, decode path `compute` (forced), wavelet CDF 9/7, precision requested 1 -> effective 1 (FP32 math, R16F storage on levels 0-1, R32F on 2-4), shaderFloat16=1 (MEASURED from logcat).
- Standalone control: `pyrowave_android`, 3328x1472 4:4:4 full range, 416,6xx B frames (w97.wave 416,708 B; w53.wave 416,636 B) from the same encoder_input.y4m, identical `.wave` inputs, 200 iterations, compute path forced.

## 3. 5/3 FP16 implementation description

- Transform: specialization constant `LEGALL53` (constant_id 1) on `dwt.comp` / `idwt.comp`; when set the four 9/7 lifting loops become the two 5/3 steps (inverse: even -= 0.25*(odd_l+odd_r); odd += 0.5*(even_l+even_r)) with no K scaling. Same tile (32+2*4 apron), same shared layout, same 5 levels, same dispatch and barrier structure by construction; the fragment path refuses 5/3 (compute only).
- Precision: process-global PyroWave precision 0 (FP16 math, `float16_t` lifting values, `f16vec2` shared tile, R16F on all five levels) selected per arm through the device property `debug.xrwired.pyro_precision`, which libpyroclient turns into `PYROWAVE_PRECISION` before the device is created; 9/7 stays at precision 1.
- Bitstream: sequence-header `code` = 1 marks a 5/3 start-of-frame (0 = 9/7); the decoder refuses a stream whose code does not match its create-info wavelet. Server env `ALVR_PYROWAVE_WAVELET=53`; decoder config blob byte 13 carries it to the client.
- Structural differences beyond transform and precision (recorded, not attributed to the transform): the encoder's per-band quantiser gain table is scaled by the DERIVED 5/3-to-9/7 synthesis-gain ratios (HL/LH by level 1.027, 0.797, 0.698, 0.668, 0.660; HH 1.382, 0.953, 0.763, 0.708, 0.693; LL 0.629), so the initial quantiser resolution per band differs. Nothing else differs (item 16).

## 4. Correctness validation (MEASURED on device, `pyrowave_android`, 200 iterations, vs the PC reference decode of the same bytes)

| comparison | max abs | MSE | PSNR dB | differing |
|---|---|---|---|---|
| A: 9/7 FP32 device vs PC 9/7 [Y] | 1 | 0.02864 | 63.56 | 140,316 / 4,898,816 (2.86 %) |
| A: 9/7 FP32 device vs PC 9/7 [Cb] | 1 | 0.02391 | 64.34 | 117,136 / 4,898,816 (2.39 %) |
| A: 9/7 FP32 device vs PC 9/7 [Cr] | 1 | 0.01970 | 65.19 | 96,494 / 4,898,816 (1.97 %) |
| A: 9/7 FP32 device vs PC 9/7 [all] | 1 | 0.02408 | 64.31 | 353,946 / 14,696,448 (2.41 %) |
| B: 5/3 FP32 device vs PC 5/3 [Y] | 1 | 0.01606 | 66.07 | 78,675 / 4,898,816 (1.61 %) |
| B: 5/3 FP32 device vs PC 5/3 [Cb] | 1 | 0.02374 | 64.38 | 116,275 / 4,898,816 (2.37 %) |
| B: 5/3 FP32 device vs PC 5/3 [Cr] | 1 | 0.02060 | 64.99 | 100,897 / 4,898,816 (2.06 %) |
| B: 5/3 FP32 device vs PC 5/3 [all] | 1 | 0.02013 | 65.09 | 295,847 / 14,696,448 (2.01 %) |
| C: 5/3 FP16 device vs PC 5/3 [Y] | 1 | 0.02373 | 64.38 | 116,247 / 4,898,816 (2.37 %) |
| C: 5/3 FP16 device vs PC 5/3 [Cb] | 1 | 0.00597 | 70.37 | 29,228 / 4,898,816 (0.60 %) |
| C: 5/3 FP16 device vs PC 5/3 [Cr] | 1 | 0.01498 | 66.37 | 73,399 / 4,898,816 (1.50 %) |
| C: 5/3 FP16 device vs PC 5/3 [all] | 1 | 0.01489 | 66.40 | 218,874 / 14,696,448 (1.49 %) |

- Transform numerical error gate, 9/7 FP32: PASS (max 1, 64.31 dB pooled).
- Transform numerical error gate, 5/3 FP32: PASS (max 1, 65.09 dB pooled).
- Transform numerical error gate, 5/3 FP16: PASS (max 1, 66.40 dB pooled).
- 9/7 vs 5/3 device decodes of the same source at equal bytes differ by max 54 / 53.46 dB: this is COMPRESSION / QUANTISATION difference between two transforms, not an error (the PC decodes differ identically: 53.45 dB).
- Negative bitstream/config tests (device and PC): PC (`pyrowave-decode`): 9/7 stream + 5/3 decoder -> `[ERROR]: Wavelet mismatch: stream is CDF 9/7, decoder is CDF 5/3.`, 68-byte (header-only) output; 5/3 stream + 9/7 decoder -> `Wavelet mismatch: stream is CDF 5/3, decoder is CDF 9/7.`; matched pairs decode (48.12 / 47.34 dB PSNR-Y vs source). Device (`pyrowave_android`): NEG1 5/3 stream + 9/7 decoder -> `pyrowave_decoder_push_packet -> -2` (INVALID_ARGUMENT), exit 1, no output file; NEG2 9/7 stream + 5/3 decoder -> same rejection; NEG3 5/3 with the fragment path forced -> `pyrowave_decoder_create -> -2`, exit 1; POS 5/3 stream + 5/3 decoder -> decoded, `wrote POS.y4m`. All as designed: REJECT / REJECT / REJECT / PASS.

## 5. FP16 validation

5/3 FP16 (precision 0) against 5/3 FP32 (precision 1) on device, identical bytes (MEASURED):

| plane | max abs | MSE | PSNR dB | differing |
|---|---|---|---|---|
| Y | 1 | 0.01769 | 65.65 | 86,654 / 4,898,816 (1.77 %) |
| Cb | 1 | 0.02458 | 64.23 | 120,401 / 4,898,816 (2.46 %) |
| Cr | 1 | 0.01819 | 65.53 | 89,120 / 4,898,816 (1.82 %) |
| all | 1 | 0.02015 | 65.09 | 296,175 / 14,696,448 (2.02 %) |

Classification (pooled): **PASS** (65.09 dB, max 1); per plane: Y PASS (65.65 dB), Cb PASS WITH DEVIATION (64.23 dB), Cr PASS (65.53 dB).
This is the on-device FP16 arithmetic error of the whole pipeline (lifting in float16_t, R16F storage on all levels, 8-bit output rounding), which is why it sits at ~65 dB rather than the offline lab's >70 dB for the transform alone; it is reported as what it is, not as equivalent to 70 dB.
- Diagnostic: 9/7 at precision 0 vs 9/7 at precision 1 on device: max 3, 54.42 dB -> FAIL. FP16 breaks 9/7 as the lab predicted; and since a silent fallback to precision 1 would have made this comparison bit-identical, it proves precision 0 genuinely executes FP16 math on this device.
- Compiled path (regenerated `slangmosh.hpp`, `spirv-dis`): idwt precision-0 variant declares `OpTypeFloat 16` (37 float16 arithmetic ops, 12 float32 ops, 14 OpFConvert; Float16 capability; f16vec2 shared tile). The precision-1 variant has 0 float16 arithmetic ops and 37 conversions (FP32 math, FP16 shared/storage), the precision-2 variant none. The 12 float32 ops in the precision-0 variant are texture-coordinate arithmetic (OpConvertSToF of integer texel coordinates and OpFMul by the push-constant inverse resolution, vec2 float32) feeding the sampled loads, not lifting arithmetic; the 14 OpFConvert are the float32 texel fetch results narrowed to float16 on load and the outputs widened for the R8 store. LEGALL53 is a specialization constant (SpecId 1) in every variant, so the 5/3 lifting is the same compiled module with the constant set at pipeline creation; the driver's final ISA is UNKNOWN (no disassembler), so hardware promotion cannot be excluded beyond the D diagnostic above.
- Runtime: every P53 cell logs `pyro precision requested 0, effective 0; shaderFloat16=1` and the standalone C run reports `PYROWAVE_PRECISION=0` (MEASURED): standalone C run printed `wavelet: CDF 5/3, PYROWAVE_PRECISION=0` and D `CDF 9/7, PYROWAVE_PRECISION=0`; live P53 cells' pyroclient lines are quoted in items 9 and 12.

## 6. Equal-byte rate-distortion result (Control A, offline, RTX 3090 encode / PC decode, 2560/eye side-by-side 4:4:4, MEASURED with ffmpeg psnr/ssim/libvmaf)

At 416,667 B per stereo frame (300 Mbps at 90 Hz):

| source | bytes 9/7 | bytes 5/3 | PSNR-Y 9/7 | PSNR-Y 5/3 | dPSNR-Y | PSNR-HVS 9/7 | PSNR-HVS 5/3 | SSIM 9/7 | SSIM 5/3 | VMAF 9/7 | VMAF 5/3 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| kodak_kodim04 | 416,544 | 416,656 | 30.55 | 30.25 | -0.30 | 30.25 | 29.64 | 0.8924 | 0.8900 | 76.16 | 71.03 |
| kodak_kodim07 | 416,612 | 416,656 | 29.30 | 28.76 | -0.54 | 28.36 | 27.46 | 0.9052 | 0.9037 | 72.10 | 67.80 |
| kodak_kodim08 | 416,600 | 416,644 | 21.42 | 21.14 | -0.28 | 21.58 | 20.98 | 0.8049 | 0.8043 | 56.34 | 52.37 |
| kodak_kodim19 | 416,596 | 416,652 | 26.90 | 26.57 | -0.33 | 28.54 | 27.65 | 0.8913 | 0.8891 | 71.76 | 67.71 |
| synthetic_panel | 416,588 | 416,636 | 51.75 | 49.39 | -2.36 | 57.49 | 56.41 | 0.9989 | 0.9987 | 96.97 | 96.79 |

Across all 325 matched cells (6 resolutions x 11 caps x 5 sources): mean dPSNR-Y -0.68 dB (Kodak -0.37, panel -1.95); range -6.21 .. +0.60 dB.

Quantiser-gain diagnostic (5/3 with the DERIVED native gains vs 5/3 with the 9/7 table, 2560/eye, 416,667 B):

| source | gains | bytes | PSNR-Y | PSNR-HVS | SSIM | VMAF |
|---|---|---|---|---|---|---|
| kodak_kodim08 | CDF 9/7, 9/7 (own) | 416,600 | 21.42 | 21.58 | 0.8049 | 56.34 |
| kodak_kodim08 | CDF 5/3, native 5/3 | 416,644 | 21.14 | 20.98 | 0.8043 | 52.37 |
| kodak_kodim08 | CDF 5/3, 9/7 table | 416,628 | 20.95 | 20.63 | 0.8018 | 54.21 |
| kodak_kodim04 | CDF 9/7, 9/7 (own) | 416,544 | 30.55 | 30.25 | 0.8924 | 76.16 |
| kodak_kodim04 | CDF 5/3, native 5/3 | 416,656 | 30.25 | 29.64 | 0.8900 | 71.03 |
| kodak_kodim04 | CDF 5/3, 9/7 table | 416,636 | 30.03 | 29.51 | 0.8949 | 70.65 |
| synthetic_panel | CDF 9/7, 9/7 (own) | 416,588 | 51.75 | 57.49 | 0.9989 | 96.97 |
| synthetic_panel | CDF 5/3, native 5/3 | 416,636 | 49.39 | 56.41 | 0.9987 | 96.79 |
| synthetic_panel | CDF 5/3, 9/7 table | 416,664 | 46.86 | 55.60 | 0.9986 | 96.69 |

Native gains stay the implementation; the table above is the contribution of that one structural difference. Per-band allocation is not exposed by the encoder (UNKNOWN).

## 7. PSNR-Y-MATCHED CONTROL (Control B, offline, 2560/eye; never 'equal quality')

5/3's cap is bisected until its PSNR-Y reaches 9/7's at 416,667 B; the other metrics at that point are reported as they fall:

| source | 9/7 bytes | 9/7 PSNR-Y | 5/3 bytes at match | 5/3 PSNR-Y | extra bytes | PSNR-HVS 9/7 / 5/3 | SSIM 9/7 / 5/3 | VMAF 9/7 / 5/3 |
|---|---|---|---|---|---|---|---|---|
| kodak_kodim04 | 416,544 | 30.55 | 505,120 | 30.55 | +21.3 % | 30.25 / 30.51 | 0.8924 / 0.9012 | 76.16 / 75.30 |
| kodak_kodim07 | 416,612 | 29.30 | 474,156 | 29.32 | +13.8 % | 28.36 / 28.50 | 0.9052 / 0.9108 | 72.10 / 70.95 |
| kodak_kodim08 | 416,600 | 21.42 | 458,648 | 21.43 | +10.1 % | 21.58 / 21.58 | 0.8049 / 0.8096 | 56.34 / 55.29 |
| kodak_kodim19 | 416,596 | 26.90 | 549,444 | 26.90 | +31.9 % | 28.54 / 28.67 | 0.8913 / 0.8998 | 71.76 / 71.16 |
| synthetic_panel | 416,588 | 51.75 | 467,460 | 51.83 | +12.2 % | 57.49 / 57.96 | 0.9989 / 0.9993 | 96.97 / 97.00 |

Mean extra bytes 5/3 needs at matched PSNR-Y: 17.9 % (within the 25 % Phase 3 trigger).

## 8. P97-1 raw results (MEASURED)

- arm proven by logcat: wavelet CDF 9/7, precision 1 -> 1, path compute (forced)
- gpu decode ms (min / mean / p50 / p95 / p99 / max): 2.77 / 3.24 / 2.96 / 4.28 / 4.32 / 7.72 (6 reports)
- convert ms (min / mean / p50 / p95 / p99 / max): 0.53 / 0.58 / 0.54 / 1.17 / 1.22 / 1.46 (6 reports)
- submit->fence ms (min / mean / p50 / p95 / p99 / max): 4.04 / 4.95 / 4.87 / 5.91 / 6.50 / 298.07 (6 reports)
- system: fps mean UNKNOWN median UNKNOWN min UNKNOWN; completed 4198, partial 39, skipped 25, dropped 58 (1.34 %), stale packets 8579 (1.9859 per frame), packets lost None, decode failures 0
- thermal: CPU 70.9 -> 72.3 C; GPU 67.2 -> 68.2 C; DDR 67.5 -> 68.6 C; hottest 70.9 -> 72.3 C (pre-launch sample 69.9 C, post 82.8 C); status start 1 end 1; rise 12.9 C
- GPU clock: mean 721 MHz (min 421, max 788, 67 % of 33 samples at the top level), busy 44 %; DERIVED GPU decode work 2.34 Mcycles/frame

## 9. P53-1 raw results (MEASURED)

- arm proven by logcat: wavelet CDF 5/3, precision 0 -> 0, path compute (forced)
- gpu decode ms (min / mean / p50 / p95 / p99 / max): 2.76 / 3.23 / 3.41 / 4.05 / 4.11 / 4.75 (5 reports)
- convert ms (min / mean / p50 / p95 / p99 / max): 0.53 / 0.69 / 0.54 / 1.79 / 1.82 / 1.89 (5 reports)
- submit->fence ms (min / mean / p50 / p95 / p99 / max): 4.02 / 5.04 / 4.91 / 6.13 / 6.48 / 240.74 (5 reports)
- system: fps mean UNKNOWN median UNKNOWN min UNKNOWN; completed 3530, partial 21, skipped 26, dropped 23 (0.64 %), stale packets 2894 (0.8039 per frame), packets lost None, decode failures 0
- thermal: CPU 74.3 -> 73.0 C; GPU 68.2 -> 68.6 C; DDR 69.2 -> 68.2 C; hottest 74.3 -> 73.0 C (pre-launch sample 69.6 C, post 83.5 C); status start 1 end 1; rise 13.9 C
- GPU clock: mean 723 MHz (min 421, max 788, 70 % of 33 samples at the top level), busy 44 %; DERIVED GPU decode work 2.33 Mcycles/frame

## 10. Pair 1 deltas (5/3 - 9/7) / 9/7 x 100

- gpu decode: mean -0.5 %, p50 +15.4 %, p95 -5.4 %, p99 -4.9 %
- convert: mean +20.7 %, p50 +0.0 %, p95 +53.0 %, p99 +49.2 %
- submit->fence: mean +1.8 %, p50 +0.9 %, p95 +3.7 %, p99 -0.3 %
- dropped rate -52.4 %, stale rate -59.5 %, thermal rise difference 1.0 C

## 11. P97-2 raw results (MEASURED)

- arm proven by logcat: wavelet CDF 9/7, precision 1 -> 1, path compute (forced)
- gpu decode ms (min / mean / p50 / p95 / p99 / max): 2.62 / 3.34 / 3.55 / 4.12 / 4.25 / 4.78 (5 reports)
- convert ms (min / mean / p50 / p95 / p99 / max): 0.53 / 0.65 / 0.54 / 1.22 / 1.78 / 1.86 (5 reports)
- submit->fence ms (min / mean / p50 / p95 / p99 / max): 3.83 / 5.03 / 5.01 / 6.05 / 6.52 / 270.00 (5 reports)
- system: fps mean UNKNOWN median UNKNOWN min UNKNOWN; completed 3464, partial 57, skipped 25, dropped 54 (1.50 %), stale packets 12813 (3.5592 per frame), packets lost None, decode failures 0
- thermal: CPU 72.3 -> 74.0 C; GPU 67.9 -> 67.9 C; DDR 68.6 -> 68.9 C; hottest 72.3 -> 74.0 C (pre-launch sample 71.3 C, post 83.5 C); status start 1 end 1; rise 12.2 C
- GPU clock: mean 722 MHz (min 421, max 788, 69 % of 32 samples at the top level), busy 44 %; DERIVED GPU decode work 2.41 Mcycles/frame

## 12. P53-2 raw results (MEASURED)

- arm proven by logcat: wavelet CDF 5/3, precision 0 -> 0, path compute (forced)
- gpu decode ms (min / mean / p50 / p95 / p99 / max): 2.51 / 3.23 / 3.43 / 4.02 / 4.12 / 4.73 (5 reports)
- convert ms (min / mean / p50 / p95 / p99 / max): 0.53 / 0.64 / 0.54 / 1.77 / 1.80 / 1.83 (5 reports)
- submit->fence ms (min / mean / p50 / p95 / p99 / max): 3.75 / 4.94 / 4.88 / 6.05 / 6.24 / 238.10 (5 reports)
- system: fps mean UNKNOWN median UNKNOWN min UNKNOWN; completed 3363, partial 87, skipped 40, dropped 110 (3.06 %), stale packets 25507 (7.0853 per frame), packets lost None, decode failures 0
- thermal: CPU 73.7 -> 73.0 C; GPU 68.2 -> 68.2 C; DDR 69.2 -> 68.9 C; hottest 73.7 -> 73.0 C (pre-launch sample 70.6 C, post 82.1 C); status start 1 end 1; rise 11.5 C
- GPU clock: mean 725 MHz (min 421, max 788, 70 % of 33 samples at the top level), busy 42 %; DERIVED GPU decode work 2.34 Mcycles/frame

## 13. Pair 2 deltas (5/3 - 9/7) / 9/7 x 100

- gpu decode: mean -3.4 %, p50 -3.4 %, p95 -2.4 %, p99 -3.1 %
- convert: mean -1.2 %, p50 +0.0 %, p95 +45.1 %, p99 +1.1 %
- submit->fence: mean -1.9 %, p50 -2.6 %, p95 +0.0 %, p99 -4.3 %
- dropped rate 103.7 %, stale rate 99.1 %, thermal rise difference -0.7 C

## 14. Thermal matching

- Pair 1: P97 start 69.9 C, P53 start 69.6 C, difference 0.3 C -> THERMALLY MATCHED; ends 82.8 / 83.5 C, status 1->3 / 1->3
- Pair 2: P97 start 71.3 C, P53 start 70.6 C, difference 0.7 C -> THERMALLY MATCHED; ends 83.5 / 82.1 C, status 1->3 / 1->3

Pairs agree on GPU and fence: YES (GPU both within the 5 % band; fence both within the 5 % band; GPU mean -0.5 % / -3.4 %, fence +1.8 % / -1.9 %).

## 15. Memory-traffic comparison

LOGICAL INTERMEDIATE BYTES per frame at the live encoded size 1984x896 4:4:4, DERIVED from buffer dimensions, precision and the pass structure (dequant writes every band once; each of five iDWT dispatches reads four bands and writes the next LL, or the three R8 planes). Physical DRAM traffic: UNKNOWN.

| arm | coefficient storage | dequant writes | iDWT reads | iDWT writes | total / frame | bytes / reconstructed pixel | per second at 90 Hz |
|---|---|---|---|---|---|---|---|
| 9/7 FP32 compute (precision 1) | R16F levels 0-1, R32F levels 2-4 | 11,332,608 | 15,082,368 | 9,082,752 | 35,497,728 | 19.97 | 3.19 GB/s |
| 5/3 FP16 compute (precision 0) | R16F all levels | 10,665,984 | 14,207,424 | 8,874,432 | 33,747,840 | 18.98 | 3.04 GB/s |

5/3 FP16 changes logical intermediate bytes by -4.9 % (DERIVED). The finest two levels are already R16F at precision 1, so all-FP16 storage saves only the three coarse levels' R32F; the shared tile is f16vec2 in both arms. Temporary buffer size: one wavelet image per arm (992x448, 12 layers, 5 mips; precision 1 splits it into a 2-mip R16F image and a 3-mip R32F image). Register pressure: UNKNOWN (no ISA).

## 16. Pass/barrier comparison (DERIVED from source; structural identity gate)

| | 9/7 FP32 compute | 5/3 FP16 compute |
|---|---|---|
| dequant/unpack dispatches | one per (component, level, band) present, <= 60, then 1 barrier | identical |
| iDWT dispatches | 5 levels x 3 components = 15 (`Decoder::Impl::idwt`, fused H+V per level in shared memory) | identical (same loop, spec constant selects the lifting) |
| barriers | 1 compute->compute per level after the three component dispatches = 5, plus the dequant->iDWT barrier | identical |
| decomposition levels / tile / apron / workgroup | 5 / 32x32 / 4 / 64 threads | identical |
| intermediate buffers | wavelet image, R16F+R32F split | wavelet image, R16F only |
| full-resolution-equivalent passes | ~2 | identical |
| arithmetic | 4 lifting steps + K scaling, FP32 | 2 lifting steps, no scaling, FP16 |
| quantiser gain table | 9/7 | scaled by the 5/3 ratios (item 3) |

Gate: STRUCTURALLY IDENTICAL except transform arithmetic, precision/storage and the gain table (the dispatch trace is not exposed by the driver; counts are from the source loops).

## 17. K propagation

Standalone attribution (device, 3328x1472 4:4:4 full range, 416,6xx B frames (w97.wave 416,708 B; w53.wave 416,636 B) from the same encoder_input.y4m, 200 iterations, MEASURED): A 9/7 FP32 7.057 ms, B 5/3 FP32 6.879 ms, C 5/3 FP16 6.815 ms (convert 1.466 ms in all three). Transform effect A-B +0.178 ms (+2.5 %), precision effect B-C +0.064 ms (+0.9 %), combined +0.242 ms (+3.4 %). Diagnostic only; 5/3 FP32 is not a live arm.

Noise floor per statistic = |P97-1 - P97-2| GPU decode: mean 0.09 ms, p50 0.59 ms, p95 0.16 ms, p99 0.07 ms

Pair 1 (THERMALLY MATCHED):
- mean: GPU savings +0.02 ms, fence savings -0.09 ms, K = n/a -> NOT COMPUTED (gpu_savings <= noise)
- p50: GPU savings -0.46 ms, fence savings -0.04 ms, K = n/a -> NOT COMPUTED (gpu_savings <= noise)
- p95: GPU savings +0.23 ms, fence savings -0.22 ms, K = -0.96 -> K < 0: GPU improves, fence worsens -- contradictory; investigate sync/scheduling
- p99: GPU savings +0.21 ms, fence savings +0.02 ms, K = 0.10 -> K ~ 0: GPU gain does not reach the fence -- downstream bottleneck

Pair 2 (THERMALLY MATCHED):
- mean: GPU savings +0.11 ms, fence savings +0.10 ms, K = 0.86 -> K ~ 1: the GPU gain propagates to completion latency
- p50: GPU savings +0.12 ms, fence savings +0.13 ms, K = n/a -> NOT COMPUTED (gpu_savings <= noise)
- p95: GPU savings +0.10 ms, fence savings +0.00 ms, K = n/a -> NOT COMPUTED (gpu_savings <= noise)
- p99: GPU savings +0.13 ms, fence savings +0.28 ms, K = 2.15 -> K > 1: fence improves more than GPU time -- barriers/traffic/stalls removed beyond the timestamp interval

### Latency vs GPU clock, per 720-frame window (MEASURED windows, DERIVED fits)

Each report window is paired with the mean sampled clock inside it; latency is fitted against 1/f per arm (time = work / rate), then each arm's windows are scored against the other arm's fit. One shared relationship means the transform contributes little to completion time.

| metric | 9/7 windows | 5/3 windows | 9/7 fit r2 | 5/3 fit r2 | 5/3 residual on the 9/7 fit | 9/7 residual on the 5/3 fit | pooled r2 - best arm r2 | verdict |
|---|---|---|---|---|---|---|---|---|
| GPU decode mean | 11 | 10 | 0.060 | UNKNOWN | -0.044 ms (-1.3 %) | UNKNOWN ms (UNKNOWN %) | UNKNOWN | ONE RELATIONSHIP (within the 9/7 scatter) |
| submit->fence mean | 11 | 10 | 0.015 | UNKNOWN | -0.013 ms (-0.3 %) | UNKNOWN ms (UNKNOWN %) | UNKNOWN | ONE RELATIONSHIP (within the 9/7 scatter) |

| arm | window end | clock MHz | busy % | GPU ms | fence ms |
|---|---|---|---|---|---|
| 97 | 1790193573 | 788 | 56 | 3.15 | 5.22 |
| 97 | 1790193581 | 788 | 53 | 3.23 | 4.76 |
| 97 | 1790193589 | 788 | 57 | 3.23 | 4.89 |
| 97 | 1790193597 | 788 | 58 | 3.19 | 4.93 |
| 97 | 1790193605 | 788 | 54 | 3.28 | 4.90 |
| 97 | 1790193613 | 763 | 55 | 3.38 | 4.98 |
| 97 | 1790193806 | 788 | 52 | 3.43 | 5.44 |
| 97 | 1790193814 | 778 | 54 | 3.47 | 5.09 |
| 97 | 1790193822 | 788 | 58 | 3.48 | 5.15 |
| 97 | 1790193830 | 788 | 58 | 3.14 | 4.74 |
| 97 | 1790193838 | 775 | 54 | 3.17 | 4.74 |
| 53 | 1790193690 | 788 | 52 | 3.21 | 5.34 |
| 53 | 1790193698 | 788 | 52 | 3.16 | 4.95 |
| 53 | 1790193706 | 788 | 56 | 3.33 | 5.24 |
| 53 | 1790193714 | 788 | 57 | 3.20 | 4.83 |
| 53 | 1790193722 | 788 | 54 | 3.23 | 4.82 |
| 53 | 1790193924 | 788 | 52 | 3.40 | 5.29 |
| 53 | 1790193931 | 788 | 52 | 3.33 | 4.88 |
| 53 | 1790193939 | 788 | 56 | 3.38 | 5.08 |
| 53 | 1790193947 | 788 | 54 | 3.04 | 4.71 |
| 53 | 1790193955 | 788 | 52 | 2.98 | 4.72 |

## 18. Dropped-frame / stale-packet regression (MEASURED)

| cell | frames | completed | dropped | dropped % | skipped | stale packets | stale / frame | packets lost | CPU start/end | hottest start/end | status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| P97-1 | 4320 | 4198 | 58 | 1.34 | 25 | 8579 | 1.9859 | None | UNKNOWN / UNKNOWN | 69.9 / 82.8 | 1 -> 3 |
| P53-1 | 3600 | 3530 | 23 | 0.64 | 26 | 2894 | 0.8039 | None | UNKNOWN / UNKNOWN | 69.6 / 83.5 | 1 -> 3 |
| P97-2 | 3600 | 3464 | 54 | 1.50 | 25 | 12813 | 3.5592 | None | UNKNOWN / UNKNOWN | 71.3 / 83.5 | 1 -> 3 |
| P53-2 | 3600 | 3363 | 110 | 3.06 | 40 | 25507 | 7.0853 | None | UNKNOWN / UNKNOWN | 70.6 / 82.1 | 1 -> 3 |

- Pair 1: dropped rate -52.4 %, stale rate -59.5 %, thermal rise difference 1.0 C (THERMALLY MATCHED)
- Pair 2: dropped rate 103.7 %, stale rate 99.1 %, thermal rise difference -0.7 C (THERMALLY MATCHED)

## 19. Sustained results (Phase 3)

Trigger conditions met by the short pairs: convert mean +9.7 %; convert p95 +49.0 %; convert p99 +25.2 %; dropped rate -26.2 %; 5/3 needs +17.9 % bytes at the PSNR-Y match (<= 25 %).

- S97 (CDF 9/7, precision 1): gpu decode 2.63 / 3.30 / 3.18 / 4.27 / 4.31 / 7.46 (39 reports); convert 0.53 / 0.58 / 0.54 / 1.41 / 1.50 / 1.82 (39 reports); fence 3.99 / 4.91 / 4.83 / 6.10 / 6.79 / 277.52 (39 reports); frames 28080 dropped 165 (0.59 %) stale 26011; thermal CPU 70.9 -> 72.0 C; GPU 66.5 -> 67.5 C; DDR 66.9 -> 67.9 C; hottest 70.9 -> 72.0 C (pre-launch sample 67.2 C, post 82.8 C); status start 1 end 1; GPU clock mean 770 MHz (min 690, max 788, 62 % of 149 samples at the top level), busy 56 %; DERIVED GPU decode work 2.54 Mcycles/frame
- S53 (CDF 5/3, precision 0): gpu decode 2.65 / 3.33 / 3.43 / 4.13 / 4.21 / 4.82 (39 reports); convert 0.53 / 0.63 / 0.54 / 1.81 / 1.85 / 1.92 (39 reports); fence 3.92 / 5.10 / 5.00 / 6.23 / 6.71 / 244.11 (39 reports); frames 28080 dropped 124 (0.44 %) stale 18643; thermal CPU 73.0 -> 71.3 C; GPU 67.9 -> 66.9 C; DDR 68.6 -> 67.5 C; hottest 73.0 -> 71.3 C (pre-launch sample 69.6 C, post 82.1 C); status start 1 end 1; GPU clock mean 760 MHz (min 421, max 788, 76 % of 162 samples at the top level), busy 52 %; DERIVED GPU decode work 2.53 Mcycles/frame
- Sustained pair: start 67.2 / 69.6 C, difference 2.4 C -> THERMALLY MATCHED; end 82.8 / 82.1 C
- Sustained deltas gpu decode: mean +1.0 %, p50 +7.9 %, p95 -3.3 %, p99 -2.3 %
- Sustained deltas convert: mean +8.0 %, p50 +0.0 %, p95 +28.4 %, p99 +23.3 %
- Sustained deltas submit->fence: mean +3.8 %, p50 +3.5 %, p95 +2.1 %, p99 -1.2 %
- Sustained regression: dropped rate -24.8 %, stale rate -28.3 %, thermal rise difference -3.1 C
- Sustained K mean: GPU savings -0.03 ms, fence savings -0.19 ms, K = n/a -> NOT COMPUTED (gpu_savings <= noise)
- Sustained K p50: GPU savings -0.25 ms, fence savings -0.17 ms, K = n/a -> NOT COMPUTED (gpu_savings <= noise)
- Sustained K p95: GPU savings +0.14 ms, fence savings -0.13 ms, K = -0.93 -> K < 0: GPU improves, fence worsens -- contradictory; investigate sync/scheduling
- Sustained K p99: GPU savings +0.10 ms, fence savings +0.08 ms, K = 0.80 -> K ~ 1: the GPU gain propagates to completion latency

## 20. Quality / bandwidth / latency trade

- Quality (Control A): 5/3 gives up 0.37 dB PSNR-Y on Kodak and 1.95 dB on the synthetic panel at equal bytes (MEASURED offline).
- Representation size (Control B): 5/3 needs 17.9 % more bytes to match 9/7's PSNR-Y (MEASURED offline); at 400 Mbps that is 471 Mbps.
- Completion latency (live, pooled means): GPU decode -2.0 %, submit->fence -0.1 %; standalone combined +3.4 %.
- Logical intermediate data: -4.9 % (DERIVED); thermal and stability: item 18.
- Outcome **E**: no meaningful performance difference: simpler arithmetic and FP16 do not move completion.

## 21. Remaining unknowns

- A first short-pair run (`cells-unsampled/`, 14:35-14:41, no clock trace, headset started cold and asleep) gave Pair 1 GPU -1.2 % / fence -1.3 % and Pair 2 GPU -22.3 % / fence -20.4 %, with the 9/7 cells at ~4.0 ms GPU against the 3.2-3.3 ms every clock-sampled and sustained 9/7 cell shows. Its within-cell timelines shift by ~0.5 ms between consecutive reports, consistent with the 421->788 MHz warm-up ramps the sampled rerun recorded; it is retained as history and excluded from the paired evidence, which is the sampled rerun (`cells/`, 14:58-15:07).

- The latency-vs-clock cross-fit has no 5/3 fit of its own: every 5/3 window sat at exactly 788 MHz, so its residual against the 9/7 relationship is the whole test; the 9/7 fit itself has almost no clock range (763-788 MHz) and its r2 is correspondingly small, so the verdict rests on the overlap of the two arms' windows at the same clock, not on a slope.

- GPU DVFS: the Adreno's msm-adreno-tz governor moves the clock with load (285-788 MHz). Every short cell shifted its decode time by ~0.5 ms between consecutive 720-frame reports with the thermal status unchanged, and cells that ran at a lower busy fraction can sit at a lower clock, so time deltas between arms are confounded with clock state unless the clock was sampled; cells with a `gpufreq.csv` report the mean clock and DERIVED cycles per frame above, cells without one do not.

- Physical DRAM traffic, cache behaviour and register pressure on the Adreno 740 (no counters, no ISA): only the logical model exists.
- Whether the driver promotes float16 arithmetic internally: the D diagnostic proves FP16 math executes, not that every op stays FP16.
- Two pairs plus one sustained pair; no distribution test is claimed. Bytes are equal by configuration and rate control per cell, not replayed.
- The FP16 gate pooled 65.09 dB sits at the PASS boundary; Cb alone is PASS WITH DEVIATION. A different source could tip the pooled figure.
- The live workload is the synthetic benchmark panel (unworn, sensor covered), not game content; the RD controls use Kodak and the panel offline.

## 22. Decision: does 5/3 FP16 become the mathematical baseline?

NO -- outcome E. GPU-time and fence-time conclusions stated separately: 5/3 FP16 changes inverse-decoder GPU execution by -2.0 % (pooled mean) and submit->fence completion by -0.1 % (pooled mean); it changes logical intermediate bytes by -4.9 % (DERIVED) and costs 17.9 % bytes at matched PSNR-Y (MEASURED offline). CDF 9/7 FP32 compute remains the reference. Per outcome E, further transform simplification (Haar, WHT) must justify itself through fewer passes, fewer barriers, lower memory traffic or simpler topology, not ops per pixel. 
