# Video transport: adb, USB networking, Wi-Fi TCP and UDP

October 7, 2026. These measurements decide how PyroWave video should travel over USB and Wi-Fi.
They come from a frame-burst benchmark, not from streaming. The PC sends one frame-sized burst
every 1/207 s, and the headset acknowledges each frame after its last byte arrives. Latency runs
from the frame's scheduled start to that acknowledgement, so a link that falls behind shows
growing latency, as a stream would. Each point is an 8 s run of 1656 frames at 207 Hz on a
Quest 3 (Android 14), with the PC on 2.5 GbE. Frame sizes match the bitrates: 604 KB is
1000 Mbps, 906 KB is 1500, 1208 KB is 2000 and 1509 KB is 2500.

At 207 Hz a frame period is 4.83 ms. Over USB, a frame that takes longer than that delays the
next one; this is why fresh FPS falls at high bitrates.

## Summary

- **USB: adb is not the bottleneck.** USB networking (NCM) and adb forwarding reach the same
  ceiling of about 2.3–2.6 Gbps per burst. NCM had steadier medians but higher base latency and
  worse p99. Four adb connections did as well as NCM or better. The limit is in the headset, not
  in adb's protocol, so the wired transport stays on adb.
- **Wi-Fi: TCP is the bottleneck in the burst test.** At 1000 Mbps TCP and UDP perform the same.
  Above that, TCP queues frames for hundreds of milliseconds to seconds, while UDP still delivers
  almost every frame on time: 1250 Mbps at about 10 ms, 1500 Mbps at about 12–17 ms.
