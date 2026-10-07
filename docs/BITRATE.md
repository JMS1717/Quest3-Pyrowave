# Bitrate controls and profile budgets

Settings → Presets has a **5–4000 Mbps slider** (4000 since `.60`), an **Auto bitrate**
checkbox and the padded resolution/frame budget. In manual mode the slider sets the codec payload
rate cap. In Auto mode it sets an enabled maximum; feedback can lower the requested
rate. Selecting Auto disables Auto's own optional minimum (Video → Bitrate → Adaptive). Auto
starts from at least the PyroWave quality floor below, and its latency limiters can go under it.
Detailed latency limiters and that optional minimum are under Video → Bitrate → Adaptive.
For PyroWave the hardware decoder latency limiter is ignored: a target below fixed
GPU reconstruction time otherwise drives bitrate toward zero without reaching the
requested frame rate. Network/encoder feedback still applies. This policy does not
change hardware codecs; changing bitrate controls also clears an old learned decoder cap.
Resolution, refresh, codec and other PyroWave settings apply at stream start. While a client
streams, the server reconnects about 2 s after the last such edit and restarts SteamVR when the
driver configuration changed (`[Q3PW_SETTINGS_RECONNECT]` in the log;
[SETTINGS-APPLY.md](SETTINGS-APPLY.md)). Bitrate updates are live.

## Quality floor

With PyroWave the floor is **0.25 bits per padded stream pixel per frame** (both eyes, 4:2:0),
rounded up to 50 Mbps (`BitrateManager::set_quality_floor_mbps`,
`alvr_session::beta::quality_floor_mbps`).

- **Constant bitrate**, from the dashboard or the headset menu: a lower setting is raised to the
  floor.
- **Auto** (since `.63`, unchanged in `.64`): the throughput estimate is raised to the floor,
  so Auto does not idle at a blocky bitrate. The network and encoder latency limiters and a manual maximum are applied
  after it, so on a congested link Auto still lowers the bitrate under the floor. In `.62` the
  floor was applied after them, and a link that could not carry it dropped frames instead.
  Checked over Wi-Fi 6E on `.63`: with a 1500 Mbps maximum and the 8 ms network-latency limit,
  Auto settled at about 550 Mbps, below the 1000 Mbps the link carries cleanly
  ([WIRELESS.md](WIRELESS.md)).

The dashboard shows the floor under the per-frame budget ("Auto starts from at least this" in
Auto; `.64` corrected that text), and the driver logs
`[Q3PW_QUALITY_FLOOR]`. 4:4:4 doubles the raw samples and the floor; that factor is not measured,
so it errs high. `ALVR_PYROWAVE_NO_QUALITY_FLOOR=1` in the streamer's environment lifts the floor
for codec measurements.

| Stream per eye | Refresh | Floor |
|---|---:|---:|
| 2064×2208 (full panel) | 72 Hz | 200 Mbps |
| 2064×2208 | 120 Hz | 300 Mbps |
| 2064×2208 | 207 Hz | 500 Mbps |
| 2064×2208, 4:4:4 | 120 Hz | 600 Mbps |
| 1552×1664 (scaled panel) | 240 Hz | 350 Mbps |

How it was measured (October 7, `tools/downsample/csf_study.py`): 4 `quality_scene` crops at
3072×3216 were filtered to 2080×2208 and coded with the live Haar 4:2:0 encoder
([ENCODER-CSF.md](ENCODER-CSF.md)) at 207 Hz:

| Mbps | bits/pixel | PSNR-HVS-M | smooth-area Y | smooth-area Cb/Cr |
|---:|---:|---:|---:|---:|
| 150 | 0.08 | 11.56 | 49.58 | 42.96 |
| 250 | 0.13 | 12.48 | 52.03 | 46.15 |
| 350 | 0.18 | 13.18 | 52.91 | 48.69 |
| 450 | 0.24 | 13.72 | 53.59 | 50.21 |
| 600 | 0.32 | 14.28 | 54.85 | 52.94 |
| 800 | 0.42 | 14.90 | 55.77 | 54.48 |
| 1000 | 0.53 | 15.34 | 56.43 | 55.02 |
| previous encoder, 1000 | 0.53 | 14.65 | 53.87 | 46.10 |

Quality falls smoothly with no knee, so the floor is anchored to a configuration the owner had
judged: the previous encoder at 1000 Mbps / 207 Hz, which looked pixelated next to Virtual
Desktop. Smooth areas, where Haar blocking shows, drop below that at about 0.25 bits per pixel
(475 Mbps at 207 Hz). The quality scene is a stress board of gratings, text and noise. Game
content compresses better, but the floor scales with pixels per second, not content.

