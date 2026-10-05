# XR2 Gen 2 whole-SoC review

October 5 review of every Quest 3 SoC block on the stream's path, after the GPU-only pass in
[ADRENO-740.md](ADRENO-740.md). Nothing here ran on hardware. "Est." marks arithmetic from repo
measurements; every lever needs an owner-approved headset screen before any keep/drop decision.

## Where the frame's time goes today

From the [whole-stack scorecard](WHOLE-STACK-SCORECARD.md) (native 120 Hz, 1000 Mbps, 4:2:0,
USB/TCP, one worker): network 4.3–4.5 ms, decoder stage ~14 ms of which the GPU fence is ~8 ms
and the rest is queueing behind the previous decode, publication to selection 3.3–3.8 ms, client
composite 1.6 ms. 117.7 unique targets/s, and every steady-state miss was a decode still running.

So the two goals pull on different blocks:

- **Throughput (207 Hz)** is GPU-bound. 207 Hz leaves ~4.8 ms per frame and the GPU spends ~7.3.
  Only GPU work, GPU clocks and memory traffic move it. CPU and transport levers will not.
- **Latency (<30 ms)** is spread out: the 4.3 ms transfer finishes before decode starts, decode
  waits for the previous frame, and the decoded frame waits for the next frame-loop slot.

## Ranked levers

