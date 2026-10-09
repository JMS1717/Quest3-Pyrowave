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

Each [streaming profile](PROFILES.md) has its own frame budget. ALVR pads each per-eye size up to
a multiple of 32: the full panel's 2064×2208 becomes **2080×2208**, the 110 % stream's 2270×2429
becomes 2272×2432, and the full 3072×3216 render becomes 3072×3232. Raw sizes below are 8-bit
stereo frames; 4:4:4 doubles them, and a fixed bitrate cap stays the same.

| Profile | Padded per eye | Mbps / Hz | Time budget ms | Payload bytes/frame ≤ | Raw/payload ratio | TCP Ethernet rate at cap ≥ Mbps | Bits per stream pixel |
|---|---|---|---:|---:|---:|---:|---:|
| Starter 72 Hz | 2080×2208 | 400 / 72 | 13.89 | 694,444 | 19.84:1 | 421 | 0.60 |
| Wi-Fi 90 Hz | 2080×2208 | 700 / 90 | 11.11 | 972,222 | 14.17:1 | 737 | 0.85 |
| Wi-Fi Quality 120 Hz | 2272×2432 | 1000 / 120 | 8.33 | 1,041,666 | 15.91:1 | 1053 | 0.75 |
| Quality 120 Hz | 2272×2432 | 1500 / 120 | 8.33 | 1,562,500 | 10.61:1 | 1580 | 1.13 |
| Colour 4:4:4 120 Hz | 2272×2432 | 1500 / 120 | 8.33 | 1,562,500 | 21.22:1 | 1580 | 1.13 |
| Godlike 90 Hz | 3072×3232 | 1500 / 90 | 11.11 | 2,083,333 | 14.30:1 | 1580 | 0.84 |
| Godlike 120 Hz | 3072×3232 | 1500 / 120 | 8.33 | 1,562,500 | 19.06:1 | 1580 | 0.63 |
| Competitive 207 Hz | 2080×2208 | 1000 / 207 | 4.83 | 603,864 | 22.82:1 | 1053 | 0.53 |

Bits per stream pixel is the clearest single comparison of how hard each profile compresses:
Quality 120 gives every pixel nearly twice the bits of Godlike 120, which is why Godlike 90 (more
time per frame) is the cleaner full-size mode. The 600–2000 Mbps 120 Hz experiments and the 75 % /
60 % render-size latency experiments that earlier versions listed were removed in `.131`; the
product profiles above replace them. Their short live observations remain in
[results](../results/LIVE-2026-10-01.md).

Ethernet estimates assume full-size TCP segments (1460 bytes payload / 1538 bytes on
the wire). They exclude ACKs, retransmissions and Wi-Fi airtime overhead. A 2.4 Gbps
Wi-Fi PHY rate is not 2.4 Gbps payload capacity. Passing these mathematical bounds does not
establish visual quality, thermal stability, or sustained frame rate. The three "(measured)"
reference profiles run at 1000 Mbps: native 120 Hz, 207 Hz at 2080×2208 and 240 Hz scaled panel
at 1440×1536 (520,832 bytes/frame). See [HIGH-REFRESH.md](HIGH-REFRESH.md). Native panel size is
also distinct from SteamVR's larger lens-corrected render recommendation.

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
install starts on the 400 Mbps / 72 Hz Starter profile.

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

### Four wired connections (`.69`, default from `.70`), October 7

Wired, CDF 5/3, ABBA, 2 against 4 video connections:

| Setting | Connections | Fresh FPS | Network p50 | Network p99 |
| --- | --- | --- | --- | --- |
| 207 Hz, 2080x2208, 1000 Mbps | 2 | 186.7, 190.2 | 2.7, 2.7 ms | 7.9, 7.8 ms |
| 207 Hz, 2080x2208, 1000 Mbps | 4 | 190.6, 186.4 | 2.8, 2.7 ms | 5.2, 5.1 ms |
| 120 Hz, 2592x2784, 2000 Mbps | 2 | 110.2, 112.9 | 7.6, 7.4 ms | 18.2, 16.7 ms |
| 120 Hz, 2592x2784, 2000 Mbps | 4 | 111.0, 110.8 | 7.0, 6.9 ms | 14.8, 14.9 ms |

