# Plan: finish the network stack, then sharpness and clarity

Prepared October 7, 2026, evening, after the owner played `.65`. It replaces the next steps in
[HANDOFF.md](HANDOFF.md#next-steps-after-64). Hardware work on any night still needs the
owner's go-ahead for that night.

## Where we are

### The owner's play test

The owner played `.65` from the `runtime-local65-play` setup. CDF 5/3 was used throughout, except
in the 4:4:4 runs.

| Setup | Owner's verdict |
|---|---|
| USB, 207 Hz, 1000 Mbit/s | Smooth, but **worse than the day before in complex scenes**. Compression patterns move with the head. Audio cut out. |
| USB, 120 Hz, 1500 Mbit/s | No artifacts, "nothing amazing". **Resolution doesn't look above native**; colour "pretty ok", nothing like 4:4:4; latency unremarkable. |
| Wi-Fi, 120 Hz, 1000 Mbit/s, UDP | Quality about the same as wired. **Notable stutter on head movement**, some visible artifacts. |
| 4:4:4 at 1500 and 2000 Mbit/s (owner's own test) | About 100 fresh FPS, which impressed the owner. Sharpness and latency still held it back. |

The worse 207 Hz result has a cause:

- The encoder's per-frame cap is `bitrate / preferred_fps`.
- The game rendered 100–170 FPS, so each frame got 0.6 MB (about 0.35 bit per sample), and the
  unused share of the bitrate was lost.
- 120 Hz at 1500 Mbit/s gives 1.5 MB per frame.

### Measured the same evening

These come from the play session's event log (ALVR statistics, averaged over the period) and from
the live harness.

Wired CDF 5/3 on `.65`, 207 Hz, 2080x2208, 690 MHz GPU, 60 deg/s pan, one 10 s block each:

| Bitrate | Fresh FPS |
|---|---|
| 800 Mbit/s | 200.4 |
| 1000 Mbit/s | 194.4 |
| 1250 Mbit/s | 184.1 |
| 1500 Mbit/s | 175.0 |

`.65` CDF 5/3 at 1000 Mbit/s is as fast as Haar (193). Before the 2-connection wired path it was
179.

Wi-Fi UDP CDF 5/3 at 207 Hz gave 177.4 fresh FPS at 1000 Mbit/s and 171.5 at 1250.

During play, the share of frames that repeated the previous pose:

| Link and settings | Repeated poses |
|---|---|
| Wi-Fi, 120 Hz, 1250 Mbit/s | 10–21% |
| Wi-Fi, 120 Hz, 1000 Mbit/s | 3–13%, about 8% on average |
| USB | about 0 |

Latency, as ALVR's estimate (not motion-to-photon), USB, 120 Hz, 1500 Mbit/s, averaged over
19,436 frames:

| Stage | ms |
|---|---|
| Game | 4.6 |
| Server compositor | 0.2 |
| Encoder | **5.7** |
| Network | 6.8 |
| Decoder | 5.7 |
| Decoder queue | 3.1 |
| Client compositor | 2.0 |
| Vsync queue | **14.1** |
| **Total** | **42.1** |

At 207 Hz and 1000 Mbit/s the total was 35.3 ms: encoder 4.8 ms and vsync queue 12.9 ms.

## How to work

- Measure the bottleneck first, then rewrite what is slow.
- Use ABBA order with a settle period and record clocks.
- Builds are local.
- Keep the number of PRs small.

## Phase 1: finish the network stack

**1.1 Tracking priority on Wi-Fi (first).**

- Tracking packets travel up a link the video keeps busy, so they arrive late and bunched. The
  server then reuses poses.
- Mark the client's tracking and stream socket for the voice access category, and leave the
  video at a lower class.
- On Linux the Wi-Fi user priority comes from the top three TOS bits, not the full DSCP. EF (46)
  therefore lands in video (UP 5). Voice needs CS6 or CS7 (UP 6/7), or `SO_PRIORITY` 6/7 on the
  socket.
- Verify on the air by A/B: tracking gap p99, repeated-pose rate and fresh FPS at 1000 and 1250
  Mbit/s.
- The Windows side doesn't matter for the uplink.
- **Done (`.67`):** CS7 reached the air but didn't help (3.8–4.8% repeated, against 2.8–2.9% with
  EF). UDP for the stream socket beat TCP (2.85% against 4.2%). EF and UDP stay
  ([TRANSPORT.md](TRANSPORT.md#late-tracking-on-wi-fi-67-68)).

**1.2 Server-predicted poses (if 1.1 isn't enough).**

- When no new tracking has arrived, the server extrapolates the head pose to the frame's display
  time from the latest sample and its velocity, rather than reusing the last pose.
- The frame carries that pose, so the client reprojects with what the frame was rendered from.
- This is a protocol change.
- **Done (`.68`, on by default):** 120 Hz and 1250 Mbit/s over Wi-Fi, ABBA, gave 0.45% repeated
  poses with it and 2.25% without. Wired was unchanged. Still needed: the owner's worn test for
  head-movement stutter, and sustained play.

**1.3 Make UDP the Wi-Fi default** once 1.1/1.2 remove the stutter. Then delete the old
PyroWave UDP path (`pyrowave_udp.rs`, the C++ `VideoSendUdp`).

- **Default done (`.68`):** the stream socket and PyroWave transport default to UDP. USB stays TCP.
- The connection already forces the encoder's `pyrowave_udp` off; the server slices UDP in Rust.
  So the C++ `VideoSendUdp` path is dead code and can go.

**1.4 Wired headroom for 2000 Mbit/s.**

- Use 4 adb connections. At 2000 Mbit/s the burst benchmark gave p99 7–10 ms with 4 connections,
  against 10–11 ms with 2 ([TRANSPORT.md](TRANSPORT.md#usb)).
- 4:4:4 and supersampled streams need this.
- **Done (`.70`):** four connections are the default. They cut network p99 by 2.6–2.7 ms at 1000
  and 2000 Mbit/s, with the same frame rate. At 2000 Mbit/s and 120 Hz a frame takes about 7 ms
  over USB whatever the connection count: the cable is near its limit
  ([BITRATE.md](BITRATE.md#four-wired-connections-69-default-from-70-october-7)).

**1.5 Audio cut-outs** (USB, 207 Hz). Reproduce with audio logging on both ends, and check
whether audio on the stream socket stalls behind video.

- **Instrumented (`.84`):** the client logs `[Q3PW_AUDIO]` every 5 s and forwards it to the
  server log. Each line has packets, the largest gap between packets, the buffer range, silent
  playback batches, recoveries, losses and overflows. The quality scene can play digital silence
  (`Q3PW_SCENE_AUDIO=silence`), so loopback capture has something to send. The harness turns game
  audio off unless a cell turns it on.
- **Not reproduced on the harness (October 8, wired CDF 5/3, 207/1000 at 182–185 fps and 120/1500):**
  - Over about 160 s of steady streaming: 100 packets/s, the largest gap 22 ms, no losses or
    overflows, the buffer 60–70 ms, and no silent batches after the start-up fill.
  - The only gap (3.3 s) came when the harness closed SteamVR Home and before the scene's sound
    started. WASAPI loopback sends nothing while the PC plays nothing, so that gap was harness
    timing, not a stream stall.
  - Video does not starve audio on the stream socket at 207 Hz.
- **Next:** the owner plays a real game on `.84` or later. The cut-outs then show in the
  server log as `[Q3PW_AUDIO]` windows with `silent_batches > 0`. Large `gap_ms_max` would point at
  the PC side (capture or the game). Silent batches with normal packet gaps would point at the
  headset's playback.

**1.6 Refresh-rate switching that works.**

- A leftover `debug.oculus.refreshRate` pins the app's rate probe to 72/80 Hz.
- At 144 Hz the USB display helper looped: it set 144, restarted the client, the value was reset,
  and it repeated.
- A rate change should be one setting and no manual relaunch.
- **Fixed in `.85` (October 8): rates probed while the headset slept.**
  - The server restarts the client after a GPU-level change. If the headset was asleep then, the
    app probed rates it could not switch to: only 72 Hz confirmed, it reported [72, 80], and the
    server refused 207 Hz until the app was reopened.
  - The probe now runs only while the app is shown. A probe that misses 90 or 120 Hz (rates the
    runtime always offers) runs again, at most twice.
  - Checked by starting the app with the headset asleep: no probe until the wake, then all rates
    up to 207 Hz confirmed. Two 207 Hz harness arms streamed at 185–187 fps.
  - HorizonOS writes `debug.oculus.refreshRate=72` itself when a VR app starts with it empty, so
    a 72 there is not a sign of a leftover override.

**1.7 A light play-session logger.**

- Tonight's logger stored every frame's statistics: 39k rows in 4 minutes. It was stopped when
  the PC ran low on memory.
- Store per-second aggregates instead.
- Forward the client's `[Q3PW_TRANSPORT]` counters to the server log. They were missing for the
  Wi-Fi play, so tonight's Wi-Fi artifacts can't be attributed.
- **Done (October 8, `tools/quest3/play_log.py`):**
  - `record OUT` reads only the server's event stream. It writes one CSV row per second (the frame
    count, latency stages, bytes, fresh frames per second, drops, thermals) and the log text.
    Memory stays flat.
  - The client already forwards its error-level lines, so the per-second counters of both ends
    are in the log: transport, tracking arrival, poses, the game's frames, audio.
  - `report OUT` prints a per-minute table: fresh FPS p50/p10, the game's fps, repeated and
    extrapolated poses, the largest tracking gap, the latency estimate and its vsync queue,
    network, decoder, KB per frame, video drops, audio silent batches and gaps.
  - `replay EVENTS_JSONL OUT` does the same for a harness phase.
  - Checked by replaying a 207 Hz wired and a Wi-Fi phase. The Wi-Fi phase gave 0.17–0.19%
    repeated poses, the same as its harness result.

**Exit:**

- Wi-Fi at 120 Hz, 1000 Mbit/s: under 2% repeated poses, and no stutter in an owner test.
- USB at 2000 Mbit/s: clean.

## Phase 2: sharpness and clarity (the main focus)

**2.1 Frame budget from the real frame rate.**

- Cap each frame at `bitrate / measured frame rate`: smoothed, bounded, never above what the link
  can burst.
- A game below the panel rate then gets the whole bitrate.
- This is small and fixes the 207 Hz complaint directly.
- **`.66`/`.67`:** the budget follows the present rate and is on by default. It was neutral in
  tests: SteamVR presents at the panel rate even when the game is slower, because it reprojects
  into a new present every vsync
  ([BITRATE.md](BITRATE.md#frame-budget-from-the-present-rate-66-67-october-7)).
- **`.83` (October 8): Stream only the game's frames**, an opt-in setting.
  - A compositor timing entry with a non-zero client frame interval marks a new game frame.
  - The driver streams only those and lets the headset re-show them.
  - At 207 Hz / 1000 Mbit/s with a game at 121 fps: 1025 KB per frame instead of 604 KB
    (1.7x), with the same 120 new frames a second.
  - A full-rate game is unaffected.
  - Details in
    [BITRATE.md](BITRATE.md#streaming-only-the-games-frames-79-83-october-8).
- **Next:** the owner's headset test of how it looks and feels against off, at 207 Hz with a
  real game below 207 fps. If it is clean, make it the default.

**2.2 A clarity budget: where is sharpness lost?**

Before changing the codec, measure each stage's loss on the PC with the quality harness, at the
headset's pixels per degree:

1. the 3072x3216 to 2080x2208 downsample
2. wavelet quantization at 1000/1500/2000 Mbit/s
3. 4:2:0 chroma
4. FP16 precision in the DWT (PyroWave's author notes PSNR plateaus from it)
5. the client's eye pass and the compositor's lens resampling

The largest loss decides what to build next.

- **Done (October 7, [CLARITY-BUDGET.md](CLARITY-BUDGET.md)):** at 120 Hz the largest loss is the
  compositor's bilinear display resampling: 2.6 dB PSNR-HVS-M at 2080, against 0.7 dB for
  quantization at 1500 Mbps. At 207 Hz / 1000 quantization dominates (3.1 dB). FP16 costs nothing.
  Sharpening 50 recovers 1.2 of the 2.2 dB that a perfect display path would. A bigger eye
  swapchain with a better upscale recovers almost nothing.

**2.3 Supersampled stream.**

- Encode at SteamVR's lens-corrected recommendation (about 2544x2704 per eye) instead of the
  panel's 2080x2208, at 120 Hz.
- After lens correction the panel centre has more pixels per degree than 2080x2208 supplies.
  This is the most likely reason the image doesn't look above native.
- Check decode time against the 8.3 ms frame and fresh FPS, then an in-headset A/B.
- This is mostly settings, so it comes early.
- **Done (`.69`):** Stream resolution gains 110/120/125 %. 125 % (2592x2784) holds 115–118 fresh
  FPS at 120 Hz, with a 7.7 ms decode fence; 150 % reaches only 86. At 1500 Mbps, 2560x2720 keeps
  1.3 dB more PSNR-HVS-M than 2080x2208 offline
  ([RENDER-ENCODE-RESOLUTION.md](RENDER-ENCODE-RESOLUTION.md#supersampled-stream-at-120-hz-68-october-7)).
  The owner's in-headset A/B is still needed.
- At 207 Hz the decoder is already the limit at 100 %, so sharpness there must come from the
  codec (2.4 entropy coding, 2.6 rate control) or a faster decode.

**2.4 Entropy coding (the big rewrite candidate).**

- PyroWave writes bit-planes raw: no entropy coding, by design, for GPU speed.
- That is why it needs so much bandwidth for its quality.
- First measure offline what entropy coding would save on real frames, per 32x32 block: zstd as
  a bound, then an HTJ2K-style block coder estimate.
- HTJ2K (JPEG 2000 Part 15) was designed for parallel GPU coding and costs only about 9% against
  full JPEG 2000.
- If the saving is 25% or more, design a GPU coder for both ends: the encoder on the PC's
  compute, the decoder on Adreno compute.
- The same Mbit/s would then carry much more detail, and that is what makes 4:4:4 affordable.
- **Estimated (October 7, [ENTROPY.md](ENTROPY.md)):**
  - A context coder saves 21–27% of the bits on harness frames: +2.0 dB PSNR at 207 Hz/1000,
    +2.4–2.6 at 207/1500, +3.3–3.5 at 120/1500.
  - A static per-group code without neighbour context saves only 8–10% (+0.6–1.2 dB).
  - zstd saves 11%.
- **Prototyped (October 8, [ENTROPY.md](ENTROPY.md#a-real-coder-and-its-decode-on-the-quest-october-8)):**
  - A per-block rANS coder with quad contexts codes a 120 Hz / 1500 frame 20.5% smaller,
    losslessly.
  - The Quest decodes it in 2.65 ms. The best layout is lane-interleaved stores, the longest
    blocks first and branch-uniform steps. That misses the 2 ms target.
  - It would fit at 120 Hz, fused with dequantization, but not at 207 Hz.
  - **Parked** until the owner's 120 Hz tests show where the clarity gap is.
  - Building it needs:
    - the live encoder's quantized blocks, to confirm the saving;
    - a PC encoder;
    - rate control on coded bytes;
    - a decode fused with dequantization.

**2.5 Modernize 4:4:4.**

- The fast decode paths (CDF 5/3 mode 5 into the AHB, the packed present) are 4:2:0 only.
  4:4:4 falls back to the old path (about 100 fresh FPS in the owner's test, 66–68 in an earlier test in
  [CHROMA.md](CHROMA.md)).
- Add full-resolution chroma to the CDF 5/3 present path, then measure against 4:2:0.
- If full 4:4:4 doesn't fit, keep part of it: 4:2:2, or a larger share of bits for chroma.
- Target 120 Hz at 1500–2000 Mbit/s wired.
- **`.74`: Decoder V2 mode 7** (4:4:4 packed into the present buffer) is the 4:4:4 default for
  CDF 5/3. Wired at 120 Hz: 116 fresh FPS at 1500 Mbps and 109-113 at 2000, against 98-106 before
  (4:2:0: 117-119). At 207 Hz/1000: 132 against 103.
  See [DECODER-V2.md](DECODER-V2.md#mode-7-444-packed-into-the-hardware-buffer-74-october-7).
- Next: the eye pass's chroma reads cost about 21 FPS at 207 Hz; the 4:4:4 decode is about 1 ms
  longer than 4:2:0. Owner test: 4:4:4 at 120 Hz, 1500 and 2000 Mbps.

**2.6 Rate control.**

- Allow bounded borrowing between frames, so a complex frame can take more than its average.
- Re-tune the CSF for 120 Hz and supersampled sizes.
- Revisit how hard coarse bands are quantized.

**2.7 Adaptive sharpening on the headset.**

- Fuse a contrast-adaptive sharpen (CAS or SGSR-style) into the eye shader that already converts
  YCbCr. Opt-in, with its cost measured.
- Virtual Desktop has a sharpening filter on by default and offers Snapdragon GSR, and the owner
  compares against it.
- **Done (`.73`, [SHARPENING.md](SHARPENING.md)):** the Sharpening setting (0–100, off by
  default) applies CAS to luma in the eye shader, in the centre 60 % of each eye. At 50, headset
  screenshots measure about 40 % more detail. Frame-rate cost:
  - 120 Hz with stream 100 %: none.
  - 207 Hz: about 5 FPS.
  - 120 Hz with stream 125 %: about 8 FPS. Whole-image sharpening cost 19 FPS in both of these
    cases.

  **`.75`:** a linear cross sharpen replaces CAS. Offline it keeps +1.69 dB instead of +1.23 at
  120 Hz / 2080, with fewer overshooting pixels, and live it costs about 10 FPS less than CAS at
  207 Hz.

  **Next:** the owner's A/B. A cheaper form would sharpen in the decoder's last pass.

**2.8 FP16 precision.** If 2.2 shows a plateau at 1500 Mbit/s and above, use FP32 for luma or for
the coarse levels. **Not needed:** 2.2 measured no FP16 loss (under 0.002 dB) at 1000-2000 Mbit/s.

**Exit:** the owner sees an image that looks above native and colour that holds up next to
Virtual Desktop, at 120 Hz.

## Phase 3: latency (later)

From the table above, in order:

1. **Vsync queue, 14 ms:** decoded frames wait almost two frames for display. Check the predicted
   display time the client targets, and its queue depth. **Checked:** it is 13.8 ms at 120 Hz and 12.9 ms at 207 Hz,
   nearly flat in milliseconds, so it is the runtime's display pipeline, not a client queue.
2. **Encoder, 5.7 ms:** PyroWave itself encodes 4K in under 0.2 ms, so this is mostly something
   else. Candidates: the downsample, copies, readback, or GPU contention with the game. Trace it.

   **Traced (`.76`-`.78`, [LATENCY.md](LATENCY.md)):** the stage is mostly the game's frame still
   rendering on the GPU after SteamVR hands it over. The streamer's part is about 1.5 ms: render
   0.6-0.9, encode 0.4, CPU 0.5. `.78` encodes on a high-priority compute queue, which cuts the
   encode wait by 0.3 ms. Under a game-like load the streamer's work runs beside the game's, so a
   Vulkan rewrite of the frame render would not help. **Done** for the server side.
3. **Network, 6.8 ms at 1.5 MB per frame:** pipeline it. Send blocks as they are encoded, and
   start decoding coarse levels while fine ones arrive.

   **Traced (`.78`, [LATENCY.md](LATENCY.md#client-side-78-120-hz--1500-frame-trace)):** the frame
   crosses USB in 3.05 ms (4.1 Gbit/s, the link). Decode takes 5.2 ms from submit, 2.8 ms of it GPU
   work; LOW priority lets the compositor preempt it, and default priority lost 2-5 FPS. Overlapping
   transfer and dequantization could save about 1 ms; not started.
4. **Optical motion-to-photon:** measure it, as AGENTS.md requires.

## Phase 4: interface and settings revamp (later)

Getting the owner from "driving home" to "headset on" took about 15 minutes on October 7, and the
interface caused most of it. The parts:

**Profiles instead of knobs.**

- Measured presets: "USB best quality", "Wi-Fi best quality" and "lowest latency". Each sets the
  refresh rate, stream size, bitrate, wavelet, chroma and transport together, from the latest
  measurements.
- The server picks the USB or Wi-Fi profile from the connection actually in use. Unplugging
  switches profile, not just the link.
- Saved custom profiles, switchable without editing `session.json`.

**Changes that apply.**

- Refresh rate, stream size, wavelet and chroma apply with one click, including the panel-rate
  change and the client restart (1.6).
- No manual relaunch, no `adb` and no developer properties by hand. The GPU clock and panel rate
  belong in the app, not the shell.

**A status view, in the dashboard and in the headset.**

- Show what is actually happening: link (USB or Wi-Fi) and its rate, fresh FPS, repeated poses,
  frame drops, bits per frame, the game's frame rate, the latency estimate by stage, GPU clock
  and temperature.
- A warning when the setup is limited: the game below the panel rate, Wi-Fi saturated, the panel
  pinned to 72/80 Hz.

**Fewer, clearer settings.**

- Group the rest into Basic and Advanced.
- Hide or remove experiments that lost (see AGENTS.md "Already tried"), and say in each help text
  what was measured.

**First run.**

- Pair over Wi-Fi without typing an IP. Detect USB and offer the wired profile.
- Check the developer prerequisites and explain any that are missing.

**One install.** A single installer or archive for the server, plus the client APK, with an
upgrade path that keeps settings. The parallel `runtime-local*` folders used in testing stay a
developer tool.

## Other later goals

- **Sustained play:**
  - 30-60 minute sessions at the chosen profiles, logging thermals, battery, clock throttling and
    fresh FPS over time.
  - Short screens don't show these.
- **Game compatibility and quality suite:**
  - A fixed set of real games and scenes (text, foliage, dark gradients, fast motion), each
    captured and scored the same way every time.
  - In-headset screenshots, for regressions in image quality, not just speed (task #6).
- **Automated nightly regression:** the live harness on main every night, reporting fresh FPS,
  latency estimate and quality against the last release.
- **Track upstream PyroWave:**
  - Valve ships PyroWave in Steam Remote Play (4:4:4, HDR), so upstream may improve the codec.
  - Check it for changes worth taking, and keep our patches small enough to rebase.
- **HDR or 10-bit:** check whether banding in dark gradients is visible, and whether 10-bit is
  worth its bits. Measure only for now.
- **User documentation:** a short README path from install to a good first stream, with the
  measured recommendations.

## Stretch goals

- **GPU entropy coder** on both ends (2.4 taken to production).
- **Lens-matched encoding:**
  - Warp the stream to the lens's pixel density, so the centre is encoded above panel density and
    the edges at what the lens shows.
  - It is the same pixel count, and no foveation is visible by construction, since the warp never
    drops below what the panel can show.
- **Conditional replenishment:**
  - Skip 32x32 blocks that didn't change since the last frame the client confirmed, and refresh
    them periodically.
  - That is temporal coding without motion search, for a large saving on static content.
- **4:4:4 at 207 Hz.**
- **Striped frames** for latency: encode, send and decode overlap.
- **Wi-Fi 7 or MLO** if the hardware allows. Not looked at yet.

## Also to revisit

- **Default wavelet:** on `.65` CDF 5/3 matches Haar's speed wired, so it can become the default.
- **Release:** alpha.10 from `.65`, or from the first build of this plan.
- **Task #25:** the trace and ablation documentation is still open.

## Night order (no fixed window; stop in time to restore)

1. **No headset:** 2.2 clarity budget and the 2.4 entropy estimate on captured frames.
2. **Wi-Fi:** 1.1 tracking priority, A/B over Wi-Fi. *Done: 1.1 and 1.2 in `.67`/`.68`.*
3. **Build:** 2.1 frame budget, checked live at 207 Hz with a game below 207. *Done in
   `.66`/`.67`: neutral; it needs the game's own rate. `.83` (October 8) adds the game's rate
   and the opt-in "Stream only the game's frames": 1.7x the bytes per frame at 121 fps on
   207 Hz.*
4. **USB:** 2.3 supersampled stream and 1.4 four connections at 2000 Mbit/s.
5. **Hand-off:** write up results, one PR, and set up the play runtime for the owner's next test.

## Research notes (October 7)

- **PyroWave's design:**
  - 32x32 blocks, raw bit-planes with per-4x2 bit counts, and signs packed at the block end.
  - Rate control is a hard per-frame cap, choosing discards by distortion per bit.
  - The author lists entropy coding as unexplored and points to HTJ2K.
  - Sources: [blog](https://themaister.net/blog/2025/06/16/i-designed-my-own-ridiculously-fast-game-streaming-video-codec-pyrowave/),
    [README](https://github.com/Themaister/pyrowave).
- **HTJ2K:** about 10x faster block coding than JPEG 2000 at about 9% more bits. GPU decoders
  reach hundreds of 4K 4:4:4 frames per second
  ([ICIP 2019](https://kakadusoftware.com/wp-content/uploads/ICIP2019_GPU.pdf)).
- **Virtual Desktop:** offers SGSR, a 12-tap Lanczos-like upscale with adaptive sharpening in
  one pass ([Qualcomm](https://www.qualcomm.com/developer/blog/2024/06/up-your-game-with-snapdragon-gaming-super-resolution),
  [Mixed](https://mixed-news.com/en/virtual-desktop-super-resolution/)).
- **DSCP to Wi-Fi priority:** [RFC 8325](https://www.rfc-editor.org/rfc/rfc8325). Whether a
  mark reaches the air depends on the kernel and driver, so verify by measurement.
