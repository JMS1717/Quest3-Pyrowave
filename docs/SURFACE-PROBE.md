# Android Surface/Vulkan WSI creation probe (`.39`)

Status: optional diagnostic candidate, **not a replacement presenter** or
measured speedup. The reviewed `.37` runtime probe confirmed Surface extension
advertisement on Quest3. This next step checks actual object creation before
attempting a static image or decoded-video path.

Set `debug.q3pw.surface_probe=1` before launching the client. Other values leave
it off. Only a requested probe enables the advertised KHR Surface extension.
After creating the GLES OpenXR session, it creates a separate64×32 Surface
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
an8-bit UNORM SRGB-nonlinear Surface format. Format acceptance does not validate
range/gamma, image orientation or color appearance.

Before testing, require a reviewed matching cloud-built pair and unchanged-default
GPU readback. Compare requested/off behavior, inspect object cleanup and resume
normal streaming afterward. The next static-chart prototype must validate
Surface lifecycle, WSI acquire/present synchronization and color. Decoded streaming
then needs verified content/pose pairing before any GLES copy can be removed.

See [Surface ordering constraints](VULKAN-PRESENTATION.md) and the
[device advertisement record](../results/SURFACE-CAPS-2026-10-04.json).
