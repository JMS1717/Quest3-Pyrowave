# nvenc_caps

Dumps what NVENC on this machine actually reports, per codec. Needs `nvEncodeAPI.h` from the
Video Codec SDK beside it (it is not vendored here), then `build_caps.cmd`.

Measured on the PC's RTX 3090, NVENC API 13.0:

| cap | H.264 | HEVC | AV1 |
|---|---|---|---|
| `WIDTH_MAX` | **4096** | **8192** | unsupported |
| `HEIGHT_MAX` | 4096 | 8192 | unsupported |
| `SUPPORT_SUBFRAME_READBACK` | **1** | **1** | unsupported |
| `SUPPORT_MVHEVC_ENCODE` | 0 | **1** | unsupported |
| `SUPPORT_10BIT_ENCODE` | 0 | 1 | unsupported |
| `NUM_ENCODER_ENGINES` | 1 | 1 | unsupported |

Three things this settled, each of which had been assumed rather than measured:

- **H.264's 4096 width is real.** It is what caps `center_size_x` at 0.35 on a panel-native
  render, because the side-by-side frame would need 4288 px at 0.40. HEVC's limit is 8192, so the
  wall is the codec's, not the card's.
- **Sub-frame readback is supported and ALVR does not use it.** With `enableSubFrameWrite` a slice
  can be read back and sent while the rest of the frame is still encoding, overlapping the 9-11 ms
  encode with the 11 ms network stage instead of running them in series.
- **AV1 is not merely absent, it fails to query at all**, confirming Ampere cannot encode it.

`NUM_ENCODER_ENGINES = 1` also rules out split-frame encoding, which needs two or more.
