# Whole-stack scorecard, October 5

**The PC is already sending about 120 frames/s. The residual miss is on the
Quest: about 3 displayed target frames/s are repeats because a completed frame
is not published inside the existing half-frame wait. Lengthening that wait is
not available. Prerecord did not move this.**

This uses the already captured `.48` control windows
(`phase32-20261005T111731Z`, arms A1 and A2). It does not add a headset run.
`.49` was reviewed after the fact and not installed.

## What is measured

Stationary chart, 2080×2208 per eye, runtime 120 Hz, 1000 Mbps, 4:2:0, no
foveation, LOW decode priority, USB/TCP, one synchronous worker, 4 ms wait.
Two 12-second control windows.

| Stage | Evidence | Result |
| --- | --- | --- |
| SteamVR / encode source | `server_fps` median | 120.7 frames/s. The server is not short of frames. |
| Game pose match | `game_time_s` | 4.4 ms then 2.8 ms. On a still headset this match is unreliable; do not treat the total as optical latency. |
| Encode | `encoder_s` median | 2.1 ms |
| Network | `network_s` median | 4.3–4.5 ms |
| Decoder stage | `decoder_s` median | 13.9–14.0 ms, including queueing inside that statistic |
| Publication to selection | `decoder_queue_s` median | 3.3–3.8 ms |
| Eye copy / client composite | `client_compositor_s` median | 1.6 ms |
| Displayed unique targets | timestamp steps | 116.8 and 117.1 /s, lost 3.8 and 3.1 /s |
| ALVR client FPS counter | `client_fps` median | 120. This counter is not the unique-target count. |
| Native completion | prior `.48` table | about 8.0 ms median, 8.5–8.6 ms p99 on the serial control |

Freshness lines in the same capture show the 4 ms wait recovering most empty
selections (`late_taken` tracks `empty`). The frames that remain lost are the
ones still unpublished when that wait ends. The wait budget is already capped
at half of the 8.33 ms frame, so a longer fixed wait is not a remaining option.

## What this does not show

No per-frame link from a PC pose to a Quest submission. No Wi-Fi comparison.
No optical latency. No proof that the 14 ms decoder statistic is pure GPU time;
the native completion probe says the GPU fence is about 8 ms. `total_pipeline_latency_s`
stayed near 44 ms and is not used as a stage diagnosis.

## Decision

Do not repeat prerecord, async copy, release fences, Haar-pair kernels, vector
dequant, packet grace, or another wait-budget sweep. The next useful measurement
is a default-off counter on the experiment branch: when the half-frame wait
expires empty, record whether decode was still in flight. That separates a late
GPU completion from an idle decoder with no input, which this capture cannot.

`.49` ([run 37303701652](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37303701652),
`28e1941`) passed every matching job. Its three native libraries match reviewed
`.48` byte for byte, so the `.47` GPU pixel proof still applies by identity.
The client change is ownership cleanup only. It is not installed; `.48` remains
the headset build with prerecord off.
