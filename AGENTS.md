# Development isolation

Preserve the owner's Virtual Desktop and SteamVR setup. Without current explicit
hardware authorization, continue development without interrupting PCVR.

- Work only on source files, documentation, lightweight CPU checks, and GitHub Actions builds.
- Do not use ADB, install APKs, launch VR applications, run GPU or live streaming benchmarks,
  restart SteamVR, register drivers, or change headset, system, network, or SteamVR settings
  until the owner explicitly permits hardware testing again. Direct owner
  authorization for remote recovery/unattended testing supersedes this restriction
  for that work; automatic goal continuations do not grant new authorization.
- Do not run heavy local builds while the owner is playing. Build on GitHub Actions.
- Preserve Virtual Desktop's registration and service. Verify current driver state
  rather than assuming an old isolation snapshot is still current. Register/enable
  the project driver only within explicitly authorized hardware work.
- Commit and push as JMS1717, using 43321848+JMS1717@users.noreply.github.com.
- Never commit private captures, session configurations, device identifiers, or signing keys.
- State separately whether a rate is accepted by the runtime, meets a standalone decode
  budget, and is sustained in live VR. Unverified presets must say they are candidates or experiments.

Local development inputs are under the sibling `workspace` directory. Do not deploy its
build outputs to an active PCVR installation without hardware authorization.

During authorized unattended work, maintain device-pinned recovery, experiment
locks, thermal/battery holds, original-setting snapshots and an independent restorer.
An agent-selected watchdog deadline is an operational safety lease, not a new human
permission boundary. Renew it while continuing authorized work only after fresh
device/health/worker checks, preserving original snapshots. Never silently extend
a deadline explicitly set by the owner. On stop, lease failure or unrecoverable
recovery, restore temporary settings with readback and physical proximity behavior.
Do not repeatedly request manual headset action when authorized ADB recovery works.
