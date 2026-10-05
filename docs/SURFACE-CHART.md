# One-shot Vulkan Surface chart (`.40`)

Status: **default-off diagnostic candidate**, awaiting matching build and Quest
validation. Normal PyroWave decoding, video projection and the GLES eye renderer
are unchanged. This is a prerequisite experiment for removing that copy, not
an optimized video path or a latency claim.

Set `debug.q3pw.surface_chart=1` before launching. The client creates a separate
64×32 Android Surface swapchain and tiny Vulkan WSI producer. It verifies
`VK_EXT_surface_maintenance1`, `VK_KHR_get_surface_capabilities2`, the device's
`VK_EXT_swapchain_maintenance1` extension **and feature bit**, and transfer-destination
usage before accepting the producer. Missing support leaves normal GLES video
available. The read-only Quest GPU inventory advertises these extensions; that
alone does not verify the device feature or presentation.

After receiving VISIBLE/FOCUSED and a renderable frame, the producer uploads
exactly one opaque asymmetric image: red/green halves, white/yellow upper
corners, cyan/magenta lower corners, and a black center cross. A separate
head-relative quad references this Surface; it does not acquire or release
OpenXR Surface images or alter either video eye. The panel appears above/right
of center. WSI format, enqueue result and a successful OpenXR layer submission
are logged separately. None of them proves optical presentation or color.

## Synchronization and shutdown

This static diagnostic deliberately waits once on an acquisition fence. The
GPU copy signals a binary semaphore consumed by `vkQueuePresentKHR`; an EXT
presentation fence tracks retirement independently of the render fence.
There is one enqueue, no semaphore recycling and no multi-frame ring. Per-frame
polls are nonblocking. A production streamer must instead use GPU-side waits
and a measured ownership/pose protocol; this host acquisition wait is not a
proposed streaming optimization.

On STOPPING the client disables writes and destroys the producer **before**
`xrEndSession` and destruction of the OpenXR Surface. Native destruction waits
for render completion and presentation-resource retirement before freeing
command/upload/semaphore/swapchain resources. Waits are bounded at two seconds.
If completion cannot be proved, the opt-in client terminates rather than freeing
in-flight resources. The unattended test harness must treat that as a failed
candidate, restore settings, and retain the prior matching pair.

`vkDeviceWaitIdle` alone cannot establish safe presentation semaphore/swapchain
retirement. The required extension supplies a presentation fence for this purpose.
[Khronos presentation-resource guidance](https://docs.vulkan.org/guide/latest/swapchain_semaphore_reuse.html),
[extension and dependencies](https://docs.vulkan.org/refpages/latest/refpages/source/VK_EXT_swapchain_maintenance1.html).
The OpenXR Surface lifecycle also prohibits ordinary eye-image acquisition and
writes outside visible/focused states.
[Khronos Surface requirements](https://registry.khronos.org/OpenXR/specs/1.0/man/html/xrCreateSwapchainAndroidSurfaceKHR.html).

## Acceptance before advancing

Require all matching CI jobs, artifact hashes/signing/version checks, and exact
small/native default GPU readbacks before installation. Then verify one enqueue,
successful layer submission, both fences, and orderly retirement using the
same recorded process. A controlled return to Home should exercise STOPPING;
force-stopping the app does not prove its native destructor worked. Inspect a
private compositor screenshot for the asymmetric panel and orientation. Keep
the chart off by default, and preserve normal video plus rollback.

Even a passing static chart does not establish full-resolution WSI storage
writes, useful performance, sustained 120 FPS, decoded image identity or
pose/content pairing. Those are required before any decoded projection replaces
the existing eye copy. [Creation baseline](SURFACE-PROBE.md#quest-3-creation-result).
