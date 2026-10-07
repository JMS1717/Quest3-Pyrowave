# Wi-Fi streaming

October 7, 2026: the first measured PyroWave runs over Wi-Fi, on the `.63` build (released as
v0.1.0-alpha.9). `.64`, now on main and not yet released, was checked over the same link
([below](#64-check)).

## Setup

- **Headset link:** Quest 3 on Wi-Fi 6E at 6 GHz (6295 MHz channel), 2401 Mbit/s PHY rate, RSSI -45 dBm.
- **PC link:** 2.5 GbE Ethernet to the same router.
- **Stream settings:**
  - 207 Hz panel, 2080x2208 per eye encoded from a 3072x3216 render.
  - Haar, 4:2:0, TCP, no foveation, mode 5 (packed YCbCr).
- **GPU clock:** level 7 (690 MHz) set through `debug.oculus.gpuLevel`. The server's own GPU-clock
  and panel helper works only over USB adb.
- **Motion:** a 60°/s pan.
- **Windows:**
  - Bitrates switched live within one stream, in ABBA order.
  - 4 s settle, then a 10 s measurement.
- **Connection:**
  - The server connected to the headset's network entry (manual IP), and the stream came from
    that address.
  - USB adb was offline during these runs. That also exercised the `.63` fallback from a wired
    entry that isn't ready to the network.

**Fresh FPS** is the client's `Q3PW_FRESH` taken count: distinct decoded frames shown. **Network**
is ALVR's network-stage estimate (`network_s`). Neither is motion-to-photon.

## Results

### 700 vs 1000 Mbit/s

The memory clock changed inside two of the four blocks:

| Bitrate | Fresh FPS by block | Network p50 / p90 / p99 |
|---|---|---|
| 700 Mbit/s | 192.8\*, 193.0 | 5.1 / 6.5 / 9-14 ms |
| 1000 Mbit/s | 189.0, 195.1\* | 6.1 / 7.6 / 10-27 ms |

\* The memory clock changed during the block.

### 1000 vs 1250 Mbit/s

All four blocks were valid, at a 2736 MHz memory clock:

| Bitrate | Fresh FPS by block | Network p50 / p90 / p99 |
|---|---|---|
| 1000 Mbit/s | 192.5, 194.9 | 6.1 / 7.6 / 10-13 ms |
| 1250 Mbit/s | 190.1, 192.5 | 7.0-7.2 / 9.7 / 21-49 ms |

### 1000 vs 1500 Mbit/s

| Bitrate | Result |
|---|---|
| 1000 Mbit/s | Same as above: about 6 ms network p50. |
| 1500 Mbit/s | **Unusable.** The first block's network p90 was 19.7 ms and p99 115 ms. In the second block a queue had built up and frames arrived **about 380 ms late**, while the headset still showed about 193 distinct, stale frames per second. Back at 1000 Mbit/s it recovered at once. |

### Constant 1000 Mbit/s vs Auto

Auto here used a 1500 Mbit/s maximum and the 8 ms network-latency limit.

| Mode | Settled bitrate | Fresh FPS by block | Network p50 / p99 | ALVR total estimate |
|---|---|---|---|---|
| Constant 1000 | 1000 Mbit/s | 193.0\*, 187.6 | 6.0 / 10-40 ms | 30-33 ms |
| Auto | 540-555 Mbit/s | 200.3, 197.3\* | 4.4 / 8-13 ms | 28 ms |

\* The memory clock changed during the block.

The Auto blocks ran partly at a lower memory clock (2092 against 2736 MHz), so their extra fresh
frames are not a clean comparison.

### `.64` check

A local build of `.64` (commit `9145530`) on the same link and settings: 207 Hz, 2080x2208 per
eye, Haar, 1000 Mbit/s.

| Build | Fresh FPS by block | Network p50 |
|---|---|---|
| `.64` | 193.9\*, 190.6 | 6.2 ms |

\* The memory clock changed during the block.

Colours were correct in both eyes. This matches the alpha.9 build, which gave 190.0 and 196.5
fresh FPS ([HANDOFF.md](HANDOFF.md)), and the `.63` blocks above. USB adb was offline again, so
the USB path that `.64` changes (numbered wired video slices) is not hardware-tested.

## What this means

- **Fresh frames:** Wi-Fi 6E at 6 GHz carries 207 Hz at full resolution with about the same fresh
  FPS as USB (189-195 against 194-197).
- **The cost is latency:** at 1000 Mbit/s ALVR's network stage is about 6.1 ms. Over USB it is
  about 2.7 ms with two wired video connections, or 3.0 ms with one.
- **The link's limit:** the usable ceiling on this link is between 1250 and 1500 Mbit/s. A PHY rate
  of 2401 Mbit/s does not mean 2400 Mbit/s of video.
- **No back-pressure above the limit:** a constant bitrate above what the link delivers is not
  throttled. TCP queues the excess and latency grows without limit.
- **Auto is the safe choice, but conservative:**
  - Since the `.63` fix (also in `.64`), the quality floor raises Auto's estimate, but the
    network-latency limit and the maximum still win.
  - On this link Auto settled at about 550 Mbit/s, about half of what the link carries cleanly.
  - ALVR's estimator divides one frame's bytes by its network time, so it stays conservative.

### `.65`: PyroWave video over UDP

`.65` adds **PyroWave → Transport → UDP** for Wi-Fi ([TRANSPORT.md](TRANSPORT.md#live-results-65)).
On the same link and settings (Haar, 207 Hz, 2080x2208):

| Bitrate | TCP fresh FPS | UDP fresh FPS | ALVR estimate, TCP / UDP |
|---|---|---|---|
| 1000 Mbit/s | 187.6 | 189.7 | 31.9 / 33.0 ms |
| 1250 Mbit/s (ABBA) | 171.0, 169.2 | 177.3, 180.7 | 34.9-36.4 / 33.9-34.9 ms |
| 1500 Mbit/s | 110.1 | 110.3 | 112.4 / 42.5 ms |

At 1250 Mbit/s and above, the video arrives complete, but the headset's tracking packets reach the
PC late. The server then sends several frames with the same pose, and the client counts them as
one fresh frame. That happens with either transport, and these `.65` runs at 1250 Mbit/s were
below the 190-192 measured on `.63`.

**Recommendation for Wi-Fi:**

- Transport **UDP** (opt-in in `.65`, the default from `.68`): never worse than TCP here, and above
  the link's limit it skips frames instead of queueing them.
- From `.68` the PC predicts head poses when tracking arrives late. At 120 Hz and 1250 Mbit/s this
  cut repeated poses from 2.25% to 0.45% ([TRANSPORT.md](TRANSPORT.md#late-tracking-on-wi-fi-67-68)).
- Constant **1000 Mbit/s** for quality, **1250 at most**.
- Auto (with a maximum and the latency limit) when latency matters more than bitrate.
- Keep the PC on Ethernet and the headset on a nearby 6 GHz access point.

## Not yet established

These results don't cover:

- other routers, 5 GHz links or a busy network
- sustained play
- optical latency
- tuning Auto's saturation multiplier towards the link's real capacity
- whether parallel connections would shorten Wi-Fi network time, as they do over USB

## Parallel connections are USB only

**Wired video connections** (`video.pyrowave.wired_video_connections`, default 2) apply only to a
client on ALVR's wired connection through USB adb. The server opens them only for that connection,
and the client listens only when the negotiated stream is wired. Over Wi-Fi, video uses ALVR's
single stream socket, or with Transport set to UDP, PyroWave's own UDP datagrams. See
[BITRATE.md](BITRATE.md#parallel-wired-video-connections-october-7).
