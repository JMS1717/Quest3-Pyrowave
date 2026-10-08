# Sharpening on the headset

October 7, 2026, `.71`–`.75`. This covers [PLAN.md](PLAN.md) 2.7. Virtual Desktop sharpens by
default, and the owner compares against it.

## What it does

The setting is **Video > PyroWave > Sharpening**, 0–100. It is off by default and needs a SteamVR
restart.

- **Algorithm (`.75`):** a linear sharpen over the cross neighbourhood (the pixel and its four
  direct neighbours), of brightness (luma) only: `Y + k (4Y − neighbours)`, with k = 0.003 per
  percent (0.15 at 50).
  - It pre-compensates the compositor's bilinear lens warp, the largest loss at 120 Hz
    ([CLARITY-BUDGET.md](CLARITY-BUDGET.md)).
  - `.71`–`.74` used AMD FidelityFX CAS (contrast-adaptive, peak weight −1/8 at 0 to −1/5 at 100).
    `debug.q3pw.sharpen_kernel=cas` still selects it for A/B.
- **Where it runs:** in the eye shader that already converts the packed YCbCr buffer
  (`present_ycbcr.glsl`, CDF 5/3 and Haar mode 5/6), so there is no extra pass.
  - In the packed layout, each 2x2 luma quad is one texel. The pixel's own quad already holds one
    horizontal and one vertical neighbour, so the cross costs two extra texture fetches.
  - Neighbours stay inside the pixel's own eye.
- **Centre only (`.73`):** the sharpened program covers a rectangle of 60 % of each axis of each
  eye, moved 5 % of the width towards the nose. The rest is drawn with the plain program, scissored
  as light foveation draws its bands.
- **Not applied:**
  - with light foveated encoding;
  - on the staging path;
  - to MediaCodec (RGBA) frames.
- **Test overrides:**
  - `debug.q3pw.sharpen=N` (0–100) overrides the setting; 0 forces it off.
  - `debug.q3pw.sharpen_area=N` (10–100) sets the share of each axis.
  - The client logs `[Q3PW_SHARPEN] sharpness=… area=… kernel=…` when sharpening is active.

## Measurements

All runs are wired, CDF 5/3, with a 3072x3216 render. They are ABBA blocks with a client relaunch
per arm; A is off and B is sharpness 50. "Eye" is the eye pass's p50 CPU-side time per frame.

Full image (`.71`/`.72`):

| Hz / Mbps / stream | FPS off | FPS on | Eye off → on |
| --- | --- | --- | --- |
| 207 / 1000 / 2080 | 176.7 | 157.5 | 4.86 → 5.66 ms |
| 120 / 1500 / 2080 | 118.9 | 118.6 | 2.15 → 3.18 ms |
| 120 / 1500 / 2592 (125 %) | 117.1 | 98.2 | 3.61 → 4.94 ms |

Centre 60 % (`.73`):

| Hz / Mbps / stream | FPS off | FPS on | Eye off → on |
| --- | --- | --- | --- |
| 207 / 1000 / 2080 | 178.1 | 173.3 | 4.82 → 4.95 ms |
| 120 / 1500 / 2592 (125 %) | 118.5 | 110.8 | 3.65 → 4.17 ms |

- **120 Hz at 100 %:** the GPU has headroom, so sharpening is free in frame rate. The decode fence
  moved by +1.8, −1.0 and +0.6 ms in three runs, so there is no consistent latency change.
- **207 Hz, and 120 Hz at 125 %:** the GPU is already full, so every extra fetch costs frames.
  Centre-only cuts the loss from 19 to about 5 FPS at 207 Hz, and from 19 to 8 at 125 %.
- **Visible effect:** on a static harness scene, the mean absolute Laplacian of the headset
  screenshot goes from 7.0–7.4 (off) to 9.7–10.7 (on). This was consistent across blocks and builds.
  Crops of small text look clearly crisper, with no visible halos and slightly more grain on flat
  areas.

Linear against CAS (`.75`, centre 60 %, setting 50):

Offline ([CLARITY-BUDGET.md](CLARITY-BUDGET.md)), PSNR-HVS-M luma at the panel's 25 px/deg, gain
over no sharpening on coded frames, and the share of pixels pushed more than 8 levels outside their
3x3 source range:

| | 2080, 120 Hz / 1500 | 2592, 120 Hz / 1500 | 2080, 207 Hz / 1000 | Overshoot |
| --- | --- | --- | --- | --- |
| CAS 50 | +1.23 dB | +0.31 dB | +0.43 dB | 9.8 % |
| Linear 0.10 per neighbour (setting 33) | +1.40 dB | **+0.76 dB** | +0.51 dB | 5.0 % |
| **Linear 0.15 (setting 50)** | **+1.69 dB** | +0.64 dB | +0.62 dB | 8.3 % |
| Linear 0.20 (setting 67) | +1.78 dB | +0.42 dB | +0.67 dB | 10.9 % |

A perfect display path would gain 2.24 dB at 2080 and 1.72 dB at 2592.

On the headset (ABBA or ABAB, a relaunch per arm):

| Cell | A | B | FPS A / B |
| --- | --- | --- | --- |
| 120 Hz / 1500 / 2080, static | CAS 50 | linear 50 | 118.0 / 119.3 |
| 207 Hz / 1000 / 2080, pan | off | linear 50 | 187.5 / 177.6 |
| 207 Hz / 1000 / 2080, pan | CAS 50 | linear 50 | 166.2 / 175.8 |

