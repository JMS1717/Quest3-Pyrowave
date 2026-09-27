# Beta validation: the CI-built release on the headset

**Static SteamVR Home results only.** The headset was unworn with its wear sensor covered, and
the cells ran unattended. This report tests the beta **as a tester gets it**, not the research
builds.

## Test setup

- **Build.** The streamer zip and the release-signed APK came from the manual CI run. Both
  matched their published SHA-256 sums, and the APK's certificate matched
  [`tools/beta/release-cert.sha256`](../../tools/beta/release-cert.sha256). The files were copied
  unchanged over the bench runtime, and every copy was verified by hash.
- **Fresh session.** The old `session.json` was set aside, so the server started from its
  defaults like a new install.
- **No research overrides.** The headset's `debug.xrwired.*` properties were cleared, and the
  PyroWave launcher sets no PyroWave environment variables. Every cell ran with
  `settings_only`, so the session alone configured the stream
  (`python -m xrbench.sweep --plan beta-validation`).

## A fresh install streams the recommended profile

With no `session.json`, the server's defaults are the **PyroWave 4:4:4 (Recommended)** profile:

- codec PyroWave, 400 Mbps constant, 90 Hz, 2131x2304 per eye;
- UDP, CDF 9/7, compute decode path;
- foveation 0.20 x 0.178 with edge ratios 3 / 4, following gaze;
- automatic trust, ALVR stream over TCP, 65000-byte packets.

The OpenVR block the C++ encoder reads is rebuilt when a headset first connects. If it changed,
the driver restarts once before streaming, as stock ALVR does for a codec change.

## Cells

Per-cell data is in [`cells.csv`](cells.csv), produced by `python -m xrbench.matrix`. The cells
ran in the order below; the headset warmed from 67 C to 81.5 C across the run.

| Cell | Setting changed | Motion-to-photon (mean) | fps median / 1 % low | GPU decode / fence | Dropped | GPU clock |
|---|---|---|---|---|---|---|
| BV-PW-1 | recommended profile | 58.2 ms | 90 / 64 | 3.1 / 4.7 ms | 10 | 735 MHz |
| BV-PW-2 | recommended profile | 60.6 ms | 90 / 42 | 3.0 / 4.7 ms | 54 | 738 MHz |
| BV-PW-3 | recommended profile | 66.0 ms | 90 / 26 | 3.8 / 5.8 ms | 129 | 622 MHz |
| BV-H264 | ALVR H.264 (tuned) profile | 85.4 ms | 72 / 65 | hardware decoder | n/a | 600 MHz |
| BV-TCP | PyroWave transport: TCP | 56.8 ms | 90 / 90 | 3.6 / 5.5 ms* | 0* | 737 MHz |
| BV-53 | wavelet: CDF 5/3 | 60.6 ms | 90 / 45 | 3.5 / 5.1 ms | 65 | 735 MHz |
| BV-FRAG | headset decode path: fragment | 60.4 ms | 90 / 44 | 3.1 / 5.3 ms | 59 | 593 MHz |
| BV-70 | render scale: 70 % | 69.5 ms | 90 / 42 | 5.9 / 8.4 ms | 76 | 625 MHz |

\* The harness reads decode timings from the UDP receiver's log lines; the TCP path does not
write them. The TCP figures come from the beta's own test report (below), which reads the
headset telemetry instead.

## Reading

- **The shipped build reproduces the operating point.** The recommended profile's median over
  three cells is 60.6 ms, at 90 fps, 3.1 ms decode and 4.7 ms fence. The research baseline on
  the research builds was 60.2 ms, 3.3 ms and 5.1 ms.
- **The third replicate ran hot.** The headset GPU clock fell from 735 to 622 MHz. Decode rose
  to 3.8 ms and 1 % lows fell, as in earlier sustained runs. The 70 % cell (625 MHz) and the
  fragment-path cell (593 MHz) also ran at reduced clock. Their numbers are warm-headset numbers
  and do not compare directly with the matrix, where 70 % measured 60.3 ms at about 730 MHz.
- **The tuned H.264 profile** streamed at 72 fps and 85.4 ms, near the research range of
  80.0-84.8 ms. It ran on the warmest part of the run and on Home rather than the original
  test scene.
- **Every PyroWave toggle took effect through the setting alone.** In each cell the headset
  logged a `PyroWave effective config (headset)` line with the transport, wavelet and encoded
  size that cell set. Its decoder reported `decode path compute (setting)`, or
  `decode path fragment (setting)` for BV-FRAG, rather than `forced`.
- **TCP was the smoothest cell here:** 1 % low of 90 fps, nothing dropped and 56.8 ms. It carries
  every frame whole and retransmits, where UDP drops late datagrams. This is one Home cell on one
  network; it does not show that TCP is the better default, and it is a good first comparison
  for testers.

## The test report against the harness

The dashboard's report builder was run on the events the harness recorded (the opt-in test
`report_from_a_recorded_cell` in the ALVR patch). Its output is in
[`test-report-BV-PW-1.json`](test-report-BV-PW-1.json),
[`test-report-BV-TCP.json`](test-report-BV-TCP.json) and
[`test-report-BV-H264.json`](test-report-BV-H264.json).

- **BV-PW-1:**
  - Motion-to-photon 58.23 ms and 1 % low 63.79 are identical to the harness.
  - Decode 3.13 ms and fence 4.69 ms, against the harness's 3.135 and 4.705.
  - Dropped 14 against 10: the report counts frames by one-second telemetry snapshots, and the
    harness by log markers.
- **Profile detection:**
  - Recognised "PyroWave 4:4:4 (Recommended)" for BV-PW-1 and "ALVR H.264 (tuned)" for BV-H264.
  - Reported "custom" for BV-TCP, the only cell whose settings left a profile.
