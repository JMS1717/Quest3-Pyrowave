# alpha.6: native-resolution USB preview

This release packages matching `.13` Quest APK and Windows server binaries from
[source 1508dd7](https://github.com/JMS1717/Quest3-Pyrowave/commit/1508dd7efe9f454aacc6c3eb305b76a7bbba3b7c),
built by [Actions run 36933424039](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/36933424039).
Android, Windows and regression jobs passed. The release tag also includes the
reviewed findings and setup documentation; it does not substitute different binaries.

Download `Quest3-Pyrowave-dev.apk`, `Quest3-Pyrowave-Windows.zip` and
`SHA256SUMS.txt` from the release. Windows includes upstream license notices;
`Quest3-Pyrowave-Android-LICENSES.zip` provides Android dependency notices.
`BUILD-METADATA.json` records the source revision, workflow and binary versions.
The stable APK certificate is supplied separately; keep APK/server versions matched.

## Launch and configure

1. Stop SteamVR before replacing this fork's server. Extract the ZIP into a new
   folder, preserving your previous server and session configuration for rollback.
   Run `ALVR Dashboard.exe` from the extracted folder. Use its driver controls to
   register this server and enable its SteamVR add-on. Keep Virtual Desktop installed.
   Use only one active ALVR driver; see [switching with Virtual Desktop](BUILD.md#switching-between-pyrowave-and-virtual-desktop).
2. Install the APK with `adb install -r Quest3-Pyrowave-dev.apk`. Open
   **Quest3 PyroWave** from Quest **Unknown Sources**, then trust it in the dashboard.
   An older disposable-key APK may require uninstalling this app once; save its
   configuration first. Do not uninstall Virtual Desktop or stock ALVR.
3. For the tested experimental recipe, select PyroWave, **Haar**, **Compute**,
   **4:2:0**, **2080 × 2208 per eye**, **120 Hz**, manual **1000 Mbps**, and no
   foveated encoding. Set both encoded and emulated per-eye sizes explicitly.
   Leave experimental parallel workers at one. The bitrate slider and Auto toggle
   are described in [BITRATE.md](BITRATE.md). Higher bitrate is not a fix for an
   over-budget decoder; 2000 Mbps regressed the measured .11 stream.
4. For USB, use **Devices → Wired Connection** with USB debugging enabled, and
   TCP. Confirm the client peer is loopback and the overlay reports USB/TCP.
   [USB.md](USB.md) explains transport and bandwidth limitations. Wireless TCP/UDP
   are separate candidates; these FPS measurements do not validate them at this rate.
5. The upright 3D performance overlay starts enabled. Click both thumbsticks
   together, release both, then click again to toggle it. Choose **Quest 3 Touch
   Plus** controller emulation for older saved sessions. Games can select their own meshes.

## Measured result and remaining limits

Native-resolution .13 delivered **99.7–100.7 fresh submitted FPS** in separate
45-second fixed-chart USB captures with the runtime at 120 Hz. Median instantaneous
FPS was 120, but p1 was about 60 and timestamp-gap p95 about 16.7 ms. **Sustained
120 fresh FPS is not achieved.** Fresh submission rate is a telemetry measurement,
not an optical display counter. ALVR estimated latency was 65.6–68.1 ms, not optical
motion-to-photon. No superiority over Virtual Desktop is established.

These captures used explicitly requested GPU level 7 / CPU level 6, with reported
GPU clock 690 MHz and battery temperature 44–45°C. The APK does not apply these
properties. Other firmware, ordinary performance requests and long thermal runs
can behave differently. Battery temperature is not GPU die temperature.

Median native completion was 9.14–9.20 ms against the 8.33 ms frame budget:
GPU decode 4.54–4.58 ms, conversion 2.57 ms, CPU recording 0.84 ms, and submit/wait
8.28–8.33 ms. Those are separately measured intervals, not additive percentile
accounting. See [DECODE-2026-10-01.json](../results/DECODE-2026-10-01.json).

The limited asymmetric fused-kernel readbacks stayed within one code value and
0.00014 dB source-PSNR loss. Live fused Haar nevertheless fell to **71.8 fresh FPS**
and 13.24 ms completion, so it remains off. Eye-image reuse showed no clear FPS
gain in this short comparison; its `debug.q3pw.repeat_render=1` optout remains.
Both require client restart when changed. No frame-completion fences or hardware
buffer leases were removed.

Keep **4:2:0 as the default**. Matched .11 chart tests gave about 100 fresh FPS at
1000 Mbps versus 66 FPS with 4:4:4 at 2000 Mbps. Full chroma strengthened colored
strokes, but its decode, pacing and latency cost failed the promotion criterion.
See [CHROMA.md](CHROMA.md). Full chroma remains optional; in-headset and broad
gameplay quality acceptance remain pending.

144/207 Hz were accepted by this headset's runtime probes; they are not sustained
streaming results. 240 Hz was rejected in the tested setup and fails gracefully.
Full 120 FPS, thermal endurance, real game comparisons, and extended refresh
remain research goals. Haar quality is experimental and not proven equivalent to
CDF 9/7, HEVC, H.264 or AV1.

## Rollback

Stop SteamVR, unregister this release's server, and re-register the preserved
previous server with its matching APK. Never mix .13's updated timing/control ABI
with an older client/server. Restore any manually changed debug properties to
their saved values; no persistent clock/network override is part of installation.
For Virtual Desktop, disable the ALVR add-on and launch your existing VD setup.
