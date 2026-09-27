# 0. EXECUTIVE RESEARCH SUMMARY

Current PyroWave baseline (all MEASURED unless marked):
- transform: CDF 9/7 irreversible float lifting, 5 levels, 32x32 independent blocks, raw bit-planes with no entropy coder (from source, checkout d2997ac)
- target resolution: 2560x2560 per eye is the research reference; the measured live operating point renders 60 % linear (2131x2304) and encodes a gaze-foveated 1984x896 frame
- target refresh rate: 90 Hz (11.1 ms period)
- practical bitrate region: 300-400 Mbps at 90 Hz (416,667-555,556 B/frame); link measured 708-978 Mbps
- known decode constraints: on the Adreno 740 the GPU decode is 3.7 ms mean / 5.2 ms p99 at 1984x896 4:4:4 but submit->fence is 6.4 / 8.9 ms, and the fence balloons to 12-18 ms at thermal status 3; the fragment path (3 render passes per level) is what the Adreno runs (E101, E102)
- known quality reference: PSNR-Y 57.45 dB at 600 Mbps on the dashboard frame (3328x1472, decoded vs encoder input); offline, 2560^2 @ 416,667 B scores PSNR-HVS 21.6 dB on tiled kodim08 and 57.5 dB on the synthetic panel against the scaled reference (E104)

Most important findings:
1. MEASURED (device): the arithmetic is not the bottleneck on the Adreno -- GPU decode 3.7 ms vs 6.4 ms submit->fence at a cool headset and 12-18 ms hot (E101); any candidate must be judged on passes and synchronisation, not multiplies.
2. MEASURED (lab, bit-plane size model, 2560^2, 416,667 B): CDF 9/7 beats Haar by 1.85 dB PSNR-Y on the Kodak mean (28.94 vs 27.09); on the synthetic panel the order REVERSES: Haar 79.30 vs 9/7 73.67 (contradictory evidence, preserved). Haar's inverse needs no multiplications and about a quarter of 9/7's adds (DERIVED).
3. MEASURED: CDF 5/3 (shift-only lifting) scores 28.68 dB Kodak mean vs 9/7's 28.94 at the same bytes -- the cheapest transform that keeps most of 9/7's compaction.
4. MEASURED (lab): CDF 9/7 lifting in FP16 arithmetic reconstructs at only 53.4 dB against its FP32 result (max error 3.399 code values), while 5/3, Haar, DCT and WHT stay above 70 dB in FP16 math; FP16 *storage* is free for all (>= 78 dB). The shorter transforms can run the whole inverse at half precision; 9/7 cannot -- which is why PyroWave defaults to FP32 math with FP16 storage.
5. MEASURED (lab): encoder-side pre-filtering (Gaussian sigma 0.5) at the 416,667 B cap GAINS +0.63 dB against the ORIGINAL on dense natural content (bytes at fixed step fall 30 %) and costs -39.9 dB on the synthetic panel -- zero decoder cost, content-dependent sign.

