# PyroWave rate-quality curve

Measured with `xrbench.ratequality` on a real captured frame: `ALVR_PYROWAVE_DUMP` at frame 600,
3328x1472 4:4:4 full range, the exact planes the live ALVR encoder consumed.

**Source content** (`source_frame.png`): the SteamVR dashboard — fine text, sharp UI edges and
colourful thumbnails over a dark gradient. Harder for a wavelet codec than a landscape, and more
representative of what actually needs to stay legible.

| Mbps | psnr_y | psnr_u | psnr_v |
|---|---|---|---|
| 10 | 30.09 | 35.32 | 38.34 |
| 25 | 34.40 | 39.39 | 40.62 |
| 50 | 37.21 | 44.34 | 44.19 |
| 75 | 40.25 | 46.57 | 46.37 |
| 100 | 42.55 | 47.95 | 47.87 |
| 150 | 45.38 | 49.37 | 49.88 |
| 200 | 47.19 | 50.24 | 51.10 |
| 300 | 49.07 | 52.29 | 52.53 |
| 400 | 50.83 | 54.08 | 53.98 |
| **600** | **57.45** | 57.10 | 57.17 |
| 800 | 63.50 | 58.27 | 58.27 |

## Three things this settles

**The cap is filled to 100% at every rate, including 800 Mbps.** PyroWave is cap-limited rather
than content-limited on this frame, so nothing has plateaued — more bits still buy quality at the
top of the range. That is the opposite of the assumption that 600 Mbps was generous.

**The marginal return *increases* above 400 Mbps**, which is backwards for a normal
rate-distortion curve: +1.76 dB for 300->400, then +6.62 dB for 400->600. Something changes near
the top of the range rather than tapering. Worth understanding before treating 600 as a free
choice — it may be a quantiser stepping to full precision, in which case the knee is a property of
the codec and not of this content.

**"39.69 dB at 25 Mbps" was content, not a constant.** That headline came from the aurora frame, a
dark, still landscape. The same 25 Mbps on this dashboard frame gives **34.40 dB** — 5.3 dB less.
Any bitrate claim for PyroWave has to name the content it was measured on.

## Decode time does not scale with bitrate

Measured on the Adreno 740 with `tools/pyrowave_android`, same frame encoded at four rates,
100 iterations each:

| Mbps | frame bytes | T2 best | T2 mean | T3-T2 convert (best) |
|---|---|---|---|---|
| 100 | 173,584 | 4.91 ms | 7.03 | 1.46 ms |
| 300 | 520,832 | 5.12 ms | 6.72 | 1.46 ms |
| 600 | 1,041,660 | 5.26 ms | 6.93 | 1.46 ms |
| 800 | 1,388,884 | 5.53 ms | 7.51 | 1.54 ms |

**Eight times the data costs 0.62 ms.** Decode is resolution-bound, not rate-bound, and the
conversion pass is flat as expected.

That makes the operating point simple: **bitrate is nearly free on the latency budget**, quality
keeps climbing to the top of the range, and the link measures 860-978 Mbps. So run as high as the
link reliably carries rather than trading quality for latency — the trade barely exists. The only
real cost of more bits is network time.

## Correction: there is no quantiser step

An earlier pass sampled 400 then 600 Mbps and read the +6.62 dB gap as something stepping near the
top of the range. A fine sweep in 25 Mbps increments (`rate_quality_fine.csv`) shows no
discontinuity — increments run 0.06 to 1.37 dB with no threshold. The slope in 400-700 is about
double that in 300-400 (~0.036 against ~0.018 dB/Mbps), so the acceleration is real, but "a
quantiser reaching full precision" was over-reading two coarse samples.

## What it does not yet answer

The frame is the SteamVR dashboard, not gameplay. A game frame has different statistics — more
texture, less text — and text is the case a wavelet codec finds hardest, so this curve is
plausibly pessimistic for real content. Worth capturing before fixing an operating point.

Nothing here says what is *perceptually* enough. These are plain PSNR-Y numbers; PyroWave's author
targets ~35 dB on PSNR-HVS-M-H, a perceptually weighted metric that reads higher than plain PSNR,
so the two are not comparable. The bitrate that looks right to the eye is still a subjective call
against real content.
