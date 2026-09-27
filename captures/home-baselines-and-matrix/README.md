# Home baselines and panel-aspect resolution x bitrate matrix

**Static SteamVR Home results only.** One scene, headset unworn with the wear sensor covered, run
unattended from the Windows workspace after the cutover to the Windows-built server, PyroWave DLL
(Granite 842d9d5) and client. Nothing here says how gameplay behaves.

Fixed in every cell: PyroWave CDF 9/7, 4:4:4, 90 Hz, UDP, compute decode path, gaze foveation
0.20 x 0.178 with edge ratios 3/4, buffering 1.5. Varied: per-eye render scale and bitrate only.
Per-cell data: [`home-baselines.csv`](home-baselines.csv), [`matrix.csv`](matrix.csv)
(from `python -m xrbench.matrix`), and [`quality.csv`](quality.csv) for objective quality (below).

## Home baselines at the operating point (60 %, 1984x896 encoded)

| Median of 3 cells | 300 Mbps | 400 Mbps |
|---|---|---|
| Motion-to-photon | 57.0 ms | 60.2 ms |
| fps median / 1 % low | 90 / 77 | 90 / 48 |
| GPU decode / fence | 3.3 / 5.0 ms | 3.3 / 5.1 ms |
| Encoder | 5.5 ms | 6.7 ms |
| Dropped frames / late packets | 12 / 2,400 | 59 / 7,900 |

Five minutes sustained at 400 Mbps: 60.5 ms, 90 fps, decode 3.9 ms as the GPU clock fell to
637 MHz, ending at 80 C (thermal status 3). These baseline cells ran before the logcat marker fix,
so their receiver counts include each stream's first ~25 s; they are comparable with each other.

## Matrix: render scale x bitrate

| Panel scale | Encoded (side by side) | Mbps | Motion-to-photon | fps median / 1 % low | GPU decode / fence | Dropped | End temp, status |
|---|---|---|---|---|---|---|---|
| 100 % | 3328x1472 | 300 | 84.1 ms | 45 / 29 | 11.2 / 18.7 ms | 11 | 84.9 C, 3 |
| 100 % | 3328x1472 | 400 | 83.9 ms | 45 / 26 | 11.3 / 18.0 ms | 56 | 85.9 C, 3 |
| 90 % | 3008x1344 | 300 | 79.9 ms | 72 / 44 | 7.2 / 10.9 ms | 9 | 91.0 C, 4 |
| 90 % | 3008x1344 | 400 | 80.2 ms | 72 / 36 | 7.2 / 10.7 ms | 20 | 90.6 C, 4 |
| 80 % | 2624x1184 | 300 | 70.6 ms | 90 / 67 | 5.6 / 8.2 ms | 7 | 87.6 C, 4 |
| 80 % | 2624x1184 | 400 | 75.3 ms | 72 / 44 | 5.5 / 8.0 ms | 18 | 88.6 C, 4 |
| 70 % | 2304x1056 | 300 | 63.2 ms | 90 / 74 | 4.3 / 6.3 ms | 10 | 85.2 C, 3 |
| 70 % | 2304x1056 | 400 | 60.3 ms | 90 / 70 | 4.8 / 7.4 ms | 6 | 86.2 C, 3 |
| 60 % | 1984x896 | 300 | 63.5 ms | 90 / 72 | 3.7 / 5.6 ms | 5 | 80.8 C, 2 |
| 60 % | 1984x896 | 400 | 59.6 ms | 90 / 44 | 3.6 / 5.2 ms | 27 | 82.5 C, 3 |
| 60 % (repeat, last) | 1984x896 | 400 | 55.5 ms | 90 / 47 | 3.0 / 4.5 ms | 22 | 81.8 C, 3 |

Cells ran interleaved (no resolution twice in a row, bitrate alternating); the repeated first cell
at the end moved 4 ms, which bounds run-to-run drift. Receiver counts are windowed to the Home
measurement interval. The 100 % cells ran with the headset GPU at ~620 MHz against ~730 elsewhere.

## Reading

- **The knee is between 70 % and 80 %.** Up to 70 % every cell holds 90 fps with the decode fence
  well inside the 11.1 ms frame period. At 80 % the fence reaches 8 ms and 90 fps holds only at
  300 Mbps. At 90 % the fence is 10.7-10.9 ms, nearly the whole period, and at 100 % it is 18 ms;
  the stream falls to 72 and 45 fps, costing 20-25 ms of latency against 60 %.
