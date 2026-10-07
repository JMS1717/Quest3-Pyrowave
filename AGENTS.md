# Development isolation

Preserve the owner's Virtual Desktop and SteamVR setup. Without current explicit
hardware authorization, continue development without interrupting PCVR.

- Work only on source files, documentation, lightweight CPU checks, and GitHub Actions builds.
  which refuses to run while SteamVR is up; reviewed CI builds remain the release evidence.
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

## Continuation and workflow freedom

Read `docs/HANDOFF.md` when taking over development. The owner explicitly allows
the next developer to improve test design, duration, build workflow and architecture.
Preserve safety, reproducible evidence and rollback; previous scripts and experiment
sequences are not mandatory. Short screens can reject candidates but cannot prove
sustained FPS, thermal stability or optical latency.

Machine-specific continuation material is outside the repo in the sibling `handoff`
directory. Refresh actual state before hardware work; paused workers and historical
credit/deadline snapshots must not be automatically reused or resumed.
