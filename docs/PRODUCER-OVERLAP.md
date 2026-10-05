# Bounded producer overlap: lifetime and opportunity audit

October 5, 2026. Source review only: no new APK, server, native library or
performance result. Synchronous completion remains the default.

Nightfall's per-slot commands and native fences are a useful ownership model,
but replacing our producer wait with its three-slot ring is not a safe local
edit. Our staging ring, shared decode storage and output leases impose separate
limits. The recent [LOW release-fence comparison](RELEASE-LOW.md) removed about
1 ms of CPU eye waiting without improving delivery. That is evidence against
assuming another removed wait will automatically recover the remaining frames.

## The measured opportunity is small and still unlocalized

The four reviewed `.44` native120 windows had median CPU recording times of
**0.521 / 0.546 / 0.534 / 0.540 ms**; p99 was **0.938 / 0.915 / 0.964 / 0.886 ms**.
The 120 Hz period is 8.333 ms. Recording therefore occupies about 6.3–6.6% of one
period at the median. These are existing measurements from
[the release comparison](../results/RELEASE-LOW-NATIVE120-2026-10-05.json), not a
new screen.

Overlapping some recording with preceding GPU work could reduce publication
jitter or CPU-to-GPU gaps. That is a hypothesis, not a predicted FPS gain:
producer completion was already approximately 120/s, eye completion remained
116.6–117.9/s, and the next complete packet may arrive too late to overlap.
Recording includes several operations; the current metrics do not separate
Granite context recycling from command recording. Do not subtract independent
percentiles, infer optical latency from these timings, or add a queued frame
just to make submission counters look better.

## A usable Granite gate already exists, with qualifications

At our pinned Granite revision, `Device::next_frame_context_is_non_blocking()`
checks the **next** frame context using `PerFrame::wait(0)`. The PyroWave C API
does not expose it. A narrow checked wrapper would be smaller than adding a
third staging context or replacing Granite.

It is not a general lock-free API: it first executes `DRAIN_FRAME_LOCK`, which
can wait for outstanding Granite-managed recording. Our borrowed command path
does not increment that managed-command counter, and `submit_discard` ends the
borrowed wrapper before returning. Preserve single-worker ownership and exclude
other managed recording before relying on the zero-time GPU check.

The check also has side effects on success: completed binary fences are reset,
recycled and removed from that context's list. It must be called by the same
owner that advances contexts. Failure returns `false` without making resources
reusable; it does not distinguish timeout from device loss. Treat failure as
defer/stop under a separate bounded health policy, never permission to advance.
The subsequent context advance still flushes work and submits completion
coverage, so the gate does not guarantee that the whole operation takes zero
time.

`submit_external` only marks a queue as needing completion coverage. It is not
the application's actual `vkQueueSubmit`. Every successful borrowed recording
must be submitted, or explicitly abandoned while unsubmitted, before another
context advance. Never let an empty covering submission stand in for an
unsubmitted application's command buffer.

References: [pinned device code](https://github.com/Themaister/Granite/blob/842d9d5686ba8c799a7d34a78a68f98d6aeb5a68/vulkan/device.cpp)
(`request_borrowed_command_buffer`, `submit_discard`, `submit_external`,
`next_frame_context_is_non_blocking`, `PerFrame::wait/begin`) and
[staging retirement](https://github.com/Themaister/Granite/blob/842d9d5686ba8c799a7d34a78a68f98d6aeb5a68/vulkan/command_buffer.cpp)
(`update_buffer`, `end`). Both were checked against the locally reconstructed pin.

## Three outputs do not guarantee two available producer slots

Our [native producer](../tools/pyroclient/pyroclient.cpp) excludes the consumer's
leased buffer and the completed pending buffer. With a third buffer being
decoded, **all three can be occupied**. This is a legal state, not proof that
every frame exhausts the ring. A second recording must reserve a distinct free
output and its generation; it cannot silently overwrite the pending image or
assume an acquired AHardwareBuffer reference prevents a race.

A fourth output would cover that worst case but costs about 35.0 MiB of RGBA
storage at 4160×2208, before allocation overhead. A split decode/conversion
command could postpone choosing an output, but changes submission boundaries.
Neither is necessary until measurements show useful overlap and slot pressure.
Conditionally preparing a successor only when a slot is genuinely free is the
smaller starting policy.

The three output slots also share payload/offset GPU buffers, wavelet images,
YUV planes and the compute-conversion scratch image. Existing upload and
transform barriers cover specific dependencies, not an independently verified
two-submission schedule. In particular, the scratch path starts its overwrite
with an UNDEFINED transition and no preceding transfer-read dependency. Preserve
the current completion guard until the fallback and every shared allocation
have explicit coverage. Queue order alone is insufficient.

## Smallest candidate worth investigating

First collect default-off, bounded scheduling diagnostics on the existing
single worker: complete-packet arrival, native recording start/submit/completion,
publication/selection deadlines, pending replacements and available output
slots. Use one monotonic clock and frame order. This should distinguish an
eligible packet waiting during GPU work from late ingress and presentation
deadline misses. Keep logging aggregated or bounded so it does not become the
bottleneck being measured.

If there is real opportunity, prefer **one GPU submission plus at most one
pre-recorded successor** before allowing two GPU submissions. Give the successor
its own command buffer and reserved output; do not submit it until the earlier
fence actually completes. Keep all decoder calls on one worker, gate Granite
context reuse, freeze geometry, and retain staging/view lifetimes. This overlaps
CPU preparation without requiring simultaneous writes to shared decode storage.
It is a design proposal, not implemented or GPU-validated. A recorded command
cannot be casually canceled: borrowed work and its Granite frame retirement
must be explicitly accounted for before reset/reuse.

Only proceed to two submitted decodes if that narrower design cannot address a
measured gap. Then add per-submission fences, query ownership and ready/release
semaphores, plus verified cross-submission dependencies for all shared storage.
Keep a strict bound and latest-frame policy; a completion thread or ring does
not by itself prevent superseding.

Before any live candidate: test delayed fences, no free output, context
exhaustion, packet/payload growth, recording/submission failure, reconnect and
outstanding teardown. Require exact small/native-size GPU readbacks for both
conversion paths. Promote only a repeated delivery/pacing/latency improvement
with correct image and pose pairing. Full native120 remains unmet.

Nightfall was reviewed at `e111b5c0825ad27be28d6584007c60cac2b017ea`, not assumed
to match future main. See the [original source and licensing review](NIGHTFALL-SYNC-REVIEW.md).
No GPL implementation has been imported.
