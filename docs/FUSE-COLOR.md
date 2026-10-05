# Fused final Haar level and colour conversion

**Status: correct on Quest, no live gain, off by default.** On a Quest 3 the `.54` fused pass
produces exactly the same bytes as the shipped two-pass path. In two live A/B screens it did not
deliver more frames. Keep `debug.q3pw.fuse_color` unset.

`debug.q3pw.fuse_color=1` replaces two passes with one fragment pass. Today PyroWave's final 4:2:0
luma iDWT writes an R8 plane, and `convert.frag` then reads that plane to make RGBA8. The fused pass
reads the level-0 luma wavelet and the chroma planes and writes the RGBA8 attachment directly. The
bitstream, wavelet, precision, chroma, foveation and the 4000 µs selection wait are unchanged.

## Results, October 5

| Check | `.53` (`656a81b`, unreviewed) | `.54` (`7460906`, this review) |
| --- | --- | --- |
| Quest exact pixels, native stereo 4160×2208 | not exact: 6010 pixels differ, by up to 2 | **0 of 9,185,280 pixels differ**, bilinear and Catmull-Rom chroma |
| Quest exact pixels, small stereo 512×320 | not exact: 278 pixels differ | **0 of 163,840 pixels differ** |
| Standalone fence p50, native, fused colour off → on | 12.22 → 11.18 ms | 12.24 → 11.32 ms |
| Live unique targets/s, fused colour off vs on | one 12 s fused block: 117.0 vs 118.3 | 10 fused blocks: 118.2 vs 117.3, and 118.2 vs 117.8 |
| Live fence p50, off vs on | one block: 7.94 vs 6.89 ms | 7.99 vs 7.92 ms, and 7.95 vs 7.93 ms |

The `.53` figures in the first two rows come from the diagnostic build of its arithmetic (luma mode
1), run against the same libraries. Sanitized evidence: [GPU and standalone timing](../results/FUSE-COLOR-GPU-2026-10-05.json),
[live A/B](../results/FUSE-COLOR-LIVE-2026-10-05.json).

**Live:** two client-restart screens on the installed `.54` pair. Settings were stationary chart,
native 2080×2208 per eye, runtime 120 Hz, 1000 Mbps, 4:2:0, no foveation, LOW priority and TCP.
Patterns were ABBAABBA and BAABBAABBAAB, 20 s blocks after 5 s settling. The fused-colour decision
appeared in logcat in every process. Paired fused − off differences in unique targets were
−0.94 ± 0.72/s (4 pairs) and −0.32 ± 0.41/s (6 pairs). Decode p50 fell by about 0.7 ms, but the fused
pass grew by the same amount (0.77 → 1.4 ms), so the fence did not move. Eye-render p90 rose by
about 0.22–0.25 ms and compositor time by about 0.02 ms in **every** pair.

The standalone run gains about 0.9 ms of fence; the live run gains nothing. Neither number is
optical latency. One plausible explanation, not proven: live, the full-resolution fused fragment
pass shares the GPU with the compositor's eye copy, while the compute iDWT it replaces overlapped
it better. The single fused `.53` block that looked like a 1 ms fence win did not replicate over
ten `.54` blocks. Standalone, the two builds differ by only 0.14 ms of fence.

## What the review fixed in `656a81b`

| Problem | Effect | Fix |
| --- | --- | --- |
| Arithmetic stayed in FP32 | 6010 native-frame pixels differed on Quest | Every lifting output is rounded to FP16 before the next step uses it. That is how Adreno evaluates the shipped `inverse_haar_pairs()`; the Quest readbacks showed it. Two variants, `OpFConvert` and `packHalf2x16`, chosen as PyroWave chooses (`shaderFloat16`) |
| Luma computed as `k / 255` in the shader | The compiler reassociated the range conversion and moved single channels by one step | Luma is read back as an R8 texel from a fixed 256×1 identity table, as `convert.frag` reads its plane |
| `setenv("PYROWAVE_FUSE_COLOR")` before every decoder creation, never restored | Process-wide state; later decoders inherited it; a failed fused pipeline still skipped luma | New per-decoder `pyrowave_decoder_set_skip_final_luma_idwt()`, called only after the fused pipeline exists. Any refusal keeps the two-pass path |
| Failure destroyed the client | No video when requested on an unsupported path | Fall back and log `[Q3PW_FUSE_COLOR] … active=0 (<reason>)` |
| Mode interactions undefined | With `haar_fused` the work was wasted; with `haar_pairs` the kernel under test was silently replaced | `fuse_color_policy.h` and PyroWave both refuse fused multilevel Haar, dedicated pair kernels, precision ≠ 1, 4:4:4, CDF wavelets, the fragment iDWT and compute colour conversion. The prerecord prototype refuses fused colour |

How the two Quest causes were found: diagnostic builds of `libpyroclient.so` that differed only in
the fused shader, each compared on the Quest with the two-pass bytes. The first table row went
502 → 6010 (no rounding) → 166 (step-wise rounding) → 96 (optimization barrier on luma) → 0
(table-fetched luma). Other forms changed nothing or made it worse: `unpackUnorm4x8`, FP16-rounded
luma, chroma coordinate forms and `precise` colour maths. Every row is in the GPU results file.

## Software checks (CI, no headset)

`fuse_color_exact` runs on Mesa lavapipe with the Khronos validation layer:

- It executes the shipped `idwt.comp` SPIR-V, extracted from `slangmosh.hpp` by
  `tools/pyroclient/extract_pyrowave_spirv.py`, then the shipped `convert.frag`.
- It compares every RGBA8 byte with the fused shader's output on the same inputs. Cases are
  2080×2208, a padded 66×34 and 1922×1090 (odd chroma, partial tiles), with both ranges, both
  chroma filters and both FP16 variants.
- Three CPU luma models report which arithmetic the driver followed. Mesa folds the FP16 round
  trips (the "folded" model matches exactly); Adreno performs them step by step.
- The 656a81b shader runs alongside as a negative control and must differ. On lavapipe it differs
  only through its store rounding.

lavapipe proves the logic, orientation, edges, formats and wiring, not Adreno arithmetic; the Quest
readbacks above cover that. CPU tests cover eligibility, the lifetime and order guarantees, and the
SPIR-V extractor.

## Reproducing on the Quest

Private scripts in `workspace/state`:

- `fuse-color-exact-device.py`: standalone `pyroclient_test`, GPU readback, saved Haar fixtures,
  off/on/off/on with both chroma filters, properties restored with readback.
- `fuse-color-timing-device.py`: standalone ABAB stage times.
- `codex-overnight-20261004/live32.py`: live A/B with snapshot, independent restorer and rollback.

To restore after a manual test, run `adb shell setprop debug.q3pw.fuse_color ""` and restart the
client.

## If this is revisited

The exact pass is a correct baseline for any cheaper fused shader. A new variant must keep 0
differing bytes on both fixtures and both chroma filters, and must show a live fence and
unique-target gain over at least two reversed-order screens. Live, watch eye-render p90 and
compositor stale counts as well as GPU decode. Directions not tried: fewer per-pixel wavelet
fetches (one fetch per 2×2 quad) and doing the fused work in compute instead of a fragment pass.
