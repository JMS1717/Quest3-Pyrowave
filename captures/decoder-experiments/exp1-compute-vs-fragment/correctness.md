Phase 1 (MEASURED on device, standalone `pyrowave_android`, identical `score.wave`, 3328x1472 4:4:4, 1,041,656 B, 200 iterations, run twice per path):

| plane | fragment vs compute: max abs | MSE | PSNR | differing pixels |
|---|---:|---:|---:|---:|
| Y | 4 | 0.156 | 56.20 dB | 764,174 / 4,898,816 (15.6 %) |
| Cb | 1 | 0.027 | 63.80 dB | 132,935 |
| Cr | 1 | 0.033 | 62.93 dB | 162,121 |

Against the PC reference decode (`pyrotap\ref444.y4m`): fragment Y 55.79 dB (max 4), compute Y 64.16 dB (max 1); chroma both ~64-66 dB. The compute path is the more accurate of the two; the difference is the fragment path's FP16 render-target intermediates (`vert`/`horiz` R16F), i.e. expected floating-point implementation behaviour, not a reconstruction fault. GATE: PASS (differences <= 4 code values, > 55 dB between paths, quality not materially changed and, if anything, in compute's favour).

Standalone timing in the same runs (MEASURED, T2 = GPU decode, 200 iterations): fragment best/mean 9.402/9.446 ms then 9.383/9.446; compute 7.021/7.077 then 7.041/7.076. Convert: fragment 2.734 ms, compute 1.466 ms. Headset hottest zone 66.7 C before, 66.8 C after.
