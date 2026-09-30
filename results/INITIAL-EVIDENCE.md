# Initial Quest 3 evidence — 2026-09-30

These measurements preceded the current full-frame/no-foveation build. They are initial evidence,
not acceptance testing of that build. Further device benchmarks stopped at the maintainer's request.

- APK and Windows streamer clean builds completed through GitHub Actions; the maintainer confirmed
  the initial app streams PCVR. They reported incorrect right-eye foveation placement; the current
  implementation bypasses all server/client foveation instead of keeping that transform.
- OpenXR enumeration returned 72/80/90/120 Hz. Standard requests confirmed 144 Hz (~6.944 ms period)
  and 207 Hz (~4.831 ms period). 240 Hz was rejected with display scaling disabled. This establishes
  runtime operation in the lobby, not sustained streamed FPS or optical scanout.
- A preliminary 60-second foveated capture contained 5,378 frame statistics: client median 89.99 FPS,
  video median 600.10 Mbps, encode median 5.92 ms, estimated pipeline median 49.64 ms. Dashboard
  settings changed around this run and no before/after settings snapshot was captured, so it is
  unsuitable as a controlled codec comparison. Network residual time often clamped to zero.
- Three warmed offline repeats decoded 1,000/1,000 frames each for Compute and Fragment. Compute
  median fence times were 3.39/3.38/3.37 ms; Fragment varied 3.44/3.90/2.86 ms. Clock/load conditions
  were not controlled. Reference comparison favored Compute reconstruction accuracy on this pattern:
  maximum channel differences 3/2/3, versus Fragment 45/16/85. Neither is source-image quality PSNR.

| UDP target Mbps | Sent Mbps | Received Mbps* | Loss % | Sent frames assembled on time % |
|---:|---:|---:|---:|---:|
| 600 | 600.5 | 600.5 | 0.000 | 100.00 |
| 800 | 799.4 | 794.4 | 0.633 | 99.11 |
| 1000 | 998.5 | 993.2 | 0.528 | 99.22 |
| 1500 | 1471.0 | 1466.4 | 0.316 | 89.35 |
| 2000 | 1780.1 | 1740.6 | 2.215 | 0.12 |

*Received bytes normalized to sender duration; UDP/network-only, 10 seconds, one pass at 90 Hz
bursts. The 2000 Mbps sender achieved only ~1780 Mbps and missed every frame send deadline.
These runs exclude active VR decode and compositor contention. They do not prove a usable 2000 Mbps stream.

Raw metrics: [network](network-first-pass.json), [decode](decode-initial.json), [refresh](refresh-initial.json).
Device identifiers and private session dumps are excluded.

Current full-frame builds have not been benchmarked or visually accepted. Matched H.264/HEVC/AV1
measurements, high-refresh streaming FPS, sustained thermals, PC GPU clocks and optical end-to-end
latency remain unmeasured. The repository provides manual tools and a repeatable protocol for them.
