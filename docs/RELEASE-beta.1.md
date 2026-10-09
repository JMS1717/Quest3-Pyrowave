# beta.1: tested profiles, chosen in the headset

This release packages the `.134` stack: everything on `claude/frame-budget` since alpha.9 (`.63`),
merged with PR #23. It is the first build meant to be set up and played without reading the
engineering notes:

- **Seven tested streaming profiles** plus a fresh-install starter, with a
  [guide](PROFILES.md) that says what each does and which is better for what.
- **A settings menu in the headset.** Hold both thumbsticks for about 0.7 s. The first row picks a
  profile; every row explains itself in two lines.
- **Quality 120 Hz is the recommended setting over USB:** 2272 x 2432 per eye, CDF 5/3,
  1500 Mbit/s, 111-120 fresh FPS of 120 in short screens.

Keep alpha.9 for rollback; this APK is signed with the same key and installs over it. Not
established: sustained gameplay, optical motion-to-photon latency, and an advantage over Virtual
Desktop.

## Install

1. Stop SteamVR. Extract `Quest3-Pyrowave-Windows.zip` into a new folder, run `ALVR Dashboard.exe`
   and register the driver.
2. Install `Quest3-Pyrowave-dev.apk` and open **Quest3 PyroWave** from Unknown Sources.
3. Trust the headset in the dashboard and start SteamVR. A fresh install streams **Starter 72 Hz**.
4. Pick **Quality 120 Hz** (USB) or **Wi-Fi Quality 120 Hz** (Wi-Fi 6E) in the headset menu or in
   **Settings → Presets → Streaming profile**. Set Game render resolution to 150 % or more.

An existing install keeps its saved settings. Pick a profile once to move to the tested values.

## The profiles

| Profile | Stream per eye | Codec | Measured, short screens over USB |
| --- | --- | --- | --- |
| **Quality 120 Hz** (recommended, USB) | 2272 x 2432 (110 %) | CDF 5/3, 1500 Mbit/s | 111-120 fresh FPS of 120 |
| **Godlike 120 Hz** (USB) | 3072 x 3216, no downsample | Haar, 1500 Mbit/s | 116-118 fresh FPS of 120 |
| **Godlike 90 Hz** (USB) | 3072 x 3216 | CDF 5/3, 1500 Mbit/s | 89-90 fresh FPS of 90 |
| **Colour 4:4:4 120 Hz** (USB) | 2272 x 2432 | Haar 4:4:4, 1500 Mbit/s | 118-120 of 120 |
| **Wi-Fi Quality 120 Hz** | 2272 x 2432 | CDF 5/3, 1000 Mbit/s, UDP | 1000 Mbit/s holds on Wi-Fi 6E; this profile's screen is pending |
| **Wi-Fi 90 Hz** | 2064 x 2208 | CDF 5/3, 700 Mbit/s, UDP | not yet measured |
| **Competitive 207 Hz** (USB) | 2080 x 2208 | CDF 5/3, 1000 Mbit/s | 194-197 with Haar before Horizon OS build 209 |
| **Starter 72 Hz** (fresh install) | 2064 x 2208 | CDF 9/7, 400 Mbit/s | streams on any connection |

The USB profiles turn on the maximum GPU clock and four wired video connections, downsample with
Lanczos (Adaptive at 207 Hz) and sharpen on the PC. The headset refuses a USB-only profile on Wi-Fi
and says why. The superseded 600-2000 Mbit/s and render-size latency experiments are gone from the
dashboard list; three "(measured)" reference profiles and the H.264/HEVC/AV1 comparisons remain.

**Horizon OS build 209** (v2.9, October 2026) holds the Quest 3 at 120 Hz or less, so Competitive
207 Hz cannot reach 207 Hz on that build ([HIGH-REFRESH.md](HIGH-REFRESH.md)).

## Changes since alpha.9

### Image quality

- **Colour.** PyroWave frames are no longer squeezed into the 16-235 range on the headset (`.103`),
  and **Quest colour** (on by default, `.105`) streams in the headset's own wide gamut as Virtual
  Desktop does. **Accurate (Rec. 709)** remains a choice.
- **Sharper PC source.** The PC downsamples the game's render with Lanczos-3 and sharpens before
  encoding (`.98`-`.99`); offline this beat sharpening on the headset.
