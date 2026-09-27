# First end-to-end PyroWave stream through the ALVR client

PC encodes PyroWave (3328x1472 4:4:4, full range, 600 Mbps), ALVR's TCP video stream carries it,
the headset decodes it with `libpyroclient` inside the ALVR client and renders through the
unchanged EGL staging path. The tester saw SteamVR's void through it. `events-clean.json` is the
first 8 s capture (~1 min into the session); `events-throttled.json` + `thermals-throttled.json`
are a 10 s capture ~3 min later, by which point the headset was at thermal status 4.

## Clean capture (8 s, 558 frames)

| stage | mean | p50 | p95 | H.264 B-15 for comparison |
|---|---|---|---|---|
| encoder | **5.41** | 5.22 | 6.09 | 13.12 |
| network | 10.31 | 11.80 | 14.94 | 11.88 |
| decoder | **11.50** | 11.27 | 16.25 | 15.95 |
| decoder_queue | 3.48 | 1.03 | 9.12 | 5.36 |
| client_compositor | 1.25 | 1.36 | 2.06 | 1.51 |
| vsync_queue | 35.29 | 37.56 | 39.82 | 26.56 |
| game_time | 17.98 | 7.06 | 44.14 | 5.36 |
| **total** | **84.31** | 78.96 | 111.38 | 80.07 |

72.0 fps, 599 Mbps, 0 packets lost. Encoder 13.1 → 5.4 ms and decoder 16.0 → 11.5 ms, as
predicted — and the total did **not** fall, exactly as the pacing sweep said it would not:
`vsync_queue` grew from 26.6 to 35.3 ms and absorbed the saving. The decoder stage here is
packet-received → frame-decoded and includes the hand-off to the decode thread plus the 9 ms
submit→fence; the pure GPU work is 4.9 + 2.7 ms.

`game_time` is high and unstable (p95 44 ms) with nothing but SteamVR's void running: the PC
side was not healthy either; unexplained, not attributed.

## Throttled capture (10 s, 503 frames) — thermal status 4

`cpu 88.9 C  gpu 88.6 C  video 84.2 C` before, essentially the same after. fps 56.9 (min 8),
decoder 19.9 mean / 35 p95, total 108.8 mean / 139 p95. This is the throttle, not the codec:
the same pipeline had run at 72 fps minutes earlier.

Two things to hold onto. The hardware map predicted this: PyroWave moves decode from the
coolest block (video) onto the shader cores, and the GPU zone is now as hot as the CPU. And
every capture from now on logs thermals before and after (`pyro_telemetry.py` does), because a
number taken at status 4 is not comparable to one taken at status 1.

Note: `dumpsys thermalservice` reports CPU0 at 38 C in the same minute the sysfs zones read 88 —
different sensors; the sysfs zones are what the harness has always used and what throttling
follows.
