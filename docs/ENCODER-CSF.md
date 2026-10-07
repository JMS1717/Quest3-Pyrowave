# Encoder rate allocation for the headset

Status: default on `claude/decoder-v2` from 2026-10-07 and checked in the headset (PyroWave
patch: `pyrowave_encoder.cpp` `get_quant_rdo_distortion_scale` and `shaders/wavelet_quant.comp`).
Only the encoder's choices change: the bitstream, the decoder, the byte budget and the encoder's work per frame are the same.

## Summary

PyroWave's rate control decides which bit planes of which 8x8 coefficient blocks fit the frame's
byte budget, weighting each band's distortion with a contrast sensitivity function (CSF). Three
changes, measured offline on the same frames at the same byte cap:

| 1000 Mbps, 4 crops | PSNR-HVS-M | Y | Cb | Cr | smooth-area Y | smooth-area Cb/Cr |
|---|---|---|---|---|---|---|
| upstream | 14.65 | 18.24 | 28.70 | 28.91 | 53.87 | 46.10 |
| headset CSF and chroma weight | 15.26 | 18.59 | 29.30 | 29.85 | 51.40 | 42.40 |
| + weighted discard distortion | 15.62 | 18.80 | 29.72 | 30.47 | 49.18 | 43.03 |
| + coarse-level weight (default) | **15.34** | **18.63** | **30.14** | **30.90** | **56.43** | **55.02** |

The default beats upstream on every measure: +0.7 dB PSNR-HVS-M on fine detail (about what
upstream gains going from 1000 to 1500 Mbps), +1.4/+2.0 dB Cb/Cr (colour bleed), +2.6 dB luma and
+8.9 dB chroma in smooth areas, and slightly less Haar blocking. The gain holds across rates:

| Mbps | upstream PSNR-HVS-M | default PSNR-HVS-M | upstream smooth Y | default smooth Y |
|---|---|---|---|---|
| 600 | 13.65 | 14.28 | 51.85 | 54.86 |
| 1000 | 14.65 | 15.34 | 53.87 | 56.43 |
| 1500 | 15.20 | 15.93 | 55.66 | 57.63 |

## 1. Headset CSF

Upstream evaluates the CSF for a 96 dpi monitor at 1 m, which puts the finest band's Nyquist
frequency at 0.34 x 96 = 32.6 cycles/degree, high on the falling side of the CSF. A Quest 3 eye
spans about 99 degrees vertically (the client logs `v 99.0`), so a 2208-row stream has 22 pixels
per degree and its finest band sits near 11 cycles/degree. The monitor model discounted level-0
detail about 10x in power (HL/LH) and 75x (HH), starving the textures that look "pixelated" next
to Virtual Desktop.

- The level-0 Nyquist frequency is `0.5 * height / 99` cycles/degree (11.2 at 2208 rows).
  `PYROWAVE_CPD_NYQUIST` overrides it. Anything from 8 to 16 gives the same result, because the
  CSF's clamp at 8 cycles/degree flattens the weights.
- The 4:2:0 chroma weight goes from 0.6 to `CHROMA_CSF_420` = 1.6. Against the flatter luma
  weights, 0.6 lost about 1 dB of Cb/Cr. `PYROWAVE_CHROMA_CSF` overrides it.

| 1000 Mbps, 1 crop | level-0 cycles/degree | chroma | PSNR-HVS-M | Y | Cb | Cr |
|---|---|---|---|---|---|---|
| upstream | 32.6 | 0.6 | 15.26 | 18.90 | 29.05 | 29.06 |
| | 16 | 0.6 | 15.97 | 19.32 | 27.91 | 27.98 |
| | 11 | 0.6 | 16.00 | 19.34 | 27.74 | 27.98 |
| | 8 | 0.6 | 16.01 | 19.35 | 27.74 | 27.98 |
| | 11 | 1.2 | 15.94 | 19.29 | 29.38 | 29.13 |
| default | 11 | 1.6 | 15.92 | 19.27 | 29.69 | 30.14 |
| | 11 | 2.4 | 15.75 | 19.15 | 30.48 | 30.91 |

## 2. Discard distortion was unweighted (upstream bug)

