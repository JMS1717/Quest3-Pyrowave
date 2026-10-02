# Full-resolution frame-rate investigation

At 120 Hz the frame period is 8.33 ms. Short .8 native USB captures at the
padded panel resolution (2080 x 2208 per eye), 4:2:0 and CDF 9/7 measured about
10.6–10.9 ms of GPU wavelet decode and 15.0–15.5 ms from recording through
completed decode/conversion. One TCP worker waits for the Vulkan fence before
taking another frame. That serial path cannot complete 120 frames per second
with those timings. The runtime accepts 120 Hz, but the delivered frame rate
clusters around 60 as missed display periods accumulate.

This is a measured decoder bottleneck, not evidence of a universal 60 FPS cap.
A previous 60% resolution UDP capture reached a ~120 FPS median with poorer
tail pacing. Increasing USB target bitrate from 1000 to 2500 Mbps did not
increase the full-resolution median FPS. At 3000 Mbps the stream stalled.
The reported GPU timing includes GPU scheduling/preemption; completion timing
also includes recording, conversion, submission and fence wait. Their difference
must not be presented as pure CPU overhead.

## Experimental independent TCP workers (.9)

The client can create two independent Vulkan decoder contexts, each with its
own command/fence resources, intermediate planes and three output buffers.
This overlaps CPU recording and fence waits. It cannot create extra GPU capacity
and may increase contention and memory consumption. The default remains one.
This applies to complete-frame TCP, including ALVR USB forwarding; UDP is unchanged.

To select two workers for a controlled trial:

```powershell
adb shell setprop debug.q3pw.decode_workers 2
adb shell am force-stop io.github.jms1717.quest3pyrowave
adb shell am start -n io.github.jms1717.quest3pyrowave/android.app.NativeActivity
```

Restore one worker using `adb shell setprop debug.q3pw.decode_workers 1` and
restart the client in the same way. The property is read when the TCP decoder
starts. The client logs `[Q3PW_WORKERS] TCP independent decoders=...`.
Only the exact value `2` enables two workers; other values choose one.

Both workers consume a shared latest-frame slot. Completed frames publish in
ingress order: an older worker cannot replace a newer frame, including after
the render loop has taken it. Tracking timestamps are not used as sequence
numbers because different game frames may share a tracking timestamp.
Incomplete/rejected frames never publish. The pending and currently leased
hardware buffers remain excluded from native writes. Frame-order and worker
selection regression checks run in the client-core CI suite.

The first matching .9 live trial **regressed**: one worker delivered ~60 median
FPS with 15.20 ms completion; two delivered ~30 with 28.37 ms completion and
126.34 ms median ALVR estimated total latency. One worker was restored immediately.
This experiment remains off by default and is not a recommended performance preset.

Compare one and two workers at the same resolution, codec/path, scene, bitrate
and thermal state. Measure delivered FPS, completion/decode distributions,
skipped/superseded frames, image correctness, latency and thermals. A cloud build
or successful decode does not establish sustained 120 FPS or a latency advantage
over Virtual Desktop. Fragment decoding also needs reference-quality validation.

## Shorter transform trial (.10)

The pinned research codec also implements lifting Haar with matching encoder,
decoder and band-gain handling. `.10` exposes it in the dashboard and control tool
as **Haar (experimental)**. Config byte 13 carries `2`; both sides must use the
matching build. Haar and CDF 5/3 require Compute, even if a research property asks
for Fragment. The default remains CDF 9/7.

Haar reduces inverse-transform arithmetic, but the existing multilevel passes and
RGBA/copy work still cost time. Faster native-resolution decoding is a hypothesis
to measure. Haar changes the rate/distortion tradeoff and can show block-like
detail or poorer dense-content reconstruction. Higher bitrate can compensate for
some errors but does not guarantee equivalence to CDF 9/7. Neither the supplied
desktop/lab research nor a successful build validates Quest frame rate or quality.

