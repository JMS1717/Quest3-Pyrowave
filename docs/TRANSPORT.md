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
- **Wi-Fi: TCP is the bottleneck.** At 1000 Mbps TCP and UDP perform the same. Above that, TCP
  queues frames for hundreds of milliseconds to seconds, while UDP still delivers almost every
  frame on time: 1250 Mbps at about 10 ms, 1500 Mbps at about 12–17 ms. UDP adds about
  250–500 Mbps of usable Wi-Fi bitrate.
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
  **PyroWave → Transport → UDP**. Live results are in [WIRELESS.md](WIRELESS.md).
- **Later:** forward error correction could recover the 0.2–3% of frames that lose a datagram.
  Windows UDP segmentation offload could cut the server's send cost.
