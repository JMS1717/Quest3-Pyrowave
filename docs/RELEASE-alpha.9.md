# alpha.9: 207 Hz at full resolution, a faster decoder, 144-240 Hz

This prerelease packages the `.63` stack:

- PRs #10-#12, #13 (multilevel Haar), #14 (Decoder V2, packed YCbCr output) and #15 (parallel
  wired video, frame trace).
- Four fixes found in review or in Wi-Fi testing.
- The first measured Wi-Fi results.

Highlights:

- Haar GPU decode at 207 Hz takes about half as long as before: 2.6-2.8 ms, down from 5.6 ms.
- At the owner's settings the client shows about **195 fresh frames per second at 207 Hz with
  2080x2208 per eye** in 10-12 s screens. The `.55` build managed about 117 there.
- **Wi-Fi 6E** carries the same 207 Hz stream at 1000 Mbit/s with 189-195 fresh FPS. It adds about
  3.4 ms of network time against USB ([WIRELESS.md](WIRELESS.md)).

Keep alpha.8 for rollback. Not established: sustained gameplay, optical motion-to-photon latency,
and an advantage over Virtual Desktop.

## Changes since alpha.8

### Faster decoding on the Quest

**Multilevel Haar** ([HAAR32.md](HAAR32.md)).

- Two dispatches per plane instead of one per level.
- Levels 0-1 are stored as packed quads, and luma as a half-size RGBA8 plane.
- Haar decode at 4160x2208 drops from 4.7 to 3.0 ms.

**Packed YCbCr straight into the eye-copy buffer (mode 5, default)**
([PRESENT-YCBCR.md](PRESENT-YCBCR.md)).

- The decoder writes luma and chroma into the hardware buffer the eye pass reads.
- The eye shader converts to RGB, so the separate full-frame conversion pass is gone.
- The client falls back to the previous mode where the buffer cannot be written as a storage
  image.

**Decoder V2 for CDF 5/3** ([DECODER-V2.md](DECODER-V2.md)).

- A register-only inverse 5/3 with the same packed output.
- It is about 3x faster than the stock 5/3 decoder: 2.3 against 7.9 ms on the bench, and 4.1
  against 11.4 ms live at 207 Hz.
- CDF 5/3 removes Haar's 8-pixel block edges and looks much smoother in the headset. It costs
  fresh frames: about 2 FPS at 700 Mbit/s with the maximum GPU clock, more at higher bitrates.
- Haar stays the default wavelet.

**Cheaper eye copy** ([FRAME-TRACE.md](FRAME-TRACE.md)).

- On an sRGB swapchain the eye pass now writes the already sRGB-coded values with sRGB encoding
  turned off.
- That saves about 0.2 ms per frame with an identical image.

### Refresh rates and profiles

