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
PYROWAVE_WAVELET=haar pyroclient_test in.wave out.rgba 80 compute 10 1 gpu 0
```

`tools/build_alvr_2013.sh` builds this and stages `libpyroclient.so` + `libpyrowave-shared.so`
into the ALVR clone's `deps/android_openxr/arm64-v8a`, which cargo-apk packages and which
`client_core/build.rs` links against.

The production output allocation is GPU-only. Pixel output now defaults to a separate
EGL/GLES consumer: retain the completed AHB, import it with image preservation, sample
at 1:1 into a linear RGBA8 framebuffer, finish GPU work and read that framebuffer.
No CPU allocation flags or CPU lock are added to the source. This happens after the
timed decode loop and does not enter the production library or APK renderer.

The final arguments are `gpu|cpu` and `flip_y=0|1`. Row zero in GPU output samples
texture v near zero by default, or near one with an explicit flip. No automatic
orientation choice, sRGB/range transform, stereo splitting or reprojection is applied;
use asymmetric input and an appropriately aligned reference. Image size is capped at
256 MiB and must fit the device's GLES texture/viewport limits. Import/extension or
draw/read failures produce a nonzero exit and incomplete metadata, with no CPU fallback.

Explicit `cpu` still refuses a dump unless the allocation includes CPU_READ usage,
even if `AHardwareBuffer_lock` would accept it. Unsupported locks produced misleading
dumps in earlier experiments. Use `-` to measure timing/protected-buffer reuse without
readback; it does not establish image correctness. Adding CPU usage to the allocation
would change the path under test and its performance.
See the [Android allocation and lock requirements](https://developer.android.com/ndk/reference/group/a-hardware-buffer#ahardwarebuffer_lock).

Successful pixel output includes `out.rgba.json` with method, actual dimensions,
allocation usage, explicit orientation, producer completion and a CRC32 of the raw
bytes. A failed rerun marks metadata incomplete so stale bytes cannot pass the scorer.
`python -m tools.quest3.score reference.rgba out.rgba --width W --height H --out score.json`
requires that sidecar by default. Legacy bytes need `--allow-unverified-readback` and
are marked unverified; that override cannot accept a known failed or mismatched sidecar.
The checksum links the record to the file; it does not establish reference correctness.

Cloud Mesa software-GLES tests exercise external-image sampling, exact RGB/alpha,
explicit row flip, odd widths, changing pixels, pack-state isolation and bounds.
Android compile checks cover the AHB wrapper. Actual Quest Vulkan-to-EGL visibility,
recommended allocation layout and independent color reference acceptance remain pending
hardware testing. This diagnostic is not a sustained streaming or optical-latency test.

The Galaxy XR scores above are historical upstream results, not current Quest 3
quality acceptance. Quest measurements and readback limitations are recorded in
[the decode pipeline](../../docs/DECODE-PIPELINE.md).
