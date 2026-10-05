# Producer-worker integration review

PR #3 consolidates the measured producer experiments, native lifetime repair,
publication race tests and frame-wait diagnostics. It does not promote a faster
decoder or establish sustained native 120 FPS. The default remains synchronous
decode with a 4000 microsecond selection wait; publication events and prerecord
remain opt-in. The measured 6000 microsecond wait remains rejected as a default.

## Changes made before integration

- Removed `effective_video` session dumps from the publication and payload
  results. Curated experiment parameters, source/build identities and every
  measurement remain. A regression check scans all public result JSON files for
  full session objects and private identifiers; this is not an exhaustive secret
  scanner. Previously published Git history is not rewritten.
- Excluded the new `debug.q3pw.haar_h2` implementation from `f3a7184`. Its
  `haar_fused.comp` shader declares binding 2 as `r16f`, but its second stage
  binds the existing `VK_FORMAT_R8_UNORM` decode plane. The shader's declared
  storage format must match the bound view's format, as described in the
  [Khronos storage-image guide](https://docs.vulkan.org/guide/latest/storage_image_and_texel_buffers.html).
  Compilation alone cannot establish correct execution. The candidate also
  changes a process-wide decoder environment variable and has no matching exact-pixel
  proof in this PR. Its commit remains available for a separate, corrected
  experiment; this review does not erase prior work.
- Kept the later `debug.q3pw.fuse_color` candidate from `656a81b` on
  [`experiment/fuse-color-review`](https://github.com/JMS1717/Quest3-Pyrowave/tree/experiment/fuse-color-review).
  It arrived during review and has no matching exact-pixel test. Its constructor
  sets `PYROWAVE_FUSE_COLOR` process-wide without restoring the previous value,
  and interactions with existing fused-Haar and prerecord modes need explicit
  coverage. Those are separate review requirements, not a measured performance
  verdict. The integration's runtime sources remain identical to `3de60f1`,
  whose four CI jobs passed in run `37324391808`; the final PR head is checked
  again before merging. The later historical Haar-regression note is retained.

## Acceptance and next work

Require the Python/portable checks, production publication tests, Android client
build and Windows streamer build to pass on the reviewed PR head before merging.
The GPU prerecord readback evidence applies to its documented historical sources;
CI compilation of a new APK does not replace device acceptance. No APK install,
driver registration, headset/SteamVR test or release follows automatically from
this source integration. The owner's Codex hardware pause remains in effect.

The useful next optimization is shortening the measured native GPU decode cost,
with shader format, edge coverage and full image/pose correctness checked before
any live comparison. Keep the whole-stack scorecard as the comparison baseline.
