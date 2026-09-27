# pyrowave_android — PyroWave decode on the headset

The core of the ALVR client decode path, standalone so a mistake costs a rebuild rather than an
APK install and a headset session. It decodes a `.wave` into Vulkan images on the Galaxy XR and
writes a y4m, so the result can be scored against the encoder's own input.

## Result

Decoding the frame the live ALVR encoder produced, on the Adreno 740:

| | psnr_y | psnr_u | psnr_v |
|---|---|---|---|
| PC reference (`pyrowave-decode`) | 57.76 | 56.29 | 55.82 |
| **headset, borrowed VkDevice** | **55.16** | **56.26** | **55.75** |

Chroma matches to within hundredths. Luma is 2.6 dB lower, well beyond visually lossless, and
worth understanding but not worth blocking on.

## The architecture, and why

ALVR's client is GLES/EGL (`Backends::GL`), so there is no `VkDevice` to borrow from it, and
PyroWave cannot import an AHardwareBuffer either — Granite's importer only handles Win32 handles
and FDs (`VkImportMemoryWin32HandleInfoKHR` / `VkImportMemoryFdInfoKHR`, no
`VkImportAndroidHardwareBufferInfoANDROID`). What remains, and what `pyrowave.h` describes, is the
**borrowed-device** path: create the Vulkan device ourselves, allocate the destination images
ourselves, and hand PyroWave plain image views. Nothing is imported or exported between us and the
codec.

`pyrowave_create_device` works on the Adreno 740, which reports every feature the decoder needs:
`shaderInt16`, `storageBuffer8BitAccess` and `subgroupSizeControl`. It selects the **fragment**
decode path, as expected for a tiled mobile GPU.

## Three things that fail silently

Each of these returns success and produces a wrong image rather than an error.

- **With a borrowed device, PyroWave owns no queue.** Leaving it to submit on its own means the
  work never executes: `decode_gpu_buffer` returns `PYROWAVE_SUCCESS` and the planes stay zeroed,
  which renders as a **flat green frame** (Y=Cb=Cr=0). Record into your own command buffer with
  `pyrowave_device_set_command_buffer` and submit it. A real client wants that anyway — the decode
  belongs in the frame's own command buffer.
- **The queue type must match the path.** `pyrowave.h`: *"Decoder: VK_QUEUE_COMPUTE_BIT (if using
  normal path), VK_QUEUE_GRAPHICS_BIT (if using fragment path)"*. The default is compute, so the
  fragment path needs `pyrowave_device_set_queue_type(VK_QUEUE_GRAPHICS_BIT)`.
- **Transition the planes.** They are created `UNDEFINED` while the image views declare `GENERAL`,
  and PyroWave performs no layout transitions of its own in the GPU buffer paths.

Grant decode targets both `STORAGE` and `COLOR_ATTACHMENT` usage: the header asks for STORAGE on a
decode view, while the fragment path writes them as colour attachments.

## This device has no single-component hardware buffers

Probed directly at 3328x1472, `AHardwareBuffer_isSupported` and `_allocate` agreeing:

| format | supported |
|---|---|
| `R8_UNORM`, `R8G8_UNORM`, `R16_UINT` | **no** |
| `R8G8B8A8_UNORM`, `R8G8B8X8_UNORM`, `Y8Cb8Cr8_420` | yes |

So PyroWave's single-component planes cannot themselves be hardware buffers, and decoding into an
RGBA8 buffer to use one channel does not work either. The planes must be ordinary `R8_UNORM`
images, and reaching GLES needs a conversion pass into one RGBA8 AHardwareBuffer — which can do
YCbCr to RGB at the same time, leaving ALVR's existing client shader unchanged, since it already
samples an RGBA texture from MediaCodec today.

## Usage

```
./build.sh                                   # needs pyrowave built for android, DEVEL=OFF
pyrowave_android <in.wave> <out.y4m>
```

| env | effect |
|---|---|
| `PYROWAVE_AHB=1` | decode into AHardwareBuffer-backed RGBA8 instead of plain R8 (does not work; kept as the record of that) |
| `PYROWAVE_FORCE_COMPUTE` / `PYROWAVE_FORCE_FRAGMENT` | override the path the device prefers |
