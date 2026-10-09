# 207 Hz frame trace: where the last fresh frames go

October 7, 2026. Settings for every run below:

- 207 Hz panel, 2080x2208 per eye encoded from a 3072x3216 render, 4:2:0, no foveation.
- Haar at 1000 Mbit/s unless noted, mode 5 (packed YCbCr in the AHB).
- Maximum GPU clock on (690 MHz), two wired video connections.
- Direct eye copy (then `debug.q3pw.direct_eye_copy=1`; the default since `.117`). The eye-pass
  diagnostics, the eye GPU timer and the raw sRGB write below exist only in that path
  (`alvr/graphics/src/direct_eye.rs`). With `debug.q3pw.direct_eye_copy=0` the client uses ALVR's
  staging renderer.
- A 60 deg/s pan.
- Measurement windows of 10-12 s after a 3-5 s settle.

At these settings the client shows about **194-197 fresh frames per second**. 207 is the ceiling.
This document records why, what changed, and what was rejected.

## Tools

**`debug.q3pw.frame_trace=1`** (read when a stream starts) turns on a per-frame critical-path trace
(`alvr/client_core/src/frame_trace.rs` in the ALVR patch).

- Hot paths only read `CLOCK_MONOTONIC` and push a fixed-size record into a bounded buffer.
- A separate thread logs `[Q3PW_TRACE]` batches four times a second.
- Records cover the whole path:
  - slice arrival and frame completion
  - decode queueing, start and fence
  - publication, supersession and selection by the render loop
  - `xrWaitFrame`/`xrBeginFrame`/`xrEndFrame`
  - eye-draw submission and eye-copy GPU completion
- `K` records pair the monotonic clock with OpenXR time, so frame age at display is measured on one
  clock.

**`tools/quest3/frame_trace.py <client.log> --from EPOCH --to EPOCH`** summarizes a window. It
reports:

- unique frames per second at each stage
- stage durations
- inter-arrival and inter-publication spread
- publications per selection interval
- where superseded frames landed
- whether each empty selection waited on the network or on the decoder

**`debug.q3pw.eye_ablate=N`** draws only 1/N of each eye. It is a diagnostic, and the image is
deliberately wrong. It measures how much the eye pass costs the frame rate.

**`debug.q3pw.eye_probe=1|2|3`** is also a diagnostic with a wrong image. Each mode replaces the eye
pass's colour so that reads can be separated from writes:

| Mode | Eye pass draws | Reads from the decoded image |
|---|---|---|
| 1 | a UV gradient | none |
| 2 | hash noise | none |
| 3 | luma only | luma |

## What the trace shows (Haar, 1000 Mbit/s, one 12 s window)

| Stage | Per second |
|---|---|
| frames arrived | 206.3 |
| decoded and published | 203.1 |
| replaced before decode | 3.2 |
| **taken by the render loop (fresh)** | **194.3** |
| superseded after publication | 8.8 |
| empty selections | 4.0 |
| display periods | 195.4 |

- Decode keeps up: GPU decode p50 is 2.67 ms (p90 3.50 ms), and 203 of 206 frames are published.
- The loss is after publication. It has two parts:
  - **Missed display periods.** The loop saw 195 periods instead of 207, because `gl.finish` in the
    render path occasionally pushes a frame past its slot.
  - **Selection jitter.** Publications arrive with a p5-p95 spread of 4.1-6.0 ms around the
    4.83 ms period. About 5 % of selection intervals receive two frames, so the older frame is
    superseded about 1.2 ms after the previous selection. Another 4 per second receive none.
  - 44 of 48 empty selections had their packet already on the headset and were still decoding.
- Frame age at display (predicted display time minus tracking time) is 30.4 ms p50.
- The render loop spends 4.1 ms p50 from render start to eye submit. That includes the selection
  wait of up to 2.4 ms while a decode is in flight.

CDF 5/3 at the same settings is decode-bound: 180 frames are decoded per second and 25.6 per second
are replaced before decode.

## Eye-pass cost

Ablation shows the eye pass costs frames. Valid blocks only: a single memory clock (2736 MHz) and
the 207 Hz period confirmed.

