# pyrowave_d3d11 — D3D11 → PyroWave encode harness

This is the core of the planned `VideoEncoderPyroWave`, developed standalone because a mistake here
costs a 10 s rebuild instead of a 15 min ALVR streamer rebuild. It does exactly what the ALVR
encoder will do — take a D3D11 texture, hand it to PyroWave as an imported external image, encode,
packetize — and adds only a y4m reader and a `.wave` writer so the output can be scored.

## Result: the path is proven equivalent to the reference encoder

At 43,388 bytes/frame (25 Mbps at 72 Hz) on the RTX 3090, against the 3328x1472 SteamVR Home frame:

| | psnr_y | psnr_u | psnr_v |
|---|---|---|---|
| `pyrowave-encode` CLI (reference) | 39.73 | 45.19 | 46.80 |
| **this harness, 3-plane GPU path** | **39.73** | **45.19** | **46.80** |

Identical. At a near-lossless budget both give 59.11 / 60.78 / 61.27, also identical.

## The recipe that works

Three **separate single-component** imported textures, each reporting its **own** extent:

- Y: `DXGI_FORMAT_R8_UNORM`, full resolution
- Cb, Cr: `DXGI_FORMAT_R8_UNORM`, half resolution each
- created with `D3D11_RESOURCE_MISC_SHARED | D3D11_RESOURCE_MISC_SHARED_NTHANDLE`
- shared via `IDXGIResource1::CreateSharedHandle`, imported with
  `VK_EXTERNAL_MEMORY_HANDLE_TYPE_D3D11_TEXTURE_BIT`
- views from `pyrowave_image_get_image_view(img, PLANE_0, SAMPLED)` — the aspect is ignored for
  single-component formats, and the returned view is R8_UNORM / IDENTITY / GENERAL

**Do not override the chroma views' `width`/`height` to the luma extent.** The header's note that
"the width/height is for the luma plane" applies to a *plane view of a planar image*, not to
separate chroma images. Forcing luma dims on a half-res chroma image drops chroma to ~25 dB with
error growing toward the bottom-right, which is what an over-running fetch looks like.

ALVR's own textures are **not** created shared, so — exactly like `VideoEncoderNVENC`, which
copies into its own NVENC input texture — the encoder must own these textures and copy into them.

## Open bug: the NV12 2-plane path misreads chroma

Importing a single `DXGI_FORMAT_NV12` texture instead, with the helper's own plane views, gives
**perfect luma and ~26 dB chroma**. Isolated as follows, all on the same frame:

| test | psnr_u / psnr_v |
|---|---|
| `encode_cpu_synchronous`, same NV12 bytes | 60.78 / 61.27 |
| readback of the shared NV12 texture vs source | **bit-exact (inf)** |
| GPU encode from that same texture | **26.24 / 26.47** |

So the data is right and the texture is right. The views are the helper's own output
(`aspect=PLANE_1` for both chroma, swizzle R and G, `G8_B8R8_2PLANE_420_UNORM`, luma extent), and
the helper's extent is correct here — forcing half extents makes it *worse* (11 dB). Adding an
ownership acquire/release with `VK_QUEUE_FAMILY_EXTERNAL` changes nothing (byte-identical result).

This is why the recipe above uses three planes. Worth reporting upstream alongside the Adreno
encoder bug.

## Usage

```
build.cmd                                  # on the PC; needs the DEVEL=OFF pyrowave build
pyrowave_d3d11 <in.y4m> <out.wave> <max_bytes_per_frame> [frame_count]
```

`max_bytes_per_frame` is the rate-control budget: `bitrate_bps / 8 / fps`. At 72 Hz, 25 Mbps is
43403 and 300 Mbps is 520833.

Diagnostic modes, all off by default — these are the bisect that found the above:

| env | effect |
|---|---|
| `PYROWAVE_3PLANE=1` | three separate imported textures (**the working path**) |
| `PYROWAVE_CPU_PATH=1` | same bytes via `encode_cpu_synchronous`; isolates data from GPU path |
| `PYROWAVE_READBACK=1` | dumps `readback.y4m` from the shared texture; proves the upload |
| `PYROWAVE_LUMA_EXTENT=1` | forces luma extent on 3-plane chroma views (reproduces the fault) |
| `PYROWAVE_NV12_HALF=1` | forces half extent on NV12 chroma views (makes it worse) |

Score a run with the ffmpeg already on the PC:

```
pyrowave-decode out.wave out.y4m
ffmpeg -i out.y4m -i source.y4m -lavfi psnr=stats_file=- -f null -
```

## Note on synchronisation

The harness orders the D3D11 copy before the Vulkan read with an `ID3D11Fence` plus a CPU wait
(`SetEventOnCompletion`). That is unambiguous but heavier than necessary. The real encoder should
import a shared `ID3D11Fence` as a timeline semaphore
(`VK_EXTERNAL_SEMAPHORE_HANDLE_TYPE_D3D12_FENCE_BIT`, which `pyrowave-device-validation --external`
confirms on this GPU) and pass it as the `acquire` sync point instead.
