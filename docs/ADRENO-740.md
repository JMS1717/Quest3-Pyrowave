# Adreno 740 low-level levers

October 5 review of the Quest 3 GPU (Snapdragon XR2 Gen 2, Adreno 740) against the current
decode path. Nothing here ran on hardware: every item below is built or proposed, and
each needs an owner-approved headset screen before any keep/drop decision.

## What the chip changes about the problem

- **The decoder is fed by the texture/load-store units, not ALU.** Vector dequant (`.43`),
  batched dequant (`.14`) and FP16 math were all neutral; payload 600–1000 Mbps barely moved GPU
  time; light foveation's 11.6% fewer pixels cut decode 13%. Cost tracks coefficient count.
  Every coefficient is one single-channel R16F `imageStore` in dequant and one R16F fetch in the
  inverse Haar: about 13.8 M of each per native 4:2:0 frame. Qualcomm's guidance is to group
  accesses to 128 bits, use 4-component vector loads/stores, and read single-channel planes as
  RGBA to fetch four pixels per instruction ([OpenCL optimization notes](https://www.qualcomm.com/news/onq/2016/06/better-opencl-performance-qualcomm-adreno-gpu-memory-optimization)).
- **Storage images and compute writes disable UBWC bandwidth compression** on Adreno
  ([Adreno best practices](https://docs.qualcomm.com/bundle/publicresource/80-78185-2/topics/mobile_best_practices.md)).
  Every decoder intermediate is a storage image, so none of it is compressed.
- **Decode shares the graphics pipe with the eye copy and Meta's compositor.** It runs at LOW
  global priority on queue family 0 and is preempted: an early trace showed ~1.2 ms of
  preemption per frame and ~700 preemptions/s at 87% GPU busy. A740 adds **LPAC** (Low Priority
  Async Compute), a second command processor for a compute-only LOW queue that runs
  concurrently with the graphics pipe instead of being preempted by it (same Qualcomm page).
- **Memory traffic will become a hard wall at 207 Hz** (estimate). Uncompressed, one native frame
  moves roughly 210 MB: coefficients written and read (~55 MB), LL chain (~7 MB), R8 planes
  (~28 MB), RGBA bridge (~73 MB), eye images (~37 MB). That is ~25 GB/s at 120 Hz and ~43 GB/s at
  207 Hz before the compositor. Removing full-frame intermediates is required, not optional.

## Built on this branch (all off by default)

| Flag | Change | Why it should help | Gate before live use |
|---|---|---|---|
| `debug.q3pw.lpac=1` | Decode and RGBA conversion submit on a compute-only queue at LOW priority (LPAC). Conversion becomes the compute pass; fragment reconstruction is forced to compute. Falls back with a logged reason if the driver has no such family, no timestamps, or rejects the queue. | Removes graphics-pipe preemption of decode and lets decode fill bubbles left by the compositor, eye copy and iDWT barriers. Every steady-state miss today is a decode still running. | `[Q3PW_LPAC] applied=1`; exact readback equal to the `debug.q3pw.convert_compute=1` reference (compute and fragment conversion differ by ≤1 code value) |
| `debug.q3pw.eye_invalidate=1` | `glInvalidateFramebuffer` on each eye image before its full-screen copy. | Tells the tiler not to load the previous eye contents into GMEM. The quad covers every pixel, so output is unchanged. Saves nothing if the driver already renders this pass direct-to-memory. | `[Q3PW_EYE_INVALIDATE] active=true`; eye-copy GPU time from `debug.q3pw.eye_gpu_probe` |
| always on | `[Q3PW_GPU_CAPS]` log lines at decoder creation: queue families (flags, count, timestamp bits, LOW support), subgroup size range, shared memory, driver, QCOM/compression extensions. | Settles which levers this driver exposes, in the first session, with no GPU work. | — |

`python -m tools.quest3.block_occupancy frame.wave` (CPU only) reports, per band, how many
32×32 blocks and 8×8 sub-blocks the bitstream leaves empty. Run it on a server bitstream dump
of real game content to size zero-skipping before anyone builds it.

## First hardware session (needs the owner's go-ahead)

1. Start `pyroclient_test` once and save logcat: the `[Q3PW_GPU_CAPS]` family list says whether
   LPAC exists on this firmware. If no compute-only family is listed, LPAC is DROP for this driver.
2. Exact readbacks, small and native, with `debug.q3pw.lpac=1` against the compute-convert reference.
3. Standalone `pyroclient_test in.wave - 200` with LPAC 0/1: the raw cost of LPAC with an idle graphics pipe.
4. Live 12 s screens at native 120 Hz, 1000 Mbps, 4:2:0, Haar: default / `convert_compute=1` /
   `lpac=1` / default. The middle control separates the conversion change from the queue change.
   Track unique targets/s, lost/s, GPU decode p50/p95, fence p50, eye-copy GPU, compositor stale
   counts. If LPAC wins at 120, repeat at 144 Hz, where LOW priority on the graphics queue lost
   ~10 fps, then at 207 Hz.
5. `eye_invalidate` 0/1/0 in the same session with `eye_gpu_probe=1`.

## Experiment record

| Change | Reason | Before | After | Headset result | Status |
|---|---|---|---|---|---|
| LPAC decode queue | Preemption and serialization on the graphics pipe | 117.7 unique/s, 5.9 ms decode, 8.0 ms fence | — | not run | CONTINUE (needs caps log) |
| Eye-image invalidate | Avoid GMEM load on tiled eye copy | ~0.62 ms eye-copy GPU | — | not run | CONTINUE |
| GPU caps log | Facts for the levers below | unknown families/subgroup range | — | not run | KEEP (diagnostic) |

## Decoder levers handed to the decoder owners

These touch `wavelet_dequant.comp`, the Haar kernels and `pyrowave_decoder.cpp`, which the
fused-color / fused dequant+Haar work is changing; details are in the project analysis
`adreno-740-levers.md`.

1. **Quad-packed coefficients.** Store each band as RGBA16F with one 2×2 coefficient quad per
   texel. Dequant goes from 8 to 2 stores per thread; each Haar thread fetches 4 texels (one per
   band) and reconstructs a 4×4 block. About 4× fewer texture/store instructions on the
   coefficient round trip, with identical FP16 rounding. Use the same layout for whatever a fused
   dequant+level-0 kernel still writes.
2. **Skip empty blocks.** A fused or Haar kernel that knows a sub-block's high bands are empty
   (from the bitstream ballot) can skip their fetches and stores; size it with `block_occupancy`.
3. **Pin the wave size.** The iDWT kernels run with subgroup-size control off and dequant lets the
   driver choose 16–128. One A/B of required 64 vs 128 on the Haar kernels is cheap.
4. **Vulkan-native presentation should write eye images from a fragment pass.** Color-attachment
   writes keep UBWC for the compositor's reads; compute storage writes would lose it.