| Wavelet | Full eye pass | Eye pass at 1/N | Eye GPU p50 |
|---|---|---|---|
| Haar | 195.3 fresh | 201.1 fresh | 1.28 vs 0.16 ms |
| CDF 5/3 | 177.9 fresh | 192.5 fresh | 1.28 vs 0.16 ms |

The eye-probe split shows that writes, not reads, dominate the pass:

| Eye pass | Eye GPU p50 |
|---|---|
| original (sRGB `pow` in the shader) | 1.28 ms |
| **raw sRGB copy (now the default)** | **1.05-1.10 ms** |
| no reads, gradient | 0.81 ms |
| no reads, noise | 0.82 ms |
| luma reads only | 0.67 ms |

**Raw sRGB is now the default in the direct eye copy** (`debug.q3pw.raw_srgb_copy=0` restores the
shader conversion; the staging renderer does not use it).

- With `GL_EXT_sRGB_write_control`, an sRGB target, sRGB correction and gamma 1, the eye pass
  disables sRGB encoding on write and writes the already sRGB-coded values directly.
- That removes the per-pixel `pow` and saves about 0.2 ms per frame. The output is identical.

The remaining 0.8 ms fills two 2080x2208 sRGB swapchain images. It is bound by write bandwidth.

- Compressing the decoder's AHB (UBWC) could save at most the read share, about 0.2 ms, so that
  work is not planned.

## Publication and selection experiments

| Arm | Display periods/s | Fresh/s | Notes |
|---|---|---|---|
| default | 196-200 | 197.3 / 194.2 | |
| `debug.q3pw.release_fd=1` | 206-207 | 195.9 / 196.1 | every period kept; fresh unchanged |
| release_fd + `debug.q3pw.frame_hold_us=6000` | 207 | 199.5 / 199.4 | vs 197.0 / 196.4 without the hold |

**`release_fd`** ([RELEASE-FENCE-EXPERIMENT.md](RELEASE-FENCE-EXPERIMENT.md)) exports the eye copy's
GPU completion as a sync fd to the decoder, which then waits for it before reusing the buffer. The
render thread no longer blocks in `gl.finish`.

- The render loop then sees every display period.
- Fresh FPS stays where it was: about 205 frames are published per second, but 10 per second are
  superseded and 11 selections per second find nothing.
- With throughput restored, selection jitter is the limit. It stays opt-in.

**Frame hold** (`debug.q3pw.frame_hold_us=1..10000`, single decode worker, opt-in) changes selection.

- When a second frame is published before the first was taken, the older frame is held instead of
  superseded.
- The next selection shows the held frame if it is younger than the limit. Otherwise it drops it
  for the newer one.
- It needs a four-buffer output ring and a third protected buffer
  (`pyroclient_decode_guarded3`), so the decoder never overwrites a held frame.
- With release_fd it adds about 2.8 fresh FPS and halves superseded frames.
- The cost: queue residence p50 rises by 1.5 ms (to 2-2.6 ms), and ALVR's latency estimate rises
  by 5.4 ms (30.5 to 36.0 ms; not motion-to-photon).
- The arms ran at different memory clocks (3196 vs 2736 MHz).
- It misses the planned gate (5 FPS, or half the superseded and empty frames, within 2 ms of
  residence), so it stays opt-in.

A private replay script (`select_sim.py`) tries other selection policies offline from the trace's
publication and selection times. It ignores the knock-on effect on the eye pass, so its numbers only
rank policies.

## What limits 207 Hz now

1. **Publication jitter against a fixed selection point.** About 10 superseded frames and 10 empty
   selections per second once every period is kept. They come from the 4.1-6.0 ms publication
   spread.
2. **Eye-pass fill**, 0.8 ms of write bandwidth per frame. It competes with decode on a GPU that is
   80-90 % busy.
3. **Memory clock.** The memory clock is not under our control. It moves sessions between 2092,
   2736 and 3196 MHz and shifts results by several FPS. Every comparison here rejects or separates
   blocks whose memory clock changed.

## Rejected or left opt-in

| Candidate | Result | Status |
|---|---|---|
| release_fd | every display period kept, fresh FPS unchanged | opt-in |
| frame hold 6 ms | +2.8 FPS for +5 ms latency estimate | opt-in |
| eye ablation and probes | diagnostics only; wrong image | diagnostic |
| UBWC for the decoded AHB | at most about 0.2 ms | not planned |