| # | Block | Lever | Est. effect | State |
|---|---|---|---|---|
| 1 | GPU | Quad-packed RGBA16F coefficients (4× fewer texture/store instructions) | ~1–2 ms GPU per frame | handed to the decoder owners ([analysis](ADRENO-740.md#decoder-levers-handed-to-the-decoder-owners)) |
| 2 | GPU + DDR | Fused dequant+Haar, fused color, Vulkan-native presentation | ~115 MB/frame less traffic | owned by the 207 Hz plan |
| 3 | GPU | LPAC compute queue (`debug.q3pw.lpac=1`) | removes graphics-pipe preemption (~1.2 ms/frame seen once) | built, off |
| 4 | Pipeline | Start dequant while block packets are still arriving | hides most of the 4.3 ms transfer | proposal for the decoder owners, below |
| 5 | Power | Re-screen `debug.xrwired.perf_level=boost` on the current pipeline | up to 690 MHz vs 599–640 seen; ≤8–15% GPU time if clock-bound | flag exists; needs an endurance screen |
| 6 | CPU | Declare critical threads to the runtime (`debug.q3pw.thread_hints=1`) | jitter and wake-up latency, not throughput | built, off |
| 7 | USB | Faster link than the ADB relay | ~1–1.5 ms of the 4.3 ms transfer | needs system settings, so owner authorization |
| 8 | CPU | Fewer receive-path copies | unmeasured | measure before building |
| 9 | DSP/NPU | Hexagon offload | none until FastRPC is reachable | check only (`[Q3PW_SOC_CAPS]`) |
| 10 | VPU | Hybrid hardware-video periphery | speculative | not recommended now |

### 4. Overlap the transfer with decode (pipeline, est.)

Each PyroWave packet is one 32×32 block with its own header, and dequant runs per block. Today the
whole ~1 MB frame crosses USB (4.3 ms) before the first dispatch. Dequant could run in slices as
packets land (for example a band group per submit), so only the last slice and the iDWT stay
serial after the final byte. That attacks latency directly, which no clock or queue change does.
It touches the decoder's packet ingestion and dispatch, so it is a proposal for that owner, not
built here. Block indices run coarse to fine (`init_block_meta`: levels 4..0); if the encoder
sends in index order (not checked), the coarse levels arrive first and could even start the
low-level iDWT early.

### 5. BOOST performance level

The only BOOST screen (alpha.7, staging era) reached 108.2 fresh FPS against 103.7 for the same
path at SUSTAINED_HIGH, at 690 MHz versus 640 MHz ([DECODE-PIPELINE.md](DECODE-PIPELINE.md)). The
decode pipeline has changed since. `XR_EXT_performance_settings` defines BOOST for short, time-limited
demand, so a win only counts if it holds through an endurance window with thermal status logged.
The earlier ADB GPU level 6/7 trials are a different mechanism and stay closed.

### 6. CPU thread hints (built)

The XR2 Gen 2 has two performance and four efficiency cores. The frame-critical CPU threads are
the OpenXR frame loop (xrWaitFrame, GLES eye copy, xrEndFrame), the PyroWave decode worker (submit
and fence wait), the socket reader, the frame hand-off thread and the tracking input thread. None
of them is declared to the runtime today, so the scheduler is free to run them on an efficiency
core at a low clock or behind system work. `XR_KHR_android_thread_settings` exists for exactly
this, and engines such as Godot expose it.

With `debug.q3pw.thread_hints=1` the client enables the extension and declares the frame loop as
`RENDERER_MAIN`, decode workers as `RENDERER_WORKER`, and the socket, hand-off and tracking
threads as `APPLICATION_WORKER`. Each `[Q3PW_THREAD_HINTS]` line logs the call's result and the
thread's policy, real-time priority, nice value and allowed CPUs **before and after**, because
the spec only promises that the runtime "adjusts scheduling priority". Unset, the instance is
created exactly as before.

Expected effect: the mean decode is GPU time and will not move. What can move is p90/p99 of the
CPU-side waits (eye-copy CPU, publication to selection, network) and lost frames caused by a late
wake-up. If the after-state equals the before-state, the runtime ignores the hint and it is DROP.

### 7. USB transport

Video rides `adb forward`: adbd receives USB bulk transfers and relays them to the app over
loopback TCP. The measured 4.3 ms per ~1.04 MB frame is about 1.9 Gbps effective (p01 ~3.6 ms,
~2.3 Gbps) on a USB 3 Gen 1 (5 Gbps) port. A direct USB network link (NCM tethering) or an
Android Open Accessory bulk endpoint would bypass adbd: at ~3 Gbps a frame takes ~2.8 ms, est. 1–1.5 ms saved.
Both change system or USB settings, so they wait for owner authorization. At 207 Hz with the same
Mbps each frame is 42% smaller, so this term shrinks on its own.

### 8. Receive-path copies

A frame is copied on the CPU at least three times before the GPU sees it: socket read into the
ALVR buffer, `data.to_vec()` into the decoder's pending slot (`push_frame_nal`), then
`pyrowave_decoder_push_packet` into the decoder's upload path. Each is ~1 MB. No telemetry splits
this cost out, so measure it (a timestamp around the hand-off) before building zero-copy receive
into a mapped Vulkan buffer.

### 9. Hexagon DSP and NPU

On Galaxy XR the cDSP node is absent and the only FastRPC node is `system`-owned
([dspprobe](../tools/dspprobe/README.md)). Quest also reserves its DSPs for tracking and
perception. `[Q3PW_SOC_CAPS]` now lists which FastRPC nodes exist and whether the app can open
them, with no DSP session opened. Even if one is open, offload needs the Hexagon SDK, a skeleton,
and dma-buf sharing with Vulkan; it would only pay as a dequant pipeline stage. Low priority.

### 10. Video decoder block

The hardware video decoder sits idle by design. A hybrid stream (hardware HEVC for the periphery,
PyroWave for the fovea) would take GPU load off but brings back the hardware decoder's latency and
a second codec path. Not worth building before the GPU levers above are measured.

## Memory traffic (est.)

From the measured stage times, decode moves well under any plausible LPDDR5 limit today: dequant
writes ~27.6 MB of R16F coefficients in ~2.7 ms (~10 GB/s), and the iDWT reads them and writes the
LL chain and planes, ~45 MB in ~3 ms (~15 GB/s). That supports the instruction-issue reading in
[ADRENO-740.md](ADRENO-740.md), not a bandwidth wall. The wall arrives at 207 Hz: ~210 MB per
frame uncompressed is ~43 GB/s before the compositor. Storage-image writes get no UBWC compression,
and the repo's timings come from a flat chart that compresses unusually well, so any
bandwidth-sensitive screen needs real game content. Qualcomm does not publish the XR2 Gen 2
memory bandwidth.

## Built on this branch

| Flag | Change | Gate before live use |
|---|---|---|
| `debug.q3pw.thread_hints=1` (read once at app start) | Enables `XR_KHR_android_thread_settings` and declares the frame loop, decode, socket, hand-off and tracking threads | `[Q3PW_THREAD_HINTS] result=SUCCESS` and an after-state that differs from before |
| always on | `[Q3PW_SOC_CAPS]` once per session: each core's max clock, the frame loop's scheduler state, visible FastRPC nodes and whether they open read/write | — |

## First hardware session additions (needs the owner's go-ahead)

1. Save the `[Q3PW_SOC_CAPS]` line: the max clocks separate performance from efficiency cores and
   the allowed-CPU mask shows what the app may use.
2. One connection with `thread_hints=1`: read every `[Q3PW_THREAD_HINTS]` line. If no thread's
   policy, nice or CPU mask changed, DROP without a live screen.
3. Otherwise live 12 s ABBA at native 120 Hz (0/1/1/0): lost/s, eye-copy CPU p90, publication to
   selection p90, network p90.
4. BOOST vs SUSTAINED_HIGH ABBA with GPU MHz and thermal status, then one endurance window if it wins.

## Experiment record

| Change | Reason | Before | After | Headset result | Status |
|---|---|---|---|---|---|
| Thread hints | Critical threads undeclared, free to land on efficiency cores | 117.7 unique/s, 3.3–3.8 ms publication to selection | — | not run | CONTINUE (needs the before/after lines) |
| SoC caps log | Core layout, affinity and FastRPC facts | unknown on Quest | — | not run | KEEP (diagnostic) |
| BOOST re-screen | Clock-bound decode | 108.2 vs 103.7 FPS, one alpha.7 screen | — | not run on current pipeline | CONTINUE (needs endurance) |
