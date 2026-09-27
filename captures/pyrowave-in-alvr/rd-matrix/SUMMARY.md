# Offline PyroWave resolution × bytes-per-frame matrix — summary

330 cells: 5 lossless 2560×2560 per-eye sources (Kodak kodim04/07/08/19 tiled at native scale;
the synthetic benchmark panel), 6 square per-eye resolutions (1920..2560, encoded side-by-side as
the pipeline does, left = right), 11 exact per-frame byte caps each (150..550 KB, 416,667 B, and
the constant-bpp point). PyroWave 4:4:4 full range, `pyrowave-encode` / `pyrowave-decode` on the
PC, scored with ffmpeg (PSNR, SSIM) and libvmaf (PSNR-HVS, VMAF). Raw: `results.csv`.
Full tables A–E per source: `report.md`. Plots: `rd_<source>.png`,
`quality_vs_resolution_at_reference.png`.

Two reference conventions, kept apart:
- **scaled_*** (OBSERVED) — decoded frame vs the *downscaled* reference, i.e. codec fidelity to
  what it was given (psnr.cpp's `--scale-size` semantics). Structurally favours lower resolution:
  the reference itself has less information.
- **full_*** (CALCULATED) — decoded frame lanczos-upscaled to 2560 and scored against the
  original: what a viewer of a fixed 2560 panel would compare. The resampler is our choice.

## Findings

1. **Every measured point is below the knee.** Marginal gain per +50 KB never falls to a quarter
   of its first step on any source at any resolution up to 550 KB (Kodak: +0.5 to +1.4 dB
   PSNR-HVS per step, steady; synthetic: +1.2 to +2.5 dB per step even at the top). The
   assumption that 2560² @ ~300 Mbps / 90 Hz (416,667 B) is "near the useful knee" is
   **contradicted for this content**: it is bit-starved. Preserved as found.

2. **Fixed bytes, viewer side (full_*): resolution is second-order on dense natural content.**
   At 416,667 B the spread across 1920..2560 is 0.1–0.6 dB PSNR-Y on the four Kodak frames, with
   2304/2432 marginally best on three of them and 2560 on kodim19 (and on SSIM everywhere).
   Bytes dominate, pixels barely matter, because at ~0.25 bit/pixel the codec is discarding
   detail long before the resampler does.

3. **Fixed bytes, viewer side: resolution is first-order on fine synthetic detail.** The panel
   (1-px zone plate, 16-cell counter, text, markers) scores 51.7 dB at 2560 and 26–30 dB at every
   lower resolution: a downscale/upscale round trip destroys single-pixel structure regardless of
   bitrate. Small text and UI are the case for keeping resolution.

4. **Against the scaled reference, lower resolution always "wins"** (1920 best in 60 of 60
   Kodak columns) and the Pareto front on that metric is the 1920 column alone. This is the
   expected artefact of comparing against an easier reference, not evidence for 1920.

5. **Constant bits-per-pixel (Analysis B): PyroWave is nearly scale-invariant.** Fidelity to the
   scaled reference at constant bpp varies by ≤0.6 dB across 1920..2560 (kodim08: 20.97..21.58).
   Spend bits per pixel, and the codec returns the same quality per pixel whatever the frame size.

6. **Content class matters more than resolution.** At the same cap, kodim08 (house, fence,
   signage) sits 8–10 dB below kodim04 (portrait) on every metric; the synthetic panel 25–30 dB
   above both. Per-image results are kept; nothing is averaged across datasets.

## Where the 2560 @ 416,667 B point sits (all OBSERVED neighbours)

| cap | kodim08 scaled_psnr_hvs | kodim08 full_psnr_y | panel scaled_psnr_hvs | panel full_psnr_y |
|---|---|---|---|---|
| 350,000 | 20.53 | 20.88 | 54.57 | 48.15 |
| 400,000 | 21.30 | 21.28 | 56.69 | 50.25 |
| **416,667** | **21.58** | **21.42** | **57.49** | **51.73** |
| 450,000 | 22.12 | 21.64 | 58.50 | 52.81 |
| 500,000 | 23.02 | 22.02 | 60.25 | 55.08 |

Below the knee on both sources: the next 50 KB still buys ~0.5 dB (dense natural) to ~1 dB
(synthetic). No interpolation was needed; the point is measured directly.

## Caveats (UNKNOWN / limits)

- The tiled Kodak frames are **far denser than a game frame** (native photographic detail on all
  13 Mpx of the stereo frame). Real content sits between the synthetic panel and these; absolute
  dB here are not the headset's. The curve shapes and the resolution comparison are the result.
- Left = right in every source: stereo disparity effects are untested.
- PSNR-HVS-M with PyroWave's own CSF weights was not computed (its evaluator is not built);
  libvmaf's psnr_hvs is the nearest available.
- Encode/decode times in the CSV are RTX 3090 process wall time (~0.3 s each), not the headset.
- The brief's caps assume the 2560² stereo frame; the live pipeline encodes a gaze-foveated
  1984×896 frame at 60 % render, ~7× fewer pixels, so the same bytes are ~7× more bpp there.

## Recommended LIVE TEST REGION (not a winner)

Points that separate the hypotheses the offline data cannot:

1. **2560 @ 416,667 B** — the reference (300 Mbps @ 90 Hz); the anchor.
2. **2560 @ 550,000 B** (396 Mbps) — the top of the measured range; everything says it is still
   buying quality, and the live question is whether transport holds at 90 Hz.
3. **2304 @ 416,667 B** — the mid resolution that edged 2560 on three of four Kodak frames on
   the viewer metric at the same bytes; 19 % fewer pixels to decode.
4. **2304 @ 337,500 B** — its constant-bpp point: same quality-per-bit as the reference at 81 %
   of the bytes and pixels; the transport-relief candidate.
5. **1920 @ 416,667 B** — the lower bound: the scaled-reference "winner". If a wearer cannot
   see the text/UI penalty the synthetic set predicts, resolution is cheaper than we think.

Tapped frames with the photo-layout panel (small text, fence, gradient, motion band) are the way
to score these live cells against the same metrics.