Checked in the headset (13ff836, 207 Hz, 2080×2208 per eye, 12 s pan blocks): with the slider at
300 Mbps the streamer logged `[Q3PW_QUALITY_FLOOR] 500 Mbps` and requested 500 Mbps from the
encoder for every frame, at 193.5 fresh FPS; with the slider at 1000 Mbps it requested 1000 Mbps
(the floor does not lower anything), at 191.1 fresh FPS.

PyroWave receives ALVR's dynamic bitrate and sets its maximum frame size to
`floor(bitrate_bits_per_second / 8 / round(refresh_hz))`, aligned down to four bytes.
The serialized complete frame is checked against that cap and rejected if oversized;
it is never truncated. Rounding avoids a floating-point 119.99999 Hz value being
treated as 119 Hz. This is a per-frame cap,
not a promise to generate that many bytes. Auto estimates capacity from frame bytes
and latency and also uses encoder/decoder feedback. It updates approximately once
per second. It cannot fix an overloaded decoder merely by changing network bandwidth.
Use TCP for initial Auto testing: the experimental separate UDP timing estimate can
clamp to zero and skip adaptation samples. Auto has only short screens: over Wi-Fi 6E on
`.63` it settled at about 550 Mbps ([WIRELESS.md](WIRELESS.md)); sustained behaviour is not
measured. An earlier USB test with an 8 ms decoder limiter collapsed the bitrate; ignoring
that limiter for PyroWave (above) addresses the mechanism.
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
| 1000 / 207 measured profile | 4.83 | 603,864 | 22.82:1 | 1053 |

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
Older full-resolution decode measurements exceeded 120 Hz's 8.33 ms, which is why the
120 Hz rows above are named experiments. The current decoder (`.62` and later) decodes
2080×2208 Haar in about 2.7 ms GPU time (p50, 207 Hz, 690 MHz GPU clock). The three
"(measured)" profiles run at 1000 Mbps: native 120 Hz, 207 Hz at 2080×2208 and 240 Hz scaled panel at 1440×1536
(520,832 bytes/frame). See [HIGH-REFRESH.md](HIGH-REFRESH.md). Those are 10-12 s screens, not
sustained play. Native panel size is also distinct from SteamVR's larger lens-corrected render
recommendation.

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

Dated record from `.44`. It predates the faster decoder: Haar GPU decode at 2080×2208 now
measures about 2.7 ms p50 (207 Hz, 690 MHz GPU clock, `.62`), not 6 ms. "Default" below means the native 120 Hz profile's 1000 Mbps; a fresh
install still starts on the 400 Mbps / 72 Hz candidate preset.

The reviewed `.44` pair passed all matching builds and production decoder tests;
its native libraries are byte-identical to GPU-verified `.42`. One continuous
Quest session used 2080×2208/eye, confirmed 120 Hz, Haar/Compute 4:2:0, no foveation,
LOW decode priority, synchronous direct eye copy, one TCP worker and the existing
4 ms selection wait. Stage diagnostics and event waiting were off. Bitrate changed
live through 1000/800/600/800/1000, with 3 seconds settling and 12-second windows.
No client restart or screenshot occurred between windows.

Source coverage, PID/clock alignment, runtime 120 and unchanged codec/geometry
were verified. Every recorded encoder directive matched its target, and **every
captured serialized frame fit the aligned per-frame byte cap**. These settings
fit the payload math; that does not establish their 120 Hz timing or visual budget.

| Target Mbps | Median ALVR payload-rate estimate Mbps | Frame cap / largest frame bytes | Eye completions/s | GPU decode p50/p95 ms | Decode-to-fence p50/p95 ms | Client FPS p1 |
| --- | --- | --- | --- | --- | --- | --- |
| 1000 | 1006.1 | 1,041,664 / 1,041,652 | 118.08 | 5.90 / 6.73 | 7.95 / 8.29 | 60.0 |
| 800 | 806.6 | 833,332 / 833,308 | 118.39 | 6.02 / 6.93 | 8.05 / 8.42 | 60.0 |
| 600 | 606.0 | 625,000 / 624,992 | 118.66 | 6.68 / 6.91 | 8.10 / 8.37 | 60.0 |
| 800 | 806.8 | 833,332 / 833,308 | 117.49 | 5.71 / 6.62 | 7.83 / 8.16 | 60.0 |
| 1000 | 1008.0 | 1,041,664 / 1,041,652 | 118.10 | 6.05 / 6.73 | 7.98 / 8.30 | 60.0 |

