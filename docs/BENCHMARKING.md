# Benchmark protocol

How to measure a change on the Quest 3, and which tool in `tools/quest3/` gives each number.
[AGENTS.md](../AGENTS.md) sets the rules; this page applies them. The current results are in the
[scorecard](WHOLE-STACK-SCORECARD.md).

The owner's target is a 3072x3216 per-eye render streamed at 2080x2208 per eye, 207 Hz, about
200-207 fresh FPS, 1000-1500 Mbit/s and under 30 ms optical motion-to-photon. Fresh frames,
pacing, correctness and latency come before bitrate or refresh numbers.

## Claims stay separate

| Claim | Proves | How |
| --- | --- | --- |
| runtime acceptance | the headset accepted the refresh rate | `[Q3PW_RATE]`, `[Q3PW_EFFECTIVE]`, [capability gate](#capability-gate) |
| standalone decode budget | the decoder alone fits the frame time | [`decoder_ab`](#standalone-decode-and-correctness) |
| short live screen | the stream works over a few seconds | [short screens](#short-screens) |
| sustained gameplay | the stream holds up in a real game | [sustained runs](#sustained-runs) |
| perceptual quality | it looks good in the headset | [image quality](#image-quality) |
| optical latency | the measured photon delay | [OPTICAL-LATENCY.md](OPTICAL-LATENCY.md) |

A short screen can reject a candidate. It cannot prove sustained FPS, thermals or latency.

## Start with the frame trace

Before optimizing anything, find where frames are lost: decode, publication, selection or
presentation ([FRAME-TRACE.md](FRAME-TRACE.md)).

1. Set `debug.q3pw.frame_trace=1` on the headset. It is read at stream start, so restart the client.
2. Save the client's log with epoch timestamps (`adb logcat -v epoch`).
3. Summarize one window:

```powershell
python -m tools.quest3.frame_trace client.log --from <epoch start> --to <epoch end> --json trace.json
```

It reports unique frames per second at each stage, stage durations, arrival and publication
spread, publications per selection interval, where superseded frames landed, and whether each
empty selection waited on the network or the decoder. Clear the property afterwards.

## Short screens

The default experiment. Use it to reject candidates quickly.

- **Window:** 3-5 s settle, then a 10-12 s measurement. Never more than about 15 s per window.
- **Order:** ABBA where possible, in one session. Keep restarts outside the measured window.
- **Restart between arms** when a setting is read at stream start (the `debug.q3pw.*` decoder and
  trace properties, for example). A session setting such as the wavelet needs its own streamer
  session per cell.
- **Scene:** `python -m tools.quest3.quality_scene --out <private dir> --pan-deg-s 60` is the standard 207 Hz scene
  (the October 7 screens all used a 60 deg/s pan). A still scene encodes small and hides cost.
- **Configuration:** record render size, stream size, refresh, wavelet, bitrate, chroma,
  foveation, decoder mode, eye copy path (direct or staging), wired video connections, GPU clock
  and transport. Change one thing per
  comparison.
- **No concurrent trials.**

### What to record for every window

| Measure | Source |
| --- | --- |
| Fresh FPS | `taken` in the client's `[Q3PW_FRESH]` line, logged each second with `debug.q3pw.fresh_probe=1`. Summarize with `python -m tools.quest3.freshness windows client.log --start <epoch> --end <epoch> --pid <client pid>`. The frame trace's taken count is the same measure |
| Superseded and empty selections | `superseded` and `empty` in the same line, or the frame trace |
| GPU decode and fence times | the decoder's `gpu decode ms:` and `submit->fence ms:` lines (every 3 s), or the frame trace's decode records |
| Eye-pass time | `[Q3PW_EYE_GPU]` with `debug.q3pw.eye_gpu_probe=1`, summarized by `python -m tools.quest3.eye_gpu`; or the frame trace's eye-copy completions. The eye GPU timer exists only in the direct eye copy (`debug.q3pw.direct_eye_copy=1`), not in ALVR's staging renderer |
| GPU and memory clocks | the VrApi logcat line: GPU level and MHz, and `Mem=` |
| Effective display period | `[Q3PW_EFFECTIVE] ... period_ns=` (logged each second) and the frame trace's display periods per second |

**Fresh FPS** means distinct decoded frames actually shown. It is not VrApi FPS, ALVR's frame
counter or the selected refresh rate.

ALVR's stage estimates (`network_s`, `decoder_s`, `total_pipeline_latency_s`) come from the
dashboard's statistics events ([capture](#alvr-statistics-capture)). They are estimates, not
motion-to-photon. Do not mix GPU-only decode, decode-to-fence time and ALVR's decoder stage.

### Noise you must control

- **Memory clock.** It moves between 2092, 2736 and 3196 MHz from session to session and shifts
  results by up to about 12 FPS. It cannot be pinned. Record `Mem=` and reject or separate any
  window in which it changed.
- **GPU clock.** Use the server's **Quest 3: maximum GPU clock over USB**
  (`video.pyrowave.quest3_max_gpu_clock`, 690 MHz) for high-refresh work. It makes decode-bound
  results repeatable. It works only over USB adb. Over Wi-Fi, set it with
  `python -m tools.quest3.gpu_level enable --state <file>` and restore it with `restore`. Don't use
  both: the server's helper puts back its saved value within about 2 s.
- **Refresh after a relaunch.** HorizonOS writes `debug.oculus.refreshRate=72` itself when a VR
  app starts with the property empty, and relaunches sometimes come up at 72 or 90 Hz. Check the
  effective display period after every client relaunch.
- **Unworn headset.** It reads as unmounted: video plays, but tracking, views and statistics stop.
  Hold proximity during a test (`python -m tools.quest3.awake --state <file>`) and release it with
  `--restore`.

Follow AGENTS.md "Hardware testing" for everything else: don't use `adb reconnect`, snapshot and
restore every property and session value with readback, and leave Virtual Desktop as found.

The ABBA harness behind the October 7 screens is private and stays outside the repo. The tools
on this page are its public building blocks.

## Capability gate

Launch the Quest APK in VR so it creates an OpenXR session. Then, from the repository root:

```powershell
python -m tools.quest3.bench capabilities --adb adb --out capabilities.json
python -m tools.quest3.bench plan --capabilities capabilities.json --out plan.json
```

- The client logs `[Q3PW_CAPS]` from enumeration and again with `source=probe` after its startup
  probe. Enumeration is incomplete on HorizonOS: absence does not mean unsupported.
- The helper reads only the running client's process. No running client is an error.
- A rate counts only when the request succeeds and matches the runtime frequency and three
  consecutive frame periods. Unconfirmed rates are labelled **not_confirmed** and never
  benchmarked at a fallback.
- The plan randomizes confirmed rates and bitrates over three replicates, 15 s per cell by default.
- Reopen the app to probe again after an OS or display-scaling change. For 240 Hz see
  [REFRESH-RATES.md](REFRESH-RATES.md#240-hz-developer-experiment).

## Controlled session setup

```powershell
python -m tools.quest3.control apply --codec PyroWave --wavelet Haar --mbps 1000 --hz 207 --decode-path Auto --capabilities capabilities.json
python -m tools.quest3.control restart --steamvr "<SteamVR folder>" --streamer "<streamer folder>"
```

- `apply` defaults to `--wavelet Cdf97`. Pass `--wavelet Haar` (the shipped default) or `Cdf53`
  explicitly.
- `--chroma 420|444` and `--transport Tcp|Udp` are separate controls. Captures record requested
  and negotiated chroma.
- `control resolution --render-eye 3072 3216 --encode-eye 2080 2208` sets the render and stream
  sizes independently ([RENDER-ENCODE-RESOLUTION.md](RENDER-ENCODE-RESOLUTION.md)).
- `control foveation`, `control downsample` and `control latency-stamp` cover the other
  stream-start settings.
- The GPU clock and wired video connections are dashboard settings; `control` does not set them.
- Refresh, resolution, codec, wavelet and decode-path changes need a SteamVR restart. Wait for
  streaming to settle before measuring. A settings change during a window invalidates it.

Decoding: Quest 3 Auto selects Compute. The fast decoders (multilevel Haar and CDF 5/3 Decoder V2,
with packed YCbCr output) run only on the compute path. The fragment path, 4:4:4 and the other
unsupported cases fall back to the older decoder ([HAAR32.md](HAAR32.md), [DECODER-V2.md](DECODER-V2.md)),
so compare decode paths only when that is the question.

## ALVR statistics capture

```powershell
python -m pip install websocket-client==1.8.0
python -m tools.quest3.bench capture --hz 207 --seconds 12 --adb adb --out results/local/haar-207-1000-r1
```

- It writes per-frame dashboard events as JSONL and a JSON report: p50/p95/p99 ALVR stage timings,
  submission rate, bitrate, timestamp gaps and headset telemetry.
- It reads the server's statistics. It does not read `[Q3PW_FRESH]`, so it does not give fresh FPS.
- It checks session settings and the client process before and after. A changed or missing client
  invalidates the cell. Startup diagnostics in `runtime_evidence` are context, not continuous
  refresh proof.
- No streaming frames is a failed measurement, not a zero-latency result.
- GPU clock and load sysfs counters are read without root. An unavailable counter carries its
  error, never an invented value. PC GPU encode time comes from ALVR; vendor PC clock sensors need
  an external logger joined by capture time.
- The provenance checks have [CI evidence](../results/BENCHMARK-PROVENANCE-CI-2026-10-02.json).

`results/local/` is for private output. Raw captures can identify a device or setup: share
reviewed reports, not raw logcat or session dumps.

## Standalone decode and correctness

**Decode budget.** `decoder_ab` times decoders on the headset with no compositor, network or eye
pass. Build it with `tools/pyroclient/build.sh`; the reproduce steps are in
[DECODER-V2.md](DECODER-V2.md#reproduce) and [HAAR32.md](HAAR32.md#reproduce). Run all arms
interleaved in one process so they share the GPU clock, and state the clock. At 207 Hz the frame
period is 4.83 ms; the 1000 Mbit/s cap is 604 KB per frame.

**Correctness.** Every new decoder mode needs an exact-pixel (or bounded-difference) gate before a
live comparison. `decoder_ab` has gates per mode. `pyroclient_test <in.wave> <out.rgba> [iterations]
[auto|compute|fragment]` decodes one bitstream on the headset; deploy `libpyroclient.so`,
`libpyrowave-shared.so` and `libc++_shared.so` beside it and run with `LD_LIBRARY_PATH=.`. Score a
readback against the PC decoder's full-range BT.709 RGBA with `python -m tools.quest3.score
reference.rgba actual.rgba --width W --height H --out score.json`.

**In-headset screenshots** of the exact build decide whether the image is correct. A release
needs them.

## Image quality

- `python -m tools.quest3.quality_scene` renders a deterministic, codec-hard scene (brick, fine
  multicolour HUD text, thin lines, saturated edges, gradients, a zone plate). It can pan.
- The server's dump trigger (`ALVR_PYROWAVE_DUMP_TRIGGER`) captures the live encoder input.
- `python -m tools.quest3.quality_score <clip.y4m> --out <dir> --mbps 1000 1500 --wavelet haar 53`
  re-encodes the clip at the live byte cap and scores PSNR-HVS-M, added block edges, colour near
  edges and flicker.
- `python -m tools.quest3.stereo_scene --quality` is the flat correctness chart with fine coloured
  HUD text ([CHROMA.md](CHROMA.md)).

Offline scores rank configurations; they do not prove perceptual quality. That needs an
in-headset comparison, ideally side by side with Virtual Desktop.

## Sustained runs

Only for candidates that pass repeated short screens.

- Run 5-15 minutes, in a real game with motion, particles, dark content and HUD text. Record the
  scene, whether the headset was worn, render size and SteamVR supersampling.
- Record battery temperature, thermal status and both clocks throughout. Stop on severe thermal
  status, visible artifacts or disconnects.
- `bench capture --seconds 310` sets the legacy `sustained_requested_fps` flag only when at least
  300 s lie between the first and last observed frame events. Neither that flag nor
  `requested_rate_screen_passed` certifies image correctness, thermals, gameplay or fresh FPS.
- Sustained thermals at the 690 MHz GPU clock have not been measured.

## Latency

- ALVR's `total_pipeline_latency_s` is an estimate from tracking to predicted display. The frame
  trace's frame age at display is measured on one clock but also ends at predicted display.
  Neither is motion-to-photon.
- Optical latency needs a camera at 240 fps or more and at least 50 pairs. Use the latency stamp
  and `tools/quest3/latency_clock.py` ([OPTICAL-LATENCY.md](OPTICAL-LATENCY.md)). That procedure
  measures composition-to-photon; state that, and keep it separate from ALVR's estimate.

## Network-only probes

Build `tools\quest3\build_probes.cmd` in a Visual Studio x64 developer prompt (it builds
`network_sender.exe`). Deploy the CI Android artifact's `udprecv-android` to
`/data/local/tmp/q3pw/` and make it executable. Then:

```powershell
python -m tools.quest3.network --ip HEADSET_IP --out results/local/udp --seconds 10
```

The sender reports achieved Mbit/s, burst duration, skipped and late frames. Receiver deadlines
are relative to its first packet, not synchronized one-way latency. Record whether the VR app is
active, because Wi-Fi locks and contention change the result.

The physical link rate is not application throughput. On the tested Wi-Fi 6E link (2401 Mbit/s
PHY) the usable video ceiling was between 1250 and 1500 Mbit/s, and a constant bitrate above it
queued frames without limit ([WIRELESS.md](WIRELESS.md)). Do not call a bitrate achieved unless
the delivered payload and fresh FPS support it.

## Codec comparisons

Use the same render size, stream size, scene, refresh and bitrate across codecs. PyroWave uses
4:2:0 by default (4:4:4 is opt-in); light foveation is opt-in ([LIGHT-FOVEATION.md](LIGHT-FOVEATION.md)).
Hardware H.264/HEVC/AV1 runs are comparison points, not equal-quality claims. Original Galaxy XR
data in `captures/` is upstream evidence only.


---

[![Support Quest3-Pyrowave](https://img.shields.io/badge/Support_Quest3--Pyrowave-PayPal-0070BA?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/jasonselsley)
