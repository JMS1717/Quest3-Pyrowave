# Decoder V2: fast CDF 5/3 (`pyrowave_decoder_set_cdf53v2`)

## Why: Haar is what looks pixelated

The owner's playtest (2026-10-06) found the stream "noticeably pixelated" next to Virtual Desktop,
with blocky colour and colour bleed on edges. The shipping configuration is Haar 4:2:0 at
1000 Mbit/s and 207 Hz, a cap of 604 KB per frame (about 0.35 bit per sample).

`tools/quest3/quality_scene.py` renders a deterministic SteamVR scene that is hard for an intra
codec: brick, multicolour HUD text, thin lines, purple sky against brown cloth, gradients, foliage,
a zone plate and a Siemens star. It can pan at head-turn speeds. The server's dump trigger
(`ALVR_PYROWAVE_DUMP_TRIGGER`) captured the live encoder input at 207 Hz: static, and 30, 60 and
120 deg/s pans (2.8, 5.1 and 10.3 px per frame, measured by phase correlation).
`tools/quest3/quality_score.py` re-encodes each clip with the PC encoder at the live byte cap and
scores it against the encoder input.

- PSNR-HVS-M: upstream PyroWave's CSF at the stream's 22 px/deg instead of a monitor distance.
- Added block edges: the step across 8-pixel boundaries relative to inside blocks, decoded minus
  source.
- Colour PSNR near strong edges (bleed).
- Flicker: PSNR of the frame-to-frame change, which catches errors that move between frames.

The offline encode reproduces the live bitstream tap (60 deg/s: PSNR-HVS-M 18.6 offline vs 18.8
live).

60 deg/s pan, 6 consecutive frames, 4:2:0:

| Mbit/s @ 207 Hz | wavelet | PSNR-HVS-M | PSNR-Y | added 8-px block edges | colour at edges | flicker |
|---|---|---|---|---|---|---|
| 700 | Haar | 16.9 | 21.5 | +1.74 | 27.7 | 18.7 |
| 700 | CDF 5/3 | 19.0 | 23.0 | 0.00 | 27.0 | 20.1 |
| 700 | CDF 9/7 | 19.6 | 23.6 | 0.00 | 28.9 | 20.7 |
| 1000 | Haar | 18.6 | 22.9 | +1.19 | 29.0 | 20.0 |
| 1000 | CDF 5/3 | 20.8 | 24.3 | 0.00 | 29.0 | 21.5 |
| 1000 | CDF 9/7 | 21.2 | 24.9 | 0.00 | 30.8 | 21.9 |
| 2000 | Haar | 23.5 | 26.9 | +0.51 | 34.1 | 23.9 |
| 2000 | CDF 5/3 | 25.8 | 28.2 | 0.00 | 34.4 | 25.5 |

- Haar is the only wavelet that adds block edges, at every bitrate. Haar at 1000 Mbit/s scores
  like CDF 9/7 at about 650; to match 9/7 at 1000 it needs about 1400.
- Static, 30 and 120 deg/s give the same per-frame scores within 0.7 dB, and the same ranking.
  Each frame is coded
  independently, so motion costs no per-frame quality. It shows up as flicker, which CDF also
  reduces.
- Absolute numbers are low because the scene is dense everywhere. Compare rows; don't read the
  numbers as game quality.

CDF 5/3 recovers most of the 9/7 gain (+2.2 of +2.6 dB PSNR-HVS-M at 1000 Mbit/s). Its synthesis
reaches one coefficient to each side instead of two, which matters more than arithmetic on this
GPU.

## Why a new decoder: stock CDF was 4x too slow

`decoder_ab wavelets` decodes the same synthetic 4160x2208 frame at the 604 KB cap with each
decoder. All arms run interleaved in one process, so they share the Adreno clock; the governor
otherwise moves it between 285 and 690 MHz from run to run.

| decoder (599 MHz, blocks 1 and 3) | total p50 | iDWT | dequant |
|---|---|---|---|
| Haar, default | 3.95 ms | 2.10 | 1.61 |
| Haar, haar32 mode 3 (shipping) | 1.97 | 1.53 | 0.99 |
| CDF 5/3, default | 7.88 | 6.20 | 1.70 |
| CDF 9/7, default | 8.63 | 6.87 | 1.71 |
| CDF 9/7, fragment path | 6.11 | (about 4.4) | 1.71 |
| **CDF 5/3, V2 mode 1** | 4.46 | 2.67 | 1.70 |
| **CDF 5/3, V2 mode 2 (packed luma output)** | 3.95 | 2.15 | 1.63 |
| **CDF 5/3, V2 mode 3 (+ quad-packed levels 0-1)** | **2.55** | **1.59** | **0.92** |

V2 mode 3 is 3.1x faster than the stock 5/3 decoder and 0.57 ms slower than Haar mode 3.

