# Engineering handoff: Quest3-Pyrowave

Prepared October 4, 2026. Read [AGENTS.md](../AGENTS.md) first. Machine-specific
state, raw captures, signing material and rollback snapshots stay outside this repo.

## Objective and acceptance

First achieve **2080 × 2208 per eye, 120 Hz, 4:2:0, no foveated encoding**, with
PyroWave decoded directly on Quest 3 Adreno using Vulkan. Prioritize fresh frames,
frame pacing, image correctness and latency over bitrate or headline refresh.
The current bridge imports Vulkan AHardwareBuffers into GLES/OpenXR eye images;
presentation is not entirely Vulkan.

Later explore higher resolutions and runtime-supported 207/240 Hz. The owner's
aspirational endpoint is 3072 × 3216 per eye at 207 Hz and 15–20 ms
motion-to-photon. Neither that latency nor sustained native 120 fresh FPS has
been established. Runtime acceptance, submitted frames, GPU completions and
fresh displayed frames are different measurements.

The owner permits improving the workflow, test duration and architecture when
supported by evidence. Preserve correctness, rollback and honest measurements;
the previous agent's process is not mandatory. A handoff does not automatically
resume paused hardware work or unattended workers.

## October 6: 240 Hz streams, higher resolution and refresh measured

`.56` (`79e38af`, signed CI 37396414259, installed) reports a system-forced 240 Hz display to the
server, so SteamVR runs at 240 Hz; `tools/quest3/refresh_scaling.py` applies and restores the
override reliably. 240 Hz exists only as a 3104x1664 panel mode (1552x1664 per eye). Short screens:
229.7 fresh FPS at 1280x1376 and 222.7 at 1440x1536 per eye; 207 Hz reaches 196 at 1440x1536 but
only 117 at native size; above native size at 120 Hz the decoder sets the rate (89 at 2560x2720,
65 at 3072x3216). GPU level 7 helps 240 Hz slightly; LOW priority above 120 Hz, server phase lock
and PR #8's fused kernel (correct, no speedup) are not adopted. Details and limits:
[HIGH-REFRESH.md](HIGH-REFRESH.md), [REFRESH-RATES.md](REFRESH-RATES.md).

## Current engineering state

PR #3 integrates diagnostics and safety work while keeping the experiments off.
See the [integration review and excluded Haar candidate](PR-3-REVIEW.md).
The owner's Codex hardware pause remains in effect; merging source does not
resume unattended tests. Installed-build statements below are historical records,
not fresh device readbacks. The [scorecard](WHOLE-STACK-SCORECARD.md) records the
later `.50`/`.51` screens and the reported `.51` installation.

