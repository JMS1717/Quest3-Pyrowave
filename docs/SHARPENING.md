# Sharpening on the headset

October 7, 2026, `.71`–`.73`. This covers [PLAN.md](PLAN.md) 2.7. Virtual Desktop sharpens by
default, and the owner compares against it.

## What it does

The setting is **Video > PyroWave > Sharpening**, 0–100. It is off by default and needs a SteamVR
restart.

- **Algorithm:** AMD FidelityFX CAS (contrast-adaptive sharpening).
  - It uses the cross neighbourhood: the pixel and its four direct neighbours.
  - It sharpens brightness (luma) only.
  - The strength adapts to local contrast, so edges that are already sharp are pushed less.
  - The peak weight runs from −1/8 at 0 to −1/5 at 100.
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
  - The client logs `[Q3PW_SHARPEN] sharpness=… area=…` when sharpening is active.

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

## Open

- **Owner's in-headset A/B.** Does 50 look better than off at 120 Hz? Is 100 too much? Is the edge
  of the 60 % centre visible?
- **Which costs less for the same clarity at 120 Hz?** Stream resolution 125 % without sharpening,
  or 100 % with it? They don't combine without losing frames.
- **A cheaper form.** Sharpen in the decoder's last synthesis pass, where the neighbours are already
  in registers or shared memory, instead of fetching them again in the eye pass.

## Already tried

- Sharpening the whole image costs 19 FPS at 207 Hz and at 120 Hz with a 125 % stream, so it was
  replaced by centre-only in `.73`.
