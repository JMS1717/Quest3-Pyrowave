# Multilevel inverse Haar (`debug.q3pw.haar32`)

Status: default on in mode 5 (`debug.q3pw.haar32` unset; [PRESENT-YCBCR.md](PRESENT-YCBCR.md)),
which steps down to mode 4 without storage on the output buffer, with limited range or with Catmull-Rom
chroma. `0` to `5` select a mode; requesting
`debug.q3pw.fuse_color=1` or `debug.q3pw.dequant_haar=1` turns the default off, since those
experiments replace passes this one owns. The client logs `[Q3PW_HAAR32] requested=N active=M
packed_luma=0|1 dual_chroma=0|1` and steps down to the best mode the decoder accepts (CDF wavelets, 4:4:4, the
fragment decode path and precisions other than 1 fall back to mode 0 or 1).

| mode | what changes |
|---|---|
| 0 | default PyroWave decoder |
| 1 | multilevel inverse Haar, two dispatches per plane |
| 2 | 1 + levels 0-1 stored as RGBA16F 2x2 quads |
| 3 | 2 + luma written as an RGBA8 plane of half size, one 2x2 pixel quad per texel |
| 4 | 3 + Cb and Cr reconstructed by one dispatch into one RG8 plane |

## Mode 4: both chroma planes in one RG8 plane

A cost probe (`PYROWAVE_HAAR32_PROBE=3`, which drops the chroma final-pass stores and lets the
compiler drop the work feeding them) put the chroma final pass at 0.44 of mode 3's 2.09 ms at
492 MHz. Mode 4 runs that pass once for both components (the `DUAL` variant of
`idwt_haar32.comp`, second component on bindings 4-6) and stores Cb and Cr together into an RG8
plane: half the store instructions, and the conversion pass reads both with one bilinear fetch.
Output is bit-identical to mode 3 (`decoder_ab haar32qd` with `AB_ALLOW_DIFF=1` reports the same
differing counts against the base decoder as `haar32qo`).

| | mode 3 | mode 4 |
|---|---|---|
| bench p50, 4160x2208, 604 KB, 492 MHz, interleaved | 2.10 ms | 2.03 ms |
| live fresh FPS, ABBA 12 s windows (blocks) | 183.7 (185.3 / 182.1) | **190.4** (190.6 / 190.2) |
| live decode fence p50 | 4.94 ms | **4.71** |
| live stale frames per second | 23.9 | 17.3 |

Live settings, 2026-10-06: the owner's 3072x3216 render, 2080x2208 per eye encoded, 207 Hz,
1000 Mbit/s, Haar 4:2:0, no foveation, Adaptive downsample, `quality_scene` panning at 60 deg/s.
Most of the live gain is in the conversion and fence, not the decode interval, so the single
fetch matters more than the halved stores. The in-headset screenshots show correct colour.

## What it does

The default Haar 4:2:0 decoder runs one inverse-Haar dispatch per level and component, with a
barrier after each level (5 levels, 13 dispatches, 5 barriers). `pyrowave_decoder_set_haar32`
replaces that with two dispatches per plane and two barriers:

| plane | pass 0 | pass 1 (writes the plane) |
|---|---|---|
| luma | levels 4-3 -> LL2 (R32F) | levels 2-0 -> R8 plane |
| 4:2:0 chroma | level 4 -> LL3 (R32F) | levels 3-1 -> R8 plane |

`shaders/idwt_haar32.comp` gives each invocation one root coefficient and rebuilds its subtree in
registers: no shared memory, no workgroup barrier, no apron. Haar synthesis is pair-local and every
level of the 32-aligned pyramid is exactly twice the next, so fetches below a valid root are always
in bounds; only the root and the final plane's stores are guarded, and the store guard is
workgroup-uniform (interior groups never test). A three-level pass starts one level below its top
and recomputes the parent's quadrant from four cached fetches instead of sharing it.

The arithmetic and FP16 rounding points are those of `idwt.comp`'s pair-local path (vertical,
round, horizontal, round, DC shift). The pass split can be overridden for tuning with
`PYROWAVE_HAAR32_LUMA` / `PYROWAVE_HAAR32_CHROMA` (level counts, each 1-3, e.g. `23` and `13`).
The decoder refuses the mode for CDF wavelets, 4:4:4, the fragment path, the other Haar
experiments, fused dequant and fused colour.

The work started as the [3,2] split the earlier `.52` attempt used; that attempt is not restored.
On Quest the [2,3] luma split is faster, because the large bottom levels gain most from staying in
registers.

## Standalone result (Quest 3, `tools/pyroclient/decoder_ab`)

