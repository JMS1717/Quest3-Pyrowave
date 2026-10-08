# Latency: where ALVR's estimate goes

October 8, 2026, `.76`-`.78` on `claude/frame-budget`. ALVR's latency estimate is not motion-to-photon;
optical measurement is in [OPTICAL-LATENCY.md](OPTICAL-LATENCY.md).

## Summary

- **The server "encoder" stage is mostly not the encoder.** It runs from the frame's hand-over to
  the driver (`frame_composed`) to the frame reaching the transport (`frame_encoded`). At 120 Hz /
  1500 Mbit/s with the test scene it was 3.0-3.6 ms; PyroWave's own GPU work is about 0.3 ms of that.
- **`.78` encodes on a compute queue at high global priority.** The encode's GPU wait fell from
  0.63-0.72 to 0.37-0.39 ms (ABBA), and the encoder stage from 3.0-3.6 to 2.0-2.8 ms.
- **Under a game-like GPU load the stage grows to 5.7-6.5 ms**, PLAN's 5.7 ms. That growth is the game's
  own frame still rendering on the GPU after SteamVR hands it over, not queueing of the streamer's
  work. It is the game's GPU time, and every streamer pays it.
- **What is left on the server is about 1.5 ms:** the frame render 0.6-0.9 ms, the encode 0.4 ms and
  CPU work 0.5 ms. The large stages are elsewhere: the vsync queue (about 14 ms at 120 Hz, the
  runtime's prediction lead), network (about 6 ms) and decoder (5-7 ms).

## Encoder stage, measured

`[Q3PW_ENCODE_TIMING]` (`.76`+) logs each second the p50/p90/max of the stages of
`VideoEncoderPyroWave::Transmit`:

- `fence`: CPU wait for the D3D11 frame render (shared fence).
- `submit`: recording and submitting the Vulkan encode.
- `gpu`: waiting for the encode and its readback copy. `pyrowave_encoder_encode_gpu_synchronous` only
  submits; the wait happens in `compute_num_packets`.
- `packetize`: copying the blocks out of the readback buffer.
- `send`: handing the frame to the transport.

`[Q3PW_RENDER_GPU]` (`.76`+) logs the D3D11 timestamp-query GPU time of the frame render:
`compose` (layers and the adaptive downsample) and `convert` (colour, foveation, YCbCr planes).

120 Hz, 1500 Mbit/s, stream 2080x2208 from 3072x3216, wired, pan 60°/s, medians of per-second p50s
(ms):

| Run | fence | submit | gpu | packetize | send | compose | convert | ALVR encoder |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `.76`, graphics queue | 1.8-1.9 | 0.15 | 1.1-1.3 | 0.15 | 0.19 | 0.41 | 0.15 | 3.6 |
| `.77` base (graphics) | 1.22 | 0.14 | 0.63 | 0.15 | 0.20 | 0.42 | 0.15 | 3.04 |
| `.77` compute, high | 2.06 | 0.14 | 0.39 | 0.16 | 0.19 | 0.65 | 0.30 | 2.80 |
| `.77` compute, high | 1.90 | 0.13 | 0.37 | 0.15 | 0.20 | 0.64 | 0.16 | 2.69 |
| `.77` base (graphics) | 2.77 | 0.15 | 0.72 | 0.15 | 0.19 | 0.99 | 0.41 | 3.56 |
| `.78` default | 1.36 | 0.14 | 0.39 | 0.15 | 0.19 | 0.44 | 0.29 | 1.96 |

- The two "compute, high" cells also set the D3D11 GPU thread priority to 7, which made no
  difference and was dropped.
- The render's GPU time varies 2x between SteamVR starts (0.42-0.99 ms compose), with the GPU's clock
  state at this light load. The `gpu` wait does not: high priority wins in every pair.
- The fence wait is longer than the render's GPU time by 0.6-1.8 ms: the render can only start when
  the game's frame is finished on the GPU.

## Under a game-like load

`tools/local/gpuload` keeps the 7900 XTX busy for 5 ms every 8.33 ms, as a game rendering at 120 Hz
would, while a live cell runs:

| Cell | fence | gpu | compose | convert | ALVR encoder | ALVR game |
| --- | --- | --- | --- | --- | --- | --- |
| base (graphics) | 4.61 | 0.57 | 0.62 | 0.23 | 6.48 | 1.94 |
| compute, high + D3D11 priority 7 | 5.26 | 0.40 | 0.65 | 0.22 | 5.65 | 2.95 |

- The encoder stage grows by about 3 ms, entirely in `fence`, while the render itself takes only
  0.85 ms. ALVR's game stage shrinks by a similar amount: SteamVR hands the frame over earlier
  relative to its GPU completion.
- A tiny job submitted beside the same load finishes in about 0.24 ms p50 on any queue (`vkprobe`,
  `gpuload --probe`):

| Queue | p50 | p90 | p99 |
| --- | --- | --- | --- |
| D3D11 | 0.236 | 0.284 | 6.31 |
| Vulkan graphics, medium | 0.234 | 0.266 | 0.44 |
| Vulkan graphics, high | 0.234 | 0.266 | 0.50 |
| Vulkan compute, medium | 0.196 | 0.247 | 0.57 |
| Vulkan compute, high | 0.193 | 0.250 | 0.58 |
| Vulkan compute, realtime | 0.198 | 0.260 | 0.63 |

  So the streamer's work runs beside the game's, not behind it. Moving the frame render from D3D11
  to a Vulkan queue would not remove the wait. Only D3D11's p99 shows an occasional full-dispatch
  wait.

## Where the estimate goes (`.78`, 120 Hz / 1500, test scene)

| Stage | ms |
| --- | --- |
| Game | 9.1 |
| Server compositor | 0.2 |
| Encoder | 2.0 |
| Network | 5.7 |
| Decoder | 5.4 |
| Decoder queue | 2.7 |
| Client compositor | 2.6 |
| Vsync queue | 13.7 |
| **Total** | **39.8** |

## Client side (`.78`, 120 Hz / 1500, frame trace)

`debug.q3pw.frame_trace=1` ([FRAME-TRACE.md](FRAME-TRACE.md)), 2080x2208 CDF 5/3 4:2:0 over four wired
connections, p50 (p90) ms over one 10 s block:

| Stage | ms |
| --- | --- |
| First slice to complete frame | 3.05 (3.61) |
| Complete to decode queue | 0.20 (0.44) |
| Decode, submit to fence | 5.20 (6.00) |
| of which GPU decode (timestamps) | 2.83 (5.11) |
| Decoded to taken by the render loop | 2.89 (3.48) |

- **Transfer:** a 1.56 MB frame crosses in 3.05 ms, about 4.1 Gbit/s over the four adb connections.
  That is the USB link; it scales with the bitrate (about 2 ms at 1000 Mbit/s).
- **Decode:** the decode runs at LOW priority ([DECODE-PRIORITY.md](DECODE-PRIORITY.md)), so the eye
  copy and the runtime's compositor preempt it. Its GPU time is bimodal: 2.8 ms alone, about 5 ms
  when preempted.
- **Decoder queue:** the decoded frame waits for the render loop's next `xrWaitFrame`.
  With a near-empty test scene, game time plus decoder queue is one frame period more than the
  pipeline needs. In a real game, the game's own rendering takes that period, so moving the
  server's virtual vsync would not gain it.
- **Vsync queue:** `predicted_display_time - now` at submit, about 13.8 ms at 120 Hz and 12.9 ms at
  207 Hz. It is nearly flat in milliseconds, so it is the runtime's display pipeline, not frames
  queued by the client.

## Already tried

- D3D11 GPU thread priority 7 (`IDXGIDevice::SetGPUThreadPriority`, granted): no change in the render's
  wait, with or without load.
- Vulkan realtime global priority: granted for the probe, no faster than high.
- Decode at default instead of LOW priority (`debug.q3pw.decode_priority=default`, `.78`, 120 Hz /
  1500, ABBA, 8 s blocks): 115.6 and 114.3 fresh FPS against 117.1 and 119.7 for LOW. The decode
  was no shorter (decode 5.35-5.45 ms against 4.97-6.45 ms). LOW stays.
- **Does a faster eye copy shorten the vsync queue?** (October 8, `.85`, 207 Hz / 1000, CDF 5/3,
  ABBA, 12 s blocks, both arms at GPU level 4.) At 207 Hz the eye copy waits behind the decode
  (client compositor 4.8 ms for a 1.1 ms pass). If the runtime set its lead from the app's frame
  time, letting the copy run first should cut the vsync queue by a display period.
  `debug.q3pw.decode_priority=low` does let it run first:

  | Arm | Fresh FPS | Decoder | Decoder queue | Client compositor | Vsync queue | Total |
  | --- | ---: | ---: | ---: | ---: | ---: | ---: |
  | default | 177.5-177.6 | 7.5-7.7 | 2.6 | 4.8-4.9 | 10.65-10.68 | 32.4-36.0 |
  | LOW | 141.6-145.9 | 9.0-9.5 | 0.13-0.17 | 2.8-2.9 | 10.17-10.24 | 29.6-32.5 |

  The vsync queue moved by only 0.5 ms. The runtime's lead does not follow the app's frame time,
  so the hypothesis is rejected. LOW saves about 3.4 ms of client stages, because a decode that
  finishes late is taken at once, but it costs 32-36 fresh FPS at 207 Hz. Not adopted. (The
  release fence's earlier result points the same way: removing the CPU wait raised the
  estimate; see [RELEASE-FENCE-EXPERIMENT.md](RELEASE-FENCE-EXPERIMENT.md).)

## Next

- Network and decoder (about 11 ms together at 1500 Mbit/s): send blocks as they are encoded and
  start decoding coarse levels while fine ones arrive. The encode is 0.4 ms, so sending while
  encoding gains little. Overlapping the 3 ms transfer with dequantization could save about 1 ms,
  at the cost of several decode submissions per frame.
- Decoder queue (2.7 ms): the decoded frame waits for the client's next render.
- The vsync queue is the Quest runtime's prediction lead ([FRESHNESS.md](FRESHNESS.md)); no ALVR
  setting reaches it, and a faster app frame does not shorten it (above).
- At 207 Hz the GPU is the limit. The client stages shrink only with less GPU work per frame
  (decode, eye pass): the decoder stage is 7.5 ms for a 2.7-3.4 ms decode because the decode
  queues behind the eye copy and the compositor.
