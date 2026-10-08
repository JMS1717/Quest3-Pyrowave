# Clarity budget: where sharpness is lost (PLAN 2.2, October 7)

The question: between the game's 3072x3216 render and the panel, which stage loses the most
detail? The answer decides what to build next.

## Method

`tools/downsample/clarity_budget.py` renders `quality_scene` windows at 3072x3216 per eye. The
reference ("ideal") is that render filtered straight to the panel's density at the lens centre:
Lanczos-3 to 25 px/deg (2364x2475 for a 99 degree eye). The stream's stages are then added one at
a time, and each result is scored against the ideal at the same density. The drop from one row to
the next is that stage's cost:

1. **Stream size:** Catmull-Rom (the live Adaptive filter) to the stream size, 4:4:4, uncoded,
   displayed with a perfect (Lanczos-3) resample.
2. **Display resampling:** the same image displayed with bilinear resampling. The eye pass copies
   1:1, so this models the compositor's lens warp onto the panel.
3. **4:2:0:** the server's 2x2 box, with bilinear chroma on the headset.
4. **Quantization:** CDF 5/3 at the live per-eye byte cap, decoded in FP32.
5. **FP16:** the same bitstream decoded at the headset's precision (`PYROWAVE_PRECISION=1`).

Metrics are luma PSNR-HVS-M at 25 px/deg (hvs), plain luma PSNR, and mean CIE76 ΔE.

```
PYROWAVE_PC_TOOLS=<workspace>/research/pyrowave/build-pc/Release \
  python -m tools.downsample.clarity_budget --out <dir>
```

## Results

| Stage | 2080x2208: hvs / Y / ΔE | 2592x2784 (125 %): hvs / Y / ΔE |
| --- | --- | --- |
| 1 stream size | 23.53 / 27.16 / 1.87 | 28.97 / 31.87 / 1.10 |
| 2 + bilinear display resampling | 20.93 / 24.83 / 2.69 (**-2.60 dB**) | 25.19 / 27.87 / 1.88 (**-3.77 dB**) |
| 3 + 4:2:0 | 20.93 / 24.83 / 3.61 (chroma PSNR -7 dB) | 25.19 / 27.87 / 2.67 |
| 4 + quantization, 120 Hz / 1500 | 20.21 / 24.13 / 5.00 (-0.72 dB) | 22.17 / 25.36 / 4.97 (-3.03 dB) |
| 4 + quantization, 120 Hz / 2000 | 20.61 / 24.48 / 4.41 (-0.32 dB) | 23.59 / 26.43 / 4.26 (-1.61 dB) |
| 4 + quantization, 207 Hz / 1000 | 17.88 / 22.26 / 7.48 (-3.06 dB) | not run (doesn't decode in time) |
| 5 + FP16 decode | no change (under 0.002 dB) | no change |

In order of cost:

- **At 120 Hz the largest loss is the display resampling** (2.6 dB at 2080, 3.8 dB at 2592),
  more than quantization at 1500 Mbps (0.7 dB). The 2080 stream sits just below the panel's
  density, and the compositor's bilinear filter blurs it on the way.
- **At 207 Hz / 1000 Mbps quantization dominates** (3.1 dB).
- **A larger stream pays:** 2592 ends 2.0 dB above 2080 at 1500 Mbps and 3.0 dB above it at 2000.
  Quantization takes a bigger share of it, so 2592 wants more bits.
- 4:2:0 costs no luma, 7 dB of chroma PSNR and about 0.9 ΔE. Quantization adds 1.4 ΔE at
  120 Hz / 1500.
- FP16 costs nothing measurable. PLAN 2.8 (FP32) is not needed.

## Recovering the display loss

The same coded frames (120 Hz / 1500 Mbps, headset decode) through different display paths:

| Display path | 2080x2208 hvs / Y / ΔE | 2592x2784 hvs / Y / ΔE |
| --- | --- | --- |
| Compositor bilinear (today) | 20.21 / 24.13 / 5.00 | 22.17 / 25.36 / 4.97 |
| Perfect resample (the bound) | 22.45 / 25.82 / 4.49 (+2.24) | 23.88 / 26.76 / 4.60 (+1.72) |
| Catmull-Rom upscale into a 1.25x eye swapchain | 20.24 (+0.04) | 22.13 |
| ... into a 1.5x eye swapchain | 20.57 (+0.36) | 22.14 |
| **Sharpening 50** (the eye shader's CAS) | **21.44 / 25.01 / 4.79 (+1.23)** | 22.48 / 25.51 / 4.96 (+0.31) |
| Sharpening 100 | 21.19 (+0.98) | 22.00 (-0.17) |
| Sharpening 50, then a 1.25x swapchain | 20.86 | 21.98 |

At 207 Hz / 1000 Mbps (2080), CAS at the strengths of settings 1, 25, 50 and 75 gains 0.36, 0.40,
0.43 and 0.43 dB.

- **Sharpening 50 recovers more than half of the recoverable display loss at 120 Hz / 2080**,
  where it costs no frame rate ([SHARPENING.md](SHARPENING.md)). This backs turning it on for that
  configuration, once the owner has judged halos and the edge of the centre region.
- A larger eye swapchain with a better upscale (or ALVR's Snapdragon GSR `upscaling`, which also
  disables the direct eye path) recovers almost nothing: the compositor's bilinear still runs.
- With the 125 % stream the display loss is mostly stream detail the panel can't show; sharpening
  helps only a little there.

## Limits

- One synthetic world, two crops, one frame each; no motion and no game content.
- 25 px/deg is the lens centre; the periphery has fewer pixels per degree, where all of this
  matters less.
- PSNR-HVS-M rewards sharpening only up to the point where it starts to add error. It says
  nothing about halos or shimmer in motion. The owner's A/B decides.
- The compositor is modelled as one bilinear resample; its real lens warp varies over the field
  and wasn't captured.
