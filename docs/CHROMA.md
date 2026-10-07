# Chroma choice on Quest 3

Keep 4:2:0 as the default. 4:4:4 remains an optional quality mode: spare USB
bandwidth did not make its GPU reconstruction cost free in the current decoder.

**Current (alpha.9 and `.64`).** 4:2:0 is the default and **Full chroma (4:4:4)** is opt-in.
The latest check is the [October 7 run at 207 Hz](#october-7-check-at-207-hz): 68.1 fresh FPS
with 4:4:4 against 189.3 with 4:2:0, because 4:4:4 decodes twice the chroma and has no packed
YCbCr output. The 120 Hz comparison below is from the older `.11` decoder (October 1).

The matching .11 pair (3c223eb) used 2080 x 2208 encoded pixels per eye, 120 Hz,
Haar, Vulkan Compute, one worker, no foveation, native ALVR USB/TCP and the same
GPU 7 / CPU 6 requests. The fixed chart inputs were byte-identical for each eye;
SteamVR recommended a 2544 x 2704 source texture which was encoded to 2080 x 2208.
The 4:4:4 target was doubled to 2000 Mbps to retain comparable bits per YUV sample.

| Fixed-chart cell | Fresh frames/s | Instantaneous FPS p1 | Timestamp gap p95 | GPU decode p50 | Completion p50 | Estimated latency p50 | Actual payload p50 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 4:2:0 / 1000 Mbps | 99.97 | 59.99 | 16.80 ms | 4.62 ms | 9.30 ms | 71.30 ms | 1002.93 Mbps |
| 4:4:4 / 2000 Mbps | 65.86 | 40.00 | 22.10 ms | 8.84 ms | 13.55 ms | 89.89 ms | 2004.74 Mbps |

Each cell had a 45-second capture within its scene window, reported battery
45 C at both ends, GPU at 690 MHz, and thermal status 0. Battery temperature is a
thermal proxy, not a GPU die thermometer. These are warmed short trials, not
repeated sustained gameplay or thermal endurance acceptance. Full records without
private identifiers are in [reviewed measurements](../results/CHROMA-2026-10-01.json).
A first 25-second 420 chart cell crossed a scene transition and is excluded here.

SteamVR Home also regressed: 4:4:4 / 1000 Mbps delivered 68.01 fresh FPS and 4:4:4 / 2000 Mbps delivered
63.46, versus 96.50 for 4:2:0 / 1000 Mbps. A return 4:2:0 control recovered 93.43 fresh FPS over
an 89-second submitted-event span; its requested 120-second window included initial
reconnection. The Home battery temperatures differed by about 1 C. SteamVR crashed
after the first 4:4:4 / 2000 Mbps capture; its cause is unknown. The known-good 4:2:0 settings
were restored and crash-added driver blocks cleared, preserving VD registration.

Screenshot inspection of fine yellow/cyan/magenta/green HUD text, outlines and
1/2/4/8-source-pixel saturated edge pairs showed stronger colored strokes in the
4:4:4 / 2000 Mbps preset. Both retained readable text. This compares the complete presets,
including doubled bitrate, so it does not isolate chroma from bit allocation.
Screenshots are not a human in-headset quality judgment. SteamVR Home supplies a
textured environment check; detailed gameplay quality comparisons remain pending.

At 4160 x 2208 stereo, eight-bit 4:2:0 contains 13,777,920 YUV samples/frame; 4:4:4 contains
27,555,840. At 120 Hz their uncompressed rates are 13.23 and 26.45 Gbps. The transmitted
stream remains compressed. Chroma format alone does not change a fixed bitrate:
1000 Mbps still provides about 1.042 MB/frame, and 2000 Mbps about 2.083 MB/frame. Doubling
target bitrate gives comparable average bits/sample, not guaranteed perceptual parity.

For this target, the observed 34% reduction in fresh FPS and 46% increase in completion
outweigh the colored-text improvement. Keep 4:2:0 for gameplay while optimizing toward
sustained 120 FPS. Reconsider 4:4:4 as a default only after a future decoder shows effectively
negligible impact in repeated sustained matched tests and a clearly noticeable in-headset
improvement. If that improvement is hard to see, prefer 4:2:0.

## October 7 check at 207 Hz

The **Full chroma (4:4:4)** setting still takes effect and still costs too much. One `.61` run
(2080 x 2208 per eye from a 3072 x 3216 render, Haar, 1000 Mbps, static quality scene, 12 s
windows, headset awake), changing only the setting:

| 207 Hz | Fresh FPS | GPU decode p50 | Eye-copy fence p50 |
|---|---:|---:|---:|
| 4:2:0 (packed YCbCr presentation) | 189.3 | 2.93 ms | 4.52 ms |
| 4:4:4 | 68.1 | 11.40 ms | 14.07 ms |

4:4:4 decodes twice the chroma samples and has no packed presentation path, so it pays for a
separate colour conversion too. Its decode alone takes more than twice the 4.83 ms frame period.
4:2:0 stays the default.

Reproduce the visual source with:

```powershell
python -m tools.quest3.stereo_scene --quality --seconds 180 --out results/local/chroma-source
```

The hidden PC scene uses a cached texture and submits via SteamVR. `ready.json`
and `scene.json` record source size and Unix-nanosecond start/end times. Start the
benchmark after readiness and finish before scene end; compare the capture start
and per-event elapsed times to verify the window. Keep the client overlay enabled
consistently. Change **Full chroma (4:4:4)** in video/PyroWave settings and restart
SteamVR/client for negotiation (while streaming, the server now reconnects and restarts
SteamVR about 2 s after the change; [SETTINGS-APPLY.md](SETTINGS-APPLY.md)); verify the
overlay and negotiated OpenVR flag.
Restore it off after the experiment. Fresh event rate, instantaneous FPS percentiles,
source-timestamp gaps and ALVR estimated latency are distinct; optical latency needs
separate measurement.

## Colour bleed: where it comes from (October 7)

Offline, on frame 0 of the 60 deg/s quality-scene dump (`quality-corpus/pan60.y4m`, encoder input,
2080 x 2208 per eye), with the current PC encoder (headset rate allocation) at the live 207 Hz
byte cap. ΔE is CIELAB distance (CIE76) of the reconstructed RGB from the 4:4:4 source; "edge"
restricts it to pixels near strong luma or chroma edges. PSNR-HVS-M is luma at 22 px/degree.

Uncoded, today's 4:2:0 (centred 2x2 box, bilinear on the Quest) is nearly invisible next to the
source: coloured HUD text keeps its shape and colour. After coding it is not: at 700 Mbps the
yellow text on blue turns into magenta blocks. **The colour bleed people see is chroma coding
loss, not chroma subsampling.** None of these cheaper fixes helped:

| 700 Mbps | RGB PSNR | ΔE mean | ΔE edge | PSNR-HVS-M |
|---|---:|---:|---:|---:|
| box chroma, chroma weight 1.6 (default) | **18.53** | 15.64 | 17.94 | **17.47** |
| least-squares chroma for bilinear upsampling | 18.43 | 15.43 | 17.63 | 16.96 |
| box, chroma weight 2.4 | 18.41 | 15.28 | 17.51 | 16.53 |
| box, chroma weight 3.2 | 18.11 | 15.42 | 17.66 | 15.70 |
| box, luma-guided chroma on the Quest (guided filter, 3x3) | 17.57 | 18.49 | 21.43 | 17.47 |

- **Least-squares chroma** (chroma chosen so its bilinear upsample best matches 4:4:4) and a
  **higher chroma weight** both lower ΔE by 1 to 4%, but the sharper chroma takes bits from luma:
  0.5 to 1.8 dB less PSNR-HVS-M. 1000 Mbps gives the same picture.
- **Linear-light luma adjustment** (each pixel's luma corrected so the reconstruction keeps the
  source's linear luminance) fixes luminance at edges uncoded (51 -> 68 dB) but nothing survives
  coding.
- **Luma-guided chroma reconstruction** would have cost the Quest nothing per pixel (fit at
  chroma resolution, then `a * Y + b`), but coded luma is too noisy to guide: 2 dB less chroma
  PSNR and more ΔE at every radius and regularisation tried.

The levers that do reduce colour bleed are bits and wavelet: higher bitrate (see
[BITRATE.md](BITRATE.md)) and 4:4:4 or CDF 5/3, both of which cost decode time. This is a deliberately dense synthetic scene;
a natural game frame should be checked before the trade is decided. Scripts:
`workspace/state/claude-oct6/chroma_study.py`, `guided_study.py` (private workspace).
