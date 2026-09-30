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

The client logs `[Q3PW_CAPS]`, enumerates runtime rates, and logs refresh request rejection and
the currently reported rate. The server refuses unadvertised requests instead of choosing the
closest rate. The plan includes 90,120,144,207,240 Hz where advertised, records unsupported rates
as **skipped**, randomizes Compute/Fragment × bitrate × three replicates, and adds hardware-codec
baselines. A debug property value alone is not evidence of panel operation. Confirm actual runtime
rate and frame period after a request; refresh changes may settle asynchronously.

## Capture

```powershell
python -m pip install websocket-client==1.8.0
python -m tools.quest3.bench capture --hz 90 --seconds 60 --adb adb --out results/local/pyro-90-600-compute-r1
python -m tools.quest3.bench capture --hz 120 --seconds 300 --adb adb --out results/local/sustained-120
```

Each capture writes per-frame events as JSONL and a JSON report with p50/p95/p99 timings, actual
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
pass, run 5–15 minutes to establish sustained performance rather than quoting the best warm frame.

ALVR `total_pipeline_latency_s` is an **estimated pipeline latency** from tracking to predicted
display, not a photodiode measurement. Optical end-to-end latency requires a high-speed camera or
photodiode and independently documented stimulus. Timestamp gaps are a frame delivery proxy, not
panel scanout. Do not mix GPU-only decode, decode-to-fence wall time and total decoder stage.

Raw captures are local and may identify a device or setup. Share reviewed reports, not unchecked
logcat/session dumps. Original Galaxy XR data in `captures/` is upstream evidence only.
