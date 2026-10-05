# Path to 207 Hz: off-headset changes and the hardware plan

Prepared October 5, 2026. Nothing on this page has run on the headset. AGENTS.md pauses
hardware testing, so every step under "Hardware session" needs the owner's go-ahead first.

## Budget

At 120 Hz with native 2080x2208 per eye, 4:2:0 and 1000 Mbps, the Quest app spends about
7.3 ms of GPU per fresh frame (decode 5.9, RGBA conversion 0.8, eye copy 0.6; measured
stages in [DECODE-PIPELINE.md](DECODE-PIPELINE.md)). That caps throughput near 137 fps. At
207 Hz the frame period is 4.83 ms, which leaves about 3.8 ms for the app once the Meta
compositor runs (est.). Reaching 207 Hz therefore needs about a 1.9x cut in GPU time per
frame. Scheduling changes cannot provide that; it has to come from fewer pixels and fewer
full-frame passes.

## What this branch adds

| Change | Expected effect | Checked off-device | Status |
|---|---|---|---|
| Balanced (68% center, 1.75x) and Strong (60% center, 2x) peripheral profiles, `video.pyrowave.foveation_profile` | Encoded pixels per eye drop by 23.8% (1824x1920) and 35.1% (1664x1792), versus 11.6% for Light. If decode scales the way Light's did, Strong is about 3.8 ms (est.) | Rust server (C++ sizing), Rust client and the Python model agree for every eye width from 512 to 4096. Light geometry is unchanged | CONTINUE: candidates, not accepted |
| Fused final color: `pyrowave_decoder_set_final_luma_store` plus `fuse_color.frag`, opt-in with `debug.q3pw.fuse_color=1` | The old experiment branch measured decode+convert going from 6.62 to 5.40 ms and lost frames from 3.8 to 1.7/s | On lavapipe at 512x320, RGBA is byte-identical to the current path for all four range/filter variants. Unsupported modes fall back to the separate conversion | CONTINUE: Quest exact-pixel and PSNR proof pending |
| Fused dequant + level-0 Haar: `pyrowave_decoder_set_fused_dequant_haar` plus `wavelet_dequant_haar0.comp`, opt-in with `debug.q3pw.dequant_haar=1` | Decodes the three level-0 luma bands inside the final Haar pass, so their R16F store and reload disappear: about 6.9M coefficient stores and 6.9M loads per 4160x2208 frame (est.), plus one dispatch. Holding three bands in registers may lower occupancy, so the net effect is unknown | On lavapipe, every level-0 coefficient matches the separate dequant within one FP16 step, which rules out indexing and band-order errors. 220 of 163,840 luma pixels (0.13%) differ by one 8-bit step; the exact rounding cause is unconfirmed | EXPERIMENT: Quest timing and exact-pixel run pending |
| Optical latency stamp, `video.pyrowave.latency_stamp` ([OPTICAL-LATENCY.md](OPTICAL-LATENCY.md)) | First real composition-to-photon number | The layout round-trips through a rasterizer for eye sizes 512 to 4096 | New diagnostic |
| No-decode cadence probe, `debug.q3pw.cadence_probe=207` | Shows whether the runtime itself sustains 207 Hz with the lobby only | Counter and summary logic unit-tested | New diagnostic |

Light stays the default profile, and fused color and fused dequant stay off by default. No
default has changed. Fused color and fused dequant replace the same final pass, so only one
can be on; the client applies fused color when both properties are set.

## Hardware session (needs the owner's go-ahead)

Run these in order. Each step can reject a candidate on its own. Short screens cannot prove
sustained FPS, thermal stability or optical latency.

1. **Fused color and fused dequant exact-pixel proof on Adreno.** Push the artifact's `fuse_color_gate`,
   `libpyrowave-shared.so` and `libc++_shared.so` to `/data/local/tmp/q3pw/`, then run
   `LD_LIBRARY_PATH=. ./fuse_color_gate`. This covers 512x320 and 4160x2208 with a 30 dB
   luma PSNR floor. It must print `FUSE_COLOR_GATE_PASS` before any streaming A/B. If only
   the PSNR floor fails while `rgba_max_diff` stays at 0 or 1, the fused path still matches
   the current one; rerun with `GATE_DUMP_DIR=.` and inspect the source and decoded PGM
   files before blaming either path. Lavapipe cannot answer this question: its PyroWave
   decode is unfaithful (8 to 13 dB) and crashes on some inputs. The same run prints a
   `dequant_haar` line for the fused dequant kernel. It passes at a difference of at most one
   8-bit step; record the differing count, because zero would make it byte-identical.
2. **No-decode 207 Hz cadence.** With the PC streamer stopped, run
   `adb shell setprop debug.q3pw.cadence_probe 207` and relaunch the APK. Read the
   `[Q3PW_CADENCE]` and `[Q3PW_CADENCE_SUMMARY]` lines, then clear the property. If the
   lobby alone skips slots at 207, decoder work cannot be the only limit.
3. **120 Hz A/B at native resolution** (interleaved, same session): Light versus Strong,
   then fused color off versus on, then `debug.q3pw.dequant_haar` off versus on with fused
   color off. Record decode stages, displayed FPS, lost frames and a peripheral-text look for
   each profile. Dequant time should fall and iDWT time rise for fused dequant; only their sum
   matters.
4. **207 Hz screen** with Strong and the winning fused path on. Report runtime acceptance,
   decode budget and live delivery as three separate results.
5. **Optical latency** at the best stable rate, following [OPTICAL-LATENCY.md](OPTICAL-LATENCY.md).

Step 4 uses whichever of fused color and fused dequant won its step 3 A/B. If 207 Hz still
misses, the next levers are Vulkan-native OpenXR presentation
([VULKAN-PRESENTATION.md](VULKAN-PRESENTATION.md)), which removes the RGBA bridge and GLES
copy, and packing four coefficients per texel in the remaining dequant levels. Both are
larger changes and are not started on this branch.
