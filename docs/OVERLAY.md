# Client performance overlay

The Quest app displays a head-relative OpenXR quad below the center of view, at
1.2 m depth, visible in both eyes. The panel is rendered locally after the video;
its text is not compressed into the PC stream. It is **on by default for development**.

Click **both thumbsticks together** to toggle it. Holding the chord toggles once;
release both sticks before the next toggle. It works without the PC dashboard.
Controller actions still reach the game, so a game binding to these buttons can also fire.
The toggle lasts for the current app/session; reopening the app defaults to on.

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
