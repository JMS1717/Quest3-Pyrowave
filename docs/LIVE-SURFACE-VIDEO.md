# Live video through an Android Surface swapchain: design and decision

October 6, 2026. Decision: **not worth building now.** The gain is about 8-9% of per-frame GPU
time in GPU-bound modes and nothing at native 120 Hz. The pose/content pairing risk needs optical
verification that is not available, and GPU level 7 already delivers a similar gain for free.
Revisit only under the conditions at the end.

## What it would replace

Today each fresh frame costs, on the Quest GPU:

1. PyroWave decode into three R8 planes (Vulkan compute).
2. A YCbCr→RGBA convert pass into an AHardwareBuffer ring slot (Vulkan).
3. The GLES "direct eye copy": import that AHB as an EGLImage and draw it into the two OpenXR
   GLES swapchain images.
4. The Meta compositor samples the swapchain images.

The live Surface path would create an `XR_KHR_android_surface_swapchain` (side-by-side,
4160x2208 at native size) and give its `ANativeWindow` to a Vulkan WSI swapchain on the decoder's
device. Step 2 renders straight into an acquired WSI image, step 3 disappears, and the projection
layer references the Surface swapchain with one sub-image rectangle per eye. `.40`/`.41` already
proved creation, one Vulkan present, orientation and orderly STOPPING retirement for a static
chart ([SURFACE-CHART.md](SURFACE-CHART.md)).

## Measured cost it removes

Medians from the [October 6 matrix](../results/HIGH-REFRESH-2026-10-06.json) (stage and eye GPU
timers on):

| Cell | Decode | Convert | GLES eye copy | Compositor | Eye copy share of app + compositor GPU |
| --- | ---: | ---: | ---: | ---: | ---: |
| 120 Hz, 2080x2208 | 6.33 ms | 0.76 | 0.66 | 0.82 | 7.9% |
| 120 Hz, 2560x2720 | 7.90 | 1.24 | 0.98 | 0.93 | 8.9% |
| 207 Hz, 1440x1536 | 2.83 | 0.41 | 0.33 | 0.76 | 7.6% |
| 240 Hz, 1440x1536 | 2.66 | 0.41 | 0.34 | 0.52 | 8.7% |
| 240 Hz, 1536x1664 | 2.94 | 0.49 | 0.39 | 0.52 | 9.0% |

The convert pass stays: it only changes its target. Folding #4's fused colour into it removes the
separate convert too, but that was already measured at 120 Hz with no delivered gain
([FUSE-COLOR.md](FUSE-COLOR.md)).

Expected effect, assuming fresh FPS tracks GPU time per frame where the GPU is saturated:

| Cell | Today | Projected without the eye copy |
| --- | ---: | ---: |
| 120 Hz, 2080x2208 | 119 | 119 (already at the panel rate) |
| 120 Hz, 2560x2720 | 89 | about 97 |
| 240 Hz, 1536x1664 | 200 (204 with GPU level 7) | about 218 |
| 240 Hz, 1440x1536 | 223 (228 with GPU level 7) | about 235 |

For comparison, GPU level 7 measured +4 to +9 fresh FPS in the same GPU-bound cells with no code
change ([HIGH-REFRESH.md](HIGH-REFRESH.md)).

## Design, if it is built

**Producer ring.** A WSI swapchain of 3-4 images on the decoder's Vulkan device, created with
`VK_EXT_swapchain_maintenance1` (already required by the chart). The decode worker acquires an
image, decodes and converts into it, signals a per-image fence, and parks it as "ready". It never
presents. A newer ready frame supersedes an older one, which is released unpresented with
`vkReleaseSwapchainImagesEXT`, so latest-frame-wins is kept without consuming a display slot.

**Presentation only from the render loop.** After `xrWaitFrame`, the render loop takes the newest
ready image (the same selection and bounded wait as today), presents it, and submits the projection
layer with that frame's pose in the same iteration, immediately before `xrEndFrame`. One present per
app frame is what makes pose/content pairing possible.

**The pairing hazard.** A BufferQueue consumer latches whatever buffer is newest when it composites.
If the GPU work behind a present is not finished by then, the compositor shows the previous buffer
with the new pose: a one-frame mismatch, visible as judder during head motion and invisible on a
stationary chart. Mitigations, in order:

1. Present only images whose GPU fence has signalled (the bounded wait already ensures this for
   most frames; a frame still decoding is skipped exactly as today).
2. Create the swapchain with `XR_ANDROID_SURFACE_SWAPCHAIN_USE_TIMESTAMPS_BIT_FB` and set each
   buffer's presentation time to the frame's predicted display time (`VK_GOOGLE_display_timing` or
   `native_window_set_buffers_timestamp`). Whether Quest's compositor honours it must be probed first.
3. Synchronous BufferQueue mode (`..._SYNCHRONOUS_BIT_FB`) as a fallback. It can block the producer
   and needs measuring.

**Lifecycle.** STOPPING destroys the producer before the OpenXR Surface, with presentation-fence
retirement as in the chart. Reconfiguration recreates the swapchain. The GLES eye path stays as
the fallback, selectable at startup. Overlay and passthrough layers are unchanged.

**Validation that would be required.**

1. Exact-pixel readback of the WSI image against today's RGBA output.
2. Screenshot orientation and per-eye rectangles.
3. Same-session A/B against the GLES path for fresh FPS, app loop and GPU timers.
4. An optical or high-speed-camera pairing test during head rotation. Stationary charts cannot show
   pose/content mismatch.

## Why not now

- **Gain.** At most about 9% of GPU time per frame. Native 120 Hz, the primary target, is
  already at the panel rate, and the above-native and 240 Hz gains are similar to what
  `debug.oculus.gpuLevel=7` already gives.
- **Risk.** Pose/content pairing is the whole difficulty. A mismatch is a regression a
  stationary-chart harness cannot detect, and no optical rig is available.
- **Cost.** About two to three days for the ring, render-loop presentation, layer submission,
  lifecycle and reconfiguration, plus exact-pixel and pairing validation. That is larger than any
  remaining lever measured tonight.

## When to revisit

- A decoder change cuts decode substantially, so the eye copy becomes a larger share of the frame
  (for example, if decode at native size fell below about 3 ms).
- A cheap probe shows Quest's compositor honours Surface buffer timestamps. That removes most of
  the pairing risk.
- A full `XR_KHR_vulkan_enable2` session migration is undertaken for other reasons
  ([VULKAN-PRESENTATION.md](VULKAN-PRESENTATION.md)). Writing straight into Vulkan swapchain images
  removes the same copy without the BufferQueue pairing problem, and is the better long-term
  route.