- **Encoder tuned for full-size streams** (`.129`-`.130`, [ENCODER-CSF.md](ENCODER-CSF.md)): the
  contrast-sensitivity weights follow the stream's rows, +0.5 dB PSNR-HVS-M at 3072 x 3216 with no
  extra encode work. **Skip invisible detail** (`.111`) drops detail below a visibility floor even
  when the frame has room for it.
- **4:4:4 at 120 Hz.** Haar decoder modes 7 and 8 pack full chroma into the eye-copy buffer
  (`.124`-`.125`): 118-120 fresh FPS of 120 at 110 %.
- **Stream sizes above the panel.** Stream resolution goes above 100 % (to 150 % in the dashboard,
  120 % in the headset menu), and full size (3072 x 3216, Virtual Desktop's Godlike size) streams
  without a downsample.

### Smoothness and latency

- **No more drops to 10 FPS** (`.131`-`.134`). After a reconnect (applying a profile, reopening
  the app), the old connection reported its disconnect after the new one had started, so the driver
  told SteamVR the headset was off and SteamVR idled the stream at 10 Hz a few seconds later. The
  driver now counts connections, blocks standby while one streams, and wakes the headset if SteamVR
  idles it anyway (a headset lying still on a desk).
- **Direct eye copy by default** (`.117`): decoded frames go straight into the headset's eye images.
  Godlike 90 went from 80 to 89 fresh FPS, and ALVR's latency estimate at Quality 120 fell from
  46-50 to about 40 ms.
- **Paired chroma by default** (`.118`): +4 fresh FPS at full size, 120 Hz.
- **Server-predicted head poses**, a high-priority encode queue (`.78`) and a decode priority that
  follows the decoded pixel rate (`.112`-`.123`).
- **Four parallel wired video connections** in the USB profiles.

### Connection and setup

- **UDP is the Wi-Fi default** (`.65`-`.66`); the old encoder-side UDP path is gone (`.93`).
- **The headset settings menu** (`.131`): profiles, refresh, bitrate, stream and render size, chroma
  and overlay, with help text. Bitrate applies live; the rest reconnect the stream.
- **Refresh-rate fixes:** a 72 Hz panel pin left behind by Horizon OS is cleared (`.95`), the
  display helper writes only changed values (`.89`), a restart loop stops after four restarts in
  90 s and names Horizon OS build 209 (`.100`-`.101`), and the GPU level survives client restarts
  (`.121`).
- **Diagnostics:** audio cut-out statistics (`.84`), a light play-session logger, a frame trace with
  swapchain markers (`.116`) and opt-in decoder probes. All are off or quiet by default.

### Opt-in experiments (off by default)

Stream only the game's frames (`.83`), phase lock (`.107`), decode gate (`.109`), GL context
priority (`.113`), tracking poll rate (`.114`), light peripheral encoding and Meta fixed foveated
rendering (`.96`-`.97`). See [HANDOFF.md](HANDOFF.md).

## Verification

- CI's Windows Rust tests (alvr_adb, client_core, graphics, session, packets, server_core,
  server_io, dashboard) and the Python suite pass on the release commit.
- Live on one Quest 3 over USB, October 9 (short screens, heavy SteamVR Home scene) on `.134`:
  Godlike 120 Hz (Haar) 117.5 and 115.9 fresh FPS of 120, ALVR's latency estimate 43 ms; Godlike
  90 Hz (CDF 5/3) 88.9 and 90.1 of 90, 48-54 ms; Quality 120 Hz 119.4 of 120, 35 ms; no standby drops while
  streaming. The Colour 4:4:4 and Competitive figures come from earlier builds of this branch.
- Not checked in this release: sustained play, Wi-Fi 90 and Wi-Fi Quality 120 live, optical
  latency, replugging USB with four wired connections.

## Assets

1. `Quest3-Pyrowave-dev.apk`: the Quest client, signed with the stable release key.
2. `Quest3-Pyrowave-Windows.zip`: dashboard, SteamVR driver and bundled files.
3. `APK-CERTIFICATE.txt`: the APK's signing certificate digest.
4. `SHA256SUMS.txt`: checksums of the assets above.

Built locally from the tagged commit with `tools/local/fast_build.py` ([LOCAL-BUILD.md](LOCAL-BUILD.md)).
