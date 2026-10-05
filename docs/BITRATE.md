# Bitrate controls and profile budgets

Settings → Presets has a **5–2000 Mbps slider**, an **Auto bitrate** checkbox and
the padded resolution/frame budget. In manual mode the slider sets the codec payload
rate cap. In Auto mode it sets an enabled maximum; feedback can lower the requested
rate. The minimum floor is disabled when Auto is selected so congestion can be relieved.
Detailed latency limiters and an optional minimum are under Video → Bitrate → Adaptive.
For PyroWave the hardware decoder latency limiter is ignored: a target below fixed
GPU reconstruction time otherwise drives bitrate toward zero without reaching the
requested frame rate. Network/encoder feedback still applies. This policy does not
change hardware codecs; changing bitrate controls also clears an old learned decoder cap.
Resolution/refresh/codec changes require restarting SteamVR. Bitrate updates are live.

PyroWave receives ALVR's dynamic bitrate and sets its maximum frame size to
`floor(bitrate_bits_per_second / 8 / round(refresh_hz))`, aligned down to four bytes.
The serialized complete frame is checked against that cap and rejected if oversized;
it is never truncated. Rounding avoids a floating-point 119.99999 Hz value being
treated as 119 Hz. This is a per-frame cap,
not a promise to generate that many bytes. Auto estimates capacity from frame bytes
and latency and also uses encoder/decoder feedback. It updates approximately once
per second. It cannot fix an overloaded decoder merely by changing network bandwidth.
Use TCP for initial Auto testing: the experimental separate UDP timing estimate can
clamp to zero and skip adaptation samples. Auto is available but live stability still
needs measurement. An earlier USB test with an 8 ms decoder limiter collapsed the
bitrate; the PyroWave policy above addresses that mechanism in the next build.
The port also corrects the hardware decoder limiter's bytes/frame → bits/s units.

The full-panel PyroWave profiles request 2064×2208 per eye, padded to **2080×2208**.
The stereo frame contains **9,185,280 pixels**, or **13,777,920 bytes of raw 8-bit
4:2:0**. With 4:4:4 the raw size doubles; a fixed bitrate cap remains the same.

| Profile Mbps / Hz | Time budget ms | Payload bytes/frame ≤ | Raw/payload ratio | TCP Ethernet rate at cap ≥ Mbps |
|---|---:|---:|---:|---:|
| 400 / 72 candidate | 13.89 | 694,444 | 19.84:1 | 421 |
| 600 / 90 candidate | 11.11 | 833,333 | 16.53:1 | 632 |
| 600 / 120 experiment | 8.33 | 625,000 | 22.04:1 | 632 |
| 800 / 120 experiment | 8.33 | 833,333 | 16.53:1 | 843 |
| 1000 / 120 experiment | 8.33 | 1,041,666 | 13.23:1 | 1053 |
| 1500 / 120 experiment | 8.33 | 1,562,500 | 8.82:1 | 1580 |
| 2000 / 120 experiment | 8.33 | 2,083,333 | 6.61:1 | 2107 |

Two additional 120 Hz / 1000 Mbps experiments lower render size: 75% requests
1548×1656 per eye (padded 1568×1664), while 60% requests 1238×1325
(padded 1248×1344). Their raw/payload ratios are 7.51:1 and 4.83:1 respectively.
The byte cap and network demand remain 1,041,666 bytes/frame and ≥1053 Mbps.
These change pixel workload, not bandwidth. Short live observations are recorded in
[results](../results/LIVE-2026-10-01.md); median 120 FPS at 60% is not sustained 120 FPS.

Ethernet estimates assume full-size TCP segments (1460 bytes payload / 1538 bytes on
the wire). They exclude ACKs, retransmissions and Wi-Fi airtime overhead. A 2.4 Gbps
Wi-Fi PHY rate is not 2.4 Gbps payload capacity; 2000 Mbps is particularly aggressive.
All byte budgets fit PWU2's 8192-fragment transport bound. Passing those mathematical
bounds does not establish visual quality, thermal stability, or sustained frame rate.
Previous full-resolution standalone decode measurements exceeded 120 Hz's 8.33 ms;
the high-rate profiles therefore remain experiments. Native panel size is also distinct
from SteamVR's larger lens-corrected render recommendation.

Regenerate machine-readable budgets using `python -m tools.quest3.budget --out
presets/frame-budgets.json`. The dashboard uses the same padding and integer cap math
for whichever resolution, rate and chroma you select.

