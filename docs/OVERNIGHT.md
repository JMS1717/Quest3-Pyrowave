# Unattended Quest 3 development

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
4. Test batched dequantization first. The development `.15` pair also adds an opt-in
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

The tracking-prompt recovery route must be proved on the actual headset before
claiming unattended operation is reliable. A disconnected cable or unrecoverable
headset failure can prevent further live tests; source work and cloud builds can
continue. In-headset quality judgment and optical motion-to-photon latency still
require later human/hardware acceptance.