**Keep 1000 Mbps as the default.** The controls agree near 118.1 eye completions/s;
800 varied 118.4→117.5, while the single 600 block reached 118.7. P1 stays near 60;
none establishes sustained fresh 120. Neither GPU nor completion time decreased
consistently with payload. GPU endpoints shifted 599/640 MHz, so DVFS and timing
remain confounds. Extra link capacity or lower rate alone has not removed the
remaining completion/presentation bottleneck. Avoid an unchanged rate sweep.

Temperatures stayed 31–33°C, thermal status 0, AC powered. All temporary settings,
proximity and driver registrations restored without errors, preserving VD. A
single final 1000 compositor image retained correct eye mapping/orientation;
lower-bitrate and in-headset quality acceptance were not tested. ALVR total/network
latency estimates with a stationary headset are not optical motion-to-photon.
[Sanitized metrics, exact byte budgets and package provenance](../results/PAYLOAD-NATIVE120-2026-10-05.json).

## Owner settings at 207 Hz: where bitrate stops paying, October 7

Live, Quest 3 over USB (ADB forward), 3072x3216 render into 2080x2208 per eye, 207 Hz, Haar 4:2:0
with mode 5 decode, quality scene panning at 60 deg/s. Cells ran 1000, 2000, 1500, 2000 and
1000 Mbps, two 12 s blocks each.

| Mbit/s | frame cap | fresh FPS (blocks) | lost/s | GPU decode p50 | network p50 / p95 (ALVR) | ALVR latency estimate p50 |
|---|---|---|---|---|---|---|
| 1000 | 604 KB | 196.3, 197.8, 198.0, 193.6 | 9-14 | 2.78 ms | 3.2 / 4.7 ms | 32.1 ms |
| 1500 | 906 KB | 193.8, 190.6 | 15-25 | 2.82 ms | 4.6 / 7.9 ms | 35.7 ms |
| 2000 | 1208 KB | 170.1, 168.6, 167.4, 169.8 | 40-44 | 3.00 ms | 7.0 / 12.2 ms | 39.9 ms |

Decode barely changes with bitrate. The loss at 2000 Mbps is the link: a frame takes longer to
arrive than the 4.83 ms frame period. Offline, Haar at 2000 Mbit/s scores +4.9 dB PSNR-HVS-M over
1000 ([DECODER-V2.md](DECODER-V2.md)), so the transport now limits image quality at this rate.

The USB link itself carries 3.5-3.65 Gbit/s of continuous data through `adb forward`, but frames
arrive in bursts. A device-side receiver that acknowledges each burst, paced at 207 Hz with 65 KB
writes like ALVR's shards, measured send-to-acknowledge (p50 / p95):

| burst | 1 connection | 2 connections | 4 connections |
|---|---|---|---|
| 604 KB (1000 Mbps) | 2.81 / 4.22 ms | 2.34 / 3.68 ms | 2.53 / 3.70 ms |
| 1208 KB (2000 Mbps) | 4.74 / 6.16 ms | 4.00 / 5.54 ms | 4.36 / 5.98 ms |

So bursts move at about 1.7-2.4 Gbit/s, half the continuous rate. Splitting a frame over two
forwarded connections saves about 0.5 ms; more do not help. For now 1000-1500 Mbps is the
useful range at 207 Hz. Above it, the options are a transport that bypasses ADB (a USB network
function such as NCM, not yet tried because switching USB functions can drop ADB until someone
replugs the headset) or fewer bytes per frame for the same quality (a better wavelet).

### CDF 5/3 at the owner's 690 MHz GPU clock, October 7

Same scene and stream, CDF 5/3 with V2 mode 5, maximum GPU clock on (690 MHz in every VrApi line),
one streamer session per cell in the order 700, 1000, 1500, 1500, 1000, 700 Mbps, 12 s each:

| Mbit/s | frame cap | fresh FPS | lost/s | GPU decode p50 | fence p50 | offline PSNR-HVS-M (60 deg/s pan) |
|---|---|---|---|---|---|---|
| 700 | 423 KB | 191.2 / 178.9 (mean 185.1) | 16.6 / 28.5 | 2.96 / 3.12 ms | 4.84 / 5.00 ms | 19.0 |
| 1000 | 604 KB | 177.8 / 180.0 (mean 178.9) | 30.1 / 28.2 | 3.13 / 3.14 | 4.94 / 4.96 | 20.8 |
| 1500 | 906 KB | 169.6 / 158.9 (mean 164.2) | 38.9 / 49.0 | 3.50 / 4.01 | 5.10 / 5.67 | not scored |

- Unlike Haar, 5/3 decode grows with bitrate (2.96 to 3.50-4.01 ms): V2 runs at 98 % GPU busy, so
  more coefficients to dequantize cost frames directly. 1000 Mbit/s costs about 6 fresh FPS against
  700, 1500 about 21.