Most promising candidates for actual testing (unranked; see section 18):
1. C05 CDF 5/3 inside PyroWave's own pipeline (shift-only lifting, same passes)
2. C02 Haar with multi-level fusion in one tile (fewest passes of any wavelet)
3. C04 block DCT 16/32 with a Vulkan port of a GPUJPEG-class IDCT (single pass)
4. C03 block WHT 16/32 (add/sub only, single pass) at the extra bytes the lab measured
5. C01 PyroWave compute path forced on the Adreno (halves the fragment path's passes) -- a build flag, not a new codec

Largest remaining unknowns:
1. No mobile-GPU decode timing exists for any candidate but PyroWave (C01) and the hardware decoders (C10).
2. Whether PyroWave's compute path runs correctly and faster than its fragment path on the Adreno 740 (the driver check forces fragment).
3. How much of the 6.4-18 ms fence is queue contention with the runtime compositor rather than decode work.
4. Bit-plane packing cost is a model of PyroWave's bitstream applied to other transforms; a real entropy coder would change every size in this report.
5. Stereo disparity and real game content: every source is left = right and either tiled Kodak or the synthetic panel.

**Scope of the lab caps:** every lab cell applies its byte cap to ONE eye's LUMA plane (2560x2560 = 6.55 Msamples), not to the pipeline's stereo 4:4:4 frame (5120x2560x3 = 39.3 Msamples). The caps therefore mean ~6x more bits per sample than the same numbers in the offline PyroWave matrix (E104), which is why lab PSNR-Y values sit ~7 dB above the matrix's for the same frame. Comparisons BETWEEN transforms in the lab are at equal bytes and equal samples and are fair; lab absolutes are not comparable to the matrix or to the headset.

No overall winner is declared: no candidate other than C01 and C10 has a comparable device measurement.

---

# 1. REQUIREMENTS AND CONSTRAINTS

| Variable | Requirement / Current Value | Evidence Class | Notes |
|---|---:|---|---|
| Target per-eye resolution | 2560 x 2560 (research reference); 2131 x 2304 live | MEASURED | live point is 60 % of the 3552x3840 panel |
| Target FPS | 90 | MEASURED | runtime grants 90 Hz; held 5 min |
| Frame interval | 11.11 ms | DERIVED | 1/90 |
| Preferred bitrate | 300 Mbps | MEASURED | X60-300-90: 55.5 ms, 13 dropped of ~3,600 |
| Practical bitrate ceiling | 400 Mbps at 90 Hz | MEASURED | 24k late packets, 92 dropped at 400; link 708-978 Mbps |
| Target decode latency | < 11.1 ms incl. fence, sustained hot | DERIVED | one period at 90 Hz |
| Current PyroWave decode latency | GPU 3.7 ms mean / 5.2 p99; fence 6.4 / 8.9 (cool); 12-18 hot | MEASURED | 1984x896 4:4:4, E101 |
| Current PyroWave quality | PSNR-Y 57.45 dB @ 600 Mbps dashboard frame; subjective "good and smooth" @ 400 Mbps 60 % | MEASURED | different frames; not comparable to lab numbers |
| Encoder hardware | RTX 3090 | OBSERVED | encode 3.8-7.7 ms in ALVR; 0.25 ms in pyrowave-bench |
| Decoder hardware | Galaxy XR SM-I610, Adreno 740, Vulkan 1.3.295 | MEASURED | fragment decode path selected by driver id |
| Encoder compute constraint | LOW PRIORITY | NOT APPLICABLE |  |
| Decoder compute constraint | CRITICAL | NOT APPLICABLE |  |
| Decoder memory traffic | CRITICAL | NOT APPLICABLE | fragment path ~2x compute-path intermediates (DERIVED) |
| Thermal load | CRITICAL | MEASURED | 78 C plateau at status 2-3 over 5 min; fence 12-18 ms at status 3 |

---

# 2. CANDIDATE INVENTORY

| ID | Transform / Method | Implementation | Repository | License | GPU API | CPU Path | GPU Path | Encoder | Decoder | Maintenance Status | Evidence Quality |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C01 | PyroWave: CDF 9/7 irreversible DWT, 5 levels, raw bit-planes (no entropy coder) | pyrowave (Themaister) | https://github.com/Themaister/pyrowave | MIT | Vulkan compute / fragment | packet parse only | dequant + iDWT | YES | YES | active (2025) | MEASURED on device + PUBLISHED desktop |
| C02 | Haar wavelet: 2-tap orthonormal lifting, hierarchical | none found (lab: numpy) | UNKNOWN | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | lab only | lab only | NOT APPLICABLE | MEASURED (lab RD) + DERIVED cost |
| C03 | Walsh-Hadamard: block WHT 8/16/32/64, add/sub butterflies | none found for images (HadaCore is an ML kernel) | https://github.com/HanGuo97/hadacore (UNVERIFIED) | UNKNOWN | CUDA (HadaCore) | NOT APPLICABLE | NOT APPLICABLE | lab only | lab only | UNKNOWN | MEASURED (lab RD) + DERIVED cost |
| C04 | DCT: block DCT-II 8/16/32 (orthonormal) | GPUJPEG (CESNET), nvJPEG, ffmpeg mjpeg | https://github.com/CESNET/GPUJPEG | BSD-2-Clause (GPUJPEG) | CUDA | Huffman (GPUJPEG: GPU) | IDCT + colour | YES | YES | active (2024-25) | PUBLISHED desktop timings + MEASURED (lab RD) |
| C05 | CDF 5/3 wavelet: 2-step integer lifting (JPEG 2000 reversible / JPEG XS) | JPEG XS (intoPIX, commercial); lab numpy | https://www.intopix.com/fasttico-xs-sdks | commercial | CUDA/OpenCL (intoPIX) | entropy decode | iDWT | YES (commercial) | YES (commercial) | active | PUBLISHED (marketing, no numbers) + MEASURED (lab RD) |
| C06 | Daubechies db2/db4: orthogonal 4-/8-tap filter banks | none (lab numpy) | UNKNOWN | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | lab only | lab only | NOT APPLICABLE | MEASURED (lab RD) + DERIVED cost |
| C07 | Laplacian pyramid: 5-tap binomial down/up, residual pyramid (4/3 over-complete) | none (lab numpy) | UNKNOWN | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | lab only | lab only | NOT APPLICABLE | MEASURED (lab RD) + DERIVED cost |
| C08 | Fixed PCA / KLT: offline eigenbasis (colour measured; spatial not run) | none | UNKNOWN | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | no | no | NOT APPLICABLE | MEASURED (colour PCA only) |
| C09 | VC-2 / Dirac Pro: wavelet (5/3, 9/7 options), slices, exp-Golomb | FFmpeg vc2 (CPU); Vulkan port in progress | https://github.com/FFmpeg/FFmpeg | LGPL-2.1+ | Vulkan compute (in progress) | Golomb parse | iDWT | YES | YES | active | PUBLISHED structure only |
| C10 | Hardware H.264 / HEVC: block DCT + inter/intra prediction, CABAC | Adreno video block via MediaCodec | NOT APPLICABLE | NOT APPLICABLE | MediaCodec | none | fixed-function | NVENC | hardware | NOT APPLICABLE | MEASURED on device |

---

# 3. SOURCE EVIDENCE TABLE

| Evidence ID | Candidate ID | Source URL | Hardware | Resolution | Input Format | Quality Setting | Size / Bitrate | Encode Time | Decode Time | Quality Metric | Quality Value | What Was Timed | Evidence Class |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| E001 | C01 | https://themaister.net/blog/2025/06/16/i-designed-my-own-ridiculously-fast-game-streaming-video-codec-pyrowave/ | RX 9070 XT (RADV) | 1080p / 4K 4:2:0 | y4m 8-bit | ~1.5 bpp | 200+ Mbit/s at 60 fps | 0.13 ms (1080p) / 0.25 ms (4K) | "Under 100 microseconds" | none | UNKNOWN | GPU time; transfers/parse UNKNOWN | PUBLISHED |
| E002 | C01 | https://github.com/Themaister/pyrowave | UNKNOWN | 1080p / 4K | UNKNOWN | UNKNOWN | UNKNOWN | < ~0.1 ms (1080p), < ~0.2 ms (4K) | same claim | none | UNKNOWN | UNKNOWN | PUBLISHED |
| E003 | C01 | https://docs.punktfunk.unom.io/docs/pyrowave | RTX 5070 Ti | 1080p | 4:2:0 8-bit | ~1.6 bpp | UNKNOWN | ~0.15 ms | ~0.07 ms | none | UNKNOWN | "GPU compute"; transfers UNKNOWN | PUBLISHED (secondary) |
| E004 | C04 | https://github.com/CESNET/GPUJPEG/blob/master/README.md | RTX 3080 | HD / 4K / 8K | RGB | q75, non-interleaved, rst 24-36 | UNKNOWN | 0.54 ms HD / 1.71 ms 4K | 0.75 ms HD / 1.94 ms 4K / 6.76 ms 8K | none | UNKNOWN | mean of 99 runs; transfers UNKNOWN | PUBLISHED |
| E005 | C04 | https://github.com/CESNET/GPUJPEG/issues/25 | RTX 2070 Super | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | 23.01 ms GPU vs 278.78 ms full | none | UNKNOWN | full figure includes transfers+preprocessing | PUBLISHED (issue thread) |
| E006 | C04 | https://github.com/CESNET/UltraGrid/wiki/Performance | UNKNOWN | 1080p30 | UNKNOWN | JPEG q90 | UNKNOWN | NOT APPLICABLE | end-to-end 4 frames vs 3.75 uncompressed | none | UNKNOWN | whole system, frames | PUBLISHED |
| E007 | C04 | https://arxiv.org/abs/2111.09219 | A100 / V100 | UNKNOWN | JPEG | UNKNOWN | UNKNOWN | NOT APPLICABLE | relative: up to 3.4x over nvJPEG HW | none | UNKNOWN | UNKNOWN | PUBLISHED |
| E008 | C05 | https://www.intopix.com/fasttico-xs-sdks | UNKNOWN | "8K real-time" | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | none | UNKNOWN | marketing claim | PUBLISHED (marketing) |
| E009 | C09 | https://www.khronos.org/blog/video-encoding-and-decoding-with-vulkan-compute-shaders-in-ffmpeg | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | none | UNKNOWN | structure only (Golomb parse simplification) | PUBLISHED |
| E010 | FFv1 (reference only) | https://www.khronos.org/blog/video-encoding-and-decoding-with-vulkan-compute-shaders-in-ffmpeg (claim unverified at source) | RTX 6000 Ada | 3840x2160 bgr0 | lossless | 50 Mbps | NOT APPLICABLE | UNKNOWN | "400 fps" (throughput) | lossless | NOT APPLICABLE | throughput, not single-frame latency | PUBLISHED (unverified) |
| E011 | C04/DXT | https://doi.org/10.1016/j.future.2013.06.006 | UNKNOWN | 1080p | UNKNOWN | UNKNOWN | UNKNOWN | NOT APPLICABLE | 133 ms (JPEG) / 166 ms (DXT) motion-to-photon | none | UNKNOWN | whole system | PUBLISHED |
| E101 | C01 | captures/pyrowave-in-alvr/udp-ladder/README.md (this project) | Galaxy XR, Adreno 740 | 1984x896 4:4:4 (60 % render, foveated) | PyroWave UDP | 400 Mbps @ 90 Hz | ~556 KB/frame | 6.8 ms (RTX 3090, ALVR stage) | GPU decode 2.5/3.7/4.5/5.2 ms min/mean/p95/p99; submit->fence 6.4/8.3/8.9 | subjective | "good and smooth" | GPU timestamps on device (decode, convert, fence) | MEASURED |
| E102 | C01 | memory: pyrowave-client-decode-path (this project) | Galaxy XR, Adreno 740 | 3328x1472 4:4:4 / 4:2:0 | PyroWave | 600 Mbps @ 72 Hz | 1,041,660 B | NOT APPLICABLE | 4:4:4 6.23 best / 8.19 mean ms; 4:2:0 3.64 / 5.08 | PSNR-Y vs PC decode | 55.16 dB (headset) vs 57.76 (PC) | GPU timestamps, 200 iterations | MEASURED |
| E103 | C10 | memory: pyrowave-beats-the-hw-decoder (this project) | Galaxy XR, Adreno video block | 3328x1472 | H.264 / HEVC via MediaCodec | 600 Mbps | UNKNOWN | NOT APPLICABLE | H.264 14.80-18.88 ms; HEVC 82.01 ms | none | NOT APPLICABLE | ALVR decoder stage (packet received -> frame out) | MEASURED |
| E104 | C01 | captures/pyrowave-in-alvr/rd-matrix/SUMMARY.md (this project) | RTX 3090 (encode/decode), scoring ffmpeg/libvmaf | 1920..2560 per eye, SBS | 4:4:4 8-bit | 150..550 KB caps | exact caps | ~0.3 s process | ~0.3 s process (not decode latency) | PSNR / PSNR-HVS / SSIM / VMAF | see matrix | process wall time | MEASURED (quality) / NOT decode latency |

---

# 4. PUBLISHED BENCHMARKS

## C01 — PyroWave

### Published Performance

Resolution: 1080p / 4K
Pixels: 2,073,600 / 8,294,400
Hardware: RX 9070 XT (RADV)
Decode latency: "under 100 microseconds" (blog)
Encode latency: 0.13 ms / 0.25 ms
Compressed size: UNKNOWN
Bitrate: 200+ Mbit/s @ 60 fps
Quality metric: none
Quality value: none
Source: https://themaister.net/blog/2025/06/16/i-designed-my-own-ridiculously-fast-game-streaming-video-codec-pyrowave/
Evidence ID: E001

### Timing Scope

CPU parsing included: NO
Host→device transfer included: UNKNOWN
GPU synchronization included: YES (GPU timestamps)
Device→host transfer included: NO
Allocation included: UNKNOWN
Color conversion included: NO
Entropy decoding included: NOT APPLICABLE (no entropy coder)
Inverse transform included: YES

### Important Limitations

Desktop numbers are for a different GPU class and a 4:2:0 1080p frame; our device numbers are for a 1984x896 4:4:4 frame with the fragment path. Neither is a 2560^2 stereo frame.

## C02 — Haar wavelet

### Published Performance

Resolution: UNKNOWN (no published benchmark found; lab-only candidate)
Pixels: UNKNOWN (no published benchmark found; lab-only candidate)
Hardware: UNKNOWN (no published benchmark found; lab-only candidate)
Decode latency: UNKNOWN (no published benchmark found; lab-only candidate)
Encode latency: UNKNOWN (no published benchmark found; lab-only candidate)
Compressed size: UNKNOWN (no published benchmark found; lab-only candidate)
Bitrate: UNKNOWN (no published benchmark found; lab-only candidate)
Quality metric: UNKNOWN (no published benchmark found; lab-only candidate)
Quality value: UNKNOWN (no published benchmark found; lab-only candidate)
Source: UNKNOWN (no published benchmark found; lab-only candidate)
Evidence ID: UNKNOWN (no published benchmark found; lab-only candidate)

### Timing Scope

CPU parsing included: NOT APPLICABLE
Host→device transfer included: NOT APPLICABLE
GPU synchronization included: NOT APPLICABLE
Device→host transfer included: NOT APPLICABLE
Allocation included: NOT APPLICABLE
Color conversion included: NOT APPLICABLE
Entropy decoding included: NOT APPLICABLE
Inverse transform included: NOT APPLICABLE

### Important Limitations

No published performance exists for this candidate; the only evidence is this laboratory's rate-distortion measurement and a derived cost model.

## C03 — Walsh-Hadamard

### Published Performance

Resolution: UNKNOWN (no published benchmark found; lab-only candidate)
Pixels: UNKNOWN (no published benchmark found; lab-only candidate)
Hardware: UNKNOWN (no published benchmark found; lab-only candidate)
Decode latency: UNKNOWN (no published benchmark found; lab-only candidate)
Encode latency: UNKNOWN (no published benchmark found; lab-only candidate)
Compressed size: UNKNOWN (no published benchmark found; lab-only candidate)
Bitrate: UNKNOWN (no published benchmark found; lab-only candidate)
Quality metric: UNKNOWN (no published benchmark found; lab-only candidate)
Quality value: UNKNOWN (no published benchmark found; lab-only candidate)
Source: UNKNOWN (no published benchmark found; lab-only candidate)
Evidence ID: UNKNOWN (no published benchmark found; lab-only candidate)

### Timing Scope

CPU parsing included: NOT APPLICABLE
Host→device transfer included: NOT APPLICABLE
GPU synchronization included: NOT APPLICABLE
Device→host transfer included: NOT APPLICABLE
Allocation included: NOT APPLICABLE
Color conversion included: NOT APPLICABLE
Entropy decoding included: NOT APPLICABLE
Inverse transform included: NOT APPLICABLE

### Important Limitations

No published performance exists for this candidate; the only evidence is this laboratory's rate-distortion measurement and a derived cost model.

## C04 — DCT

### Published Performance

Resolution: HD / 4K
Pixels: 2,073,600 / 8,294,400
Hardware: RTX 3080
Decode latency: 0.75 ms / 1.94 ms
Encode latency: 0.54 ms / 1.71 ms
Compressed size: UNKNOWN
Bitrate: UNKNOWN (q75)
Quality metric: none
Quality value: none
Source: https://github.com/CESNET/GPUJPEG/blob/master/README.md
Evidence ID: E004

### Timing Scope

CPU parsing included: UNKNOWN
Host→device transfer included: UNKNOWN
GPU synchronization included: UNKNOWN
Device→host transfer included: UNKNOWN
Allocation included: UNKNOWN
Color conversion included: UNKNOWN
Entropy decoding included: YES
Inverse transform included: YES

### Important Limitations

GPUJPEG is CUDA-only; the Adreno has no CUDA. The published time includes Huffman decoding of a JPEG, which is not the DCT-alone architecture this research asks about.

## C05 — CDF 5/3 wavelet

### Published Performance

Resolution: "8K"
Pixels: UNKNOWN
Hardware: UNKNOWN
Decode latency: UNKNOWN
Encode latency: UNKNOWN
Compressed size: UNKNOWN
Bitrate: UNKNOWN
Quality metric: none
Quality value: none
Source: https://www.intopix.com/fasttico-xs-sdks
Evidence ID: E008

### Timing Scope

CPU parsing included: UNKNOWN
Host→device transfer included: UNKNOWN
GPU synchronization included: UNKNOWN
Device→host transfer included: UNKNOWN
Allocation included: UNKNOWN
Color conversion included: UNKNOWN
Entropy decoding included: UNKNOWN
Inverse transform included: UNKNOWN

### Important Limitations

Marketing claim with no numbers, hardware or timing scope.

## C06 — Daubechies db2/db4

### Published Performance

Resolution: UNKNOWN (no published benchmark found; lab-only candidate)
Pixels: UNKNOWN (no published benchmark found; lab-only candidate)
Hardware: UNKNOWN (no published benchmark found; lab-only candidate)
Decode latency: UNKNOWN (no published benchmark found; lab-only candidate)
Encode latency: UNKNOWN (no published benchmark found; lab-only candidate)
Compressed size: UNKNOWN (no published benchmark found; lab-only candidate)
Bitrate: UNKNOWN (no published benchmark found; lab-only candidate)
Quality metric: UNKNOWN (no published benchmark found; lab-only candidate)
Quality value: UNKNOWN (no published benchmark found; lab-only candidate)
Source: UNKNOWN (no published benchmark found; lab-only candidate)
Evidence ID: UNKNOWN (no published benchmark found; lab-only candidate)

### Timing Scope

CPU parsing included: NOT APPLICABLE
Host→device transfer included: NOT APPLICABLE
GPU synchronization included: NOT APPLICABLE
Device→host transfer included: NOT APPLICABLE
Allocation included: NOT APPLICABLE
Color conversion included: NOT APPLICABLE
Entropy decoding included: NOT APPLICABLE
Inverse transform included: NOT APPLICABLE

### Important Limitations

No published performance exists for this candidate; the only evidence is this laboratory's rate-distortion measurement and a derived cost model.

## C07 — Laplacian pyramid

### Published Performance

Resolution: UNKNOWN (no published benchmark found; lab-only candidate)
Pixels: UNKNOWN (no published benchmark found; lab-only candidate)
Hardware: UNKNOWN (no published benchmark found; lab-only candidate)
Decode latency: UNKNOWN (no published benchmark found; lab-only candidate)
Encode latency: UNKNOWN (no published benchmark found; lab-only candidate)
Compressed size: UNKNOWN (no published benchmark found; lab-only candidate)
Bitrate: UNKNOWN (no published benchmark found; lab-only candidate)
Quality metric: UNKNOWN (no published benchmark found; lab-only candidate)
Quality value: UNKNOWN (no published benchmark found; lab-only candidate)
Source: UNKNOWN (no published benchmark found; lab-only candidate)
Evidence ID: UNKNOWN (no published benchmark found; lab-only candidate)

### Timing Scope

CPU parsing included: NOT APPLICABLE
Host→device transfer included: NOT APPLICABLE
GPU synchronization included: NOT APPLICABLE
Device→host transfer included: NOT APPLICABLE
Allocation included: NOT APPLICABLE
Color conversion included: NOT APPLICABLE
Entropy decoding included: NOT APPLICABLE
Inverse transform included: NOT APPLICABLE

### Important Limitations

No published performance exists for this candidate; the only evidence is this laboratory's rate-distortion measurement and a derived cost model.

## C08 — Fixed PCA / KLT

### Published Performance

Resolution: UNKNOWN (no published benchmark found; lab-only candidate)
Pixels: UNKNOWN (no published benchmark found; lab-only candidate)
Hardware: UNKNOWN (no published benchmark found; lab-only candidate)
Decode latency: UNKNOWN (no published benchmark found; lab-only candidate)
Encode latency: UNKNOWN (no published benchmark found; lab-only candidate)
Compressed size: UNKNOWN (no published benchmark found; lab-only candidate)
Bitrate: UNKNOWN (no published benchmark found; lab-only candidate)
Quality metric: UNKNOWN (no published benchmark found; lab-only candidate)
Quality value: UNKNOWN (no published benchmark found; lab-only candidate)
Source: UNKNOWN (no published benchmark found; lab-only candidate)
Evidence ID: UNKNOWN (no published benchmark found; lab-only candidate)

### Timing Scope

CPU parsing included: NOT APPLICABLE
Host→device transfer included: NOT APPLICABLE
GPU synchronization included: NOT APPLICABLE
Device→host transfer included: NOT APPLICABLE
Allocation included: NOT APPLICABLE
Color conversion included: NOT APPLICABLE
Entropy decoding included: NOT APPLICABLE
Inverse transform included: NOT APPLICABLE

### Important Limitations

No published performance exists for this candidate; the only evidence is this laboratory's rate-distortion measurement and a derived cost model.

## C09 — VC-2 / Dirac Pro

### Published Performance

Resolution: UNKNOWN
Pixels: UNKNOWN
Hardware: UNKNOWN
Decode latency: UNKNOWN
Encode latency: UNKNOWN
Compressed size: UNKNOWN
Bitrate: UNKNOWN
Quality metric: none
Quality value: none
Source: https://www.khronos.org/blog/video-encoding-and-decoding-with-vulkan-compute-shaders-in-ffmpeg
Evidence ID: E009

### Timing Scope

CPU parsing included: UNKNOWN
Host→device transfer included: UNKNOWN
GPU synchronization included: UNKNOWN
Device→host transfer included: UNKNOWN
Allocation included: UNKNOWN
Color conversion included: UNKNOWN
Entropy decoding included: UNKNOWN
Inverse transform included: UNKNOWN

### Important Limitations

Structure description only; no timings; Vulkan decoder status unclear.

## C10 — Hardware H.264 / HEVC

### Published Performance

Resolution: 3328x1472
Pixels: 4,898,816
Hardware: Galaxy XR Adreno video block
Decode latency: H.264 14.80-18.88 ms; HEVC 82.01 ms (MEASURED, ALVR stage)
Encode latency: NOT APPLICABLE
Compressed size: UNKNOWN
Bitrate: 600 Mbps
Quality metric: none
Quality value: none
Source: memory: pyrowave-beats-the-hw-decoder (this project)
Evidence ID: E103

### Timing Scope

CPU parsing included: YES (MediaCodec input)
Host→device transfer included: YES
GPU synchronization included: YES
Device→host transfer included: NOT APPLICABLE (surface out)
Allocation included: UNKNOWN
Color conversion included: YES
Entropy decoding included: YES
Inverse transform included: YES

### Important Limitations

Measured as ALVR's decoder stage (packet received to frame out), which includes MediaCodec queueing; not comparable to GPU-timestamp decode times.

---

# 5. NORMALIZED DERIVED ESTIMATES

Target: 2560 x 2560 per eye, 2 eyes, 90 FPS. Total pixels/frame: 13,107,200. Total pixels/second: 1,179,648,000.

Original benchmark: C01 PyroWave decode, RX 9070 XT (E001)
Original pixel count: 2,073,600
Original decode time: 0.1 ms
Target pixel count: 13,107,200
Scaling assumption: linear in pixels on the SAME GPU
Derived target decode time: 0.63 ms (same desktop GPU)
Evidence class: DERIVED
Confidence: LOW
Note: blog says 'under 100 microseconds' at 1080p; GPU class differs from the Adreno by roughly two orders of magnitude in throughput, so this says nothing about the headset

Original benchmark: C04 GPUJPEG decode, RTX 3080 (E004)
Original pixel count: 2,073,600
Original decode time: 0.75 ms
Target pixel count: 13,107,200
Scaling assumption: linear in pixels on the SAME GPU
Derived target decode time: 4.74 ms (same desktop GPU)
Evidence class: DERIVED
Confidence: LOW
Note: includes Huffman decode; desktop CUDA

Headset: NORMALIZATION NOT JUSTIFIED. Desktop-to-Adreno scaling is not linear in pixels: our own device measurement shows the fence (6.4-18 ms) exceeding the GPU work (3.7 ms) and rising with temperature, so no published desktop number can be scaled to the Galaxy XR.

---

# 6. QUALITY COMPARABILITY MATRIX

| Candidate | Metric | Best Published Quality | Bitrate / Size | Comparable to PyroWave? | Reason |
|---|---|---:|---:|---|---|
| C01 | PSNR-HVS-M-H (author's modified, luma) | ~35 dB 'good' curve | 125-300 Mbit/s 720p-4K | NO | author's own CSF weights and viewing-distance model; not reproducible here |
| C01 | PSNR-Y (this project) | 57.45 dB | 600 Mbps, 3328x1472 dashboard | YES | same evaluator (ffmpeg psnr) as the lab |
| C02 | PSNR-Y (lab, bit-plane model) | 27.09 dB Kodak mean | 416,667 B | YES | identical sources, quantiser and size model as C01's lab row |
| C03 | PSNR-Y (lab, bit-plane model) | 26.51 dB Kodak mean | 416,667 B | YES | identical sources, quantiser and size model as C01's lab row |
| C04 | PSNR-Y (lab, bit-plane model) | 27.97 dB Kodak mean | 416,667 B | YES | identical sources, quantiser and size model as C01's lab row |
| C05 | PSNR-Y (lab, bit-plane model) | 28.68 dB Kodak mean | 416,667 B | YES | identical sources, quantiser and size model as C01's lab row |
| C06 | PSNR-Y (lab, bit-plane model) | 28.59 dB Kodak mean | 416,667 B | YES | identical sources, quantiser and size model as C01's lab row |
| C07 | PSNR-Y (lab, bit-plane model) | 24.35 dB Kodak mean | 416,667 B | YES | identical sources, quantiser and size model as C01's lab row |
| C08 | none | NOT APPLICABLE | NOT APPLICABLE | NO | not run spatially |
| C09 | none published | UNKNOWN | UNKNOWN | UNKNOWN | no numbers found |
| C10 | subjective / fps | NOT APPLICABLE | 600 Mbps | NO | hardware codec; no PSNR pairing with timing |

PSNR, PSNR-HVS, PSNR-HVS-M, SSIM and MS-SSIM are reported in separate columns everywhere and never combined.

---

# 7. DECODER COMPUTATIONAL MODEL

## C01 — PyroWave

### Decode Stages

1. CPU packet parse (headers, block offsets; no entropy decode)
2. upload payload + offset table; barrier
3. one fused bit-plane unpack + dequant dispatch per band (up to 60)
4. inverse DWT: 5 levels; compute path fuses H+V per level, fragment path 3 render passes per level
5. output planes -> colour convert (separate pass in libpyroclient)

### Estimated Computational Structure

Arithmetic complexity: mult/pixel 8.0, add/pixel 11.0 (DERIVED)
Memory reads: coefficients once per level-pass; full-res-equivalent passes 2.0 (compute) / 4.0 (fragment)
Memory writes: same pass count; temporaries: 1x coefficients (FP16) + LL chain
Full-frame passes: 2.0
Temporary buffers: 1x coefficients (FP16) + LL chain
Branching: per-8x8 ballot skip
Serial dependencies: 5 sequential levels
Parallelism: per 32x32 tile per level; bands independent
Synchronization points: 1 barrier/level (compute); 3/level (fragment)

### Likely Primary Bottleneck

CPU/GPU SYNCHRONIZATION and MEMORY BANDWIDTH. Evidence: MEASURED device GPU decode 3.7 ms vs submit->fence 6.4-18 ms (E101); the fragment path's 3 passes per level double intermediate traffic (DERIVED from source).

## C02 — Haar wavelet

### Decode Stages

1. packet parse (same container assumed)
2. unpack + dequant
3. inverse Haar: 2-tap lifting per level; several levels fusable inside one 32x32 tile
4. output
5. colour convert

### Estimated Computational Structure

Arithmetic complexity: mult/pixel 0.0, add/pixel 2.7 (ESTIMATED)
Memory reads: coefficients once per level-pass; full-res-equivalent passes 1.0 (compute) / 2.0 (fragment)
Memory writes: same pass count; temporaries: coefficients only; multi-level fusable in one tile
Full-frame passes: 1.0
Temporary buffers: coefficients only; multi-level fusable in one tile
Branching: UNKNOWN
Serial dependencies: levels fusable (2-tap support)
Parallelism: fully tile-local
Synchronization points: 1 (if fused)

### Likely Primary Bottleneck

UNKNOWN on the headset (no measurement). From structure: block transforms (C03, C04, C08) are GPU DISPATCH / MEMORY BANDWIDTH bound with one pass; multi-level transforms (C02, C05, C06, C07) inherit C01's per-level synchronisation unless levels are fused. ESTIMATED.

## C03 — Walsh-Hadamard

### Decode Stages

1. packet parse
2. unpack + dequant
3. block WHT butterflies (log2 n stages of add/sub) per row then column, in registers
4. output
5. colour convert

### Estimated Computational Structure

Arithmetic complexity: mult/pixel 0.0, add/pixel see note (DERIVED (2*log2 n adds/pixel: 6/8/10/12 for 8/16/32/64))
Memory reads: coefficients once per level-pass; full-res-equivalent passes 1.0 (compute) / 1.0 (fragment)
Memory writes: same pass count; temporaries: one block in registers
Full-frame passes: 1.0
Temporary buffers: one block in registers
Branching: UNKNOWN
Serial dependencies: none across blocks
Parallelism: block-local
Synchronization points: 1

### Likely Primary Bottleneck

UNKNOWN on the headset (no measurement). From structure: block transforms (C03, C04, C08) are GPU DISPATCH / MEMORY BANDWIDTH bound with one pass; multi-level transforms (C02, C05, C06, C07) inherit C01's per-level synchronisation unless levels are fused. ESTIMATED.

## C04 — DCT

### Decode Stages

1. packet parse
2. unpack + dequant (or Huffman if JPEG-style)
3. block IDCT rows then columns in registers
4. output
5. colour convert

### Estimated Computational Structure

Arithmetic complexity: mult/pixel see note, add/pixel see note (DERIVED (direct: 2n mult+2n add per pixel; fast 8x8 AAN ~1.25 mult + 7 add))
Memory reads: coefficients once per level-pass; full-res-equivalent passes 1.0 (compute) / 1.0 (fragment)
Memory writes: same pass count; temporaries: one block in registers
Full-frame passes: 1.0
Temporary buffers: one block in registers
Branching: UNKNOWN
Serial dependencies: none across blocks
Parallelism: block-local
Synchronization points: 1

### Likely Primary Bottleneck

UNKNOWN on the headset (no measurement). From structure: block transforms (C03, C04, C08) are GPU DISPATCH / MEMORY BANDWIDTH bound with one pass; multi-level transforms (C02, C05, C06, C07) inherit C01's per-level synchronisation unless levels are fused. ESTIMATED.

## C05 — CDF 5/3 wavelet

### Decode Stages

1. packet parse
2. unpack + dequant
3. inverse 5/3: 2 lifting steps (shift+add) per level, same pass structure as C01
4. output
5. colour convert

### Estimated Computational Structure

Arithmetic complexity: mult/pixel 0.0, add/pixel 10.64 (DERIVED (shifts instead of multiplies))
Memory reads: coefficients once per level-pass; full-res-equivalent passes 2.0 (compute) / 4.0 (fragment)
Memory writes: same pass count; temporaries: as C01
Full-frame passes: 2.0
Temporary buffers: as C01
Branching: UNKNOWN
Serial dependencies: 5 sequential levels
Parallelism: as C01
Synchronization points: as C01

### Likely Primary Bottleneck

UNKNOWN on the headset (no measurement). From structure: block transforms (C03, C04, C08) are GPU DISPATCH / MEMORY BANDWIDTH bound with one pass; multi-level transforms (C02, C05, C06, C07) inherit C01's per-level synchronisation unless levels are fused. ESTIMATED.

## C06 — Daubechies db2/db4

### Decode Stages

1. packet parse
2. unpack + dequant
3. 8-tap synthesis filter bank per level (wider apron)
4. output
5. colour convert

### Estimated Computational Structure

Arithmetic complexity: mult/pixel 21.28, add/pixel 18.62 (DERIVED (db4))
Memory reads: coefficients once per level-pass; full-res-equivalent passes 2.0 (compute) / 4.0 (fragment)
Memory writes: same pass count; temporaries: as C01
Full-frame passes: 2.0
Temporary buffers: as C01
Branching: UNKNOWN
Serial dependencies: 5 levels
Parallelism: as C01 (8-tap support widens aprons)
Synchronization points: as C01

### Likely Primary Bottleneck

UNKNOWN on the headset (no measurement). From structure: block transforms (C03, C04, C08) are GPU DISPATCH / MEMORY BANDWIDTH bound with one pass; multi-level transforms (C02, C05, C06, C07) inherit C01's per-level synchronisation unless levels are fused. ESTIMATED.

## C07 — Laplacian pyramid

### Decode Stages

1. packet parse
2. unpack + dequant of residual pyramid
3. upsample + 5-tap blur + add residual, 5 levels (each level reads base and residual)
4. output
5. colour convert

### Estimated Computational Structure

Arithmetic complexity: mult/pixel 13.3, add/pixel 11.97 (DERIVED)
Memory reads: coefficients once per level-pass; full-res-equivalent passes 2.66 (compute) / None (fragment)
Memory writes: same pass count; temporaries: 1.33x coefficients
Full-frame passes: 2.66
Temporary buffers: 1.33x coefficients
Branching: UNKNOWN
Serial dependencies: 5 levels
Parallelism: per level
Synchronization points: 1/level

### Likely Primary Bottleneck

UNKNOWN on the headset (no measurement). From structure: block transforms (C03, C04, C08) are GPU DISPATCH / MEMORY BANDWIDTH bound with one pass; multi-level transforms (C02, C05, C06, C07) inherit C01's per-level synchronisation unless levels are fused. ESTIMATED.

## C08 — Fixed PCA / KLT

### Decode Stages

1. packet parse
2. unpack
3. n x n matrix multiply per block
4. output
5. colour convert

### Estimated Computational Structure

Arithmetic complexity: mult/pixel see note, add/pixel see note (ESTIMATED (n^2 mult per block sample for an n x n basis; not run spatially))
Memory reads: coefficients once per level-pass; full-res-equivalent passes 1.0 (compute) / None (fragment)
Memory writes: same pass count; temporaries: basis matrix
Full-frame passes: 1.0
Temporary buffers: basis matrix
Branching: UNKNOWN
Serial dependencies: none
Parallelism: block-local
Synchronization points: 1

### Likely Primary Bottleneck

UNKNOWN on the headset (no measurement). From structure: block transforms (C03, C04, C08) are GPU DISPATCH / MEMORY BANDWIDTH bound with one pass; multi-level transforms (C02, C05, C06, C07) inherit C01's per-level synchronisation unless levels are fused. ESTIMATED.

## C09 — VC-2 / Dirac Pro

### Decode Stages

1. slice parse (serial exp-Golomb)
2. dequant
3. inverse DWT
4. output
5. colour

### Estimated Computational Structure

Arithmetic complexity: mult/pixel see note, add/pixel see note (UNKNOWN)
Memory reads: coefficients once per level-pass; full-res-equivalent passes None (compute) / None (fragment)
Memory writes: same pass count; temporaries: UNKNOWN
Full-frame passes: UNKNOWN
Temporary buffers: UNKNOWN
Branching: UNKNOWN
Serial dependencies: Golomb parse serial per slice
Parallelism: per slice
Synchronization points: UNKNOWN

### Likely Primary Bottleneck

UNKNOWN on the headset (no measurement). From structure: block transforms (C03, C04, C08) are GPU DISPATCH / MEMORY BANDWIDTH bound with one pass; multi-level transforms (C02, C05, C06, C07) inherit C01's per-level synchronisation unless levels are fused. ESTIMATED.

## C10 — Hardware H.264 / HEVC

### Decode Stages

1. bitstream -> MediaCodec
2. CABAC (fixed function)
3. IDCT + prediction (fixed function)
4. surface out
5. sampler

### Estimated Computational Structure

Arithmetic complexity: mult/pixel see note, add/pixel see note (NOT APPLICABLE (hardware))
Memory reads: coefficients once per level-pass; full-res-equivalent passes None (compute) / None (fragment)
Memory writes: same pass count; temporaries: fixed-function
Full-frame passes: UNKNOWN
Temporary buffers: fixed-function
Branching: UNKNOWN
Serial dependencies: CABAC serial
Parallelism: fixed-function
Synchronization points: MediaCodec queue

### Likely Primary Bottleneck

SERIAL DEPENDENCY (CABAC) in fixed function; MEASURED 14.8-82 ms at 3328x1472 (E103).

---

# 8. CPU + GPU PARALLELISM

| Candidate | CPU Work | GPU Work | Same-Frame Overlap Possible? | Independent Tiles/Bands? | Expected Benefit | Synchronization Risk | Evidence |
|---|---|---|---|---|---|---|---|
| C01 | packet parse, offset table | unpack, dequant, iDWT | YES: parse of later packets overlaps decode of earlier bands only if dispatch is per band as packets land (not today: decode starts after decode_is_ready) | YES: 32x32 blocks and bands | parse is ~1 ms scale (UNKNOWN exactly); main win is starting dequant before the last packet | upload barrier per partial dispatch | source (pyrowave_decoder.cpp), MEASURED counters |
| C02 | parse | lifting | YES as C01 | YES | as C01 | as C01 | ESTIMATED |
| C03 | parse | butterflies | YES: blocks independent; decode can start per received block | YES (blocks) | highest: single pass, block-granular | none beyond upload | ESTIMATED |
| C04 | parse (Huffman if JPEG) | IDCT | YES as C03 | YES (blocks) | as C03 | Huffman is serial per restart interval if used | E004 structure |
| C05 | parse | lifting | as C01 | YES | as C01 | as C01 | ESTIMATED |
| C06 | parse | filter bank | as C01 | YES | as C01 | as C01 | ESTIMATED |
| C07 | parse | upsample/add | levels serial | per level | low | per level | ESTIMATED |
| C08 | parse | matrix mult | as C03 | YES | as C03 | none | ESTIMATED |
| C09 | Golomb parse (serial per slice) | iDWT | UNKNOWN | slices | UNKNOWN | UNKNOWN | E009 |
| C10 | none | fixed function | NO (MediaCodec queue) | NO | none | queue | MEASURED |

MULTI-FRAME PIPELINING (decoding frame N+1 while presenting N) is what ALVR's receiver already does and it does not reduce single-frame latency; only SAME-FRAME PARALLELISM does. No benchmark in section 3 demonstrates same-frame CPU/GPU overlap reducing latency.

---

# 9. MEMORY TRAFFIC ANALYSIS

Stereo luma frame N = 13,107,200 pixels (2 x 2560^2). Chroma 4:4:4 triples every figure below; values are per frame in MB, DERIVED from the pass models in section 7 with FP16 coefficients (2 B) and 8-bit output (1 B). Payload read = actual bytes (MEASURED per cell).

| Candidate | Input bytes read | Coefficient bytes written+read | Temporary written+reread | Output bytes written | Full-res passes | Total MB/frame (luma) | MB/s @ 90 Hz | Class |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| C01 | 0.42 (MEASURED cap) | 52.4 | 17.3 | 13.1 | 2 (compute) / 4 (fragment) | 83.3 | 7493 | DERIVED |
| C02 | 0.42 (MEASURED cap) | 52.4 | 0.0 | 13.1 | 1 (fused) | 66.0 | 5936 | DERIVED |
| C03 | 0.42 (MEASURED cap) | 52.4 | 0.0 | 13.1 | 1 | 66.0 | 5936 | DERIVED |
| C04 | 0.42 (MEASURED cap) | 52.4 | 0.0 | 13.1 | 1 | 66.0 | 5936 | DERIVED |
| C05 | 0.42 (MEASURED cap) | 52.4 | 17.3 | 13.1 | 2 / 4 | 83.3 | 7493 | DERIVED |
| C06 | 0.42 (MEASURED cap) | 52.4 | 17.3 | 13.1 | 2 / 4 | 83.3 | 7493 | DERIVED |
| C07 | 0.42 (MEASURED cap) | 69.7 | 17.3 | 13.1 | 2.7 | 100.6 | 9050 | DERIVED |
| C08 | 0.42 (MEASURED cap) | 52.4 | 0.0 | 13.1 | 1 | 66.0 | 5936 | DERIVED |
| C09 | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN |
| C10 | 0.42 | NOT APPLICABLE | UNKNOWN (fixed function) | 13.1 | NOT APPLICABLE | UNKNOWN | UNKNOWN | UNKNOWN |

The fragment path (what the Adreno runs today) roughly doubles C01/C05/C06's temporary traffic (DERIVED from `idwt_fragment`: `vert[2][2]` targets of w_l x 2h_l per level). No candidate's memory traffic has been MEASURED.

---

# 10. MATHEMATICAL COMPLEXITY

| Candidate | Forward Transform Complexity | Inverse Transform Complexity | Primary Operations | Multiplications | Adds/Subtracts | Memory Passes | Serial Components |
|---|---|---|---|---|---|---|---|
| C01 | O(N) lifting, 5 levels | O(N) | 4 lifting steps + scale per dim per level | ~8/pixel | ~11/pixel | 2 (compute) / 4 (fragment) | 5 levels |
| C02 | O(N) | O(N) | add, sub | 0 (scale folded) | ~2.7/pixel | 1 if levels fused | levels (fusable) |
| C03 | O(N log n) per block | O(N log n) | add, sub butterflies | 0 (+1 scale) | 2 log2 n /pixel: 6, 8, 10, 12 | 1 | none |
| C04 | O(N n) direct / O(N log n) fast | same | mult-add | direct 2n/pixel; AAN 8x8 ~1.25 | direct 2n; AAN ~7 | 1 | none |
| C05 | O(N) | O(N) | shift, add | 0 | ~11/pixel | 2 / 4 | 5 levels |
| C06 | O(N) | O(N) | 8-tap FIR | ~21/pixel (db4) | ~19/pixel | 2 / 4 | 5 levels |
| C07 | O(N) | O(N) | 5-tap separable blur + add | ~13/pixel | ~12/pixel | ~2.7 | 5 levels |
| C08 | O(N n) per n x n basis | same | matrix-vector | n/pixel per dim | n/pixel per dim | 1 | none |
| C09 | O(N) lifting | O(N) | lifting + Golomb | UNKNOWN | UNKNOWN | UNKNOWN | Golomb per slice |
| C10 | NOT APPLICABLE | NOT APPLICABLE | fixed function | NOT APPLICABLE | NOT APPLICABLE | NOT APPLICABLE | CABAC |

All counts DERIVED from the transform definitions (section 7 models); none MEASURED on hardware.

---

# 11. ASYMMETRIC ENCODER OPPORTUNITY

## C01

Can additional RTX 3090 encoder computation reduce headset decode work? YES

What moves: per-block quantiser selection already runs on the encoder (rate control); further: encoder-side pre-filtering (section 13 lab: preconditioning) and choosing per-band steps by measured marginal value (bandvalue.csv) move nothing to the decoder

Additional encoder work: rate-control search over more candidates
Removed decoder work: none (decoder unchanged)
Additional transmitted data: none
Decoder arithmetic saved: 0
Decoder memory traffic saved: 0

## C02

Can additional RTX 3090 encoder computation reduce headset decode work? YES

What moves: encoder can pick per-block Haar depth so the decoder fuses all levels in one tile

Additional encoder work: depth search
Removed decoder work: level passes
Additional transmitted data: 1 byte/block
Decoder arithmetic saved: UNKNOWN
Decoder memory traffic saved: up to (passes-1) x coefficient traffic

## C03

Can additional RTX 3090 encoder computation reduce headset decode work? YES

What moves: encoder chooses block size per region; decoder always single pass

Additional encoder work: block-size search
Removed decoder work: none
Additional transmitted data: 1 byte/block
Decoder arithmetic saved: 0
Decoder memory traffic saved: 0

## C04

Can additional RTX 3090 encoder computation reduce headset decode work? YES

What moves: as C03; encoder can also pre-compensate quantisation (trellis) so the decoder does plain IDCT

Additional encoder work: trellis search
Removed decoder work: none
Additional transmitted data: none
Decoder arithmetic saved: 0
Decoder memory traffic saved: 0

## C05

Can additional RTX 3090 encoder computation reduce headset decode work? YES

What moves: as C01

Additional encoder work: as C01
Removed decoder work: none
Additional transmitted data: none
Decoder arithmetic saved: 0
Decoder memory traffic saved: 0

## C06

Can additional RTX 3090 encoder computation reduce headset decode work? UNKNOWN

What moves: NOT APPLICABLE

Additional encoder work: UNKNOWN
Removed decoder work: UNKNOWN
Additional transmitted data: UNKNOWN
Decoder arithmetic saved: UNKNOWN
Decoder memory traffic saved: UNKNOWN

## C07

Can additional RTX 3090 encoder computation reduce headset decode work? YES

What moves: encoder can choose the downsample filter so the decoder's upsample is a fixed cheap kernel

Additional encoder work: filter search
Removed decoder work: none
Additional transmitted data: none
Decoder arithmetic saved: 0
Decoder memory traffic saved: 0

## C08

Can additional RTX 3090 encoder computation reduce headset decode work? YES

What moves: basis learned offline on the encoder side; decoder applies a fixed matrix

Additional encoder work: eigen-analysis (offline)
Removed decoder work: none
Additional transmitted data: basis once
Decoder arithmetic saved: 0
Decoder memory traffic saved: 0

## C09

Can additional RTX 3090 encoder computation reduce headset decode work? UNKNOWN

What moves: UNKNOWN

Additional encoder work: UNKNOWN
Removed decoder work: UNKNOWN
Additional transmitted data: UNKNOWN
Decoder arithmetic saved: UNKNOWN
Decoder memory traffic saved: UNKNOWN

## C10

Can additional RTX 3090 encoder computation reduce headset decode work? NO

What moves: NOT APPLICABLE (fixed-function decoder)

Additional encoder work: NOT APPLICABLE
Removed decoder work: NOT APPLICABLE
Additional transmitted data: NOT APPLICABLE
Decoder arithmetic saved: NOT APPLICABLE
Decoder memory traffic saved: NOT APPLICABLE

---

# 12. COMMON-DATASET TESTABILITY

| Candidate | Kodak Compatible | Synthetic Panel Compatible | Exact Byte Target Possible | Exact Reconstruction Available | Timing Instrumentation Available | Integration Difficulty |
|---|---|---|---|---|---|---|
| C01 | YES (done) | YES (done) | YES (encoder cap) | YES | YES (GPU timestamps, device) | LOW |
| C02 | YES (lab) | YES (lab) | YES (lab model) | YES | NO (no decoder) | MEDIUM: new lifting shader in PyroWave's pipeline |
| C03 | YES (lab) | YES (lab) | YES (lab model) | YES | NO | HIGH: new block-coefficient bitstream + shader; PyroWave's band/block layout does not map |
| C04 | YES (lab) | YES (lab) | YES (lab model) | YES | NO on device (GPUJPEG is CUDA) | HIGH: same as C03 plus a Vulkan IDCT port |
| C05 | YES (lab) | YES (lab) | YES (lab model) | YES | NO | LOW-MEDIUM: swap lifting constants/steps in dwt shaders; same passes |
| C06 | YES (lab) | YES (lab) | YES (lab model) | YES | NO | MEDIUM: wider apron in the tile shaders |
| C07 | YES (lab) | YES (lab) | YES (lab model) | YES | NO | HIGH: different bitstream (over-complete) |
| C08 | NO (not run) | NO | UNKNOWN | NO | NO | EXTREME: new codec |
| C09 | YES (ffmpeg CPU) | YES | NO (bitrate control, not exact) | YES | NO on device | EXTREME: FFmpeg Vulkan decoder into an Android client |
| C10 | NO (video codec) | NO | NO | NO | YES (ALVR stage) | NOT APPLICABLE (in use) |

HIGH/EXTREME reasons: C03/C04/C07 need a new coefficient container and shaders outside PyroWave's band structure; C08 has no implementation at all; C09 would mean porting FFmpeg's in-progress Vulkan decoder into a NativeActivity client.

---

# 13. PROPOSED CONTROLLED BENCHMARK

Datasets: A Kodak (kodim04/07/08/19 tiled to 2560^2, done), B synthetic panel (done), C real lossless game imagery (NOT AVAILABLE: no VR game installed; only a SteamVR-void dump exists). Resolutions 1920..2560 (lab ran 1920 and 2560; the matrix ran all six for C01). Byte budgets 150 KB..550 KB + 416,667 B (all run in the lab under the bit-plane model; exact for C01 via its encoder). Metrics: PSNR-Y (numpy), SSIM-Y (numpy), PSNR-HVS (libvmaf, reference cap only), PSNR-HVS-M NOT AVAILABLE. Performance metrics: decode mean/p95/p99 exist for C01 and C10 on device only; CPU/GPU split, memory traffic, temporary memory and synchronization time UNKNOWN for every candidate except C01's GPU timestamps (decode, convert, submit->fence).

**Scope of the lab caps:** every lab cell applies its byte cap to ONE eye's LUMA plane (2560x2560 = 6.55 Msamples), not to the pipeline's stereo 4:4:4 frame (5120x2560x3 = 39.3 Msamples). The caps therefore mean ~6x more bits per sample than the same numbers in the offline PyroWave matrix (E104), which is why lab PSNR-Y values sit ~7 dB above the matrix's for the same frame. Comparisons BETWEEN transforms in the lab are at equal bytes and equal samples and are fair; lab absolutes are not comparable to the matrix or to the headset.

What this benchmark does NOT do: it does not run any candidate's decoder on the headset except C01; sizes for C02-C07 come from a packing model of PyroWave's bitstream applied to their coefficients, so a real entropy coder (or PyroWave's exact 4x2 layout) would move every size.

---

# 14. DECODER-FIRST RESULTS TABLE

Lab rows: cap applied to one eye's luma plane (see section 13); 'Mbps @ 90 Hz' for lab rows is the cap x 8 x 90 and would be ~6x higher for a stereo 4:4:4 frame at the same bits per sample.

| Candidate | Resolution | Bytes/Frame | Mbps @ 90 Hz | Quality | Decode Mean | Decode P95 | Decode P99 | CPU Time | GPU Time | Memory Traffic | Evidence Class |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| C01 (device) | 1984x896 SBS (60 %) | ~556 KB (400 Mbps) | 400 | subjective good | fence 6.4 ms | 8.3 | 8.9 | UNKNOWN | 3.7 mean / 5.2 p99 | UNKNOWN | MEASURED (E101) |
| C01 (device) | 3328x1472 SBS 4:4:4 | 1,041,660 | 750 (at 90) | PSNR-Y 55.16 vs PC | 8.19 ms (GPU) | UNKNOWN | UNKNOWN | UNKNOWN | 6.23 best | UNKNOWN | MEASURED (E102) |
| C10 H.264 (device) | 3328x1472 | UNKNOWN | 600 @ 72 | subjective | 14.8-18.9 ms stage | UNKNOWN | UNKNOWN | NOT APPLICABLE | fixed fn | UNKNOWN | MEASURED (E103) |
| C10 HEVC (device) | 3328x1472 | UNKNOWN | 600 @ 72 | subjective | 82.0 ms stage | UNKNOWN | UNKNOWN | NOT APPLICABLE | fixed fn | UNKNOWN | MEASURED (E103) |
| C01 cdf97 (lab) | 2560x2560 per eye | 416,667 (model) | 300 | PSNR-Y 28.94 Kodak mean / 73.67 panel | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | DERIVED model (sec. 9) | MEASURED quality, no timing |
| C02 haar (lab) | 2560x2560 per eye | 416,667 (model) | 300 | PSNR-Y 27.09 Kodak mean / 79.30 panel | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | DERIVED model (sec. 9) | MEASURED quality, no timing |
| C03 wht8 (lab) | 2560x2560 per eye | 416,667 (model) | 300 | PSNR-Y 26.66 Kodak mean / 65.31 panel | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | DERIVED model (sec. 9) | MEASURED quality, no timing |
| C03 wht16 (lab) | 2560x2560 per eye | 416,667 (model) | 300 | PSNR-Y 26.51 Kodak mean / 64.31 panel | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | DERIVED model (sec. 9) | MEASURED quality, no timing |
| C03 wht32 (lab) | 2560x2560 per eye | 416,667 (model) | 300 | PSNR-Y 25.93 Kodak mean / 50.09 panel | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | DERIVED model (sec. 9) | MEASURED quality, no timing |
| C03 wht64 (lab) | 2560x2560 per eye | 416,667 (model) | 300 | PSNR-Y 25.74 Kodak mean / 41.76 panel | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | DERIVED model (sec. 9) | MEASURED quality, no timing |
| C04 dct8 (lab) | 2560x2560 per eye | 416,667 (model) | 300 | PSNR-Y 27.64 Kodak mean / 64.46 panel | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | DERIVED model (sec. 9) | MEASURED quality, no timing |
| C04 dct16 (lab) | 2560x2560 per eye | 416,667 (model) | 300 | PSNR-Y 27.97 Kodak mean / 64.84 panel | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | DERIVED model (sec. 9) | MEASURED quality, no timing |
| C04 dct32 (lab) | 2560x2560 per eye | 416,667 (model) | 300 | PSNR-Y 27.55 Kodak mean / 54.76 panel | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | DERIVED model (sec. 9) | MEASURED quality, no timing |
| C05 cdf53 (lab) | 2560x2560 per eye | 416,667 (model) | 300 | PSNR-Y 28.68 Kodak mean / 73.72 panel | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | DERIVED model (sec. 9) | MEASURED quality, no timing |
| C06 db2 (lab) | 2560x2560 per eye | 416,667 (model) | 300 | PSNR-Y 28.14 Kodak mean / 72.03 panel | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | DERIVED model (sec. 9) | MEASURED quality, no timing |
| C06 db4 (lab) | 2560x2560 per eye | 416,667 (model) | 300 | PSNR-Y 28.59 Kodak mean / 72.64 panel | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | DERIVED model (sec. 9) | MEASURED quality, no timing |
| C07 lap (lab) | 2560x2560 per eye | 416,667 (model) | 300 | PSNR-Y 24.35 Kodak mean / 52.37 panel | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | UNKNOWN | DERIVED model (sec. 9) | MEASURED quality, no timing |

---

# 15. QUALITY-TARGET TABLE

Thresholds are defined on the lab's PSNR-Y (numpy, BT.709 luma, 2560^2 per eye) for the tiled kodim08 frame, the hardest source: A High = 24 dB, B Very High = 26 dB, C Near-Transparent = 30 dB. These are chosen from the measured range of this frame (its scores run ~18-27 dB across the caps), not from any candidate's convenience; they do not transfer to game content.

## Target A — High Quality (PSNR-Y >= 24.0 dB on kodak_kodim08)

| Candidate | Required Bytes/Frame | Mbps @ 90 Hz | Decode Time | Memory Traffic |
|---|---:|---:|---:|---:|
| C01 cdf97 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C02 haar | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C03 wht8 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C03 wht16 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C03 wht32 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C03 wht64 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C04 dct8 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C04 dct16 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C04 dct32 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C05 cdf53 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C06 db2 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C06 db4 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C07 lap | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |

## Target B — Very High Quality (PSNR-Y >= 26.0 dB on kodak_kodim08)

| Candidate | Required Bytes/Frame | Mbps @ 90 Hz | Decode Time | Memory Traffic |
|---|---:|---:|---:|---:|
| C01 cdf97 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C02 haar | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C03 wht8 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C03 wht16 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C03 wht32 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C03 wht64 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C04 dct8 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C04 dct16 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C04 dct32 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C05 cdf53 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C06 db2 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C06 db4 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C07 lap | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |

## Target C — Near-Transparent (PSNR-Y >= 30.0 dB on kodak_kodim08)

| Candidate | Required Bytes/Frame | Mbps @ 90 Hz | Decode Time | Memory Traffic |
|---|---:|---:|---:|---:|
| C01 cdf97 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C02 haar | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C03 wht8 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C03 wht16 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C03 wht32 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C03 wht64 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C04 dct8 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C04 dct16 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C04 dct32 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C05 cdf53 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C06 db2 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C06 db4 | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |
| C07 lap | not reached within 550 KB | > 396 | UNKNOWN | DERIVED sec. 9 |

---

# 16. PARETO FRONTIER

Axes: quality (lab PSNR-Y Kodak mean at 416,667 B, MEASURED), bytes (equal by construction), decode arithmetic (ops/pixel, DERIVED), memory passes (fragment-path count, DERIVED). Decode latency and memory traffic are model values, so nothing here is a confirmed Pareto point except C01's device measurement against C10.

### Confirmed Pareto Points

- C01 PyroWave on device dominates C10 H.264/HEVC on decode latency at equal or higher bitrate (MEASURED: 3.7-8 ms vs 14.8-82 ms, E101/E103); quality comparability with C10 is NO, so this is confirmed on latency only.

### Potential Pareto Points (lab quality x model cost)

- C01 cdf97: PSNR-Y 28.94 dB, 19.0 ops/pixel, 4.0 passes
- C05 cdf53: PSNR-Y 28.68 dB, 10.6 ops/pixel, 4.0 passes
- C04 dct16: PSNR-Y 27.97 dB, 64.0 ops/pixel, 1.0 passes
- C04 dct8: PSNR-Y 27.64 dB, 32.0 ops/pixel, 1.0 passes
- C02 haar: PSNR-Y 27.09 dB, 2.7 ops/pixel, 2.0 passes
- C03 wht8: PSNR-Y 26.66 dB, 6.0 ops/pixel, 1.0 passes

### Dominated Configurations

- C03 wht16 (26.51 dB, 8.0 ops, 1.0 passes) dominated by C03 wht8 (26.66 dB, 6.0 ops, 1.0 passes)
- C03 wht32 (25.93 dB, 10.0 ops, 1.0 passes) dominated by C03 wht8 (26.66 dB, 6.0 ops, 1.0 passes)
- C03 wht64 (25.74 dB, 12.0 ops, 1.0 passes) dominated by C03 wht8 (26.66 dB, 6.0 ops, 1.0 passes)
- C04 dct32 (27.55 dB, 128.0 ops, 1.0 passes) dominated by C04 dct8 (27.64 dB, 32.0 ops, 1.0 passes)
- C06 db2 (28.14 dB, 39.9 ops, 4.0 passes) dominated by C01 cdf97 (28.94 dB, 19.0 ops, 4.0 passes)
- C06 db4 (28.59 dB, 39.9 ops, 4.0 passes) dominated by C01 cdf97 (28.94 dB, 19.0 ops, 4.0 passes)
- C07 lap (24.35 dB, 25.3 ops, 2.66 passes) dominated by C02 haar (27.09 dB, 2.7 ops, 2.0 passes)

---

# 17. HEADSET-SIDE FEASIBILITY

## C01

Required compute API: Vulkan 1.3 compute or graphics
Expected mobile GPU compatibility: runs (MEASURED)
CPU requirements: packet parse thread
GPU requirements: Adreno 740: fragment path forced; storage-on-AHB RGBA8
Memory requirements: coefficient images + output
Expected thermal implications: 78 C plateau at 60 %/400 Mbps/90 Hz; status 3 at full panel
Implementation obstacles: fence 6-18 ms; no external semaphores (SYNC_FD only)
Known Android/XR constraints: AHB R8 unsupported -> convert pass
Evidence: E101, E102, memory notes
Confidence: HIGH

## C02

Required compute API: Vulkan compute
Expected mobile GPU compatibility: expected (subset of C01's ops)
CPU requirements: as C01
GPU requirements: as C01
Memory requirements: as C01 or less
Expected thermal implications: likely lower than C01 (fewer ops, passes) -- ESTIMATED
Implementation obstacles: must be written; no implementation
Known Android/XR constraints: as C01
Evidence: none
Confidence: LOW

## C03

Required compute API: Vulkan compute
Expected mobile GPU compatibility: expected
CPU requirements: parse
GPU requirements: one block per workgroup
Memory requirements: block in shared memory
Expected thermal implications: single pass -- ESTIMATED lower
Implementation obstacles: new bitstream + shader
Known Android/XR constraints: as C01
Evidence: none
Confidence: LOW

## C04

Required compute API: Vulkan compute
Expected mobile GPU compatibility: expected
CPU requirements: parse
GPU requirements: as C03
Memory requirements: as C03
Expected thermal implications: as C03
Implementation obstacles: port of a CUDA IDCT; no Vulkan reference
Known Android/XR constraints: as C01
Evidence: E004 (desktop only)
Confidence: LOW

## C05

Required compute API: Vulkan
Expected mobile GPU compatibility: expected
CPU requirements: as C01
GPU requirements: as C01
Memory requirements: as C01
Expected thermal implications: slightly lower than C01 -- ESTIMATED
Implementation obstacles: constant/step change in dwt shaders
Known Android/XR constraints: as C01
Evidence: none
Confidence: MEDIUM

## C06

Required compute API: Vulkan
Expected mobile GPU compatibility: expected
CPU requirements: as C01
GPU requirements: wider aprons
Memory requirements: as C01
Expected thermal implications: higher than C01 -- ESTIMATED
Implementation obstacles: shader rewrite
Known Android/XR constraints: as C01
Evidence: none
Confidence: LOW

## C07

Required compute API: Vulkan
Expected mobile GPU compatibility: expected
CPU requirements: as C01
GPU requirements: per level
Memory requirements: 1.33x
Expected thermal implications: UNKNOWN
Implementation obstacles: new bitstream
Known Android/XR constraints: as C01
Evidence: none
Confidence: LOW

## C08

Required compute API: Vulkan
Expected mobile GPU compatibility: UNKNOWN
CPU requirements: UNKNOWN
GPU requirements: matrix per block
Memory requirements: basis
Expected thermal implications: UNKNOWN
Implementation obstacles: everything
Known Android/XR constraints: UNKNOWN
Evidence: none
Confidence: LOW

## C09

Required compute API: Vulkan compute (FFmpeg)
Expected mobile GPU compatibility: UNKNOWN
CPU requirements: Golomb parse
GPU requirements: UNKNOWN
Memory requirements: UNKNOWN
Expected thermal implications: UNKNOWN
Implementation obstacles: porting FFmpeg's decoder into the client
Known Android/XR constraints: UNKNOWN
Evidence: E009
Confidence: LOW

## C10

Required compute API: MediaCodec
Expected mobile GPU compatibility: runs (MEASURED)
CPU requirements: none
GPU requirements: video block
Memory requirements: surfaces
Expected thermal implications: coolest block (video zone 6 C below CPU)
Implementation obstacles: HEVC 8x slower than AVC
Known Android/XR constraints: MediaCodec latency
Evidence: E103
Confidence: HIGH

No desktop CUDA/Vulkan timing has been extrapolated to the headset anywhere in this report.

---

# 18. SHORTLIST FOR IMPLEMENTATION

## Candidate C05

Reason to test: cheapest change to a working decoder: same passes, shift-only lifting
Specific hypothesis: 5/3 loses <= 0.25 dB (lab) and removes every multiply from the iDWT; on the Adreno this changes decode time by an amount that is UNKNOWN because the fence, not arithmetic, dominates
Open-source implementation: pyrowave (MIT) shaders dwt_common.h / idwt.*
Required integration work: new lifting constants and 2-step path in the dwt shaders; encoder side same
Expected decoder advantage: arithmetic only (ESTIMATED small)
Main risk: fence-bound decoder shows no change
Experiment needed to falsify hypothesis: A/B on device: GPU decode + fence with 9/7 vs 5/3 at the same cap

## Candidate C02

Reason to test: fewest passes of any multi-level transform if levels are fused in-tile
Specific hypothesis: Haar costs 1.85 dB (lab) at equal bytes; if a fused single-pass inverse cuts the fragment path's 20 render passes to a handful, the fence should fall
Open-source implementation: none; write inside PyroWave's decoder
Required integration work: new fused iDWT shader; encoder DWT trivial
Expected decoder advantage: passes and synchronisation
Main risk: quality loss visible in text/UI (synthetic panel result)
Experiment needed to falsify hypothesis: device timing of a fused Haar iDWT vs current 9/7 fragment path

## Candidate C04

Reason to test: single-pass block decoder with the best block-transform quality in the lab
Specific hypothesis: a 16x16 or 32x32 IDCT in registers reconstructs in one dispatch; the quality gap to 9/7 is measured in section 14
Open-source implementation: GPUJPEG IDCT (BSD-2) as reference for a Vulkan port
Required integration work: new coefficient container + IDCT shader
Expected decoder advantage: passes (1) and no inter-level sync
Main risk: block artefacts at low bpp; container work
Experiment needed to falsify hypothesis: device timing of an IDCT-only decoder on the same frames

## Candidate C03

Reason to test: add/sub only, single pass
Specific hypothesis: WHT trades bytes for zero multiplies; the lab shows how many bytes
Open-source implementation: none
Required integration work: as C04 with butterflies
Expected decoder advantage: arithmetic and passes
Main risk: quality gap at equal bytes
Experiment needed to falsify hypothesis: same as C04

## Candidate C01

Reason to test: not a new codec: force PyroWave's compute path on the Adreno
Specific hypothesis: the driver check forces the fragment path (3 passes/level) 'for tiled mobile GPUs'; our fence data suggests passes, not ALU, cost the time; the compute path halves passes
Open-source implementation: pyrowave `device_prefers_fragment_path`
Required integration work: a build/runtime flag (PYROWAVE_FORCE_COMPUTE exists in pyrowave_android)
Expected decoder advantage: half the intermediate traffic
Main risk: compute path may be slower or broken on Adreno (the author says not recommended)
Experiment needed to falsify hypothesis: device A/B of the two paths at the same cap

---

# 19. REJECTED / DEFERRED APPROACHES

| Candidate | Status | Reason | Evidence |
|---|---|---|---|
| C06 Daubechies | DEFERRED | quality 28.59 dB (db4) is within noise of 9/7 at ~2x the multiplies and wider aprons; no decoder-side gain | lab rd.csv, sec. 10 |
| C07 Laplacian | DEFERRED | over-complete: 4/3 coefficients and more traffic for 24.35 dB | lab rd.csv, sec. 9 |
| C08 fixed PCA/KLT (spatial) | INSUFFICIENT EVIDENCE | not run; colour PCA measured only (section 20) | colour.csv |
| C09 VC-2 | DEFERRED | no timings, Vulkan decoder status unclear, EXTREME integration | E009 |
| C10 HEVC | REJECTED | 82 ms decode on device | E103 |
| DSP/HVX offload | REJECTED | cDSP not reachable from an app (no node; aDSP DAC-blocked) | tools/dspprobe/README.md |

---

# 20. NEW MATHEMATICAL DIRECTIONS

Name: Colour decorrelation by offline PCA basis (measured)
Mathematical basis: eigenvectors of the RGB covariance per source
Why potentially relevant: entropy after transform vs YCbCr/YCoCg (colour.csv)
Expected decoder complexity: 9 mult + 6 add per pixel (3x3 matrix)
Expected bandwidth behavior: see section 21 table
Existing implementation: none needed
Evidence: MEASURED (colour.csv)
Recommended action: adopt only if H_sum falls below YCoCg's and the 3x3 cost is accepted

Name: Encoder-side preconditioning (measured)
Mathematical basis: I' = F_theta(I), C = T(I'), decoder unchanged
Why potentially relevant: fewer coefficient bits at the same quantiser, zero decoder cost
Expected decoder complexity: 0 extra
Expected bandwidth behavior: precond.csv (bytes at fixed delta) -- HYPOTHESIZED to save 5-20 %
Existing implementation: cv2 filters
Evidence: MEASURED (precond.csv)
Recommended action: see section 21 values

Name: Multi-level fusion of short wavelets in one tile (HYPOTHESIZED)
Mathematical basis: 2-tap (Haar) or 4-tap (5/3) support lets several inverse levels complete inside a 32x32 tile with aprons
Why potentially relevant: passes and barriers, which the device data says are the cost
Expected decoder complexity: same arithmetic, 1 pass
Expected bandwidth behavior: intermediate traffic -> 0
Existing implementation: none
Evidence: HYPOTHESIZED
Recommended action: prototype inside PyroWave's decoder

---

# 21. CRITICAL UNKNOWNS

Supporting MEASURED tables from the lab (sections 10-14 of the research prompt):

Sparsity P(eps) and PSNR after thresholding, kodak_kodim08 (MEASURED):

| transform | eps=2: sparsity / psnr | eps=8 | eps=32 |
|---|---|---|---|
| cdf53 | 0.281 / 52.9 | 0.634 / 39.2 | 0.878 / 28.5 |
| cdf97 | 0.276 / 52.9 | 0.625 / 39.2 | 0.874 / 28.7 |
| db2 | 0.272 / 53.0 | 0.613 / 39.3 | 0.861 / 28.8 |
| db4 | 0.263 / 53.1 | 0.606 / 39.3 | 0.863 / 28.7 |
| dct16 | 0.232 / 53.5 | 0.593 / 38.9 | 0.880 / 28.5 |
| dct32 | 0.210 / 53.9 | 0.569 / 38.8 | 0.880 / 28.2 |
| dct8 | 0.257 / 53.2 | 0.614 / 39.0 | 0.875 / 28.9 |
| haar | 0.257 / 53.4 | 0.604 / 39.1 | 0.859 / 28.8 |
| lap | 0.164 / 53.6 | 0.452 / 37.3 | 0.845 / 23.0 |
| wht16 | 0.182 / 54.5 | 0.517 / 39.0 | 0.860 / 27.7 |
| wht32 | 0.155 / 55.2 | 0.478 / 39.0 | 0.852 / 27.3 |
| wht64 | 0.130 / 55.9 | 0.437 / 39.1 | 0.839 / 26.9 |
| wht8 | 0.214 / 53.8 | 0.563 / 39.0 | 0.862 / 28.3 |

Precision (MEASURED, PSNR of the fp16/int16 reconstruction vs the fp32 one, kodak_kodim08):

| transform | fp16 storage | fp16 math | int16 |
|---|---|---|---|
| cdf53 | 78.6 dB | 70.4 dB | 99.4 dB |
| cdf97 | 78.0 dB | 53.4 dB | 97.1 dB |
| dct16 | 81.1 dB | 81.1 dB | 90.7 dB |
| haar | 79.1 dB | 69.9 dB | 88.4 dB |
| lap | 84.2 dB | 84.2 dB | 104.2 dB |
| wht16 | 79.3 dB | 79.3 dB | 90.5 dB |

Colour (MEASURED): channel correlations and zero-order entropy sums (bins of 1.0) per space. YCbCr709's chroma is scaled by 1/1.8556 and 1/1.5748, which lowers its raw entropy by log2 of those factors (1.55 bits); the corrected column adds that back (DERIVED) so the spaces are compared at equal scale:

| source | rho_RG / rho_RB / rho_GB | RGB H_sum | YCbCr709 raw | YCbCr709 scale-corrected | YCoCg | Y,R-G,B-G | PCA_offline |
|---|---|---|---|---|---|---|---|
| kodak_kodim04 | 0.608 / 0.6889 / 0.9569 | 21.60 | 16.80 | 18.34 | 19.80 | 19.41 | 19.20 |
| kodak_kodim07 | 0.8165 / 0.7553 / 0.909 | 21.25 | 16.20 | 17.74 | 18.30 | 18.02 | 18.77 |
| kodak_kodim08 | 0.9648 / 0.9167 / 0.9756 | 22.94 | 17.62 | 19.16 | 19.24 | 19.31 | 19.20 |
| kodak_kodim19 | 0.9669 / 0.8257 / 0.9205 | 22.12 | 17.25 | 18.79 | 18.42 | 18.84 | 18.07 |
| synthetic_panel | 0.8334 / 0.8334 / 0.8333 | 4.10 | 2.32 | 3.86 | 2.23 | 2.10 | 2.38 |

Preconditioning (MEASURED, cdf97 at the delta that met 416,667 B unfiltered):

| source | prefilter | bytes at fixed delta | PSNR vs original | PSNR vs original at the cap |
|---|---|---:|---:|---:|
| kodak_kodim04 | none | 416,634 | 32.51 | 32.51 |
| kodak_kodim04 | gauss_0.5 | 309,980 | 32.01 | 33.09 |
| kodak_kodim04 | gauss_0.8 | 217,873 | 30.64 | 32.21 |
| kodak_kodim04 | bilateral_5_20 | 322,406 | 31.75 | 32.65 |
| kodak_kodim07 | none | 416,615 | 31.57 | 31.57 |
| kodak_kodim07 | gauss_0.5 | 328,571 | 30.94 | 32.20 |
| kodak_kodim07 | gauss_0.8 | 227,634 | 29.45 | 31.28 |
| kodak_kodim07 | bilateral_5_20 | 393,968 | 31.02 | 31.29 |
| kodak_kodim08 | none | 416,629 | 22.44 | 22.44 |
| kodak_kodim08 | gauss_0.5 | 289,838 | 21.81 | 23.07 |
| kodak_kodim08 | gauss_0.8 | 193,250 | 20.75 | 22.51 |
| kodak_kodim08 | bilateral_5_20 | 394,025 | 22.33 | 22.54 |
| kodak_kodim19 | none | 416,656 | 29.24 | 29.24 |
| kodak_kodim19 | gauss_0.5 | 314,850 | 28.60 | 29.69 |
| kodak_kodim19 | gauss_0.8 | 192,089 | 26.23 | 27.93 |
| kodak_kodim19 | bilateral_5_20 | 350,715 | 28.82 | 29.51 |
| synthetic_panel | none | 416,408 | 73.67 | 73.67 |
| synthetic_panel | gauss_0.5 | 384,186 | 33.71 | 33.73 |
| synthetic_panel | gauss_0.8 | 334,413 | 26.50 | 26.50 |
| synthetic_panel | bilateral_5_20 | 422,103 | 48.58 | 48.58 |

Marginal value of precision per band, cdf97 at 416,667 B (MEASURED, dPSNR per extra KB, kodak_kodim08):

| band | dPSNR/KB |
|---|---:|
| LH5 | 0.0422 |
| HL5 | 0.0275 |
| HH4 | 0.0234 |
| HL4 | 0.0224 |
| LH4 | 0.0214 |
| HH5 | 0.0161 |
| LH3 | 0.0144 |
| HL3 | 0.0138 |

1.
UNKNOWN: Decode time of any non-PyroWave transform on the Adreno 740.
WHY IT MATTERS: The entire decoder-cost side of the Pareto analysis is a model until one exists.
WHAT MEASUREMENT RESOLVES IT: GPU timestamps of a candidate inverse inside PyroWave's decoder on device.
MINIMUM EXPERIMENT REQUIRED: Swap 9/7 lifting for 5/3 in the dwt shaders, rebuild libpyrowave for Android, A/B at 416,667 B on the existing harness.

2.
UNKNOWN: Whether PyroWave's compute path beats its fragment path on the Adreno.
WHY IT MATTERS: It halves the passes the device data says cost the time.
WHAT MEASUREMENT RESOLVES IT: PYROWAVE_FORCE_COMPUTE A/B on device.
MINIMUM EXPERIMENT REQUIRED: Env/property flag in pyroclient, one worn cell each.

3.
UNKNOWN: How much of the submit->fence time is contention with the runtime compositor rather than decode.
WHY IT MATTERS: If it is contention, no transform change helps; scheduling does.
WHAT MEASUREMENT RESOLVES IT: Decode on a dedicated queue vs the runtime's, with GPU timestamps at queue submit and at completion.
MINIMUM EXPERIMENT REQUIRED: Queue-family experiment in libpyroclient.

4.
UNKNOWN: Real sizes under PyroWave's exact 4x2 packing (and under an entropy coder) for C02-C07.
WHY IT MATTERS: Every non-C01 byte figure is a model.
WHAT MEASUREMENT RESOLVES IT: Encode the lab's quantised coefficients with pyrowave's packer, or with rANS.
MINIMUM EXPERIMENT REQUIRED: Export quantised bands and feed the C encoder's packetize path.

5.
UNKNOWN: Behaviour on real game content and stereo pairs.
WHY IT MATTERS: All sources are left=right and either tiled Kodak or synthetic.
WHAT MEASUREMENT RESOLVES IT: Lossless captures from a game via the pre-encode tap.
MINIMUM EXPERIMENT REQUIRED: Install one VR title; tap 10 frames.

---

# 22. NEXT EXPERIMENTS

EXPERIMENT ID: X01
QUESTION: Does the compute path beat the fragment path on the Adreno 740?
HYPOTHESIS: passes, not ALU, set the fence; compute path halves passes
INPUT: live stream at X60-400-90
CONTROL: fragment path (today)
VARIABLE: PYROWAVE_FORCE_COMPUTE
MEASUREMENTS: GPU decode, submit->fence p50/p95/p99, fps, thermal
FALSIFICATION CONDITION: fence not lower by > 1 ms, or corruption
ESTIMATED IMPLEMENTATION EFFORT: hours (flag + rebuild)

EXPERIMENT ID: X02
QUESTION: Does 5/3 lifting reduce device decode time at equal bytes?
HYPOTHESIS: no multiplies -> lower ALU time; fence unchanged
INPUT: same
CONTROL: 9/7
VARIABLE: 5/3 shaders
MEASUREMENTS: same + PSNR-Y vs encoder input
FALSIFICATION CONDITION: GPU decode not lower by > 0.5 ms
ESTIMATED IMPLEMENTATION EFFORT: 1-2 days (shaders both ends)

EXPERIMENT ID: X03
QUESTION: Does a fused multi-level Haar iDWT cut passes enough to move the fence?
HYPOTHESIS: one pass per tile beats 20 render passes
INPUT: same
CONTROL: 9/7 fragment
VARIABLE: fused Haar decoder shader
MEASUREMENTS: same
FALSIFICATION CONDITION: fence not lower, or quality loss > lab-predicted
ESTIMATED IMPLEMENTATION EFFORT: days

EXPERIMENT ID: X04
QUESTION: How many bytes do C02-C07 really need under PyroWave's exact packer?
HYPOTHESIS: the lab model is within 10 % of the real packer
INPUT: lab quantised bands
CONTROL: C01 via its encoder
VARIABLE: transform
MEASUREMENTS: actual bytes from pyrowave packetize
FALSIFICATION CONDITION: model error > 10 %
ESTIMATED IMPLEMENTATION EFFORT: 1 day

EXPERIMENT ID: X05
QUESTION: Is decode-queue contention with the compositor a large part of the fence?
HYPOTHESIS: a dedicated queue lowers fence variance
INPUT: live stream
CONTROL: shared queue
VARIABLE: queue family
MEASUREMENTS: fence p95/p99 distribution
FALSIFICATION CONDITION: no change in p95
ESTIMATED IMPLEMENTATION EFFORT: 1 day

---

# 23. MACHINE-READABLE SUMMARY

```json
{
  "baseline": {
    "codec": "PyroWave",
    "transform": "CDF 9/7 irreversible, 5 levels, raw bit-planes",
    "resolution_per_eye": "2560x2560 (research) / 2131x2304 (live)",
    "fps": 90,
    "bitrate_mbps": 400,
    "decode_ms": 3.7,
    "quality_metric": "psnr_y_dashboard_600mbps",
    "quality_value": 57.45
  },
  "candidates": [
    {
      "id": "C01",
      "name": "PyroWave",
      "transform": "CDF 9/7 irreversible DWT, 5 levels, raw bit-planes (no entropy coder)",
      "repository": "https://github.com/Themaister/pyrowave",
      "license": "MIT",
      "decode_ms_measured": 3.7,
      "decode_ms_derived": null,
      "quality_metric": "psnr_y_lab_kodak_mean_2560_416667B",
      "quality_value": 28.94,
      "bytes_per_frame": 416667,
      "cpu_gpu_parallelism": "same-frame possible per block/band",
      "memory_traffic": null,
      "evidence_class": "MEASURED on device + PUBLISHED desktop",
      "confidence": "HIGH",
      "status": "baseline"
    },
    {
      "id": "C02",
      "name": "Haar wavelet",
      "transform": "2-tap orthonormal lifting, hierarchical",
      "repository": "UNKNOWN",
      "license": "NOT APPLICABLE",
      "decode_ms_measured": null,
      "decode_ms_derived": null,
      "quality_metric": "psnr_y_lab_kodak_mean_2560_416667B",
      "quality_value": 27.09,
      "bytes_per_frame": 416667,
      "cpu_gpu_parallelism": "same-frame possible per block/band",
      "memory_traffic": null,
      "evidence_class": "MEASURED (lab RD) + DERIVED cost",
      "confidence": "LOW",
      "status": "shortlist"
    },
    {
      "id": "C03",
      "name": "Walsh-Hadamard",
      "transform": "block WHT 8/16/32/64, add/sub butterflies",
      "repository": "https://github.com/HanGuo97/hadacore (UNVERIFIED)",
      "license": "UNKNOWN",
      "decode_ms_measured": null,
      "decode_ms_derived": null,
      "quality_metric": "psnr_y_lab_kodak_mean_2560_416667B",
      "quality_value": 26.66,
      "bytes_per_frame": 416667,
      "cpu_gpu_parallelism": "same-frame possible per block/band",
      "memory_traffic": null,
      "evidence_class": "MEASURED (lab RD) + DERIVED cost",
      "confidence": "LOW",
      "status": "shortlist"
    },
    {
      "id": "C04",
      "name": "DCT",
      "transform": "block DCT-II 8/16/32 (orthonormal)",
      "repository": "https://github.com/CESNET/GPUJPEG",
      "license": "BSD-2-Clause (GPUJPEG)",
      "decode_ms_measured": null,
      "decode_ms_derived": null,
      "quality_metric": "psnr_y_lab_kodak_mean_2560_416667B",
      "quality_value": 27.97,
      "bytes_per_frame": 416667,
      "cpu_gpu_parallelism": "same-frame possible per block/band",
      "memory_traffic": null,
      "evidence_class": "PUBLISHED desktop timings + MEASURED (lab RD)",
      "confidence": "LOW",
      "status": "shortlist"
    },
    {
      "id": "C05",
      "name": "CDF 5/3 wavelet",
      "transform": "2-step integer lifting (JPEG 2000 reversible / JPEG XS)",
      "repository": "https://www.intopix.com/fasttico-xs-sdks",
      "license": "commercial",
      "decode_ms_measured": null,
      "decode_ms_derived": null,
      "quality_metric": "psnr_y_lab_kodak_mean_2560_416667B",
      "quality_value": 28.68,
      "bytes_per_frame": 416667,
      "cpu_gpu_parallelism": "same-frame possible per block/band",
      "memory_traffic": null,
      "evidence_class": "PUBLISHED (marketing, no numbers) + MEASURED (lab RD)",
      "confidence": "MEDIUM",
      "status": "shortlist"
    },
    {
      "id": "C06",
      "name": "Daubechies db2/db4",
      "transform": "orthogonal 4-/8-tap filter banks",
      "repository": "UNKNOWN",
      "license": "NOT APPLICABLE",
      "decode_ms_measured": null,
      "decode_ms_derived": null,
      "quality_metric": "psnr_y_lab_kodak_mean_2560_416667B",
      "quality_value": 28.59,
      "bytes_per_frame": 416667,
      "cpu_gpu_parallelism": "same-frame possible per block/band",
      "memory_traffic": null,
      "evidence_class": "MEASURED (lab RD) + DERIVED cost",
      "confidence": "LOW",
      "status": "deferred"
    },
    {
      "id": "C07",
      "name": "Laplacian pyramid",
      "transform": "5-tap binomial down/up, residual pyramid (4/3 over-complete)",
      "repository": "UNKNOWN",
      "license": "NOT APPLICABLE",
      "decode_ms_measured": null,
      "decode_ms_derived": null,
      "quality_metric": "psnr_y_lab_kodak_mean_2560_416667B",
      "quality_value": 24.35,
      "bytes_per_frame": 416667,
      "cpu_gpu_parallelism": "same-frame possible per block/band",
      "memory_traffic": null,
      "evidence_class": "MEASURED (lab RD) + DERIVED cost",
      "confidence": "LOW",
      "status": "deferred"
    },
    {
      "id": "C08",
      "name": "Fixed PCA / KLT",
      "transform": "offline eigenbasis (colour measured; spatial not run)",
      "repository": "UNKNOWN",
      "license": "NOT APPLICABLE",
      "decode_ms_measured": null,
      "decode_ms_derived": null,
      "quality_metric": "",
      "quality_value": null,
      "bytes_per_frame": null,
      "cpu_gpu_parallelism": "same-frame possible per block/band",
      "memory_traffic": null,
      "evidence_class": "MEASURED (colour PCA only)",
      "confidence": "LOW",
      "status": "deferred"
    },
    {
      "id": "C09",
      "name": "VC-2 / Dirac Pro",
      "transform": "wavelet (5/3, 9/7 options), slices, exp-Golomb",
      "repository": "https://github.com/FFmpeg/FFmpeg",
      "license": "LGPL-2.1+",
      "decode_ms_measured": null,
      "decode_ms_derived": null,
      "quality_metric": "",
      "quality_value": null,
      "bytes_per_frame": null,
      "cpu_gpu_parallelism": "UNKNOWN",
      "memory_traffic": null,
      "evidence_class": "PUBLISHED structure only",
      "confidence": "LOW",
      "status": "deferred"
    },
    {
      "id": "C10",
      "name": "Hardware H.264 / HEVC",
      "transform": "block DCT + inter/intra prediction, CABAC",
      "repository": "NOT APPLICABLE",
      "license": "NOT APPLICABLE",
      "decode_ms_measured": 14.8,
      "decode_ms_derived": null,
      "quality_metric": "",
      "quality_value": null,
      "bytes_per_frame": null,
      "cpu_gpu_parallelism": "UNKNOWN",
      "memory_traffic": null,
      "evidence_class": "MEASURED on device",
      "confidence": "HIGH",
      "status": "rejected"
    }
  ],
  "shortlist": [
    "C05",
    "C02",
    "C04",
    "C03",
    "C01 compute path"
  ],
  "pareto_confirmed": [
    "C01 vs C10 on device decode latency"
  ],
  "pareto_potential": [
    "C01 cdf97",
    "C02 haar",
    "C03 wht8",
    "C04 dct8",
    "C04 dct16",
    "C05 cdf53"
  ],
  "critical_unknowns": [
    "mobile decode timing for any non-C01 transform",
    "compute vs fragment path on Adreno",
    "fence contention share",
    "real packer sizes for C02-C07",
    "game content and stereo"
  ],
  "next_experiments": [
    "X01",
    "X02",
    "X03",
    "X04",
    "X05"
  ]
}
```

---

# 24. RESEARCH INTEGRITY RULES

1. Never convert an ESTIMATE into a RESULT.
2. Never compare incompatible quality metrics as equivalent.
3. Never extrapolate desktop GPU latency to Galaxy XR and call it measured headset performance.
4. Never use throughput FPS as a substitute for single-frame decode latency.
5. Never hide missing data.
6. Never average results from different datasets without preserving the individual results.
7. Never silently exclude contradictory evidence.
8. Never select a preferred candidate before comparable measurements exist.
9. Never optimize for compression ratio when decoder latency is the primary objective.
10. Never assume symmetric encoder/decoder complexity is desirable.
11. Always preserve source URLs for externally derived claims.
12. Always state what a benchmark actually timed.
13. Always distinguish same-frame CPU/GPU parallelism from multi-frame pipelining.
14. Always preserve raw benchmark results alongside derived conclusions.
15. When evidence is insufficient, output UNKNOWN rather than filling the gap with intuition.
