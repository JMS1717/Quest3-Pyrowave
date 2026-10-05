# One-shot Vulkan Surface chart (`.40` / `.41`)

Status: **default-off diagnostic**. `.40` passed API lifecycle checks on Quest 3,
but its compositor image was vertically inverted. Reviewed `.41` corrected the
static upload rows and passed the screenshot orientation and lifecycle checks.
Normal PyroWave decoding, video projection and the GLES eye renderer
are unchanged. This is a prerequisite experiment for removing that copy, not
an optimized video path or a latency claim.

Set `debug.q3pw.surface_chart=1` before launching. The client creates a separate
64×32 Android Surface swapchain and tiny Vulkan WSI producer. It verifies
`VK_EXT_surface_maintenance1`, `VK_KHR_get_surface_capabilities2`, the device's
`VK_EXT_swapchain_maintenance1` extension **and feature bit**, and transfer-destination
usage before accepting the producer. Missing support leaves normal GLES video
available. The read-only Quest GPU inventory advertises these extensions; that
alone does not verify the device feature or presentation. The `.40` live test
separately confirmed the required feature and one successful enqueue.

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

Validate the complete saved startup/close log with device-clock bounds:

```text
python -m tools.quest3.surface_chart client.log --start DEVICE_START_EPOCH --end DEVICE_END_EPOCH --pid CLIENT_PID
```

The reader rejects partial, duplicated, failed or reordered lifecycle records.
Shutdown's two successful fence waits prove retirement even if the nonblocking
poll did not observe both fences earlier. A passing report is API/lifecycle
evidence, with optical presentation and performance acceptance explicitly false.

## Quest 3 result: lifecycle passed, orientation rejected

The October 4 `.40` matching pair passed all CI jobs, artifact/version/signing
checks and exact small/native baseline GPU readbacks. A short native 2080×2208
per-eye, 120 Hz, 1000 Mbps, 4:2:0 USB/TCP off/on/off screen confirmed:

- Exactly one Vulkan present and a successful OpenXR layer call.
- Both render and presentation fences completed, followed by STOPPING,
  producer retirement and destruction before Surface destruction.
- The panel appeared in both eyes in a private compositor capture. Its red/green
  sides were correct, but cyan/magenta appeared above white/yellow: **vertical
  orientation failed**. This is a screenshot observation, not headset acceptance.
- Temporary properties, proximity behavior, the saved session and VD-only driver
  registration restored successfully; no fatal signal was recorded.

| Chart | Submission events/s | Eye-completion proxy/s | FPS p1 | GPU decode p50 ms | Completion p50 ms | Payload p50 Mbps |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Off, first | 118.38 | 118.31 | 60.00 | 6.51 | 8.02 | 1011 |
| On | 114.79 | 114.27 | 60.00 | 5.90 | 8.05 | 1013 |
| Off, last | 115.00 | 115.14 | 60.00 | 5.86 | 7.91 | 1015 |

Each measurement lasted eight seconds after two seconds settling, with separate
client launches and verified continuous source coverage. Battery temperature was
29–30°C, thermal status zero. The final control was also slower; these blocks
establish no causal performance benefit or penalty. p1 near 60 exposes missed
120 Hz periods. Counters are not unique optically delivered frames. No optical
latency or sustained gameplay/thermal acceptance was measured.
[Sanitized evidence](../results/SURFACE-CHART-2026-10-04.json).

`.41` reverses upload rows only for this diagnostic in the existing GLES-bound
session, based on the observed compositor interpretation. It preserves channel
order and corner colors, with a CPU regression for both RGBA/BGRA and output
bounds. It does not change video decode, eye mapping or shaders. A future
Vulkan-bound session must establish its own orientation convention. Full-resolution
color handling and decoded-frame/pose selection remain unresolved.

## `.41` orientation confirmation

The October 5 matching `.41` pair passed all CI jobs, artifact/signing/version
checks and exact small/native baseline GPU readbacks. The first capture showed
white/yellow above cyan/magenta in both eyes. Its shutdown verifier rejected the
limited 4000-line log tail because startup records had fallen out; the independent
restorer returned to `.40` without errors. The full continuously saved capture
subsequently passed the unchanged process/clock/lifecycle reader, including both
fences, STOPPING and ordered retirement.

The harness now reads its continuous filtered log. A shorter on/off confirmation
(two eight-second windows, two-second settling) passed orientation and lifecycle,
left `.41` installed and restored all temporary settings and VD registration
without errors. Submission rates were 117.63/113.78 events/s, eye-completion proxies
117.55/113.66, p1 near60, temperature35/33°C and thermal status0. Separate restarts,
short samples and unmatched GPU clocks prevent any causal speedup claim. Screenshot
correctness is not in-headset or colorimetric acceptance, full-resolution decoded
Surface presentation, pose matching or optical latency.
[Sanitized confirmation](../results/SURFACE-CHART-2026-10-05.json),
[matching build](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37261571795).
