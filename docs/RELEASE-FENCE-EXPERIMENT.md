# .26 release-fence experiment

**Default off. Source candidate; matching build review and live acceptance required.**
This implements the first step of the [Nightfall synchronization assessment](NIGHTFALL-SYNC-REVIEW.md).
It does not enable asynchronous Vulkan decoding, change defaults, enable foveation,
or change stream resolution/bitrate. Keep the matching .25 runtime for rollback.

## Change and ownership

At `debug.q3pw.release_fd=1`, the TCP decoder checks SYNC_FD import support and
enables `VK_KHR_external_semaphore_fd`. Each output slot has a temporary-import
binary semaphore. Native completion still CPU-waits on `vkWaitForFences`; a
frame is counted decoded only after that succeeds.

After both direct eye draws, the renderer exports an EGL native fence and
flushes instead of `glFinish`. The FD is registered to that AHB's completed-frame
token. The current leased and pending buffers remain protected. When the next
frame is taken and a prior buffer becomes reusable, Vulkan temporarily imports
its release FD and GPU-waits **before the FOREIGN-family acquire barrier and
subsequent writes**. The existing image ownership/layout barriers remain.

The independent implementation retains our three-buffer ring and latest-frame
policy. It does not prevent superseding by itself. It aims to remove a render-thread
CPU wait, with producer publication still synchronous and complete-frame-only.

Tokens travel with pending/leased frames and survive superseding correctly.
The native registry invalidates a token at reuse, assigns a new token at successful
completion, rejects stale attachments and owns/closes every accepted FD.
Successful Vulkan import transfers FD ownership to the driver. On import failure,
a bounded CPU fallback checks poll result/revents; timeout/error poisons the
native context so that it cannot overwrite that slot on a later retry.

GLES GL fences independently observe actual eye completion without gating the
next render. The observer queue is bounded to three. Submission does not increment
completed-eye counters. A busy queue, failed export/attachment or failed observer
wait falls back to synchronous completion; failed observations remain uncounted.
These events disqualify the run as a successful native-fence comparison.

Only **TCP PyroWave/direct-eye** gets nonzero release tokens. UDP, staging,
unsupported native imports and other decoders retain their existing path.
The existing CPU-polled async-eye mode is separate: leave it off in this experiment.

## Validation gates

Portable C++ tests cover FD ownership transfer/replacement/removal, stale tokens,
pointer reuse, and multiple output slots. A Rust FrameSlot regression covers
lease tokens through superseding/out-of-order publication. CI must compile both
matching artifacts, including the exported native API and Android-only EGL path.
These tests do not prove cross-API synchronization on Adreno.

Before live timing, verify output through the actual Vulkan -> AHB -> GLES
consumer, including delayed reads/reuse. Then run native 2080x2208 per-eye/120 Hz,
420/noFFE, one TCP worker, current wavelet/bitrate and continuously covered source.
Screen off/on/off for 15 seconds per cell after settling, at matched temperature.
Check epoch/PID-scoped diagnostics:

- `[Q3PW_RELEASE_FD] Vulkan requested=1 importable=1`.
- Growing Vulkan `imports=... gpu_wait=1` and GLES `exports`/`observed` counters.
- No import failure, rejected attachment, observer failure or inactive fallback.
- Matching producer/consumer telemetry endpoints, fresh FPS/p1, superseding,
  CPU eye-render time, GPU decode/completion, latency and thermals.

Use saved original properties, a device-pinned ADB wrapper and existing thermal,
process-identity and experiment-lock guards. Enable only after matching artifacts
are reviewed. Both the graphics constructor and native decoder read the property
at initialization; restart **this Quest app** after toggling it. Keep
`debug.q3pw.direct_eye_copy=1`, `debug.q3pw.async_eye_copy=0`; preserve every other
setting. Restore the property's saved value and the matching .25 pair if there
is regression. Do not promote from nominal refresh or queued/submitted counters.

Longer sustained and in-headset acceptance follow only after correctness and
repeated short comparisons. No 120-FPS or latency improvement is claimed here.