`wavelet_quant.comp` records, for each 8x8 block, the distortion of dropping 1..msb bit planes
and of discarding the block (`errors[msb + 1]`). The plane errors are multiplied by the band
weight `rdo_distortion_scale` (about 10^2 to 10^6); the discard error was not. Rate control sums
these per 32x32 unit (`analyze_rate_control.comp`), so every sub-block whose planes run out
contributed a far-too-small distortion and low-amplitude blocks - faint texture and gradient
steps - were dropped long before their cost justified it. Weighting the discard error
(`rdo_discard_scale`) adds 0.33 to 0.36 dB PSNR-HVS-M and 0.4 to 1.5 dB Cb/Cr with either CSF.
`PYROWAVE_DISCARD_WEIGHT=0` restores upstream.

## 3. Coarse-level weight

The first live screenshots of change 1 (static quality scene, same view, old against new
streamer) showed sharper fine texture but visible block steps in the scene's soft gradients,
which upstream drew smoothly. The CSF models noise, but coarse Haar levels fail as 16-pixel and
larger blocks in flat areas and gradients, which the eye picks out far more readily, and
flattening the weights had moved bits from those levels to level 0. Levels 3 and 4 now carry 6x
the amplitude weight (`PYROWAVE_LF_BOOST=<level>,<factor>`, default `3,6`).

| 1000 Mbps, headset CSF + discard fix | PSNR-HVS-M | Cb | Cr | smooth Y | smooth Cb/Cr | block steps |
|---|---|---|---|---|---|---|
| no boost | 15.62 | 29.72 | 30.47 | 49.18 | 43.03 | 6.74 |
| levels >= 2 x2 | 15.41 | 30.45 | 31.01 | 52.16 | 47.10 | 5.91 |
| levels >= 3 x3 | 15.47 | 30.18 | 30.93 | 53.99 | 51.28 | 5.38 |
| levels >= 3 x4 | 15.41 | 30.17 | 30.95 | 55.30 | 53.35 | 5.02 |
| levels >= 3 x6 (default) | 15.34 | 30.14 | 30.90 | 56.43 | 55.02 | 4.86 |
| levels >= 3 x8 | 15.24 | 30.10 | 30.88 | 57.24 | 55.81 | 4.69 |
| levels >= 4 x4 | 15.56 | 29.95 | 30.74 | 53.86 | 50.98 | 5.75 |
| upstream | 14.65 | 28.70 | 28.91 | 53.87 | 46.10 | 4.97 |

x6 is the first setting with less blocking than upstream, and it keeps most of the detail gain.

**Rejected: activity masking from in-band coefficients.** Weighting each block's distortion by
`(1 + A / offset)^-s`, where A is the block's largest coefficient, made smooth areas worse
(smooth Y 49.2 dB fell to 44 to 48 dB). In Haar a smooth gradient produces large detail
coefficients at the coarse levels, so the weight discounted exactly the blocks it was meant to
protect. A spatial activity map measured from the finest bands over each block's footprint
would be needed.

## Method

`tools/downsample/csf_study.py` crops the `quality_scene` world at 3072x3216, downsamples it with
the live Catmull-Rom filter to 2080x2208 and encodes Haar 4:2:0 at the live per-eye byte cap for
207 Hz with a PC build of the patched encoder (`pyrowave-encode.exe`, `pyrowave-decode.exe`).
Variants are `cpd[:chroma[:aq[:level/factor]]]`; `32.64:0.6::99/1` with
`PYROWAVE_DISCARD_WEIGHT=0` reproduces upstream exactly. PSNR-HVS-M (at the headset's pixels per
degree) and Y/Cb/Cr PSNR are scored against the 3072x3216 source. Smooth-area columns are PSNR
over pixels whose 31x31 neighbourhood has luma standard deviation below 3. Block steps (2 crops)
is the mean absolute step of the coding error across 8-pixel boundaries over the step inside
them, where the encoder input has low local curvature (flats and gradients).

## Live

Quest 3, 207 Hz, 2080x2208 per eye from a 3072x3216 render, 1000 Mbps Haar, 12 s blocks with
the headset awake; the same client throughout, only the streamer changes.

| streamer | 60 deg/s pan, fresh FPS (A/A ABBA) | static, fresh FPS |
|---|---|---|
| upstream rate allocation | 191.1 / 188.1 | 190.3 |
| headset CSF only (c892e11) | 186.3 / 186.4 | 193.0 |
| default (0581e97) | 189.2 / 185.8 | 195.0 |

The differences are within the run-to-run noise of about 5 FPS: the change costs no speed.
Static in-headset screenshots of the same view show the gain. With the headset CSF alone, the
scene's soft blobs and colour ramps broke into visible 16-pixel blocks and wide bands. With the
default, they are smoother than upstream's (upstream still shows faint vertical bands in the
red-to-blue ramp), and the small text on the ramp's border is at least as sharp as with the
headset CSF alone.
