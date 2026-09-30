# Benchmark protocol

The physical Wi-Fi link rate is not application throughput. Start with 600 Mbps, measure delivered
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
python -m tools.quest3.bench capture --hz 90 --seconds 60 --adb adb --out results/local/pyro-90-600-compute-r1
python -m tools.quest3.bench capture --hz 120 --seconds 300 --adb adb --out results/local/sustained-120
```

Each capture checks session settings before/after and writes per-frame events as JSONL and a JSON report with p50/p95/p99 timings, actual
FPS/bitrate, timestamp gaps and headset telemetry. No streaming frames is a failed measurement,
not a zero-latency result. GPU-clock/load sysfs counters are read without root; unavailable counters
carry their error, never an invented value. Thermal-service and battery snapshots complement the
ALVR Android PowerManager telemetry. PC GPU encode time is measured by ALVR; vendor-specific PC
clock/load sensors require an external tool (e.g. GPU-Z logging), joined by capture time.

Use **the same resolution, scene, encoder range, foveation and refresh** across codecs. PyroWave
preserves full chroma at the encoded resolution; foveation still reduces peripheral spatial detail.
Run CDF 9/7 Compute and Fragment at equal settings. Keep Auto as the startup path until measured
correctness and sustained latency favor one. CDF 5/3 forces Compute and is a separate experiment.
Hardware H.264/HEVC/AV1 at 200 Mbps are comparison starting points, not equal-quality claims.

Use a static text/color-grid scene for correctness, then an actual game with motion, particles,
dark content and HUD text. Record whether worn, scene name, render size and SteamVR supersampling.
Cool to the same thermal/battery baseline between cells; stop a cell on severe thermal status,
visible artifacts or disconnects. Alternate settings over at least three replicates. After a short
pass, run 5â€“15 minutes to establish sustained performance rather than quoting the best warm frame.

ALVR `total_pipeline_latency_s` is an **estimated pipeline latency** from tracking to predicted
display, not a photodiode measurement. Optical end-to-end latency requires a high-speed camera or
photodiode and independently documented stimulus. Timestamp gaps are a frame delivery proxy, not
panel scanout. Do not mix GPU-only decode, decode-to-fence wall time and total decoder stage.

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
