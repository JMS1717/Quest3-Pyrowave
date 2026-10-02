# .26 release-fence experiment

**Default off. Matching builds and GPU reuse checks passed; the first live
comparison did not improve delivered FPS. Sustained acceptance is pending.**
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

The cloud Android artifact includes a separate `release_fence_gpu_test` diagnostic:

```text
release_fence_gpu_test A.wave B.wave haar 3 16
```

Use distinct valid Haar-encoded frames with identical dimensions/chroma/range and
the saved-property guards below. It records synchronous reference pixels, queues
GLES reads of A, exports/attaches their release FD, and cycles all three Vulkan
slots to overwrite the source with B **before** the outer GLES CPU completion/readback.
Both captured A and later B must match their references exactly. The diagnostic
reports FD attachments, verified source reuse and how many exports were still
unsignaled. It does not force a fixed GPU delay, prove the FD is the only driver
dependency, reproduce OpenXR scheduling or establish absolute image quality.
This extra diagnostic is not linked into the production renderer/library.

## Recorded outcome on Quest 3

[Matching `.26` CI builds](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37027807642)
passed all jobs. APK/server versions, hashes, packaged native libraries, stable
certificate metadata and the two native API exports were reviewed.
[Artifact record](../results/RELEASE-FENCE-CI-2026-10-02.json).

The standalone probe passed three reuses at 512x320 and three at native
4160x2208 stereo. All six exported fences were initially unsignaled; Vulkan
imports were confirmed. Queued A captures and later overwritten B outputs
matched their references exactly. Default-off pixels also matched the previously
tested bridge. [GPU proof](../results/RELEASE-FENCE-GPU-2026-10-02.json).

The complete 15-second off/on/off comparison, after settling, measured:

| Metric | Off control | On | Off restore |
| --- | ---: | ---: | ---: |
| Fresh submissions FPS | 103.11 | 97.69 | 112.02 |
| Completed eye copies/s | 103.08 | 97.67 | 112.66 |
| CPU eye-copy p50 ms | 2.37 | 0.55 | 6.04 |
| Decode completion p50 ms | 6.53 | 5.93 | 6.97 |
| Superseded outputs/s | 16.94 | 22.14 | 7.38 |
| Estimated pipeline latency p50 ms | 58.71 | 68.44 | 66.27 |

Native GPU imports and GLES completion observers were active without fallback.
Both controls and the experiment completed about 120 producer decodes/s. Thus
removing this CPU wait **did not remove output superseding or improve delivered
fresh FPS** in this comparison. Keep the experiment off; do not publish it as a
performance improvement. [Live evidence](../results/RELEASE-FENCE-LIVE-2026-10-02.json).

These were stationary screens at 43 C, thermal status 0 and dynamic GPU clocks
(off 599 MHz, on 690 MHz, restore 599-640 MHz). Requested decode geometry stayed
2080x2208/eye, 120 Hz, 1000 Mbps, Haar/Compute, 420/noFFE. The actual SteamVR
source texture was 2544x2704/eye due to its scaling, identical across the group.
CPU completion observations are quantized by the next poll; they are not exact
GPU draw times. Screenshot eyes/orientation looked correct, but optical latency,
sustained gameplay and human in-headset acceptance remain unverified.

An initial partial comparison was rejected: its validator matched an unrelated
color-copy `active=false` message and aborted before complete source-interval
metadata. It is excluded from the accepted comparison; it was not a native-fence
failure. The validator now checks only release-fence messages and closes the
source gracefully on failure.

The next scheduling investigation should measure empty/late frame selection and
runtime queue/`xrWaitFrame` feedback. This result supports safe buffer ownership,
not the assumption that more asynchronous submission alone solves the FPS gap.

A follow-up with native release disabled tried the existing bounded selection
wait at **0/1000/0 microseconds**. Fresh FPS was **105.96/108.78/109.90** and
estimated latency p50 **59.58/64.45/61.26 ms**. The wait caught late completions,
but did not establish a gain over both controls. Its default stays zero.
[Wait evidence](../results/FRAME-WAIT-1000-LIVE-2026-10-02.json).

Use saved original properties, a device-pinned ADB wrapper and existing thermal,
process-identity and experiment-lock guards. Enable only after matching artifacts
are reviewed. Both the graphics constructor and native decoder read the property
at initialization; restart **this Quest app** after toggling it. Keep
`debug.q3pw.direct_eye_copy=1`, `debug.q3pw.async_eye_copy=0`; preserve every other
setting. Restore the property's saved value and the matching .25 pair if there
is regression. Do not promote from nominal refresh or queued/submitted counters.

Longer sustained and in-headset acceptance follow only after correctness and
repeated short comparisons. No 120-FPS or latency improvement is claimed here.
