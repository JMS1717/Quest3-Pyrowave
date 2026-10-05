# Fused final Haar level and colour conversion

**Status: experiment, off by default.** Software Vulkan shows it produces the same bytes as the
shipped path. It has not been tested on a Quest, measured for speed, or accepted in live VR.

`debug.q3pw.fuse_color=1` replaces two passes with one fragment pass. Today PyroWave's final 4:2:0
luma iDWT writes an R8 plane, and `convert.frag` then reads that plane to make RGBA8. The fused pass
reads the level-0 luma wavelet and the chroma planes and writes the RGBA8 colour attachment
directly. The bitstream, wavelet, precision, chroma, foveation and the 4000 µs selection wait are
unchanged.

Commit `656a81b` measured median GPU decode+convert at 6.62 → 5.40 ms and fence at 7.94 → 6.89 ms
in one short screen, with unique targets about 117 → 118/s. That screen proves nothing about
sustained FPS, latency or images. It is the reason to finish the review, not an acceptance.

## What the review found in `656a81b`

| Problem | Effect | Fix on `review/fuse-color-exact` |
| --- | --- | --- |
| No FP16 rounding of intermediates. The shipped `inverse_haar_pairs()` rounds the vertical step and the result through FP16 (`haar_intermediate`); the candidate stayed in FP32 | Luma could differ by one step in some pixels. No exact-pixel test existed to show it | The shader rounds at the same points, in two variants: `OpFConvert` when PyroWave uses its `shaderFloat16` variant, `packHalf2x16` otherwise. pyroclient chooses the variant the way PyroWave does |
| `floor(x·255 + 0.5)` for the luma store | The add can round across a boundary that the hardware's round-to-nearest store does not cross | `roundEven(clamp(v)·255)` |
| `setenv("PYROWAVE_FUSE_COLOR")` before every decoder creation, never restored | Process-wide state. Any later decoder in the process inherits it, `setenv` is unsafe against concurrent `getenv`, and a failed fused pipeline still left PyroWave skipping luma | New `pyrowave_decoder_set_skip_final_luma_idwt()` per decoder. It is called only after the fused pipeline exists, and any failure leaves the two-pass path running |
| Fused-pipeline failure destroyed the client | Requesting the experiment on an unsupported path meant no video | Fall back and log `[Q3PW_FUSE_COLOR] … active=0 (<reason>)` |
| Mode interactions undefined | With `debug.q3pw.haar_fused=1` PyroWave still wrote luma, so the work was wasted and the reference was different. With `debug.q3pw.haar_pairs` set, the fused shader silently replaced the kernel being tested | `fuse_color_policy.h` and PyroWave both refuse fused multilevel Haar, dedicated pair kernels, precision other than 1, 4:4:4, CDF wavelets, the fragment iDWT and compute colour conversion |
| Combination with the prerecord prototype untested | Two unaccepted experiments at once cannot be attributed | `pyroclient_prerecord_enable` refuses while fused colour is active |

Format check: the fused shader reads the level-0 wavelet as a sampled `sampler2DArray`, and pyroclient
checks that its view is `R16_SFLOAT`. It writes only the colour attachment, so it has no storage
image whose declared format could mismatch, which was the defect in the earlier H2 port. The
Vulkan validation layer runs in CI.

## Evidence

- `fuse_color_exact` CI job (Mesa lavapipe, Khronos validation layer). It runs the **shipped**
  `idwt.comp` SPIR-V, taken from the patched `slangmosh.hpp` by
  `tools/pyroclient/extract_pyrowave_spirv.py`, then the shipped `convert.frag`, and compares every
  RGBA8 byte with the fused shader's output on the same inputs. Both FP16 variants are run on
  2080×2208, on 66×34 (padded to a 96×64 wavelet) and on 1922×1090 (odd chroma, partial tiles),
  with limited and full range and with bilinear and Catmull-Rom chroma. A CPU model of the Haar
  level checks that the reference actually wrote every pixel. The old `656a81b` shader runs
  alongside as a negative control and must differ, which shows the comparison can see this kind
  of defect.
- CPU tests: `fuse_color_policy_test.cpp` covers the eligibility rules. `tests/test_fuse_color.py`
  covers the extractor, the absence of `PYROWAVE_FUSE_COLOR`, that the skip is enabled only after
  the pipeline exists, the decoder-side refusals and the prerecord refusal.
- The Android client and Windows streamer still build in the normal CI jobs, now with the patch
  applied.

lavapipe is not an Adreno. The test proves the shader logic, coefficient orientation, edges,
formats and wiring. It does not prove that Adreno rounds `OpFConvert`, `packHalf2x16` and UNORM
stores the same way in a fragment shader as in compute. Only a device readback can show that.

## Before any live comparison (needs hardware authorization)

1. With the matching CI APK and libraries installed, run `pyroclient_test` against the saved
   small and native stereo fixtures twice, with `debug.q3pw.fuse_color` unset and then `1`. Use GPU
   readback, Haar/Compute, precision 1, 4:2:0, limited range. Both runs must match the saved
   reference RGBA exactly. Confirm from logcat in the same process and time window that
   `[Q3PW_FUSE_COLOR] … active=1 (eligible)` and the expected `fp16_variant` appeared.
2. Repeat with `debug.q3pw.chroma_filter=catmull` and with full range.
3. Only then run short interleaved screens against the `.48` / `.50` controls in
   [WHOLE-STACK-SCORECARD.md](WHOLE-STACK-SCORECARD.md). Report stage GPU time, fence, unique and
   lost targets, and compositor stale counts separately. A faster decode is not a delivered-FPS gain
   until sustained runs show one.

Restore with `adb shell setprop debug.q3pw.fuse_color 0` and restart the client. Nothing else
changes.
