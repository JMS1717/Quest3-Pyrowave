# Benchmark protocol

The physical Wi-Fi link rate is not application throughput. The startup candidate is 400 Mbps / 72 Hz / full panel-relative resolution / 4:2:0 / TCP, with live acceptance pending. Try 600 Mbps / 90 Hz only after it passes. The 1000 Mbps / 120 Hz target is experimental. For manual throughput sweeps, measure each requested rate separately, including delivered
bytes, loss and p99 timing, and increase through 800/1000/1500/2000 Mbps. A 2000 Mbps budget is
250 MB/s before IP, Wi-Fi, control and retransmission overhead. Do not call a target bitrate achieved
unless the actual payload and frame rate support that statement.

## Capability gate

Launch the Quest APK in VR so it creates an OpenXR session. Then, from the repository root:

```powershell
python -m tools.quest3.bench capabilities --adb adb --out capabilities.json
python -m tools.quest3.bench plan --capabilities capabilities.json --out plan.json
```

The client logs `[Q3PW_CAPS]` initially from enumeration and again with `source=probe` after
its startup probe. Enumeration is incomplete on HorizonOS v2.7: absence does not mean unsupported.
Each request must succeed and match the runtime frequency and three consecutive frame periods.
Unconfirmed rates are labelled **not_confirmed**, never silently benchmarked at a fallback.
The plan randomizes confirmed Compute/Fragment × bitrate × three replicates and hardware-codec
baselines. Run the probe again after OS or display-scaling changes by reopening the app.
See [refresh setup](REFRESH-RATES.md) for the distinct 240 Hz scaling experiment.

## Capture

```powershell
python -m pip install websocket-client==1.8.0
python -m tools.quest3.bench capture --hz 90 --seconds 15 --adb adb --out results/local/pyro-90-600-compute-r1
python -m tools.quest3.bench capture --hz 120 --seconds 310 --adb adb --out results/local/sustained-120
```

Each capture checks session settings before/after and writes per-frame events as JSONL and a JSON report with p50/p95/p99 timings, actual
FPS/bitrate, timestamp gaps and headset telemetry. No streaming frames is a failed measurement,
not a zero-latency result. GPU-clock/load sysfs counters are read without root; unavailable counters
carry their error, never an invented value. Thermal-service and battery snapshots complement the
ALVR Android PowerManager telemetry. PC GPU encode time is measured by ALVR; vendor-specific PC
clock/load sensors require an external tool (e.g. GPU-Z logging), joined by capture time.

Use **the same resolution, scene, encoder range and refresh** across codecs. PyroWave
uses 4:2:0 by default (optional 4:4:4); foveation is disabled in the current build.
Run CDF 9/7 Compute and Fragment at equal settings. Quest 3 Auto uses Compute based on the initial reference/readback check; both paths remain
available for manual comparison. CDF 5/3 forces Compute and is a separate experiment.
Hardware H.264/HEVC/AV1 at 200 Mbps are comparison starting points, not equal-quality claims.

Use a static text/color-grid scene for correctness, then an actual game with motion, particles,
dark content and HUD text. Record whether worn, scene name, render size and SteamVR supersampling.
Cool to the same thermal/battery baseline between cells; stop a cell on severe thermal status,
visible artifacts or disconnects. Alternate settings over at least three replicates. After a short
pass, run 5–15 minutes to establish sustained performance rather than quoting the best warm frame.

ALVR `total_pipeline_latency_s` is an **estimated pipeline latency** from tracking to predicted
display, not a photodiode measurement. Optical end-to-end latency requires a high-speed camera or
photodiode and independently documented stimulus. Timestamp gaps are a frame delivery proxy, not
panel scanout. Do not mix GPU-only decode, decode-to-fence wall time and total decoder stage.

`requested_rate_screen_passed` tests the observed submission rate, p1 instantaneous
FPS and direct-copy completion rate when available against the requested rate
(with the existing 2% sampling tolerance). A 15-second pass is a screening result.
The legacy `sustained_requested_fps` flag additionally requires at least **300
seconds between the first and last observed frame events**. Use a slightly longer
capture (for example, 310 seconds) to leave room for connection/startup overhead.
`submission_rate_window_s` records the actual interval. Requested capture duration
or time spent in ADB cleanup cannot substitute for observed streaming time.
Neither flag certifies full configuration, image correctness, thermal endurance,
gameplay, optical display FPS or the native120 goal. Review those gates separately.
Long tests remain reserved for candidates that pass repeated short comparisons.

Raw captures are local and may identify a device or setup. Share reviewed reports, not unchecked
logcat/session dumps. Original Galaxy XR data in `captures/` is upstream evidence only.

## Controlled session setup

```powershell
python -m tools.quest3.control apply --codec PyroWave --mbps 600 --hz 90 --decode-path Compute --capabilities capabilities.json
python -m tools.quest3.control restart --steamvr "C:\Program Files (x86)\Steam\steamapps\common\SteamVR" --streamer C:\q3pw\research\ALVR-20.13.0\build\alvr_streamer_windows
```

Refresh, resolution, codec and decode-path changes need a SteamVR restart/reconnection. Wait for
streaming to settle before capture. The tool verifies persisted settings; the report separately
records negotiated OpenVR configuration. A settings change during capture invalidates that cell.
ALVR network time is a residual estimate and can clamp to zero with the separate PyroWave UDP
transport; this is not evidence of zero network latency.

## Network-only and correctness probes

Build `tools\quest3\build_probes.cmd` in a Visual Studio x64 developer prompt. Deploy the Actions
Android artifact's `udprecv-android` to `/data/local/tmp/q3pw/` and make it executable. Then:

```powershell
python -m tools.quest3.network --ip HEADSET_IP --out results/local/udp --seconds 10
```

The sender reports achieved Mbps, burst duration, skipped and late frames. Receiver deadlines are
relative to its first packet, not synchronized one-way latency. These are network-only tests;
record whether the VR app is active, because Wi-Fi locks and contention change the result.

`pyroclient_test input.wave output.rgba 200 compute` and `fragment` compare decode paths on the same
bitstream. Deploy `libpyroclient.so`, `libpyrowave-shared.so`, and `libc++_shared.so` alongside it and
run with `LD_LIBRARY_PATH=.`. Score each readback against the PC PyroWave decoder, converted to
full-range BT.709 RGBA, with `python -m tools.quest3.score --help`. Include warm-up policy and
image dimensions; these offline results exclude VR compositor and network work.

Use `--chroma 420` or `--chroma 444` with `tools.quest3.control apply`. Chroma and bitrate are separate controls. Capture files record both requested and negotiated chroma.

For a reproducible fine colored HUD/text source, use `python -m tools.quest3.stereo_scene --quality --seconds 180 --out results/local/chroma-source`. Keep capture events strictly within its ready/start/end window. The client remains on420 by default; see [matched chroma evidence](CHROMA.md).

## Fast iteration

Plans and captures now default to **15-second screening cells**. Warm the unchanged
configuration before capturing; use a few deterministic frames for image/readback
correctness. Compare matched scenes and keep restarts outside the capture window.
Do not run concurrent trials. These short windows can reject a regression quickly;
they cannot establish sustained FPS or thermal behavior. Use longer explicit
`--seconds` runs only when a candidate reaches the target and merits acceptance.