Same Haar 4:2:0 bitstream (synthetic 4160x2208 stereo-sized frame, 1 Mbyte, about 1000 Mbit/s at
120 Hz) decoded by the default decoder and the arm on one Vulkan device; one submission per frame
bracketed by GPU timestamps; 8 blocks x 40 frames in ABBA order after 10 warm-up frames each.

| schedule (luma / chroma) | default p50 | arm p50 | delta |
|---|---|---|---|
| 3,2 / 2,2, chroma root below its top level (first version) | 4.708 ms | 4.215 ms | -10.5 % |
| 3,2 / 2,2 | 4.678 | 4.210 | -10.0 % |
| 3,2 / 3,1 | 4.708 | 4.293 | -8.8 % |
| 3,2 / 1,3 | 4.684 | 4.160 | -11.2 % |
| 2,2,1 / 2,2 | 4.695 | 4.439 | -5.4 % |
| 2,1,2 / 2,1,1 | 4.697 | 4.211 | -10.3 % |
| 1,1,3 / 1,3 | 4.698 | 4.083 | -13.1 % |
| **2,3 / 1,3 (default)** | **4.691** | **4.086** | **-12.9 %** |

Default schedule, all 8 blocks at 640 MHz: block medians 4.688-4.710 ms vs 4.085-4.088 ms.
PyroWave stage timestamps: iDWT 2.68 -> 1.99 ms (-26 %); dequant unchanged at about 2.0 ms.

## Why packing: the passes are store-bound

Probes on Adreno 740 (`decoder_ab h32nostore`, `h32nofetch`, `dqprobe`; timing only, wrong output):

| stage | as built | without image stores | without band fetches |
|---|---|---|---|
| multilevel iDWT (mode 1) | 2.2 ms | 0.75 ms | 1.6 ms |
| dequant | 2.0 ms | 0.95 ms | - |

The cost follows the number of `imageStore` instructions, not bytes. Dequant decodes 4x2 values per
invocation starting at an even coordinate, which is exactly two 2x2 quads, so mode 2 stores levels
0-1 (93 % of the coefficients) as RGBA16F quads: two stores per invocation instead of eight, and
the Haar pass fetches one texel per band per child quad. Mode 3 additionally stores the luma plane
as RGBA8 quads (one store per four pixels); `ycbcr_to_rgba.comp` and `convert.frag` unpack it with
a specialization constant. Chroma planes stay R8, so the sampler still upsamples 4:2:0 for free.

| arm (standalone, 640 MHz) | dequant | iDWT | total p50 |
|---|---|---|---|
| mode 0 | 2.0 | 2.7 | 4.7 |
| mode 1 | 2.0 | 2.0 | 4.09-4.31 |
| mode 2 | 1.3-1.4 | 2.1 | 3.61-3.63 |
| mode 3 | 1.3-1.4 | 1.23-1.5 | 3.005-3.037 |

## Live result (Quest 3, 207 Hz, 2080x2208 per eye, 4:2:0, 1000 Mbit/s)

Local build 5246dff (`.61` + this branch), ABBA with a client restart per block, 12 s windows,
GPU level default, thermal status 0. Fresh = newly decoded frames shown per second.

| cell | arm | fresh FPS (blocks) | GPU decode p50 | fence p50 | VrApi App GPU |
|---|---|---|---|---|---|
| 0 vs 1 (cb4efd5) | mode 0 | 118.6 / 119.0 | 5.57 ms | 8.02 ms | 5.80 ms |
| | mode 1 | 136.6 / 137.0 | 4.79 | 6.88 | 5.03 |
| 1 vs 3 | mode 1 | 137.2 / 135.2 | 4.40 | 6.92 | 4.83 |
| | **mode 3** | **180.4 / 178.5** | **2.77** | **5.17** | **3.78** |
| 0 vs 3 | mode 0 | 121.5 / 120.3 | 5.55 | 7.94 | 5.76 |
| | **mode 3** | **174.9 / 160.1** | **2.96** | **5.45** | **3.94** |
| 3 vs 0, PC render 3072x3216 per eye, Adaptive downscale to 2080x2208 | mode 3 | 174.8 (mean) | 2.90 | - | - |
| | mode 0 | 120.1 (mean) | 5.58 | - | - |

Mode 3 delivers 160-180 fresh frames/s at 207 Hz against about 120 for mode 0 (+40-50 %), and
ALVR's pipeline-latency estimate fell from 39.3 to 34.6 ms in the 1-vs-3 cell (an estimate, not
motion-to-photon). GPU load stays near 96 %: the client is still GPU-throughput bound, with
compositor time-warp at about 0.85 ms per displayed frame. Raw cells are private
(`workspace/state/claude56/matrix-20261006T180714Z`).

