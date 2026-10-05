# One GPU submission, one prepared successor

October 5, 2026. **Isolated standalone prototype, not a streaming optimization
accepted on Quest.** The `.46` ALVR worker does not call the experimental APIs.
Defaults, queue policy, foveation and resolution are unchanged. Matching builds
and device correctness tests are required before integrating a worker call site.

The [measured opportunity](PRODUCER-OPPORTUNITY.md) was approximately 4.94 ms of
possible native-call overlap, compared with approximately 0.56 ms of command
recording. These observations do not predict delivered FPS or optical latency.

## What the prototype bounds

- One actually submitted GPU command and at most one prepared command. A second
  command buffer is allocated only by explicit `pyroclient_prerecord_enable`.
- Three output slots. A successor excludes the active output and both protected
  consumer outputs from one coherent snapshot; absence of a free slot defers it.
- Single worker owns the decoder, Vulkan calls, Granite context gate and tickets.
  Foreign-thread prototype calls and stale generations are rejected.
- Complete Haar/Compute/4:2:0 frames, fixed geometry, warmed decoder,
  `PYROWAVE_NO_LINEAR_TEX=1`, ready/release/stage probes disabled.
- Only existing payload upload capacity is allowed during preparation. Growth
  returns `-7`; a future worker must defer it until the earlier GPU work completes.
  Geometry/chroma mismatches are rejected by the codec sequence parser.

The next Granite frame context must pass its existing `wait(0)` gate immediately
before borrowed recording. The gate can lock and recycle completed fences; it is
not a promise that every subsequent CPU operation is nonblocking. Failure means
defer under a bounded health policy, never permission to reuse storage.

B's commands may be recorded while A executes. **B cannot submit before A's
fence succeeds and A's native queries are collected.** Shared payload/offset
storage, wavelet images, planes and conversion scratch therefore keep serial GPU
execution and the existing barriers. No producer completion wait was deleted.

## Cancellation needs more than resetting a command buffer

At pinned Granite `842d9d5`, `QueryPool::begin` uses
`VK_QUERY_RESULT_WAIT_BIT` for every allocated query. Resetting an unsubmitted
borrowed command alone would leave its stage timestamps unexecuted; later
context recycling or destruction could wait indefinitely.

The narrow prototype disables **only PyroWave's optional Granite stage timestamp
recording** on that decoder before experimental calls. It disables them for
both a control and a preparation arm. Native decode/conversion GPU timestamps,
completion fence and query collection remain active. The default API continues
to record the stage timestamps. No Granite source change or fake timestamp value
is used. Stage timing reports are unavailable in prototype mode.

Cancellation resets only the never-submitted spare command, restores output
first-use and plane layout bookkeeping, and clears CPU packet assembly. Staging
allocations, views and descriptors remain retained in their Granite context;
they are not recycled at cancellation. Future checked context advance accounts
for the earlier actual submission. Destructor drains the device and resets any
unsubmitted command before Granite destruction. These operations still require
device testing; source reasoning alone is insufficient.

## Verification and measurement boundaries

`prerecord_state_test.cpp` exercises the same native reservation helper: occupied
ring, one active/one prepared limit, observed completion, explicit input rejection,
generation reuse, cancellation before/after completion, poison retention and
10,000 mixed reuse cycles. CPU tests cannot prove GPU memory lifetime.

`prerecord_gpu_test A.wave B.wave` compares distinct synchronous references with
12 preparations, six cancellations and 24 exact A/B readbacks. It rejects a
second submission, a second preparation, missing completion-query collection,
foreign-owner calls, stale cancellation and a fully occupied ring. It tests
continued context reuse after cancellation and outstanding A+B teardown. At
native width it requires A's fence to remain unsignaled after at least one B
preparation. A matching source-built executable/library set is required.

The proof deliberately reads A before submitting B, increasing B's queue delay.
It is **not a throughput benchmark**. `total_ms` includes preparation-to-submit
delay; `record_ms` excludes that delay, and `wait_ms` starts at actual submit.
`pyroclient_prerecord_queue_ms` reports the delay separately. Never subtract it to
claim optical latency. Small/native readbacks, default-path regression and
payload-growth rejection remain device gates; no result is claimed here yet.

A future ALVR integration must reserve from a coherent consumer snapshot,
re-check the latest ingress order before submitting B and explicitly cancel a
superseded preparation. It must publish only completed outputs, preserve pose
pairing, bound any waits and retain ordinary synchronous fallback. A short A/B
can reject the candidate; promotion needs repeated pacing/latency/image-safe
improvement and later sustained/in-headset acceptance.
