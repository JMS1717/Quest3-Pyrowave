# Quest 3 extended refresh

Primary reference: [Meta display refresh documentation](https://developers.meta.com/vr/documentation/unity/unity-set-disp-freq/), updated August 27, 2026.

HorizonOS v2.7+ supports integer 72-207 Hz on Quest 3 through standard refresh requests.
Quest 3S does not have these extended modes. Legacy enumeration can omit valid modes.
The APK therefore tests requests rather than adding a hard-coded capability claim.

On first session startup, the lobby tests 72, 90, 120, 144, 165, 180, 200, 207 and 240 Hz
(`PROBE_RATES`). When `debug.oculus.refreshRate` pins the display, or the runtime runs a rate above
120 Hz that it does not enumerate, the probe verifies only that rate: every other request would
"succeed" without changing the rate and cost the full timeout. Each tested rate must
match `xrGetDisplayRefreshRateFB` and at least three consecutive `xrWaitFrame` periods within
0.5%, with a two-second settling deadline. It restores the session's original rate, then sends
confirmed capabilities to ALVR. A request failure or timeout is a recorded result. Reopen the
APK after changing the environment to run a fresh probe. Startup can briefly change lobby refresh.

**`.85`-`.86` (October 8).** The probe runs only while the app is shown, and a full probe in which
90 or 120 Hz fails to confirm is repeated up to twice. From `.86` a property value of 72 is not
read as a pin. HorizonOS writes 72 itself when the shell takes over after a VR app exits, and
clears it about a second after the next VR app starts (watched with the property, the resumed
activity and the client pid polled every 0.3 s), so a starting client usually reads 72. If 72 really
is pinned, the full probe shows it (every other rate fails) at the cost of about 15 s per round.

**`.89`-`.90` (October 8): the server's display helper pinned 72 Hz itself.** With only the maximum
GPU clock requested, the helper read the three properties, woke the headset (about 2 s), then
wrote all three. The client it was about to restart had started in the meantime and cleared
`refreshRate`, so the helper wrote the 72 it had read back: a change while awake, which holds the
panel at 72 Hz. The restarted client then confirmed only 72 (every request "succeeded" without
changing the rate), reported [72, 80], and the server refused the stream until the app was
reopened. Whether the client's clear landed before the helper's read was a race, so it struck
about half the time, in the owner's setup as well as on the harness:

| Build | Server restarts | Stuck at 72 Hz |
|---|---|---|
| `.86`-`.87` | 7 | 3 |
| `.88` (2.5 s pause before the restart) | 4 | 3 |
| `.89`-`.90` (write only the values that change) | 8 | 0 |

All at 120 Hz, 2000 Mbit/s, 4:4:4, with the server setting the GPU clock and the harness leaving
the display properties alone. `.90` streamed at 110.2 and 109.3 fresh FPS. Two earlier theories
were wrong and are reverted: that the restarted client started with the headset asleep (`.87` held
proximity through the restart; its delayed release then blinked the unworn headset asleep, and the
wake opened the Quick Actions menu over the client), and that the shell's write raced the restart
(`.88` paused 2.5 s, which made it worse).

**`.91`-`.92` (October 8): a restarted client with no activity.** In 2 of the 8 harness restarts on
`.89`-`.90`, the restarted client came up as a process with no resumed activity, and the server
looped on "Failed to find resumed state line" until the app was reopened. The restart was `am
force-stop` followed at once by `am start`. Repeating that directly on a client that had been up
for about 3 s lost the start in 2 of 16 trials; `am start -S -W` (the activity manager stops the
app and waits for the new launch) started it in 12 of 12. `.91` starts a restarted client once
more if it has no activity 10 s after the restart (seen to fire and recover in about 10 s). `.92`
restarts with `am start -S -W`; 4 of 4 harness restarts at 120 Hz streamed with no retry. The
`.91` retry stays as a fallback.

**Next: the forced 207 Hz path churns.** With the owner's display setup at 207 Hz (1000 Mbit/s),
the server restarts the client one to three times per run before the rate holds: the shell's 72
write after the client exits and the clear about 1 s after the next start undo the forced 207,
and the helper sees the difference and restarts again. Each run converges in about 10-15 s and
then streams at 185-190 fresh FPS (`.90` and `.92` alike, so this is not new). Writing the rate
after the shell's exit write, or not restarting for a cleared value, should remove the churn.

One run's property timeline (polled every 0.5 s, `.92`, October 8), from the client's start:

| Time | `refreshRate` | What happened |
|---|---|---|
| 0.0 s | 72 | Client starts (the shell's value from the last exit) |
| 1.0 s | empty | HorizonOS clears it |
| 2.9 s | 207 | Helper writes 207 and the GPU level, then restarts the client |
| 4.9 s | 72 | The shell takes over and writes 72 over 207 |
| 8.2 s | 207 | Helper writes 207 again (the new client's activity is not resumed yet) and restarts |
| 10.2 s | empty | The next client starts; HorizonOS clears 207 |
| 13.2 s | 207 | Helper writes 207 a third time and restarts |
| 14.0 s | 207 | This client keeps 207 and streams |

In the next run, one restart was enough: neither the 72 nor the clear happened. What decides
whether HorizonOS writes 72 on exit or clears on start is not known.

The forced rate is not needed to reach 207 Hz. With nothing forced, the client's probe confirmed
144, 165, 180, 200 and 207 Hz on its own requests. On `.93` (October 8, 207 Hz, 1000 Mbit/s,
CDF 5/3, 4:2:0, one AB run each), forced off gave 178.9 and 174.2 fresh FPS and forced on gave
173.4 and 174.5. Both ran the panel at 207 Hz (`[Q3PW_EFFECTIVE] runtime_hz=207`). A `.92` run
straight after gave 178.5 and 168.3, so the earlier 185-190 came from the conditions of those
runs, not the build.

**`.94` forced the panel only above 207 Hz (rejected, reverted).** It removed the rate writes as
intended: the property watch showed `refreshRate` never written, with one client restart per
block, for the GPU clock. But in one of two runs the first connection was refused ("requested
207 Hz unsupported; client-confirmed rates [72, 80]"). The GPU-clock restart's wake opened Quick
Actions over the client, and the shell's 72 stayed set: nothing cleared it, so the panel was
pinned at 72. The harness got the stream only about a minute later, after the client was
restarted again. With the rate forced, the helper rewrites 207 over that 72, so part of the
"churn" is this recovery. None of the other 15 runs tonight was refused (`.92`/`.93`, forced and
not), so a 72 left behind after a restart is the underlying fault. `.94` streamed at 183.2-184.1
fresh FPS in both runs (network p50 3.3-3.8 ms), against 166.7-178.9 (7.4-9.2 ms) for the `.93`
runs around it. That difference is not explained and not claimed.

Next: when the server's rate check refuses a client whose only confirmed rates are 72 and 80 while
`refreshRate` reads 72, have the helper clear the property while awake and restart the client
once. That would serve both paths, forced and not. Only then stop forcing native rates.

## 240 Hz developer experiment

**Current (`.60` and later, including `.64`).** The dashboard offers 72, 80, 90, 120, 144, 165,
180, 200, 207 and 240 Hz, and **Preferred FPS** takes any whole rate. The setting described below
is now named **Quest 3: set the panel refresh rate over USB**; its key is still
`video.pyrowave.quest3_240hz_display_scaling`, so saved sessions load. Choosing 165, 180, 200 or
240 Hz turns it on, since the dashboard expects the runtime to grant those rates only with the
panel forced. With it on and the headset on USB, the PC forces the panel to exactly the
selected rate whenever that rate is above 120 Hz: natively up to 207 Hz (4128x2208), in the scaled
3104x1664 mode above 207 Hz, up to 240 Hz. Selecting 120 Hz or less, or closing SteamVR, restores
the saved values. The "207 Hz (measured)" profile leaves it off and turns on only the maximum GPU
clock. The PC changes these properties over USB adb only. Without USB, the helper below sets 240 Hz
only, and `tools/quest3/gpu_level.py` sets the GPU clock. The text that follows describes `.57`,
when the setting covered 240 Hz only.

From `.57` the PC can do this itself: choosing **240 Hz** in the dashboard turns on
**Quest 3: switch to 240 Hz over USB** (`video.pyrowave.quest3_240hz_display_scaling`). While the
headset is on USB and the client is not yet streaming, the server saves the headset's values, wakes
it, applies the override (stepping through a change if needed), confirms the kernel DSI mode, and
restarts the client so its probe confirms 240 Hz. Selecting another rate (a SteamVR restart) or
closing SteamVR restores the saved values the same way. **Quest 3: maximum GPU clock over USB**
(`video.pyrowave.quest3_max_gpu_clock`) does the same for `debug.oculus.gpuLevel=7`. With neither
requested and nothing saved, the server issues no extra ADB commands. Over Wi-Fi, or to manage the
properties by hand, use the helper below.

Waking the headset turns on its proximity automation. From `.59` the server turns it off again
on every path, including when the headset never wakes or the cable is pulled mid-change. Before
`.59`, a failed wake returned before that step and left the proximity sensor overridden until a
later successful change. Unit-tested only; not yet checked on the headset.

HorizonOS writes `debug.oculus.refreshRate=72` itself within a second of a VR app starting with
the property empty. Up to `.61` the server treated that as a change to undo: with only the GPU
clock requested it put the saved empty rate back, which restarted the client, which made HorizonOS
write 72 again, every 4 to 5 seconds without end. The server now records the values it wrote and
restores a property only while it still holds one of them; values written by HorizonOS or by hand
afterwards are left alone. Checked on the headset (October 7): one client restart, then streaming
at 207 Hz with the GPU clock pinned.

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

### Restart loop with Meta Quest Link running (October 8, `.100`)

A 207 Hz harness cell on `.98` never streamed.

- **What happened:** the helper restarted the client every 5-6 s for over 3 minutes.
  - After each restart the properties had been reset: `refreshRate` was 72 or empty, and every other
    time `gpuLevel` was empty as well.
  - The client's probe saw only 72/80/90/120 Hz. Every request above 120 was rejected
    (`ERROR_DISPLAY_REFRESH_RATE_UNSUPPORTED_FB`), so the server refused the stream each time.
- **Likely cause, not proven:** Meta Quest Link's `OVRServer_x64` had been running on the PC since
  the owner's other streaming test. The same 207 Hz switch worked earlier that day without it.
  `gpuLevel` being cleared is new; HorizonOS alone only rewrites `refreshRate`.
- **Fix (`.100`):** at most four display-change restarts within 90 s.
  - The fifth stops the helper forcing the panel for 5 minutes.
  - It logs, and shows in the dashboard status: *"The headset resets its refresh rate after every
    client restart… Close Meta Quest Link (OVRServer) on this PC if it runs, or choose 120 Hz."*
  - The client then stays up, and the server's rate check reports the unsupported rate instead of
    looping.
- **Not yet checked:** a 207 Hz switch with OVRServer closed, to confirm the cause. Two
  stale state files from interrupted runs (`.97`, `.98` runtime folders) also still record
  `applied.gpuLevel=7`.

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
