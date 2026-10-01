# Development isolation

The owner is using Virtual Desktop and SteamVR. Continue development without interrupting PCVR.

- Work only on source files, documentation, lightweight CPU checks, and GitHub Actions builds.
- Do not use ADB, install APKs, launch VR applications, run GPU or live streaming benchmarks,
  restart SteamVR, register drivers, or change headset, system, network, or SteamVR settings
  until the owner explicitly permits hardware testing again.
- Do not run heavy local builds while the owner is playing. Build on GitHub Actions.
- Preserve Virtual Desktop's registration and service. The development driver is unregistered
  and its SteamVR add-on is disabled. Never re-enable it automatically.
- Commit and push as JMS1717, using 43321848+JMS1717@users.noreply.github.com.
- Never commit private captures, session configurations, device identifiers, or signing keys.
- State separately whether a rate is accepted by the runtime, meets a standalone decode
  budget, and is sustained in live VR. Unverified presets must say they are candidates or experiments.

Local development inputs are under the sibling `workspace` directory. Do not deploy its
build outputs to an active PCVR installation. Hardware acceptance is pending explicit permission.