- **70 % is the sharpest setting that keeps the operating point's behaviour:** 60-63 ms, 90 fps,
  1 % lows of 70-74, clean transport. It carries 1.37x the encoded pixels of 60 %.
- **Bitrate moves latency little** (a few ms either way) at every scale; 400 Mbps tends to cost
  1 % lows and late packets at 60 %, not at 70 %.
- **Heat:** 80 % and 90 % cells ended at thermal status 4 (critical), up to 91 C. Higher scales are
  not sustainable on this headset regardless of frame rate.
- **Quality holds up at 70 %** (next section): at 300 Mbps it gives up 0.4 dB PSNR-Y against 60 %
  while carrying 37 % more pixels, and at 400 Mbps it scores above 60 % at 300 Mbps.

## Objective quality

Separate quality cells (the same ten settings, labels `PQ...`) recorded the stream the headset was
sent (bitstream tap) and 30 lossless encoder-input frames inside the Home window (runtime dump).
The tapped frames were paired with the dumped ones by target timestamp (30 of 30 in every cell),
decoded with PyroWave's PC decoder (which matches the headset to within one code value) and
scored with ffmpeg against their own source (`python -m xrbench.quality`). Timing columns are from
the matrix cells above; the quality cells' own timing is not used, because the dump stalls the
encoder.

| Scale | Encoded | Mbps | Bits/pixel | PSNR-Y | SSIM | VMAF | Motion-to-photon | fps median / 1 % low |
|---|---|---|---|---|---|---|---|---|
| 100 % | 3328x1472 | 300 | 0.68 | 39.3 dB | 0.967 | 96.2 | 84.1 ms | 45 / 29 |
| 100 % | 3328x1472 | 400 | 0.91 | 40.6 dB | 0.972 | 96.3 | 83.9 ms | 45 / 26 |
| 90 % | 3008x1344 | 300 | 0.82 | 39.4 dB | 0.967 | 96.2 | 79.9 ms | 72 / 44 |
| 90 % | 3008x1344 | 400 | 1.10 | 41.0 dB | 0.973 | 96.7 | 80.2 ms | 72 / 36 |
| 80 % | 2624x1184 | 300 | 1.07 | 40.8 dB | 0.973 | 96.6 | 70.6 ms | 90 / 67 |
| 80 % | 2624x1184 | 400 | 1.43 | 41.9 dB | 0.977 | 96.9 | 75.3 ms | 72 / 44 |
| 70 % | 2304x1056 | 300 | 1.37 | 41.5 dB | 0.977 | 96.8 | 63.2 ms | 90 / 74 |
| 70 % | 2304x1056 | 400 | 1.83 | 42.5 dB | 0.981 | 96.8 | 60.3 ms | 90 / 70 |
| 60 % | 1984x896 | 300 | 1.88 | 41.9 dB | 0.981 | 96.7 | 63.5 ms | 90 / 72 |
| 60 % | 1984x896 | 400 | 2.50 | 44.3 dB | 0.988 | 97.1 | 59.6 ms | 90 / 44 |

- **Fidelity falls gently with resolution** as bits per pixel drop: 0.4 dB PSNR-Y from 60 % to 70 %
  at 300 Mbps, 1.8 dB at 400 Mbps, and about 2.5-3.8 dB from 60 % to 100 %.
- **70 % at 400 Mbps** (42.5 dB, SSIM 0.981) beats 60 % at 300 Mbps (41.9 dB) with 37 % more pixels,
  at 60.3 ms and 90 fps. It is the strongest candidate to replace 60 %, pending a worn look.
- **VMAF is saturated** (96-97 in every cell) and does not separate these settings; PSNR-Y and
  SSIM do.
- These scores measure each setting against its own encoder input. They show what compression
  costs at each resolution, not how much extra detail a higher resolution delivers to the eye;
  that needs a comparison in display space or a worn A/B.

## Also found and fixed during these runs

ALVR floors each eye's render width to a multiple of 32 (3197/2842/2486/2131 requested became
3168/2816/2464/2112), so 80 % and 70 % encode at 2624x1184 and 2304x1056; the size model now
matches all five measured sizes. The harness also gained: windowed receiver counts for Home cells,
a byte-offset check for ALVR session-parse errors, configure backups beside the runtime,
PyroWave diagnostics logged without ALVR's error pop-ups, and a PyroWave bitstream tap that reaches
the server (and is disarmed again when a run ends).
