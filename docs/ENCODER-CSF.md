# Encoder rate weighting for the headset

Status: default on `claude/decoder-v2` from 2026-10-07 (PyroWave patch,
`Encoder::Impl::get_quant_rdo_distortion_scale`). Live validation below.

## Why

PyroWave's encoder spends its byte budget by weighting each wavelet band's distortion with a
contrast sensitivity function (CSF). Upstream evaluates the CSF for a 96 dpi PC monitor at 1 m:
the finest band's Nyquist frequency is 0.34 x 96 = 32.6 cycles/degree, high on the falling side of
the CSF. Fine luma detail was therefore discounted about 10x in power (HL/LH) and 75x (HH) against
the coarse bands, and the rate control starved it of bits.

A Quest 3 eye spans about 99 degrees vertically (the client logs `v 99.0`). A 2208-row stream
has 22 pixels per degree, so its finest band sits near 11 cycles/degree. That is still on the
rising side of the CSF: every band clamps near the peak and the weighting becomes nearly flat. Fine
detail is exactly what the eye resolves at this pixel density, and what looked "pixelated" next to
Virtual Desktop.

## Change

- The level-0 Nyquist frequency is `0.5 * height / 99` cycles/degree (11.2 at 2208 rows) instead
  of 32.6. `PYROWAVE_CPD_NYQUIST` overrides it for experiments.
- With 4:2:0, chroma bands were weighted 0.6x. Against the flatter luma weighting that cost about
  1 dB of Cb/Cr PSNR (more colour bleed), so the factor is now `CHROMA_CSF_420` = 1.6.
  `PYROWAVE_CHROMA_CSF` overrides it.

Only the distortion weights change: the bitstream, the decoder, the byte budget and the encoder's
work per frame are the same.

## Offline evidence

`quality_scene` crops, 3072x3216 render downsampled with the live Catmull-Rom filter to 2080x2208,
Haar 4:2:0, the live per-eye byte cap at 207 Hz, PC build of the same encoder source. Scores at the
source size: PSNR-HVS-M at the headset's pixels per degree, and PSNR of Y, Cb and Cr.

Two crops (the per-eye byte cap applies to each frame):

| Mbps | weighting | PSNR-HVS-M | Y | Cb | Cr |
|---|---|---|---|---|---|
| 1000 | upstream (monitor, chroma 0.6) | 14.49 | 18.12 | 28.97 | 29.11 |
| 1000 | headset, chroma 1.2 | **15.16** | 18.51 | 29.10 | 29.25 |
| 1000 | headset, chroma 1.6 (default) | 15.13 | 18.49 | **29.63** | **30.07** |
| 1500 | upstream | 15.06 | 18.50 | 30.36 | 30.68 |
| 1500 | headset, chroma 1.2 | **15.71** | 18.91 | 30.13 | 30.53 |
| 1500 | headset, chroma 1.6 (default) | 15.67 | 18.88 | **30.74** | **31.11** |

At 1000 Mbps the headset weighting scores higher than upstream does at 1500 Mbps, and at the same
rate it gains about 0.65 dB PSNR-HVS-M with better chroma too.

One crop, sweeping the two factors at 1000 Mbps:

| level-0 cycles/degree | chroma factor | PSNR-HVS-M | Y | Cb | Cr |
|---|---|---|---|---|---|
| 32.6 (upstream) | 0.6 | 15.26 | 18.90 | 29.05 | 29.06 |
| 16 | 0.6 | 15.97 | 19.32 | 27.91 | 27.98 |
| 11 | 0.6 | 16.00 | 19.34 | 27.74 | 27.98 |
| 8 | 0.6 | 16.01 | 19.35 | 27.74 | 27.98 |
| 11 | 0.9 | 15.99 | 19.33 | 28.67 | 28.62 |
| 11 | 1.2 | 15.94 | 19.29 | 29.38 | 29.13 |
| 11 | 1.6 | 15.92 | 19.27 | 29.69 | 30.14 |
| 11 | 2.4 | 15.75 | 19.15 | 30.48 | 30.91 |

Anything from 8 to 16 cycles/degree gives the same result, because the CSF clamp at 8 flattens
the weights. So the exact field of view and stream size hardly matter. 1.6 is a chroma factor
that costs 0.08 dB of PSNR-HVS-M against 0.6 and gains 2 dB of Cb/Cr. The study script is
`tools/downsample/csf_study.py`. It takes a PC build of the patched encoder
(`pyrowave-encode.exe` and `pyrowave-decode.exe`) and runs the variants through the env overrides. On the patched encoder,
`32.64:0.6` reproduces upstream's weighting.
