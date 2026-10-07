# Haar mode 5: no RGBA conversion pass

Status: default from 2026-10-07 (`debug.q3pw.haar32` unset or `5`; `4` selects the previous
default). Won all three live ABBA runs below. CDF 5/3 has the same output as V2 mode 5
(`debug.q3pw.cdf53v2`, default 5); see [DECODER-V2.md](DECODER-V2.md#mode-5-packed-ycbcr-into-the-hardware-buffer-default-for-cdf-53).

## Why

In mode 4 the client's last GPU pass reads the decoded planes and writes the full frame as RGBA8
into the hardware buffer ALVR imports: 4160x2208x4 = 37 MB written per frame, about 0.9 ms of
the 4.7 ms live fence at 207 Hz. ALVR's eye shader then reads those 37 MB again. Folding the last
luma level into that pass was slower (see [HAAR32.md](HAAR32.md), next steps), because the pass is
bandwidth-bound. Mode 5 removes the pass instead.

The earlier planar attempt ([DECODE-PIPELINE.md](DECODE-PIPELINE.md#planar-output-without-the-rgba-pass-not-possible-on-quest-3-october-4))
failed because Quest 3 gralloc has no R8 hardware buffers. Mode 5 needs only RGBA8, which this
driver already accepts as a Vulkan storage image.

## Layout

Each output slot is one RGBA8 AHardwareBuffer of width x height/2 (4160x1104 at the owner's
setting, 18.4 MB instead of 36.7 MB):

| texels | contents |
|---|---|
| x < width/2 | luma, one 2x2 pixel quad per texel ((0,0), (1,0), (0,1), (1,1) in RGBA), as mode 3 writes it |
| x >= width/2 | 4:2:0 chroma, one pixel per texel: Cb in R, Cr in G, 0 in B and A |

Chroma keeps one pixel per texel, so the consumer can still bilinear-filter it in hardware: one
fetch for luma and one for chroma per output pixel.

## Implementation

- PyroWave (`patches/pyrowave-cdf53v2.patch`): `pyrowave_decoder_set_haar32(5)`. The multilevel
  pass gains an `output_offset` push constant; luma's final pass stores at offset 0 and the dual
  chroma pass at (width/2, 0). Planes 0 and 1 are both views of the slot image. Requires frame
  width and height divisible by 4.
- pyroclient: allocates the slots at width x height/2 with `STORAGE` usage, passes the slot image as
  planes 0-1 for each frame, takes it from the foreign (GLES) queue family before the decode and
  hands it back after, and records no conversion pass. Steps down to mode 4 (same plane
  allocation) without storage on the AHB, with limited range, or with `chroma_filter=catmull`.
- ALVR (`patches/quest3-alvr.patch`, `alvr/graphics/resources/present_ycbcr.glsl`): the direct-eye
  copy and the staging renderer each compile a second program with `q3pw_present_sample()`
  (nearest luma, hardware-bilinear chroma clamped to the chroma half, full-range BT.709, the same
  maths as `convert.frag`). Each frame selects it when the buffer is exactly
  2*eye_width x eye_height/2 (`AHardwareBuffer_describe`); RGBA buffers, including every MediaCodec
  frame, keep the original program. With fixed foveation the direct copy filters luma bilinearly.

## Correctness

- `decoder_ab haar32qp` (gate with `AB_ALLOW_DIFF=1`): planes identical to mode 4 (same differing
  counts against the base decoder at 512x320, 1000x600 and 4160x2208).
- `pyroclient_test` dumps of the same frame, mode 5 converted offline with the shader's maths
  against the mode 4 RGBA output: max 2 code values, mean 0.003, 0.012% of samples above 1
  (hardware bilinear weight rounding, and mode 5 skips the RGBA8 rounding before the eye copy).

## Standalone cost (Quest 3, 4160x2208, 604 KB frame, awake, 690 MHz)

| | mode 4 | mode 5 |
|---|---|---|
| `decoder_ab` decode p50, interleaved | 1.495 ms | 1.509 ms |
| `pyroclient_test` submit-to-fence p50, ABAB | 3.79 / 3.73 ms | **2.90 / 2.89 ms** |
| `pyroclient_test` GPU decode p50 | 1.70 / 1.71 ms | 1.98 / 1.98 ms |
| conversion pass p50 | 0.88 ms | none |

Writing the imported buffer costs about 0.27 ms more than writing the decoder's own planes, but the
fence falls by 0.85 ms.

## Live (Quest 3, owner settings, October 7)

3072x3216 render, 2080x2208 per eye stream, 207 Hz, 1000 Mbps Haar 4:2:0, Adaptive downsample,
quality scene panning at 60 deg/s, headset awake, over the 5246dff (.61) streamer.
Three ABBA runs of mode 4 against mode 5 (f4a0bf2), then one against the default build (run 4), 12 s windows, means of the two blocks per arm. The GPU ran mostly at 640 MHz:
manually pinning `debug.oculus.gpuLevel=7` did not reach the client in these runs, see below.

| run | arm | fresh FPS | lost/s | fence p50 | GPU decode p50 | convert p50 | eye copy p50 | ALVR latency estimate |
|---|---|---|---|---|---|---|---|---|
| 1 | mode 4 | 190.2 | 17.3 | 4.70 ms | 2.52 ms | 0.88 ms | 4.42 ms | 34.3 ms |
| 1 | mode 5 | **196.5** | 11.1 | **4.35 ms** | 2.73 ms | none | **3.90 ms** | 31.2 ms |
| 2 | mode 4 | 188.0 | 20.7 | 4.69 ms | 2.51 ms | 0.88 ms | 4.39 ms | 34.0 ms |
| 2 | mode 5 | **190.0** | 19.6 | **4.13 ms** | 2.67 ms | none | **3.76 ms** | 33.4 ms |
| 3 | mode 4 | 174.5 | 32.7 | 5.48 ms | 2.64 ms | 1.30 ms | 5.16 ms | 33.4 ms |
| 3 | mode 5 | **187.0** | 20.8 | **4.68 ms** | 2.85 ms | none | **4.26 ms** | 33.3 ms |
| 4 | mode 4 (`haar32=4`) | 184.9 | 23.0 | 4.80 ms | 2.53 ms | 0.88 ms | 4.47 ms | 35.8 ms |
| 4 | default (6bc09da) | **197.6** | 10.1 | **4.44 ms** | 2.90 ms | none | **3.78 ms** | 29.8 ms |

Run 4 checks the 6bc09da build with no property set: the client logs `requested=5 active=5`.
Mode 5 had more fresh frames, fewer lost frames and a shorter fence in every run, and the eye copy
was 0.5-0.9 ms cheaper because it reads 18 MB per frame instead of 37 MB. Decode grows by about
0.2 ms (stores into the imported buffer), as standalone predicted. Run 3 had a weak final mode 4
block (157 FPS), so its gap is the least reliable; runs 1 and 2 alone give +2 to +6 FPS. The ALVR
latency figure is the client's pipeline estimate, not motion-to-photon. Screenshots of every block
show correct colour (per-channel means within 1.3 code values of mode 4 on the same scene).

`session_settings.video.pyrowave.quest3_max_gpu_clock=true` does pin the client at 690 MHz, but
in two attempts the server then reported no statistics after its client restart, so the harness
could not measure; this is a separate defect under investigation. Two causes were found on
October 7: the server restarted the client every few seconds because it undid HorizonOS's own
refresh-rate write (fixed, see [REFRESH-RATES.md](REFRESH-RATES.md)), and the test headset was
not worn, so it went to standby once the server handed proximity back after the restart. In that
state the server received no client statistics at all, although the same restart with the
headset kept awake by hand reported normally. The server now logs `[Q3PW_STATS]` when client
statistics stop arriving or stop matching its frames, so the next occurrence names itself.
Whether a worn headset ever shows the gap is not yet checked.
