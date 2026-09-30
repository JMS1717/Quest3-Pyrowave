# Quest 3 pipeline and experiments

SteamVR texture → ALVR fixed-foveation composition → BT.709 planar full-resolution Y/Cb/Cr
D3D11 textures → Vulkan external-memory/fence import → PyroWave CDF 9/7 encoding → MTU-sized UDP
datagrams → Android batch receive → Vulkan reconstruction → YCbCr-to-RGBA conversion into an
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
| Receive/decode serialization | Kernel drops during GPU work | Separate receive staging from decode with bounded latest-frame queue |
| Vulkan/GLES bridge | Conversion GPU time and fence wall time | External semaphore/native-fence handoff; explicit lifetime validation first |
| Frame age/queueing | Pipeline stages, FPS, timestamp gaps | One- or two-frame bounded queues vs baseline; no unbounded backlog |
| Renderer/compositor | Runtime rates and actual intervals | Keep request, effective refresh and delivery FPS distinct |
| Thermal budget | GPU clocks, PowerManager thermal status, battery temperature | Interleaved replicates, cooling gate and long runs |

Auto currently honors PyroWave's Qualcomm preference. Choosing Compute by default merely because
it won on Galaxy XR would be unjustified; both paths must pass Quest correctness and timing tests.
The CDF 5/3 experimental path forces Compute. FP32 is the build baseline; FP16 experiments must
check Vulkan feature support and image quality instead of assuming faster arithmetic is safe.

## Ordered next changes

1. Measure an end-to-end 90 Hz baseline, then 120 Hz, at the smallest useful render size. Record
   actual encoded dimensions after alignment and foveation.
2. Measure receive packet pressure separately from decode. Android's existing `recvmmsg` path is
   retained; increasing datagram size beyond MTU would trade packet cost for IP fragmentation loss.
3. Prototype a bounded receive ring: receive continuously while the decoder owns the GPU. Preserve
   sequence/drop policy and bound memory by bytes as well as frames. Require equal-image correctness
   and lower frame age before adoption.
4. Prototype asynchronous GPU handoff to EGL. A returned hardware buffer must remain alive until
   GLES finishes sampling; skipping the current fence without a synchronization contract is unsafe.
5. Explore slice/tile delivery so reconstruction begins before a whole frame is transmitted.
   This requires PyroWave packet scheduling and partial-frame quality measurements, not just a
   higher bitrate slider. Whole-frame deadline and missing-region counters remain necessary.

The above redesigns are **proposals**, not implemented optimizations or performance claims. High
refresh fundamentally needs the entire application and runtime to meet 8.33/6.94/4.83/4.17 ms budgets.
Rates absent from runtime enumeration cannot be unlocked by this codec.