**Any whole refresh rate from 144 to 240 Hz** (PRs #10 and #12).

- Rates up to 207 Hz run on the native panel mode; 240 Hz uses the scaled panel mode.
- Over USB the PC switches the Quest 3 panel itself, and restores it when SteamVR stops.

**Measured streaming profiles** in Settings, Presets, Streaming profile:

| Profile | Stream per eye | Fresh FPS (short screens) |
|---|---|---|
| Native 120 Hz | 2064x2208 | about 119 |
| **207 Hz (updated)** | **2080x2208** (was 1440x1536) | about 195 |
| 240 Hz scaled panel | 1440x1536 | about 223-228 |

- The 207 and 240 Hz profiles also turn on the maximum GPU clock (690 MHz) over USB.
- The 120 and 240 Hz rows were measured before the new decoder.

### Bitrate, quality and transport

- **Bitrate:** sliders go up to 4000 Mbit/s, the USB 3.2 Gen 1 payload ceiling.
  - A quality floor keeps PyroWave at 0.25 bits per stream pixel or more.
  - Encoder rate allocation is weighted for the headset's pixels per degree
    ([ENCODER-CSF.md](ENCODER-CSF.md), [BITRATE.md](BITRATE.md)).
- **Two parallel wired video connections by default**
  (`video.pyrowave.wired_video_connections`, 0-4).
  - Over USB each frame is split across dedicated adb-forwarded connections.
  - That cuts ALVR's network-stage estimate by 0.7 ms at 1000 Mbit/s and 1.8 ms at 1500.
  - If the client does not answer on every port, video stays on the stream socket.
- **Optional:** an adaptive Lanczos-3 render downsample filter, light foveated encoding
  ([LIGHT-FOVEATION.md](LIGHT-FOVEATION.md)), and 4:4:4 chroma ([CHROMA.md](CHROMA.md)). All are
  off by default.

### Wi-Fi and connection fixes (`.63`)

- **Wired mode chooses a USB headset only.**
  - It uses an online adb device attached over USB. Before, the headset's own Wi-Fi adb address
    could be taken for a wired device and the stream tunnelled through adb.
  - An offline or unauthorized USB entry no longer blocks connecting.
  - When the wired connection isn't ready, the server goes on to manual IPs and discovery. Before,
    the wired entry stopped it from ever trying the network.
- **Auto bitrate respects network latency.** The quality floor raises Auto's estimate, but the
  network-latency and maximum limits still apply. Under congestion Auto can go below the floor
  instead of queuing frames.
- **No mixed frames on parallel wired connections.** A frame whose timestamp repeats the previous
  one (a game stutter re-presenting a pose) is skipped. Before, its slices could mix with the first
  copy's.
- **Packed YCbCr fallback.**
  - If the eye copy's packed-YCbCr programs fail to build on a driver, packed frames go through the
    staging path, which converts them. Before, they were drawn with wrong colours.
  - The staging renderer logs a failure of its own packed program instead of stopping the client.

**Wi-Fi measurements**, 207 Hz, 2080x2208, Haar, Wi-Fi 6E at 6 GHz with the PC on Ethernet
([WIRELESS.md](WIRELESS.md)):

| Bitrate | Fresh FPS | ALVR network p50 / p99 |
|---|---|---|
| 1000 Mbit/s | 189-195 | 6.1 / 10-13 ms |
| 1250 Mbit/s | 190-192 | 7.1 / 21-49 ms |
| 1500 Mbit/s | frames about 380 ms late | the link's limit was exceeded |
| Auto (max 1500, 8 ms limit) | 197-200 | 4.4 ms; settles at about 550 Mbit/s |

On Wi-Fi:

- Use a constant 1000 Mbit/s (1250 at most), or Auto with a maximum and the latency limit.
- The server's GPU-clock and panel helper works only over USB.

### Usability and fixes

- **Stream-start settings apply by themselves.** Refresh, resolution, wavelet and similar settings
  changed while streaming reconnect after 2 s, restarting SteamVR when needed
  ([SETTINGS-APPLY.md](SETTINGS-APPLY.md)).
- **Display helper.**
  - HorizonOS writes `debug.oculus.refreshRate=72` itself when a VR app starts with the property
    empty. The server no longer undoes that write, which had restarted the client every few
    seconds.
  - The server now logs when client statistics stop.
- **Overlay modes, independent game and stream controls, and diagnostics** (PR #9).

### For developers

- **Diagnostics:** an opt-in per-frame critical-path trace (`debug.q3pw.frame_trace=1`) and its
  analyzer (`tools/quest3/frame_trace.py`).
- **Opt-in experiments**, not defaults: `debug.q3pw.release_fd=1` and
  `debug.q3pw.frame_hold_us`. Results are in [FRAME-TRACE.md](FRAME-TRACE.md).
- **Local fast builds:** [LOCAL-BUILD.md](LOCAL-BUILD.md).

## Defaults

Fresh installs keep the conservative **400 Mbit/s / 72 Hz** candidate. The other defaults:

- Haar wavelet, 4:2:0, TCP, Quest 3 Auto (Compute decoding).
- Packed YCbCr output, two wired video connections.
- No foveation.

Saved settings are preserved. To try high refresh, choose **Quest 3 PyroWave 207 Hz (measured)**
in Settings, Presets, Streaming profile, with the headset connected over USB (or over Wi-Fi
at 1000 Mbit/s, without the GPU-clock helper).

To install:

1. Stop SteamVR.
2. Extract the new server into a separate folder and run its dashboard.
3. Install the matching APK.
4. On the Quest, open Quest3 PyroWave from Unknown Sources, then trust the headset and register
   this server.

Keep the previous pair.

## Known issues

- **Not hardware-tested in this build:**
  - The USB wired path. USB adb was offline during `.63` testing, and every run in this build was
    over Wi-Fi. Wired streaming was last measured on `.62`, and the `.63` device choice is covered
    by unit tests.
  - The duplicate-frame skip and the packed-YCbCr fallback, since no stutter or driver failure was
    available to trigger them.
- **Unplugging and replugging USB** with two wired video connections hasn't been checked. If video
  stops, set **Wired video connections** to 0.
- **The "207 Hz (measured)" profile name** refers to 10-12 s screens. Sustained play at that profile
  is not yet measured.
- **Constant bitrate above the Wi-Fi link's capacity** queues frames without limit (about 380 ms at
  1500 Mbit/s on the test link). Stay at or below about 1250 Mbit/s on Wi-Fi, or use Auto.

## Not in this release

- No claim of sustained 207 fresh FPS, optical latency or perceptual parity with Virtual Desktop.
- Frame hold, release fence, mode 6 and the eye diagnostics stay opt-in or diagnostic only.
- Rejected work stays out: fused colour, levels 4-2 fusion, Haar H2.
