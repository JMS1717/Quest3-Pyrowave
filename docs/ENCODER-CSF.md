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

**CDF 5/3 (Oct 8, `STUDY_WAVELET=53`, 1000 Mbps at 207 Hz, 4 crops).** The 5/3 coarse levels
fail as soft ringing rather than blocks, so the boost was re-swept on that wavelet. It shows the
same trade-off as Haar and the default is kept:

| 5/3, 1000 Mbps | PSNR-HVS-M | smooth Y |
|---|---|---|
| levels >= 3 x6 (default) | 15.50 | 54.0 |
| levels >= 3 x3 | 15.62 | 52.3 |
| levels >= 4 x4 | 15.70 | 48.9 |
| levels >= 2 x2 | 15.56 | 50.0 |
| no boost (3/1) | 15.75 | 46.5 |

**Level weights for the 125 % stream (October 8, rejected).** At 2592x2784 quantization costs
3.0 dB at 1500 Mbps against 0.7 dB at 2080 ([CLARITY-BUDGET.md](CLARITY-BUDGET.md)), and part of
level 0 lies above the panel's density. The idea: discount level 0 so its bits go to levels the
compositor's bilinear keeps. `tools/downsample/clarity_budget.py`, 2592x2784, CDF 5/3, 120 Hz,
2 crops, scored at 25 px/deg after a bilinear display, with an experimental per-level amplitude
weight (`PYROWAVE_LEVEL_SCALE`, PC tools only):

| Level weights (0,1,2,3,4) | 1500: PSNR-HVS-M / Cb / ΔE | 2000: PSNR-HVS-M | 1500 with the Quality profile's Lanczos + PC sharpen 30 |
|---|---|---|---|
| 1,1,1,1,1 (default) | **23.99** / 33.36 / 4.29 | **24.59** | 25.68 |
| 0.7,1,1,1,1 | 23.78 / 33.65 / 4.19 | 24.48 | 25.63 |
| 0.5,1,1,1,1 | 23.39 / 33.86 / 4.18 | 24.32 | 25.34 |
| 0.35,1,1,1,1 | 22.65 / 34.08 / 4.17 | 23.95 | 24.67 |
| 1.4,1,1,1,1 | 23.92 / 33.03 / 4.49 | 24.61 | 25.48 |
| 2,1,1,1,1 | 23.66 / 32.49 / 4.76 | 24.53 | 25.09 |
| 1,1,1,0.5,0.5 (coarse boost 3 instead of 6) | 24.15 / 33.56 / 4.27 | 24.67 | 26.00 |

Level 0 is not wasted at 125 %: discounting it costs luma and only moves bits to chroma, and
boosting it costs both. Halving the coarse boost gains 0.16-0.32 dB, the same detail-for-gradients
trade as the 5/3 sweep above, so the default stays.

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

## 4. Supersampled streams: scale by panel rows (`.129`, October 9)

The headset CSF uses the stream's own frequency: the level-0 Nyquist is `0.5 * rows / 99`
cycles/degree, 16.2 at full size (3216 rows). A stream taller than the panel's 2208 rows is
resampled down for display, which removes much of its finest band before the eye sees it. The
bits the encoder spends there are partly invisible.

**Study:** `STUDY_FULL=1` (or `STUDY_STREAM=WxH`) in `tools/downsample/csf_study.py`.
- **Encode:** the crop itself (3072x3216) or a Catmull-Rom downsample, Haar 4:2:0, at the
  120 Hz cap per eye.
- **Scoring (`d_` columns):** the source and the decode are both resampled with Catmull-Rom to
  2080x2208, then scored with PSNR-HVS-M at 2208 / 99 pixels per degree.
- **Crops:** two crops of the quality scene.

| Stream | Mbps | level-0 cpd | Display PSNR-HVS-M | Display Y | Display Cb / Cr | Smooth Y / CbCr |
| --- | --- | --- | --- | --- | --- | --- |
| 3072x3216 | 1500 | 16.2 (old default) | 29.75 | 30.62 | 34.54 / 35.59 | 57.96 / 56.52 |
| | | 19 | 30.02 | 30.73 | 34.78 / 35.81 | 58.31 / 56.66 |
| | | 22 | 30.27 | 30.82 | 35.02 / 36.07 | 58.45 / 56.77 |
| | | **23.6 (`.129` default)** | 30.28 | 30.75 | 35.26 / 36.29 | 58.67 / 56.96 |
| | | 25 | **30.35** | 30.76 | 35.26 / 36.27 | 58.67 / 56.96 |
| | | 32 | 28.53 | 29.62 | 36.08 / 36.84 | 59.04 / 57.30 |
| | | 48 | 23.59 | 26.73 | 36.58 / 37.13 | 59.27 / 57.57 |
| 3072x3216 | 1000 | 16.2 (old default) | 24.81 | 27.03 | 33.52 / 34.51 | 56.70 / 55.22 |
| | | 22 | 25.24 | 27.33 | 33.76 / 34.85 | 57.30 / 55.65 |
| | | **23.6 (`.129` default)** | 25.34 | 27.38 | 33.80 / 34.98 | 57.30 / 55.65 |
| | | 25 | **25.51** | 27.46 | 33.98 / 35.15 | 57.30 / 55.65 |
| 2592x2784 (125 %) | 1500 | 14.1 (old default) | 25.57 | 28.34 | 34.58 / 35.43 | 58.17 / 56.69 |
| | | **17.7 (`.129` default)** | 25.56 | 28.36 | 34.68 / 35.49 | 58.20 / 56.73 |
| | | 20 | 25.45 | 28.30 | 35.05 / 35.74 | 58.40 / 56.92 |