- The two 700 cells differ by 12 FPS. The memory clock moves between 2092, 2736 and 3196 MHz from
  cell to cell (VrApi `Mem=`) and is not pinned by the GPU level; at this load it is the largest
  noise source left.
- At 700 Mbit/s a frame carries about 0.35 bit per sample; the stream is bitrate-starved by any
  inter-frame codec's standard, and each step up is visible offline (+1.8 dB to 1000). For the
  owner's 207 Hz target, CDF 5/3 at 700-1000 Mbit/s is the useful range; the trade is the user's.
- A first attempt switched the bitrate inside one streamer session, with the harness relaunching
  the client for each block. With the maximum GPU clock on, every relaunch makes the server release
  GPU level 7 (it does so whenever the client is not running, so other VR apps never inherit it),
  re-apply it when the client starts, and restart the client once more. Two of four such
  back-to-back restarts came back at 72 or 90 Hz, so that run is discarded and this sweep uses one
  session per bitrate. All 11 fresh-session cells that day started at 207 Hz; a client reopened
  seconds after closing it was not checked with the owner's settings.


### Parallel wired video connections, October 7

Over USB, adb forwards TCP only, and one forwarded connection moves a burst at about 1.7-2.4
Gbit/s. A 906 KB frame (1500 Mbit/s at 207 Hz) then takes about 4 ms of a 4.83 ms frame interval
to cross. `video.pyrowave.wired_video_connections` (default 2 since e325386; 0 restores the old path) splits each
complete frame into 1 to 4 contiguous slices and writes them in parallel on dedicated
adb-forwarded connections (ports 9950-9953). The client puts the frame back together and decodes
it as if it had come on ALVR's video stream. If the client does not answer on every port, video
stays on the stream socket.

The setting applies only to a client on ALVR's wired (USB adb) connection. Over Wi-Fi or a manual
network address, video always uses ALVR's stream socket, whatever the setting says. From `.63`,
wired mode uses only an online adb device attached over USB and otherwise falls through to manual
IPs and discovery.

Current behaviour (`.64`, on main, not yet released):

- Each slice carries a 48-byte header (magic `PWT1`) with a per-frame sequence number in bytes
  28..32. The client assembles frames by that number and rejects overlapping or mismatched
  slices. This replaces `.63`'s skip of frames whose timestamp repeats the previous one.
- An older server sends sequence 0, and the client falls back to timestamps.
- Each writer has a 1 s write timeout. A stalled connection counts as lost, and video falls back
  to the stream socket.
- `.64` was checked over Wi-Fi only. The USB parallel path with numbered slices is not yet
  hardware-tested.

ABBA at the owner's settings: CDF 5/3 V2 mode 5, 690 MHz, 207 Hz, 2080x2208 per eye from
3072x3216, 60 deg/s pan, 12 s, one streamer session per cell. "Network" and "pipeline" are
medians of ALVR's own per-frame estimates over the last two thirds of each session; they are
not motion-to-photon.

| Mbit/s | connections | fresh FPS | GPU busy | network | pipeline estimate |
|---|---|---|---|---|---|
| 1000 | stream socket | 184.2 / 181.2 (mean 182.7) | 98 / 97 % | 3.76 / 2.98 ms | 33.7 / 32.9 ms |
| 1000 | 2 | 183.7 / 180.5 (mean 182.1) | 98 / 97 % | 2.64 / 2.64 ms | 33.3 / 32.5 ms |
| 1500 | stream socket | 171.4 / 157.6 (mean 164.5) | 98 / 98 % | 4.77 / 6.42 ms | 35.4 / 40.1 ms |
| 1500 | 2 | 170.2 / 168.8 (mean 169.5) | 97.5 / 97 % | 3.56 / 4.02 ms | 34.9 / 35.4 ms |

- Two connections cut the network stage by about 0.7 ms at 1000 Mbit/s and 1.8 ms at 1500. On
  one connection at 1500, frames take longer than a frame interval to arrive and queue behind
  each other (6.4 ms in one cell).
- Fresh FPS does not change at 1000 Mbit/s. At 1500 the 5 FPS gain is within the cell-to-cell
  noise. The headset GPU is 97-98 % busy in every cell, so decode, not the link, caps the frame
  rate. A faster link shortens latency but cannot add frames.
- The 1000 Mbit/s probe cell before the ABBA (two connections) gave 180.6 FPS and 3.3 ms network.
- Default check (e325386, the owner's session without the key): two connections engaged, 178.1
  fresh FPS and 2.71 ms network against 177.4 and 3.04 ms with the setting at 0 (1000 Mbit/s).
- Not yet measured: unplugging the cable mid-stream; 3 or 4 connections; Haar, whose decode does not grow with bitrate and so may
  turn the shorter arrival into frames; sustained play.