## What it does

`shaders/idwt_cdf53v2.comp` replaces `idwt.comp` for CDF 5/3. That shader loads a 32x32 tile
plus a 9/7-sized apron into shared memory, transposes it twice and waits on four workgroup
barriers. In V2 each invocation instead rebuilds a 4x4 output block from the band texels around it:

- No shared memory, no barriers, no transposes. Neighbours re-fetch the overlap through the texture
  cache.
- Lifting matches the stock path. Vertical runs first, then FP16 rounding, then horizontal, then
  rounding.
- The boundary extension is the one the stock path gets from its mirrored-repeat gathers:
  s[N] = s[N-1], d[-1] = d[0], d[N] = d[N-2].
- Mode 2 writes luma as RGBA8 2x2 pixel quads (4 stores instead of 16), like haar32 mode 3.
- Mode 3 also reuses haar32's quad-packed levels 0-1. Dequant stores RGBA16F coefficient quads, and
  V2 reads them with 25 fetches instead of 49. V2 writes the next level's LL as RGBA16F quads into
  the packed image's unused LL layer.
- The four 4x4 coefficient arrays must be filled by fully unrolled code with literal bounds. A first
  version indexed one shared array from non-constant loops; it spilled and ran at 9-15 ms.

Exactness (`decoder_ab cdf53v2[q|qp]`, `AB_WAVELET=53`): against the stock 5/3 decoder, every
plane is within one code value on about 0.6 % of pixels, at the same PSNR. This holds for
512x320, 4160x2208 and the unaligned 1000x600, which exercises the edge mirroring. That is the
same Adreno float-to-half behaviour documented for haar32.

## Live at 207 Hz

The client enables V2 with `debug.q3pw.cdf53v2` (0-3, default 3). It only applies to CDF 5/3
streams; the client logs `[Q3PW_CDF53V2] requested=3 active=3`. The test configuration (2026-10-06)
was:

- the owner's settings: 3072x3216 render, 2080x2208 per eye encoded, 207 Hz, 1000 Mbit/s, 4:2:0,
  no foveation;
- the `quality_scene` panning at 60 deg/s;
- 12 s ABBA windows, with SteamVR restarted per cell.

| cell | fresh FPS (blocks) | GPU decode p50 | decode fence p50 |
|---|---|---|---|
| CDF 5/3, stock decoder | 72.1 (71.9 / 72.2) | 11.36 ms | 13.5 ms |
| CDF 5/3, V2 mode 3 (first cell) | 138.0 (147.0 / 129.1) | 4.33 | 6.93 |
| Haar, haar32 mode 3 | 175.2 (175.3 / 175.0) | 2.80 | 5.26 |
| CDF 5/3, V2 mode 3 (last cell) | 144.7 (144.6 / 144.9) | 4.14 | 6.54 |

Fresh FPS follows 1000 / fence. Live decode takes about 1.8x the bench time: the clock is lower and
the compositor preempts decode. In-headset image captures are still pending.

## Where the time goes

`PYROWAVE_V2_LEVEL_TIMES=1` with `AB_STAGES=1` adds one GPU interval per level. Interleaved at
690 MHz, total p50 is Haar mode 3 1.71 ms vs V2 mode 3 2.27 ms. The V2 iDWT splits as:

| level | V2 mode 3 | Haar mode 3 equivalent |
|---|---|---|
| L0 (luma 4160x2208, packed RGBA8 out) | 0.48-0.55 ms | luma pass 1 (levels 1-0): 0.37 |
| L1 (luma, plus both chroma final planes) | 0.67-0.75 | chroma pass 1: 0.23 per plane |
| L2 | 0.19-0.24 | levels 4-2, all of pass 0: about 0.05 |
| L3 | 0.11 | |
| L4 | 0.04 | |

- Haar's chroma final pass costs the same as V2's (0.56 ms per plane at 285 MHz). The R8 chroma
  stores are expensive for both, not a V2 defect.
- Skipping V2 levels 2-4 (wrong output) cuts the total from 2.27 to 1.87 ms. 5/3 synthesis needs
  a neighbour apron that Haar's pair-local transform does not.
- Bitrate barely matters: V2 at 700 Mbit/s (423 KB cap) decodes in 2.21 ms vs 2.27 at 1000.

## Packing levels 2-3 (both decoders)

Dequant stored levels 2-4 as single R16F texels, eight `imageStore`s per invocation. That was
about 36% of dequant's store instructions for 7% of the coefficients. Dequant cost probes, p50 at
690 MHz (wrong output by design):

| | Haar mode 3 | V2 mode 3 |
|---|---|---|
| as built (levels 0-1 packed) | 1.72 ms | 2.27 ms |
| no dequant stores at all | 1.18 | 1.63 |
| skip only the level 2-4 stores | 1.43 | 1.93 |

