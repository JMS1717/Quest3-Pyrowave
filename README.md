# Quest3-Pyrowave

**PCVR streaming for Meta Quest 3, with PyroWave decoding directly on the Adreno GPU.**

An experimental ALVR fork for exploring high-quality, low-latency streaming over
USB and fast local networks. Vulkan handles wavelet decoding; ALVR supplies
SteamVR integration, tracking, controllers and audio.

[![Build](https://github.com/JMS1717/Quest3-Pyrowave/actions/workflows/ci.yml/badge.svg)](https://github.com/JMS1717/Quest3-Pyrowave/actions/workflows/ci.yml)
[![Preview release](https://img.shields.io/github/v/release/JMS1717/Quest3-Pyrowave?include_prereleases&label=preview&color=0070BA)](https://github.com/JMS1717/Quest3-Pyrowave/releases)
[![License](https://img.shields.io/badge/license-MIT-3B8C6E)](LICENSE)
[![Support development](https://img.shields.io/badge/Support_development-PayPal-0070BA?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/jasonselsley)

[**Download**](#download-a-matching-build) · [**Get started**](#quick-start) ·
[**Settings**](#settings-and-quality) · [**Performance**](#measured-performance) ·
[**Troubleshooting**](#troubleshooting) · [**Build / contribute**](#build-benchmark-and-contribute)

> **Research preview.** SteamVR video, audio and tracking work in tested setups.
> Short screens reach about 195 fresh frames per second at 207 Hz with full-resolution
> 2080 × 2208 per-eye streams. Sustained gameplay, optical motion-to-photon latency and an
> advantage over Virtual Desktop remain unproven. A selected refresh rate or an FPS counter
> does not establish that many distinct displayed frames.

## Current status

| | |
| --- | --- |
| **Latest preview: alpha.9 / `.62`** | A decoder about twice as fast as alpha.8's, any refresh rate from 144 to 240 Hz with PC-driven panel switching over USB, Decoder V2 for smoother CDF 5/3 images, bitrate up to 4000 Mbps, and two parallel wired video connections. [Release notes](docs/RELEASE-alpha.9.md) |
| **Best measured high-refresh setting** | **207 Hz · 2080 × 2208 per eye · Haar · 1000 Mbps · 4:2:0 · no foveation · maximum GPU clock over USB**: 194-197 fresh FPS in 10-12 s screens with a 60°/s pan. [Frame trace](docs/FRAME-TRACE.md) |
| **Smoothest image** | CDF 5/3 (Decoder V2) removes Haar's block edges. It costs about 2 fresh FPS at 700 Mbps and more at higher bitrates. [Decoder V2](docs/DECODER-V2.md) |
| **What limits 207 Hz** | Frames that finish too close to the display deadline, the eye copy's fill cost (about 0.8 ms), and a headset memory clock the app cannot control. [Details](docs/FRAME-TRACE.md#what-limits-207-hz-now) |
| **Fresh-install defaults** | Conservative 400 Mbps / 72 Hz candidate, Haar, 4:2:0, TCP, Quest 3 Auto → Compute. Pick a measured profile to go higher. |

## Why explore PyroWave?

| Capability | What it enables |
| --- | --- |
| **Quest GPU decode** | Custom Vulkan wavelet reconstruction on Adreno: Haar in about 2.6 ms and CDF 5/3 in about 3-4 ms per 2080 × 2208 stereo frame at 690 MHz, written straight into the buffer the eye pass reads. |
| **USB and Wi-Fi** | ALVR wired TCP forwarding or LAN streaming. UDP is a separate experimental path. |
| **High refresh** | Any whole rate from 144 to 240 Hz; the PC switches the Quest 3 panel over USB and restores it afterwards. |
| **Bitrate control** | A 5–4000 Mbps slider, a quality floor, measured profiles and latency-driven Auto bitrate. |
| **Independent render / encode sizes** | Render a larger PC source while keeping the Quest's decoded pixel count fixed. |
| **Chroma choices** | 4:2:0 by default; optional 4:4:4 for controlled quality comparisons. |
| **Local performance overlay** | A client-rendered 3D panel: both-thumbstick clicks cycle Compact / Full / Hidden; a hold opens settings. |
| **Reproducible research** | Pinned upstream sources, matching platform builds, regression checks and published measurements. |

Decoded Vulkan images cross an AHardwareBuffer bridge into GLES/OpenXR eye
images for presentation. [Pipeline and ownership details](docs/ARCHITECTURE.md).

PyroWave uses a custom wavelet codec and GPU reconstruction. H.264, HEVC and
AV1 PCVR paths use conventional video codecs and the headset's hardware video
decoder. High bitrate and full chroma are useful research options, but spare
bandwidth does not make PyroWave decoding free. This project has **not** established
a matched quality or latency advantage over those codecs or Virtual Desktop.

## Download a matching build

The latest published preview is **[v0.1.0-alpha.9](https://github.com/JMS1717/Quest3-Pyrowave/releases/tag/v0.1.0-alpha.9)**.

| Download | Purpose |
| --- | --- |
| [**Quest APK**](https://github.com/JMS1717/Quest3-Pyrowave/releases/download/v0.1.0-alpha.9/Quest3-Pyrowave-dev.apk) | Install on the Quest 3. |
| [**Windows server ZIP**](https://github.com/JMS1717/Quest3-Pyrowave/releases/download/v0.1.0-alpha.9/Quest3-Pyrowave-Windows.zip) | Dashboard, SteamVR driver and required bundled files. |
| [**SHA-256 checksums**](https://github.com/JMS1717/Quest3-Pyrowave/releases/download/v0.1.0-alpha.9/SHA256SUMS.txt) | Verify the downloads. |

Read the [alpha.9 release notes](docs/RELEASE-alpha.9.md) for what changed and what is
still opt-in. [alpha.8](https://github.com/JMS1717/Quest3-Pyrowave/releases/tag/v0.1.0-alpha.8)
remains available for rollback. For development builds, choose a **successful full run** in
[GitHub Actions](https://github.com/JMS1717/Quest3-Pyrowave/actions/workflows/ci.yml)
and download both `Quest3-Pyrowave-Android` and `Quest3-Pyrowave-Windows` artifacts
from that same run. Tests-only runs produce no installable pair.

**Do not mix an APK and server from different releases or runs.** Pull-request
APKs use temporary signing keys; main/release signing can differ. See
[APK signing and updates](docs/BUILD.md#android-apk) before switching builds.
Keep your previous matching pair for rollback.

## Quick start

### What you need

- **Meta Quest 3**, with Developer Mode and USB debugging enabled for sideloading.
- **Windows PC**, SteamVR and a Vulkan GPU/driver supporting the required
  Vulkan–D3D11 interop. SteamVR and the encoder must use the same GPU.
- **USB data cable** and preferably a USB 3 port, or a fast LAN with the PC
  connected to the router by Ethernet.

### Launch on PC and Quest

1. **Stop SteamVR** before changing server installations. Extract the Windows ZIP
   into a new folder so the previous installation stays available.
2. Run **`ALVR Dashboard.exe`** from the extracted folder. Register this server
   using its driver controls and enable the ALVR SteamVR add-on.
3. Install **`Quest3-Pyrowave-dev.apk`** with SideQuest or ADB. The app has its own
   package, `io.github.jms1717.quest3pyrowave`.
4. On Quest, open **Quest3 PyroWave** from **Unknown Sources**. On PC, explicitly
   trust the discovered headset in the dashboard, then start SteamVR.
5. In **Settings → Presets**, start with **Quest 3 PyroWave 400 Mbps / 72 Hz
   candidate**. Once that works over USB, choose a measured **Streaming profile**:
   **native 120 Hz** for the most frames per pixel, **207 Hz** for full resolution at
   high refresh, or **240 Hz scaled panel** for the highest rate at 1440 × 1536 per eye.

These are starting candidates, not guaranteed performance levels. The full
[installation and rollback guide](docs/BUILD.md#install-and-rollback) covers
prerequisites, controller emulation and restoring another driver.

### Choose your connection

| Connection | Setup | How to confirm it |
| --- | --- | --- |
| **USB / TCP** | Plug in a data cable; enable **Devices → Wired Connection**. Use **PyroWave TCP** and the custom client package above. | The wired client is Streaming, the server peer is `127.0.0.1`, and ADB forwards include ports 9943/9944. [USB guide](docs/USB.md) |
| **Wi-Fi / LAN** | PC and headset on the same LAN; prefer wired PC Ethernet and a nearby access point. | Confirm the active peer and measure actual video payload. [Bitrate and network budgets](docs/BITRATE.md) |

A connected cable alone does not prove the stream uses USB. USB link speed and
Wi-Fi PHY speed are also different from usable payload throughput. USB avoids
the radio hop, but still includes ADB forwarding, GPU decode and compositor work.

## Settings and quality

| Setting | Where / recommended starting choice |
| --- | --- |
| **Bitrate** | **Settings → Presets**. The slider sets a fixed payload cap; with Auto it sets a maximum. |
| **Auto bitrate** | Uses network/encoder latency feedback to lower the requested rate when needed. It cannot guarantee FPS or remove a GPU bottleneck. [Details](docs/BITRATE.md) |
| **Profiles** | **Settings → Presets → Streaming profile**: the three "(measured)" profiles set refresh, stream size, wavelet, chroma, bitrate and GPU clock together; the game render resolution is kept. Native 120 Hz (2064 × 2208, about 119 fresh FPS), 207 Hz (2080 × 2208, about 195) and 240 Hz scaled panel (1440 × 1536, about 223-228). [Measurements](docs/HIGH-REFRESH.md) |
| **Refresh** | 72-120 Hz, or any whole rate from 144 to 240 Hz with **Preferred FPS**; while streaming it applies by itself ([below](#settings-that-restart-steamvr)). Over USB the PC switches the panel: 144-207 Hz natively, above 207 Hz in the scaled panel mode (1552 × 1664 per eye). [Capability detection](docs/REFRESH-RATES.md) |
| **Wavelet** | **Haar** (default) is fastest. **CDF 5/3** uses Decoder V2 and gives smoother gradients without Haar's 8-pixel block edges, for a few fresh FPS; pair it with the maximum GPU clock. [Decoder V2](docs/DECODER-V2.md), [decoder findings](docs/DECODE-PIPELINE.md) |
| **GPU clock** | **Quest 3: maximum GPU clock** (690 MHz, over USB) helps whenever decoding limits the frame rate; the measured 207 and 240 Hz profiles turn it on. [Details](docs/HIGH-REFRESH.md) |
| **Wired video connections** | Two by default over USB: each frame is split across parallel adb-forwarded connections, which shortens network time at 1000 Mbps and above. 0 restores the single stream socket. [Details](docs/BITRATE.md) |
| **Chroma** | Keep **4:2:0** for the baseline. 4:4:4 increased decode cost in the recorded comparison. [Chroma comparison](docs/CHROMA.md) |
| **Foveation** | Off by default. [Light peripheral encoding](docs/LIGHT-FOVEATION.md) is optional development work, with sustained and in-headset acceptance pending. |
| **Overlay** | On by default. **Click both thumbsticks together**, release both, then click again to toggle. [Metrics and overrides](docs/OVERLAY.md) |
| **Controllers** | **Settings → Headset → Controllers → Emulation mode → Quest 3 Touch Plus**; SteamVR restarts to apply it. |

### Settings that restart SteamVR

Settings marked ⚠ (refresh, resolution, codec, wavelet, chroma, foveation, downsample filter,
controllers and the other SteamVR driver options) are read when a stream starts. Changed while
streaming, they apply by themselves: 2 s after the last edit the headset reconnects, and SteamVR
restarts when the driver needs it. The stream is back about 25 s after the edit, so save game
progress first. Bitrate changes apply live without a reconnect. [Details](docs/SETTINGS-APPLY.md)

The 600 / 800 / 1000 / 1500 / 2000 Mbps 120 Hz presets are experiments.
At 120 Hz, 1000 Mbps permits approximately **1.04 MB per encoded stereo frame**;
at 207 Hz it permits about **0.60 MB**. These are payload ceilings, not
guaranteed utilization or quality. More bitrate improves quality but costs decode time,
most of all with CDF 5/3: at 207 Hz, 5/3 measured 185 fresh FPS at 700 Mbps, 179 at
1000 and 164 at 1500. [Profile math and measured bitrate comparisons](docs/BITRATE.md).

Refresh rates above 120 Hz are runtime-gated; an accepted refresh does not imply an
equal fresh-frame rate. Rates above 120 Hz rely on the panel switch the PC applies over
USB (or `tools/quest3/refresh_scaling.py` by hand); do not assume every OS/runtime
exposes the same modes.

### Sharper PC source without a larger Quest decode

The two resolutions are independent:

| Per-eye size | Role |
| --- | --- |
| **SteamVR render recommendation** | Game source quality, subject to SteamVR percentages and game settings. |
| **PyroWave encoded resolution** | Stream dimensions and Quest decode workload. |

From the source checkout, with the matching server running:

```powershell
python -m tools.quest3.control resolution --profile supersampled3072
```

This requests **3072 × 3216** for the PC source while keeping encode/decode at
**2080 × 2208 per eye**. ALVR aligns the source recommendation to **3072 × 3232**.
Restart SteamVR afterward; games may need restarting too. Record the game's
actual render size rather than assuming it equals the recommendation.

Restore native geometry with `--profile native2080`. These controls preserve
bitrate, refresh, chroma and decoder settings. Apply overall dashboard presets
first, then independent geometry, since those presets can set both sizes together.
[Resolution semantics and A/B procedure](docs/RENDER-ENCODE-RESOLUTION.md).

## Measured performance

Short live screens on Quest 3 over USB, October 6-7, 2026. Unless noted:

- 2080 × 2208 per eye encoded from a 3072 × 3216 render, 4:2:0, no foveation
- maximum GPU clock (690 MHz)
- a 60°/s pan, ABBA order
- 10-12 s windows after a 3-5 s settle

Blocks whose headset memory clock changed mid-window were rejected.

| Setting | Fresh FPS | Notes |
| --- | --- | --- |
| **207 Hz, Haar, 1000 Mbps** | **194-197** | GPU decode p50 2.67 ms, eye copy 1.05-1.10 ms; frame age at display 30.4 ms p50. [Frame trace](docs/FRAME-TRACE.md) |
| 207 Hz, CDF 5/3, 700 Mbps | 185-193 | Two runs; in the matched comparison about 2 FPS below Haar. Visibly smoother gradients. [Decoder V2](docs/DECODER-V2.md) |
| 207 Hz, CDF 5/3, 1000 / 1500 Mbps | 179 / 164 | Quality rises about 1.8 dB PSNR-HVS-M from 700 to 1000 Mbps. [Bitrate](docs/BITRATE.md) |
| 240 Hz scaled panel, 1440 × 1536, Haar | 223-230 | Measured on October 6, before the faster decoder. [High refresh](docs/HIGH-REFRESH.md) |
| 120 Hz, 2064 × 2208, Haar | about 119 | Measured on October 6, before the faster decoder. |
| 207 Hz, 4:4:4 | 68 | 4:2:0 stays the default. [Chroma](docs/CHROMA.md) |

What the trace shows at 207 Hz:

- Decode keeps up: 203 of 206 frames per second are decoded and published.
- The rest are lost after publication:
  - The render loop occasionally misses a display period.
  - About 5 % of display intervals receive two frames, so one is superseded.
  - A few intervals receive none.
- Opt-in experiments trade latency for a few frames:
  - A release fence keeps every display period.
  - A short frame hold shows both frames of a burst.
  - Neither is a default.

These are short screening measurements, not sustained gameplay or optical
motion-to-photon tests. ALVR's estimated pipeline latency (about 30-33 ms at these
settings) is not a measured motion-to-photon result. The headset's memory clock
(2092 / 2736 / 3196 MHz) is not under app control and moves results by several FPS
between sessions. Read the [engineering handoff](docs/HANDOFF.md) and [reviewed results](results/).

## Troubleshooting

| Symptom | First action |
| --- | --- |
| **Dashboard will not open** | Launch it from the extracted server folder with its bundled files. Avoid mixing installations. |
| **Audio works, but video is black or corrupt** | Check APK/server provenance, selected codec and foveation settings. Preserve logs and report exact settings. |
| **USB is plugged in, but video uses Wi-Fi** | Check the wired peer and ADB forwards using the [USB verification steps](docs/USB.md). |
| **Game never appears in the headset** | Check SteamVR's active headset and the game's OpenXR runtime. [OpenXR setup and VD rollback](docs/OPENXR.md) |
| **FPS is below selected refresh** | Use a measured profile over USB with the maximum GPU clock. Compare fresh-frame delivery, GPU decode and completion timing; more bitrate or CDF 5/3 costs decode time. |
| **Overlay will not hide** | A benchmark's forced-visible override can take precedence over the controller chord. [Clear the override](docs/OVERLAY.md). |
| **Wrong controller model** | Select Quest 3 Touch Plus. Older saved sessions or game-specific meshes can differ. |
| **APK update fails** | Compare signing certificates; PR builds can use different keys. Follow the [signing guide](docs/BUILD.md#android-apk). |

**Returning to Virtual Desktop:** stop SteamVR, disable ALVR in SteamVR's
**Manage Add-ons**, and preserve Virtual Desktop's installation and registration.
If you changed OpenXR selection, follow the [runtime rollback guide](docs/OPENXR.md).
Keep the previous matching APK/server pair when changing project installations.

## Build, benchmark and contribute

| I want to… | Start here |
| --- | --- |
| **Build an APK and Windows server** | [Pinned build recipe](docs/BUILD.md), or run **Actions → Quest3-Pyrowave → Run workflow** in your fork. Full runs generate matching artifacts, hashes and license notices. |
| **Benchmark a change** | [Benchmarking guide](docs/BENCHMARKING.md): short controlled screens first, then sustained and image acceptance for promising candidates. |
| **Understand the pipeline** | [Architecture](docs/ARCHITECTURE.md), [decode pipeline](docs/DECODE-PIPELINE.md), [presentation research](docs/VULKAN-PRESENTATION.md). |
| **Continue development** | [Engineering handoff](docs/HANDOFF.md), [whole-stack scorecard](docs/WHOLE-STACK-SCORECARD.md) and [agent restrictions](AGENTS.md). |
| **Report a bug** | [Open an issue](https://github.com/JMS1717/Quest3-Pyrowave/issues) with build/run identity, GPU/driver, connection, per-eye sizes, refresh, bitrate and visible symptom. Remove secrets and private identifiers from logs. |

Pinned inputs live in [sources.lock.json](sources.lock.json), modifications in
`patches/`, native bridge code in `tools/pyroclient/`, Quest controls and analyzers
in `tools/quest3/`, and regression checks in `tests/`. Upstream trees are
reconstructed from these pinned inputs during builds.

Documentation-only updates do not create new APK/server artifacts. Build success,
on-device correctness, sustained performance and in-headset quality are separate
gates. Contributions should make clear which were actually checked.

## Credits and support

Maintained by **[JMS1717](https://github.com/JMS1717)**. Built on
[Terminal-ennui's Galaxy XR ALVR/PyroWave research](https://github.com/Terminal-ennui/galaxy-xr-alvr-pyrowave-444),
[ALVR](https://github.com/alvr-org/ALVR), and
[PyroWave](https://github.com/Themaister/pyrowave) / [Granite](https://github.com/Themaister/Granite)
by Hans-Kristian Arntzen (Themaister). Upstream authors retain credit for their work.

This project's changes are **MIT-licensed**; dependencies retain their own
licenses. See [LICENSE](LICENSE), [NOTICE](NOTICE) and the
[preserved upstream README](docs/UPSTREAM-README.md). Independent project;
not affiliated with Meta, Valve, Qualcomm or ALVR.

If this research is useful to you, help fund development and testing:

[![Support Quest3-Pyrowave with PayPal](https://img.shields.io/badge/Support_Quest3--Pyrowave-PayPal-0070BA?style=for-the-badge&logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/jasonselsley)