- With four connections the frame rate is the same, and the network tail is 2.6–2.7 ms shorter.
  `.70` makes four the default.
- At 2000 Mbps a 120 Hz frame is about 2.1 MB. It takes about 7 ms over the USB link, which
  carries 2.3–2.6 Gbps in bursts ([TRANSPORT.md](TRANSPORT.md#usb)). That is most of the
  8.3 ms frame, and fresh FPS falls from about 116 at 1500 Mbps to about 111.
- So 2000 Mbps at 120 Hz is near the cable's limit whatever the connection count. Above it,
  more bits per frame have to come from coding efficiency, not bitrate.

### Frame budget from the present rate (`.66`, `.67`), October 7

Before `.66`, constant bitrate was divided by the refresh rate. A game presenting 150–180 fps on
a 207 Hz panel therefore got `bitrate / 207` per frame and used only 77–87% of the bitrate.

`.66` turns **Adapt to framerate** on by default. It follows an exponential average of the
present interval:

- rising rates apply at once
- falling rates apply at the 1 s update
- the result is bounded

Wired, CDF 5/3, 207 Hz, 1000 Mbps, fresh FPS, ABBA (adapt off is A, on is B):

| Scene | A | B | B | A |
| --- | --- | --- | --- | --- |
| Harness scene | 188.3 | 190.2 | 191.6 | 193.0 |
| Scene held to a 6 ms frame | 192.6 | 189.5 | 191.7 | 192.7 |
| Scene held to a 6 ms frame (repeat) | 193.9 | 188.3 | 189.4 | 190.5 |

Adapt made no difference, because the present rate never dropped. `.67` counts what the driver
receives (`[Q3PW_PRESENT]`, one line per second):

- With the scene held to 6 ms or made GPU-bound by overdraw, SteamVR still presented 192–208
  frames per second.
- Each present had new compositor textures, and at most 2 per second repeated a pose.
- SteamVR's compositor reprojects the game's last frame into a new present at every vsync, so
  the driver's present rate is the panel rate, not the game's.

So the frame budget only helps when SteamVR itself presents below refresh. Giving a slower game
the full bitrate needs the game's own frame rate. One way is the compositor's frame timing
(reprojection flags). Another is to stream at the game rate and let the headset reproject.
This is in [PLAN.md](PLAN.md) 2.1.

Adapt stays on: it is neutral here and right when presents do drop.

### Streaming only the game's frames (`.79`–`.83`), October 8

The driver sees one present per vsync, so the game's own frame rate has to come from SteamVR's
frame timing (`IVRServerDriverHost::GetFrameTimings`, `Compositor_FrameTiming`).

**What the timing contains.** Every entry is a compositor frame, not a game frame. With the
scene held to 8 ms (about 120 fps) on a 207 Hz panel, every entry had:

- a frame index one higher than the previous entry
- `m_nNumFramePresents` = 1
- reprojection flags 0
- no mispresented or dropped frames

The driver's view (`.80`, `[Q3PW_FRAMETIMING]`) and the scene's own view
(`IVRCompositor::GetFrameTimings`, logged to `timing.csv` by `tools/quest3/quality_scene.py`)
were the same. So "same frame index as the last present" (`.79`) never fires.

**The signal.** `m_flClientFrameIntervalMs` is non-zero only in compositor frames that received
a new game frame, and 0 in frames where SteamVR re-showed the last one. In the scene's log:

- 116 non-zero entries per second out of 200
- 119–122 per second in each 1 s window, against the scene's 120–122
- the non-zero values were 8.1–14 ms

In the driver's 128-entry history, 74–76 non-zero entries per 0.62 s means 121 fps.

**`.81`–`.83`:**

- At each present, the driver checks the timing entries newer than the last present. If none
  has a non-zero client interval, the present re-shows the game's previous frame.
- With **Stream only the game's frames** on, that present is neither encoded nor reported to
  the bitrate manager. The headset re-shows the previous frame, with its own rotation
  correction.
- **Adapt to framerate** then spreads the bitrate over the game's rate. It is bounded at 2x
  the nominal frame size by `framerate_reset_threshold_multiplier`.
- Safeguards:
  - every frame is streamed while no game frame has arrived for 100 ms (SteamVR's own scene, a
    loading or stalled game);
  - at most 3 presents in a row are skipped.
- `.81` without the first safeguard skipped every frame before the scene started, and the
  stream never came up.

Wired, CDF 5/3, 2080x2208, 207 Hz, 1000 Mbit/s, adapt on, pan 60 deg/s, scene held to 8 ms
(about 121 fps). Median of the statistics events after warm-up:

| | Off (`.81`) | On (`.82`) |
| --- | --- | --- |
| Fresh frames/s, A / B | 189.3 / 189.0 | 119.8 / 120.5 |
| Game frames/s (scene) | 120–122 | 120–123 |
| Frames streamed/s | 208 | 122 |
| Bytes per frame | 604 KB | 1025 KB (1.70x) |
| Link | 1000 Mbit/s | 993 Mbit/s |
| Headset GPU decode p50 | 3.15 ms | 3.42 / 3.81 ms |
| ALVR latency estimate | 34.8 ms | 32.9 ms |
| Vsync queue | 10.8 ms | 15.8 ms |

- **Detection:**
  - with skipping on, new = scene frames in every 1 s window (121/121, 121/122, 123/123), with
    85–87 skips per second;
  - with skipping off, 31–42 presents per second found no new timing entry yet and were counted
    as new (streamed), so detection errs towards streaming.
- **Game at full rate**, on (`.82`, block A): 0–1 skips per second and 206–208 frames
  streamed. It is unaffected.
- **Not measured yet:**
  - how 120 frames on a 207 Hz panel feel in the headset (judder, head-rotation smoothness)
    against SteamVR's reprojection;
  - real games and Wi-Fi;
  - with SteamVR Motion Smoothing, whose synthesized frames are not streamed;
  - the image quality gain from 1.7x the bytes per frame in the headset.
- **Image quality, offline:** `tools/downsample/clarity_budget.py`, one run, 2080x2208,
  CDF 5/3, PSNR-HVS-M at 25 px/deg, bilinear display. Before coding the score is 20.93 dB. A
  121 fps game streamed alone gets the per-frame budget of 120 Hz / 1000:

  | Per-frame budget | Bytes per eye | HVS-M | Coding loss | ΔE |
  | --- | --- | --- | --- | --- |
  | 207 Hz / 1000 (every compositor frame) | 302 KB | 19.11 | −1.82 dB | 6.26 |
  | 120 Hz / 1000 (the game's frames only) | 521 KB | 20.29 | −0.64 dB | 5.24 |
  | 120 Hz / 1500 (reference) | 781 KB | 20.70 | −0.23 dB | 4.67 |

  - This recovers about two thirds of the quantization loss.
  - The absolute levels differ from the October 7 table in
    [CLARITY-BUDGET.md](CLARITY-BUDGET.md); compare within this run only.
- **Static scene in the headset** (`.83`, pan 0, the same 8 ms scene): the screenshot's mean
  absolute Laplacian was 6.77 / 6.77 off and 7.25 / 6.95 on. Fresh frames: 187 / 184 off;
  120 / 151 on (the scene ran faster in the second block). This is a small, consistent rise; the
  metric is coarse.
- **Setting:** `video.pyrowave.game_frames_only`, off by default, restart SteamVR.
  `ALVR_Q3PW_GAME_FRAMES_ONLY=0/1` overrides it for A/B runs. The harness cannot set the
  setting itself: it runs SteamVR without the dashboard, so the derived `openvr_config` key
  changes only after a restart that never happens. Harness runs use the environment variable.

Already tried:

| Idea | Result |
| --- | --- |
| Repeat = latest timing entry has the last present's frame index (`.79`) | Never repeats: one entry per compositor frame |
| `m_nNumFramePresents`, `m_nReprojectionFlags`, mispresented/dropped counts | Always 1 / 0 / 0 / 0 for a slow game, from driver and app side |
| Same compositor textures or same pose across presents (`.67`) | Each present has new textures and a new pose |
| Skip repeats with no fallback (`.81`) | No stream before the game starts |
