# Quest 3 pipeline and experiments

## Current pipeline (October 7, `.64`)

This is the path a PyroWave stream takes. A fresh install starts from a conservative
400 Mbit/s / 72 Hz Starter profile ([profiles](PROFILES.md)). The "Quest 3 PyroWave 207 Hz (measured)" profile sets the stream
below and also turns on the maximum GPU clock over USB. Numbers below are from the owner's
settings: a 3072x3216 per-eye render, streamed at 2080x2208 per eye, 207 Hz, Haar, 1000 Mbit/s,
4:2:0, no foveation, maximum GPU clock (690 MHz), USB. They come from 10-12 s screens
([FRAME-TRACE.md](FRAME-TRACE.md)); sustained play is not measured.

**PC (Windows, SteamVR driver)**

1. ALVR composes the SteamVR layers into one stereo frame at the stream size, 4160x2208 for two
   2080x2208 eyes. A larger game render is filtered down in that pass: bilinear by default,
   adaptive Catmull-Rom or Lanczos-3 optional ("Render downsample filter").
2. The frame is converted to BT.709 Y/Cb/Cr R8 planes. With 4:2:0 (the default) Cb and Cr are
   averaged to half width and height ([Chroma selection](#chroma-selection)).
3. The D3D11 planes are imported into Vulkan with external memory and fences, and PyroWave encodes
   one complete frame to the bitrate's byte cap. The wavelet is Haar by default; CDF 5/3 and
   CDF 9/7 are selectable. The cap is about 604 KB per frame at 1000 Mbit/s and 207 Hz.

**Transport**

- Over USB, the frame goes over adb-forwarded TCP. By default it is split into two contiguous
  slices sent in parallel on dedicated connections (`video.pyrowave.wired_video_connections`,
  0-4, ports 9950-9953). One adb-forwarded connection tops out at about 2 Gbit/s.
  - Each slice has a 48-byte header (magic `PWT1`) with the frame's timestamp, length, offset and,
    from `.64`, a per-frame sequence number (bytes 28..32). The client assembles each frame by
    sequence number and rejects overlapping or mismatched slices. A server older than `.64` sends
    sequence 0, and the client then groups slices by timestamp.
  - A connection that fails, or whose writes stall for 1 s, is treated as lost; video then falls
    back to ALVR's stream socket. A client that does not answer the wired connections gets the
    stream socket from the start.
  - `.64` parallel wired video is not yet hardware-tested over USB, and unplugging and replugging
    with two connections is unchecked.
- Over Wi-Fi, the whole frame goes on ALVR's stream socket (TCP by default). UDP (`PWU2`,
  [below](#udp-byte-fragments-pwu2)) is experimental.

**Headset (Quest 3 client)**

1. One decoder thread takes the newest complete frame. A frame replaced before decode starts is
   dropped; incomplete frames never reach the decoder.
2. pyroclient (`tools/pyroclient/`) decodes on the Adreno 740 with Vulkan compute. Quest 3 Auto
   selects Compute.
   - Haar uses the multilevel inverse Haar ([HAAR32.md](HAAR32.md)). CDF 5/3 uses Decoder V2
     ([DECODER-V2.md](DECODER-V2.md)). Both default to mode 6 since `.118` (mode 5 before).
   - Haar GPU decode is 2.67 ms p50 (3.50 ms p90) at 690 MHz.
3. **Mode 5** ([PRESENT-YCBCR.md](PRESENT-YCBCR.md)): the last iDWT level writes packed YCbCr
   straight into the output slot, an RGBA8 AHardwareBuffer of width x height/2 (4160x1104). Luma is
   stored as 2x2 quads in the left half and Cb/Cr as one RG pixel per texel in the right half.
   There is no separate YCbCr-to-RGBA pass. Mode 6, the default, keeps this layout but stores two
   chroma pixels per texel (Cb, Cr of the left pixel in RG, of the right in BA), so the chroma
   half is half as wide and the decoder stores half as many chroma texels. The client steps down to mode 4 (RGBA8 output through
   a conversion pass) without storage-image support on the AHB, with limited range, or with
   Catmull-Rom chroma.
4. The decoded slot is published with its GPU fence complete. The output ring has three slots
   (four with the opt-in frame hold); the slot being published and the slot the eye pass holds are
   never written.
5. The render loop selects the newest published frame after `xrWaitFrame`. If none is ready but
   one is decoding, it waits for it, up to half a frame period and at most 4000 µs (2.4 ms at
   207 Hz; `debug.q3pw.frame_wait_us`, [FRESHNESS.md](FRESHNESS.md)). With no new frame, it
   resubmits the last released eye images.
6. The GLES eye pass imports the AHB as an EGLImage and converts BT.709 YCbCr to RGB in its
   fragment shader (`present_ycbcr.glsl`).
   - By default since `.117` it draws straight into the two OpenXR eye swapchains (direct eye
     copy; `debug.q3pw.direct_eye_copy=0` turns it off). The 207 Hz measurements used this path. On an sRGB swapchain it writes the already
     sRGB-coded values with sRGB encoding off (raw sRGB copy, default on in this path,
     `debug.q3pw.raw_srgb_copy=0` to disable): 1.05-1.10 ms GPU p50 per frame.
   - With `debug.q3pw.direct_eye_copy=0` the client uses ALVR's staging renderer, which draws into a staging
     texture that ALVR's stream renderer then draws into the eyes. The staging path also has the
     YCbCr program. If its program fails to build, packed frames are skipped and the log names the
     workaround (`debug.q3pw.haar32=4`, or `debug.q3pw.cdf53v2=4` with CDF 5/3).
   - If the direct copy's packed programs fail to build, packed frames go to the staging path.
7. The eye pass finishes with `gl.finish` before the slot is handed back to the decoder.
   `debug.q3pw.release_fd=1` (opt-in) passes a sync fd instead
   ([RELEASE-FENCE-EXPERIMENT.md](RELEASE-FENCE-EXPERIMENT.md)).

At these settings about 206 frames arrive per second, 203 are decoded and published, and 194-197
are shown. The losses are after publication: see
[What limits 207 Hz now](FRAME-TRACE.md#what-limits-207-hz-now).

**Not used, and why**

- Planar R8 output (decoding Y, Cb and Cr into three R8 AHBs) is impossible: Quest 3 gralloc has no
  R8 AHardwareBuffer ([DECODE-PIPELINE.md](DECODE-PIPELINE.md#planar-output-without-the-rgba-pass-not-possible-on-quest-3-october-4)).
  Mode 5 packs YCbCr into RGBA8 instead.
- A Vulkan OpenXR presenter is research only ([VULKAN-PRESENTATION.md](VULKAN-PRESENTATION.md)).
- Foveated encoding is off by default. Light foveation, 4:4:4, mode 6 (paired chroma), frame
  hold and release fences are opt-in.

## Early design notes (October 1-4)

The rest of this document is the design record from before multilevel Haar, packed YCbCr output,
Decoder V2 and parallel wired video. It is kept as history. Where it disagrees with the section
above, the section above is current. The current status and next steps are in
[HANDOFF.md](HANDOFF.md).

The October 1 path was: SteamVR texture → ALVR uniform stereo composition → BT.709 planar
Y/Cb/Cr (full-resolution luma, selectable chroma) D3D11 textures → Vulkan external-memory/fence
import → PyroWave CDF 9/7 encoding → complete codec frame → TCP (default) or experimental
MTU-sized UDP byte fragments → Android receive → bounded complete-frame assembly → Vulkan
reconstruction → YCbCr-to-RGBA conversion into an AHardwareBuffer → EGL import → ALVR OpenXR
projection → Meta compositor.

The smallest implementation retains this already-established path. It is useful because tracking,
controllers, audio and SteamVR presentation remain ALVR responsibilities; inventing a new VR stack
would obscure codec and transport measurements. Existing GPU timestamps separate reconstruction
from conversion, and wall timing records the decode-to-fence wait.

### UDP byte fragments (PWU2)

PyroWave's packet boundary is advisory: an indivisible codec block can exceed 1368 bytes.
The original one-codec-packet-per-datagram assumption caused oversize rejection/truncation.
PWU2 encodes one complete frame, splits its bytes into payloads of at most 1368 bytes,
and rejoins all fragments before the single codec push. Loss drops a frame; FEC/retransmission
for this UDP path is not implemented. TCP preserves byte delivery but can delay later frames
during retransmission. PWU2 and the new protocol version require matching client/server builds.

### Constraints to measure before redesign

| Limit | Observable | Experiment |
|---|---|---|
| Qualcomm reconstruction | Decode GPU time, correctness, decode-to-fence | CDF 9/7 Compute vs Fragment at equal pixels and clocks |
| Radio/packet rate | Delivered Mbps, loss, assembly/deadline misses | Native UDP probe at 600–2000 Mbps; PHY rate alone is insufficient |
| Independent receive/decode | Complete transport frames, loss, queue replacements | Verify continuous reception and bounded latest-complete-frame handoff |
| Vulkan/GLES bridge | Conversion GPU time and fence wall time | External semaphore/native-fence handoff; explicit lifetime validation first |
| Frame age/queueing | Pipeline stages, FPS, timestamp gaps | One- or two-frame bounded queues vs baseline; no unbounded backlog |
| Renderer/compositor | Runtime rates and actual intervals | Keep request, effective refresh and delivery FPS distinct |
| Thermal budget | GPU clocks, PowerManager thermal status, battery temperature | Interleaved replicates, cooling gate and long runs |

Quest 3 Auto uses Compute. The initial 1536×768 RGBA comparison against PC reference decoding
had maximum per-channel errors of 3/2/3 for Compute, versus 45/16/85 for Fragment. These are
same-bitstream reconstruction differences, not source-image PSNR or proof for every scene.
Fragment remains an explicit manual experiment. CDF 5/3 forces Compute.

Foveated encoding and client foveation are disabled in server/client logic, defaults and presets.
The first full-frame configuration requests 2064×2208 per eye, aligned to 2080×2208 by ALVR,
with 400 Mbps/72 Hz/CDF 9/7/Compute/4:2:0/TCP as a conservative candidate. The 1000 Mbps/120 Hz target is experimental. Dimensions are padded upward to codec alignment. Panel-relative size is not the larger lens-corrected OpenXR recommendation.

### Ordered next changes

Status on October 7: the stream now runs at 207 Hz (item 1 is long past). The asynchronous handoff
of item 4 exists as the opt-in `release_fd`. Item 5 became parallel wired slices, which deliver a
frame sooner but are still decoded only once the whole frame has arrived. The October 1 list
follows.

1. With permission to resume headset testing, evaluate the 400 Mbps / 72 Hz / full-resolution candidate before advancing to 600 Mbps / 90 Hz and the 120 Hz experiments. Record
   actual encoded dimensions after alignment.
2. Measure receive packet pressure separately from decode. Android's existing `recvmmsg` path is
   retained; increasing datagram size beyond MTU would trade packet cost for IP fragmentation loss.
3. Validate the implemented bounded receiver: four assembling frames, at most 8192 MTU-sized packets each, plus one pending complete frame and one being decoded. Duplicate indices do not count toward completeness. Reordered tails can arrive across newer frames. Incomplete frames expire after a bounded 25–50 ms assembly window; complete frames are handed off immediately. Both transport completeness and codec readiness are required before decoding.
4. Prototype asynchronous GPU handoff to EGL. A returned hardware buffer must remain alive until
   GLES finishes sampling; skipping the current fence without a synchronization contract is unsafe.
5. Explore slice/tile delivery so reconstruction begins before a whole frame is transmitted.
   This requires PyroWave packet scheduling and partial-frame quality measurements, not just a
   higher bitrate slider. Whole-frame deadline and missing-region counters remain necessary.

The independent receiver and fragment color bridge are implemented. An asynchronous replacement for the synchronized GLES copy and slice/tile delivery remain proposals. High
refresh fundamentally needs the entire application and runtime to meet 8.33/6.94/4.83/4.17 ms budgets.
HorizonOS v2.7 can accept rates absent from enumeration; this client verifies requests and frame periods.

### Chroma selection

4:2:0 is the default. Windows first converts RGB to three BT.709 R8 planes, then
GPU passes average each 2x2 Cb/Cr block into half-width/half-height shared R8 textures.
Luma keeps its full extent. Aligned per-eye widths prevent chroma averaging across
the stereo seam. The encoder imports each plane with its actual extent and signals
chroma in byte 8 of the decoder configuration. The Quest decoder already supports
both modes; it allocates smaller chroma targets for 4:2:0.

This halves the uncompressed component sample count, not necessarily compressed
bitrate. A fixed bitrate remains a separate frame-size budget. 300/400 Mbps presets
provide lower budgets; 600–2000 Mbps targets remain available. No savings, latency
or visual quality measurements are claimed for this new path. A future fused
conversion could avoid writing full-sized chroma intermediates.

### Corruption repair and color bridge

The previous UDP path decoded arbitrary partial frames when a newer timestamp arrived, without requiring pristine low-frequency bands. Live full-resolution logs showed zero complete frames and thousands of partial frames, producing near-black damaged images. Reception also stopped during the synchronous GPU fence wait. The receiver and decoder now have separate owners; missing packets never reach presentation. Under packet loss, this deliberately holds the last valid frame instead of displaying damaged reconstruction. High-rate UDP still needs loss recovery or a reliable transport before sustained frame delivery can be promised.

The decoder describes each 4:2:0 chroma image with its actual half-sized extent. The former full-sized metadata was invalid for fragment render areas. The Adreno RGBA bridge (used today only by mode 4 and below; mode 5 has no RGBA pass) now renders a full-screen triangle into the hardware buffer after checking combined sampled/storage/color-attachment import support. Other devices or unsupported imports retain the compute/copy path. The GPU completion fence remains in place. The producer excludes both the currently leased buffer and the pending buffer from its output rotation. The GLES staging copy finishes before the render loop can advance its lease; a paused render loop cannot permit overwriting its held buffer. Fragment wavelet reconstruction and fragment color conversion are separate choices. See the measured limitations in [repair evidence](../results/CORRUPTION-FIX.md).
