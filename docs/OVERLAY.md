# Client performance overlay

The Quest app displays a head-relative OpenXR quad below the center of view, at
1.2 m depth, visible in both eyes. The panel is rendered locally alongside the video;
its text is not compressed into the PC stream. It is **on by default for development**.

Click **both thumbsticks together** to toggle it. Holding the chord toggles once;
release both sticks before the next toggle. It works without the PC dashboard.
Controller actions still reach the game, so a game binding to these buttons can also fire.
The toggle lasts for the current app/session; reopening the app defaults to on.

Experimental `.24` adds `debug.q3pw.overlay_visible` for unattended comparisons:
`0` forces hidden, `1` forces visible, and empty/other values use the controller
state. The app polls this property at most twice per second, including while hidden;
it takes effect without restarting the app or stream. The controller chord still
updates its session state underneath an override, which wins while set. Clearing
the property restores that controller state. The normal unset default stays on.
Snapshot and restore the original property along with the rest of a bounded test.

If both thumbsticks cannot hide the panel after a benchmark, check for a leftover
forced-visible override. Clear it before normal play; `1` intentionally overrides
the controller chord. From PowerShell, using your connected Quest's serial:

```powershell
adb -s YOUR_QUEST_SERIAL shell 'setprop debug.q3pw.overlay_visible ""'
adb -s YOUR_QUEST_SERIAL shell getprop debug.q3pw.overlay_visible
```

The readback should be empty. Release both sticks, then click both together.
No APK update or SteamVR restart is needed. Leave the property unset in manual
play launchers; the overlay already starts enabled by default.

With `debug.q3pw.loop_probe=1` set before launching `.24`, `[Q3PW_OVERLAY_DRAW]`
records text construction, graphics-context/acquire/wait, renderer submission and
release wall times for each redraw. `[Q3PW_LOOP]` also reports total overlay-update
CPU-wall mean/maximum over its frame window. Renderer submission includes CPU
font rasterization and command submission; it does **not** measure completed GPU
work. Property mode and effective visibility changes have native diagnostic logs.
No render ordering or GPU synchronization changes accompany these diagnostics.
Matching Android/Windows builds and a same-session visible/hidden/visible comparison
are required before attributing any pacing problem to the overlay.
The matching `.24` pair passed all cloud checks, hashes, embedded version and
signing review; [artifact evidence](../results/OVERLAY-CONTROL-CI-2026-10-02.json).
It was subsequently installed as a matching pair for authorized remote tests.
No overlay performance gain is claimed.

Summarize a saved `logcat -v epoch` from that client process without accessing a
device: `python -m tools.quest3.overlay frame-loop.log --start START_UNIX_SECONDS
--end END_UNIX_SECONDS --pid RECORDED_CLIENT_PID --out overlay-timing.json`.
Use the actual frame-capture interval, not the later log-download time. The reader
filters other processes/intervals, preserves control/visibility transitions, weights
per-frame means by the recorded sample count, and reports missing timing as unknown.
Loop records cover frame windows ending inside the interval and may overlap its
start; compare settled cases and review the controls separately. It does not
declare a visibility or performance pass. Share reviewed summaries, not raw logs.

The [six .24 same-session screens](../results/OVERLAY-LIVE-2026-10-02.json) used
native120/1000-Mbps 4:2:0, no foveation, the same chart and no client restart.
Visible / hidden / visible delivered 102.63 / 100.11 / 106.64 fresh submissions/s;
hidden / visible / hidden delivered 111.79 / 103.78 / 96.72. Reported clock samples
were 690 MHz throughout, while the headset warmed. These short screens found no
repeatable FPS/p1 or latency gain when hidden. Visible updates averaged about
0.09–0.10 ms CPU per loop frame, with 6.4–6.9 ms maxima. Hidden updates averaged
0.007–0.009 ms and produced no redraw records. The default stays on. CPU cost is
measurable, but hiding the panel did not establish sustained native120 or optical
latency acceptance. Matching source coverage and property-mode logs were checked.

The panel shows codec/chroma, per-eye stream size, requested refresh, transport,
actual/target bitrate, delivered client FPS, Game/host FPS, pipeline latency and
game/encode/network/decode/queue/render/vsync stages, presented frames, packet loss,
PyroWave GPU/completion time and decoded/dropped/skipped/superseded/error counters,
battery temperature/charge, thermal status and readable GPU clock.

**Metric definitions:**

- Game/host FPS is the server's present cadence, a proxy for game output. It is not
  an independent application FPS counter and can include SteamVR resubmitted frames.
- Delivered FPS counts new client submissions. It is distinct from the display's
  refresh rate and from reprojected repeats. FPS smoothing averages intervals, not rates.
- Latency is ALVR's estimated tracking-to-predicted-display pipeline, not optical
  motion-to-photon. Network is the residual estimate, especially limited on PWU2 UDP.
- Frames/counters are cumulative for the current stream; superseded means an
  unpresented ready/queued frame was replaced. These categories should not be added
  together as if every counter represented a distinct lost display frame.
- Completion timing includes command recording/submit and wait for GPU completion.
  GPU decode uses timestamps. They measure different scopes.
- Missing counters or unreadable GPU sysfs values display `--`. A stopped/stale stream
  displays a waiting message, rather than retaining apparently healthy values.

Server feedback arrives once per second; the small panel texture updates at most
twice per second and is reused by the compositor between updates. The overlay adds
one composition layer and some CPU/GPU work. Measure its overhead in matched on/off
captures before making performance claims. Initialization/render errors disable the
overlay and leave video running. Physical thumbstick usability and in-headset text
placement are part of hardware acceptance, beyond the chord regression tests.

The .7 build passed cloud checks, displayed live statistics in both eyes and had
its physical thumbstick toggle confirmed by the owner. Device screenshots exposed
an upside-down texture origin; .8 corrects the GLES/OpenXR texture orientation and
has passed matching Android/Windows builds and cloud regressions. A .8 device
screenshot confirmed upright text and the panel in both eyes during USB streaming.
Do not use .7 as the final overlay acceptance build. Matched on/off overhead and
long-duration gameplay checks remain pending.
