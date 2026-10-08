# Entropy coding: what it would be worth

October 7, 2026. This answers [PLAN.md](PLAN.md) 2.4: how much would entropy coding save over
PyroWave's raw bit-planes? The first sections are offline estimates on the PC. The
[last](#a-real-coder-and-its-decode-on-the-quest-october-8) measures a real coder and its GPU
decode on the Quest (October 8). The coder is not in the stream.

## PyroWave's raw layout

From `shaders/block_packing.comp`:

- Coefficients sit in 32x32 blocks, split into 8x8 blocks.
- Each active 8x8 has a 24-bit control word: a base plane count, plus 0–3 extra planes for each
  of its eight 4x2 groups.
- Each 4x2 group stores (planes × 8) raw magnitude bits. Every coefficient in the group pays for
  the largest one.
- Signs take one bit per nonzero coefficient.
- Inactive 8x8 blocks cost only a ballot bit, and each active 32x32 block has a small header.

There is no entropy coding, by design: the author chose it for GPU speed.

## Byte-level compressors

General-purpose compressors over real PyroWave bitstreams of a 2080x2208 frame (CDF 5/3):

| Rate | zlib -9 | zstd -19 | lzma -9e | Order-0 bytes |
| --- | --- | --- | --- | --- |
| 207 Hz, 1000 Mbps | 10.3% | 11.4% | 12.8% | 7.7% |
| 207 Hz, 1500 Mbps | 10.0% | 11.0% | 12.5% | 7.4% |
| 120 Hz, 1500 Mbps | 10.4% | 11.4% | 12.9% | 7.4% |

These are lower bounds. The bit-planes hide the coefficient structure from a byte-level coder.

## A coefficient model

`tools/downsample/entropy_model.py <input.y4m>` models the luma plane:

1. A 5-level CDF 5/3 transform.
2. A deadzone quantizer, with each band's step scaled by its synthesis norm.
3. The same quantized coefficients costed three ways:
   - **raw:** PyroWave's layout as above.
   - **ctx:** a context coder. This is the static conditional entropy of each magnitude given
     its already-coded neighbours (left, up, up-left, up-right) and its parent band, plus
     Exp-Golomb bits above 15 and one bit per sign. It is a two-pass bound, so an adaptive coder
     lands a few percent above it.
   - **group:** PyroWave's structure kept (control words, headers and signs as they are), with
     the magnitudes coded by a static code per band and group plane count. This is the cheap
     GPU option: no neighbour context.

For each costing, the model then finds the step that fits the live byte cap and the PSNR it
reconstructs at. Luma is assumed to take 75% of the cap.

| Frame | Rate | Raw | ctx | group |
| --- | --- | --- | --- | --- |
| Harness chart, one eye | 207 Hz, 1000 Mbps | 27.69 dB | +2.09 dB | +0.58 dB |
| Harness chart, one eye | 207 Hz, 1500 Mbps | 30.51 dB | +2.40 dB | +0.70 dB |
| Harness chart, one eye | 120 Hz, 1500 Mbps | 35.48 dB | +3.33 dB | +1.24 dB |
| Quality corpus `static2`, stereo | 207 Hz, 1000 Mbps | 25.53 dB | +2.02 dB | |
| Quality corpus `static2`, stereo | 207 Hz, 1500 Mbps | 28.00 dB | +2.60 dB | |
| Quality corpus `static2`, stereo | 120 Hz, 1500 Mbps | 32.87 dB | +3.50 dB | |

In bits at equal quality:

- The context coder saves 21–27% at the operating points. That is about the same as a third
  more bitrate.
- The static per-group code saves 8–10%.
- Most of the gain therefore comes from neighbour context, which tells the coder where the
  significant coefficients are. A group of eight that pays for its largest coefficient is where
  the raw layout loses.

Checks:

- **The raw baseline is not a straw man.** The real encoder at the same caps reaches 25.96,
  27.48 and 32.25 dB luma PSNR on the harness chart, below the model's raw numbers. The real
  encoder spends bits by contrast sensitivity, not for PSNR.
- **The content is synthetic.** Both frames are harness charts: textures, text, zone plates and
  noise. Game frames have more smooth areas, where raw bit-planes waste more, so they will
  probably save at least as much. That is not measured, because no game frames have been
  dumped yet.

## What it would take

The 25% gain needs a context-adaptive coefficient coder on both ends:

- An HTJ2K-style block coder: significance coding with quad contexts, plus magnitude bits
  bounded by the context.
- The encoder runs on PC compute.
- The decoder runs on Adreno compute, one 32x32 block per subgroup.

The open question is decode time on the Quest:

- At 207 Hz the decoder already sets the frame rate.
- At 120 Hz there is about 3 ms of headroom at panel resolution.
- Published GPU HTJ2K decoders reach hundreds of 4K frames per second on desktop GPUs, and
  Adreno 740 is far smaller.

**Next step:** a decode-throughput prototype on the headset. It would decode a synthetic 32x32
block stream with HTJ2K-style significance and magnitude passes, and time it at 2080x2208 per eye.

- Under about 2 ms: the coder is worth building.
- Above that: it pays only at 120 Hz.

## A real coder and its decode on the Quest (October 8)

### The coder ("QR")

It was built to decode with one GPU thread per 32x32 block
([`tools/entropy/qrcodec.hpp`](../tools/entropy/qrcodec.hpp)):

- Each 32x32 block of a band is an independent rANS stream. A table gives each non-empty
  block's word offset.
- **8x8 flags:** inside a block, a raw bit per 8x8 marks it non-empty.
- **Quad patterns:** in a non-empty 8x8, the 2x2 quads come in raster order. Each quad codes its
  4-bit significance pattern with a context taken from the largest magnitude of the quad to its
  left and of the quad above, each clamped to 3: 16 contexts.
- **Coefficients:** each nonzero coefficient codes `|q| - 1` as a 16-symbol magnitude class.
  - The class's context is the sum of those two maxima, in 13 buckets.
  - The class's low bits and the sign follow as one raw field.
- **Probabilities:** 12-bit frequencies per frame, 16 per context, for 26 band classes (luma and
  chroma, by level and orientation). They travel in the frame header.
- **Words:** 16-bit, and the rANS state stays in [2^16, 2^32).

**Model variants** (`tools/downsample/entropy_qr.py`, harness chart, luma, dB over raw at equal
bytes). Variant letters:

- **ctx:** the context-coder bound from above.
- **Huffman + Rice:** prefix and Rice codes.
- **R:** static rANS, the coder that was built.
- **P:** the parent coefficient added to the pattern context.

| Rate | ctx | Huffman + Rice | R (rANS) | R + parent (P) |
| --- | --- | --- | --- | --- |
| 120 Hz, 1500 Mbps | +3.33 | +2.35 | +2.76 | +2.86 |
| 120 Hz, 1000 Mbps | +2.60 | +1.69 | +2.14 | |
| 207 Hz, 1000 Mbps | +2.09 | +1.26 | +1.74 | +1.63 |

- rANS keeps about 0.4 dB that prefix codes lose. At step 10.5, prefix codes lose 20 KB on
  patterns and Rice codes lose 29 KB on magnitudes.
- Parent context adds little or nothing, so the coder leaves it out.

**Real coder, end to end:**

- **Input:** `static2`, stereo, luma plus 4:2:0 chroma.
  - `tools/entropy/dump_qcoef.py` produces it, using one quantizer step for the frame (17.887).
  - That step is chosen so that PyroWave's raw packing takes exactly one 120 Hz / 1500 Mbit/s
    frame: 1,562,500 bytes.
- **Content:** 14.49M coefficients, 1.66M of them nonzero; 14,151 blocks, 9,583 of them coded.
- **Result:** `qrenc` codes it in 1,241,518 bytes, 20.5% smaller than raw. The CPU and GPU
  decoders both reproduce every coefficient.
- **Stream lengths per block:** p50 38 words, p90 165, p99 323. The maximum is 681 words, in the
  LL band.

### GPU decode

The decoder is `tools/entropy/qrdec.comp`, and `tools/entropy/qrbench.cpp` runs it on the headset
through `adb shell`.

- **What a run does:** it decodes the whole frame into int16 coefficients and checks every
  coefficient against the dump.
- **Timing:** timestamp queries, p50 of 60 runs after 5 warm-ups. The GPU clock was confirmed at
  its 690 MHz maximum during runs, and the first run of each process is discarded because the
  clock is still rising.
- **Every row below decoded the frame exactly.**

| Quest 3 (Adreno 740) | Decode |
| --- | --- |
| First version: binary search in global tables, raster output, frame order, local size 32 | 6.1 ms |
| 64-bucket LUT, rANS state in 16 KB of shared memory, local size 128 | 8.3 ms |
| Tables compared in registers (two 16-byte loads), raster output, local size 128 | 7.7 ms |
| ... with lane-interleaved output (each workgroup's stores coalesce), local size 64 | 4.2 ms |
| ... with blocks dispatched longest first, local size 128 | 2.87 ms |
| ... with branch-uniform steps, loading a stream word only when it is consumed | **2.65 ms** |
| Same structure, writing only a checksum per block (decode alone) | 2.56 ms |
| 7900 XTX, desktop: register tables, raster output, frame order | 0.53 ms |
| 7900 XTX: the Quest's best configuration | 1.24 ms |

What limits it on Adreno:

- **Scattered stores.** One thread writing its own 32x32 block makes every store a separate
  transaction. Interleaving the output by lane halved the time.
- **Tail.** Frame order puts many long LL-band blocks in a few waves. Dispatching longest first
  cut the time by a third.
- **Throughput, not the serial chain.**
  - Decoding the frame twice in one dispatch takes 1.63x as long, and four times 3.2x.
  - So the GPU is close to saturated at about 2.35 ms per frame.
  - With the 4000 longest blocks skipped (76% of the decode steps), the time is still 1.7–2.0 ms.
- **Loads.** Each step loads 32 bytes of table and one stream word.
  - Loading the word only when it is consumed gained 0.3 ms.
  - Every variant that added a dependent load lost time.
- **Steps.** The frame is 5.04M decode steps:
  - 153k 8x8 flags;
  - 1.56M quad patterns, of which 47% are nonzero;
  - 1.66M coefficients, each a class symbol plus a raw field.
  - A 4x4 mask symbol per 8x8 would remove only 14% of the steps.

**Already tried, slower or no help:**

| Variant | Result |
| --- | --- |
| All tables (25 KB) in shared memory | 9.2 ms; occupancy collapses |
| 64-bucket LUT plus a refining search | 3.67 vs 2.89 ms |
| Packed u16 compare (8 subtracts, one popcount), then a load of the range | 3.26 vs 2.96 ms |
| Loading the second half of a context's table only when needed | 2.82 vs 2.69 ms |
| Prefetching the next stream word | no change |
| Clearing the output first and skipping zero stores | no change |
| Longest-first order with raster output | 12.1 vs 6.1 ms |

### Verdict

- **Speed.** 2.65 ms for a 120 Hz / 1500 frame misses the 2 ms target.
- **Context:** today's whole CDF 5/3 decode is 2.8 ms at 1000 Mbit/s, and its dequantization
  alone is 1.25 ms (overlapped) to 2.6 ms.
- **Fused estimate (not measured):** a decoder fused with dequantization would replace that
  stage, adding about 0.1–1.4 ms.
- **At 120 Hz:** it fits. The decode fence is far inside the 8.3 ms frame, and the gain is
  20% fewer bytes at the same quantization.
- **At 207 Hz:** it does not fit, because decode already limits the frame rate there.

**What building it would still take:**

- **PyroWave's own quantization.** The dump uses one step per frame, but the live encoder
  allocates by contrast sensitivity and block RDO. Dump the live encoder's quantized blocks to
  confirm the 20%.
- **Game frames**, not only harness and corpus frames.
- **Encoder:** QR encoding on PC compute. Encoding is per block and parallel, and the 7900 XTX
  decodes a frame in 0.5 ms.
- **Rate control:** it targets coded bytes, not raw-packing bytes.
- **Decoder:** fused with dequantization, writing dequantized values to the iDWT inputs, with the
  stores coalesced as above.
- **More parallelism if needed.** Two or four streams per 32x32 block cost about 2–4 bytes each
  per coded block (1–3%). That would only help if the GPU were latency-bound, and on Adreno it is
  close to throughput-bound.

**Decision:** keep the prototype. Building it into the stream is the largest remaining
bitrate-for-clarity lever, especially for Wi-Fi at about 1000 Mbit/s. Do not start it before the
owner's 120 Hz 4:4:4 and supersampling tests show where the clarity gap is.
