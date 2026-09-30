# Galaxy XR ALVR Research: PyroWave 4:4:4

**Full-chroma (4:4:4) experimental wireless PC VR streaming for the Samsung Galaxy XR.** A patched
[ALVR](https://github.com/alvr-org/ALVR) 20.13 streams SteamVR from a Windows PC with
[PyroWave](https://github.com/Themaister/pyrowave), a GPU wavelet codec. The headset decodes it
with Vulkan compute instead of its hardware video decoder. Every frame keeps full colour
resolution, and the recommended profile runs at about 55-60 ms motion-to-photon at 90 Hz.

This is a **beta research release**, not a product. It supports the Galaxy XR only. Every result
below comes from one PC (RTX 3090), one headset and one Wi-Fi network. Results marked *Home* were
measured on static SteamVR Home with the headset unworn and say nothing about gameplay.

**Status:** experimental and provided as-is, with no support or maintenance promise. Report bugs
through this repository's Issues, and security problems privately through **Security > Report a
vulnerability**.

## Results

| | **PyroWave 4:4:4 (Recommended)** | **ALVR H.264 (tuned)** |
|---|---|---|
| Chroma | 4:4:4 | 4:2:0 |
| Render per eye | 60 % of the 3552x3840 panel (2131x2304; 1984x896 encoded, both eyes) | panel-native 3552x3840 |
| Refresh / bitrate | 90 Hz, 400 Mbps (300 Mbps fallback) | 72 Hz, 600 Mbps |
| Motion-to-photon | 60.2 ms at 400 Mbps, 57.0 ms at 300 Mbps (Home, median of 3); best 55.5 ms | 80.1 ms |
| Headset decode | 3.3 ms GPU, 5.0 ms to completion | 15.4-16.0 ms (hardware H.264) |
| Sustained | 90 fps for 5 minutes at 400 Mbps, 60.5 ms (Home) | 72 fps |

Measured along the way:

- **The decoder was the wall.** The headset's hardware H.264 decode takes 15-16 ms per frame
  and its HEVC decode about 82 ms. That caps ALVR's hardware path at 72 Hz and about 80 ms,
  because the latency floor falls only with the frame period. PyroWave on the GPU decodes in
  about 3 ms, which is what makes 90 Hz possible.
- **Resolution has a knee between 70 % and 80 % of the panel (Home).** 70 % (2304x1056 encoded)
  holds 90 fps at 60-63 ms. 80 % holds 90 fps only at 300 Mbps (70.6 ms). 90 % and 100 % fall to
  72 and 45 fps at 80-84 ms and drive the headset to critical thermal status.
- **Compression cost (Home).** Each setting was scored against its own lossless encoder input,
  over 30 frames per setting. PSNR-Y is 44.3 dB at 60 %/400 Mbps, 42.5 dB at 70 %/400 Mbps and
  41.9 dB at 60 %/300 Mbps. So 70 % at 400 Mbps carries 37 % more pixels at a smaller compression
  cost than 60 % at 300. This measures compression cost, not delivered detail, so it cannot say
  which setting looks better; that needs a worn comparison.

Full reports: [Home baselines and resolution x bitrate matrix](captures/home-baselines-and-matrix/README.md),
[latency budget](captures/pyrowave-in-alvr/latency-budget.md), and the
[UDP and 90 Hz ladder](captures/pyrowave-in-alvr/udp-ladder/README.md).

## Quick start for beta testers

**Requirements**

- A Samsung Galaxy XR with developer mode on, and adb.
- A Windows 10 or 11 PC with SteamVR. The PyroWave encoder uses D3D11 and Vulkan; it was tested on
  an NVIDIA RTX 3090. The H.264 profile uses NVENC, so it needs an NVIDIA GPU.
- A network with at least 400 Mbps of UDP headroom to the headset: 5 or 6 GHz Wi-Fi 6/6E/7 close
  to the access point. The link test on the development network ran at 750-950 Mbps.
- Stock ALVR removed from SteamVR, or its driver disabled. This build calls itself
  `20.13.0-pyro.1` and refuses to pair with stock ALVR clients and servers.

**Steps**

Download `pyrowave-beta-streamer-windows.zip`, `pyrowave-beta-client-galaxy-xr.apk` and
`SHA256SUMS.txt` from this repository's GitHub Releases page, and check the two files against the
sums.

1. Unzip `pyrowave-beta-streamer-windows.zip` and run `ALVR Dashboard.exe`. Register the driver
   with SteamVR from the Installation tab, as with stock ALVR.
2. Install the headset app with `adb install pyrowave-beta-client-galaxy-xr.apk`, then open
   **ALVR** on the headset. Allow the microphone, eye tracking and hand tracking when asked; eye
   tracking drives the foveation. Android XR asks only on the first launch, so close and reopen
   ALVR once after allowing them.
3. In the dashboard, open **Settings > Presets > Streaming profile** and choose
   **PyroWave 4:4:4 (Recommended)** (*Full-chroma experimental wireless VR streaming*). A fresh
   install starts on it.
4. The headset connects by itself; there is no pairing step. Discovery needs the PC and headset
   on the same subnet. Otherwise add the headset's IP address in the **Devices** tab.
5. Start SteamVR content.

## What you can change

The dashboard shows only what matters for the codecs. Colour correction, upscaling, client-side
foveation, HDR and similar visual tuning are hidden, and so are HEVC and AV1. ALVR's own features
are unchanged: passthrough (camera), game audio and microphone, controllers and hand tracking,
face and eye tracking, and connection settings.

### Experiment controls

| Control | Where | Default | What the research measured |
|---|---|---|---|
| Streaming profile | Presets | PyroWave 4:4:4 (Recommended) | Sets every control below to a measured configuration; the other profile is ALVR H.264 (tuned) |
| Render scale | Presets | 60 % | Home: 90 fps up to 70 %; 80 % holds 90 fps only at 300 Mbps; 90-100 % drop to 72 and 45 fps and overheat |
| Refresh rate | Presets | 90 Hz | Latency falls with the frame period; 60, 72 and 90 Hz are what the headset runs |
| Codec | Video | PyroWave 4:4:4 | H.264 (4:2:0) uses the headset's hardware decoder: best 80 ms at 72 Hz |
| Bitrate | Video | 400 Mbps constant | Home, 60 %: 400 Mbps scores 44.3 dB PSNR-Y against 41.9 at 300; 300 Mbps measured 57.0 ms against 60.2, with better 1 % lows |
| Foveated encoding | Video | on; centre 0.20 x 0.178, edge ratios 3 / 4 | Sets the encoded size (1984x896 at 60 %); a wider centre costs decode time |
| Follow gaze | Video > Foveated encoding | on | Moves the full-resolution centre with the eyes, per eye |
| Transport | Video > PyroWave 4:4:4 | UDP | Each frame is sent as independent datagrams and late ones are dropped; every measured result used UDP |
| Wavelet | Video > PyroWave 4:4:4 | CDF 9/7 | CDF 5/3 was no faster at equal GPU clock and decodes only on the compute path |
| Headset decode path | Video > PyroWave 4:4:4 | Compute | 13-17 % faster to completion than the fragment path |

### Advanced / Research controls

These are collapsed under **Video > Advanced / Research controls**. The research found they move
latency between pipeline stages rather than reduce it, and some settings cause stutter. Reset
them before filing a report; the report lists any that differ from the profile.

| Control | Where | Default | What the research measured |
|---|---|---|---|
| Maximum buffering | Video > Advanced | 1.5 frames | Shallower buffering moved waiting between stages; the total stayed |
| Buffering history weight | Video > Advanced | 0.90 | ALVR's buffering smoothing |
| Server frame pacing | Video > Advanced | on | Off let the server free-run: 600 Mbps overshot to 1356 Mbps and latency reached 194 ms |
| NVENC preset, entropy coding, rate control | Video > Advanced > Encoder | P1, CAVLC, CBR | The H.264 profile's values |
| GPU adapter index | Video > Advanced | 0 | For PCs with more than one GPU |

## Run a test and send your report

In the dashboard's **Statistics** tab, while streaming, press **Run PyroWave test (60 s)**. Keep
the content you want to test on screen until the countdown ends. Then answer the optional
questions:

- how different 4:4:4 looked against H.264;
- sharpness and smoothness, from 1 to 5;
- your Wi-Fi standard, band and router model.

Press **Export PyroWave Test Report**. One file, `pyrowave-report-<id>.json`, is saved in
`Documents\PyroWave reports`. To send it, open an issue in this repository and attach the file.

**The report contains:**

- the effective settings, and any Advanced / Research controls you changed;
- GPU, driver, CPU and Windows model names and versions;
- the distributions of fps (median and 1 % low), motion-to-photon, GPU decode and decode-to-fence
  time;
- dropped, partial and skipped frames, late packets and packet loss;
- the headset's thermal status and battery temperature at the start and the end;
- your answers.

**It does not contain** IP or MAC addresses, host or user names, file paths, serial numbers or
e-mail addresses. Free text is redacted, and the export refuses to write a report in which
anything identifying remains. Reports from many testers pool into one CSV with
`python -m xrbench.beta_reports <folder>` (run from `tools/`).

## How it was measured

- **Motion-to-photon** is ALVR's own per-frame `total_pipeline_latency`: the predicted display
  time minus the time the tracking input was acquired. Its stages (game, encoder, network,
  decoder, queues, compositor) partition it exactly. A faster stage therefore only helps if the
  frame is displayed earlier; otherwise the frame waits longer later. See the
  [latency budget](captures/pyrowave-in-alvr/latency-budget.md).
- **Harness.** `tools/xrbench` runs each cell the same way. It applies the session settings and
  restarts SteamVR. It then starts the client and primes and settles the scene. Finally it
  records per-frame statistics, the headset's decoder counters, the GPU clock, thermals and the
  battery over a fixed window marked in the headset log.
- **Scene and wearer.** The Home results use static SteamVR Home with the headset unworn and its
  wear sensor covered. An unworn control once matched the worn latency. Gameplay content (motion,
  foliage, HUD text, dark scenes) has not been measured yet.
- **Replication and order.** Bitrates were alternated over three replicates. Resolution cells ran
  interleaved, so no resolution ran twice in a row. A repeated first cell bounds run-to-run
  drift, at 4 ms. The headset cools to a thermal gate between cells, and sustained runs last
  5 minutes.
- **Decode timing** is measured on the headset: GPU timestamps for decode, and submit-to-fence
  for completion.
- **Objective quality.** A bitstream tap and a lossless dump of the encoder input are paired by
  frame timestamp. Each pair is decoded with PyroWave's PC decoder and scored with ffmpeg
  (PSNR-Y, SSIM, VMAF) against that setting's own source. VMAF saturates at 96-97 and does not
  separate settings, so PSNR-Y and SSIM carry the comparison. A PSNR match is not called equal
  quality.
- **Discipline.** Every code change was written test-first (Python and Rust unit tests), and
  every patch reproduces its upstream clone byte for byte. Results carry their caveats: Home
  results are Home results.

## Research log

1. **Wired first.** Android's USB networking delivered 453 Mbps of a 3.7 Gbps cable, and a custom
   USB transport needs root. Wi-Fi reached 1,124 Mbps over TCP, so the work moved to Wi-Fi.
2. **Benchmark harness and codec ceilings.** `xrbench` sweeps ALVR configurations. Hardware H.264
   held 72 fps with 7-12 ms decode. The HEVC decoder was roughly 8x slower.
3. **Eye-tracked foveation.** Gaze from the headset drives ALVR's foveated encoding, per eye.
4. **Latency budget.** Pacing and buffering controls only moved the waiting around; at 72 Hz the
   floor is about 70 ms. Only a higher refresh rate lowers it, and 90 Hz needs decode well under
   11 ms.
5. **PyroWave in ALVR.** The PC encodes with PyroWave, and the headset decodes with Vulkan compute
   and receives frames over UDP. Decode fell from about 15 ms to about 3 ms, which made 90 Hz and
   55-60 ms possible.
6. **Decoder experiments 1-5.**
   - Reconstruction as compute rather than fragment passes cut completion time 13-17 %.
   - CDF 5/3 in FP16 was no faster at equal clock.
   - Fusing a Haar transform halved reconstruction time but cost 9-35 % more bytes on rendered
     content.
   - Fusing CDF 9/7 was slower.

   CDF 9/7 on the compute path stayed the baseline.
7. **Windows builds.** Every target builds on the Windows PC and in CI, from pinned upstream
   commits plus [`patches/`](patches/README.md). A replicated regression gate confirmed the
   switch.
8. **Home baselines and resolution matrix.** Replicated baselines, then 60-100 % of the panel at
   300 and 400 Mbps, with objective quality from frame taps.
9. **Beta.** Streaming profiles, a codec-focused dashboard and PyroWave settings in the session
   instead of environment variables. It adds automatic trust on discovery, headset telemetry and
   the exportable test report. The CI-built release, run with no research overrides, reproduced
   the operating point on Home ([beta validation](captures/beta-validation/README.md)).

Reports: [PyroWave in ALVR](captures/pyrowave-in-alvr/README.md),
[offline rate-distortion matrix](captures/pyrowave-in-alvr/rd-matrix/SUMMARY.md),
[transform research](captures/decoder-experiments/mathlab/REPORT.md), experiments
[1](captures/decoder-experiments/exp1-compute-vs-fragment/REPORT.md),
[2](captures/decoder-experiments/exp2-cdf53-fp16/REPORT.md),
[3](captures/decoder-experiments/exp3-fused-haar/REPORT.md),
[4](captures/decoder-experiments/exp4-fused-97/REPORT.md),
[5](captures/decoder-experiments/exp5-haar-worn/REPORT.md),
[UDP transport](captures/udp-transport/README.md).

## Building from source

The upstream sources are not in this repository. [`tools/ci/fetch_sources.sh`](tools/ci/fetch_sources.sh)
checks out each one at its pinned commit and applies [`patches/`](patches/README.md): ALVR
20.13.0, PyroWave, and Granite at the commit the measurements used. Each patch is cumulative: the
base plus one patch reproduces the measured tree.

- **Windows:** [`tools/windows/README.md`](tools/windows/README.md) covers the toolchain, PyroWave
  for PC and Android, the streamer (`tools/windows/build_streamer.cmd`), libpyroclient and the
  headset APK.
- **CI:** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs the tests on every push and
  builds the streamer zip and the APK from scratch, with SHA-256 sums. Tagged builds are signed
  with the beta release key, whose certificate fingerprint is in
  [`tools/beta/release-cert.sha256`](tools/beta/release-cert.sha256).
- **Tests:** from `tools/`, run `pip install -r requirements-test.txt`, then `python -m pytest -q`.

## Repository layout

| Path | What it is |
|---|---|
| `tools/xrbench/` | The benchmark harness: plans, sweeps, telemetry, thermal and battery tracking, reports, beta report pooling |
| `tools/pyroclient/` | `libpyroclient`, the PyroWave decoder inside the headset client |
| `tools/windows/`, `tools/lib/`, `tools/ci/` | Build scripts, toolchain installer, patch verification, CI source checkout |
| `tools/tests/` | Unit tests for the harness, build scripts, privacy and links |
| `patches/` | Our changes to ALVR, PyroWave and other upstream projects, against pinned commits |
| `captures/` | Reports and data from each measurement campaign |
| `tools/pyrowave_android/`, `tools/pyrowave_d3d11/`, `tools/udptest/`, `tools/quicprobe/`, `tools/nvenc_caps/`, `tools/dspprobe/` | Focused probes: standalone decode, D3D11 encode, UDP and QUIC transport, NVENC, DSP |
| `receiver/`, `receiver-xr/`, `driver/`, `runtime/` | Early experiments: custom receivers, SteamVR driver and OpenXR runtime |

## Limitations

- The beta supports the Galaxy XR only. It was tested with one PC (RTX 3090) and one network.
- The Home results come from one static scene. Motion-heavy content is unmeasured.
- Headset battery drains 20-30 % per hour while streaming, even on a charger.
- Render scales above 70 % overheat the headset.
- Discovery does not cross subnets; add the headset's IP address in the Devices tab instead.
- Hand tracking: the headset app now holds Android XR's hand-tracking permission, but hand
  skeletons and ALVR's pinch gestures have not been checked with the headset worn. Pinch
  gestures stay off by default, as in upstream ALVR (**Headset > Controllers > Hand tracking
  interaction**).
- The dashboard can restart SteamVR itself only while its window is open.
- Next research steps:
  - a worn comparison of 60 % and 70 %;
  - played gameplay content;
  - pooled reports from other PCs and networks.

## License and credits

MIT, see [`LICENSE`](LICENSE). This work builds on:

- [ALVR](https://github.com/alvr-org/ALVR) (MIT);
- [PyroWave](https://github.com/Themaister/pyrowave) and
  [Granite](https://github.com/Themaister/Granite) (MIT);
- the [OpenXR SDK](https://github.com/KhronosGroup/OpenXR-SDK-Source) (Apache-2.0; see
  [`NOTICE`](NOTICE)).
