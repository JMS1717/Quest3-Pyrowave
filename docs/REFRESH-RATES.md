# Quest 3 extended refresh

Primary reference: [Meta display refresh documentation](https://developers.meta.com/vr/documentation/unity/unity-set-disp-freq/), updated August 27, 2026.

HorizonOS v2.7+ supports integer 72-207 Hz on Quest 3 through standard refresh requests.
Quest 3S does not have these extended modes. Legacy enumeration can omit valid modes.
The APK therefore tests requests rather than adding a hard-coded capability claim.

On first session startup, the lobby tests 90,120,144,207,240 Hz. Each accepted request must
match `xrGetDisplayRefreshRateFB` and at least three consecutive `xrWaitFrame` periods within
0.5%, with a two-second settling deadline. It restores the session's original rate, then sends
confirmed capabilities to ALVR. A request failure or timeout is a recorded result. Reopen the
APK after changing the environment to run a fresh probe. Startup can briefly change lobby refresh.

## 240 Hz developer experiment

From `.57` the PC can do this itself: choosing **240 Hz** in the dashboard turns on
**Quest 3: switch to 240 Hz over USB** (`video.pyrowave.quest3_240hz_display_scaling`). While the
headset is on USB and the client is not yet streaming, the server saves the headset's values, wakes
it, applies the override (stepping through a change if needed), confirms the kernel DSI mode, and
restarts the client so its probe confirms 240 Hz. Selecting another rate (a SteamVR restart) or
closing SteamVR restores the saved values the same way. **Quest 3: maximum GPU clock over USB**
(`video.pyrowave.quest3_max_gpu_clock`) does the same for `debug.oculus.gpuLevel=7`. With neither
requested and nothing saved, the server issues no extra ADB commands. Over Wi-Fi, or to manage the
properties by hand, use the helper below.

Above 207 Hz the panel requires display scaling. HorizonOS exposes the 4128x2208 panel modes up to
207 Hz; 240 Hz exists only as a 3104x1664 mode (1552x1664 per eye) that the panel upscales. Fine
detail changes even with a full-chroma encoded stream, so keep 240 Hz results separate from
full-resolution 207 Hz measurements. The APK does not set these properties.

```powershell
python -m tools.quest3.refresh_scaling --serial <device> status
python -m tools.quest3.refresh_scaling --serial <device> enable --state results/local/scaling-before.json
# Reopen the APK, choose 240 Hz on the PC, stream, then:
python -m tools.quest3.refresh_scaling --serial <device> restore --state results/local/scaling-before.json
```

`enable` saves both prior values, sets `debug.oculus.forceDisplayScaling=1` and
`debug.oculus.refreshRate=240`, and confirms the panel mode. `restore` puts the saved values back and
confirms the panel left the scaled mode. No root, reboot, persistent property or clock forcing.
Do not leave the experiment enabled between sessions: until it is restored, every VR app, including
Virtual Desktop, runs in the 240 Hz scaled mode.

### How the override is applied (measured October 6)

- The properties are applied when they **change while the display is awake**. A change made while
  the headset sleeps is not picked up, and later sleep/wake cycles do not re-read it. The helper
  wakes the headset first and, if the properties already hold the target, steps through the
  opposite values so a change event occurs.
- `dumpsys SurfaceFlinger` can report `activeMode=240` while the panel still runs 4128x2208 at
  120 Hz. The kernel's `dsi_display_set_mode` log line (`hactive`, `vactive`, `fps`) is the reliable
  signal, and it is what the helper checks.
- Once in the scaled mode, restoring the properties while asleep left the panel at 240 Hz through
  four sleep/wake cycles. Changing them again while awake returned it to 4128x2208 at 120 Hz.

### Runtime and SteamVR acceptance

With the override active, the lobby ran at 240 Hz: `xrGetDisplayRefreshRateFB` reported 240.0,
`predicted_display_period` was 4,166,816 ns and VrApi logged 240/240 FPS. The request API still
refuses 240 (`ERROR_DISPLAY_REFRESH_RATE_UNSUPPORTED_FB`), and every other request "succeeds"
without changing the rate. `.55` therefore confirmed no rate and the server refused a 240 Hz session.

From `.56` the client treats a refused request as confirmed when the runtime already runs that rate
and three frame periods match. It reports `[80, 240]`, the server accepts `preferred_fps=240`, and
SteamVR's display frequency is 240: the live session read back `openvr_config.refresh_rate=240` and
the server rendered and encoded an average of 240.6-241.1 frames/s (harmonic mean of the
per-frame rate; the instantaneous median reads about 243). Streaming results are in
[HIGH-REFRESH.md](HIGH-REFRESH.md).

Thermal throttling can reduce the rate. `[Q3PW_EFFECTIVE]` records runtime/frame-period changes;
client FPS and dropped frames establish delivery separately. Physical optical latency requires
external instrumentation.
