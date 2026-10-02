# alpha.7: optional direct eye copy

Matching `.15` Android and Windows binaries come from
[ba415a8](https://github.com/JMS1717/Quest3-Pyrowave/commit/ba415a8f21d25db3c8a1fb7a077a454dc189d9b6),
with all build/regression jobs passing
[run 36942639620](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/36942639620).
The release tag adds setup documentation and reviewed findings. Never mix this
client/server protocol with older versions. Preserve your previous matching pair.

Download the APK, Windows ZIP and `SHA256SUMS.txt` together. Android dependency
notices, the stable development APK certificate and `BUILD-METADATA.json` are
separate assets; Windows notices are in the ZIP. This release does not modify
your running installation automatically.

## Setup and experimental opt-in

Follow [the launch/USB/overlay setup](RELEASE-alpha.6.md#launch-and-configure),
using **both alpha.7 binaries**. The screened recipe is 2080 × 2208 encoded and
emulated pixels per eye, 120 Hz, PyroWave Haar / Compute, 4:2:0, no foveated
encoding, one decoder worker, manual 1000 Mbps and native ALVR USB/TCP.
Keep 4:2:0; additional bandwidth does not resolve a GPU completion bottleneck.

Direct eye copying is **off by default**. To try it on an authorized Quest:

```powershell
adb shell setprop debug.q3pw.direct_eye_copy 1
adb shell am force-stop io.github.jms1717.quest3pyrowave
```

Reopen **Quest3 PyroWave** from Unknown Sources. The overlay starts enabled;
click both thumbsticks together to toggle it. The benchmark reports actual
direct/staging copy counters: check these rather than assuming the property ran.
If native 120 Hz is rejected after restarting, allow the previous XR session to
close before requesting the refresh rate; verify the app's accepted runtime
frequency and frame period. A property value alone is not evidence of support.

The direct path requires matching source/eye resolution, SDR targets, no foveated
encoding/upscaling, no passthrough and an identity warp. Other configurations
fall back to staging. Vulkan compute still decodes PyroWave; GLES imports its
hardware buffer and copies directly into both OpenXR eye textures. Source-buffer
leases and synchronous completion remain protected.

## Improvement and limits

Sequential same-scene **15-second** screens after 3 seconds settling delivered:

| Path | Fresh submissions FPS | Completion p50 ms | GPU MHz |
| --- | ---: | ---: | ---: |
| Staging control 1 | 98.67 | 9.40 | 690 |
| Direct 1 | 103.71 | 8.72 | 640 |
| Staging control 2 | 99.33 | 9.32 | 690 |
| Direct 2 | 103.63 | 8.74 | 640 |
| Direct + OpenXR BOOST, single screen | 108.19 | 8.29 | 690 |

These tests requested GPU7/CPU6; the APK does not apply these debug properties
automatically. Battery temperature was 45 °C and reported thermal status 0.
Clocks were dynamic, so this is not a controlled fixed-clock comparison. BOOST
was not replicated or endurance-validated and is not a release default.
The ordinary staging defaults do not claim a performance improvement over alpha.6.

Actual copy counters confirmed activation. Basic private captures showed correct
eyes, upright orientation and matching dominant colors; full image-reference and
human in-headset acceptance remain pending. p1 nominal FPS remained about 60 and
p95 timestamp gaps about 16.7 ms. **Sustained fresh 120 FPS remains unmet.**
Fresh submissions are not optical displayed frames; ALVR pipeline latency is
not optical motion-to-photon latency. No advantage over Virtual Desktop, sustained
thermal behavior or broad gameplay quality has been established. See
[full measurements](../results/DIRECT-EYE-LIVE-2026-10-01.json).

The `.14` batch experiment is included but remains off: matched live screens
showed no improvement. Fused Haar and experimental FP16 remain unpromoted.
144/207 Hz capability acceptance does not establish streaming performance;
240 Hz was rejected on the tested setup. Larger resolution targets remain research.

## Rollback and Virtual Desktop

To turn direct copy off, set `debug.q3pw.direct_eye_copy` to `0`, force-stop this
app and reopen it. Restore any manually changed performance properties to their
saved original values. To revert the entire release, stop SteamVR, unregister
this server, register the preserved alpha.6 server and reinstall its matching
APK. Do not mix `.13` and `.15` binaries.

For Virtual Desktop, stop this app, disable this fork's wired autolaunch, disable
the ALVR SteamVR add-on and unregister this server. Preserve VD's driver/service.
Connect to the PC in Virtual Desktop before choosing **Launch SteamVR**. See
[driver switching](BUILD.md#switching-between-pyrowave-and-virtual-desktop).
The maintainer's hardware tests and recovery automation are currently paused
for Virtual Desktop; this release does not restart them.