Levels 0-3 now pack the same way: dequant writes two RGBA16F quads per invocation. Level 4 stays
scalar because its bands can be odd-sized; the frame alignment of 32 keeps levels 0-3 even. Each
non-final pass writes its LL output as quads into the packed image:
- V2's per-level pass already supported this;
- haar32's one-level and two-level passes gained it (`store_quad` without the DC shift).

| interleaved, 690 MHz | levels 0-1 packed | levels 0-3 packed |
|---|---|---|
| Haar mode 3 | 1.71-1.72 ms | **1.50-1.51** |
| V2 mode 3 | 2.26 | **1.94-1.96** |

Both exactness gates still pass (max diff 1): `decoder_ab haar32q`, `haar32qo` and `cdf53v2qp`.
`PYROWAVE_HAAR32_PACKED_LEVELS` / `PYROWAVE_V2_PACKED_LEVELS` = 2 restore the old layout; the client
sets both from `debug.q3pw.packed_levels` ("2" or "4") for live A/B.

Live, 2026-10-06. Settings: the owner's (3072x3216 render, 2080x2208 per eye encoded, 207 Hz,
1000 Mbit/s, 4:2:0, no foveation), `quality_scene` panning at 60 deg/s, 12 s ABBA windows,
A = `packed_levels` 2, B = 4:

| cell | fresh FPS A (blocks) | fresh FPS B (blocks) | fence p50 A / B | convert p50 A / B |
|---|---|---|---|---|
| Haar, haar32 mode 3 | 174.3 (175.4 / 173.2) | **184.1** (184.3 / 183.8) | 5.31 / **4.95** ms | 0.99 / 0.92 ms |
| CDF 5/3, V2 mode 3 | 137.0 (136.8 / 137.1) | **145.5** (145.8 / 145.3) | 6.81 / **6.41** | 1.82 / 1.78 |

Every B window beat both A windows. Stale frames fell from 33 to 23 per second for Haar and from 71
to 63 for V2. The in-headset screenshots of every window decode correctly. V2's reported GPU decode
interval rose (3.52 to 3.81 ms) while its fence fell: the interval is not the critical path once
the compositor preempts decode, so fresh FPS and the fence are the measures that count.

The RGBA conversion after decode costs 0.92 ms live for Haar and 1.8 ms for V2 with the same
shader. This is next to look at.

## Tried and rejected

Each candidate passed the exactness gate unless noted. All were timed interleaved against mode 3.

| candidate | result | why it fails |
|---|---|---|
| Chroma pair in one dispatch, interleaved RG8 CbCr | 2.85 vs 2.87 ms | register pressure doubles |
| Shared-memory staged row stores | no gain | the stores are not the coalescing kind of slow |
| FP16 arithmetic | no change | not ALU-bound |
| Level 1 in its own image | no change | |
| Shared-memory band tile in `idwt_cdf53v2.comp` | 7.2 vs 5.3 ms at 285 MHz | any shared-memory path in that shader, even one switched off by a specialization constant, makes Adreno spill the band arrays |
| Mode 4: levels 4-2 in one dispatch (separate program, 4 KB shared memory) | 2.28 vs 2.27 ms | fusing saves no pipeline drains worth having; the 12 KB first version took 1.49 ms for those levels |
| Mode 4 overlapped with the level 0-1 dequant | 2.29 ms | dequant grows by what iDWT loses; the GPU runs them back to back |

The Adreno 740 decode is throughput-bound. Only less work helps; reordering or fusing dispatches
does not.

## Reproduce

```
tools/pyroclient/build.sh
adb push decoder_ab libpyrowave-shared.so libc++_shared.so /data/local/tmp/q3pw-ab/
# exactness gate against stock 5/3 (the synthetic frame scores 22-24 dB at the 604 KB cap)
adb shell 'cd /data/local/tmp/q3pw-ab && AB_WAVELET=53 AB_MIN_PSNR=15 AB_ALLOW_DIFF=1 AB_GATE_ONLY=1 LD_LIBRARY_PATH=. ./decoder_ab cdf53v2qp'
# all decoders, interleaved, at the 1000 Mbit/s / 207 Hz cap
adb shell 'cd /data/local/tmp/q3pw-ab && AB_STAGES=1 AB_BLOCKS=4 AB_FRAMES=20 LD_LIBRARY_PATH=. ./decoder_ab wavelets 4160 2208 603864'
# image quality on dumped encoder input
python -m tools.quest3.quality_score <clip.y4m> --out <dir> --mbps 700 1000 --wavelet haar 53 97
```

## Next

1. In-headset captures of V2 against Haar at the owner's settings, with motion.
2. The RGBA conversion pass: 0.9 ms (Haar) to 1.8 ms (V2) of the live fence.
3. CDF 9/7 in the same structure, if the remaining +0.4 dB is worth its wider support.
