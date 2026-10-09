# beta.2: Quality 120 out of the box

This release packages `.136`. It changes what a first-time user sees after [beta.1](RELEASE-beta.1.md):
**a fresh install streams Quality 120 Hz**, the recommended profile, instead of Starter 72 Hz, and
the dashboard stays quiet while the stream works.

- **Fresh-install settings = Quality 120 Hz:** 2272 x 2432 per eye (110 %), the game rendered at
  150 % and downsampled on the PC with Lanczos, CDF 5/3 on the compute decoder, 1500 Mbit/s,
  120 Hz, sharpening 30, maximum GPU clock. beta.1 measured 111-120 fresh FPS of 120 for this
  profile in short screens over USB.
- **Starter 72 Hz is now a fallback.** It is still in the headset menu and the dashboard list. Use
  it if the stream does not start or stutters.
- **On Wi-Fi, pick a Wi-Fi profile.** Quality 120's 1500 Mbit/s is meant for USB; choose **Wi-Fi
  Quality 120 Hz** (Wi-Fi 6E) or **Wi-Fi 90 Hz** in the headset menu.
- **No red errors for a working stream** (`.136`). Tracking and pose measurements, logged every
  5 s for diagnostics, no longer fill the dashboard's notification bar; the Logs tab and
  `crash_log.txt` keep them. A handshake that times out while the headset app starts is logged as
  information, not an error.

An existing install keeps its saved settings. Pick a profile once to move to the tested values.
Keep beta.1 for rollback; this APK is signed with the same key and installs over it. Not
established, as in beta.1: sustained gameplay, optical motion-to-photon latency, and an advantage
over Virtual Desktop.

## Install

1. Stop SteamVR. Extract `Quest3-Pyrowave-Windows.zip` into a new folder, run `ALVR Dashboard.exe`
   and register the driver.
2. Install `Quest3-Pyrowave-dev.apk` and open **Quest3 PyroWave** from Unknown Sources.
3. Start SteamVR and turn on **Devices → Wired Connection** (USB), or trust the headset under
   **New Wireless Devices** (Wi-Fi). SteamVR restarts once on the first connection. A fresh install
   streams **Quality 120 Hz**.
4. To change profiles, hold both thumbsticks in the headset for about 0.7 s, or use
   **Settings → Presets → Streaming profile** on the PC. The [profile guide](PROFILES.md) explains
   each one.