- **The optimum is about 22-25 at full size, at both rates:**
  - 1500 Mbps: +0.6 dB PSNR-HVS-M, +0.14 dB luma, +0.7 dB chroma;
  - 1000 Mbps: +0.7 dB PSNR-HVS-M, +0.43 dB luma, +0.5-0.6 dB chroma;
  - smooth areas improve as well.
- **Past about 25, display luma falls fast:** too little is left for level 0.
- **At 125 % the scaled value is neutral**, within 0.1 dB.
- **Default from `.129`:** the level-0 frequency is multiplied by `max(1, rows / 2208)`. That is
  23.6 at full size and 17.7 at 2784 rows. At full size it scores +0.5 dB PSNR-HVS-M at both
  rates, +0.1-0.35 dB luma and +0.3-0.7 dB chroma. Streams at or below the panel's rows are
  unchanged (`patches/pyrowave-csf-panel.patch`). `PYROWAVE_CPD_NYQUIST` still overrides it.
- The full-size scores are not comparable with the 125 % ones. A full-size decode goes through
  the reference's own resampling chain, but the 125 % stream adds a resample of its own.
- **Live (`.129`, wired, full size, 120 Hz, 1500 Mbps, two blocks):**
  - 116.3 and 117.9 fresh FPS, against 115-118 before.
  - The encoder does the same work; only which bit planes it keeps changes.

## 5. Coarse-level weight for supersampled streams (`.130`, October 9)

Section 3 chose the 6x coarse weight at 2208 rows as the first setting with no more blocking than
upstream. At full size, 6x leaves less blocking than upstream, so it spends bits on gradients that
upstream already drew well enough.

**Study:** as in section 4, with the level-0 frequency at its `.129` value. Upstream is
`32.64:0.6::99/1` with `PYROWAVE_DISCARD_WEIGHT=0`.
- **Block steps (`tools/downsample/block_steps.py`):** the mean absolute step of the luma coding error across 16- and 32-pixel
  boundaries, over the mean step inside them.
- **Flat areas:** where the encoder input's smoothed Laplacian is below 0.5, which is 36-39 % of
  the crops.
- **Flat display PSNR (`d_flat`):** the luma error resampled to 2080x2208, over the same areas.

| Stream, Mbps | Levels 3-4 weight | Display PSNR-HVS-M | Display Y | 16 px / 32 px step | Flat display PSNR |
| --- | --- | --- | --- | --- | --- |
| full size, 1500 | upstream | 29.08 | 30.31 | 0.668 / 0.755 | 50.15 |
| | 6x (`.129`) | 30.28 | 30.75 | 0.584 / 0.578 | 49.92 |
| | **4.12x (`.130`)** | **30.82** | 31.14 | 0.663 / 0.663 | 49.60 |
| | 4x | 30.89 | 31.18 | 0.669 / 0.670 | 49.56 |
| | 3x | 31.45 | 31.46 | 0.721 / 0.756 | 49.17 |
| | 6x at level 4 only | 31.77 | 31.64 | 0.949 / 0.959 | 47.15 |
| | none | 32.17 | 32.01 | 1.000 / 1.140 | 46.60 |
| full size, 1000 | upstream | 25.38 | 27.73 | 0.842 / 0.971 | 48.01 |
| | 6x (`.129`) | 25.34 | 27.38 | 0.828 / 0.844 | 47.02 |
| | **4.12x (`.130`)** | **26.00** | 27.79 | 0.856 / 0.891 | 47.20 |
| | 3x | 26.32 | 28.05 | 0.905 / 0.963 | 47.18 |
| | 6x at level 4 only | 26.93 | 28.33 | 1.348 / 1.366 | 43.99 |
| 125 %, 1500 | upstream | 24.46 | 27.69 | 0.597 / 0.623 | 51.95 |
| | 6x (`.129`) | 25.55 | 28.36 | 0.719 / 0.709 | 48.89 |
| | 4.75x | 25.60 | 28.40 | 0.751 / 0.746 | 48.80 |
| | 8x | 25.43 | 28.23 | 0.692 / 0.682 | 48.95 |
| 125 %, 1000 | upstream | 22.98 | 26.38 | 0.818 / 0.917 | 49.17 |
| | 6x (`.129`) | 23.86 | 26.79 | 0.880 / 0.868 | 47.78 |
| | 4.75x | 23.96 | 26.87 | 0.902 / 0.892 | 47.66 |
| | 12x | 23.26 | 26.30 | 0.838 / 0.828 | 47.20 |

- **Full size:** 4.12x (6x / (3216 / 2208)) matches upstream's 16-pixel blocking and has less at
  32 pixels, at both rates. It scores +0.54 dB PSNR-HVS-M at 1500 and +0.66 dB at 1000 over 6x,
  +0.4 dB display luma. Chroma is unchanged. At 1000 Mbps, 6x had only tied upstream on PSNR-HVS-M.
- **Lower weights add blocking past upstream:** 3x by 8 % at 16 pixels, level 4 alone and none by
  40-60 %, with the flat display PSNR 3-4 dB down.
- **125 %:** 6x is already above upstream's blocking. Even 12x does not reach upstream, and 4.75x
  gains under 0.1 dB, so 6x stays. The `.129` frequency did not cause this: the old 14.1 had a
  16-pixel step of 0.708 at 1500 and 0.938 at 1000.
- **Default from `.130`:** streams at least 1.4x the panel's rows use 6x / (rows / 2208); smaller
  streams keep 6x (`patches/pyrowave-csf-panel.patch`). `PYROWAVE_LF_BOOST` still overrides it.
- **Live (`.130`, wired, full size, 120 Hz, 1500 Mbps, three blocks):** 116.1, 117.2 and 114.4
  fresh FPS. In the last block the panel's own vsync fell to 115.7. The same as `.129`.
- Untested: whether the headset view shows the gradient change. `.129` is the rollback.
