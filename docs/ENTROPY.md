# Entropy coding: what it would be worth

October 7, 2026. This answers [PLAN.md](PLAN.md) 2.4: how much would entropy coding save over
PyroWave's raw bit-planes? Everything here is offline, on the PC; nothing has been built.

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
