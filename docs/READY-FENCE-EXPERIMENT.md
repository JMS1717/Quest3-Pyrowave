# Early publication with a Vulkan ready fence

**Experimental, disabled by default, not recommended.** It is correct on the GPU,
but interleaved live screens showed it lowers displayed FPS: 108.3 to 87.2 at
120 Hz without a wait ([fresh-frame loss](FRESHNESS.md)). The `.29` candidate tests whether publishing the output before the
producer's CPU fence wait lets the renderer select a frame it would otherwise
miss. It preserves 4:2:0, full-frame geometry and existing presets.

## What changes

1. Submit the existing decode/conversion and AHB foreign-ownership release on
   the existing Vulkan queue, signaling an exportable binary semaphore.
2. Export its `SYNC_FD` and publish the AHB, tracking timestamp and ingress order.
   The FD is owned by the frame slot, including replacement and teardown.
3. Before changing poses or acquiring new eye images, the renderer imports a
   duplicated FD into EGL and checks `eglWaitSyncKHR`. The wait orders subsequent
   reads in the same GLES context. The original FD remains available for a
   bounded, checked CPU fallback. Timeout/HUP/errors do not authorize a read.
4. Immediately after publication, the producer verifies its existing Vulkan
   completion fence. Only then does it report a completed decode, read timestamp
   queries or push/clear/decode another frame.
5. Both-eye synchronous GLES completion and the consumer lease remain in place.

This keeps **one GPU decode submission in flight**. It does not remove the
producer's completion wait, overlap shared codec resources, or implement
Nightfall's full asynchronous producer. Shared planes, scratch, command/fence/
query objects and Granite upload staging therefore keep their existing lifetime.
The latest-only queue can still supersede an unselected frame; a ready FD is
closed when that happens. A future two-submission design needs the separate
resource/context changes described in [the lifetime audit](NIGHTFALL-SYNC-REVIEW.md).

Extension/entry-point/semaphore-export failure retains synchronous decoding.
A successful export with FD `-1` means an already-signaled payload and uses a
verified synchronous return. A completion timeout or native submission failure
stops the decoder rather than recycling outstanding resources.

## Controlled experiment

Use a **matching `.29` APK and Windows server** after cloud builds and artifact
review. With the client stopped, enable `debug.q3pw.ready_fd=1`, keep
`debug.q3pw.direct_eye_copy=1`, and restart the client. The TCP worker must be one;
`release_fd`, `decode_handoff` and `async_eye_copy` must be off. UDP and independent
two-worker modes retain their existing synchronous path. An unset/zero ready
property keeps the default. Save and restore the original property value.

First run the packaged `ready_fence_gpu_test A.wave B.wave` on distinct complete
Haar/compute frames, including native 4160×2208 stereo. It uses persistent EGL
imports, queues the server wait before sampling, compares twelve outputs (four
per slot) with synchronous references, rejects packet/second-submission reuse
while pending, checks that clear does not corrupt the pending pixels and
exercises outstanding-work teardown. References are seeded B,A,B so every fenced
A/B write replaces different pixels; an unordered read cannot match. At least one
of those discriminating reads must start with an unsignaled native fence. The
earlier `e32596f` probe seeded A,B,A, so its first three reads could pass stale.
This is GPU correctness evidence, not a latency or optical measurement. CPU
mock/poll/ownership tests in CI do not substitute for the device probe.

Packet readiness is consumed when PyroWave records a decode; it must not be used
as a GPU completion check. Diagnostic-only repairs can use the manual **Android
native diagnostic probes** workflow to avoid rebuilding the Windows server.
Its artifact contains no APK/server: all three native library hashes must match
the reviewed APK exactly before using a probe with that pair. Runtime/native API
changes still require the full matching build workflow.

**Recorded outcome (2026-10-04):** the corrected probe from `bd0de87` (native
libraries byte-identical to the reviewed `.29` APK) passed on Quest 3 at
512×320 and native 4160×2208: 12 of 12 discriminating reads per size started
with an unsignaled fence and matched exactly; default-off output was unchanged.
See [results](../results/READY-FENCE-GPU-2026-10-04.json). Correctness only.

Then compare publication modes. `.30` can switch `debug.q3pw.ready_fd_active`
within one session, so interleaved blocks replace restart-based screens; see
[fresh-frame loss](FRESHNESS.md). Use the same actual source texture
size, normalized chart, projection, encode geometry, bitrate, runtime rate,
overlay and thermal guards. Verify current PID, source coverage and all gates.
Compare fresh FPS, p1/gap distribution, native decode and verified completion
time, actual payload, full-framework cost and total estimated pipeline latency.
Keep defaults unchanged until a repeatable benefit survives sustained tests.

Logs distinguish `submitted` from `completed`, with `max_inflight=1`.
`server_waits` counts successful EGL server-wait calls; it does not prove GPU
hardware implementation or completed sampling. `server_mean_us/server_max_us`
measure CPU import/wait/destroy overhead. CPU fallback or rejected-read markers
must be reported explicitly. The existing full-loop probe includes the ready
gate; the eye-render-only interval starts later and excludes it.

**Statistics limitation:** early selection can precede the producer's actual
completion callback. Generic ALVR decode/decoder-queue/rendering subcomponents
assume callback order and may be misattributed in this experiment. Use native
timestamp/completion measurements and the independently measured total pipeline
estimate; do not use those generic subcomponents as evidence of a gain. Total
pipeline latency is still an estimate, not optical motion-to-photon.

## API and ownership references

Implemented independently under MIT; no Nightfall GPL implementation was copied.
[Vulkan semaphore FD export](https://docs.vulkan.org/refpages/latest/refpages/source/vkGetSemaphoreFdKHR.html)
defines application ownership and semaphore export transference.
[Android EGL native fences](https://registry.khronos.org/EGL/extensions/ANDROID/EGL_ANDROID_native_fence_sync.txt)
transfer the imported duplicate to EGL.
[EGL server waits](https://registry.khronos.org/EGL/extensions/KHR/EGL_KHR_wait_sync.txt)
order the current context without proving CPU completion; their implementation
need not be GPU hardware. Import/wait/destroy failures are checked. A failed
destroy disables further imports, retains at most one handle with a live display
reference and retries cleanup at teardown; a repeated cleanup failure is logged.