- **Privacy:** none of the three reports contains an address, host name, path or serial.

## Worn check

One tester wore the headset in SteamVR Home on the CI-built beta, with the dashboard and the
PyroWave profile set as a tester would set them.

- **Eye gaze:** tracked in all 5406 frames of a 20 s window; yaw -15 to +17 degrees, pitch -16 to
  +26 degrees.
- **Head:** all 5406 samples tracked; the position spanned 129 cm.
- **Passthrough:** switched on and off live from the dashboard. The headset showed the camera view,
  then Home again.
- **Hand tracking:** no hand skeleton arrived at all. The app declared only Meta's
  hand-tracking permission, not Android XR's `android.permission.HAND_TRACKING`. Fixed (below).
- **70 % render scale at 400 Mbps**, set by the tester: 5.4 ms GPU decode and 7.4 ms
  decode-to-display on the headset, against 3.1 and 4.7 ms at 60 % (one reading from the headset
  log, not a measured cell).
- **SteamVR render resolution at 150 %** (supersampling before ALVR scales to the encoded size):
  98 % of frames arrived complete (21200 of 21600) and GPU decode stayed at 5.1-5.5 ms.
- **Black screen after a reconnect:** the headset slept once while its wearer used the PC, and
  after it woke the stream stayed black until the app was relaunched. A SteamVR restart did the
  same. Fixed (below).

## Fixes after the worn check

- **Reconnect.** The client asks the server for the PyroWave decoder configuration only while no
  decoder callback is set, and nothing cleared the previous stream's callback, so a reconnect
  within the same app session never asked. The harness relaunches the app before every cell,
  which is why no cell showed it. The callback is now dropped when a stream ends.
- **Hand tracking.** The app declares `android.permission.HAND_TRACKING` and requests it on the
  Galaxy XR, as it does for gaze.

Recovery with the fixed app, reconnecting without relaunching it (same process throughout):

| Event | Stream restarts | Frames back after |
|---|---|---|
| Headset slept 15 s, then woke | 1 | 16 s after waking |
| SteamVR stopped and started again | 1 | 14 s after SteamVR started |

Both showed live Home frames on the headset afterwards. The installed app holds the hand-tracking
permission; hand skeletons have not been checked worn since the fix.

## The fixed build, re-run

The same eight cells were run again on the fixed app, sensor covered, the morning after the first
run. The headset had been worn and charging for about an hour beforehand, and every cell ended at
thermal status 3 (status 2 in the first run). Per-cell data is in
[`cells-fixed-build.csv`](cells-fixed-build.csv), and the permission A/B below in
[`hand-tracking-ab.csv`](hand-tracking-ab.csv).

| Cell | First run | Fixed build | fps median / 1 % low | GPU decode / fence | Dropped | Late packets | GPU clock |
|---|---|---|---|---|---|---|---|
| BV-PW-1 | 58.2 ms | 60.7 ms | 90 / 43 | 3.5 / 5.0 ms | 105 | 34618 | 737 MHz |
| BV-PW-2 | 60.6 ms | 61.1 ms | 90 / 47 | 3.9 / 5.7 ms | 43 | 17545 | 613 MHz |
| BV-PW-3 | 66.0 ms | 59.3 ms | 90 / 53 | 3.4 / 5.0 ms | 24 | 4278 | 735 MHz |
| BV-H264 | 85.4 ms | 98.2 ms | 72 / 51 | hardware decoder | n/a | n/a | 595 MHz |
| BV-TCP | 56.8 ms | 62.1 ms | 90 / 70 | n/a | n/a | n/a | 626 MHz |
| BV-53 | 60.6 ms | 59.5 ms | 90 / 47 | 3.6 / 5.4 ms | 70 | 29672 | 627 MHz |
| BV-FRAG | 60.4 ms | 62.3 ms | 90 / 44 | 3.2 / 5.6 ms | 60 | 25617 | 595 MHz |
| BV-70 | 69.5 ms | 60.5 ms | 90 / 47 | 4.6 / 6.6 ms | 13 | 2330 | 726 MHz |

- **PyroWave is unchanged:** a median of 60.7 ms over the three recommended-profile cells, against
  60.6 ms in the first run. The 70 % cell ran at full GPU clock this time, and measured 60.5 ms.
- **H.264 was slower,** 98.2 ms against 85.4 ms. The whole difference is in the headset's decoder
  queue (20.5 ms against 11.7 ms); encode and decode times were unchanged.
- **The hand-tracking permission costs nothing measurable.** It is the only fix that changes
  steady-state work on the headset, so it was revoked and granted in turn, over four interleaved
  pairs of cells:

  | Hand-tracking permission | H.264 | PyroWave recommended |
  |---|---|---|
  | revoked | 91.2 and 92.4 ms | 60.8 and 62.5 ms |
  | granted | 86.9 and 91.8 ms | 61.5 and 58.2 ms |

  H.264 stayed above the first run whether the permission was granted or not. It tracks the
  hotter headset, but that is an association, not a tested cause.
- **Late packets vary more than tenfold between cells in both runs** (1378 to 38298). The Wi-Fi
  link is the least controlled part of these measurements.

## Not checked

- **Microphone.** It is off by default on Windows, as in upstream ALVR, because it needs a
  virtual audio cable. The headset opened its game-audio output stream normally.
- Galaxy XR controllers: not used in the worn check.
- Hand skeletons and pinch gestures after the permission fix.
- The subjective 4:4:4 comparison, and the test report run end to end from the dashboard window.
  Only its unit tests and the recorded-cell builds above cover the report.
