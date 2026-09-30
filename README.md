# Quest3-Pyrowave

Experimental **Meta Quest 3 PCVR streaming with PyroWave 4:4:4**, built on ALVR.
Maintained by [JMS1717](https://github.com/JMS1717). Designed to investigate low latency on
same-room Wi-Fi 6E and 2.5 GbE, with **600 / 800 / 1000 / 1500 / 2000 Mbps** targets.

**Development status:** Quest 3 port implemented; builds and device validation are in progress.
No Quest streaming performance result is claimed until it appears in `results/`. High bitrate
presets are experiment targets, not promises of usable throughput or quality.

The starting point is [Terminal-ennui's Galaxy XR PyroWave/ALVR integration](https://github.com/Terminal-ennui/galaxy-xr-alvr-pyrowave-444).
[PyroWave](https://github.com/Themaister/pyrowave) and [Granite](https://github.com/Themaister/Granite)
are by Hans-Kristian Arntzen (Themaister); [ALVR](https://github.com/alvr-org/ALVR) provides the
SteamVR driver, tracking, audio, controllers, session configuration and compositor integration.
This is a port and research project, not a claim to authorship of those components.

## Start here

1. Build or download the **Quest3-Pyrowave-Android** and **Quest3-Pyrowave-Windows** Actions artifacts.
2. Follow [build/install instructions](docs/BUILD.md). Install the distinct Quest APK, register the
   streamer driver in SteamVR and explicitly trust your headset in the dashboard.
3. Start at **Quest 3 PyroWave 600 Mbps**, **90 Hz**, CDF 9/7 and **Auto** decode. This uses fixed
   foveation and full chroma at the encoded resolution. Quest 3 has no eye tracking.
4. Follow [the benchmark protocol](docs/BENCHMARKING.md) before raising bitrate, resolution or refresh.

| Requested refresh | Frame budget | Policy |
|---|---:|---|
| 90 Hz | 11.11 ms | Use when runtime advertises it |
| 120 Hz | 8.33 ms | Use when runtime advertises it |
| 144 Hz | 6.94 ms | Experimental; capability gate required |
| 207 Hz | 4.83 ms | Experimental; capability gate required |
| 240 Hz | 4.17 ms | Experimental; capability gate required |

The server rejects an unsupported rate instead of silently benchmarking a fallback. Runtime
advertisement, request acceptance, effective refresh, and delivered frame rate are separate checks.
An Android refresh override does not establish physical scanout capability. This project does not
set persistent refresh overrides, root the headset, or force GPU clocks.

## What the port changes

- Separate Quest 3 app (`io.github.jms1717.quest3pyrowave`) and protocol version; stock ALVR and
  the Galaxy XR beta cannot accidentally pair with it.
- Removes Android XR required manifest features and uses ALVR's existing Quest OpenXR/tracking path.
- Quest panel-relative render presets; fixed foveation with gaze following off.
- 600–2000 Mbps constant bitrate controls and one-click profiles; server pacing remains enabled.
- Qualcomm vendor decode selection plus explicit Compute/Fragment controls for controlled comparison.
- PyroWave selected before the AMD AMF fallback chain, so an AMD PC cannot silently select AMF
  when PyroWave is requested. Vulkan/D3D11 external-memory and fence support is checked at startup.
- Runtime rate enumeration, clean rejection logs and no refresh-request panic.
- Reconstructable pinned upstream sources, CI APK/streamer generation, tests and benchmark captures.

## Codec comparison

| Codec | Client decode | Chroma in this path | Main tradeoff |
|---|---|---|---|
| PyroWave | Vulkan GPU wavelet + RGBA hardware-buffer bridge | 4:4:4 | Intra-only and high bandwidth; shares GPU/thermal budget with VR rendering |
| H.264 | Android MediaCodec | 4:2:0 | Hardware path, lower network demand; refresh/bitrate limits must be measured |
| HEVC | Android MediaCodec | 4:2:0 | Hardware path and efficient compression; driver-dependent decode latency |
| AV1 | Android MediaCodec on supported devices | 4:2:0 | Efficient compression; needs compatible PC encoder and headset decoder |

These are architectural differences, not measured Quest rankings. The RX 7900 XTX has an ALVR
AMD hardware-codec path; the PyroWave path additionally needs working cross-API GPU interop.
GPU-only decode is not total decoder time, and estimated ALVR latency is not optical motion-to-photon.
Foveation reduces peripheral spatial detail even with 4:4:4. Compare the same scene and dimensions.

## Layout

| Path | Purpose |
|---|---|
| `patches/quest3-alvr.patch` | Quest port on top of the cumulative research ALVR patch |
| `sources.lock.json`, `tools/ci/` | Pinned source reconstruction |
| `tools/quest3/`, `tests/` | Quest capability, plan, capture and analysis tools and regression tests |
| `tools/pyroclient/` | Vulkan decoder and GLES hardware-buffer bridge |
| `tools/windows/` | PyroWave and Windows streamer builds |
| `docs/` | Build, install, measurement and architecture notes |
| `presets/` | Portable benchmark configuration metadata |
| `results/` | Reviewed Quest evidence; raw local captures are ignored |
| `captures/`, other `tools/`, `receiver*`, `driver/`, `runtime/` | Inherited Galaxy XR research and probes; not Quest results |

The original README is preserved in `docs/UPSTREAM-README.md` for provenance. Historical source
patches and research scripts remain available, but the supported Quest recipe is `docs/BUILD.md`.

## Limitations and next experiments

This remains experimental. A 2401 Mbps PHY link cannot guarantee 2000 Mbps application payload.
At 2000 Mbps and 1400-byte datagrams the receiver handles roughly 179,000 packets/s before overhead.
High resolution, high refresh and 4:4:4 all compete for mobile bandwidth and GPU time. The current
Vulkan-to-GLES bridge waits for a fence, and packet reception and decode share a thread; both are
candidates for architectural work after a measured baseline. See [architecture notes](docs/ARCHITECTURE.md).

No Galaxy XR decode, thermal, FPS or latency number is a Quest result. Inherited results are labelled
by their original device. Optical latency, PC GPU clocks/load and full sustained gameplay comparisons
need explicit measurements; unavailable device counters must stay unavailable in reports.

## License and credits

MIT for this project's changes; original notices retained in [LICENSE](LICENSE) and [NOTICE](NOTICE).
Credit to Terminal-ennui for the experimental ALVR integration and benchmark work, Themaister for
PyroWave/Granite, ALVR contributors, and Khronos for OpenXR. Bundled dependencies retain their own
licenses. This is an independent project, not affiliated with Meta, Valve, Qualcomm or ALVR.