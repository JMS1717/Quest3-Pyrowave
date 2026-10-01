# Chroma choice on Quest 3

Keep 4:2:0 as the default. 4:4:4 remains an optional quality mode: spare USB
bandwidth did not make its GPU reconstruction cost free in the current decoder.

The matching .11 pair (3c223eb) used 2080 x 2208 encoded pixels per eye, 120 Hz,
Haar, Vulkan Compute, one worker, no foveation, native ALVR USB/TCP and the same
GPU7/CPU6 requests. The fixed chart inputs were byte-identical for each eye;
SteamVR recommended a 2544 x 2704 source texture which was encoded to 2080 x 2208.
The 4:4:4 target was doubled to 2000 Mbps to retain comparable bits per YUV sample.

| Fixed-chart cell | Fresh frames/s | Instantaneous FPS p1 | Timestamp gap p95 | GPU decode p50 | Completion p50 | Estimated latency p50 | Actual payload p50 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 4:2:0 / 1000 Mbps | 99.97 | 59.99 | 16.80 ms | 4.62 ms | 9.30 ms | 71.30 ms | 1002.93 Mbps |
| 4:4:4 / 2000 Mbps | 65.86 | 40.00 | 22.10 ms | 8.84 ms | 13.55 ms | 89.89 ms | 2004.74 Mbps |

Each cell had a 45-second capture within its scene window, reported battery
45 C at both ends, GPU690 MHz, and thermal status0. Battery temperature is a
thermal proxy, not a GPU die thermometer. These are warmed short trials, not
repeated sustained gameplay or thermal endurance acceptance. Full records without
private identifiers are in [reviewed measurements](../results/CHROMA-2026-10-01.json).
A first 25-second 420 chart cell crossed a scene transition and is excluded here.

SteamVR Home also regressed: 444/1000 delivered68.01 freshFPS and444/2000 delivered
63.46, versus96.50 for420/1000. A return420 control recovered93.43 freshFPS over
an89-second submitted-event span; its requested120-second window included initial
reconnection. The Home battery temperatures differed by about1 C. SteamVR crashed
after the first444/2000 capture; its cause is unknown. The known-good420 settings
were restored and crash-added driver blocks cleared, preserving VD registration.

Screenshot inspection of fine yellow/cyan/magenta/green HUD text, outlines and
1/2/4/8-source-pixel saturated edge pairs showed stronger colored strokes in the
444/2000 preset. Both retained readable text. This compares the complete presets,
including doubled bitrate, so it does not isolate chroma from bit allocation.
Screenshots are not a human in-headset quality judgment. SteamVR Home supplies a
textured environment check; detailed gameplay quality comparisons remain pending.

At4160x2208 stereo, eight-bit420 contains13,777,920 YUV samples/frame;444 contains
27,555,840. At120 Hz their uncompressed rates are13.23 and26.45 Gbps. The transmitted
stream remains compressed. Chroma format alone does not change a fixed bitrate:
1000Mbps still provides about1.042MB/frame, and2000Mbps about2.083MB/frame. Doubling
target bitrate gives comparable average bits/sample, not guaranteed perceptual parity.

For this target, the observed34% reduction in freshFPS and46% increase in completion
outweigh the colored-text improvement. Keep420 for gameplay while optimizing toward
sustained120FPS. Reconsider444 as a default only after a future decoder shows effectively
negligible impact in repeated sustained matched tests and a clearly noticeable in-headset
improvement. If that improvement is hard to see, prefer420.

Reproduce the visual source with:

```powershell
python -m tools.quest3.stereo_scene --quality --seconds 180 --out results/local/chroma-source
```

The hidden PC scene uses a cached texture and submits via SteamVR. `ready.json`
and `scene.json` record source size and Unix-nanosecond start/end times. Start the
benchmark after readiness and finish before scene end; compare the capture start
and per-event elapsed times to verify the window. Keep the client overlay enabled
consistently. Change **Full chroma (4:4:4)** in video/PyroWave settings and restart
SteamVR/client for negotiation; verify the overlay and negotiated OpenVR flag.
Restore it off after the experiment. Fresh event rate, instantaneous FPS percentiles,
source-timestamp gaps and ALVR estimated latency are distinct; optical latency needs
separate measurement.
