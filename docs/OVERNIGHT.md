# Unattended Quest 3 development

**Current status: resumed under the owner's explicit overnight authorization.**
Hardware access and automatic own-app recovery were verified again after the
Virtual Desktop pause. Independent restoration enforces a bounded five-hour session;
credit checks follow the owner's requested sparse schedule. Private budget/account
state is kept outside the repository. An earlier pause remains documented for rollback.

The owner authorized overnight headset testing on October 1, 2026. This supersedes
the older offline-only instruction for this session; it does not grant other users
permission to alter devices automatically. Keep the PC powered, Codex open, and the
Quest connected by USB. Device identifiers, screenshots, signing keys and runtime
backups remain in the private sibling workspace.

1. Verify the connected Quest, matching APK/server and actual streaming events.
   Use a temporary proximity override and wake-up key, with an independent deadline
   restoring physical proximity control. Preserve Windows power-plan settings.
2. Establish 2080 × 2208 per eye, 120 Hz, Haar compute Vulkan decode, 4:2:0,
   no foveated encoding, native ALVR USB/TCP, and manual 1000 Mbps as the baseline.
3. Screen one change at a time for **15 seconds after 3 seconds settling**. Run
   sequential baseline/candidate/baseline controls with the same scene, thermal
   state and GPU clock. Reject missing frames, configuration changes and restarts.
4. Batching showed no live gain and stays off. The development `.15` pair adds an opt-in
   direct AHB-to-OpenXR eye copy, preserving Vulkan decoding, both source-buffer
   leases and `glFinish`. It is restricted to equal encoded/output eye resolution,
   SDR, no foveation/upscaling, no passthrough, and identity client reprojection.
   Compilation/FBO setup failures use the existing staging path.
5. Validate the asymmetric image chart before timing a new copy path. Check both
   eyes, vertical orientation, colored text and gamma. `debug.q3pw.direct_eye_copy=1`
   opts in; absent/0 restores staging. `debug.q3pw.direct_flip_y=0` is a diagnostic
   orientation control; the candidate defaults to a vertical flip matching the
   existing staging-to-WGPU path. Neither is a promoted default.
6. Use `.15` telemetry to distinguish CPU wall time in OpenXR acquire/wait,
   the renderer call including its copy-completion fence, and OpenXR release.
   Actual direct/staging copy counters must establish which path ran. These are
   not GPU timer-query measurements. `.15` requires matching client/server.
7. Roll back after image corruption, a crash, substantial fresh-FPS/pacing or
   latency regression, or unknown copy-path activation. Preserve all binaries.
   Do not increase bitrate to compensate for a measured GPU/completion bottleneck.
8. Use longer repeated runs only for promising candidates, then thermal endurance
   after reaching the target. Runtime-accepted 120 Hz is not sustained fresh 120 FPS.
   Keep 4:2:0 as the default; current 4:4:4 evidence fails the performance gate.
9. Recover known interruptions with bounded retries. Wake/relaunch our own app and
   restore its USB route; handle the observed tracking prompt only through a
   verified recovery method. Do not blindly tap unknown dialogs or disable thermal
   protections. If recovery fails, continue source/CI work instead of asking the
   sleeping owner repeatedly. Do not reboot the PC or change network configuration.
10. Pause device workloads on Android severe thermal status or low battery; allow
    cooling and recovery. Restore temporary settings and physical proximity at the
    morning deadline. Leave a measured report, artifact hashes, current baseline
    and rollback instructions. Commit/push only as JMS1717.

Unworn tracking recovery was verified on this headset: temporarily set
`debug.oculus.guardian_pause=1`, refresh the bounded proximity override, send the
wake-up key and launch our app. An ADB screenshot and fresh streaming events
confirmed the prompt cleared. The private supervisor keeps the PC awake through
Windows' execution-state API, pins ADB to this Quest, monitors temperature/battery,
and stops workloads on Android severe thermal status, battery below 20%, or battery
temperature at least 48°C. Battery temperature is not GPU die temperature. A
separate process restores saved properties with readback and physical proximity
at the saved five-hour deadline or earlier stop marker. It stops our app before
restoring properties, because XR teardown can overwrite an earlier readback.
Guardian is paused only for stationary unattended
tests; normal boundary behavior is restored before later play. Windows power-plan
and network settings are unchanged.

A second startup issue was reproduced: a resumed unworn session sometimes retained
the probe's 72-Hz property and reported only 72/80 Hz. Stop the app first, allow
its old XR session to finish closing, then request `debug.oculus.refreshRate=120`
before relaunch. Closing the old session can overwrite an earlier property request.
This sequence recovered the headset to OpenXR 120 Hz
with an 8,333,333-ns frame period. This property alone is not a capability test;
the request/frequency/frame-period gate remains required. Its original value is
included in the independent morning restoration. The supervisor avoids app
restarts while experiment locks are held and backs off between recovery attempts.

A disconnected cable or unrecoverable
headset failure can prevent further live tests; source work and cloud builds can
continue. In-headset quality judgment and optical motion-to-photon latency still
require later human/hardware acceptance.

## Stretch target and next bottlenecks

First establish sustained fresh native 120 FPS. Then investigate **3072 × 3216
pixels per eye at runtime-supported 207 Hz**, with **15–20 ms idealized
motion-to-photon** as a research target. That is about **3.71 times** the pixel
rate of 2080 × 2208 at 120 Hz and only **4.83 ms per frame**. Attainability is not
established. Mode requests must fail gracefully when unsupported. Separate
idealized pipeline estimates from ALVR telemetry and actual optical latency.

Prioritize the measured eye-copy completion stall, conversion and decoder/render
concurrency. Any asynchronous copy must retain both the hardware-buffer lease and
imported-image storage until completion, count completed copies separately from
queued work, and preserve a synchronous fallback. Research primary OpenXR/EGL/GL
specifications and relevant codec/GPU papers before changing these contracts.

A fast Windows restart also exposed the dashboard's fatal bind on a temporarily
occupied port. The private supervisor now permits bounded server recovery only
when this project's driver is registered/enabled and no benchmark lock is held;
it waits for server exit and a bindable port before relaunching. A source-level
fallible bind/retry fix remains pending. Virtual Desktop's service/driver and all
network settings remain preserved.
