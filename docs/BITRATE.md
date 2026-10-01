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
