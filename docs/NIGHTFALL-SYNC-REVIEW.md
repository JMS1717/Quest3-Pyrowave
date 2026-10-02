# Nightfall native-fence review

Source review on 2026-10-02; the review itself did not deploy a native change.
The latest matching development pair is now .28. Keep full-frame 4:2:0 and
synchronous producer completion as the defaults.

Follow-up: the [.26 release-fence candidate](RELEASE-FENCE-EXPERIMENT.md) implements
the first step below, default off. Its queued-read/reuse correctness checks passed,
but its first short live screen regressed fresh FPS; it has no validated live gain.

Reviewed Nightfall main at `e111b5c0825ad27be28d6584007c60cac2b017ea` (merge of
PR #47), especially:

- [d1a0f0b: zero-copy decode](https://github.com/tB0nE/nightfall/commit/d1a0f0b37ec1c41c3dce700d36ca064abc609b78).
- [a4beb30: fragment conversion / GPU cost](https://github.com/tB0nE/nightfall/commit/a4beb304335077b342d77f023e226c57e34980cc).
- [GPU pipeline](https://github.com/tB0nE/nightfall/blob/e111b5c0825ad27be28d6584007c60cac2b017ea/addons/nightfall-stream/src/video/pyrowave_gpu_pipeline.cpp),
  `decode_frame`, `completion_loop` and per-slot resources.
- [Texture uploader](https://github.com/tB0nE/nightfall/blob/e111b5c0825ad27be28d6584007c60cac2b017ea/addons/nightfall-stream/src/video/texture_uploader.cpp),
  `acquire/present_pyrowave_gpu_slot` and `_render_thread_present_pyrowave_gpu`.

## What transfers to our pipeline

Nightfall signals an exportable Vulkan binary semaphore after conversion,
exports its SYNC_FD, and inserts an EGL server wait before GLES sampling. It
returns an EGL native release fence for prior GLES reads; Vulkan temporarily
imports that FD into a semaphore before writing the reused slot. Its three
slots each have command/fence resources. It still waits when recycling submitted
Vulkan resources, and supersedes an unconsumed pending frame. A separate FD
polling thread reports completion.

Its reported ~0.5 ms is decode-thread recording/submission cost, not end-to-end
GPU decode, optical latency or our expected gain. Its earlier GPU readback and
CPU upload removal is already present in Quest3-Pyrowave. We also already use
fragment YUV-to-RGBA conversion in the best measured configuration.

| Boundary | Current Quest3-Pyrowave | Native-fence opportunity |
| --- | --- | --- |
| Vulkan -> GLES | `record_and_submit` waits on `vkWaitForFences` before publishing a completed AHB | Export ready SYNC_FD, enqueue `eglWaitSyncKHR` before either eye draw |
| GLES -> Vulkan | Synchronous `glFinish` covers both eye draws; consumer lease remains protected until the next dequeue | Export an EGL release FD after both draws; next Vulkan write waits on a temporarily imported semaphore |
| Output storage | Three AHB outputs per decoder; one leased and one pending handle protected | Explicit per-slot state plus fence ownership/generation |
| Vulkan resources | One command buffer, fence and query pool per decoder, reused after CPU completion | Per-slot command/fence/query resources before allowing multiple submissions |
| Latest-frame scheduling | One pending completed output; newer output can supersede it | Still needs a bounded freshness policy; a ring alone does not change it |

The current experimental async-eye path already replaces `glFinish` with
`glFenceSync`/`glFlush`, but CPU polls that fence and defers taking another frame
while the copy is pending. It retains source protection. This is not a
bidirectional GPU handoff and has not earned promotion over synchronous mode.

An AHB reference keeps allocation alive; it does not make concurrent reads and
writes safe. Our FOREIGN-family barriers must remain alongside semaphore waits.
Our GLES operation copies into **two OpenXR projection swapchains**. Nightfall
binds a streaming texture in Godot, so its callback placement is not a drop-in
replacement for our acquire/wait/release, view-pose and repeated-image rules.

## Does it explain the lost frames?

[Saved matched-counter analysis](../results/OUTPUT-SLOT-LOSS-2026-10-02.json)
found ~120 completed decodes/s, ~112-113 completed eye copies/s and ~7-8
superseded outputs/s. This localizes the counted loss after producer completion;
it does **not** prove `glFinish` causes it. Runtime wait/acquire cadence,
publication phase and deadline misses can still supersede outputs with native
fences. Nightfall's `present_pyrowave_gpu_slot` explicitly replaces pending
frames, so importing its ring policy cannot guarantee zero superseding.

Our later .25 eye timer measured roughly 0.62-0.66 ms of GPU draw time in short
instrumented cases, versus larger CPU renderer-call timings. Neither is a
measurement of removable synchronization alone: import, driver scheduling and
clock variation contribute. [Current evidence](../results/EYE-GPU-LIVE-2026-10-02.json).

**Assessment:** removing the render-thread completion wait is a plausible way
to recover deadline margin, not evidence that 120 fresh FPS will follow.
Removing the producer wait may reduce publication jitter and overlap CPU
recording with GPU work, but can also queue older frames and increase latency.

## Smallest safe implementation sequence

1. **Release handoff first, behind a default-off property.** Retain completed-frame
   producer publication and synchronous Vulkan decode. After both eye draws,
   export/flush an EGL native fence, transfer its FD with a slot generation, and
   allow source reuse only through a Vulkan semaphore wait. Replace the
   current CPU `copy_ready` dependency with explicit slot states; do not simply
   delete `glFinish` or stop protecting the source. Keep OpenXR image-release
   ordering and original frame poses unchanged. Retain the leased source when
   the renderer may redraw it; don't reintroduce reads after declaring it free.
2. **Then ready handoff and bounded asynchronous submission.** Allocate
   per-slot command buffers/fences/query ranges, preserve shared-plane ordering,
   and audit PyroWave/Granite upload-buffer lifetime before queueing another
   decode. Carry ready FD, slot generation, timestamp and ingress order in the
   FFI frame descriptor. GLES GPU-waits on the ready fence before sampling.
   Bound in-flight work to available slots; reject/defer rather than overwrite
   a sampled output or create an unbounded FIFO.
3. **Preserve honest metrics.** Report submitted, actually completed, published,
   superseded, eye-submitted and eye-completed counts separately. Completion
   requires a signaled fence, not merely successful submission. A background
   completion observer must validate poll result/revents and ignore old session
   generations. Pair frames with poses consistently; submitting an unfinished
   but fenced image is not yet a completed decode.

Check Vulkan SYNC_FD import/export capabilities and EGL native-fence/server-wait
extensions and entry points. Successful Vulkan FD import transfers ownership;
failed import leaves cleanup to us. Use explicit FD ownership, duplicate only
for independent telemetry, and close on superseding/teardown. Import failure
must hold the slot until **verified** completion or safely disable the feature.
[Vulkan import rules](https://docs.vulkan.org/refpages/latest/refpages/source/vkImportSemaphoreFdKHR.html),
[EGL native fence](https://registry.khronos.org/EGL/extensions/ANDROID/EGL_ANDROID_native_fence_sync.txt),
[EGL server wait](https://registry.khronos.org/EGL/extensions/KHR/EGL_KHR_wait_sync.txt).

Do not copy these failure paths unchanged: Nightfall's CPU FD fallback waits and
completion loop call `poll` with a timeout but do not inspect the result/revents;
the completion loop then invokes its completion callback regardless. Its EGL
server-wait result is also unchecked. These are source-level failure-path risks,
not evidence that its normal path fails on Quest. Use bounded, checked waits,
and preserve our safe synchronous fallback; never release on a timeout.

Before promotion, test delayed producer/consumer fences, ring exhaustion,
superseding, reconnect, generation reuse, import/export failure and FD leaks.
Verify both eyes/colored edges before 15-second on/off/on screening at identical
native resolution/120 Hz/420/bitrate/scene/temperature. Compare producer completion,
fresh FPS/p1, superseding, CPU wait, GPU time, latency and thermal behavior. Follow
screening with sustained and in-headset acceptance. Preserve the known-good
synchronous runtime throughout. Implement the API-level model independently;
Nightfall is GPL-3.0 and this review does not import its code or binaries.

## Producer lifetime audit after .28

The reconstructed sources match the pins in
[`fetch_sources.sh`](../tools/ci/fetch_sources.sh): PyroWave
`d2997ac172bdc00e29c58e3f2938acb7e94580bf` plus our cumulative patches,
and Granite `842d9d5686ba8c799a7d34a78a68f98d6aeb5a68`.
This is a source audit, not a new timing measurement or asynchronous candidate.

| Resource | Current lifetime | Required before overlapping submissions |
| --- | --- | --- |
| Command buffer, completion fence, three timestamp queries | One set in `pyroclient`, reset on each decode after the preceding synchronous wait | Slot-owned resources; reset/read/recycle only after verified completion |
| Packet payload and offset CPU vectors | Copied by `Decoder::Impl::decode` into `cmd.update_buffer` staging allocations during recording | Preserve complete-frame ingestion and copied allocation lifetime; retaining the original packet pointer alone is insufficient |
| Granite staging blocks, temporary views and descriptors | Owned/recycled through Granite frame contexts | Ensure every borrowed command is actually submitted on the tracked queue before advancing its context; preserve completion coverage on failures |
| Payload/offset GPU buffers and internal wavelet images | Shared by one PyroWave decoder | Audit read-before-overwrite dependencies across submissions, or isolate resources; queue order alone is not a substitute for memory dependencies |
| YUV planes and conversion scratch | Shared by all three AHB slots | Preserve dependencies through the last conversion/copy read before a later decode/write; the scratch fallback also needs review |
| AHB output | Three slots, with consumer lease/generation and optional release FD | Add ready FD and submitted/completed state without weakening the existing release protection |

The important additional constraint is **Granite's frame ring has two contexts,
not three**. `Device::set_context` initializes two; every
`pyrowave_decoder_decode_gpu_buffer` calls `next_frame_context`, including the
borrowed-command path. `CommandBuffer::update_buffer` allocates staging memory
and records a copy. Ending the borrowed wrapper moves those allocations to its
frame's recycling list; `submit_external` marks that queue as needing completion
coverage. Context advancement emits a covering submission, and `PerFrame::begin`
waits before recycling staging memory and other frame resources.
[Pinned device implementation](https://github.com/Themaister/Granite/blob/842d9d5686ba8c799a7d34a78a68f98d6aeb5a68/vulkan/device.cpp),
[pinned command-buffer implementation](https://github.com/Themaister/Granite/blob/842d9d5686ba8c799a7d34a78a68f98d6aeb5a68/vulkan/command_buffer.cpp).

Consequently, adding three command buffers cannot guarantee a nonblocking
producer: the third context reuse can still wait for earlier GPU work. Our
current synchronous path submits the borrowed command before the next decode;
that ordering must remain explicit in any asynchronous path, including after a
failed submission. A three-slot AHB ring does not extend the staging ring.

The smallest conservative candidate should start with **at most two in-flight
submissions**, per-slot command/fence/query ownership, and a checked readiness
gate before Granite context advancement. The upstream C wrapper currently offers
no nonblocking context gate; expose a narrow checked wrapper or retain a bounded
CPU wait at reuse, rather than bypassing context recycling. Keep one decoder on
the existing queue, add the necessary cross-submission resource dependencies,
and carry ready FD/generation/pose through the frame descriptor. Retain separate
submission and actual completion metrics. More contexts or per-slot decoders
would be later experiments with additional memory cost, not prerequisites to
claim an unmeasured speedup.

Before live use, prove delayed completion, two-context exhaustion, submit failure,
payload growth/reallocation, both conversion paths and teardown with outstanding
work. Existing software-GLES and release-FD tests do not exercise these producer
lifetimes. No producer wait has been removed by this audit.

The independently implemented `.26` release-FD step now has reviewed matching
builds and passing queued-read/reuse GPU checks. Its first complete off/on/off
screen reduced CPU copy cost, but fresh FPS fell to ~98 versus ~103/~112 controls
and superseding increased. It remains off by default. See the
[recorded outcome](RELEASE-FENCE-EXPERIMENT.md#recorded-outcome-on-quest-3).
