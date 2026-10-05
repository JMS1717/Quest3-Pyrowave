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

> **Research preview.** SteamVR video, audio and tracking have worked in tested
> setups. Sustained native-resolution 120 fresh FPS and lower latency than
> Virtual Desktop remain unproven. A selected refresh rate or a 120 FPS counter
> does not establish 120 distinct displayed frames.

## Current status

| Track | What to expect |
| --- | --- |
| **Published preview: alpha.8 / `.51`** | Matching APK and Windows server with bounded frame waiting, automatic decode priority, independent resolution controls and safety fixes. [Release notes](docs/RELEASE-alpha.8.md) |
| **Development: `.51`** | [PR #3](https://github.com/JMS1717/Quest3-Pyrowave/pull/3) merged native lifetime fixes, publication tests and producer diagnostics. [All four CI jobs passed](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37327925015) for the integrated runtime sources. |
| **Working experimental target** | **2080 × 2208 per eye · 120 Hz · 1000 Mbps · 4:2:0 · no foveated encoding**, with Vulkan Compute decoding. Sustained acceptance is still pending. |
| **Defaults preserved** | Conservative 400 Mbps / 72 Hz candidate; 4:2:0, TCP and Quest 3 Auto → Compute. Development keeps synchronous decoding and the 4 ms selection wait. |

Publication notifications, prerecording and experimental fence handoffs remain
off by default. The new Haar H2 and final-color fusion candidates are excluded
from this integration; they require further correctness work. See the
[integration review](docs/PR-3-REVIEW.md) and [whole-stack scorecard](docs/WHOLE-STACK-SCORECARD.md).

## Why explore PyroWave?

| Capability | What it enables |
| --- | --- |
| **Quest GPU decode** | Custom Vulkan wavelet reconstruction on Adreno, with measured decode and completion times. |
| **USB and Wi-Fi** | ALVR wired TCP forwarding or LAN streaming. UDP is a separate experimental path. |
| **Bitrate control** | A 5–2000 Mbps slider, high-bitrate presets and latency-driven Auto bitrate. |
| **Independent render / encode sizes** | Render a larger PC source while keeping the Quest's decoded pixel count fixed. |
| **Chroma choices** | 4:2:0 by default; optional 4:4:4 for controlled quality comparisons. |
| **Local performance overlay** | A client-rendered 3D panel, toggled with both thumbsticks. |
| **Reproducible research** | Pinned upstream sources, matching platform builds, regression checks and published measurements. |

Decoded Vulkan images cross an AHardwareBuffer bridge into GLES/OpenXR eye
images for presentation. [Pipeline and ownership details](docs/ARCHITECTURE.md).

PyroWave uses a custom wavelet codec and GPU reconstruction. H.264, HEVC and
AV1 PCVR paths use conventional video codecs and the headset's hardware video
decoder. High bitrate and full chroma are useful research options, but spare
bandwidth does not make PyroWave decoding free. This project has **not** established
a matched quality or latency advantage over those codecs or Virtual Desktop.

## Download a matching build

The latest published preview is **[v0.1.0-alpha.8](https://github.com/JMS1717/Quest3-Pyrowave/releases/tag/v0.1.0-alpha.8)**.

| Download | Purpose |
| --- | --- |
| [**Quest APK**](https://github.com/JMS1717/Quest3-Pyrowave/releases/download/v0.1.0-alpha.8/Quest3-Pyrowave-dev.apk) | Install on the Quest 3. |
| [**Windows server ZIP**](https://github.com/JMS1717/Quest3-Pyrowave/releases/download/v0.1.0-alpha.8/Quest3-Pyrowave-Windows.zip) | Dashboard, SteamVR driver and required bundled files. |
| [**SHA-256 checksums**](https://github.com/JMS1717/Quest3-Pyrowave/releases/download/v0.1.0-alpha.8/SHA256SUMS.txt) | Verify the downloads. |

Read the [alpha.8 release notes](docs/RELEASE-alpha.8.md) for its setup and optional
direct-eye-copy mode. For development builds, choose a **successful full run** in
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
   candidate**. Once that works, try the 600 Mbps / 90 Hz candidate before the
   higher-rate experiments.

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
| **Refresh** | Select a runtime-confirmed mode. Restart SteamVR after changing refresh. [Capability detection](docs/REFRESH-RATES.md) |
| **Decoder / wavelet** | Quest 3 Auto chooses Compute. Haar/Compute is the measured development recipe; follow the matching build's instructions. [Decoder findings](docs/DECODE-PIPELINE.md) |
| **Chroma** | Keep **4:2:0** for the baseline. 4:4:4 increased decode cost in the recorded comparison. [Chroma comparison](docs/CHROMA.md) |
| **Foveation** | Off by default. [Light peripheral encoding](docs/LIGHT-FOVEATION.md) is optional development work, with sustained and in-headset acceptance pending. |
| **Overlay** | On by default. **Click both thumbsticks together**, release both, then click again to toggle. [Metrics and overrides](docs/OVERLAY.md) |
| **Controllers** | **Settings → Headset → Controllers → Emulation mode → Quest 3 Touch Plus**; restart SteamVR. |

The 600 / 800 / 1000 / 1500 / 2000 Mbps high-refresh profiles are experiments.
At 120 Hz, 1000 Mbps permits approximately **1.04 MB per encoded stereo frame**;
2000 Mbps permits approximately **2.08 MB**. These are payload ceilings, not
guaranteed utilization or quality. More bitrate can increase decode and network
cost. [Profile math and measured bitrate comparisons](docs/BITRATE.md).

90/120 Hz and extended 144/207 Hz requests are runtime-gated. An accepted refresh
does not imply an equal fresh-frame rate. Experimental 240 Hz was rejected on
the tested setup; do not assume every OS/runtime exposes the same modes.

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

Recent stationary native-resolution USB screens used **120 Hz, 1000 Mbps,
4:2:0, no foveation, Haar/Compute, one synchronous worker and a 4 ms selection wait**.

| Observation | Evidence / interpretation |
| --- | --- |
| **About 117 distinct targets/s** | Recent `.48`/`.50` screens, despite an ALVR client counter around 120. Sustained fresh 120 remains unmet. [Whole-stack scorecard](docs/WHOLE-STACK-SCORECARD.md) |
| **GPU decode p50 5.90 ms; conversion p50 0.77 ms** | `.50` classification window. Native decode-to-fence p50 was 7.98 ms; these measure different intervals. [Stage findings](docs/WHOLE-STACK-SCORECARD.md#decision) |
| **PC source about 120.7 frames/s** | Two `.48` control windows, with encode median about 2.1 ms and network estimate 4.3–4.5 ms. These are not additive optical latency measurements. [Stage breakdown](docs/WHOLE-STACK-SCORECARD.md#what-is-measured) |
| **4:4:4 was substantially slower** | Earlier matched comparison: about 100 fresh FPS at 1000 Mbps / 4:2:0 versus 66 at 2000 Mbps / 4:4:4. Keep 4:2:0 as default. [Full comparison](docs/CHROMA.md) |
| **Larger PC source, unchanged Quest decode** | Separation worked in short native/larger/native screens; no sustained FPS or repeatable quality/latency win established. [Resolution experiment](docs/RENDER-ENCODE-RESOLUTION.md#first-controlled-quest-3-screen) |

These are short screening measurements, not sustained gameplay, optical FPS or
motion-to-photon tests. Earlier pacing screens still had p1 near 60. ALVR's
estimated pipeline latency is not a measured motion-to-photon result.

In these captures, frames were still decoding when selection expired, while
the PC supplied enough frames. Longer waits, publication notifications and prerecording
did not establish a delivery gain; defaults stay unchanged. New shader candidates
need correctness evidence before promotion. Read the
[whole-stack scorecard](docs/WHOLE-STACK-SCORECARD.md),
[producer findings](docs/PRODUCER-PRERECORD.md) and [reviewed results](results/).

## Troubleshooting

| Symptom | First action |
| --- | --- |
| **Dashboard will not open** | Launch it from the extracted server folder with its bundled files. Avoid mixing installations. |
| **Audio works, but video is black or corrupt** | Check APK/server provenance, selected codec and foveation settings. Preserve logs and report exact settings. |
| **USB is plugged in, but video uses Wi-Fi** | Check the wired peer and ADB forwards using the [USB verification steps](docs/USB.md). |
| **Game never appears in the headset** | Check SteamVR's active headset and the game's OpenXR runtime. [OpenXR setup and VD rollback](docs/OPENXR.md) |
| **FPS is below selected refresh** | Compare fresh-frame delivery, GPU decode and completion timing. Increasing bitrate alone may make it worse. |
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