- **Live, UDP delivers every frame, but tracking becomes the limit** ([live results](#live-results-65)).
  At 1500 Mbps UDP kept ALVR's latency estimate at 42 ms where TCP queued to 112 ms. Fresh FPS
  was about 115 with either transport, because the headset's tracking packets reach the PC late
  on a link this busy.
- **No IP fragmentation.** On the measured Wi-Fi path every fragmented datagram was lost (8, 32
  and 60 KB: 0 of 1656 frames arrived). Each datagram must fit a 1500-byte MTU. ALVR's own UDP
  stream (65000-byte packets) cannot work on such a network.

## USB

| Bitrate | adb, 1 conn. | adb, 2 conn. | adb, 4 conn. | NCM, 1 conn. | NCM, 2 conn. | NCM, 4 conn. |
| --- | --- | --- | --- | --- | --- | --- |
| 1000 Mbps | 2.9 / 26.5 | 2.6 / 5.1 | | 3.5 / 4.3 | 3.5 / 4.5 | |
| 1500 Mbps | 4.0 / 37.4 | 3.5 / 7.4 | | 3.7 / 11.2 | 3.8 / 14.9 | |
| 2000 Mbps | 5.4 / 44.5 | 4.3–4.6 / 9.6–11.0 | 4.0 / 7.3–9.7 | 4.1 / 46.0 | 4.3 / 26.7 | 4.3 / 27.1 |
| 2500 Mbps | 6.7 / 52.3 | 6.6 / 27.0 | 4.7 / 8.2 | 58.9 / 97.2 (falls behind) | 5.2 / 45.4 | 5.1 / 43.8 |

Each cell gives p50 / p99 burst latency in ms; empty cells were not measured. Frames later than
one period at 2000 Mbps: adb with 2 connections 265–667 of 1656, with 4 connections 82–138;
NCM with 2 connections 62, with 4 connections 71. Run-to-run variation is large; the 2000 Mbps adb
runs were repeated.

UDP over NCM lost nothing up to 2500 Mbps, but its latency was higher than TCP's (6.5 ms p50 at
2000 Mbps). The PC makes one system call per 1472-byte datagram, and that is too slow for this
rate.

### What USB networking takes on Quest 3

NCM is the only USB network function the gadget HAL accepts; RNDIS fails ("Usb Gadget
setcurrent functions failed"). With `svc usb setFunctions ncm`, Windows binds its inbox UsbNcm
driver and reports a 3.8 Gbps link. Meta's Ethernet service also claims `usb0` (its interface
pattern is `(eth\d)|(usb\d)`), which conflicts with USB tethering. A working, if fragile,
recipe from the adb shell:

1. `cmd ethernet set-ip-configuration usb0 static <ip>/16 --gateway <pc-ip> --dns <pc-ip>`.
   The PC has only a 169.254 link-local address, and without a gateway and DNS server Android
   never registers the network.
2. `svc usb setFunctions ncm`.
3. If `usb0` stays down, turn Ethernet off and on through `IEthernetManager.setEthernetEnabled`.
   The shell has NETWORK_SETTINGS, so `app_process` can call it.
4. The client must bind its sockets to the Ethernet network (`android_setsocknetwork`), because
   Wi-Fi stays the default network.

Since NCM gives no throughput gain, the product does not use it.

## Wi-Fi 6E (6 GHz, 2401 Mbps PHY, RSSI −44 dBm)

Two TCP connections against UDP datagrams of 1472 bytes. Ranges cover every run, including an ABBA
repeat at 1000 and 1250 Mbps:

| Bitrate | TCP p50 / p99 | UDP p50 / p99 | UDP frames incomplete |
| --- | --- | --- | --- |
| 1000 Mbps | 8.0–8.2 / 103–117 ms | 7.8–8.9 / 40–103 ms | 0.2–1.1% |
| 1250 Mbps | 108–350 ms, growing | 9.8–11.0 / 59–93 ms | 0–1.1% |
| 1500 Mbps | 1.0–2.5 s, growing | 12–17 / 91–134 ms | 0–2.7% |
| 2000 Mbps | – | Wi-Fi delivers about 1.4 Gbps; almost no frame completes | – |

One TCP connection is worse than two: at 1000 Mbps it reached a p50 of 32 ms once. The headset
must be awake for these numbers; with the proximity hold expired, every Wi-Fi result was worse.

## Design

- **USB:** keep adb forwarding and the parallel wired connections.
- **Wi-Fi:** PyroWave video over UDP sends the wired path's slices, one per datagram of at most
  1472 bytes (`PYROWAVE_UDP_DATAGRAM`). The client assembles them with the same code as the wired
  connections and hands complete frames to the same decoder. Nothing is retransmitted; a frame
  missing a datagram is skipped, and the next complete frame replaces it. Select it with
  **PyroWave → Transport → UDP**. Live results are [below](#live-results-65).
- **Later:** forward error correction could recover frames that lose a datagram, if a link turns
  out to lose them; this one did not. Windows UDP segmentation offload could cut the server's send
  cost.

## Live results (`.65`)

The settings were:

- `.65` local builds `c909f7f`, `bd67b5a` and `2a168d8` (the later two add only diagnostics).
- 207 Hz, 2080x2208 per eye from a 3072x3216 render, Haar, no foveation.
- Wi-Fi 6E only: the wired entry was removed from the test session while USB adb stayed attached
  for the harness.
- Two 10 s blocks per cell after the settle, with a 60 deg/s pan in the PC scene.
- The first block of every cell ran at GPU level 4 (640 MHz) and the second at level 7 (690 MHz);
  the memory clock moved between 2092, 2736 and 3196 MHz.

**Fresh FPS** counts distinct frame timestamps shown. The **estimate** is ALVR's total latency
estimate, not motion-to-photon.

| Bitrate | Order | Transport | Fresh FPS | Estimate |
| --- | --- | --- | --- | --- |
| 1000 Mbps | AB | UDP / TCP | 189.7 / 187.6 | 33.0 / 31.9 ms |
| 1250 Mbps | ABBA | TCP, UDP, UDP, TCP | 171.0, 177.3, 180.7, 169.2 | 36.4, 33.9, 34.9, 34.9 ms |
| 1250 Mbps | later runs | UDP | 175.7, 174.8, 176.6, 170.0 | |
| 1500 Mbps | AB | TCP / UDP | 110.1 / 110.3 | **112.4 / 42.5 ms** |
| 1500 Mbps | later runs | UDP | 116.0, 112.3, 116.3, 120.0, 118.0 | |

What the diagnostics showed:

- **The transport delivers.**
  - At 1500 Mbps the server sent 207 frames/s of 637 datagrams each, spending 1.9–2.5 ms per
    frame in `send_to`.
  - The headset completed 9260 frames in 45 s and dropped 38, all while the stream started.
  - At 1250 Mbps it dropped none.
  - The kernel reported no receive-buffer errors.
- **The frames repeat poses.** The server matches each SteamVR frame to the tracking sample it
  was rendered from (`PoseHistory::GetBestPoseMatch`). When no new tracking has arrived, the next
  frame carries the same timestamp. The client shows it, but fresh FPS counts it once.

| Bitrate | Frames with the previous frame's timestamp | Tracking arrival gap p90 / p99 / max |
| --- | --- | --- |
| 1000 Mbps | about 2% | 3.7–4.0 / 6.1–8.7 / 9–45 ms |
| 1250 Mbps | 8–15% | 4.5–4.6 / 9.1–9.9 / 24–39 ms |
| 1500 Mbps | 41–45% | 6.6–8.3 / 15.5–17.5 / 25–39 ms |

Gaps include the zero gaps between the client's several packets per pose. The tracking packets
travel up a link the video keeps busy, so they arrive late and bunched.

Moving ALVR's stream socket (which carries tracking) from TCP to UDP changed nothing: 116.3 and
176.6 fresh FPS at 1500 and 1250 Mbps.

So on this link:

- UDP is never worse than TCP.
- At 1250 Mbps UDP shows about 8 more fresh frames per second.
- At 1500 Mbps UDP avoids TCP's queue, but nothing yet fixes the late tracking.

Fixing the tracking would take the server rendering from a predicted pose for the frame's own
display time and sending that pose with the frame, because the client can only reproject with
poses it has sent.

The diagnostics stay in the build as error-level lines:

- `[Q3PW_TRANSPORT]` on the client: frames complete, dropped and missing per transport.
- `[Q3PW_UDP_SEND]` on the server: frames, repeated timestamps, datagrams and send time.
- `[Q3PW_TRACKING_RX]` on the server: tracking arrival gaps.

## Late tracking on Wi-Fi (`.67`, `.68`)

The settings were:

- 120 Hz, 2080x2208 per eye from a 3072x3216 render, CDF 5/3, constant 1250 Mbps.
- Wi-Fi 6E only, with the harness scene panning at 60 deg/s and the headset static and unworn.
- One 10 s block per run after the settle.

**Repeated** is the share of server frames that carry the previous frame's tracking timestamp
(`[Q3PW_UDP_SEND]`).

### Stream protocol and DSCP (`.67`)

| Stream socket / DSCP | Repeated | Fresh FPS | Network p99 | Tracking gap max |
| --- | --- | --- | --- | --- |
| TCP / EF | 3.7%, 4.7% | 112–116 | 27–32 ms | 37–42 ms |
| UDP / EF | 2.8%, 2.9% | 112–116 | 23–35 ms | 26–30 ms |
| UDP / CS7 | 4.8%, 3.8% | 112–116 | | up to 69 ms |
| TCP / CS7 | 3.8% | 112–116 | | |

- UDP for ALVR's stream socket (which carries tracking) repeats fewer poses than TCP.
- CS7 maps to the same voice access category as EF on this router and doesn't help, so EF stays.
- From `.68` both the stream socket and the PyroWave transport default to UDP. USB always uses
  TCP whatever the setting: the client opens the UDP video socket only when the stream isn't wired.

### Server-predicted head poses (`.68`)

When no head sample reaches the PC between two vsyncs:

1. The server extrapolates the newest head sample by its velocity to the time elapsed since it.
2. It renders the next frame from that pose, under a timestamp marked as an extrapolation
   (`alvr_packets::extrapolated_timestamp`: the base plus a multiple of 65.536 µs plus a marker).
3. The client keeps the motion it sent for each timestamp. When a frame carries a marked
   timestamp, it finds the base sample and applies the same extrapolation, so the reprojection
   uses the pose the frame was rendered with.

The client now sends head velocity, which it used to zero. A real sample older than the last
extrapolated one is held back, so the head pose never moves backwards. The setting is
**Headset → Predict late head poses on the PC** (`headset.extrapolate_late_head_poses`), on by
default; it needs a SteamVR restart.

UDP / EF, 120 Hz, 1250 Mbps, ABBA:

| Run | Extrapolation | Repeated | Extrapolated vsyncs | Fresh FPS | Lost frames/s | Tracking gap max |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | on | 0.8% | 984 / 7808 | 112.6 | 20.9 | 214 ms |
| 2 | off | 3.2% | | 114.4 | 9.9 | 33 ms |
| 3 | off | 1.3% | | 117.8 | 4.7 | 25 ms |
| 4 | on | 0.1% | 161 / 7808 | 118.8 | 4.9 | 48 ms |

- The mean repeated share is 0.45% with extrapolation and 2.25% without, below the 2% target.
- Run 1 caught a bad stretch of Wi-Fi (a 214 ms tracking gap), which accounts for its losses.
- The client found every extrapolated pose: `[Q3PW_POSE_LOOKUP] missing=0` in every run.

Over Wi-Fi at 207 Hz and 1000 Mbps (one run, which accidentally connected through the Wi-Fi
entry), repeated poses were 0.2%, with 164 fresh FPS and no dropped frames.

Wired (USB, TCP, 207 Hz, 1000 Mbps, ABBA) is unaffected: fresh FPS was 184.6 and 189.5 with
extrapolation on, and 185.6 and 187.5 with it off.

Still untested:

- Whether head movement looks smoother in the headset.
  - The harness headset is static, so its extrapolated poses barely differ from the held one.
  - Fresh FPS counts distinct timestamps, so these runs show that the mechanism works: every
    frame gets a new timestamp, and the client finds every pose.
  - They don't show that the predicted motion is right.
  - A unit test (`server_extrapolation_matches_the_client`) checks that the PC and the headset
    compute the same pose. Whether it looks right in motion needs a worn test.
- How it behaves under sustained play.

New diagnostics, logged at error level every 5 s:

- `[Q3PW_POSE_EXTRAPOLATE]` on the server: vsyncs, extrapolated frames, held-back samples, and
  the mean and maximum extrapolation distance.
- `[Q3PW_POSE_LOOKUP]` on the client: frames that found their sent pose, found an extrapolated
  pose, or found neither.
