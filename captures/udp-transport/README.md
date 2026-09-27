# UDP on the Galaxy XR Wi-Fi: can PyroWave drop TCP? (prep runs)

Tool: `tools/udptest/udprecv.c` (NDK receiver on the headset) + `tools/xrbench/udptest.py`
(paced frame-burst sender and runner on the PC). These are 3-second prep runs at 17–18 %
battery with the headset idle; the 10-second decision sweep is still to run.

## The answer so far (3 s, 90 fps bursts of 1400-byte datagrams, low-latency Wi-Fi lock held)

| Mbps | frames on time | path added p99 | kernel→app lag p99 / max | loss | headset CPU | receiver CPU (1 core) |
|---|---|---|---|---|---|---|
| 100 | 100 % | 2.5–3.2 ms | 0.6 / 1.6 ms | 0.00 % | 7–8 % | 2.4–2.7 % |
| 600, plain recvmmsg | 98.5 % | 2.6 ms | 0.9 / 3.2 ms | 1.01 % | 14 % | 25 % |
| 600, UDP_GRO | 100 % | 1.9 ms | 1.9 / 3.1 ms | 0.00 % | 11 % | 17 % |

"On time" = every datagram of the frame arrived within one 90 Hz period (11.1 ms) of the frame's
first datagram, judged on kernel receive timestamps. "Path added" = frame assembly time minus the
sender's own emission span. The Wi-Fi path adds ~2–3 ms p99 to a 596-datagram burst at 600 Mbps.

`UDP_GRO` works on this 5.10 kernel: ~500 datagrams per read, receive CPU down a third.
`SCHED_FIFO` is refused for an adb-shell process (uid 2000), so the real-time arm is untested.

## The decision sweep (10 s per cell, 900 frames each, headset charged, lock held)

| Mbps | GRO | frames on time | path added p99 | kernel→app lag p99 / max | late packets | loss | receiver CPU (1 core) |
|---|---|---|---|---|---|---|---|
| 100 | off | 99.9 % | 4.6 ms | 1.1 / 4.2 ms | 70 of 90000 | 0.00 % | 4.3 % |
| 100 | on | 100.0 % | 3.2 ms | 1.7 / 4.6 ms | 0 of 90000 | 0.00 % | 3.3 % |
| 300 | off | 99.6 % | 5.1 ms | 1.5 / 9.1 ms | 521 of 268200 | 0.00 % | 11.0 % |
| 300 | on | 100.0 % | 3.2 ms | 1.5 / 5.9 ms | 0 of 268200 | 0.00 % | 7.8 % |
| 600 | off | 98.0 % | 3.5 ms | 2.1 / 12.9 ms | 1449 of 536400 | 0.19 % | 17.8 % |
| 600 | on | 99.1 % | 2.3 ms | 1.4 / 7.6 ms | 681 of 536400 | 0.00 % | 15.6 % |

**UDP is viable for PyroWave on this link.** Across 5,400 frames at 100–600 Mbps: loss 0.00–0.19 %,
98.0–100 % of frames complete inside a 90 Hz period, the path adds 2–5 ms p99 to a frame burst,
and kernel→app lag stays under ~2 ms p99 / 13 ms max. With `UDP_GRO` on, every rate is ≥ 99.1 %
on time at zero loss and the receiver costs ≤ 16 % of one core at 600 Mbps. Hottest zone flat at
66.8 °C throughout.

Two honest limits of this rig: the Python sender needs ~8.7 ms to emit a 596-datagram frame at
600 Mbps (subtracted, but it means the burst reaching the air is smoother than an encoder's), and
the whole-headset CPU column is confounded by whatever else the system was doing (the compositor
after a wake reads ~50 % on its own), so the receiver's own CPU is the number to use.

What a real PyroWave transport should take from this: UDP datagrams ≤ MTU, `UDP_GRO` on the
receive socket, `recvmmsg` with `MSG_WAITFORONE`, a per-frame deadline of one period measured
from the frame's first packet, drop-on-sight for packets past it, and XOR FEC only on PyroWave's
critical packets. Sender-side batching (sendmmsg / USO) is needed for a real encoder burst.

## Three instrument mistakes, each of which produced a confident wrong number first

1. **Userland arrival timestamps** (taken after each `recvmmsg`) made 57 % of frames look late at
   100 Mbps. Kernel `SO_TIMESTAMPNS` showed 99.6 % on time: the packets were there in time.
2. **The sender's emission span was inside "assembly".** Python `sendto` on Windows takes ~8.6 ms
   to emit a 596-datagram frame, most of the 10.7 ms assembly at 600 Mbps. Reported and subtracted.
3. **`recvmmsg` without `MSG_WAITFORONE`** waits for all 64 slots or the 200 ms `SO_RCVTIMEO`. The
   tail of every frame sat in the kernel until the next frame topped the batch up — p99 "11 ms of
   scheduler lag" and 200 ms "stalls", to the microsecond. Any real receiver built on `recvmmsg`
   needs the flag.

The Wi-Fi power-save hypothesis was tested and rejected: results were identical with a verified
`WIFI_MODE_FULL_LOW_LATENCY` lock held (via our own `com.xrwired.receiver`).

Files: `sanity-*.json` are the runner's summary rows; the earlier ones carry the artifacts above and
are kept so the progression is auditable.

## To run the decision sweep (from the PC, headset charged and worn)

    python -m xrbench.udptest --ip 192.0.2.10 --rates 100 300 600 --seconds 10 --gro --wifi-lock --out udp-runs\<date>
