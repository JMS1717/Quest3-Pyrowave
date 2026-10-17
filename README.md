# PyroWave for Steam Frame

Experimental PCVR streaming from a **Windows gaming PC** to **Steam Frame**, with Vulkan wavelet
decoding and optional eye-tracked foveated streaming. The client runs as an Android ARM64 app in
Lepton through OpenXR. This repository began as Quest3-Pyrowave and retains its Quest support.

**Current source: `20.13.0-frame.pyro.1`.** The Android APK and decoder libraries build, the
Windows host Rust check passes, and offline unit/shader checks pass. Steam Frame streaming,
image quality, fresh FPS and latency have **not been tested on hardware**. The full Windows
C++ server package still needs its native build. No Frame release is published.

[Steam Frame setup, implementation and validation](docs/STEAM-FRAME.md) ·
[Engineering handoff](docs/HANDOFF.md) · [Build workflow](docs/LOCAL-BUILD.md)

## Frame defaults

- **Steam Frame 72 Hz (experimental):** runtime-recommended stream resolution, 3240 × 3240
  per-eye PC render, CDF 5/3 compute decoding, 4:2:0, 500 Mbps over Wi-Fi/UDP.
- **Steam Frame eye-tracked 72 Hz (experimental):** the same starting profile with a gaze-steered
  full-detail region and compressed peripheral pixels. Requires OpenXR gaze support.
- Panel-relative controls use Frame's 2160 × 2160 panel. Quest clock and colour overrides are off.
  Existing saved sessions retain their settings; select a Frame profile after upgrading.

At a 2176 × 2176 expanded view, the experimental warp encodes 1632 × 1632 per eye, 43.75% fewer
pixels. This is a geometry calculation; performance and perceptual gains remain unverified.
Each encoded frame carries its own per-eye layout so delayed and repeated images reconstruct
with the layout used to encode them. Invalid or stale gaze returns to head-forward.

## Build and test

Use a Windows development PC for the matching server and APK:

```powershell
python tools/local/fast_build.py setup
python tools/local/fast_build.py build
python tools/local/fast_build.py test
```

The build uses [pinned sources](sources.lock.json). ALVR changes are reconstructed from the
patches; the native decoder bridge lives in `tools/pyroclient/`. On Linux, the Android build
scripts also work with an explicit SDK/NDK/JDK and the pinned Cargo tools; see the existing
[build documentation](docs/BUILD.md).

For Frame, enable developer mode, run Lepton Development, connect to its Android ADB endpoint,
install the APK and launch **PyroWave Frame**. Connect through the matching Windows dashboard
and start with the uniform Frame profile. The [Frame guide](docs/STEAM-FRAME.md) covers setup,
eye tracking, timing limitations and the first hardware comparison.

## Published Quest builds

The existing [v0.1.0-beta.2 release](https://github.com/JMS1717/Quest3-Pyrowave/releases/tag/v0.1.0-beta.2)
is the earlier **Quest 3** build, not this Frame port. Never mix its APK/server with the Frame
source build. Quest measurements and profiles are retained in [PROFILES.md](docs/PROFILES.md),
[the release notes](docs/RELEASE-beta.2.md) and [the handoff](docs/HANDOFF.md).

The pipeline uses SteamVR capture on PC, PyroWave encoding, ALVR transport and tracking, Vulkan
decoding, an AHardwareBuffer/GLES eye pass and the headset's OpenXR compositor. See
[architecture](docs/ARCHITECTURE.md) and [benchmarking](docs/BENCHMARKING.md).
