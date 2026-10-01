# Quest3-Pyrowave

Experimental **Meta Quest 3 PCVR streaming with PyroWave 4:2:0 and optional 4:4:4**, built on ALVR.
Maintained by [JMS1717](https://github.com/JMS1717). Designed to investigate low latency on
same-room Wi-Fi 6E and 2.5 GbE, with **300 / 400 / 600 / 800 / 1000 / 1500 / 2000 Mbps** targets.

**Development status:** The PWU2 repair has delivered visible SteamVR Home video on Quest 3 over wireless TCP/UDP and native USB. Full-panel 1000 Mbps / 120 Hz still misses the frame budget; short captures often delivered ~60 new FPS. See [live observations and limitations](results/LIVE-2026-10-01.md). Gameplay, sustained thermals and an advantage over Virtual Desktop remain unvalidated. Foveation is disabled.
The [original matching PWU2 build](results/PWU2-BUILD.md) passed Android/Windows builds and cloud regression tests. The next .6 iteration adds latest-frame polling, byte-cap enforcement and Auto safeguards; use matching artifacts after all CI checks pass. See [research inputs and priorities](docs/RESEARCH-NOTES.md).
Reviewed initial evidence is in [results](results/INITIAL-EVIDENCE.md). Those earlier foveated results do not validate the current full-frame build. High bitrate
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
3. Start with **Quest 3 PyroWave 400 Mbps / 72 Hz candidate**, CDF 9/7, **Auto** decode and TCP.
   This is a conservative full-resolution 4:2:0 candidate, with live acceptance pending.
   **600 Mbps / 90 Hz** is the next candidate. **1000 Mbps / 120 Hz** and the 600–2000 Mbps
   120 Hz profiles remain explicitly experimental: current full-resolution decode measurements
   do not establish an 8.33 ms frame budget. Enable **Full chroma (4:4:4)**
   in PyroWave settings and restart SteamVR for full chroma. Foveation is disabled throughout this fork.
4. Follow [the benchmark protocol](docs/BENCHMARKING.md) before raising bitrate, resolution or refresh.

Settings → Presets includes a bitrate slider, Auto bitrate toggle and padded per-frame
budget. See [profile math and automatic bitrate](docs/BITRATE.md) for every preset's
resolution, bytes/frame, compression ratio and network overhead. Auto uses latency feedback;
its slider sets a ceiling rather than a guaranteed rate.

[Native USB streaming](docs/USB.md) is available through Devices → Wired Connection.
The fork selects its own APK package and uses TCP for USB; verify the loopback peer before benchmarking.

| Requested refresh | Frame budget | Policy |
|---|---:|---|
| 72 Hz | 13.89 ms | Conservative candidate; live acceptance pending |
| 90 Hz | 11.11 ms | Use when runtime advertises it |
| 120 Hz | 8.33 ms | Use when runtime advertises it |
| 144 Hz | 6.94 ms | HorizonOS v2.7+; request/frame-period probe |
| 207 Hz | 4.83 ms | HorizonOS v2.7+; request/frame-period probe |
| 240 Hz | 4.17 ms | Developer display scaling; probe required |

[Meta's v2.7 documentation](https://developers.meta.com/vr/documentation/unity/unity-set-disp-freq/)
permits integer 72–207 Hz on Quest 3 through standard requests even when enumeration omits them.
The APK probes 72/90/120/144/207/240 Hz in its lobby, checks request success, reported rate and three
consecutive frame periods, then restores its original rate before connecting. The server accepts
only the resulting capabilities. Older OS versions fail extended requests gracefully.

Above 207 Hz, developer display scaling is required and reduces fine-detail quality; see
[extended refresh setup](docs/REFRESH-RATES.md). Scaling is separate from chroma subsampling.
Runtime acceptance, effective refresh, and delivered frame rate remain separate measurements.
The APK never changes system refresh properties or forces GPU clocks.

## What the port changes

- Separate Quest 3 app (`io.github.jms1717.quest3pyrowave`) and protocol version; stock ALVR and
  the Galaxy XR beta cannot accidentally pair with it.
- Removes Android XR required manifest features and uses ALVR's existing Quest OpenXR/tracking path.
- Uniform full-frame rendering: server and client foveation are disabled, including stale sessions.
- Full panel-relative starting size (requested 2064×2208, padded 2080×2208 per eye); adjustable render size.
- 300–2000 Mbps constant bitrate controls and one-click profiles; server pacing remains enabled.
- Quest 3 Auto defaults to Compute wavelet reconstruction, with explicit Compute/Fragment controls. The color-conversion bridge uses a separately capability-gated fragment pass on Adreno.
- TCP default and experimental PWU2 UDP byte fragmentation, independent reception, bounded reorder-tolerant assembly and deduplication; partial frames are rejected before GPU submission.
- PyroWave selected before the AMD AMF fallback chain, so an AMD PC cannot silently select AMF
  when PyroWave is requested. Vulkan/D3D11 external-memory and fence support is checked at startup.
- v2.7-compatible refresh probing, runtime/frame-period verification and graceful rejection.
- Reconstructable pinned upstream sources, CI APK/streamer generation, tests and benchmark captures.

## Codec comparison

| Codec | Client decode | Chroma in this path | Main tradeoff |
|---|---|---|---|
| PyroWave | Vulkan GPU wavelet + RGBA hardware-buffer bridge | 4:2:0 default; optional 4:4:4 | Intra-only and high bandwidth; shares GPU/thermal budget with VR rendering |
| H.264 | Android MediaCodec | 4:2:0 | Hardware path, lower network demand; refresh/bitrate limits must be measured |
| HEVC | Android MediaCodec | 4:2:0 | Hardware path and efficient compression; driver-dependent decode latency |
| AV1 | Android MediaCodec on supported devices | 4:2:0 | Efficient compression; needs compatible PC encoder and headset decoder |

These are architectural differences, not measured Quest rankings. The RX 7900 XTX has an ALVR
AMD hardware-codec path; the PyroWave path additionally needs working cross-API GPU interop.
GPU-only decode is not total decoder time, and estimated ALVR latency is not optical motion-to-photon.
This build has no foveated encoding or client foveation. Compare the same scene and dimensions.

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
Vulkan-to-GLES bridge waits for a fence. Packet reception now runs independently of decode;
complete-frame drops under radio loss and GPU budget remain constraints to measure. See [architecture notes](docs/ARCHITECTURE.md).

No Galaxy XR decode, thermal, FPS or latency number is a Quest result. Inherited results are labelled
by their original device. Optical latency, PC GPU clocks/load and full sustained gameplay comparisons
need explicit measurements; unavailable device counters must stay unavailable in reports.

## License and credits

MIT for this project's changes; original notices retained in [LICENSE](LICENSE) and [NOTICE](NOTICE).
Credit to Terminal-ennui for the experimental ALVR integration and benchmark work, Themaister for
PyroWave/Granite, ALVR contributors, and Khronos for OpenXR. Bundled dependencies retain their own
licenses. This is an independent project, not affiliated with Meta, Valve, Qualcomm or ALVR.
