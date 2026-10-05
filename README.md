# Quest3-Pyrowave

**Experimental PCVR streaming for Meta Quest 3, powered by PyroWave and ALVR.**

Decode PyroWave directly on the Quest's Adreno GPU using Vulkan. Connect over
USB or Wi-Fi, tune the stream, and measure where every millisecond goes.

[![Build](https://github.com/JMS1717/Quest3-Pyrowave/actions/workflows/ci.yml/badge.svg)](https://github.com/JMS1717/Quest3-Pyrowave/actions/workflows/ci.yml)
[![Preview release](https://img.shields.io/github/v/release/JMS1717/Quest3-Pyrowave?include_prereleases&label=preview&color=0070BA)](https://github.com/JMS1717/Quest3-Pyrowave/releases)
[![License](https://img.shields.io/badge/license-MIT-3B8C6E)](LICENSE)
[![Support development](https://img.shields.io/badge/Support_development-PayPal-0070BA?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/jasonselsley)

[**Download preview**](https://github.com/JMS1717/Quest3-Pyrowave/releases/tag/v0.1.0-alpha.7)
 · [**Quick start**](#quick-start)
 · [**USB setup**](docs/USB.md)
 · [**Settings**](#settings-youll-use)
 · [**Benchmarking**](docs/BENCHMARKING.md)
 · [**Report a problem**](https://github.com/JMS1717/Quest3-Pyrowave/issues)

[Latest manual playtest and setup fixes](docs/PLAYTEST-2026-10-02.md): optional
larger PC source, mild sharpening, the overlay override fix and the OpenXR game
launch fix. The latest reviewed development pair is `.42`, with optional decode
stage and Surface diagnostics off by default. It retains the `.33` improvements: wait up to half a frame
for a frame that is still decoding ([measured gain](docs/FRESHNESS.md)) and at
≤120 Hz / 4:2:0 lowers decode priority so the eye copy is not stuck behind it
([measured gain](docs/DECODE-PRIORITY.md)). Dedicated [Haar kernels](docs/HAAR-PAIRS.md)
passed exact GPU checks and lower transform cost, but remain optional: repeated
short screens still miss some 120 Hz intervals. The published preview remains
alpha.7. These notes do not establish sustained fresh 120 FPS.

> **Research preview, not a finished Virtual Desktop replacement.** Real SteamVR
> video, audio and tracking work in tested setups. Sustained 120 fresh FPS at
> native resolution, broad gameplay compatibility and lower latency than Virtual
> Desktop are still goals. Start with the published matching APK/server pair.

[Developer / AI handoff](docs/HANDOFF.md): current bottleneck, source layout,
pending fence experiment, validation and continuation guide.

## What you get

| Feature | What it does |
| --- | --- |
| GPU PyroWave decoding | Vulkan wavelet reconstruction, with an AHardwareBuffer bridge into the Quest's GLES/OpenXR eye images |
| USB and wireless PCVR | ALVR's wired TCP mode, wireless TCP, and an experimental UDP transport |
| High bitrate controls | 300-2000 Mbps targets, a bitrate slider, and latency-driven Auto bitrate |
| Full-frame default | 4:2:0 by default; optional 4:4:4. Development `.28` also offers [light peripheral encoding](docs/LIGHT-FOVEATION.md); client foveation stays disabled |
| Independent resolutions | Increase PC source rendering while keeping Quest decode at 2080x2208 per eye |
| In-headset stats | A 3D performance panel, toggled by clicking both thumbsticks together |
| Reproducible experiments | Pinned source inputs, matching APK/Windows builds, short benchmarks and published findings |

## Quick start

### 1. Download a matching pair

The published preview is **[v0.1.0-alpha.7](https://github.com/JMS1717/Quest3-Pyrowave/releases/tag/v0.1.0-alpha.7)**,
containing matching `.15` binaries. Download:

| File | Use |
| --- | --- |
| [`Quest3-Pyrowave-dev.apk`](https://github.com/JMS1717/Quest3-Pyrowave/releases/download/v0.1.0-alpha.7/Quest3-Pyrowave-dev.apk) | Quest app |
| [`Quest3-Pyrowave-Windows.zip`](https://github.com/JMS1717/Quest3-Pyrowave/releases/download/v0.1.0-alpha.7/Quest3-Pyrowave-Windows.zip) | PC dashboard and SteamVR driver |
| [`SHA256SUMS.txt`](https://github.com/JMS1717/Quest3-Pyrowave/releases/download/v0.1.0-alpha.7/SHA256SUMS.txt) | Download checksums |

**Keep APK and server from the same release or Actions run.** Newer development
artifacts are available in [successful Actions runs](https://github.com/JMS1717/Quest3-Pyrowave/actions/workflows/ci.yml).
They are experimental; the newest build is not automatically the fastest.
Read the [preview release notes](docs/RELEASE-alpha.7.md) before enabling optional paths.

### 2. Prepare the PC and headset

You need a **Quest 3 with Developer Mode and USB debugging**, a Windows PC with
SteamVR and a compatible Vulkan GPU/driver, and either a USB data cable or a fast
local network. PyroWave's Windows path also requires working Vulkan/D3D11 GPU
interop. Use the same GPU for SteamVR and encoding.

1. Stop SteamVR before replacing a server. Extract the Windows ZIP into a **new
   folder**, preserving your previous installation.
2. Run **`ALVR Dashboard.exe`** from that folder. Register this server with its
   driver controls and enable the ALVR SteamVR add-on.
3. Install the APK with SideQuest or ADB. Its separate package is
   `io.github.jms1717.quest3pyrowave`; keep other VR apps installed.
4. On Quest, open **Quest3 PyroWave** from **Unknown Sources**. On PC, explicitly
   trust the discovered headset in the dashboard, then start SteamVR.

For USB, connect a USB 3 data cable and enable **Devices -> Wired Connection**.
Use **PyroWave TCP**. [USB setup and connection verification](docs/USB.md).
For Wi-Fi, use the same LAN and preferably wired Ethernet from PC to router.
Network link speed is not the stream's usable payload throughput.

[Full installation, prerequisites and rollback](docs/BUILD.md) ·
[Launch and configure the preview](docs/RELEASE-alpha.6.md#launch-and-configure)

### 3. Start conservatively, then increase quality

Choose **Quest 3 PyroWave 400 Mbps / 72 Hz candidate** in Settings -> Presets.
It retains full panel-relative resolution, **4:2:0**, TCP and Auto decode.
The next candidate is 600 Mbps /90 Hz. These are starting configurations,
not a guarantee of performance on every PC or network.

The development target is **2080x2208 per eye /120 Hz /1000 Mbps /4:2:0 /no
foveation**. That target remains experimental. The faster measured development
recipe uses Haar/Compute, one decoder worker and optional direct eye copying;
read the relevant [release setup](docs/RELEASE-alpha.7.md#setup-and-experimental-opt-in)
and [decoder findings](docs/DECODE-PIPELINE.md) rather than enabling every experiment.

## Settings you'll use

| Control | Where / how |
| --- | --- |
| Bitrate and presets | **Settings -> Presets**. Slider sets a fixed target, or a ceiling with Auto enabled |
| Auto bitrate | Adjusts bitrate using latency feedback. It cannot guarantee target FPS or fix a GPU bottleneck |
| Refresh | Choose a mode confirmed by the app's runtime probe; restart SteamVR after changing it |
| Wavelet / decoder / chroma | PyroWave settings. Keep 4:2:0 unless matched tests justify 4:4:4 |
| Performance overlay | Enabled by default. **Click both thumbsticks together**, release, then click both again to toggle |
| Quest controller model | **Settings -> Headset -> Controllers -> Emulation mode -> Quest 3 Touch Plus**; restart SteamVR |
| Larger PC render source | Use the [independent resolution controls](docs/RENDER-ENCODE-RESOLUTION.md); encoded size can stay fixed |

[Bitrate math and Auto](docs/BITRATE.md) · [Overlay metrics](docs/OVERLAY.md) ·
[Refresh capabilities](docs/REFRESH-RATES.md) · [4:2:0 versus 4:4:4](docs/CHROMA.md)

For example, from a source checkout with the matching server running:

```powershell
python -m tools.quest3.control resolution --profile supersampled3072
```

This requests a 3072x3216 PC source while keeping encode/decode at 2080x2208.
ALVR pads the source recommendation to **3072x3232**. Restart SteamVR afterward;
SteamVR percentages and game settings can change the actual source size.
Use `--profile native2080` to restore native geometry. This controls geometry
only and preserves bitrate, refresh, chroma and decoder settings.

## What has actually been measured?

| Observation | Evidence and limits |
| --- | --- |
| Recent native 120 Hz screens averaged **116–119 submission/completion events per second** | `.37`/`.38`, 2080x2208 per eye, 1000 Mbps, 4:2:0, no foveation. p1 instantaneous client FPS remained near 60: smooth sustained 120 is unmet. These counters are not unique optical frames. [Latest comparison](results/DECODE-STAGE-LIVE-2026-10-04.json), [earlier screen](results/SURFACE-CAPS-2026-10-04.json) |
| Awake GPU stage intervals averaged **2.6–2.8 ms dequantization** and **3.2–3.6 ms inverse transform** | Both stages are material costs. Delayed interval averages, not frame percentiles; diagnostic remains off. [Method and findings](docs/DECODE-STAGE-PROBE.md#awake-vr-comparison) |
| Native USB streaming reached about **112-113 fresh FPS** in the best short `.21` screens | 2080x2208 per eye, requested 120 Hz, 4:2:0, 1000 Mbps. [Results](results/OUTPUT-BRIDGE-LIVE-2026-10-02.json) |
| Producer completed about **120 decodes/s**, while eye copies completed about **112-113/s** | Superseded outputs account for the counted gap. This does not prove its synchronization cause. [Counters](results/OUTPUT-SLOT-LOSS-2026-10-02.json) |
| Later short captures varied, rather than consistently matching the best run | Frame selection: ~97-110 FPS; `.25` timer calibration: ~99-108 FPS. Dynamic clocks and thermal state matter. [Selection](results/FRAME-SELECTION-LIVE-2026-10-02.json), [timer](results/EYE-GPU-LIVE-2026-10-02.json) |
| 4:4:4 cost performance in the matched chroma comparison | About 100 fresh FPS at 1000 Mbps/420 versus 66 at 2000 Mbps/444. **4:2:0 remains the default.** [Decision](docs/CHROMA.md) |
| Optional light encoding reduced GPU decode p50 to **3.91 ms** versus **4.49 / 4.46 ms** controls | `.28`, same 1000 Mbps/native expanded view; short screens only, CPU eye time rose. [Mapping, tradeoffs and results](docs/LIGHT-FOVEATION.md) |
| PC source **3072x3216** streamed with Quest decode still **2080x2208 per eye** | `.28`, 4:2:0/no foveation; **105 / 109 / 112 fresh FPS** in native/larger/native short screens. Separation verified; no sustained 120 or repeatable quality/latency win. [Controls and findings](docs/RENDER-ENCODE-RESOLUTION.md#first-controlled-quest-3-screen) |
| The Vulkan -> AHB -> GLES bridge preserved RGB bytes in tested allocation modes | Small and native stereo readbacks; relative preservation, not an absolute color/quality certification. [Readbacks](results/GPU-READBACK-QUEST-2026-10-02.json) |

These are **short stationary screening measurements**, not sustained gameplay
or optical display FPS. ALVR's estimated pipeline latency is not a measured
motion-to-photon result. No quality/latency win over Virtual Desktop is established.

90/120 Hz and extended 144/207 Hz requests are runtime-gated; accepted display
refresh does not imply that many fresh streamed frames. 240 Hz is a separate
display-scaling experiment and was rejected on the tested setup.

Current presentation research checks an optional
[Android Surface/Vulkan WSI path](docs/SURFACE-PROBE.md) that could eventually
avoid the GLES eye copy. Extension advertisement and tiny object creation are
verified. A [one-shot static Surface image](docs/SURFACE-CHART.md) passed fence
retirement and shutdown; `.41` corrected its vertical flip and passed a compositor
screenshot check. Decoded-video identity, pose pairing and sustained performance remain
unverified. It is not a measured speedup or replacement presenter yet. Earlier synchronization research examined a
[Nightfall-inspired native-fence handoff](docs/NIGHTFALL-SYNC-REVIEW.md).
The `.26` release-fence experiment passed GPU reuse checks and reduced CPU copy
time, but **did not improve delivered FPS** in a short live comparison. It stays
off by default. [Findings](docs/RELEASE-FENCE-EXPERIMENT.md#recorded-outcome-on-quest-3).

[All reviewed results](results/) · [Development history](docs/DEVELOPMENT-HISTORY.md)

## Troubleshooting and switching back

| Symptom | First check |
| --- | --- |
| Dashboard won't open | Run it from the extracted Windows folder, with the bundled files present; avoid mixing installations |
| Quest connects but video is black/corrupt | Verify matching APK/server versions, PyroWave selection and no foveation; preserve logs and report exact settings |
| USB is connected but streaming still uses Wi-Fi | Confirm the wired peer is `127.0.0.1` and ADB forwards are active; see [USB verification](docs/USB.md) |
| SteamVR uses the wrong headset/driver | Enable ALVR for this project. When returning to Virtual Desktop, disable ALVR in SteamVR **Manage Add-ons** |
| OpenXR game closes or never enters the headset | Select SteamVR's OpenXR runtime and relaunch the game; [check the runtime and preserve VD rollback](docs/OPENXR.md) |
| FPS is lower than selected refresh | Read delivered FPS and decode/completion times in the overlay. Increasing bitrate alone cannot remove decoder/compositor stalls |
| Overlay won't hide | Clear any forced-visible benchmark override, then use the both-thumbstick chord; [instructions](docs/OVERLAY.md) |
| Controllers look wrong | Select Quest 3 Touch Plus; older sessions may retain earlier controller settings |

Keep your previous **matching APK/server pair** for rollback. Stop SteamVR before
changing driver installations. Keep Virtual Desktop installed and its registration
intact. [Installation and rollback](docs/BUILD.md#install-and-rollback).

## Build, benchmark or contribute

- **Build:** [pinned Windows/APK instructions](docs/BUILD.md), or run the existing
  GitHub Actions workflow in your fork. Builds include checksums and license notices.
- **Benchmark:** [15-second screening protocol](docs/BENCHMARKING.md), followed by
  longer runs for sustained performance and thermal acceptance. Capture actual
  payload, fresh FPS/p1, decode/completion, latency and thermals.
- **Understand the code:** [architecture](docs/ARCHITECTURE.md),
  [decode pipeline](docs/DECODE-PIPELINE.md), [Vulkan presentation](docs/VULKAN-PRESENTATION.md).
- **Development quality option:** [light peripheral encoding](docs/LIGHT-FOVEATION.md)
  keeps the central region sharp and reduces encoded pixels. Matching `.28` short screens verified mapping and lower GPU decode cost; it
  stays optional and off by default pending sustained and in-headset acceptance.
- **Report a problem:** include release/build version, PC GPU/driver, connection
  type, resolution, refresh, bitrate and the visible symptom. Remove device IDs,
  private network information and secrets from logs before sharing.

Sources are reconstructed from `sources.lock.json` and `patches/`; client/Windows
builds are generated together. Quest tools live in `tools/quest3/`, the native
bridge in `tools/pyroclient/`, regression checks in `tests/`, and reviewed evidence
in `results/`. Inherited Galaxy XR research is retained and labelled separately.

## Credits, license and support

Maintained by **[JMS1717](https://github.com/JMS1717)**. Based on
[Terminal-ennui's experimental Galaxy XR integration](https://github.com/Terminal-ennui/galaxy-xr-alvr-pyrowave-444),
[ALVR](https://github.com/alvr-org/ALVR), and
[PyroWave](https://github.com/Themaister/pyrowave)/[Granite](https://github.com/Themaister/Granite)
by Hans-Kristian Arntzen (Themaister). Upstream authors retain credit for their work.

This project's changes are MIT-licensed; bundled dependencies retain their own
licenses. See [LICENSE](LICENSE), [NOTICE](NOTICE) and
[the preserved upstream README](docs/UPSTREAM-README.md). Independent project;
not affiliated with Meta, Valve, Qualcomm or ALVR.

If you'd like to help fund development and testing:

[![Support Quest3-Pyrowave with PayPal](https://img.shields.io/badge/Support_Quest3--Pyrowave-PayPal-0070BA?style=for-the-badge&logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/jasonselsley)
