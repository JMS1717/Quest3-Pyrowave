# Next overnight run: native 120, with bounded deliverables

Prepared October 5, 2026. This is a plan, not authorization to resume the paused
headset workload. A future owner-approved session needs a fresh deadline and
current device/process state; the October 4/5 authorization and watcher are closed.

## Outcome and starting point

Aim for smooth 2080×2208 per eye, runtime 120 Hz, 1000 Mbps, 4:2:0, no foveation,
Vulkan PyroWave decode. Higher resolution/refresh follow native 120 acceptance.
Use the reviewed ordinary synchronous worker as the performance control.
The `.48` explicit serial worker is an experimental control, not automatically
interchangeable with the ordinary synchronous API. Review the `.49` safety-only
matching build before choosing a new baseline; its full artifact review is pending.

Last night's baseline was already approximately 118 eye completions/s. Prerecord,
async copies, release fences, notifications and payload sweeps did not produce a
repeatable gain. Saving about 1 ms of eye CPU waiting did not fix pacing. Native
completion is around 8 ms, close to the 8.33 ms frame interval. This suggests
investigating the completion/presentation critical path, but does not prove which
stage causes each miss. See [measured findings](PRODUCER-PRERECORD.md).

Commit to these deliverables, even if no candidate improves FPS:

1. A bounded per-frame trace and replay analyzer that locates individual misses.
2. One source-built scheduling candidate addressing the dominant measured cause,
   with an explicit comparison or a documented reason it cannot safely be tested.
3. A reproducible verdict, matching artifact hashes, rollback and remaining gates.

If the scheduling candidate is rejected early, use remaining implementation time
on an isolated decoded-video presenter milestone. Do not spend the whole night
repeating rejected flag combinations. A measured FPS improvement is a target;
it cannot be promised before the work establishes one.

## Reference time budget: six hours

The owner can choose another window. These are wall-clock budgets, including CI,
not permission to extend a user deadline. Reserve the final 20 minutes for restore
and handoff. Do not begin a phase whose worst-case runtime and cleanup will not fit.
The table allocates 340 minutes; retain 20 minutes of slack for recovery or CI.

| Budget | Work and exit condition |
| --- | --- |
| First 20 min | Refresh installation/source hashes, `.49` CI, driver/process ownership and device health. Verify unattended recovery and independent restoration. One short baseline; stop live work if it cannot recover safely. |
| Next 70 min | Implement bounded frame trace, analyzer and meaningful ownership/timing tests. Batch one matching Actions build. Produce a trace or a specific build blocker before proceeding. |
| Next 80 min | Classify misses and implement **one** mechanism-supported scheduling candidate. Build/review, check correctness, run a short controlled screen. Reject neutral/regressive results rather than expanding a flag matrix. |
| Next 90 min | If promising: diagnostic-off repeated comparisons, then unattended sustained validation. Otherwise: implement one isolated decoded-video presenter milestone, with no claim that a static Surface chart is streaming acceptance. |
| Remaining 80 min | Complete correctness/artifact gates, a controlled source-quality comparison if time permits, publish findings and finish restoration. The final 20 min belong to shutdown/handoff. |

The approximate 60/30/10 implementation/validation/reporting split is a planning
target, not a reason to omit a required correctness gate. After 40 minutes of
unchanged CI waiting, work on the next independent source deliverable; do not
poll continuously or run the same headset test to occupy the wait.

Batch coherent changes before cloud builds. Reuse previously reviewed native or
server binaries only when their protocol/ABI compatibility is established and
the exact hashes/source provenance are recorded. Change-required compilation and
tests still run; a source or protocol change invalidates affected cached proofs.
Do not manufacture a matching pair by relabeling an old binary.

## First implementation: trace real frame ownership and deadlines

Use client ingress order as the diagnostic frame identity, bound to its decoded
buffer generation and original content/pose timestamp. ALVR tracking timestamps
can repeat; they are not frame sequence numbers. Do not report their gaps as exact
video drops. Client receive order alone does not prove distinct source pixels.

Record, in a fixed-size in-memory ring with explicit overflow accounting:

- Complete packet available; native record/start; GPU completion observed.
- Publication, superseding/cancellation, consumer selection and buffer generation.
- OpenXR wait/begin, predicted display time and period, image acquire/wait.
- Eye-copy start/completion, swapchain release and frame-end call/return.
- Both selected eyes' common frame identity and pose timestamp.

Keep host/device/`Instant`/OpenXR clock domains explicit. Use a validated supported
clock conversion or paired calibration with uncertainty; never subtract unrelated
clock values. GPU execution, CPU completion observation and predicted display
time are different events. Missing/overflowed traces must be rejected.