At native resolution a short .8 app-specific GPU trace showed about 8.7 ms of
compute dispatch plus ~1.2 ms of preemption, and ~2 ms for the 4160 x 2208 RGBA
surface. Separate eye-copy and presentation surfaces also execute. Detailed
profiling adds overhead, so these are diagnostic timings, not benchmark cells.
The ADB CPU/GPU level-6 trial retained a 640 MHz GPU and ~60 median client FPS;
properties and detailed profiling were restored afterward. A separate level-7 GPU request
reported 690 MHz and 14.51 ms completion, but retained ~60 median FPS; it was also
restored. This is an observed request on this OS, not a guarantee on other firmware.
Meta documents these
[performance levels](https://developers.meta.com/vr/documentation/native/android/os-cpu-gpu-levels/)
and the [GPU profiler](https://developers.meta.com/vr/documentation/spatial-sdk/ts-ovrgpuprofiler/).


## Pair-local Haar inverse (.11)

Haar coefficient pairs are independent. The experimental compute path now reads
only the four bands for each 2 x 2 output block, rather than loading neighboring
aprons and transposing a shared tile. The branch retains the transposed dispatch
mapping and vertical-then-horizontal inverse order, including intermediate FP16
rounding for reduced-range storage. Bounds checks cover padded dispatch edges.
CDF 9/7 and CDF 5/3 keep their existing kernels. Multilevel dispatches, final RGBA
conversion, completion fences and buffer leases remain; this optimization does
not by itself guarantee the 8.33 ms complete-frame budget.

The embedded header must be regenerated with the pinned Granite `slangmosh`
compiler after GLSL changes. `shaders/quest3-manifest.json` hashes the changed
kernel, shared definitions, variant description and generated header; Android
and Windows interop build helpers reject stale inputs. The manually triggered
`Regenerate pinned PyroWave shaders` workflow produces reviewable header artifacts.


Before installing .11, the same cloud-built plain-R8 readback harness was run
against the .10 and .11 codec libraries on Quest 3. Deterministic asymmetric
512 x 320 and 4160 x 2208 Haar/4:2:0 frames produced at most one code-value
difference. Changed samples were 1.34% and 1.17%, with mean absolute differences
0.01342 and 0.01172. Source PSNR over all YUV samples changed by +0.00610 dB and
+0.00005 dB, respectively. These limited checks found no band/orientation error;
they do not establish quality parity with CDF 9/7 or hardware codecs. The new
kernel is not bit-identical. The first strict MAE <0.01 trial failed; acceptance
was revised after recording both complete outputs and their source error to
require max error <=1 and source PSNR loss <=0.05 dB. No APK was installed before
that review. The three-iteration readback timing includes startup/clock variation
and is not used as a streaming performance result.

Create the same input with `python -m tools.quest3.reference_pattern pattern.y4m`
(or add `--width 4160 --height 2208`), then encode with the pinned research
`pyrowave-encode` using `PYROWAVE_WAVELET=haar` and byte caps 131072 or 2083333.
For on-device readback use the cloud artifact `pyrowave_android` with
`PYROWAVE_WAVELET=haar PYROWAVE_FORCE_COMPUTE=1 PYROWAVE_AHB=0`, and keep old/new
codec libraries in separate directories selected by `LD_LIBRARY_PATH`.


## Reuse released eye images (.12 candidate)

On Quest 3 PyroWave repeats, .12 skips eye swapchain acquisition/copy/redraw after
one image has been rendered and released. It resubmits that last image with its
original poses while the runtime reprojects it; the client overlay remains a
separate layer. A presentation configuration update invalidates the cached image
and causes one redraw. Fresh frames retain copy completion and buffer leases.
Other clients/codecs keep their existing path. The
[OpenXR specification](https://registry.khronos.org/OpenXR/specs/1.0-khr/html/xrspec.html)
allows xrEndFrame without another release and uses the most recently released
image. This saves repeat rendering; it does not count repeats as new video frames.

For a same-build A/B control, `adb shell setprop debug.q3pw.repeat_render 1`
disables reuse. Restart the client after changing the property. Empty/default
means reuse; restore the original property after the experiment. Cloud compilation
and live correctness/performance acceptance remain required before claiming a gain.


## Optional fused Haar (.13 research candidate)

A separate kernel reconstructs each coarsest coefficient's descendants in two
32 x 32 shared float arrays, with the same intermediate storage rounding as .11.
Luma uses five levels and 32 x 32 output tiles. 4:2:0 chroma uses four levels and
16 x 16 output tiles, ending at level 1. Explicit texture bindings avoid descriptor
indexing requirements. Dispatch covers aligned coarsest coefficients, with output
bounds checks against visible plane size. It remains off by default.

Use `PYROWAVE_FUSED_HAAR=1` for plain readback harness tests. The matching .13
client accepts `adb shell setprop debug.q3pw.haar_fused 1`; restart the client
before a trial. Set 0 or restore the original value to use the .11 pair-local path.
The option only applies to Haar compute; the server's encoded transform stays
Haar. It does not change the bitstream. Verify readbacks against .11 before live
use; fewer dispatches are not proof of faster execution.

Matching .13 timing records add CPU command-recording wall time, conversion GPU
timestamps, and vkQueueSubmit + vkWaitForFences wall time. The existing completion
metric starts before recording commands, so it is not simply submit-to-fence.
Wait wall time includes queue scheduling and GPU work; subtracting GPU decode
from completion does not isolate CPU cost. Use matching .13 native/Rust ABI and
APK/server binaries: the control telemetry and frame-info layout changed together.

### .13 measured acceptance

The native asymmetric 4:2:0 fused readback passed the existing max-error <=1 /
source-PSNR-loss <=0.05 dB gate. Live performance failed: the fixed-chart 45-second
trial fell to 71.81 fresh FPS, 8.65 ms GPU decode and 13.24 ms completion. Keep
fused Haar off. Pair-local controls produced 99.66–100.74 fresh FPS and 9.14–9.20 ms
completion. Reuse did not show a clear FPS gain; keep the optout for comparison.
One initial reuse trial overlapped the next client restart and was excluded,
then repeated in a clean window. These sequential short observations do not
validate sustained 120 FPS or thermal endurance. See the sanitized
[timing/readback results](../results/DECODE-2026-10-01.json) and
[alpha.6 setup](RELEASE-alpha.6.md).

## Optional batched dequant (.14 candidate)

The first matched live screen is now available in
[BATCH-DEQUANT-LIVE-2026-10-01.json](../results/BATCH-DEQUANT-LIVE-2026-10-01.json).
Three sequential **15-second** off/on/off windows delivered **98.75 / 97.73 /
99.47 fresh FPS**, with median completion **9.43 / 9.60 / 9.32 ms**. GPU clock was
690 MHz throughout; battery temperature was 43–44°C. This did not reproduce the
native-only improvement, so batching remains off. These quick screens do not
establish a thermal endurance result or sustained fresh 120 FPS.

The dequantizer previously dispatched each subband separately. A new opt-in path
uses dispatch Z for adjacent bands of one component/level, after checking equal
block count/stride and contiguous block offsets. Each workgroup retains its own
shared storage and writes a different image layer. 4:2:0 dispatch count falls
from 42 to 13; 4:4:4 falls from 48 to 15. Payload storage modes and the final
write-to-sample barrier stay intact. The push-constant layout gains block count,
so the generated shader header must match the C++ caller.

It is off by default. Use `PYROWAVE_BATCH_DEQUANT=1` with the plain readback
harness or `debug.q3pw.dequant_batch=1` with a matching .14 client, then restart.
The property overrides the environment when present. Verify asymmetric small
and native-size readbacks before APK deployment, then compare matched streaming
windows with fused Haar off. This is a candidate, not a measured speedup.

The matching .14 native bridge also exposes `debug.q3pw.convert_compute=1` for
a compute-color-conversion control, independently of the wavelet decode path.
Set 0 or restore the original property to retain capability-gated Adreno fragment
conversion. Restart the client after changes. No default conversion change is implied.

.14 Android and Windows CI passed, and its small/native Haar/4:2:0 plain readbacks were
byte-identical to .13 with batching both off and on. Matching Windows build and
live performance screening are separate gates; this does not prove a live speedup.
The initial tracking-recovery prompt blocked launch. It was subsequently recovered;
the live off/on/off comparison above found no gain and batching remains off.
Isolated native screens (80 frames / 20 warmup) showed completion p50 9.10 ms
with batching versus 9.50–9.65 ms in off controls at a reported 690 MHz. These
are short single-frame standalone checks, not sustained streaming results;
[reviewed batch results](../results/BATCH-DEQUANT-2026-10-01.json) record the gates.

A separate .13 FP16-math check gave max one YUV code-value difference, but the
small asymmetric source PSNR fell by 0.05934 dB, slightly exceeding the existing
0.05 dB loss gate. Native-size source PSNR rose by 0.00026 dB. Precision stays at
the default 1; no FP16 quality/performance promotion is made. Native color-bridge
readbacks differed by at most one RGBA code value between compute and fragment.
Their first timing sequence was confounded by wired client auto-launch and was
excluded; subsequent isolated short timings still varied. Default fragment
conversion remains, with the .14 control available for matched live trials.

The separate 180-second .13 SteamVR Home observation delivered 94.46 fresh FPS,
4.73 ms GPU decode, 2.52 ms conversion and 9.88 ms completion, with ALVR estimated
latency 70.82 ms. It confirms the remaining budget problem beyond the short chart
tests but is not a thermal-endurance certification. See
[the sanitized Home result](../results/DECODE-HOME-2026-10-01.json).

## Direct eye copy (.15)

Matching `.15` Android/Windows builds and regression checks passed
[CI](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/36942639620).
An optional GLES copy samples the decoded AHardwareBuffer directly into both
OpenXR eye textures, avoiding the intermediate staging and WGPU eye pass.
PyroWave decoding remains Vulkan compute. Source leases and the synchronous
completion barrier remain intact. Geometry, SDR color, no foveation/upscaling,
identity warp and no passthrough are required; unsupported configurations fall back.

At 2080 × 2208 per eye, runtime-confirmed 120 Hz, 4:2:0, no foveated encoding,
Haar compute, one worker and native USB/TCP at 1000 Mbps, the same static quality
scene gave these sequential 15-second screens after 3 seconds settling:

| Path | Fresh submissions FPS | GPU decode p50 ms | Completion p50 ms | Eye render CPU p50 ms | GPU MHz |
| --- | ---: | ---: | ---: | ---: | ---: |
| Staging control 1 | 98.67 | 4.65 | 9.40 | 9.22 | 690 |
| Direct 1 | 103.71 | 4.86 | 8.72 | 8.68 | 640 |
| Staging control 2 | 99.33 | 4.62 | 9.32 | 9.11 | 690 |
| Direct 2 | 103.63 | 4.81 | 8.74 | 8.69 | 640 |
| Direct + OpenXR BOOST, single screen | 108.19 | 4.58 | 8.29 | 8.32 | 690 |

Actual direct/staging counters confirmed the paths. Battery temperature was 45 °C
and reported thermal status 0. Acquire/release CPU times were only a few microseconds;
eye rendering, including its GPU completion wait, consumed most of the frame budget.
Dynamic clocks differed between paths, and BOOST has only one matched-scene screen.
These observations identify the next bottleneck; they do not establish fixed-clock
causality, endurance, or a default promotion. GPU work completion still needs careful
buffer synchronization in any future asynchronous copy experiment.

Private captures showed correct left/right labels, upright orientation and matching
dominant flat colors. Full reference equivalence and human in-headset acceptance
remain unverified. p1 nominal FPS remained about 60 and p95 timestamp gaps about
16.7 ms. Fresh submission rate and ALVR estimated pipeline latency are not optical
display or motion-to-photon measurements. **Sustained fresh 120 FPS remains unmet.**
See the [sanitized full distributions](../results/DIRECT-EYE-LIVE-2026-10-01.json).
Direct copy stays optional/off by default, and 4:2:0 remains the default.
The owner paused hardware work for Virtual Desktop; resume only with explicit permission.
