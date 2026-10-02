# pyroclient — PyroWave decode on the headset, as RGBA8 AHardwareBuffers

The ALVR client's PyroWave decoder. It gives the GLES client exactly what MediaCodec gives it —
an `AHardwareBuffer` to wrap in an `EGLImage` — so the existing staging and render path is
unchanged. Built on the borrowed-`VkDevice` path proven in `tools/pyrowave_android`.

```
pyroclient_create(w, h, chroma444, full_range, ring) -> pyroclient*
pyroclient_push_packet(c, data, size)                 -> 1 when the frame is complete
pyroclient_decode(c, &ahb, &info)                     -> synchronous; ahb ready for EGL import
pyroclient_clear(c)                                   -> drop a frame whose deadline passed
```

Inside: plain R8 decode planes (this device has no single-component hardware buffers), a
YCbCr→RGBA compute pass writing an RGBA8 AHardwareBuffer-backed image **directly as a storage
image** (the Adreno 740 allows it; a convert+copy fallback exists), a ring of three, a fence wait,
and a release to `VK_QUEUE_FAMILY_FOREIGN_EXT` before the buffer is handed out. The three silent
failure modes recorded in the harness README are all handled.

## Measured on the Galaxy XR (3328x1472 4:2:0 encoder frame)

| | ms |
|---|---|
| PyroWave decode (GPU timestamps) | 4.9 |
| YCbCr→RGBA into the AHardwareBuffer | 2.7 |
| submit → fence, p50 | 9.2 |

Correctness: 47.3 dB RGB PSNR against the PC reference decode converted by ffmpeg (MSE 1.2, one
8-bit step). Colour bars score 38 dB because chroma upsampling at razor edges differs between
samplers; that is not the conversion.

## Two bugs this exposed

- The harness's shipped SPIR-V header had **no `LocalSize` execution mode** and ran 1x1x1: only
  the top 184 rows of a 1472-row frame were written. The harness had timed that pass but never
  scored it. `ycbcr_to_rgba_spv.h` here is regenerated from the `.comp` with the NDK's glslc; do
  that again whenever the shader changes (`build.sh` does not).
- The shader hard-coded full range. Fed a limited-range frame it scored 25 dB — the client-side
  twin of the 29 dB phantom. Range is now a push constant from `pyroclient_create`, filled from the
  server's `use_full_range` via the DecoderConfig blob.

## Build and test

```
./build.sh                    # libpyroclient.so (bundled into the APK) + pyroclient_test
pyroclient_test in.wave out.rgba [iterations]     # on the headset, LD_LIBRARY_PATH to the .so dir
pyroclient_test in.wave - 80 fragment 10 1        # timing/guarded-buffer screen, no CPU dump
```

`tools/build_alvr_2013.sh` builds this and stages `libpyroclient.so` + `libpyrowave-shared.so`
into the ALVR clone's `deps/android_openxr/arm64-v8a`, which cargo-apk packages and which
`client_core/build.rs` links against.

The production output allocation is GPU-only. The probe now refuses CPU pixel dumps
unless the actual allocation includes CPU_READ usage, even if the driver would accept
`AHardwareBuffer_lock`. Such unsupported locks produced misleading dumps with the
recommended allocation experiment. Use `-` to measure timing and protected-buffer
reuse; it does not establish image correctness. Validate production pixels through
the actual GLES/OpenXR consumer or a proper GPU reference readback. Adding CPU usage
to the allocation would change the path under test and its performance.
See the [Android allocation and lock requirements](https://developer.android.com/ndk/reference/group/a-hardware-buffer#ahardwarebuffer_lock).

The Galaxy XR scores above are historical upstream results, not current Quest 3
quality acceptance. Quest measurements and readback limitations are recorded in
[the decode pipeline](../../docs/DECODE-PIPELINE.md).
