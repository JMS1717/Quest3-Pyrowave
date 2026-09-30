# Quest 3 pipeline and experiments

SteamVR texture → ALVR uniform stereo composition → BT.709 planar Y/Cb/Cr (full-resolution luma, selectable chroma)
D3D11 textures → Vulkan external-memory/fence import → PyroWave CDF 9/7 encoding → MTU-sized UDP
datagrams → independent Android batch receive → bounded complete-frame assembly → Vulkan reconstruction → YCbCr-to-RGBA conversion into an
AHardwareBuffer → EGL import → ALVR OpenXR projection → Meta compositor.

The smallest implementation retains this already-established path. It is useful because tracking,
controllers, audio and SteamVR presentation remain ALVR responsibilities; inventing a new VR stack
would obscure codec and transport measurements. Existing GPU timestamps separate reconstruction
from conversion, and wall timing records the decode-to-fence wait.

## Constraints to measure before redesign

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
with 1000 Mbps/120 Hz/CDF 9/7/Compute/4:2:0. Dimensions are padded upward to codec alignment. Panel-relative size is not the larger lens-corrected OpenXR recommendation.

## Ordered next changes

1. Manually evaluate the default 1000 Mbps / 120 Hz / full-resolution configuration, then vary one setting at a time. Record
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

## Chroma selection

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

## Corruption repair and color bridge

The previous UDP path decoded arbitrary partial frames when a newer timestamp arrived, without requiring pristine low-frequency bands. Live full-resolution logs showed zero complete frames and thousands of partial frames, producing near-black damaged images. Reception also stopped during the synchronous GPU fence wait. The receiver and decoder now have separate owners; missing packets never reach presentation. Under packet loss, this deliberately holds the last valid frame instead of displaying damaged reconstruction. High-rate UDP still needs loss recovery or a reliable transport before sustained frame delivery can be promised.

The decoder describes each 4:2:0 chroma image with its actual half-sized extent. The former full-sized metadata was invalid for fragment render areas. The Adreno RGBA bridge now renders a full-screen triangle into the hardware buffer after checking combined sampled/storage/color-attachment import support. Other devices or unsupported imports retain the compute/copy path. The GPU completion fence remains in place. The producer excludes both the currently leased buffer and the pending buffer from its output rotation. The GLES staging copy finishes before the render loop can advance its lease; a paused render loop cannot permit overwriting its held buffer. Fragment wavelet reconstruction and fragment color conversion are separate choices. See the measured limitations in [repair evidence](../results/CORRUPTION-FIX.md).