Do not log every event synchronously to logcat or add blocking GPU readbacks.
Dump the bounded trace outside the measured interval. Compare trace off/on once
to check perturbation. Replay tests cover repeated tracking timestamps, sequence
wrap, superseding, stale generations, overflow, clock uncertainty and session reset.

The analyzer must distinguish:

1. No complete input available in time.
2. GPU decode completed too late for the observed application scheduling window.
3. A completed output was superseded or selected too late.
4. Eye copy or application frame submission exceeded its scheduling budget.
5. Unknown/runtime behavior that these software events cannot explain.

The predicted display timestamp is **not** a disclosed compositor deadline or
optical measurement. Bound conclusions to the events actually recorded.
OpenXR adjusts frame-loop timing based on submissions, so measure each interval
rather than assuming a fixed phase from the 120 Hz request.
[Frame timing requirements](https://registry.khronos.org/OpenXR/specs/1.1/man/html/xrWaitFrame.html).

## Choose one change from the trace

If completed frames arrive in the usable application window but selection misses
them, the first candidate is deadline-aware selection: bound the wait using the
current OpenXR timing and measured copy/submission reserve. Preserve the completed
frame lease, both-eye identity and synchronous fallback. This replaces a fixed
selection policy only if the trace supports it; it is not another fixed-wait sweep.

If input or GPU completion is late, change only the dominant identified stage.
Do not force a presentation fix, raise bitrate, or allow two GPU decodes to overlap
shared query/upload/scratch resources. A multi-flight decoder is a separate
resource-lifetime project, not a quick deletion of its completion wait.

If the presentation bridge is implicated and selection offers no safe gain,
prioritize a real decoded-video presenter prototype. Select **one** architecture
after a bounded audit: the existing Android Surface route only if exact decoded
image/pose selection can be established, otherwise an isolated Vulkan-bound
OpenXR session with the runtime-selected device and explicit shared-device contract.
Do not build both competing presenters during the same night.

Its first useful milestone is moving asymmetric **decoded A/B frames**, retaining
their identities and matching projection poses across reuse and shutdown. Cover
orientation, range/gamma, chroma edges, right-eye crop and disconnect/resize.
Migration must account for the lobby, overlay and non-PyroWave fallback, not just
the video blit. Keep it isolated until those contracts work. A Vulkan binding
requires runtime-compatible instance/device/queue handles; an arbitrary decoder
device is insufficient.
[Binding requirements](https://registry.khronos.org/OpenXR/specs/1.1/man/html/XrGraphicsBindingVulkan2KHR.html).

## Test for decisions, then for endurance

- Keep 2080×2208 encode, 120 Hz, 1000 Mbps, 4:2:0, no FFE, source scene, overlay,
  decoder priority and transport unchanged within each comparison.
- Use 8–12 second screening windows after startup/settling, with verified actual
  activation and source coverage. One neutral/rejected ABBA ends that candidate.
- A promising candidate earns a second diagnostic-off ABBA in a fresh session.
  Measure distinct completed frame identities, missed runtime intervals, pacing,
  frame age, completion tails, CPU/GPU times, payload and thermal behavior.
- Gains must exceed measured control variation. A provisional promotion gate is
  at least 1.5 additional fresh completions/s or at least a halving of missed
  application intervals in both comparisons, without meaningful frame-age,
  completion-tail, image or thermal regression. If already near 120, prioritize
  reduced misses and age over that absolute-rate threshold.
- Only then run two 5–10 minute unattended sustained checks. Repeated sustained
  near-120 software delivery is a milestone; formal smooth native120 acceptance
  still needs correct content/pose behavior and an owner gameplay check.
- No optical motion-to-photon claim without an actual optical measurement.

For render-quality validation, use a source that actually renders at the larger
recommended size before encoding native resolution. The previous native-sized
fixture cannot demonstrate supersampling improvement. Compare matched detailed
scenes/text at both source sizes, retaining native decode resolution. This is
secondary to pacing, and visible headset quality still needs owner acceptance.

## Work that stays closed unless new evidence changes it

No unchanged packet-grace, notification, bitrate, async/release, vector-dequant
or Haar flag sweeps. No 4:4:4, foveation or 207/240 expansion during the native120
critical-path experiment. Preserve all candidate code and measured results.

At each phase boundary update a compact scorecard: baseline, actual candidate,
time spent, artifact/build status, correctness gate, FPS/misses/age/tails, decision
and next deliverable. If 90 minutes pass without an executable deliverable or
usable trace, reduce scope and finish one module; do not start another experiment.

Before stopping: stop only owned work, restore temporary properties/proximity
and original sessions with readback, preserve VD, clear verified owned locks,
publish reviewed findings as JMS1717, and mark unfinished gates explicitly.
Never promote a neutral result just to produce a release.
