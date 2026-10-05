# Android Surface/Vulkan WSI creation probe (`.39`)

Status: **object creation verified on Quest 3** with matching reviewed `.39`
artifacts; still **not a replacement presenter** or measured speedup. The
earlier `.37` runtime probe confirmed extension advertisement. Presentation,
static image correctness and decoded content/pose pairing remain separate gates.

Set `debug.q3pw.surface_probe=1` before launching the client. Other values leave
it off. Only a requested probe enables the advertised KHR Surface extension.
After creating the GLES OpenXR session, it creates a separate 64×32 Surface
swapchain, obtains its Android window and creates a separate Vulkan instance,
present-capable graphics device and tiny WSI swapchain. It enumerates image
count, formats, usage and extent capabilities, then destroys every object.

**No image is acquired, submitted, presented or added to an OpenXR layer.** The
Surface handle never enters the normal eye acquire/wait/release path. The native
helper retains its own JNI/window references and frees WSI/device/surface before
the window, then the Rust wrapper destroys the OpenXR swapchain. A thread is
detached only if the helper attached it. Failures log their status and continue
with the existing GLES presenter; no codec or ownership path changes.

Look for `[Q3PW_SURFACE_WSI] created=true ... submitted=false` followed by
`[Q3PW_SURFACE_PROBE] native_result=0 destroyed=true ...`. Advertisement alone,
OpenXR creation alone or Vulkan Surface creation alone are insufficient to claim
the entire probe passed. Missing/failed records mean unknown or rejected.
The creation probe rejects unexpectedly large extents/image counts and requires
an 8-bit UNORM SRGB-nonlinear Surface format. Format acceptance does not validate
range/gamma, image orientation or color appearance.

Validate a saved startup log using the recorded client process and device-clock
interval (include startup, since the probe runs before video begins):

```text
python -m tools.quest3.surface_probe client.log --start DEVICE_START_EPOCH --end DEVICE_END_EPOCH --pid CLIENT_PID
```

The reader requires one ordered creation/destruction pair with native result 0.
Wrong-process and out-of-window records are excluded; partial, duplicated,
reordered, malformed or mixed failure/success records cannot pass. Missing
records remain unknown. A passing report verifies reported object creation and
destruction, not leak freedom, presentation, image correctness or performance.

Before testing, require a reviewed matching cloud-built pair and unchanged-default
GPU readback. Compare requested/off behavior, inspect object cleanup and resume
normal streaming afterward. The next static-chart prototype must validate
Surface lifecycle, WSI acquire/present synchronization and color. Decoded streaming
then needs verified content/pose pairing before any GLES copy can be removed.

See [Surface ordering constraints](VULKAN-PRESENTATION.md) and the
[device advertisement record](../results/SURFACE-CAPS-2026-10-04.json).

## Quest 3 creation result

Matching `.39` Android/Windows builds and required checks completed successfully
in [Actions run 37256606847](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37256606847).
APK signing, hashes, packaged native libraries, versions and the Windows shader
were reviewed. The codec library is unchanged from `.38`; the changed wrapper
passed exact small/native default RGBA GPU readback before installation.

A short off/on/off screen successfully created a **64×32 Vulkan swapchain with
five images**, RGBA8 UNORM format, through graphics/present queue family 0.
The runtime reported min/max image counts 3/64 and supported usage `0x9f`.
Native result 0 and the ordered destruction record passed the process-specific
evidence reader. Both off blocks had no probe records. Existing GLES streaming
continued, no fatal signal appeared, and temporary settings/proximity plus
Virtual Desktop driver state restored without errors.

The three 8-second blocks recorded submission rates 118.63 / 116.00 / 115.13
and direct completion rates 118.73 / 115.58 / 115.10. The later control also
slowed, so these restart-based screens do not isolate a probe effect. Battery
temperature was 32–33°C with thermal status 0; clocks were not sampled.
The probe stays **off by default**. No Surface image was submitted or displayed,
and no latency gain or native 120 Hz acceptance is claimed.
[All blocks, capabilities and limitations](../results/SURFACE-CREATION-2026-10-04.json).

The next prototype should display a separate tiny asymmetric static chart while
retaining the normal video renderer. Permit Surface writes only while the
OpenXR session is VISIBLE/FOCUSED, stop/drain the producer before STOPPING,
and prove WSI image/semaphore ownership before destroying resources. Use one
dedicated diagnostic layer before attempting a full-resolution projection path.
Color/orientation and image selection need acceptance before pose-matched
decoded frames can replace any GLES copy.
