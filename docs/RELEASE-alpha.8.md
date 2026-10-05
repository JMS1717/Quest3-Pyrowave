# Proposed v0.1.0-alpha.8 — native-frame pacing and usability preview

This prerelease packages the reviewed `.51` integration. Retain alpha.7 for
rollback. It improves usability, pacing and safety; sustained 120 fresh FPS and
an advantage over Virtual Desktop remain unproven.

## Changes since alpha.7

- Bounded fresh-frame selection: the default waits up to half a frame while a
  decode is active (4 ms at 120 Hz). Interleaved Quest screens recorded roughly
  4–8 more distinct target frames/s than no wait. See
  [freshness evidence](https://github.com/JMS1717/Quest3-Pyrowave/blob/main/docs/FRESHNESS.md).
- Automatic LOW decode queue priority for 4:2:0 at <=120 Hz when supported,
  with a default-priority fallback. Measured 120 Hz screens improved target
  delivery and reduced eye-copy stalls; the policy avoids applying that rule
  at 144 Hz, where LOW regressed delivery. See
  [priority evidence](https://github.com/JMS1717/Quest3-Pyrowave/blob/main/docs/DECODE-PRIORITY.md).
- Independent SteamVR source and encoded sizes. Request 3072 x 3216 per eye
  (aligned to 3072 x 3232) on PC while retaining 2080 x 2208 Quest decode.
  Actual game render size still depends on SteamVR/game settings. See
  [resolution controls](https://github.com/JMS1717/Quest3-Pyrowave/blob/main/docs/RENDER-ENCODE-RESOLUTION.md).
- Later native-buffer lifetime, descriptor-ownership and Rust unwind cleanup
  fixes; production publication race tests and bounded diagnostic tooling.
- Clearer USB verification, bitrate math, Auto bitrate guidance, overlay override
  troubleshooting, OpenXR game-launch help and rollback documentation.
- Updated README, engineering handoff and sanitized, reproducible findings.

## Default behavior and recommended comparisons

Fresh-install defaults remain the 400 Mbps /72 Hz conservative candidate,
4:2:0, TCP, full-frame encoding and Quest 3 Auto -> Compute. Existing saved
settings are preserved; installing a new build does not reset them to this
candidate. This starting profile is not universally live-validated.

The screened experimental recipe is 2080 x 2208 per eye, confirmed 120 Hz,
1000 Mbps, Haar/Compute, 4:2:0, no foveated encoding, USB/TCP and one worker.
Direct eye copying remains an explicit opt-in. The normal decode path is
synchronous. Start from matching binaries and follow the existing
[setup guide](https://github.com/JMS1717/Quest3-Pyrowave/blob/main/docs/BUILD.md)
and [USB guide](https://github.com/JMS1717/Quest3-Pyrowave/blob/main/docs/USB.md).

To install, stop SteamVR, extract the new server into a separate folder and run
its dashboard. Install the matching APK, open Quest3 PyroWave from Unknown
Sources, trust the headset and register this server. Keep the previous pair.
The overlay starts enabled; click both thumbsticks together, release both, then
click again to toggle. An old forced-visible benchmark override must be cleared
as described in the [overlay guide](https://github.com/JMS1717/Quest3-Pyrowave/blob/main/docs/OVERLAY.md).

For the optional native-resolution direct-eye comparison, explicitly enable the
path on your connected Quest and reopen the app after these commands:

```powershell
adb shell setprop debug.q3pw.decode_workers 1
adb shell setprop debug.q3pw.direct_eye_copy 1
adb shell am force-stop io.github.jms1717.quest3pyrowave
```

Use SDR, matching encoded/output eye sizes, no client upscaling or foveation and
no passthrough for this baseline. Larger PC source rendering can remain separate.
Verify actual direct-copy counters rather than assuming the property activated
the path. To return to staging, set `debug.q3pw.direct_eye_copy` to `0` and reopen
the app. Restore any other manually changed debug properties to their original
values; an APK update does not clear existing research overrides.

Publication notifications, prerecording, experimental ready/release fences,
asynchronous eye-copy completion and Surface presentation remain off by default.
Keep stage probes off for ordinary play. Light foveated encoding and 4:4:4 are
optional quality experiments, not new release defaults. Do not set 2000 Mbps
by default: recorded extra payload did not establish better delivery or latency.

## Explicit exclusions

- Haar H2 (`debug.q3pw.haar_h2`) is excluded due to the R16F/R8 shader/view defect.
- Final-color fusion (`debug.q3pw.fuse_color`) is excluded from this `.51` pair.
  Its branch is preserved separately for exact-pixel and setting-lifetime tests.
- No promotion of the rejected 6 ms selection wait or other neutral experiments.
- No 207/240 Hz, high-resolution or 15–20 ms motion-to-photon performance promise.
- No automatic installation, SteamVR/driver switch, headset recovery override,
  thermal override or system/network change.

## What the evidence supports

Later short native120 controls delivered approximately 117 distinct targets/s,
while the general client FPS counter was around120. A `.50` stationary window
reported GPU decode p50 5.90 ms, conversion p50 0.77 ms and native fence p50
7.98 ms. These are different intervals, not additive latency components.
Earlier pacing screens still had p1 near60. See the
[whole-stack scorecard](https://github.com/JMS1717/Quest3-Pyrowave/blob/main/docs/WHOLE-STACK-SCORECARD.md).

The alpha.7 direct-copy screens were around104 fresh submissions/s. Those and
later117/s captures differ in session, clocks and conditions; do not advertise
that cross-version difference as a controlled percentage gain. The bounded-wait
and priority comparisons above are the relevant controlled evidence.

Sustained fresh120, broad game compatibility, continuous pose/image acceptance,
thermal endurance, optical FPS and motion-to-photon latency remain unmet or
unverified. Screenshot/GPU readback checks do not replace human in-headset quality
acceptance. No latency or image-quality superiority over Virtual Desktop is proven.

## Release assets

1. `Quest3-Pyrowave-dev.apk` — stable-signed main build, protocol `.51`.
2. `Quest3-Pyrowave-Windows.zip` — matching server/dashboard, with notices.
3. `Quest3-Pyrowave-Android-LICENSES.zip` — Android dependency notices.
4. `APK-CERTIFICATE.txt` — public signing fingerprint, matching alpha.7.
5. `BUILD-METADATA.json` — exact build source/run, protocol, artifact hashes,
   completed CI checks and limitations.
6. `SHA256SUMS.txt` — checksums of the release assets above.

Keep raw captures, signing keys, complete session objects and machine-specific
configuration out of the release. Native standalone test executables stay in
Actions artifacts; they are not needed in the ordinary player download.

## Build provenance and validation

Matching binaries come from source `3d87fd31fc993615879cca3f297958b0c66b339f`,
[main Actions run 37330983265](https://github.com/JMS1717/Quest3-Pyrowave/actions/runs/37330983265).
All four jobs passed: portable regressions, production publication tests,
Android client and Windows server. Packaging checks verified the artifact hashes,
matching `.51` protocol, APK native libraries, public signing fingerprint and
bundled license notices. The release tag adds documentation only; exact source,
tag commit and asset hashes are recorded in `BUILD-METADATA.json`.

The stable APK signing fingerprint matches alpha.7. Users of that stable build
can use the normal Android update path. Temporary-key PR APKs can differ; consult
[signing guidance](https://github.com/JMS1717/Quest3-Pyrowave/blob/main/docs/BUILD.md#android-apk)
before switching. Always replace the APK and server as a matching pair.

No new headset installation or live test was performed to publish this release.
Historical device results are scoped to their recorded source and configuration;
cloud build and artifact checks are not fresh hardware acceptance. Sustained and
human in-headset acceptance remain separate. Preserve alpha.7 for rollback and
follow the [Virtual Desktop switching guide](https://github.com/JMS1717/Quest3-Pyrowave/blob/main/docs/BUILD.md#switching-between-pyrowave-and-virtual-desktop).

Upstream credits and dependency licenses are preserved in the packaged notices
and [NOTICE](https://github.com/JMS1717/Quest3-Pyrowave/blob/main/NOTICE).

[![Support development](https://img.shields.io/badge/Support_development-PayPal-0070BA?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/jasonselsley)
