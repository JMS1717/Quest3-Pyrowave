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

1. Client switch and live 207 Hz ABBA against Haar mode 3 with the server on 5/3: fresh FPS,
   decode time and in-headset captures.
2. Multilevel fusion of the small levels 2-4 (five barriers now).
3. CDF 9/7 in the same structure, if the remaining +0.4 dB is worth its wider support.
