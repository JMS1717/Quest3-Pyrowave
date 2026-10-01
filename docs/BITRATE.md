# Bitrate controls and profile budgets

Settings → Presets has a **5–2000 Mbps slider**, an **Auto bitrate** checkbox and
the padded resolution/frame budget. In manual mode the slider sets the codec payload
rate cap. In Auto mode it sets an enabled maximum; feedback can lower the requested
rate. The minimum floor is disabled when Auto is selected so congestion can be relieved.
Detailed latency limiters and an optional minimum are under Video → Bitrate → Adaptive.
Resolution/refresh/codec changes require restarting SteamVR. Bitrate updates are live.

PyroWave receives ALVR's dynamic bitrate and sets its maximum frame size to
`floor(bitrate_bits_per_second / 8 / integer_refresh_hz)`. This is a per-frame cap,
not a promise to generate that many bytes. Auto estimates capacity from frame bytes
and latency and also uses encoder/decoder feedback. It updates approximately once
per second. It cannot fix an overloaded decoder merely by changing network bandwidth.
Use TCP for initial Auto testing: the experimental separate UDP timing estimate can
clamp to zero and skip adaptation samples. Auto is available but live stability still
needs measurement. The port corrects the decoder limiter's bytes/frame → bits/s units.

All current PyroWave profiles request 2064×2208 per eye, padded to **2080×2208**.
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
