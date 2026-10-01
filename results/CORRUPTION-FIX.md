# Full-resolution corruption repair — September 30, 2026

Status: the earlier buffer-safety APK builds and native decoder checks passed; the new PWU2
transport repair passed Android/Windows builds and cloud regression tests. See [candidate hashes and validation](PWU2-BUILD.md). Live headset acceptance is pending.
The last live TCP attempt encountered the OS tracking dialog. Testing then stopped at the owner's
request. This is not proof of a repaired streamed image or sustained 120 fps. Dashboard startup
was verified locally before development was isolated.

## Observed failures and changes

The old full-resolution 1000 Mbps/120 Hz/4:2:0/Compute session recorded **zero complete frames**,
thousands of partial presentations, and around 14.5 ms mean GPU submission-to-fence time. UDP
reception and the blocking GPU wait shared a thread. Arbitrary incomplete wavelet frames were
presented without a pristine-band readiness check; an ADB screenshot showed near-black damage.
The dashboard independently crashed because its default resolution label did not match a preset.

The repair separates packet reception from GPU work, bounds assembly and the completed-frame
queue, deduplicates packet indices, tolerates reordered tails, and rejects incomplete transport
or codec frames. Loss now holds the last valid image. 4:2:0 chroma views describe their actual
half-sized images. The dashboard label is corrected and missing preset defaults have a fallback.
The Vulkan/GLES color bridge uses a capability-gated fragment pass on Adreno. The producer now excludes the renderer's held buffer and the pending buffer from reuse, and the GLES copy completes before the render loop advances its lease. A native test holds the first buffer across 60 further complete decodes without recycling it; client-core lease/transport tests pass (14 total).

## On-device decoder checks

A synthetic labelled stereo frame was encoded once with the pinned PC PyroWave encoder:
4160×2208, 4:2:0, limited range, CDF 9/7, 1,041,666-byte cap; actual payload 283,700 bytes.
Both wavelet paths reconstruct LEFT/red in the left half, RIGHT/blue in the right half, and TOP
above the labels. This validates codec readback layout, not the final OpenXR eye projection.
The synthetic Y4M uses OpenCV color conversion, so source-image PSNR is not a BT.709 quality test.

Matched short checks use 10 warm-up frames plus 30 measured frames per row on Adreno 740:

| Wavelet reconstruction | Color bridge | Complete | GPU decode p50 | Conversion p50 | Submit-to-fence p50 |
|---|---|---:|---:|---:|---:|
| Compute | Compute | 30/30 | 10.56 ms | 4.39 ms | 15.81 ms |
| Compute | Fragment | 30/30 | 10.50 ms | 2.63 ms | 13.99 ms |
| Fragment | Compute | 30/30 | 7.86 ms | 4.38 ms | 12.73 ms |
| Fragment | Fragment | 30/30 | 7.19 ms | 2.13 ms | 11.06 ms |

Clocks and temperatures were not controlled. These short serial diagnostic checks use one
synthetic bitstream, exclude network/OpenXR/GLES presentation, and are not a sustained benchmark.
Even the fastest median exceeds the 8.33 ms budget for 120 fps. The configured 120 Hz display
request remains separate from delivered frame rate; no full-resolution 120 fps claim is made.

Fragment color conversion differs from compute by at most 1/255 on the full-resolution 4:2:0
readback. A separate 1536×768 4:4:4/full-range pattern yields identical conversion readbacks.
Compute remains the default wavelet path because detailed-pattern reconstruction checks
find larger errors in Fragment. A new 4160×2208 4:2:0 stress pattern (8-pixel seeded color blocks, ramps, diagonal lines and labels; 1,041,612-byte bitstream) passed 30 complete decodes on each path, but Fragment differed from Compute by up to 52/39/56 RGB levels (mean absolute 0.41/0.28/0.43 levels). This is a same-bitstream comparison, not source-image PSNR. Changing only color conversion avoids that reconstruction tradeoff.

## Reproduce and verify

Build using [BUILD.md](../docs/BUILD.md). Regenerate the new conversion shader headers with
`python tools/pyroclient/compile_shaders.py --glslc <Vulkan-SDK-glslc>` when editing GLSL.
For an offline bridge comparison, run `pyroclient_test` with `PYROWAVE_CONVERT_COMPUTE=1`
for compute conversion; the default uses fragment conversion on supported Adreno imports.
Deploy all native dependencies alongside the executable and use `LD_LIBRARY_PATH=.`.

For live eye mapping, install the matching APK/server, open Quest3 PyroWave, and dismiss any
OS tracking prompt. Install `numpy opencv-python openvr glfw PyOpenGL` on Windows, then run
`python -m tools.quest3.stereo_scene --out results/local/stereo --seconds 30`.
Capture with `adb shell screencap -p /sdcard/stereo.png` and `adb pull /sdcard/stereo.png`.
Verify LEFT/red and RIGHT/blue in their own eyes, upright TOP/BOTTOM, and complete-frame counters
in `adb logcat -s PYROWAVE-UDP`. A scene submission count is not a decoded/displayed frame count.
Screenshots may include private surroundings when a system passthrough dialog is active; keep
raw captures local and publish only reviewed synthetic test evidence.

## Black-screen follow-up

The repaired strict receiver still completed zero transport frames at 1000 Mbps/120 Hz;
invalid-packet counters rose to 3714. Source inspection found a deterministic transport defect:
PyroWave preserves whole codec blocks even when they exceed the requested 1368-byte boundary.
The old server treated that boundary as a hard MTU limit. The strict receiver rejects oversized
payloads, and older receivers can truncate sufficiently large datagrams. Invalid counters are
aggregate evidence; they do not independently identify every rejected packet's cause.

The new server encodes a complete frame, fragments its bytes into payloads no larger than
1368 bytes, and the new receiver rejoins them before codec submission. PWU2 magic and protocol
20.13.0-quest3.pyro.5 prevent silent mixing with the old UDP layout. Loss recovery remains absent.
TCP is now the default. A live TCP attempt was interrupted by the system tracking interstitial;
it does not establish successful streamed presentation.

Presets now start at a **400 Mbps / 72 Hz / full-resolution / 4:2:0 / TCP candidate**.
The 600 Mbps / 90 Hz candidate and 600–2000 Mbps / 120 Hz experiments remain available.
Neither candidate is certified by sustained live testing. The change gives the GPU more frame
time and reduces the per-frame byte budget; it is not a claim that lowering bitrate alone fixes
full-resolution GPU throughput. Native timings exceed the 120 Hz budget in multiple test cases.

The owner requested uninterrupted Virtual Desktop use. Development was moved to an isolated
directory, the ALVR driver was disabled/unregistered, and headset tests stopped. Further builds
and regression checks run on GitHub Actions. No repaired live image is claimed.

Remaining work: live full-resolution acceptance with permission, loss recovery/pacing, an asynchronous replacement for the synchronized GLES handoff, and further decode/bridge optimization before a 120 fps guarantee.