- Linear is cheaper than CAS: about 10 FPS more at 207 Hz in the same cell.
- The screenshot Laplacian is 9.9 with linear and 10.6 with CAS (7.0–7.4 off), in line with its
  lower overshoot.
- At 207 Hz sharpening still costs about 10 FPS; at 120 Hz with stream 100 % it is free.
- For the 125 % stream, a lower setting (about 33) scored best.

## Sharpening on the PC instead (offline, October 8)

The idea: spend PC time to save Quest GPU time. Sharpen, or downsample with a sharper kernel, on
the PC before 4:2:0 and encoding. The headset then shows the decode unchanged.

`tools/downsample/clarity_budget.py --prefilters` codes each variant at the live per-eye byte cap,
decodes it at headset precision and shows it with bilinear resampling. The metric is luma
PSNR-HVS-M at 25 px/deg against the ideal render. Setup: 2080x2208 stream, CDF 5/3, two
`quality_scene` windows.

| Stream filter | 120 Hz / 1500 | 120 Hz / 2000 | 207 Hz / 1000 | ΔE at 120 / 1500 |
| --- | --- | --- | --- | --- |
| Catmull-Rom (today), no sharpening | 20.21 | 20.61 | 17.88 | 5.00 |
| Catmull-Rom, headset linear 50 (`.75`) | 21.90 | | | 4.66 |
| Lanczos-3 | 20.98 | 21.34 | 18.20 | 4.86 |
| Keys a=-1.5 | 21.17 | 21.69 | 18.19 | 4.87 |
| Catmull-Rom + PC linear 0.10 | 21.72 | 22.26 | 18.42 | 4.88 |
| Catmull-Rom + PC linear 0.15 | 22.09 | 22.57 | 18.60 | 4.87 |
| Catmull-Rom + PC linear 0.20 | 22.07 | 22.62 | **18.65** | 4.93 |
| **Lanczos-3 + PC linear 0.10** | **22.23** | **22.70** | 18.56 | 4.83 |

- **Sharpening before encoding scores at least as well as sharpening on the headset.**
  - At 120 Hz / 1500, PC sharpening scores 22.1-22.2 against 21.9.
  - At 207 Hz / 1000, the PC's best is +0.77 dB. The headset kernel scored +0.62 dB earlier (table
    above).
  - The quantizer spends some bits on the extra high-frequency energy, but less than the gain.
- **It costs the Quest nothing.** The headset sharpen costs about 10 FPS at 207 Hz and 8 at 120 Hz
  with a 125 % stream.
- **The sharper downsample kernels alone gain 0.3-1.0 dB.** The server's existing "Adaptive Lanczos"
  option already offers Lanczos-3.
- **Colour is unchanged:** ΔE 4.83-4.93 against 5.00. Only luma is sharpened.

Not yet measured:

- the 2592x2784 stream (the run was stopped for low memory before it);
- moving content;
- how it looks.

Sharpening before a lossy coder can make ringing and noise more visible, and the owner asked for
subjective judgement over PSNR.

### Server option (source only, not yet built or measured)

`ALVR_Q3PW_PRESHARPEN=N` in vrserver's environment (1–50, k = N/100 per neighbour; unset is off)
adds one pass after the planar YCbCr conversion in `FrameRender.cpp`.

- It sharpens the full-size gamma luma plane into a second shared R8 texture, which the encoder
  imports in place of the plain plane. This is the same kernel as the offline `+linK` rows.
- Neighbours are clamped to the pixel's own eye and to the frame edge.
- Chroma and its 4:2:0 box are unchanged.
- The server logs `[PYROWAVE] luma pre-sharpen k=…` when it is on.
- With foveated encoding the pass runs on the compressed frame, so the periphery is sharpened more
  in display terms. Measure it with foveation off first.
- The shader compiles with `fxc` for ps_5_0.
- It is an environment variable rather than a setting until a headset A/B shows it is worth
  keeping.

Next steps:

1. build, then run the server tests;
2. an in-headset A/B, static and panning, at 120 Hz / 1500 / 2080: PC 15 against headset linear 50
   against off. Record the server's `[Q3PW_RENDER_GPU]` convert time for the PC's cost;
3. the 2592 rows.

## Open

- **Owner's in-headset A/B.** Does 50 look better than off at 120 Hz? Is 100 too much? Is the edge
  of the 60 % centre visible?
- **Which costs less for the same clarity at 120 Hz?** Stream resolution 125 % without sharpening,
  or 100 % with it? They don't combine without losing frames.
- **A cheaper form.** Sharpen in the decoder's last synthesis pass, where the neighbours are already
  in registers or shared memory, instead of fetching them again in the eye pass.

## Already tried

- CAS (`.71`–`.74`): replaced by the linear kernel in `.75`, which keeps more detail with fewer
  overshooting pixels and costs less.
- A larger eye swapchain with a Catmull-Rom upscale before the compositor recovers almost nothing
  (+0.04 dB at 1.25x, +0.36 dB at 1.5x; [CLARITY-BUDGET.md](CLARITY-BUDGET.md)).
- Sharpening the whole image costs 19 FPS at 207 Hz and at 120 Hz with a 125 % stream, so it was
  replaced by centre-only in `.73`.
