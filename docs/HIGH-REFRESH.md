# Higher resolution and refresh on Quest 3, October 6

Short stationary-chart screens on the owner's Quest 3 over USB/TCP, 4:2:0, Haar/Compute,
1000 Mbps, no foveated encoding, one synchronous decode worker, stage and eye GPU timers on.
Each cell is two 12-second windows (four for ABBA comparisons) in its own guarded phase with a
snapshot, an independent restorer and readback of the owner's properties and session.
These are screening results, not sustained, gameplay, perceptual or optical-latency measurements.
Per-window numbers, PC-side stages and temperatures: [results](../results/HIGH-REFRESH-2026-10-06.json).

"Fresh FPS" is distinct server target timestamps reaching the display per second. "App loop" is
VrApi's frame rate for the client, which drops below the panel rate when the client misses frames.

## Panel modes

The panel's mode list settles what each rate can show:

| Rates | Panel mode | Per eye |
| --- | --- | --- |
| 72-207 Hz | 4128x2208 | 2064x2208 |
| 240 Hz | 3104x1664, upscaled by the panel (`debug.oculus.forceDisplayScaling=1`) | 1552x1664 |

A 240 Hz stream larger than about 1552x1664 per eye cannot add panel detail.

## Results

| Refresh | Stream per eye | Fresh FPS | Lost /s | GPU decode p50 | Decode fence p50 | App loop |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 120 Hz | 2080x2208 | 118.6 | 1.4 | 6.3 ms | 8.0 ms | 121 |
| 120 Hz | 2560x2720 | 89.2 | 30.9 | 7.9 ms | 10.6 ms | 121 |
| 120 Hz | 3072x3216 | 65.0 | 55.0 | 11.9 ms | 15.1 ms | 121 |
| 144 Hz | 2080x2208 | 132.7 | 11.5 | 4.8 ms | 6.7 ms | 138 |
| 144 Hz | 1664x1760 | 132.6 | 11.4 | 4.7 ms | 6.1 ms | 144 |
| 207 Hz | 2080x2208 | 116.6 | 90.9 | 5.6 ms | 8.2 ms | 118 |
| 207 Hz | 1664x1760 | 174.2 | 34.0 | 3.4 ms | 5.4 ms | 177 |
| 207 Hz | 1440x1536 | 196.2 | 13.5 | 2.8 ms | 4.5 ms | 201 |
| 240 Hz | 1536x1664 | 200.2 | 40.5 | 2.9 ms | 4.7 ms | 203 |
| 240 Hz | 1440x1536 | 222.7 | 18.1 | 2.7 ms | 4.0 ms | 227 |
| 240 Hz | 1280x1376 | 229.7 | 11.3 | 2.5 ms | 3.9 ms | 234 |
| 120 Hz, repeat | 2080x2208 | 119.2 | 0.9 | 5.9 ms | 8.0 ms | 120 |

120-207 Hz ran on the reviewed `.55` pair; 240 Hz needs `.56` (see below). The repeated 120 Hz
cell matched the first, so the matrix did not drift thermally (battery 35-41 C, thermal status 0).

- **Above native resolution the decoder sets the frame rate.** Fresh FPS follows
  1000 / fence: 2560x2720 reaches 89, 3072x3216 reaches 65 at 120 Hz while the panel keeps
  reprojecting at 120. Decode cost grows a little slower than pixels (2.15x pixels, 1.88x decode).
- **207 Hz at native resolution is worse than 120 Hz.** The GPU saturates (96%), the client's own
  frame loop falls to 118 and only 117 fresh frames/s arrive. 1440x1536 per eye reaches 196.
- **144 Hz loses about 11 frames/s whatever the size.** At 1664x1760 the GPU has headroom (82%)
  and clocks down to 545 MHz, yet about 11 decoded frames/s are superseded: two arrive within one
  display slot while about 46 slots/s find nothing until the 3.5 ms wait catches a late frame. The
  PC sent 142.3 frames/s. This is arrival jitter at the selection edge, not decode.
- **240 Hz streams end to end.** 1280x1376 per eye delivered 229.7 fresh frames/s and
  1440x1536 222.7 with SteamVR at 240 Hz; the server averaged 240.6-241.1 rendered frames/s.

