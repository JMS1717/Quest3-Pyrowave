# One GPU submission, one prepared successor

October 5, 2026. **Isolated standalone prototype, not a streaming optimization
accepted on Quest.** The `.46` ALVR worker does not call the experimental APIs.
Defaults, queue policy, foveation and resolution are unchanged. Matching builds
and standalone Quest correctness tests passed; streaming integration remains pending.

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
unsubmitted command before Granite destruction. The standalone checks below exercise these operations on Quest; source reasoning
alone would be insufficient.

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
preparation. It pads unused coefficient-packet tails to force upload growth,
requires rejection before recording while A is pending, then checks exact A and
exact B after a drained synchronous growth fallback. An independent 30-second
native alarm bounds a query or destructor hang; interrupted runs cannot report
success. A matching source-built executable/library set is required.

The one-submitted-decode limit refers to frame computation; Granite's internal
completion-coverage submissions and the diagnostic GLES readbacks still exist.

The proof deliberately reads A before submitting B, increasing B's queue delay.
It is **not a throughput benchmark**. `total_ms` includes preparation-to-submit
delay; `record_ms` excludes that delay, and `wait_ms` starts at actual submit.
`pyroclient_prerecord_queue_ms` reports the delay separately. Never subtract it to
claim optical latency. Small/native readbacks, default-path regression and
payload-growth rejection passed the standalone checks below.

A future ALVR integration must reserve from a coherent consumer snapshot,
re-check the latest ingress order before submitting B and explicitly cancel a
superseded preparation. It must publish only completed outputs, preserve pose
pairing, bound any waits and retain ordinary synchronous fallback. A short A/B
can reject the candidate; promotion needs repeated pacing/latency/image-safe
improvement and later sustained/in-headset acceptance.

## Quest correctness results: October 5

Matching `.46` source [`fd3903b`](https://github.com/JMS1717/Quest3-Pyrowave/commit/fd3903b25a6d59664d913e54a7d88e3eb521fb8a)
passed [all four Actions jobs](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37289807663),
including 111 Python tests and 48 production Rust ownership tests on Linux.
APK/server hashes, signing certificate, packaged native libraries and compiled
markers were reviewed. Native libraries changed, so earlier device proofs were
not reused. [Sanitized results and hashes](../results/PRODUCER-PRERECORD-GPU-2026-10-05.json).

| Check | Small 512×320 | Native stereo 4160×2208 |
| --- | --- | --- |
| Default fragment-conversion regression | Exact previous golden pixels | Exact previous golden pixels |
| Prerecord fragment conversion | 24 exact A/B frames; 12 preparations, six cancellations | Same |
| Prerecord compute conversion | 24 exact A/B frames; 12 preparations, six cancellations | Same |
| A fence still unsignaled after B preparation | 12/12 on each path | 12/12 on each path |
| Full ring / foreign worker rejection | 12 each per conversion | 12 each per conversion |
| Payload growth while pending / drained growth | Rejected / exact fallback | Rejected / exact fallback |
| Outstanding A+B teardown | Completed on both paths | Completed on both paths |

All three output slots were warmed before enabling the prototype. This does not
cover cancellation of an initially unused output. Before streaming integration,
require all slots warmed or add an explicit first-use cancellation proof. Actual
device/fence failures were not injected; portable CPU tests cover poison guards.

The installed `.45` streaming pair was retained. Temporary properties, session
values and physical proximity behavior were restored with readback; Virtual
Desktop registration was preserved. This is a correctness milestone, **not a
measured FPS or latency improvement**. Keep the normal synchronous path as default.

## Opt-in streaming integration candidate `.48`

Not device-accepted or promoted. Matching builds, renewed native proof and a
controlled streaming screen are required. `debug.q3pw.prerecord_mode=1` at client
startup requests the experimental worker. With `debug.q3pw.prerecord_active=0`,
it uses explicit single-submission start/poll/finish with no preparation.
`prerecord_active=1` additionally permits one successor preparation. Both arms
disable optional Granite stage timestamps and retain native completion queries.
Leave `prerecord_mode` empty or zero to retain the existing synchronous default.

The first `.47` streaming screen was excluded: eligibility passed, but native
enable rejected the missing `PYROWAVE_NO_LINEAR_TEX=1` allocation environment.
Standalone exact-pixel tests had set it before constructing the decoder. All
four captured windows stayed on the ordinary fallback; no preparation gain can
be inferred. Settings were restored. `.48` scopes that environment to the one
eligible decoder, before construction, and restores the prior value after native
destruction or constructor failure. Both experimental arms use the same allocation
setting; the default worker does not change its environment. Native libraries are
unchanged from `.47`; exact artifact identity is still required for proof reuse.

The Rust gate requires one TCP worker, exact native stereo geometry, Haar/Compute
4:2:0 and LOW priority policy. Other ready/release/async/handoff/event/stage/Surface
experiments and producer diagnostics are excluded. Before enable, the worker
observes three distinct completed output buffers; the native API independently
rejects any initially unused slot. Native startup can still reject unsupported
configuration, preserving baseline decoding before any experimental submission.

While A runs, the worker removes at most one complete pending packet and reserves
a genuinely free B output from the coherent consumer snapshot. Context, slot or
upload-capacity deferral restores that packet only if no newer receive arrived.
Successful preparation retains only timestamp/order/generation metadata, not a
second payload FIFO. After A's verified fence and query collection, publish A
normally. At the next producer selection point, newer pending input or a switch
to control cancels B; otherwise submit B with its original content timestamp and
receive order. Arrivals after that selection are eligible for the next successor.
Only completed outputs enter `FrameSlot`; no ready-fence early publication.

The existing render-loop fresh-frame wait (default 4000 microseconds) is unchanged.
`wait_producer_ready` is also preserved; it normally returns immediately with
copy handoff disabled. **There is no new 4 ms producer sleep.** Experimental
completion status polling sleeps 100 microseconds between checks in both arms,
with a one-second bound plus the existing checked drain. Cancellation/poison or
completion failure stops the decoder rather than releasing unsafe storage.

`Q3PW_PRERECORD` reports 120-completion windows, both arm counts, cumulative
prepared/submitted/canceled/deferred counts and mean queue/record/completion/GPU
times. Preparation-to-submit delay stays in native `total_ms`; do not subtract
it to claim latency. Mixed-arm windows must be excluded. CPU policy tests cover
eligibility, payload deferral races, latest-input cancellation across order wrap,
and the timestamp/generation association. They do not replace Quest image,
fresh-frame pacing, latency or sustained thermal acceptance.
