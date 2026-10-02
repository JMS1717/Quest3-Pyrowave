# Assessment of issue #1: optimization suggestions

[Issue #1](https://github.com/JMS1717/Quest3-Pyrowave/issues/1) proposes fixed
regional workers, immediate UDP transmission, per-region freshness checks,
independent display updates and lower-frequency peripheral updates with TAA.
Assessment on October 2: useful pipeline research, but the proposed unsynchronized
presentation is not the next production change. No implementation or speedup is
claimed here; the issue remains open.

## What is worth retaining

Overlap stages where dependencies permit, bound queues, reject stale packets and
avoid spending more work on frames that cannot meet their presentation deadline.
Independent codec slices might eventually allow an early slice to travel and
decode while later slices encode. This can reduce a pipeline's critical path,
even when its throughput stays unchanged. Fixed CPU workers alone do not establish
parallel GPU execution or remove memory-bandwidth and submission bottlenecks.

Our UDP assembler already rejects old/duplicate fragments and bounds pending
frames. It reconstructs a complete codec bitstream before submission. These
transport fragments are not codec blocks: one block can exceed the UDP payload.
Removing that boundary restored a previous black/corrupt-video defect; preserve
the complete-frame path as the reference and fallback.

## What needs architectural changes

PyroWave packs wavelet coefficients by plane, decomposition level and band. A
coefficient block is not an independent rectangular RGB display region. Its
decoder uses one current sequence and reconstruction of the complete image;
the public partial-frame facility describes missing coefficients as zero, not
as retained pixels from the preceding frame. Region updates therefore require
a defined slice format, transform boundary/halo rules, chroma alignment, metadata
and decoder changes. CDF filters have spatial dependencies; even Haar requires
appropriate multilevel alignment. [Pinned codec API](https://github.com/Themaister/pyrowave/blob/d2997ac172bdc00e29c58e3f2938acb7e94580bf/pyrowave.h),
[encoder](https://github.com/Themaister/pyrowave/blob/d2997ac172bdc00e29c58e3f2938acb7e94580bf/pyrowave_encoder.cpp),
[decoder](https://github.com/Themaister/pyrowave/blob/d2997ac172bdc00e29c58e3f2938acb7e94580bf/pyrowave_decoder.cpp).

We infer that displaying tiles from different frame numbers would introduce
temporal seams, moving-object discontinuities and inconsistent stereo poses.
The app cannot use ordinary OpenXR submission to update physical scanout whenever
a network block arrives: it submits released swapchain images with a predicted
display time and layer poses. A safe experiment can pipeline slices internally
while presenting coherent stereo frames and maintaining GPU buffer ownership.
[OpenXR frame submission](https://registry.khronos.org/OpenXR/specs/1.0/man/html/xrEndFrame.html),
[swapchain ordering](https://registry.khronos.org/OpenXR/specs/1.0/man/html/xrReleaseSwapchainImage.html).

Lower-frequency peripheral updates are a separate temporal-quality tradeoff,
outside the current uniform-resolution/no-foveated-encoding baseline. Client TAA
does not establish fresh frames or recover unseen scene content. It would need
separate temporal/stereo/edge-quality evaluation; retained old regions must never
inflate our fresh-FPS counter.

## Priority and experiment gate

The latest six native120/4:2:0/1000-Mbps USB screens delivered roughly 97–112 fresh
submissions/s despite producer completion near 120/s. Hiding the overlay did not
yield a repeatable gain. These 15-second screens do not prove sustained acceptance.
[Recorded results](../results/OVERLAY-LIVE-2026-10-02.json).

The opt-in `.25` diagnostic subsequently measured two-eye GPU draw means around
0.6–0.7 ms. Six short screens still delivered 99–108 fresh FPS. CPU-render spikes
occurred with the timer both on and off, alongside lower clock samples; these
are not evidence of a timer optimization or total copy-completion cost.
[GPU calibration](../results/EYE-GPU-LIVE-2026-10-02.json).
Continue separating synchronization, handoff and runtime waiting. Prioritize the measured limiter,
including a shared-device Vulkan presentation path if interop/copy proves material.
See [presentation investigation](VULKAN-PRESENTATION.md).

After that, prototype a few independently encoded aligned slices, with one stereo
frame/pose ID and bounded frame deadlines. Retain safe assembly, explicit completion
and buffer leases. Measure complete-frame encode, first/last-byte arrival, slice
GPU completion, fresh stereo submissions, p1/pacing and latency against the same
full-frame baseline. Missing slices must trigger an explicit coherent fallback;
do not overwrite in-flight output or count an old/mixed frame as fresh.

At a nominal 1000 Mbps and 120 Hz the budget is about 1.042 MB/frame. Serializing
that payload at an ideal 2.5 Gbit/s takes 3.33 ms before overhead. This illustrates
a possible overlap opportunity, not measured USB/ADB throughput or a promised
3.33-ms saving. More slicing can add dispatch, synchronization and packet overhead;
promote it only after repeated matched measurements and image correctness checks.
