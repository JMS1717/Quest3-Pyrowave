# Whole-stack scorecard, October 5

**The PC is already sending about 120 frames/s. The residual miss is on the
Quest: about 3–4 displayed target frames/s are repeats. A later `.50` window
showed every steady-state empty wait expired while decode was still running,
not because the decoder was idle.**

The stage table below is the already captured `.48` control windows
(`phase32-20261005T111731Z`, arms A1 and A2). The classification run is in
Decision.

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
dequant, packet grace, or another wait-budget sweep.

`.50` (`d0a77ef`, [run 37309129157](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37309129157))
adds that classification on the existing once-per-second frame-wait line. Its
three native libraries match reviewed `.49` and `.48`. One 25-second stationary
window (`phase32-20261005T124733Z`, 2080×2208, runtime 120 Hz, 1000 Mbps, 4:2:0,
no foveation, LOW priority applied) measured:

| Result | Value |
| --- | --- |
| Unique displayed targets | 117.7 /s |
| Lost targets | 3.9 /s |
| Empty wait expiries in the window | 56, all `still_decoding`, none `idle_no_input` |
| GPU decode p50 / p90 | 5.90 / 6.78 ms |
| Convert p50 / p90 | 0.77 / 1.53 ms |
| Native fence p50 / p90 | 7.98 / 8.34 ms |

Startup seconds before that window did include idle expiries. The steady window
did not. Delivery matches the `.48` control; this run classifies the miss, it
does not claim a faster client. Every empty expiry was a decode that had not
published after 4 ms. Convert is too small to close that gap, and a different
presenter cannot publish a frame the GPU has not finished.

`.50` stayed installed after the verified run. Virtual Desktop remained the only
registered driver, saved settings stayed 144 Hz / 2000 Mbps / 4:4:4, and SteamVR
was stopped. Native 120 sustained and optical latency remain unmet.