## 240 Hz: what had to change

See [REFRESH-RATES.md](REFRESH-RATES.md#240-hz-developer-experiment). In short: the runtime refuses
the 240 request even while the forced mode runs at 240, so `.55` never reported 240 and the server
refused the session. `.56` confirms a rate the runtime already runs (three matching periods), and
`tools/quest3/refresh_scaling.py` now applies the override the way HorizonOS needs (change while
awake, confirm the kernel DSI mode) and restores it the same way.

## Levers measured at high refresh

| Lever | Cell | Off | On | Decision |
| --- | --- | --- | --- | --- |
| GPU level 7 (`debug.oculus.gpuLevel=7`, 690 MHz vs level 4 at 640 MHz, verified in VrApi) | 240 Hz, 1440x1536, ABBA | 220.8 fresh, fence 4.05 ms, loop 225 | 224.1 fresh, fence 3.89 ms, loop 232 | Gain wherever decode is GPU-bound. A developer property the APK cannot request: `tools/quest3/gpu_level.py` sets and restores it |
| GPU level 7 | 240 Hz, 1536x1664, ABBA | 198.9 fresh, loop 205 | 203.8 fresh, loop 218 | |
| GPU level 7 | 207 Hz, 1664x1760, ABBA | 172.1 fresh, loop 176 | 181.5 fresh, loop 185 | |
| GPU level 7 | 120 Hz, 2560x2720, ABBA | 89.5 fresh, fence 10.6 ms | 93.2 fresh, fence 10.2 ms | |
| GPU level 7 | 120 Hz, native, ABBA | 119.1 | 118.8 | No gain where 120 is already reached |
| LOW decode priority (`debug.q3pw.decode_priority=low`) | 240 Hz, 1440x1536, ABBA | 220.8 fresh, loop 225 | 183.3 fresh, loop 240 | The loop stops missing frames but decode slows to 3.5 ms; keep the >120 Hz default (normal) |
| Server phase lock (`ALVR_PHASE_LOCK=1`, separate SteamVR starts, off/on/on/off) | 207 Hz, 1440x1536 | 194.1, 200.2 fresh | 196.3, 193.7 fresh | No gain. The controller never converged: it walked the virtual vsync 17-36 ms earlier while the client's decoder queue stayed 1.3-2.0 ms at every phase. At 144 Hz all three lock-on starts failed: SteamVR Home (`steamtours.exe`) never finished launching, so SteamVR refused the test scene, and the hung processes outlived SteamVR and also blocked the next lock-off start until they were stopped. Not adopted |
| PR #8 fused dequant + level-0 Haar | standalone Adreno decode | 5.32 ms decode, 7.76 ms fence | 5.28 ms, 7.75 ms | Correct (gate pass, default path byte-identical to `.55`), no speedup: [results](../results/FUSED-DEQUANT-HAAR-GPU-2026-10-06.json). Keep off |

Under its BOOST performance request the client gets GPU level 4. The governor caps that level at
640 MHz and drops to 545 MHz at light load. That is why decode time barely falls between 2080x2208
and 1664x1760 at 144 Hz.

## Recommended settings from these screens

- Native detail: **120 Hz, 2080x2208**. Highest fresh rate per pixel; higher stream sizes cost
  fresh frames one for one with decode time.
- High refresh: **240 Hz with display scaling at 1280x1376-1440x1536 per eye** (223-230 fresh
  frames/s), or **207 Hz at 1440x1536** (196) when full panel resolution matters more than rate.
  Add GPU level 7 for these and for above-native sizes; battery stayed 38-41 C in 12-second
  windows, but sustained thermals at 690 MHz are not measured.
- Avoid 207 Hz at native resolution and 144 Hz at native resolution: both starve the client loop.

## Not established

Sustained thermals, gameplay content (a stationary chart encodes small), perceived sharpness of the
240 Hz scaled mode, and optical motion-to-photon latency. The PC side had headroom in every cell
(encode 1.5-3.4 ms on the RX 7900 XTX); real games must also render at the chosen rate.
