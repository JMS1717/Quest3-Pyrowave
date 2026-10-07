# Vulkan presentation investigation

Status: source design, not an implemented renderer or a measured speedup.

**Current state (October 7, `.64`).** Still no Vulkan presenter: the OpenXR session and eye pass
are GLES. This page dates from October 2-4. Since then:

- The YCbCr-to-RGBA pass is gone. In mode 5 ([PRESENT-YCBCR.md](PRESENT-YCBCR.md), the default)
  PyroWave writes packed YCbCr into an RGBA8 AHardwareBuffer of half height, and the GLES eye
  shader converts it. That covers part of migration step 5 below without a Vulkan session.
- Planar R8 output is not possible: Quest 3 gralloc has no R8 AHardwareBuffer
  ([DECODE-PIPELINE.md](DECODE-PIPELINE.md#planar-output-without-the-rgba-pass-not-possible-on-quest-3-october-4)).
- The direct eye copy costs 1.05-1.10 ms GPU p50 at 207 Hz with 2080x2208 eyes. About 0.8 ms of
  that is writing the two sRGB swapchain images. A Vulkan path that still copies into eye
  swapchains pays that write too; only a path without the copy (such as the Surface route below)
  could avoid it ([FRAME-TRACE.md](FRAME-TRACE.md#eye-pass-cost)).
- The owner's stream is 2080x2208 per eye at 207 Hz (1,901,352,960 stereo pixels/s), rendered at
  3072x3216. The 3072x3216 rows in the table below model encoding at the render size.

Morning .23 Quest logs advertise `XR_KHR_vulkan_enable` and
`XR_KHR_vulkan_enable2`. Current PyroWave decode is already Vulkan; the OpenXR
session, stream projection and performance quad are GLES. Advertised bindings
do not establish a working Vulkan session, sustained FPS or latency.

## Android Surface alternative: capability and ordering caveats

The `.35`/`.36` `[Q3PW_SURFACE]` probe checked only `ExtensionSet.other`.
That excludes extensions already known to the
[pinned openxr bindings](https://github.com/Ralith/openxrs/blob/9270509d23dc774b43a8b7289e8adf69fcac6828/openxr/src/generated.rs).
Its all-false result cannot establish lack of support. `.37` reads the three
named extension fields; runtime advertisement still needs on-device verification.

The reviewed matching `.37` pair subsequently confirmed all three Surface
extension fields **true** on Quest3, alongside both Vulkan bindings. Its short
native120 baseline recorded118.73 submission events/s and118.75 direct eye
completions/s. This establishes advertisement, not creation/presentation or a
performance gain. [Device capability evidence](../results/SURFACE-CAPS-2026-10-04.json).

Next prototype a small static asymmetric Surface on a separate optional path:
verify OpenXR Surface creation, JNI/ANativeWindow lifetime, Vulkan WSI support,
formats and acquire/present synchronization before feeding decoded planes.
Retain the current GLES renderer as fallback. Only after pixel, eye-edge and
pose/content pairing checks should a live comparison remove the RGBA eye copy.

An Android Surface swapchain requires a different ownership path: its images
cannot be enumerated, acquired, waited or released with the ordinary OpenXR
swapchain calls. The application submits through the Surface and stops all
producer writes before ending a stopping session. A Vulkan WSI implementation
therefore cannot substitute this handle into the current eye-copy loop.
[Khronos Surface swapchain requirements](https://github.com/KhronosGroup/OpenXR-Docs/blob/main/specification/sources/chapters/extensions/khr/khr_android_surface_swapchain.adoc).

The FB create extension can request synchronous BufferQueue mode, which can
block the producer, or timestamp-based compositor buffer selection. Neither
flag automatically proves a content/pose match or low latency. Validate
presentation timestamps, image identity and projection poses together before
calling this a replacement for the current leased ring.
[Khronos FB create semantics](https://github.com/KhronosGroup/OpenXR-Docs/blob/main/specification/sources/chapters/extensions/fb/fb_android_surface_swapchain_create.adoc).

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

The matching `.25` cloud APK/server built successfully and passed artifact hash,
signing, embedded-version and packaged-native-library verification. Native decoder
libraries remain byte-identical to `.21`. This is an instrumentation candidate,
not a measured optimization; Quest measurements remain required.
[Build verification](../results/EYE-GPU-CI-2026-10-02.json).

Six matched 15-second Quest screens subsequently confirmed activation with a
53-bit counter. Enabled GPU draw means were 0.616, 0.636 and 0.663 ms, with no
invalid or skipped query results. Fresh submissions remained about 99–108 FPS.
CPU eye-render median spikes occurred with the probe both on and off, alongside
lower sampled GPU clocks (599–640 versus 690 MHz). Battery samples were 43–44°C,
Android thermal status 0. These restart-based screens do not isolate diagnostic
bias or session phase and do not establish sustained 120 FPS. The timer stays off
by default; investigate synchronization, handoff and pacing alongside decoder
cost. GPU draw timestamps do not account for all memory/compositor completion.
[Live calibration](../results/EYE-GPU-LIVE-2026-10-02.json).

Read saved epoch logs with `python -m tools.quest3.eye_gpu LOG --start EPOCH
--end EPOCH --pid RECORDED_PID --out REPORT.json`. The reader requires verified
activation for that process before the capture, rejects malformed/disabled results
and retains per-window percentiles without inventing pooled percentiles.

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