## Native USB screen, October 2

At 2080×2208 encoded pixels per eye, confirmed 120 Hz, 4:2:0, Haar Compute
decode, synchronous direct eye copy and a stationary chart, three 15-second
screens measured:

| Target Mbps | Actual video payload, median Mbps | Fresh submissions/s | Completed direct copies/s | Median completion ms | Estimated pipeline latency ms | GPU MHz |
|---:|---:|---:|---:|---:|---:|---:|
| 1000, control 1 | 1000.5 | 106.56 | 106.78 | 8.40 | 61.74 | 690 |
| 1500 | 1509.3 | 96.86 | 97.34 | 9.12 | 74.15 | 640 |
| 1000, control 2 | 1002.7 | 106.65 | 106.79 | 8.31 | 61.25 | 690 |

This shows native USB can carry roughly 1.5 Gbps of video payload in a short
screen. Extra bandwidth did not improve frame delivery in this sequence, so
1000 Mbps remains the working experimental target. Dynamic clocks differed;
these measurements do not isolate bitrate as the sole cause or establish a
sustained USB capacity. Battery temperature was 45–46 °C, thermal status 0.

The chart remained correct in both eyes. Captures do not establish a noticeable
in-headset quality benefit. ALVR network latency is a residual estimate, and
completed copies/submissions do not prove optical FPS or motion-to-photon.
P1 nominal FPS stayed near 60; sustained fresh 120 FPS remains unmet.
See [complete sanitized distributions](../results/BITRATE-USB-LIVE-2026-10-02.json).

## Current native120 payload isolation, October5

The reviewed `.44` pair passed all matching builds and production decoder tests;
its native libraries are byte-identical to GPU-verified `.42`. One continuous
Quest session used2080×2208/eye, confirmed120Hz, Haar/Compute4:2:0, no foveation,
LOW decode priority, synchronous direct eye copy, one TCP worker and the existing
4ms selection wait. Stage diagnostics and event waiting were off. Bitrate changed
live through1000/800/600/800/1000, with3 seconds settling and12-second windows.
No client restart or screenshot occurred between windows.

Source coverage, PID/clock alignment, runtime120 and unchanged codec/geometry
were verified. Every recorded encoder directive matched its target, and **every
captured serialized frame fit the aligned per-frame byte cap**. These settings
fit the payload math; that does not establish their120Hz timing or visual budget.

| Target Mbps | Median ALVR payload-rate estimate Mbps | Frame cap / largest frame bytes | Eye completions/s | GPU decode p50/p95 ms | Decode-to-fence p50/p95 ms | Client FPS p1 |
| --- | --- | --- | --- | --- | --- | --- |
| 1000 | 1006.1 | 1,041,664 / 1,041,652 | 118.08 | 5.90 / 6.73 | 7.95 / 8.29 | 60.0 |
| 800 | 806.6 | 833,332 / 833,308 | 118.39 | 6.02 / 6.93 | 8.05 / 8.42 | 60.0 |
| 600 | 606.0 | 625,000 / 624,992 | 118.66 | 6.68 / 6.91 | 8.10 / 8.37 | 60.0 |
| 800 | 806.8 | 833,332 / 833,308 | 117.49 | 5.71 / 6.62 | 7.83 / 8.16 | 60.0 |
| 1000 | 1008.0 | 1,041,664 / 1,041,652 | 118.10 | 6.05 / 6.73 | 7.98 / 8.30 | 60.0 |

**Keep1000Mbps as the default.** The controls agree near118.1 eye completions/s;
800 varied118.4→117.5, while the single600 block reached118.7. P1 stays near60;
none establishes sustained fresh120. Neither GPU nor completion time decreased
consistently with payload. GPU endpoints shifted599/640MHz, so DVFS and timing
remain confounds. Extra link capacity or lower rate alone has not removed the
remaining completion/presentation bottleneck. Avoid an unchanged rate sweep.

Temperatures stayed31–33°C, thermal status0, AC powered. All temporary settings,
proximity and driver registrations restored without errors, preserving VD. A
single final1000 compositor image retained correct eye mapping/orientation;
lower-bitrate and in-headset quality acceptance were not tested. ALVR total/network
latency estimates with a stationary headset are not optical motion-to-photon.
[Sanitized metrics, exact byte budgets and package provenance](../results/PAYLOAD-NATIVE120-2026-10-05.json).
