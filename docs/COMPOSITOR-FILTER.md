# Compositor layer filtering

**`.34` can ask the Quest compositor to supersample and sharpen the projection
layer. The strongest setting costs about 0.26 ms of compositor GPU time per
frame and no measurable FPS at 120 Hz / 4:2:0. It is opt-in and off by default.
Whether it looks better has not been judged in the headset.**

## Why

The stream is decoded at 2080x2208 per eye and copied into the OpenXR eye
swapchains. The compositor then warps that image onto the panels for lens
distortion and reprojection. Its default bilinear filter blurs fine detail and
can shimmer where the warp minifies. `XR_FB_composition_layer_settings` lets an
app request better filtering for a layer. This happens on the headset after
decode, so the stream, encoder, bitrate and decode cost do not change.

## Control

| Property | Read | Effect |
| --- | --- | --- |
| `debug.q3pw.layer_filter` | once per session, at stream start | `+`-separated list of `supersample`, `supersample_hq`, `sharpen`, `sharpen_hq`, `auto`. Unset or unknown: no filter request |

`auto` adds `XR_META_automatic_layer_filter`, which lets the runtime choose
among the requested filters. The extension requires at least one other filter
flag, so the client ignores a request that contains only `auto`. logcat `[Q3PW_LAYER_FILTER]`
reports the request, whether the runtime advertised both extensions, and the
flags sent. Quest 3 advertised both extensions on the tested OS.

To try it, set the property before starting the client:

```
adb shell setprop debug.q3pw.layer_filter supersample_hq+sharpen_hq
```

Clear it with `adb shell setprop debug.q3pw.layer_filter ""`.

## Measurement

Each block relaunched the client: 5 s settle excluded, 20 s measured, order
ABBAABBAAB. The source was a stationary procedural chart at 2080x2208 per eye,
120 Hz, 1000 Mbps, 4:2:0, over USB/TCP. TW is the compositor GPU time from the
runtime's once-per-second VrApi line for the client process.
([results](../results/LAYER-FILTER-AB-2026-10-04.json))

| Arm | Displayed target FPS | Lost frames/s | Compositor GPU (TW) | Eye-copy CPU p90 |
| --- | --- | --- | --- | --- |
| No filter | 117.7 | 2.4 | 0.86 ms | 1.77 ms |
| `supersample_hq+sharpen_hq` | 117.6 | 2.6 | **1.13 ms** (5/5 pairs) | 1.80 ms |

Paired B−A: TW +0.26 ± 0.01 ms, FPS −0.08 ± 0.27, lost frames +0.16 ± 0.34/s
(mean ± standard error over 5 adjacent pairs). The FPS and loss differences
are within noise.

## Limits

No perceptual evaluation: sharpening can add halos or shimmer, and supersampling
can soften. Only the strongest combination was measured, only at 120 Hz / 4:2:0
on an unworn headset with a stationary chart. At 144 Hz the GPU is close to full
(decode about 5.4 ms of a 6.9 ms frame), so 0.26 ms may cost frames there.