**Chip-level levers (October 5, draft PR #7, not run on hardware):** [ADRENO-740.md](ADRENO-740.md)
and [XR2-GEN2-SOC.md](XR2-GEN2-SOC.md) rank GPU, CPU, memory, DSP, USB and power levers. Built
behind default-off flags: `debug.q3pw.lpac`, `debug.q3pw.eye_invalidate` and
`debug.q3pw.thread_hints`. The always-on `[Q3PW_GPU_CAPS]` and `[Q3PW_SOC_CAPS]` logcat lines
say which of them this firmware exposes, so read them first in the next authorized session.

The `.48` prerecord worker is now active and measured, but **not promoted**.
All matching builds passed; actual native activation, preparation and direct-only
eye completions were verified. Same-session ABBA eye completion proxies stayed
116–117/s and p1 near 60; preparation worsened native completion tails to about
12.3 ms p99 versus 8.5–8.6 ms control. Keep sync default and prerecord off.
Estimated latency shifted about one runtime interval, not an optical gain.
See [measurements, hashes and limitations](PRODUCER-PRERECORD.md).

Installed reviewed `.48` is left with the mode off; temporary properties and
both sessions were restored, physical proximity restored, Virtual Desktop-only
driver registration and no VR/client/capture workers verified. Older pairs remain.
Native libraries EXACT `.47` reuse its small/native exact-pixel proofs including
partial-warm rejection. `.47` streaming windows remain excluded for inactive
allocation. Source fixture was native-sized; no supersampling quality claim.

Latest [LOW+release handoff](RELEASE-LOW.md) confirms exact native-size fenced
reuse and active GPU imports. CPU waiting drops ~1 ms and copy deferrals vanish,
but delivery/p1 do not improve. Keep release off; investigate producer resource
lifetime and presentation scheduling before another mechanism.

Latest [LOW+async comparison](ASYNC-LOW.md) verifies actual GPU-fence polling:
CPU eye-render time fell ~1.63→0.59 ms, but eye completions stayed ~117/s and
p1 near 60. Keep synchronous default; no sustained or optical acceptance.

| Item | Evidence / limitation |
| --- | --- |
| Public release | [alpha.7](https://github.com/JMS1717/Quest3-Pyrowave/releases/tag/v0.1.0-alpha.7), matching `.15` APK/server |
| Development pair | Reviewed `.42` (`ef1e8cc`), all matching CI jobs and exact default/three candidate GPU readbacks passed. Dedicated Haar kernels remain off by default after three short comparisons; see [findings](HAAR-PAIRS.md). `.41` static Surface orientation/lifecycle passed; normal video remains on GLES. Older matching pairs retained. Recheck actual installation before hardware work |
| User feedback | Positive manual playtest after overlay/OpenXR repairs; not sustained FPS, optical latency or broad game acceptance |
| Best short native screens | `.33` at 120 Hz / 1000 Mbps / 4:2:0: about 116–118 displayed target FPS (2.4–4.8 lost/s across sessions, `.34` unfiltered arm included) with the default wait and LOW decode priority. Without the priority it was 110–114, and without the wait 107–110 ([fresh-frame loss](FRESHNESS.md), [decode priority](DECODE-PRIORITY.md)). Stationary chart, not sustained gameplay |
| Baseline recommendation | Haar/Compute, full-frame native encode, USB/TCP, 120 Hz request, 1000 Mbps, 4:2:0; experimental fence paths off |
| 4:4:4 | Optional quality mode. Prior matched screens regressed performance; spare bandwidth does not make it free |
| 2000 Mbps | Short idle/Home comparison increased estimated latency about 11 ms versus 1000; not a controlled gameplay/optical measurement |
| High refresh | 144/207 requests accepted on tested OS. Short 144 Hz screens: about 134 displayed FPS at 1000 Mbps / 4:2:0, about 74 at 2000 Mbps / 4:4:4 (decode-bound). No sustained delivery claim. 240 rejected in tested configuration |

See [manual playtest](PLAYTEST-2026-10-02.md), [decode findings](DECODE-PIPELINE.md),
[chroma](CHROMA.md), [bitrate](BITRATE.md) and sanitized JSON under `results/`.
The current local session can differ from this historical baseline. Refresh live
state before testing; the private handoff includes a fresh disk snapshot.

## Read these first

1. [Fresh-frame loss](FRESHNESS.md): measured cause, `.31` default wait, interleaved A/B method.
   [Decode priority](DECODE-PRIORITY.md): `.33` eye copy preempts decode at ≤120 Hz / 4:2:0.
   [Compositor filtering](COMPOSITOR-FILTER.md): `.34` opt-in supersample/sharpen cost.
2. [Ready-fence experiment](READY-FENCE-EXPERIMENT.md): correct on GPU, rejected live; opt-in only.
3. [Nightfall synchronization review](NIGHTFALL-SYNC-REVIEW.md): ownership/lifetime audit.
4. [Independent render/encode resolution](RENDER-ENCODE-RESOLUTION.md): already implemented.
5. [Benchmarking](BENCHMARKING.md), [build](BUILD.md), [unattended safeguards](OVERNIGHT.md).
6. [OpenXR routing](OPENXR.md), [overlay](OVERLAY.md), [light foveation](LIGHT-FOVEATION.md).

## Highest-value next experiment

**207 Hz branch (October 5, no hardware run):** [PATH-TO-207.md](PATH-TO-207.md) adds
Balanced and Strong peripheral profiles (about 24% and 35% fewer encoded pixels,
candidates), an optical latency stamp and a no-decode cadence probe. Fused final
colour is #4's Quest-verified implementation ([FUSE-COLOR.md](FUSE-COLOR.md)): byte-exact,
no live 120 Hz gain, off. #8 adds an experimental fused dequant + level-0 Haar kernel behind
`debug.q3pw.dequant_haar` with a software exact-pixel gate; the client refuses it while fused
colour is active. The hardware plan starts with the Quest `dequant_haar_gate` and the 207 Hz
lobby cadence.

Use the [next overnight plan](NEXT-OVERNIGHT.md) and the
[October 5 scorecard](WHOLE-STACK-SCORECARD.md). The server already submits
about 120 frames/s. About 3 unique targets/s are still missed after the
half-frame wait, and that wait is already at its cap. `.49` passed review with
native libraries identical to `.48` and is not installed.

**October 5 measurement:** `.50` (`d0a77ef`) counted those expiries. In the
steady 25-second window every empty expiry was still decoding (56/56) and none
were idle. Unique targets stayed 117.7/s with 3.9 lost/s. GPU decode was about
5.9 ms and conversion about 0.8 ms, inside an 8.0 ms fence. Do not repeat
prerecord, async copy, release fences, or another wait sweep, and do not build
a presenter to recover these misses. Two concurrent GPU decodes remain unsafe
with current shared scratch/query/upload lifetimes. The open lever is shortening
that 5.9 ms GPU decode. An explicit 6 ms wait was measured on `.51` and is not
the default: unique targets did not move, and compositor stale counts roughly
doubled.

**Haar [3,2] live, October 5:** Reviewed `.52` (`f3a7184`) was measured, then
the branch removed that candidate. See [the format defect](PR-3-REVIEW.md).
Binding 2 is declared `r16f` while the final plane is `R8`. A 12 s off/on/off
at 120 Hz, 1000 Mbps, 4:2:0, 4160×2208 still ran: GPU decode went from 5.91 ms
to 6.37 ms p50, unique targets from about 118/s to 112/s, and lost targets from
about 2.3/s to 7.6/s. The flag was restored off. The standalone −50% result was
a different device, a locked 788 MHz clock, and 4:4:4 with no compositor. On
Quest the GPU sat at 640 MHz and about 87% busy, with roughly 700 preemptions
per second. Drop this port. A corrected shader needs an exact-pixel proof
before another live comparison. Adreno reports a 64-lane compute subgroup and
32 KB of shared memory; that does not explain the 5.9 ms, and retuning this
[3,2] port is not justified.

**Reported fused final color, October 5:** `.53` (`656a81b`) skips the final luma Haar
store when `debug.q3pw.fuse_color=1` and writes RGBA from that wavelet plus the
4:2:0 chroma planes in the existing fragment pass. A 12 s off/on/off at 120 Hz,
1000 Mbps, 4:2:0, 4160×2208: combined GPU decode+convert 6.62 ms to 5.40 ms
p50 (−18%), fence 7.94 ms to 6.89 ms (−13%), lost targets about 3.8/s to 1.7/s.
Unique targets moved only about 117/s to 118/s. These are preliminary reported
short-screen results, not sustained or exact-pixel acceptance. That implementation
is preserved on `experiment/fuse-color-review` and excluded from this integration;
`debug.q3pw.fuse_color` is not implemented by the integrated `.51` decoder. Require
exact-pixel and setting-lifetime checks before proposing it for integration.
Native 120 sustained/optical acceptance remains unmet. The historical evidence
below records earlier hypotheses; completed experiments are not pending work.


**Producer audit, October 5:** [bounded overlap](PRODUCER-OVERLAP.md) found an
existing Granite context-readiness method, but it has locking/recycling side
effects and is absent from the C API. Three AHBs can all be occupied by a lease,
pending output and active decode. Measure packet-arrival/recording/publication
overlap and free-slot availability before implementing a second submission.
A conditional pre-recorded successor with only one GPU submission is a smaller
proposal; no wait has been removed and no new native artifact was built.

**Latest payload screen, October5:** same-session native120/4:2:0/LOW comparison
of1000/800/600/800/1000Mbps used verified live directives and exact frame-byte
caps. Controls agree near118.1 eye completions/s;800 varied118.4→117.5,600 reached
118.7 once. GPU/completion times did not decrease consistently; p1 remains near60.
Keep1000 default and avoid an unchanged sweep. [Metrics and limitations](BITRATE.md#current-native120-payload-isolation-october5).
Next inspect presentation/completion scheduling. Earlier async-copy screens
predated the LOW decode-priority change; audit ownership and the actual prior
configuration before deciding whether that combination is a new useful test.

**Latest CPU scheduling screen, October 5:** `.44` replaced repeated50µs
selection sleeps with a bounded condition-variable notification, off by default.
All matching CI jobs passed, including40 Linux production decoder tests with
nine new race/FD cases. Its native libraries are byte-identical to GPU-verified
`.42`. The candidate executed223/194 real waits with zero fallback, but completed
115.82/116.03 eyes/s versus controls118.28/115.54. No consistent delivery gain;
keep it disabled and avoid an unchanged repeat. Private compositor images retain
correct orientation and eye mapping; sustained/optical acceptance remains unmet.
[Source, matching artifacts, replay tooling and sanitized findings](https://github.com/JMS1717/Quest3-Pyrowave/blob/experiment/publication-event/docs/PUBLICATION-EVENT.md).
Main retains `.42`; `.44` remains a documented experiment. Next isolate payload
versus fixed reconstruction/completion cost with the current native120 path,
or investigate removing the GLES bridge with exact image/pose ownership.

**Latest rejection, October 5:** the reviewed `.43` vector-dequant branch passed
all matching builds and six exact GPU checks (baseline/vector/combined at two
sizes). Stage-enabled medians suggested small headroom, but the diagnostic-off
repeat found no consistent GPU, completion or delivery gain. Keep it disabled;
do not repeat unchanged screens. [Implementation and measured findings](https://github.com/JMS1717/Quest3-Pyrowave/blob/experiment/dequant-vector/docs/DEQUANT-VECTOR.md).
Main retains `.42`; `.43` stays a documented experiment with matching artifacts.
The subsequent publication-notification screen above found no consistent gain;
notification itself does not establish GPU or optical completion.

**Latest kernel screen, October 5:** `.42` row-wise Haar lowered inverse-transform
stage averages to 2.8–3.0 ms versus roughly 3.1–3.6 ms, but delivery still overlaps
115–118 completion events/s with p1 near 60. Candidates ended at599MHz and controls
at640MHz. Diagnostic-off repeats suggest a small benefit, insufficient for default
promotion or sustained120 acceptance. Dequantization remains about2.6–3.0ms.
Read [source, matching build, exact GPU checks and three comparisons](HAAR-PAIRS.md).
Next examine dequant shader work or remove the GLES bridge with exact image/pose
ownership. Avoid repeating the same screens without a new hypothesis.

**Latest update, October 5:** `.40`'s tiny static Surface image
passed one-shot Vulkan enqueue, layer submission, both fences and orderly
STOPPING retirement on Quest 3. A private compositor screenshot exposed a
vertical flip. `.41` corrected rows only for that diagnostic in the existing
GLES-bound session. Matching builds, exact default GPU readbacks, screenshot
orientation and orderly shutdown passed; video/pose identity remains unverified.
Read [the result and remaining gates](SURFACE-CHART.md#quest-3-result-lifecycle-passed-orientation-rejected).
This screen found no speedup and does not establish live decoded-video identity,
color fidelity or pose/content pairing. The normal GLES path is retained.

Awake `.38` stage diagnostics showed dequantization (~2.6–2.8 ms) and inverse
transform (~3.2–3.6 ms) both contribute. Diagnostics remain off. Restarts yield
roughly 115–118 submission/completion events per second with p1 near 60; smooth
sustained native 120 remains unmet. Read
[the stage comparison](DECODE-STAGE-PROBE.md#awake-vr-comparison).
A Surface handle cannot enter the ordinary OpenXR eye acquire/release loop.
Establish exact decoded-image selection and matching pose before replacing that
loop. The following entries document earlier hypotheses.

**Update 2026-10-04 (evening):** removing the convert pass is not possible
this way: Quest 3 gralloc has no R8 AHardwareBuffer, so GLES cannot sample the
decoded planes ([details](DECODE-PIPELINE.md)). `.34` adds opt-in compositor
supersampling/sharpening. `supersample_hq+sharpen_hq` costs about 0.26 ms of
compositor GPU and no measurable FPS at 120 Hz ([details](COMPOSITOR-FILTER.md)).
It needs a headset-on visual comparison before it can become a default.
Larger lead: present through `XR_KHR_android_surface_swapchain` with Vulkan WSI
from the convert pass. That would remove the GLES eye copy (p50 about 1.55 ms
CPU), but pose/content pairing is the risk.

**Update 2026-10-04 (later):** with equal GPU priority, the GLES eye copy
queued behind the Vulkan decode (eye-copy CPU p90 about 7.5 ms). A LOW decode
queue fixes that at 120 Hz (+3–5 displayed FPS, half the lost frames) but costs
about 10 FPS at 144 Hz, where decode is throughput-bound. `.33` applies it only
at ≤120 Hz / 4:2:0. At 144 Hz / 1000 Mbps / 4:2:0 the GPU is close to full:
decode about 5.4 ms, convert 0.75 ms and copy about 0.65 ms in a 6.9 ms frame.
Next: remove the YCbCr→RGBA convert pass by sampling the decoded planes in the
eye copy. [Details](DECODE-PRIORITY.md).

**Update 2026-10-04:** the corrected ready-fence GPU probe passed on Quest 3.
Saved captures show lost frames come from publication landing 1–2 ms before the
post-`xrWaitFrame` selection, not from transfer or decode spikes. Interleaved
live screens on `.30` showed a bounded wait while a frame is decoding raises
displayed target FPS at 120 Hz / 1000 Mbps / 4:2:0 from about 107–108 to
113–116. Ready-FD early publication lowered it. `.31` enables the wait by default
(`debug.q3pw.frame_wait_us=0` disables). The saved 144 Hz / 2000 Mbps / 4:4:4
profile is decode-bound at about 74 displayed FPS. See
[fresh-frame loss](FRESHNESS.md).

Next: the remaining loss is late packet arrival, not decode. Check server send
pacing against the client's selection phase. Measure 144 Hz at 1000 Mbps /
4:2:0, where decode (about 7 ms) is near the 6.9 ms period. Then validate in
gameplay. The original `.29` notes follow.

`.29` adds default-OFF `debug.q3pw.ready_fd=1`: publish the AHardwareBuffer and
Vulkan SYNC_FD early so the renderer can enqueue a checked EGL GPU wait. It
retains one in-flight decode, producer completion checks before codec/resource
reuse, and synchronous completion of both eye copies. It may remove a CPU
handoff delay; it does not yet overlap multiple producer decodes.

The first small GPU probe was **inconclusive**: its post-decode
`pyroclient_is_ready` assertion confused packet readiness with GPU completion.
The first fenced read matched the reference, but that does not prove the full
lifecycle. Commit `e32596f` corrected the probe to use fence completion, GPU
queries and exact readback. Its diagnostic build succeeded, with three native
libraries matched byte-for-byte to the reviewed `.29` APK. **The corrected probe
has not run on-device; the ON/OFF live comparison remains pending.** Preserve
failed evidence instead of overwriting it.

Suggested sequence, adaptable by the next developer:

1. Audit source and corrected probe; establish installed binary hashes and a
   recoverable baseline before touching hardware.
2. Once hardware use is authorized and available, run corrected GPU correctness
   checks under a new output label. Stop on corruption, timeout or lifetime failure.
3. If correct, compare OFF/ON/OFF with identical geometry, chroma, source,
   thermal conditions and overlay state. Count native completions, actual eye
   copies, superseded frames, p1/gaps and estimated latency separately.
4. Keep optional unless a repeatable benefit appears. Do not simultaneously
   increase bitrate, enable 4:4:4 or change foveation.

Future multi-flight work must cover slot-owned commands/fences/queries, shared
YUV/codec/scratch resources, staging, descriptor lifetime, failures and teardown.
**Granite defaults to two frame contexts; the AHardwareBuffer ring has three
slots.** A third output slot does not extend staging lifetime. Do not delete a
completion wait without bounded ownership and cross-submission dependencies.
Nightfall is inspiration, not proof its model can be transplanted unchanged.

## Source layout and reproducibility

This publishable repo contains cumulative patches and canonical helpers. Local
ALVR/PyroWave trees are reconstructed inputs, not additional publishable repos.
[sources.lock.json](../sources.lock.json) pins upstream inputs.

`sh tools/ci/fetch_sources.sh <new-destination>` reconstructs ALVR, PyroWave and
Granite, applying research patches followed by Quest patches. Use a new
destination; never overwrite existing reconstructed trees.

| Change | Canonical location |
| --- | --- |
| Native decode / AHB bridge | `tools/pyroclient/pyroclient.cpp`, `.h`, fence helpers and GPU probes |
| ALVR client, server, settings | `patches/quest3-alvr.patch` |
| PyroWave integration changes | `patches/quest3-pyrowave.patch` |
| Ready-FD helpers | `tools/fences/native_ready.rs`, `ready_wait.rs`, `ready_frames.rs` |
| Light peripheral mapping | `tools/foveation/light.glsl` |
| Benchmark/control tools | `tools/quest3/`, `tests/` |

The fetch script copies the four canonical Rust/GLSL files into ALVR **after**
patching. Edit repo originals and mirror them locally as needed; exclude duplicate
copies when regenerating the cumulative patch.

Local reconstructed trees contain a staged research baseline and unstaged Quest
changes. Preserve both. Diff against the correct research baseline, include new
files deliberately, and use Git's binary patch output to avoid PowerShell
encoding changes. Validate reverse/forward application and fresh reconstruction
before publishing. A change absent from canonical patches/helpers disappears in CI.

Useful reconstructed paths: `alvr/client_core/src/video_decoder/`,
`alvr/graphics/src/{stream,direct_eye}.rs`, `alvr/client_openxr/src/`,
`alvr/server_openvr/cpp/platform/win32/{FrameRender,VideoEncoderPyroWave}.cpp`,
`alvr/server_core/`, `alvr/session/src/`, and PyroWave decoder/Granite resources.

## Settings and presentation traps

- `emulated_headset_view_resolution` sets SteamVR recommended per-eye source
  size (`target_eye_resolution_*`). `transcoding_view_resolution` sets stream
  size (`eye_resolution_*`). Both align up to 32: 3072 × 3216 becomes 3072 × 3232.
  Existing PC composition downsamples; separation needs no codec redesign.
- Global/per-app/game scaling and cached recommendations change actual game
  submission size. Verify textures; settings alone are insufficient.
- Direct-eye compatibility concerns decoded and Quest eye sizes, not equality
  between PC source and encode size.
- Optional `.2` sharpening uses neutral color controls; it cannot recover
  discarded detail and can produce halos.
- Forced `debug.q3pw.overlay_visible=1` overrides controller toggling. Clear for
  manual use. Click both thumbsticks and release both before rearming.
- SteamVR OpenXR fixed a VDXR form-factor failure with ALVR active. Preserve
  Virtual Desktop's service/driver and exact rollback.
- Light foveation stays optional: its screen reduced pixels 11.594% and GPU
  decode time but did not establish sustained/perceptual acceptance.

## Efficient validation and publication

Early performance screens can usually run 5–15 seconds after verified startup
and source coverage. Promising results still need repeated sustained
thermal/gameplay validation. Improve test design rather than rerunning everything.

Use [CI](../.github/workflows/ci.yml) for heavy builds while the PC may be used:

```text
python -m unittest discover -s tests -v
gh workflow run ci.yml --ref main -f tests_only=true
gh workflow run ci.yml --ref main
gh workflow run native-probes.yml --ref main
```

Choose checks appropriate to the change. The latest suite included 77 Python
checks, three chart regressions, Rust/portable C++ ownership checks and software
GLES mapping/readback. `tests_only` produces no installable pair; native-probes
produces diagnostics, not a release. Native/protocol/shader edits need matching
reviewed builds; check packaged library/shader hashes, version, certificate and
provenance before deployment.

Start the stereo fixture after the client settles. Use normalized charts with
explicit source sizes; verify scene coverage, FOV, reference hashes, actual
submitted size and current process/session provenance. Under early publication,
generic ALVR decode/queue timings can reorder; use native timing and full-loop
estimates. Optical motion-to-photon remains unmeasured.

Overlay, eye timers, release-FD, frame wait/poll, scheduler, light-foveation and
geometry screens are already documented. Rerun to answer a new question or
resolve uncontrolled evidence, not to retrace history.

Commit/push only to `JMS1717/Quest3-Pyrowave` as JMS1717 with the configured
noreply email. Retain upstream credits/licenses and PayPal support links. Keep
raw captures, sessions, serials, keys and rollback material private. Passing CI
alone does not justify a release or a performance claim.


## October 5 `.55` integration completed

[PR #9](https://github.com/JMS1717/Quest3-Pyrowave/pull/9) merged after the signed
`2b289f8` install and 26 valid short comparison windows. The corrected 207 Hz
no-decode probe reached 206.9 FPS/15s; no 207 streaming or optical proof. Optional
profiles decode less but Strong completion 7.12 ms still misses 4.83 ms. Adaptive
filter tail pacing was weaker, so amended `9da8560` keeps Bilinear fresh/legacy
defaults and explicit Adaptive choices. All five signed CI jobs and matching-pair
review passed; all three native decoder libraries EXACT tested2b. [Report](PR-9-REVIEW.md).

Temporary hardware settings/proximity were restored and VD registration retained.
Tested2b pair remains installed, amended pair available but not deployed. Old
pairs and private evidence are preserved. Sustained native120/gameplay, menu
Apply/restart, perceptual quality and optical latency remain acceptance gates.
PR8's later fused-dequant follow-up533dfc4 remains separate/draft against main;
these integration results do not validate its new kernel.
