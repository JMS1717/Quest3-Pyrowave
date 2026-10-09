<div align="center">

# Quest3-Pyrowave

**PCVR streaming for Meta Quest 3, with PyroWave wavelet decoding directly on the Adreno GPU.**

An experimental ALVR fork for high-quality, low-latency streaming over USB and fast local networks.
Vulkan handles wavelet decoding; ALVR supplies SteamVR integration, tracking, controllers and audio.

[![Build](https://github.com/JMS1717/Quest3-Pyrowave/actions/workflows/ci.yml/badge.svg)](https://github.com/JMS1717/Quest3-Pyrowave/actions/workflows/ci.yml)
[![Preview release](https://img.shields.io/github/v/release/JMS1717/Quest3-Pyrowave?include_prereleases&label=preview&color=0070BA)](https://github.com/JMS1717/Quest3-Pyrowave/releases)
[![License](https://img.shields.io/badge/license-MIT-3B8C6E)](LICENSE)
[![Support development](https://img.shields.io/badge/Support_development-PayPal-0070BA?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/jasonselsley)

[**Download**](#-download) ·
[**Quick start**](#-quick-start) ·
[**Profiles**](docs/PROFILES.md) ·
[**Settings**](#-settings-and-quality) ·
[**Performance**](#-measured-performance) ·
[**Troubleshooting**](#-troubleshooting) ·
[**Contribute**](#-build-benchmark-and-contribute)

</div>

> [!IMPORTANT]
> **Preview.** SteamVR video, audio and tracking work in tested setups over USB and Wi-Fi 6E.
> Over USB the recommended **Quality 120 Hz** profile holds about 111–120 fresh frames per second
> of 120 in short screens. Sustained gameplay, optical motion-to-photon latency and an advantage
> over Virtual Desktop are **not yet established**. A selected refresh rate or an FPS counter does
> not prove that many distinct frames reach your eyes.

## 📍 Current status

|  |  |
| --- | --- |
| **Latest release** | **beta.2 / `.135`**: a fresh install now streams **Quality 120 Hz**, the recommended profile. beta.1 added seven tested [streaming profiles](docs/PROFILES.md) with a guide, chosen and explained in the headset (hold both thumbsticks), full-size Godlike streams, 4:4:4 colour at 120 Hz, Quest colour by default, sharper PC downsampling, and no more drops to 10 FPS after a reconnect. [Release notes →](docs/RELEASE-beta.2.md) |
| **Recommended** | **Quality 120 Hz over USB**: 2272 × 2432 per eye (110 %), CDF 5/3, 1500 Mbps, Lanczos downsample, maximum GPU clock. 111–120 fresh FPS of 120. [All profiles →](docs/PROFILES.md) |
| **Most detail** | **Godlike 90 Hz**: the full 3072 × 3216 per eye with CDF 5/3 at 89–90 fresh FPS of 90. **Godlike 120 Hz** streams the same size with Haar at 116–118 fresh FPS of 120. |
| **Over Wi-Fi** | **Wi-Fi Quality 120 Hz** at 1000 Mbps on Wi-Fi 6E (6 GHz, PC on Ethernet); 1250 Mbps is the practical ceiling on the test link. **Wi-Fi 90 Hz** at 700 Mbps for ordinary Wi-Fi 6. [Wi-Fi →](docs/WIRELESS.md) |
| **Above 120 Hz** | **Competitive 207 Hz** reached 194–197 fresh FPS before Horizon OS build 209 (v2.9, October 2026), which holds the headset at 120 Hz or less. Use Quality 120 on that build. [High refresh →](docs/HIGH-REFRESH.md) |
| **Fresh-install defaults** | **Quality 120 Hz**, the recommended USB profile. On Wi-Fi, pick a Wi-Fi profile; if nothing streams, **Starter 72 Hz** (400 Mbps, panel resolution) works on any connection. |

## ✨ Why PyroWave?

| Capability | What it enables |
| --- | --- |
| 🎮 **Quest GPU decode** | Custom Vulkan wavelet reconstruction on Adreno: Haar in about 2.6 ms and CDF 5/3 in about 3–4 ms per 2080 × 2208 stereo frame at 690 MHz, written straight into the buffer the eye pass reads. |
| 🔌 **USB and Wi-Fi** | ALVR wired TCP forwarding or LAN streaming, both measured at 207 Hz. UDP is a separate experimental path. |
| ⚡ **High refresh** | Any whole rate from 144 to 240 Hz; over USB the PC switches the Quest 3 panel and restores it afterwards. |
| 📶 **Bitrate control** | A 5–4000 Mbps slider, a quality floor, measured profiles and latency-driven Auto bitrate. |
| 🖼️ **Independent render and encode sizes** | Render a sharper PC source while keeping the Quest's decoded pixel count fixed. |
| 🎨 **Chroma choices** | 4:2:0 by default; optional 4:4:4 for controlled quality comparisons. |
| 📊 **Performance overlay** | A client-rendered 3D panel: clicking both thumbsticks cycles Compact / Full / Hidden; a hold opens settings. |
| 🔬 **Reproducible research** | Pinned upstream sources, matching platform builds, regression checks and published measurements. |

### How a frame travels

```mermaid
flowchart LR
    subgraph PC["🖥️ Windows PC"]
        A[SteamVR frame] --> B[YCbCr planes] --> C[PyroWave encode<br/>Vulkan]
    end
    subgraph Link["🔌 Link"]
        D[USB: adb-forwarded TCP<br/>or Wi-Fi / LAN]
    end
    subgraph Quest["🥽 Quest 3"]
        E[Frame assembly] --> F[PyroWave decode<br/>Vulkan on Adreno] --> G[AHardwareBuffer] --> H[GLES eye pass<br/>YCbCr → RGB] --> I[OpenXR compositor]
    end
    C --> D --> E
```

Decoded frames cross an AHardwareBuffer bridge from Vulkan into the GLES/OpenXR eye images.
[Pipeline and ownership details →](docs/ARCHITECTURE.md)

PyroWave uses a custom wavelet codec and GPU reconstruction. H.264, HEVC and AV1 PCVR paths use
conventional codecs and the headset's hardware video decoder. High bitrate and full chroma are useful
research options, but spare bandwidth does not make PyroWave decoding free. This project has **not**
established a matched quality or latency advantage over those codecs or Virtual Desktop.

## 📦 Download

The current release is **[v0.1.0-beta.1](https://github.com/JMS1717/Quest3-Pyrowave/releases/tag/v0.1.0-beta.1)**.

| Download | Purpose |
| --- | --- |
| 🥽 [**Quest APK**](https://github.com/JMS1717/Quest3-Pyrowave/releases/download/v0.1.0-beta.1/Quest3-Pyrowave-dev.apk) | Install on the Quest 3. |
| 🖥️ [**Windows server ZIP**](https://github.com/JMS1717/Quest3-Pyrowave/releases/download/v0.1.0-beta.1/Quest3-Pyrowave-Windows.zip) | Dashboard, SteamVR driver and required bundled files. |
| 🔐 [**SHA-256 checksums**](https://github.com/JMS1717/Quest3-Pyrowave/releases/download/v0.1.0-beta.1/SHA256SUMS.txt) | Verify the downloads. |

Read the [beta.1 release notes](docs/RELEASE-beta.1.md) for what changed and the known issues. The
previous preview, [alpha.9](https://github.com/JMS1717/Quest3-Pyrowave/releases/tag/v0.1.0-alpha.9),
stays available for rollback. The APK is signed with the same key as alpha.9, so it installs over
it.

> [!WARNING]
> **Never mix an APK and server from different releases or runs.** Pull-request APKs use temporary
> signing keys, and main/release signing can differ. See
> [APK signing and updates](docs/BUILD.md#android-apk) before switching builds, and keep your
> previous matching pair for rollback.

<details>
<summary><b>Development builds from GitHub Actions</b></summary>

<br>

Choose a **successful full run** in
[GitHub Actions](https://github.com/JMS1717/Quest3-Pyrowave/actions/workflows/ci.yml) and download
both the `Quest3-Pyrowave-Android` and `Quest3-Pyrowave-Windows` artifacts from that same run.
Tests-only runs produce no installable pair.

</details>

## 🚀 Quick start

### What you need

- **Meta Quest 3** with Developer Mode and USB debugging enabled for sideloading.
- **Windows PC** with SteamVR and a Vulkan GPU/driver that supports the required Vulkan–D3D11
  interop. SteamVR and the encoder must use the same GPU.
- **USB data cable**, preferably on a USB 3 port, or a fast LAN with the PC on Ethernet.

### Launch on PC and Quest

1. **Stop SteamVR** before changing server installations. Extract the Windows ZIP into a new
   folder so the previous installation stays available.
2. Run **`ALVR Dashboard.exe`** from the extracted folder. Register this server with its driver
   controls and enable the ALVR SteamVR add-on.
3. Install **`Quest3-Pyrowave-dev.apk`** with SideQuest or ADB. The app has its own package,
   `io.github.jms1717.quest3pyrowave`.
4. On the Quest, open **Quest3 PyroWave** from **Unknown Sources**. On the PC, explicitly trust the
   discovered headset in the dashboard, then start SteamVR.
5. The fresh install streams **Quality 120 Hz**, the recommended profile for USB. To change it,
   **hold both thumbsticks** in the headset for about 0.7 s, choose **Profile**, then **Apply**; or
   on the PC, **Settings → Presets → Streaming profile**. On Wi-Fi, pick **Wi-Fi Quality 120 Hz**
   or **Wi-Fi 90 Hz**. If the stream does not start or stutters, **Starter 72 Hz** works on any
   connection.

   | Profile | Per-eye stream | Pick it for |
   | --- | --- | --- |
   | **Quest 3 Quality 120 Hz** (recommended) | 2272 × 2432 (110 %), CDF 5/3, 1500 Mbps | USB: the best balance of detail, smoothness and latency |
   | **Quest 3 Godlike 90 Hz** | 3072 × 3216 (full size), CDF 5/3, 1500 Mbps | USB: the cleanest full-size image; sims, racing, slow games |
   | **Quest 3 Godlike 120 Hz** | 3072 × 3216, Haar, 1500 Mbps | USB: the most detail at 120 Hz, a few FPS short of 120; faint blocks in gradients |
   | **Quest 3 Colour 4:4:4 120 Hz** | 110 %, Haar, full colour, 1500 Mbps | USB: crisp coloured text, menus and cockpits |
   | **Quest 3 Wi-Fi Quality 120 Hz** | 110 %, 1000 Mbps | Wi-Fi 6E (6 GHz) with the PC on Ethernet |
   | **Quest 3 Wi-Fi 90 Hz** | panel size, 700 Mbps | Ordinary Wi-Fi 6 (5 GHz) |
   | **Quest 3 Competitive 207 Hz** | 2080 × 2208, 1000 Mbps | USB: lowest latency, where Horizon OS allows 207 Hz |

   Then set **Game render resolution** to 150 % or more if your PC holds the frame rate. The
   [profile guide](docs/PROFILES.md) explains what each profile and setting does and which is
   better for what.

These are tested starting points, not guaranteed performance levels: a heavy game or a weaker PC
or link gives less. The full [installation and rollback guide](docs/BUILD.md#install-and-rollback)
covers prerequisites, controller emulation and restoring another driver.

### Choose your connection

| Connection | Setup | How to confirm it |
| --- | --- | --- |
| 🔌 **USB / TCP** | Plug in a data cable and enable **Devices → Wired Connection**. Use **PyroWave TCP** and the custom client package above. | The wired client is Streaming, the server peer is `127.0.0.1`, and ADB forwards include ports 9943/9944. [USB guide →](docs/USB.md) |
| 📡 **Wi-Fi / LAN** | PC on Ethernet, headset on a nearby 6 GHz (Wi-Fi 6E) access point. Trust the headset in **Devices**, or add its IP address. Use a constant **1000 Mbps** (1250 at most), or Auto with a maximum. | The client is Streaming from the headset's LAN address. Watch the overlay's network latency. [Wi-Fi measurements →](docs/WIRELESS.md) |

> [!NOTE]
> A connected cable alone does not prove the stream uses USB, and USB link speed or Wi-Fi PHY speed
> is not usable payload throughput. USB avoids the radio hop but still includes ADB forwarding, GPU
> decode and compositor work.

## 🔧 Settings and quality

| Setting | Where and what to start with |
| --- | --- |
| **Bitrate** | **Settings → Presets.** The slider sets a fixed payload cap; with Auto it sets a maximum. PyroWave keeps a quality floor of 0.25 bits per stream pixel (about 500 Mbps at 207 Hz with 2080 × 2208 per eye) and raises lower settings to it. [Details](docs/BITRATE.md) |
| **Auto bitrate** | Lowers the requested rate from network/encoder latency feedback. The quality floor raises its estimate, but the network-latency and maximum limits win, so on a congested link it goes lower instead of queuing frames. On Wi-Fi 6E it settled at about 550 Mbps with an 8 ms limit, which is conservative. It cannot guarantee FPS or remove a GPU bottleneck. |
| **Profiles** | **Settings → Presets → Streaming profile**, or **Profile** in the headset menu. Each sets refresh, stream size, wavelet, chroma, bitrate, filter, sharpening and GPU clock together and keeps the game's render resolution. The headset menu explains the selected profile in two lines. [Profile guide](docs/PROFILES.md) |
| **Refresh** | 72–120 Hz, or any whole rate from 144 to 240 Hz with **Preferred FPS**. Over USB the PC switches the panel: 144–207 Hz natively, above 207 Hz in the scaled panel mode (1552 × 1664 per eye). [Capability detection](docs/REFRESH-RATES.md) |
| **Wavelet** | **CDF 5/3** (the product profiles) uses Decoder V2 and gives smooth gradients without Haar's 8-pixel block edges; pair it with the maximum GPU clock. **Haar** decodes fastest (the Colour profile uses it to fit 4:4:4). **CDF 9/7** (the Starter fallback) is smoothest and slowest. [Decoder V2](docs/DECODER-V2.md) · [decoder findings](docs/DECODE-PIPELINE.md) |
| **GPU clock** | **Quest 3: maximum GPU clock** (690 MHz) helps whenever decoding limits the frame rate. Every USB profile turns it on. The PC applies it, and the panel switch, over USB adb only. [Details](docs/HIGH-REFRESH.md) |
| **Wired video connections** | Four in the USB profiles (two by default): each frame is split across parallel adb-forwarded connections, which shortens network time at 1000 Mbps and above. **0** restores the single stream socket. [Details](docs/BITRATE.md) |
| **Direct eye copy** | On by default since `.117`: decoded frames are drawn straight into the headset's eye images, which saves 3 ms of GPU time a frame against ALVR's staging renderer. `adb shell setprop debug.q3pw.direct_eye_copy 0`, then force-stop and reopen the app, restores staging for comparisons. [Frame trace](docs/FRAME-TRACE.md) |
| **Chroma** | **4:2:0** in every profile but Colour. **4:4:4** keeps coloured text and thin coloured lines crisp; at 110 % and 120 Hz it holds 118–120 fresh FPS with Haar. [Chroma comparison](docs/CHROMA.md) |
| **Foveation** | Off by default. [Light peripheral encoding](docs/LIGHT-FOVEATION.md) is optional development work; sustained and in-headset acceptance are pending. |
| **Overlay** | On by default, in Compact mode. **Click both thumbsticks together** and release to cycle Compact → Full → Hidden. **Hold both** for about 0.7 s to open the headset settings menu: profile, refresh, bitrate, stream and render size, chroma and overlay, each with a short explanation. [Metrics and overrides](docs/OVERLAY.md) |
| **Controllers** | **Settings → Headset → Controllers → Emulation mode → Quest 3 Touch Plus.** SteamVR restarts to apply it. |

### Settings that restart SteamVR

Settings marked ⚠ (refresh, resolution, codec, wavelet, chroma, foveation, downsample filter,
controllers and the other SteamVR driver options) are read when a stream starts. If you change them
while streaming, they apply by themselves: 2 s after the last edit the headset reconnects, and SteamVR
restarts when the driver needs it. The stream returns about 25 s after the edit, so **save game
progress first**. Bitrate changes apply live without a reconnect. [Details](docs/SETTINGS-APPLY.md)

<details>
<summary><b>Bitrate budgets</b></summary>

<br>

| Refresh | Payload ceiling per stereo frame at 1000 Mbps |
| --- | --- |
| 90 Hz | about **1.39 MB** |
| 120 Hz | about **1.04 MB** |
| 207 Hz | about **0.60 MB** |

These are payload ceilings, not guaranteed utilization or quality. More bitrate improves quality but
costs decode time, most of all with CDF 5/3: at 207 Hz, 5/3 measured 185 fresh FPS at 700 Mbps, 179
at 1000 and 164 at 1500. [Every profile's frame budget](docs/BITRATE.md)

Refresh rates above 120 Hz are runtime-gated, and an accepted refresh does not imply an equal
fresh-frame rate. Rates above 120 Hz rely on the panel switch the PC applies over USB (or
`tools/quest3/refresh_scaling.py` by hand); do not assume every OS/runtime exposes the same modes.

</details>

<details>
<summary><b>Sharper PC source without a larger Quest decode</b></summary>

<br>

The two resolutions are independent:

| Per-eye size | Role |
| --- | --- |
| **SteamVR render recommendation** | Game source quality, subject to SteamVR percentages and game settings. |
| **PyroWave encoded resolution** | Stream dimensions and Quest decode workload. |

From the source checkout, with the matching server running:

```powershell
python -m tools.quest3.control resolution --profile supersampled3072
```

This requests **3072 × 3216** for the PC source while keeping encode/decode at **2080 × 2208 per
eye**. ALVR aligns the source recommendation to 3072 × 3232. Restart SteamVR afterwards; games may
need restarting too. Record the game's actual render size rather than assuming it equals the
recommendation.

Restore native geometry with `--profile native2080`. These controls keep bitrate, refresh, chroma
and decoder settings. Apply overall dashboard presets first, then independent geometry, since those
presets can set both sizes together.
[Resolution semantics and A/B procedure](docs/RENDER-ENCODE-RESOLUTION.md)

</details>

## 📊 Measured performance

Short live screens on Quest 3 over USB (Wi-Fi where noted), October 6–7, 2026. Unless noted, each
used 2080 × 2208 per eye encoded from a 3072 × 3216 render, 4:2:0, no foveation, the maximum GPU clock (690 MHz), the
direct eye copy (`debug.q3pw.direct_eye_copy=1`, see [Settings](#-settings-and-quality)), a 60°/s pan in ABBA order, and 10–12 s windows after a 3–5 s settle. Blocks whose headset memory clock
changed mid-window were rejected.

| Setting | Fresh FPS | Notes |
| --- | ---: | --- |
| **207 Hz · Haar · 1000 Mbps** | **194–197** | GPU decode p50 2.67 ms, eye copy 1.05–1.10 ms, frame age at display 30.4 ms p50. [Frame trace](docs/FRAME-TRACE.md) |
| 207 Hz · Haar · 1000 Mbps · **Wi-Fi 6E** | 189–195 | Network stage 6.1 ms p50 (USB: about 2.7). 1250 Mbps: 190–192 with worse tails; 1500 Mbps overloads the link. [Wi-Fi](docs/WIRELESS.md) |
| 207 Hz · CDF 5/3 · 700 Mbps | 185–193 | Two runs; about 2 FPS below Haar in the matched comparison. Visibly smoother gradients. [Decoder V2](docs/DECODER-V2.md) |
| 207 Hz · CDF 5/3 · 1000 / 1500 Mbps | 179 / 164 | Quality rises about 1.8 dB PSNR-HVS-M from 700 to 1000 Mbps. [Bitrate](docs/BITRATE.md) |
| 240 Hz scaled panel · 1440 × 1536 · Haar | 223–230 | October 6, before the faster decoder. [High refresh](docs/HIGH-REFRESH.md) |
| 120 Hz · 2064 × 2208 · Haar | about 119 | October 6, before the faster decoder. |
| 207 Hz · 4:4:4 | 68 | 4:2:0 stays the default. [Chroma](docs/CHROMA.md) |

**What the trace shows at 207 Hz.** Decode keeps up: 203 of 206 frames per second are decoded and
published. The rest are lost after publication. The render loop occasionally misses a display
period, about 5 % of display intervals receive two frames (so one is superseded), and a few receive
none. Opt-in experiments trade latency for a few frames. A release fence keeps every display period,
and a short frame hold shows both frames of a burst. Neither is a default.

> [!CAUTION]
> These are short screening measurements, not sustained gameplay or optical motion-to-photon tests.
> ALVR's estimated pipeline latency (about 30–33 ms at these settings) is not a measured
> motion-to-photon result. The headset's memory clock (2092 / 2736 / 3196 MHz) is not under app
> control and moves results by several FPS between sessions. See the
> [engineering handoff](docs/HANDOFF.md) and the [reviewed results](results/).

## 🧰 Troubleshooting

| Symptom | First action |
| --- | --- |
| **Dashboard will not open** | Launch it from the extracted server folder with its bundled files. Avoid mixing installations. |
| **Audio works, but video is black or corrupt** | Check APK/server provenance, the selected codec and foveation settings. Keep the logs and report the exact settings. |
| **Wi-Fi stream lags more and more** | The bitrate is above what the link delivers, and TCP queues the excess. Lower it to 1000 Mbps or use Auto with a maximum. [Wi-Fi](docs/WIRELESS.md) |
| **USB is plugged in, but video uses Wi-Fi** | Check the wired peer and ADB forwards with the [USB verification steps](docs/USB.md). |
| **Video stops after unplugging or replugging USB** | Replugging with two wired video connections has not been checked yet. Stop SteamVR, reconnect the cable and confirm `adb devices` lists the headset before restarting. If it recurs, set **Wired video connections** to **0** and [report it](https://github.com/JMS1717/Quest3-Pyrowave/issues). |
| **Game never appears in the headset** | Check SteamVR's active headset and the game's OpenXR runtime. [OpenXR setup and VD rollback](docs/OPENXR.md) |
| **Stream drops to about 10 FPS** a few seconds after connecting or applying a profile | Fixed in `.134`: a reconnect no longer tells SteamVR the headset was taken off. Update both the APK and the server. |
| **FPS is below the selected refresh** | Use a USB profile (they turn on the maximum GPU clock); Godlike 120 runs a few FPS below 120 by design. Compare fresh-frame delivery, GPU decode and completion timing; more bitrate or CDF 5/3 costs decode time. |
| **Overlay will not hide** | A benchmark's forced-visible override can take precedence over the controller chord. [Clear the override](docs/OVERLAY.md) |
| **Wrong controller model** | Select Quest 3 Touch Plus. Older saved sessions or game-specific meshes can differ. |
| **APK update fails** | Compare signing certificates; PR builds can use different keys. Follow the [signing guide](docs/BUILD.md#android-apk). |

> [!TIP]
> **Returning to Virtual Desktop:** stop SteamVR, disable ALVR in SteamVR's **Manage Add-ons**, and
> leave Virtual Desktop's installation and registration as they are. If you changed the OpenXR
> runtime, follow the [runtime rollback guide](docs/OPENXR.md). Keep the previous matching APK/server
> pair when changing project installations.

## 🧪 Build, benchmark and contribute

| I want to… | Start here |
| --- | --- |
| **Build an APK and Windows server** | The [pinned build recipe](docs/BUILD.md). On a set-up Windows PC, [local fast builds](docs/LOCAL-BUILD.md) take 1–3 minutes against about 20 on CI; releases are built this way. **Actions → Quest3-Pyrowave → Run workflow** in your fork also produces a matching pair with hashes and license notices. |
| **Benchmark a change** | The [benchmarking guide](docs/BENCHMARKING.md): short controlled screens first, then sustained and image acceptance for promising candidates. |
| **Understand the pipeline** | [Architecture](docs/ARCHITECTURE.md) · [decode pipeline](docs/DECODE-PIPELINE.md) · [presentation research](docs/VULKAN-PRESENTATION.md) |
| **Continue development** | [Engineering handoff](docs/HANDOFF.md) · [whole-stack scorecard](docs/WHOLE-STACK-SCORECARD.md) · [agent guide](AGENTS.md) |
| **Report a bug** | [Open an issue](https://github.com/JMS1717/Quest3-Pyrowave/issues) with the build/run identity, GPU and driver, connection, per-eye sizes, refresh, bitrate and the visible symptom. Remove secrets and private identifiers from logs. |

<details>
<summary><b>Repository layout</b></summary>

<br>

| Path | Contents |
| --- | --- |
| [`sources.lock.json`](sources.lock.json) | Pinned upstream inputs (ALVR, PyroWave, Granite) |
| `patches/` | All modifications to the upstream trees |
| `tools/pyroclient/` | Native Vulkan decode bridge for the Quest client |
| `tools/quest3/` | Quest controls, trace analyzers and probes |
| `tests/` | Regression checks run in CI |
| `docs/` | Design notes, measurements and release notes |

Upstream trees are reconstructed from these pinned inputs during builds.

</details>

Documentation-only changes do not produce new APK/server artifacts. Build success, on-device
correctness, sustained performance and in-headset quality are separate gates, and contributions
should say which of them were actually checked.

## 🙏 Credits and support

Maintained by **[JMS1717](https://github.com/JMS1717)**. Built on
[Terminal-ennui's Galaxy XR ALVR/PyroWave research](https://github.com/Terminal-ennui/galaxy-xr-alvr-pyrowave-444),
[ALVR](https://github.com/alvr-org/ALVR), and [PyroWave](https://github.com/Themaister/pyrowave) /
[Granite](https://github.com/Themaister/Granite) by Hans-Kristian Arntzen (Themaister). Upstream
authors retain credit for their work.

This project's changes are **MIT-licensed**; dependencies keep their own licenses. See
[LICENSE](LICENSE), [NOTICE](NOTICE) and the [preserved upstream README](docs/UPSTREAM-README.md).
This is an independent project, not affiliated with Meta, Valve, Qualcomm or ALVR.

<div align="center">

If this research is useful to you, please help fund development and testing:

[![Support Quest3-Pyrowave with PayPal](https://img.shields.io/badge/Support_Quest3--Pyrowave-PayPal-0070BA?style=for-the-badge&logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/jasonselsley)

</div>