Harness lessons from this run: local builds share a version string, so an "already deployed"
harness kept the previous APK until it compared APK hashes; and SteamVR's watchdog aborted
vrserver on a hung Windows audio-endpoint callback, after which the next start entered safe mode
and blocked both the ALVR and Virtual Desktop drivers until the `blocked_by_safe_mode` flags in
`steamvr.vrsettings` were cleared.

## Exactness

The decoded planes are not byte-identical to the default decoder: one code value on 1.4 % of luma
pixels and 0.9-2.0 % of chroma pixels at 4160x2208, never more than one, and the luma PSNR against the
source is identical to three decimals (32.199 dB; 54.315 vs 54.326 dB at 512x320).

The evidence points to Adreno's float-to-half conversion, not to the reconstruction:

- The default decoder against itself and against its dedicated pair kernel
  (`PYROWAVE_HAAR_PAIRS=64-column`) are byte-exact, so the comparison itself is deterministic.
- Single-level passes of the new shader (`PYROWAVE_HAAR32_LUMA=11111`), structurally the default
  path, still differ, by more pixels than the fused schedule.
- Two chroma schedules of the new shader with identical arithmetic per output differ from each
  other (0 vs 45 152 Cb pixels); `precise` on every intermediate changed nothing, and FMA
  contraction cannot matter because every product is a multiplication by 0.5.

So `decoder_ab` accepts the mode only with `AB_ALLOW_DIFF=1`, and the gate line says `WITHIN`
instead of `EXACT`. Modes 2 and 3 are `WITHIN` with the same counts as mode 1.

End to end through `libpyroclient` (`pyroclient_test`, the `native-asymmetric.wave` fixture,
4160x2208 Haar 4:2:0, compute and fragment conversion): modes 1, 2 and 3 produce byte-identical
RGBA on both conversion paths. Against mode 0, 1.96 % of pixels differ, by at most 4 RGB codes
(one plane code value through the BT.709 limited-range matrix; 307 k channel values differ by 1,
62 k by 2, 137 by 3 or 4). A difference of that size at equal PSNR is not visible, but it is still
a perceptual change by the project's rules until it is compared in the headset.

## Reproduce

```
tools/pyroclient/build.sh            # builds decoder_ab next to libpyroclient.so
adb push decoder_ab libpyrowave-shared.so libc++_shared.so /data/local/tmp/q3pw-ab/
adb shell 'cd /data/local/tmp/q3pw-ab && AB_ALLOW_DIFF=1 AB_STAGES=1 LD_LIBRARY_PATH=. ./decoder_ab haar32'
```

`decoder_ab base` and `decoder_ab pairs64` are the controls; `haar32q` and `haar32qo` are modes 2
and 3; `dqprobe`, `h32nostore` and `h32nofetch` are the cost probes (`AB_SKIP_GATE=1`). Raw output:
[`results/HAAR32-2026-10-06.json`](../results/HAAR32-2026-10-06.json).

## Next steps (in order)

1. **Dequant: find what costs the remaining ~1.3 ms.** Rejected 2026-10-06: decoding the bit
   planes with three word loads and an 8x8 bit transpose instead of one byte load and 16 bit
   operations per plane (CPU-verified, saved privately as a patch). Standalone, mode 3 with it ran
   3.04 ms against 3.005-3.04 ms without, so the plane loop is not the cost. Mode 3 dequant without its
   stores measured 1.09 ms (probe, one block at 640 MHz), so stores are now only about 0.25 ms;
   the rest is per-block header loads, subgroup scans and barriers. Probe those next.
2. **Chroma stores.** Done: mode 4.
3. **Conversion pass** (0.9 ms live in mode 4, about 37 MB of RGBA8 written per frame) and the ALVR
   eye render it feeds: the next large item. Rejected 2026-10-06: folding the final luma level into
   the conversion pass (`debug.q3pw.fuse_color=1` on the packed level 0, mode 4). Decode got
   cheaper (best 1.78 to 1.67 ms; bench arm 2.03 to 1.72 ms) but the conversion pass went from
   0.89 to 1.71 ms, and the standalone fence p50 rose from 4.13 to 5.19 ms. Each fragment then
   fetches four RGBA16F band texels instead of one RGBA8 texel, and the pass is bandwidth-bound.
   Done 2026-10-07 by dropping the pass instead: mode 5 ([PRESENT-YCBCR.md](PRESENT-YCBCR.md)),
   now the default, writes packed luma and chroma into the output buffer and ALVR's eye render
   converts; live fence -0.35 to -0.8 ms and +2 to +12 fresh FPS over mode 4.
4. **Queueing.** ALVR's vsync-queue estimate is about 11.4 ms in every arm; the adaptive-buffering
   policies in the optimization plan are the next latency lever once decode has headroom.
