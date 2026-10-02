# Vulkan presentation investigation

Status: source design, not an implemented renderer or a measured speedup.
Morning .23 Quest logs advertise `XR_KHR_vulkan_enable` and
`XR_KHR_vulkan_enable2`. Current PyroWave decode is already Vulkan; the OpenXR
session, stream projection and performance quad are GLES. Advertised bindings
do not establish a working Vulkan session, sustained FPS or latency.

## Why investigate presentation

At native resolution the saved stage counters complete about 120 decodes/s,
but fewer completed eye copies. Short pre-wait and post-wait experiments did
not establish a repeatable gain. The producer completes a full stereo RGBA AHB,
then GLES samples it into the eye swapchains. Direct eye copy still crosses APIs
and preserves buffer leases until the consumer finishes. Caching and asynchronous
copy screens have not established a benefit. See [live selection results](../results/FRAME-SELECTION-LIVE-2026-10-02.json).

Nominal traffic illustrates why more USB bitrate cannot solve every bottleneck:

| Encoded pixels/eye | Hz | Stereo pixels/s | Raw 8-bit 4:2:0 Gbit/s | RGBA MB/frame | Three RGBA accesses GB/s |
|---|---:|---:|---:|---:|---:|
| 2080×2208 | 120 | 1,102,233,600 | 13.227 | 36.741 | 13.227 |
| 3072×3216 | 207 | 4,090,134,528 | 49.082 | 79.036 | 49.082 |
| 3072×3216 | 240 | 4,742,184,960 | 56.906 | 79.036 | 56.906 |

For stereo pixel count S=2WH, raw 4:2:0 is 12S bits/frame. Three RGBA accesses
model conversion output write, full-frame source read and total two-eye output
write: 12S bytes/frame. The equal numerical values in the last two columns have
different units. These are byte-count models, not measured DRAM traffic or hardware
limits: tiling, caches, compression, reconstruction and sampling change actual
traffic. The 207-Hz target is 3.71 times the native120 pixel rate. Frame budgets are
8.33 / 4.83 / 4.17 ms. A working 120-Hz display request does not meet those budgets.

## Migration sequence

### Eye-copy GPU diagnostic candidate (.25)

`debug.q3pw.eye_gpu_probe=1` before client startup requests an optional GLES
`EXT_disjoint_timer_query` diagnostic around the two eye draws. It defaults off.
Eight query slots are polled for availability before reading 64-bit elapsed
results. A full ring skips measurement; it never waits for a query. Extension,
entrypoint, counter-width or GL errors disable the diagnostic. Disjoint events
invalidate pending results, including events detected during result polling.
The existing finish/fence and decoded-buffer lease rules remain intact.

`[Q3PW_EYE_GPU_SETUP]` confirms whether the probe actually activated.
`[Q3PW_EYE_GPU]` reports valid sample count, mean/p50/p95/max milliseconds,
invalid results and skipped measurements. Empty/unsupported results are unknown,
not zero cost. These GPU timings cover the draw interval, exclude CPU EGL import,
decoder work and runtime presentation, and do not measure optical latency.
Tile rendering can affect the meaning and overhead of timer boundaries; use
off/on/off screens to check diagnostic bias before drawing performance conclusions.
[Khronos timer-query semantics](https://registry.khronos.org/OpenGL/extensions/EXT/EXT_disjoint_timer_query.txt).

This is an instrumentation candidate, not an optimization or a validated build.
Matching cloud APK/server verification and Quest measurements are required.

### Presentation migration

1. First measure the current overlay with same-session visible/hidden/visible
   15-second screens, using `.24` visibility control and CPU-wall diagnostics.
   Preserve geometry, bitrate, scene, thermal state and clock samples. Reject
   incomplete source coverage; longer sustained tests follow only repeated gains.
2. Build a separate Vulkan OpenXR static asymmetric stereo-chart path. Use
   `XR_KHR_vulkan_enable2`, runtime graphics requirements, runtime-assisted Vulkan
   instance/device creation and the physical device returned by OpenXR. The
   binding requires those handles; selecting the first enumerated GPU in the
   current standalone decoder is insufficient. Keep the GLES client as fallback.
   [Khronos binding requirements](https://registry.khronos.org/OpenXR/specs/1.0/man/html/XrGraphicsBindingVulkan2KHR.html).
3. Add a caller-owned Vulkan device contract to pyroclient. Its present
   `create_device` owns instance/device/queue, then lends them to PyroWave. The
   new contract must preserve create-info storage lifetimes, shader features,
   ownership of destruction and queue synchronization. Check both OpenXR and
   Vulkan results during device creation. Do not reinterpret existing AHB handles
   as swapchain images. [Device creation](https://registry.khronos.org/OpenXR/specs/1.0/man/html/xrCreateVulkanDeviceKHR.html).
4. Initially preserve synchronous completion and copy the decoded RGBA image
   into acquired/waited Vulkan eye swapchains on that shared device. Validate
   orientation, stereo crop, alpha, gamma/range, poses and swapchain layouts before
   changing waits. A shared device removes interop, not the copy itself. Session,
   projection, lobby and quad types must migrate together; they currently bind
   `xr::OpenGlEs`. A different presentation binding also needs explicit non-PyroWave
   MediaCodec handling or a clean return to the GLES session.
5. Only then consider sampling decoded Y/Cb/Cr planes directly into eye
   swapchains, avoiding the intermediate RGBA conversion/read. Preserve chroma
   upsampling, range, gamma and eye-edge sampling rules. Hold plane/output leases
   until the consumer GPU fence completes; neither submission nor swapchain
   release alone proves the decoder may overwrite shared planes. Handle
   disconnection, resize and failed submission without freeing in-flight memory.

OpenXR requires acquire/wait/release ordering for swapchain images.
[Release requirements](https://registry.khronos.org/OpenXR/specs/1.0/man/html/xrReleaseSwapchainImage.html).
If a staged implementation shares AHBs across logical devices/APIs, it must retain
the allocation, query import properties, use required dedicated image allocations
and implement ownership transfers and GPU synchronization. External and foreign
ownership are distinct; a CPU lock is not a substitute for this contract.
[Khronos AHB memory and ownership rules](https://github.khronos.org/Vulkan-Site/spec/latest/chapters/memory.html#memory-external-android-hardware-buffer).

Each stage needs matching APK/server builds, asymmetric GPU-consumer correctness
checks and repeated live comparisons. Retain 4:2:0, no foveation and measured
native120 geometry until performance improves. Runtime binding support, unique
frame submissions, GPU completion and optical presentation remain separate claims.
