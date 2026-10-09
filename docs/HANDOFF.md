# Engineering handoff: Quest3-Pyrowave

Prepared October 4, 2026, with dated updates through October 8. Read [AGENTS.md](../AGENTS.md)
first. Machine-specific state, raw captures, signing material and rollback snapshots stay outside
this repo.

## Current goal and state (October 7)

The goal is the one in [AGENTS.md](../AGENTS.md#goal). Where it differs from the October 4
objective below, AGENTS.md wins:

- a 3072x3216 per-eye PC render, streamed at 2080x2208 per eye at 207 Hz
- about 200-207 fresh displayed frames per second, at 1000-1500 Mbit/s
- under 30 ms optical motion-to-photon (the October 4 text says 15-20 ms)
- no visible foveation; 4:2:0 is fine

State:

- **Released:** v0.1.0-alpha.9, built from `.63`. **On main:** `.65` (`.64` plus the opt-in
  Wi-Fi UDP transport), not released.
- **On `claude/frame-budget`:** `.93` (`.92` without the dead encoder-side UDP path). It has:
  - server-predicted head poses;
  - UDP as the Wi-Fi default;
  - Stream resolution up to 125 %;
  - four wired connections;
  - the Sharpening setting;
  - a fast 4:4:4 path;
  - a high-priority encode queue;
  - the opt-in setting "Stream only the game's frames"
    ([below](#october-7-late-night-66-74-on-claudeframe-budget));
  - audio cut-out diagnostics;
  - a refresh-rate probe that no longer runs while the headset sleeps;
  - a fix for the server's display helper, which held the panel at 72 Hz after its client restart
    (the stream was refused until the app was reopened);
  - a client restart with `am start -S -W`, plus a retry if the restarted client has no activity
    ([REFRESH-RATES.md](REFRESH-RATES.md)).
- **October 8, later, `.95`-`.97` on the same branch:**
  - `.95` clears a 72 Hz panel pin the headset left behind ([REFRESH-RATES.md](REFRESH-RATES.md)).
  - `.96` keeps the binocular middle at full density in all three peripheral-encoding profiles
    ([LIGHT-FOVEATION.md](LIGHT-FOVEATION.md#where-the-full-density-band-sits-96)).
  - `.97` makes Meta fixed foveated rendering work for the eye draw (hidden setting, off by
    default) and adds layout diagnostics.
  - Result ([table](LIGHT-FOVEATION.md#above-125--through-foveation-october-8-96-97)): above 125 % is
    not cheap at 120 Hz. Strong 148 % with FFR Medium gets 99.7 fresh FPS against about 109 for
    uniform 125 %. At 90 Hz, 148 % holds 87 of 90.
  - **Harness pitfall:** live A/B arms cannot change resolution or foveation (they need a SteamVR
    restart), so such comparisons need one cell per configuration.
  - Next, per the owner: a Virtual Desktop quality baseline, then cheap quality wins, then 207 Hz
    scheduling.
  - **VD baseline, started.** The same static quality scene (3072x3216 source) was streamed through
    Virtual Desktop at the owner's own settings, which were left unchanged:

    | VD setting | Value |
    |---|---|
    | Quality | Godlike, 106 % (SteamVR target 3072x3264) |
    | Codec | H.264+ at 500 Mbps, automatic bitrate off |
    | Refresh | 144 Hz, held 144/144 |
    | Spacewarp | off |
    | Link | PC on Ethernet, headset on Wi-Fi 6E |
    | Reported latency | about 38 ms (VD's own figure, not motion-to-photon) |

    Pyrowave was captured on the same scene and framing (`.99`, 120 Hz, 2496x2656 at 1000 Mbps):
    its text is clearly crisper, its zone plate keeps more detail but shows moiré, and its colour was
    weaker. That colour gap is fixed in `.103`-`.105` (next bullet). The private screenshots stay
    outside the repo.
- **October 8, late night, `.107`-`.108`: full 3072x3216 stream (no downsample).** The owner asked for
  Virtual Desktop's Godlike resolution streamed at full size. Same 3072x3216 render, 2000 Mbps,
  CDF 5/3 mode 5, 690 MHz GPU clock, SteamVR Home, wired:

  | Panel | Configuration | Fresh FPS | Decode p50 (GPU) |
  |---|---|---|---|
  | 90 Hz | full eye swapchain | **90.1 of 90** | 5.4 ms |
  | 120 Hz | full eye swapchain (`.107`, earlier, owner wearing) | 97.7 | 9.0 ms |
  | 120 Hz | full eye swapchain (`.107` and `.108`, later) | 66-68 | 10.5-11 ms |
  | 120 Hz | eye swapchain at 67 % (`.108`) | 71-77 | 8.3-9.2 ms |

  - **Full resolution holds at 90 Hz, not at 120 Hz.** Decode alone takes about 5.1-5.4 ms when the
    GPU is otherwise idle. At 120 Hz the 3072 eye draw (about 3 ms) and the compositor
    (0.9-1.4 ms) preempt it every 8.3 ms, stretching it to 9-11 ms. To fit 120 Hz, decode must
    drop to about 4 ms together with the smaller eye swapchain.
  - `.107` adds an opt-in phase lock (`debug.q3pw.phase_lock=1`): publish decoded frames just
    before the render loop wakes. It gives nothing once decode exceeds the frame period.
    Decoder mode 6 (two chroma pixels per texel) is faster on the bench but slower live at
    3072 (77.8 FPS).
  - `.108` adds a hidden `debug.q3pw.eye_percent` (50-99, unfoveated only): the eye draw goes
    into a smaller swapchain with a bilinear luma tap. Same `.107`/`.108` numbers in the same
    conditions, so `.108` has no regression.
  - `.108` also fixes two server faults:
    - A missing configured audio device now falls back to the default output instead of
      refusing the handshake.
    - The forced GPU level stops after the headset clears it twice, until the server restarts.
      The `display_override` unit test for this has not been run yet.
  - **Tried for 120 Hz at full size, each in ABBA order (fresh FPS):**

    | Change | Result | Notes |
    |---|---|---|
    | Haar instead of CDF 5/3 | 76.5-77 full eye, 79-82 at 67 % (CDF: 66-68 and 73-77) | Bench: 3.7-4.0 against 5.1 ms |
    | Decode at default queue priority (67 % eye, CDF) | 83-87 fresh, but the app drops to 85-87 of 120 frames: judder | Low stays |
    | Render-loop frame wait 0 instead of 4000 µs (Haar, 67 %) | 75-76 against 79-82 | The wait stays |

    Live decode takes about twice the standalone time while the GPU reports 87-91 % busy.
  - **`.109` decode gate, opt-in, rejected as a default.** `debug.q3pw.decode_gate_us=<start>[,<end>]`
    holds each decode until `start` µs after the render loop's wake. A frame later than `end`
    waits for the next period. Unit tested. Haar, 67 % eye, 120 Hz:

    | Arm | Fresh FPS | App frames/s | Decode p50 |
    |---|---|---|---|
    | off | 80-84 | 120 | 7.1-7.5 ms |
    | start 3000 | 80-84 | 120 | 7.1-7.4 ms |
    | window 2500-4500 | 77-78 | 120 | 6.4-6.7 ms |
    | window 2500-4500, default priority | 80-92 | 114-117 (judder) | 5.0-5.7 ms |

    Even serialized, decode takes 5 ms live against 3.3-4 ms on the bench, so full size does not
    fit 120 Hz on this GPU. For 120 Hz, the clarity work moves to the largest stream that fits
    (125-135 %).
  - **125 % in SteamVR Home is slower than the quality-scene numbers.** Fresh FPS at 120 Hz,
    2000 Mbps, unworn headset:

    | Arm | Fresh FPS |
    |---|---|
    | CDF 5/3, 2608x2752, `.109` | 88-91 (decode 7.3-7.8 ms) |
    | Same, PC sharpening off | 91-92 |
    | Same, Quest colour off | 89 (no effect) |
    | Decoder mode 4 instead of 5 | 61-62 |
    | `.97`'s own pair today | 92 |
    | Haar, 135 % | 83 |

    `.97` measured 108-109 at 125 % with 4.8 ms decode on the 60°/s quality-scene pan, so the gap
    is content, not code. Real game content can decode much slower than the quality scene,
    which matters for every "fits at 120 Hz" claim. Re-measure the profiles on real game content.
  - **Largest stream that holds 120 Hz in SteamVR Home (`.109`, CDF 5/3 mode 5, wired, unworn).**
    Each size had its own SteamVR start. Fresh FPS per 20 s block:

    | Stream per eye | Bitrate | Fresh FPS | GPU decode p50 | ALVR decoder stage |
    |---|---|---|---|---|
    | 2080x2208 (native) | 1500 | 118.4-119.9 | 3.4-6.9 ms | 6.2-12.4 ms |
    | 2304x2432 (111 %) | 1500 | 114.9-115.0 | 5.2-5.3 ms | 10.7-11.0 ms |
    | 2560x2688 (123 %) | 2000 | 90.4-90.5 | 7.3-7.4 ms | 14.3-14.6 ms |
    | 2816x2944 (135 %) | 2000 | 77.3-80.7 | 8.3-8.7 ms | 16.3-16.4 ms |

    In this scene, 120 Hz holds up to about 110 %. Full 3072x3216 holds at 90 Hz. Virtual
    Desktop decodes on the Quest's video hardware, but PyroWave decodes on the same GPU as the
    eye draw and the compositor. That is why VD's Godlike preset runs at high refresh and ours
    does not.
  - **`.110` profiles from these sizes.** **Quality 120 Hz** and **Wi-Fi Quality 120 Hz** now
    stream 110 % (2272x2432), the largest size that held 120 Hz; 120 % and 125 % are marked
    "below 120 Hz". A new **Godlike 90 Hz** profile streams the full 3072x3216 with no downsample
    (CDF 5/3, 2000 Mbps, PC sharpening 30, maximum GPU clock).
  - **Full size at 90 Hz in heavy content (`.108`, SteamVR Home with the Library dashboard open,
    wired, unworn).** The earlier 90.1 of 90 was lighter content. Fresh FPS of 90:

    | Wavelet | Bitrate | Fresh FPS | GPU decode p50 |
    |---|---|---|---|
    | CDF 5/3 | 2000 | 64-66 (69 at 690 MHz) | |
    | CDF 5/3 | 1500 | 69 | |
    | CDF 5/3 | 1000 | 76-78 | |
    | Haar | 2000 | 70-74 | 7.7 ms |
    | Haar | 1500 | 78.5-81 | 6.8-7.3 ms |

    Decode cost follows bytes per frame (bitrate / fps; 2000 Mbps at 90 Hz is 2.78 MB). With the
    GPU otherwise idle (`decoder_ab wavelets 6144 3216 <bytes>`, 690 MHz), CDF 5/3 decodes in
    4.53 / 4.96 / 5.43 / 5.75 ms at 0.6 / 1.39 / 2.08 / 2.78 MB and Haar in 3.56 / 3.83 / 4.01 /
    4.17 ms. Dequant is 1.28-1.94 ms of that standalone but about 4.4 ms live at 2000 Mbps
    (iDWT 3.1-3.5 standalone, 4.8 live): sharing the GPU with the 3072 eye draw inflates it
    about 2.3x.
  - **Neither content nor the eye draw explains that inflation.**
    - Fixed foveated rendering (Static Medium, VrApi `Fov=2`) on the eye draw at 690 MHz:
      CDF 5/3 2000 gave 65.8-66.1 and Haar 1500 gave 79.8-82.8, the same as without it.
    - Bench sources: `decoder_ab` now takes `AB_SOURCE=<I420 file>`. At equal clocks, a stock
      photo and a text-heavy dashboard image cost 15-25 % more dequant than the synthetic source,
      at both 1.39 and 2.78 MB per frame. On the dashboard image, Haar and CDF 5/3 are within
      1 dB in luma PSNR: 43.0 against 42.3 dB at 1.39 MB, and 52.5 against 53.3 dB at 2.78 MB. On the photo,
      CDF 5/3 is 1.5-2.7 dB better.
    - VrApi reports about 7 GPU preemptions per vsync in every configuration, at 90 Hz and at
      120 Hz.
  - **`.111`: Godlike runs at 80 Hz; Skip invisible detail.**
    - Full size holds 80 Hz with Haar at 1500 Mbps in the heavy scene: 79.6-80.1 of 80 (`.108`)
      and 78.5-80.0 of 80 (`.111`). CDF 5/3 at 1500 gave 74-78 of 80. The **Godlike** profile is
      now 80 Hz, Haar, 1500 Mbps (renamed "Quest 3 Godlike 80 Hz").
    - **Skip invisible detail** (`video.pyrowave.skip_invisible_detail`, on by default) adds a
      quality ceiling to the encoder's rate control: every discard in a rate-control bucket
      below 56 is applied even when the frame fits its budget. Buckets are about 1.5 dB apart
      in weighted distortion per byte. The new `patches/pyrowave-quality-floor.patch` adds
      `pyrowave_encoder_set_quality_floor()` plus the shader; `PYROWAVE_RDO_FLOOR=<bucket>`
      overrides it for experiments. The bitstream is unchanged, so the client is unaffected.
    - Calibration, `decoder_ab wavelets 6144 3216 2777778` with `AB_SOURCE`:

      | Floor | Photo bytes (dB, Haar / CDF) | Dashboard bytes (dB) |
      |---|---|---|
      | off | 2.78 MB (59.5 / 62.2) | 2.78 MB (52.5 / 53.3) |
      | 54 | 1.61 / 1.21 MB (56.0 / 56.6) | 2.78 MB (unchanged) |
      | 56 | 0.83 / 0.66 MB (54.6 / 55.1) | 2.52 / 2.39 MB (51.6) |
      | 58 | 0.62 / 0.54 MB (53.4 / 54.0) | 2.01 MB (48.8) |
      | 62 | 0.48 / 0.46 MB (51.6 / 52.4) | 1.39-1.46 MB (43.0 / 42.8) |

      The synthetic source was unchanged at 56. CDF decode of the photo fell from 5.76 to 4.11 ms.
    - Live, in the heavy scene at 2.34 MB per frame, frames still fill the budget with the
      floor at 56 (confirmed in `openvr_config`). That content needs more than the ceiling
      allows, so the setting helps only easier content there.
  - **Decode priority at full size (`.112`-`.115`).** At 90 Hz (Haar 1500, heavy scene, ABBA)
    default priority beat LOW: 84.6 / 83.4 against 79.9 / 79.3 fresh, fence 6.7-7.1 against
    10.7-11.0 ms. `.112` therefore chose default at ≤90 Hz. At 80 Hz (the Godlike profile) LOW won:
    79.2 / 79.3 / 80.2 / 78.9 with no stale frames, against 78.6 / 78.0 / 78.3 with 0-10 stale a
    second, and total latency was no lower. `.115` restores the original rule (LOW for 4:2:0 at
    ≤120 Hz) ([DECODE-PRIORITY.md](DECODE-PRIORITY.md)).
    - In those 90 Hz blocks all 90 frames a second arrive and decode. The 6 lost a second are
      superseded: arrival gaps p10/p90 7.6-8.6 / 13.6-14.3 ms against an 11.1 ms period put two
      frames in one display period. Frame ids (pose timestamps) jump by about ±3.7 ms because
      the client polls tracking 3 times a frame and the server renders from the newest sample.
  - **120 Hz keeps LOW (Quality 120, CDF 5/3, 110 %, 1500 Mbps, `.113`).** LOW: 118.6 / 116.1 /
    117.2 fresh, fence 7.0-7.8 ms, VrApi stale 0-4 a second. Default: 118.3 / 113.8, fence
    4.9-5.0 ms, but stale 11-17 a second (judder). ALVR's decoder stage fell from 8-10 to 6 ms
    while its vsync queue rose from 15.3 to 17-21 ms.
    - **`.113` opt-in `debug.q3pw.gl_priority=high|low`:** the client's own GL work (eye copy,
      staging) runs in a second EGL context, shared with wgpu's and created at that
      `EGL_IMG_context_priority` level. The driver grants HIGH (`[Q3PW_GL_PRIORITY]
      requested=0x3101 granted=0x3101`). With default decode priority it gave 116.5 fresh and
      stale 7-12 a second in the one valid block (the other came up pinned at 72 Hz). That is
      not enough to drop LOW at 120 Hz; it stays opt-in.
  - **`.114` opt-in `debug.q3pw.input_polls_per_frame=1..16`** (default 3, as upstream): the
    client's tracking poll rate. Each sample is predicted for `now + offset`, so the server's
    newest sample is up to one poll interval stale (3.7 ms at 90 Hz). Godlike 80, ABBA: 8 polls
    did apply (frame id gaps fall on multiples of 1.56 ms instead of 4.17 ms), but their spread
    grew to ±6 ms (6.25-18.75 ms against 8.25-16.75 ms). The jitter is the game's pose-fetch
    time or tracking delivery moving by several ms, which 3 polls only rounded. Fresh FPS 75.6 /
    74.3 against 78.1 / 76.4, one 8-poll block with 7-12 stale a second: no gain shown, default
    stays 3. Release logcat drops info-level Rust lines, so `[Q3PW_INPUT_RATE]` is not visible.
  - **`.117`: the direct eye copy is the default (`debug.q3pw.direct_eye_copy=0` restores
    staging).** Play sessions never set the property, so every play test until now (and the
    `.111`-`.115` blocks above) ran ALVR's staging renderer. Staging copies the decoded buffer
    into staging textures, waits with glFinish, then draws them again with wgpu. The `.116` trace
    markers (J/I around the swapchain acquire) put 3.2-3.4 ms p50 of the render loop in that
    draw at 120 Hz. The acquire itself takes 0.01 ms. Staging also ignores headset sharpening;
    the owner's sharpening runs on the PC, so the image doesn't change.
    - Godlike 90 (Haar, 3072x3216 full size, 1500 Mbps, LOW priority), `.116`, same session:
      direct 88.8 / 89.4 fresh of 90.1 against staging 79.7 (79.3-79.9 earlier tonight). ALVR
      latency estimate 50.6-52.1 against 63.4 ms. VrApi stale 0-1 a second in both.
    - Quality 120 (CDF 5/3, 110 %, 1500 Mbps): direct 117.3 against staging 118.7 / 119.3, with
      decode GPU 3.2 against 6.3-7.0 ms, app GPU 4.0 against 5.3-5.6 ms, GPU load 0.61 against
      0.83-0.88 and latency estimate 39.8 against 46-50 ms. VrApi stale 2-4 against 0-1 a
      second. The first direct block caught only 4 s of trace at stream start (73 fresh) and is
      discarded.
    - `.117` with no properties set: Godlike 90 88.9 fresh of 90.1, so the default takes the
      direct path.
    - **Full size at 120 Hz on direct (Haar, 3072x3216, 2000 Mbps, `.117`):** LOW 102.2 fresh
      (17.5 frames a second replaced before decode). Default decode priority 111.1 / 111.8 /
      112.6 / 114.1, but the render loop sees only 115-116 of 120 display periods and VrApi
      stale runs 1-22 a second: the synchronous glFinish after the eye draw waits 7.4-7.7 ms
      behind decode. Staging reached 66-82 here on October 8.
    - **Async eye copy at full size 120 Hz (`debug.q3pw.async_eye_copy=1`, default priority) is
      rejected again:** 91.5 / 83.9 against 112.6 / 114.1 synchronous. Stale is 0, but the copy
      completes 8.4-10.8 ms later behind decode and frames are deferred meanwhile.
    - **A high-priority GL context does not shorten that wait** (`debug.q3pw.gl_priority=high`,
      default decode priority, ABBA): 111.2 / 111.0 against 112.7 / 111.5, eye draw to
      glFinish still 4.9-7.5 ms p50, stale 4-18 a second in both.
    - **`debug.q3pw.release_fd=1` (flush and a fence handed to the decoder instead of glFinish)
      at full size 120 Hz, ABBA:** fresh 112.7 / 112.2 against 112.4 / 112.0. The render loop
      now sees 119.8-120.1 display periods instead of 116.4-116.7, and stale was 0-5 a second
      in one block (4-14 without), 0-22 in the other. Same fresh FPS; it stays opt-in, but it
      is the candidate for full size at 120 Hz once a worn test can judge smoothness.
    - Full size at 120 Hz is now GPU-bound: GPU level 7 (690 MHz), GPU load 0.94, decode 4.8 ms
      GPU and 7.3 ms to its fence. 1500 Mbps instead of 2000 gives the same 112.9 fresh
      (decode 4.6 ms). Only less GPU work per frame moves it (decode cost, or the
      eye copy, see [LIVE-SURFACE-VIDEO.md](LIVE-SURFACE-VIDEO.md)).
    - Where the GPU time goes at full size 120 Hz (`.117`, 1000 Mbps, default priority):
      decode stages iDWT 2.4-2.9 ms and Dequant 1.5-2.1 ms per frame; the eye copy's GL timer
      (`debug.q3pw.eye_gpu_probe=1`) reads 2.5-2.7 ms p50, which includes time the GPU spends
      on decode alongside it.
    - **A smaller eye swapchain does not help full size at 120 Hz** (`debug.q3pw.eye_percent=67`,
      ABBA): 113.2 / 113.7 fresh against 113.5 / 113.6, eye timer 2.4 against 2.55 ms. ALVR's
      latency estimate in the same game-stage mode was 49 against 53-56 ms (decoder queue 0.2
      against 4.2 ms); the other 67 % block was in the low game mode (38 ms) and doesn't count.
      Not followed up. `.118` filters that path with a tent as wide as the downscale (same four
      fetches as the old bilinear tap); untested live.
    - **`.118`: decoder mode 6 (paired chroma) is the default** for Haar and Decoder V2. Full
      size 120 Hz, ABBAAB on `.117`: 115.0 / 115.3 / 116.4 fresh against 111.4 / 112.0 / 110.4,
      GPU decode 3.4-4.1 against 4.5-4.6 ms, fewer stale frames. Quality 120: 120.0 / 120.1
      against 119.0 / 118.5 / 119.6. Same decoded planes. Details:
      [PRESENT-YCBCR.md](PRESENT-YCBCR.md). Earlier runs that seemed to show this didn't apply
      mode 6 (the harness set only `debug.q3pw.cdf53v2`), and identical arms still differed by
      3 FPS, so check the applied mode in the log for every arm.
    - Decode priority on direct, Quality 120: LOW 120.0 against default 119.3 / 119.9 with
      fewer stale frames; the LOW rule at 120 Hz and below stays.
    - **`.118` verified with no properties set (wired, 1000 Mbps, `local118-cea89c8`):**
      - full size 120 Hz: LOW (the default rule) 115.4 / 117.1 fresh, default priority 118.3 /
        115.7 / 118.3; `.117` LOW was 102.2. GPU load 0.88 against 0.94, VrApi app GPU 6.0 ms.
        The LOW/default gap is inside the noise and LOW has fewer stale frames, so the rule stays;
      - Godlike 90 full size: 89.5 / 90.1 of 90.1, stale 0-2 a second;
      - Quality 120: 115.1 / 119.7. In the first block every frame decoded, but 5 a second were
        superseded before display.
    - **`.119` (`local119-32861bd`): automatic decode priority picks LOW only up to 2.0 G decoded
      pixels a second**, so full size at 120 Hz (2.37 G) now gets default priority; Godlike 90,
      full size at 80 Hz and Quality 120 keep LOW (unit-tested, not re-run live). ABBABA at full
      size 120 Hz, GPU level 7 in every block: automatic 118.6 / 114.4 / 116.0 against forced LOW
      117.4 / 115.5 / 117.7. That's noise, and LOW no longer replaced frames before decode (R 0-0.3
      a second), so the change is kept only for the `.118` evidence. See
      [DECODE-PRIORITY.md](DECODE-PRIORITY.md).
    - **Check the GPU level per block.** VrApi's `CPU4/GPU=4/N,.../MHz` changed between blocks
      on October 9: the mode 6 ABBAs ran at level 4 (640 MHz) throughout, but the `.118`
      `release_fd` set mixed level 7 and level 4, and a Quality 120 set mixed 690, 640 and 545
      MHz. Those comparisons are confounded. The private harness now prints the level per block.
  - **The maximum GPU clock was not applied in these runs.** `quest3_max_gpu_clock=true`, but
    `debug.oculus.gpuLevel` read empty and VrApi showed level 4 (640 MHz). After the helper's
    `GPU_LEVEL_REVERTS` (2) reapplications it stops until the server restarts. Setting the
    property by hand held through every harness client restart. Check whether the owner's normal
    flow gets level 7.
  - **Client phase lock and latency (`debug.q3pw.phase_lock=1`, native, 120 Hz, 1500 Mbps).**
    Two alternating rounds, medians of ALVR's estimate:
    - Fresh FPS: 118.9-119.9 with the lock, against 118.4-119.2 without it.
    - Decoder queue: 1.8 ms with the lock, against 1.6-2.6 ms without it.
    - Total after the game stage: 35.5-38.6 ms with the lock, against 36.8-42.6 ms without it.
    - The vsync queue is 14.5-14.8 ms in every block.
    - The spread comes from the decoder stage (6-12 ms), not the lock. The lock stays opt-in.
    Details are in [LATENCY.md](LATENCY.md#client-phase-lock-at-120-hz-october-8-109).
  - **Harness traps:**
    - A force-stopped client often comes back pinned at 72 Hz.
    - Set `debug.oculus.refreshRate=120` while awake, before `am start`. That made 4 of 4 starts
      clean.
    - Re-assert proximity every 5 s while measuring, or the unworn headset sleeps after about
      15 s and SteamVR drops to 10 frames a second.
    - Sleep, clear `debug.oculus.refreshRate`, then wake helps but adds a compositor layer
      (TW about 1.4 ms).
    - The running server rewrites `session.json` on shutdown. Edit it only after SteamVR exits.
- **October 8, night, `.106`: wired 120 Hz quality is the first choice in the dashboard.** The owner's
  direction is maximum quality and lowest latency at a smooth 120 Hz over USB, set from the PC
  as Virtual Desktop does.
  - **Streaming profile** now lists **Quality 120 Hz** first (marked recommended for wired),
    then Wi-Fi Quality and Competitive 207 Hz. The fresh install is still the 400 Mbps / 72 Hz
    candidate.
  - **Stream resolution** adds 135 % and 150 % (3096x3312, close to VD's 3072-wide Godlike), labelled "below
    120 Hz": the headset decodes 125 % within the frame and larger sizes at about 90-105 FPS.
  - New **Colour** row on Presets: Vivid (Quest gamut, default) or Accurate (Rec. 709).
  - Dashboard and session tests only. Not run on the headset (the client is unchanged apart
    from its version).
- **October 8, night, `.103`-`.105`: colour now matches Virtual Desktop**
  ([CHROMA.md](CHROMA.md#colour-against-virtual-desktop-october-8-103-105)).
  - `.103` fixes a bug: the headset squeezed every PyroWave frame into 16-235 (a MediaCodec
    work-around), lifting blacks and dulling whites and colour.
  - `.105` adds **Quest colour** (on by default): the stream uses the headset's own gamut, as VD
    does, instead of Rec. 709. Saturation and white point now match VD's screenshot.
  - Frame rate unchanged (119 fresh FPS in the same static cell). Not yet seen by the owner.
- **October 8, afternoon, `.98`-`.99`: sharpening moves to the PC**
  ([SHARPENING.md](SHARPENING.md#in-the-streamer-98-option-99-setting)).
  - New setting **Sharpening location**, PC by default: the streamer sharpens luma before
    encoding, so the Quest does no extra work.
  - On the headset at 120 Hz / 1500 / 2080 (static): PC k=0.15 looked crisper than headset 50 at
    the same frame rate.
  - Two new profiles, **Competitive 207 Hz** and **Quality 120 Hz**, fold in CDF 5/3, the better
    downsample kernel and PC sharpening. Quality held about 117 FPS on the headset while panning;
    Competitive has not been run.
- **October 8, evening: Horizon OS build 209 blocks rates above 120 Hz.** The headset updated itself
  (`ro.vros.build.version=209`, built October 6). Virtual Desktop's developer reports the update
  broke high refresh rates. A 207 Hz cell on `.98` had already looped: the client was offered only
  72/80/90/120 Hz.
  - `.100` stops that loop after four display-change restarts in 90 s and pauses forcing for
    5 minutes. `.101` points its message at the OS update instead of Meta Quest Link
    ([REFRESH-RATES.md](REFRESH-RATES.md#restart-loop-with-meta-quest-link-running-october-8-100)).
  - The 207 Hz work (Competitive profile, not forcing native rates) waits for a fixed OS, or for a
    check that forcing over USB still works on build 209. Until then, work at 120 Hz.
- **Headset client (October 8, late night):** `.108` is installed with its pair
  `workspace/runtime-local108-play` (3072x3216 stream, 90 Hz, 2000 Mbps, game audio off).
  `.107` is staged in `workspace/review/local107-27a064f` with `workspace/runtime-local107-play`.
  Earlier: `.105` (pair `workspace/runtime-local105-e3891a0`) and `.106` (staged in
  `workspace/review/local106-c39362c`). The owner's `.92`
  play pair is still staged (`workspace/runtime-local92-play`). Reinstall its APK before playing
  `.92`, or play the `.99` pair. Virtual Desktop's registration is untouched.
- **Best short screens** (10-12 s, 207 Hz, 2080x2208, Haar, 1000 Mbit/s, 690 MHz GPU clock):
  194-197 fresh FPS over USB with the direct eye copy, 189-195 over Wi-Fi 6E.
- **Not established:** sustained gameplay, optical motion-to-photon latency, image quality on par
  with Virtual Desktop.

**Plan from October 7, evening:** [PLAN.md](PLAN.md). The owner played `.65` and set new priorities:
finish the network stack, then sharpness and clarity (bitrate use, colour), then latency.

The per-claim summary is the [scorecard](WHOLE-STACK-SCORECARD.md). The current next steps are
[after the `.64` section](#next-steps-after-64). The dated sections below are a record and stay as
written.

## Objective and acceptance

First achieve **2080 × 2208 per eye, 120 Hz, 4:2:0, no foveated encoding**, with
PyroWave decoded directly on Quest 3 Adreno using Vulkan. Prioritize fresh frames,
frame pacing, image correctness and latency over bitrate or headline refresh.
The current bridge imports Vulkan AHardwareBuffers into GLES/OpenXR eye images;
presentation is not entirely Vulkan.

Later explore higher resolutions and runtime-supported 207/240 Hz. The owner's
aspirational endpoint is 3072 × 3216 per eye at 207 Hz and 15–20 ms
motion-to-photon. Neither that latency nor sustained native 120 fresh FPS has
been established. Runtime acceptance, submitted frames, GPU completions and
fresh displayed frames are different measurements.

The owner permits improving the workflow, test duration and architecture when
supported by evidence. Preserve correctness, rollback and honest measurements;
the previous agent's process is not mandatory. A handoff does not automatically
resume paused hardware work or unattended workers.

## October 7, late night: `.66`-`.74` on `claude/frame-budget`

Builds from branch `claude/frame-budget`, local only:

| Build | Commit | Change |
| --- | --- | --- |
| `.66` | `c9c38c5` | Frame budget from the present rate, on by default |
| `.67` | `cec2857` | `[Q3PW_PRESENT]` counts the compositor presents the driver receives; the scene gains a frame-time cap |
| `.68` | `864ef69` | Server-predicted head poses; UDP becomes the Wi-Fi default |
| `.69` | `c01280a` | Stream resolution up to 125 % |
| `.70` | `71e5ce2` | Four wired video connections by default |
| `.71`-`.73` | `ae7b380`, `8d0fc77`, `e38216f` | Sharpening: debug property, then setting, then centre-only |
| `.74` | `0f3c97a` | Decoder V2 mode 7: 4:4:4 packed into the present buffer |
| `.75` | `a1cc167` | Sharpening uses a linear kernel instead of CAS |
| `.76` | `11dc2e4`, `43daf79`, `375c3ee` | Encoder stage timing, `[Q3PW_ENCODE_TIMING]` and `[Q3PW_RENDER_GPU]` |
| `.77` | `e7e6bec`, `937ba46` | GPU priority experiments (`ALVR_PYROWAVE_QUEUE`, D3D11 thread priority) |
| `.78` | `90788e7` | PyroWave encodes on a high-priority compute queue by default |
| `.79`-`.82` | `8a91b0d`, `6bc5632`, `f3a77db`, `b7cb64b` | The game's frame rate from SteamVR's frame timing (diagnostics, then detection) |
| `.83` | `8137a94` | Setting: Stream only the game's frames (off by default) |
| `.84` | `8b0b764` | Audio diagnostics: the client's `[Q3PW_AUDIO]` statistics; the scene can play silence |
| `.85` | `df7f838` | The refresh-rate probe waits until the app is shown and re-probes an unreliable result (PLAN 1.6) |
| `.86` | `036a648` | The probe no longer reads a refreshRate of 72 as a pin (HorizonOS writes it) |
| `.87` | `cab68b3` | Proximity held through the server's client restart (rejected: not the cause) |
| `.88` | `fe9e231` | 2.5 s pause in the server's client restart (rejected: 3 of 4 restarts stuck) |
| `.89` | `6b20675` | The display helper writes only the values that change (fixes the 72 Hz hold) |
| `.90` | `cab4918` | `.89` without the `.87` hold; 4 of 4 restarts streamed first time |
| `.91` | `7d332d2` | A restarted client with no activity after 10 s is started once more |
| `.92` | `4b760b5` | The client restart uses `am start -S -W` (12 of 12 direct starts, against 14 of 16) |
| `.93` | `3ee2cdc` | The old encoder-side PyroWave UDP path is deleted (PLAN 1.3); no behaviour change |
| `.94` | `b2d3685` | The panel rate is forced only above 207 Hz (rejected, reverted in `62e4abc`) |

**Frame budget:**

- On its own it is neutral in tests. SteamVR presents at the panel rate even when the game is
  slower, because it reprojects into a new present every vsync
  ([BITRATE.md](BITRATE.md#frame-budget-from-the-present-rate-66-67-october-7)).
- `.83` (October 8) finds the game's new frames in SteamVR's frame timing: entries with a
  non-zero client frame interval.
- With the opt-in **Stream only the game's frames**, the driver streams only those frames. At
  207 Hz / 1000 Mbit/s, a game at 121 fps got 1025 KB per frame instead of 604 KB (1.7x), with
  the same 120 new frames a second.
- A game at full rate is unaffected.
- How it looks and feels in the headset is untested
  ([BITRATE.md](BITRATE.md#streaming-only-the-games-frames-79-83-october-8)).

**Audio (`.84`):** the harness found no cut-outs at 207/1000 or 120/1500 wired. Audio arrived at
100 packets/s with gaps of at most 22 ms, and the headset buffer never ran dry while streaming.
The owner's cut-outs need a real game on `.84`; the server log then shows `[Q3PW_AUDIO]`
windows ([PLAN.md 1.5](PLAN.md)).

**Wi-Fi tracking:**

- On `.67`, UDP beat TCP for ALVR's stream socket: 2.85% against 4.2% repeated poses at 120 Hz
  and 1250 Mbps.
- CS7 didn't help, so EF stays.
- On `.68`, the PC extrapolates the head pose when no sample arrives before a vsync, and the
  headset repeats the same extrapolation to reproject. ABBA gave 0.45% repeated poses with it and
  2.25% without.
- Wired was unchanged: about 187 fresh FPS at 207 Hz, on or off.
- The under-2% checkpoint is met in the harness. The owner's worn test for head-movement stutter
  is still needed.
- See [TRANSPORT.md](TRANSPORT.md#late-tracking-on-wi-fi-67-68).

**Defaults in `.68`:**

- The stream socket and PyroWave transport are UDP; USB always uses TCP.
- `headset.extrapolate_late_head_poses` is on; changing it needs a SteamVR restart.

**Harness notes:**

- Session snapshots read every key the plan sets. A new setting therefore has to be added to
  both runtimes' `session.json` before a cell can set it.
- The Wi-Fi setup replaces the runtime's client entry with `q3pw-wifi.client`. Restore the wired
  entry before a USB cell, or the "wired" cell streams over Wi-Fi.
- October 8: three cells failed with "No stream after relaunch". In each, the panel stayed at
  72 Hz, the client then confirmed only 72/80 Hz, and the server refused 207.
  - One case came between arms, after the server's display helper restarted the client
    ("Failed to find resumed state line").
  - What fixed it, with the headset awake:
    1. force-stop the app;
    2. `setprop debug.oculus.refreshRate ''`, then `207`;
    3. restore the previous value.
  - This is PLAN #33, reliable refresh switching.
  - **Cause** (harness only): the server's GPU-level change wakes the headset and then broadcasts
    `automation_disable`. That also drops the harness's proximity hold, and the headset on the
    desk sleeps.
  - The harness then set `debug.oculus.refreshRate` to the value it already held. HorizonOS
    ignores a set that doesn't change the value.
  - **Fix:** `live56.py` now clears the property first whenever the panel is not at the rate.
    The next cell passed both arms with no relaunch.
  - For a worn headset the helper's release is correct.

**Supersampled stream (`.69`):**

- Stream resolution gains 110/120/125 %.
- 125 % (2592x2784) holds 120 Hz wired: 115.5 fresh FPS with a 7.7 ms decode fence.
- At 1500 Mbps, offline, it keeps 1.3 dB more detail than the panel-size stream.
- See [RENDER-ENCODE-RESOLUTION.md](RENDER-ENCODE-RESOLUTION.md#supersampled-stream-at-120-hz-68-october-7).

**Sharpening (`.71`-`.73`):**

- The setting is Video > PyroWave > Sharpening (0-100, off by default).
- It applies CAS to luma in the eye shader, in the centre 60 % of each eye.
- At 50:
  - no frame-rate cost at 120 Hz with stream 100 %;
  - about 5 FPS at 207 Hz;
  - about 8 FPS at 120 Hz with stream 125 %.
- Headset screenshots measure about 40 % more detail.
- See [SHARPENING.md](SHARPENING.md).

**Fast 4:4:4 (`.74`):**

- Full chroma (4:4:4) with CDF 5/3 now uses Decoder V2 mode 7. Luma, Cb and Cr are packed as
  quads into one buffer, and the eye shader converts them, with no conversion pass.
- Wired at 120 Hz: 116 fresh FPS at 1500 Mbps and 109-113 at 2000, against 98-106 on the old path.
  4:2:0 gets 117-119. At 207 Hz/1000: 132 against 103.
- Screenshot colour matches the old path.
- See [DECODER-V2.md](DECODER-V2.md#mode-7-444-packed-into-the-hardware-buffer-74-october-7).

**Clarity budget (PLAN 2.2, [CLARITY-BUDGET.md](CLARITY-BUDGET.md)):**

- Offline, scored at the panel's 25 px/deg. At 120 Hz the largest loss is the compositor's bilinear
  display resampling: 2.6 dB at 2080, against 0.7 dB for quantization at 1500 Mbps.
- At 207 Hz / 1000, quantization dominates (3.1 dB). FP16 costs nothing.
- Sharpening 50 recovers 1.2 of the 2.2 dB that is recoverable at 120 Hz / 2080. A bigger eye
  swapchain recovers almost nothing.
- So, at 120 Hz / 100 %, Sharpening 50 is the measured choice, pending the owner's look.
- `.75` changes the kernel from CAS to linear: +1.69 instead of +1.23 dB offline at 120 Hz / 2080,
  fewer overshooting pixels, and about 10 FPS cheaper than CAS at 207 Hz
  ([SHARPENING.md](SHARPENING.md)).

**Latency, server side (`.76`-`.78`, [LATENCY.md](LATENCY.md)):**

- ALVR's "encoder" stage (3.0-3.6 ms at 120 Hz / 1500, 5.7-6.5 ms under a game-like GPU load) is
  mostly waiting for the game's frame to finish on the GPU. PyroWave's own encode is about 0.3 ms.
- `.78` encodes on a compute queue at high global priority: the encode's GPU wait falls from
  0.63-0.72 to 0.37-0.39 ms (ABBA) and the stage to 2.0-2.8 ms. `ALVR_PYROWAVE_QUEUE=graphics`
  restores the old queue.
- Probes show the streamer's GPU work runs beside a game's, not behind it, so moving the frame
  render from D3D11 to Vulkan would not help. D3D11 GPU thread priority changed nothing.
- On `.78` the estimate is 39.8 ms. The large stages are the vsync queue (13.7 ms, the runtime's
  lead), network (5.7) and decoder (5.4).

**Next:**

1. Owner tests:
   - 4:4:4 (Full chroma), CDF 5/3, 120 Hz at 1500 and 2000 Mbps, on `.74`.
   - `.68`+ over Wi-Fi with head movement.
   - 120 Hz, 1500 Mbps, stream 125 % with game render 150 %, against stream 100 %.
   - At 120 Hz, stream 100 %: Sharpening 50 against 0. Check whether the edge of the centre region
     shows.
2. Owner test of `.83` **Stream only the game's frames**:
   - 207 Hz, 1000 Mbps, a real game that runs below 207 fps;
   - on against off;
   - look for sharper frames, and for judder or rougher head rotation.
   - If clean, make it the default (PLAN 2.1).
   - Play on `.84` or later, so a cut-out shows in the server log as `[Q3PW_AUDIO]` windows
     with `silent_batches` above 0 (PLAN 1.5).
   - Run `python -m tools.quest3.play_log record <dir outside the repo>` during play, then
     `report <dir>` for a per-minute table of fresh FPS, poses, latency, drops and audio
     (PLAN 1.7).
3. (Done in `.70`: four wired connections by default. Network p99 is 2.6 ms shorter and the
   frame rate is unchanged. See
   [BITRATE.md](BITRATE.md#four-wired-connections-69-default-from-70-october-7).)
4. Entropy coding (PLAN 2.4, [ENTROPY.md](ENTROPY.md)):
   - A context coder would save about 25% of the bits, worth +2.0–3.5 dB.
   - A cheap static per-group code saves only 8–10%.
   - Prototyped October 8 (`tools/entropy/`):
     - A per-block rANS coder codes a 120 Hz / 1500 frame 20.5% smaller.
     - The Quest decodes it, exact, in 2.65 ms. That misses the 2 ms target, but it would fit at
       120 Hz.
     - Parked until the owner's 120 Hz tests. See
       [ENTROPY.md](ENTROPY.md#a-real-coder-and-its-decode-on-the-quest-october-8) for what
       building it needs.
5. Engineering next, as of October 8 (`.93`):
   - **The forced 207 Hz churn** (PLAN 1.6): the fix to try and how to measure it are in PLAN.md.
     `.94` (stop forcing native rates) was rejected: a 72 left by the shell pinned the panel and
     refused one stream. Keep the forced rate on at 207 Hz; its 10-15 s of restarts also
     recover from that 72 ([REFRESH-RATES.md](REFRESH-RATES.md)).
   - **4:4:4 decode at 120 Hz / 2000** (PLAN 2.5): the decoder takes 8.0-8.1 ms of the 8.3 ms
     period. The iDWT is about 3 ms at any bitrate, and dequant grows with bitrate (2.1-2.3 ms at
     2000, 1.5-1.6 at 1500). See
     [FRESHNESS.md](FRESHNESS.md#where-120-hz--2000--444-loses-frames-92-october-8).
   - **Latency** (PLAN 3): ALVR's estimate is 42-61 ms in the test scene. Read the game-stage
     bimodality in [LATENCY.md](LATENCY.md) before comparing runs.

## October 7, night: `.65`, transport measured and Wi-Fi UDP video

- **Question:** would replacing adb or TCP raise usable bitrate? See [TRANSPORT.md](TRANSPORT.md).
- **USB:** adb forwarding is not the limit. NCM USB networking reaches the same 2.3-2.6 Gbps burst
  ceiling, so USB stays on adb.
- **Wi-Fi:** `.65` adds PyroWave → Transport → UDP. It sends the wired path's slices as datagrams
  of at most 1472 bytes, because this network dropped every IP fragment. The client receives them
  with `recvmmsg` and assembles them with the wired code. UDP is opt-in, and USB ignores the
  setting.
- **Live:**
  - UDP never did worse than TCP:
    - 1000 Mbps: 189.7 vs 187.6 fresh FPS.
    - 1250 Mbps, ABBA: 177.3 and 180.7 vs 171.0 and 169.2.
    - 1500 Mbps: ALVR's latency estimate was 42.5 ms vs 112.4 ms.
  - At 1250 Mbps and above, fresh FPS is limited by tracking that arrives late over the busy link.
    The server then repeats a pose: 11% of frames at 1250 Mbps, 43% at 1500 Mbps. That limits both
    transports.
- **New diagnostics:** `[Q3PW_TRANSPORT]`, `[Q3PW_UDP_SEND]` and `[Q3PW_TRACKING_RX]`.
- **Checked:**
  - Windows tests (`fast_build.py test`), run on `c909f7f`, before the diagnostics commits.
  - The live cells above.
  - USB on the final build `7e46ef5`: 193.2 fresh FPS at 1000 Mbps; wired assembly completed
    10338 frames and dropped none; colours correct in both eyes.
  - The headset restored to the `.61` hold client, with the properties read back.
- **Not checked:** other networks, sustained play, perceptual quality and optical latency.

## October 7, late: `.64` on main (PR #18 merged with alpha.9)

- **What happened:** two sessions fixed the `.62` review issues in parallel. #19 (`.63`) was
  released as alpha.9. #18 was then rebased on it as `.64`, keeping the stronger parts of each.
- **From #18:**
  - Wired slices carry a frame sequence number (header bytes 28..32).
  - The client assembles by that number and rejects overlapping or mismatched slices.
  - Writers have a 1 s write timeout.
  - The staging path skips packed frames when it has no YCbCr program.
  - The dashboard's Auto floor text is corrected.
  - These replace `.63`'s skip of repeated timestamps.
- **From `.63`:**
  - USB-only wired device choice with network fallback.
  - The Auto floor fix.
  - The Wi-Fi docs.
- **Dropped:** #18's rename of the "(measured)" profiles to "candidate (short screens)". Saved
  presets, docs and the harness use the existing names; the descriptions already say "short
  screens".
- **Compatibility:** older servers send sequence 0 and the client falls back to timestamps. Older
  clients ignore the bytes.
- **Checked:**
  - Local build of `9145530` over Wi-Fi 6E: 207 Hz, 2080x2208, Haar, 1000 Mbit/s.
  - 193.9 (memory clock changed mid-block) and 190.6 fresh FPS; network 6.2 ms p50.
  - Correct colours in both eyes; restore errors [].
  - That matches alpha.9 (190.0 and 196.5).
  - Rust tests: client_core 73 (3 new wired-video tests), server_core 13, packets 7, adb 17,
    session 39. Python: 183.
- **Not checked:** USB parallel wired video, the path the numbering changes. USB adb was offline.

## Next steps after `.64`

Collected from the `.64`, `.63` and October 7 afternoon notes below:

1. **USB on `.64`.** Once USB adb is back, recheck parallel wired video over USB, the path the
   slice numbering changes. Then run the unplug/replug test with two wired connections. Simulate an
   unplug by restarting the PC's adb server, not with `adb reconnect`, which once left adb offline
   until a physical replug.
2. **Auto on Wi-Fi.** Tune Auto towards the link's real capacity. On the tested link it settled
   at about 550 Mbit/s, about half of what the link carried cleanly.
3. **Tracking over a busy Wi-Fi link** (replaces "parallel connections over Wi-Fi"; `.65`'s UDP
   transport covers Wi-Fi video). Above about 1250 Mbps, late tracking makes the server repeat
   poses. A fix would render from a server-predicted pose for each frame's display time and send
   that pose with the frame. Then decide whether UDP becomes the Wi-Fi default.
4. **P2A, selection.** Find lower-latency selection than the 6 ms frame hold, for example a 2.8 ms
   selection wait with release_fd.
5. **P3, offline quality.** Compare CDF 5/3 and Haar at 1000, 1300 and 1500 Mbit/s.
6. **P4, optical latency.** Measure with a camera at 240 fps or more and at least 50 pairs
   ([OPTICAL-LATENCY.md](OPTICAL-LATENCY.md)).
7. **Sustained gameplay** at the "207 Hz (measured)" profile, with thermals at the 690 MHz GPU clock.
8. **Release from `.64`** after in-headset screenshots of that exact build look correct.

## October 7 evening: `.63`, Wi-Fi, alpha.9

Branch `claude/wifi-adb-fixes`, released as alpha.9. USB adb was offline all evening. Every hardware
run used adb over Wi-Fi (the private harness takes `Q3PW_ADB_SERIAL`) and streamed over Wi-Fi.

**Fixes in `.63`:**

- **Wired mode device choice.** Wired mode now uses only an online adb device with a USB serial.
  Before, it took the first non-loopback device: possibly the headset's own Wi-Fi adb address, or an
  offline USB entry. The handshake loop now falls through to manual IPs and discovery when wired
  isn't ready. Before, the wired entry blocked network connections entirely.
- **Auto bitrate.** The quality floor raises Auto's estimate but no longer overrides the
  network-latency and maximum limits.
- **Parallel wired video.** A frame whose timestamp repeats the previous one is skipped, so two
  frames can no longer mix slices.
- **Packed YCbCr fallback.**
  - Packed frames go to the staging path when the eye copy's packed programs fail to build.
  - The staging renderer no longer panics when its packed program fails.

**Wi-Fi results** ([WIRELESS.md](WIRELESS.md)), Wi-Fi 6E at 6 GHz, PC on 2.5 GbE, 207 Hz, 2080x2208,
Haar:

- **1000 Mbit/s:** 189-195 fresh FPS, about the same as USB. ALVR's network stage is about 6.1 ms
  p50, against about 2.7 ms over USB.
- **1250 Mbit/s:** works, with worse network tails (p99 21-49 ms).
- **1500 Mbit/s:** a TCP queue grows to about 380 ms.
- **Auto:** with an 8 ms latency limit it settles at about 550 Mbit/s.

**Not hardware-tested on `.63`:**

- the USB wired path; USB adb was offline, so only unit tests cover the device choice
- the duplicate-timestamp skip
- the packed-fallback paths, since no driver failure was available to trigger them

Next:

- Recheck USB streaming and the unplug/replug test once the cable is reconnected.
- Tune Auto towards the link's real capacity on Wi-Fi.
- Try parallel connections over Wi-Fi.
- Continue P2A (selection), P3 (quality) and P4 (optical latency) from the afternoon list.

## October 7 afternoon: 207 Hz trace and scorecard

Branch `claude/frame-trace` (on top of `claude/wired-parallel-video`, PR #15). Owner target:
2080x2208 per eye at 207 Hz from a 3072x3216 render, 1000-1500 Mbit/s, about 200-207 fresh FPS,
under 30 ms optical latency, no visible foveation. Details: [FRAME-TRACE.md](FRAME-TRACE.md).

| Item | Result |
|---|---|
| best shipped config | Haar, mode 5, 1000 Mbit/s, two wired connections, maximum GPU clock (690 MHz), raw sRGB eye copy |
| fresh FPS (valid blocks, memory clock 2736 MHz) | 194-197; published 203; display periods 195-200 |
| superseded + empty per second | about 9 + 4 (default); 10 + 11 with release_fd |
| Haar GPU decode | 2.67 ms p50, 3.50 ms p90 |
| eye pass | 1.05-1.10 ms (was 1.28 ms) |
| best opt-in | release_fd + frame hold 6 ms: 199.5 fresh, ALVR latency estimate +5.4 ms |
| quality choice | CDF 5/3 looks much smoother but is decode-bound near 180 fresh FPS at 1000 Mbit/s; Haar is the 207 Hz choice |
| optical latency | not yet measured (P4); frame age at display 30.4 ms p50, ALVR estimate about 30-33 ms |
| limits on 207 Hz | publication jitter against the selection point, 0.8 ms eye-pass fill, uncontrolled memory clock |

Pending:

- **Unplug/replug safety for two wired connections (PR #15).**
  - The simulated test (`adb reconnect` mid-stream) left the headset "offline" to adb until a
    physical replug.
  - The client's behaviour after a real replug still needs a check.
- Lower-latency selection than the 6 ms hold, for example a 2.8 ms selection wait with release_fd.
- P4 optical latency with a phone at 240 fps or more.
- P3 offline quality: CDF 5/3 vs Haar at 1000/1300/1500.

## October 7: findings and planned next steps

Branches: `claude/decoder-v2` (PR #14) holds Decoder V2 (CDF 5/3 with packed YCbCr output, modes
5 and 6). `claude/wired-parallel-video` is stacked on it and adds opt-in parallel wired video
connections. Local fast builds only; no release. Owner settings: 207 Hz, 2080x2208 per eye from a
3072x3216 render, maximum GPU clock on (690 MHz), 700 Mbit/s.

Findings, all live ABBA at the owner's settings with a 60 deg/s pan and 12 s windows:

- **CDF 5/3 costs about 2 fresh FPS at 690 MHz** (mean 192.0 vs Haar 193.5). It is much smoother
  than Haar in the headset: Haar shows blocky, soft spheres. Recommendation: Wavelet = CDF 5/3
  with the maximum GPU clock on. Haar stays the shipped default for now
  ([DECODER-V2.md](DECODER-V2.md)).
- **Bitrate with 5/3:** 700 Mbit/s gives 185 fresh FPS, 1000 gives 179 and 1500 gives 164.
  Quality rises about 1.8 dB PSNR-HVS-M from 700 to 1000. At 700 the stream carries about 0.35 bit
  per sample, so the owner's "bitrate starved" impression is plausible. The cost of more bitrate
  is decode time, because 5/3 dequant grows with bitrate ([BITRATE.md](BITRATE.md)).
- **Parallel wired connections** (`video.pyrowave.wired_video_connections`, default 2) cut ALVR's
  network-stage estimate by 0.7 ms at 1000 Mbit/s and 1.8 ms at 1500, where one adb-forwarded
  connection queues frames. Fresh FPS is unchanged: the headset GPU is 97-98 % busy in every cell,
  so decode sets the frame rate.
- **Latency budget** (ALVR's own estimate, not motion-to-photon) at 1000 Mbit/s is about 33 ms in
  total:

  | Stage | Time |
  |---|---|
  | vsync queue | 10.7 ms |
  | decoder stage | 7.3 ms |
  | client compositor | 5.0 ms |
  | network | 2.6-3.8 ms |
  | encoder | 2.2 ms |
  | game | 1-2.6 ms |

- **Standalone V2 mode 5 profile at the 1000 Mbit/s cap** (decoder_ab with
  `PYROWAVE_V2_LEVEL_TIMES=1`; the bench reported a 492 MHz clock because the GPU level was unset):

  | Stage | Time |
  |---|---|
  | total p50 | 2.78-2.88 ms |
  | iDWT, all levels | 2.15 ms |
  | level 0 | 0.59 ms |
  | level 1 | 1.22 ms |
  | level 2 | 0.2 ms |
  | levels 3-4 | 0.1 ms |
  | dequant | 1.25 ms, overlapping the iDWT |

  Level 1 taking twice level 0 points to it waiting on the finest bands' dequant. That is a
  hypothesis and has not been checked.
- **Noise:** the headset memory clock moves between 2092, 2736 and 3196 MHz from session to session
  (VrApi `Mem=`). It is not pinned by the GPU level and causes cell-to-cell spreads of up to 12 FPS.

Planned next steps, in order:

1. **Shorten 5/3 decode, which caps the frame rate.**
   - Attribute level 1's 1.22 ms: dequant overlap or iDWT.
   - Reorder or split dequant so the bands each iDWT level needs finish first.
   - Bring Haar's dequant speedups (bit-plane decode) to 5/3, whose dequant grows with bitrate.
   - Target: 5/3 at 1000 Mbit/s at 2.5 ms or less, which should give about 195+ fresh FPS.
   - Run the bench at GPU level 7 so standalone numbers match the owner's clock.
2. **Haar with two connections at 1500 Mbit/s.** Haar decode barely grows with bitrate, so faster
   arrival may turn into frames there.
3. **Latency.**
   - The vsync queue (about 2.2 frames at 207 Hz) and the decoder stage (7.3 ms) dominate.
   - Check the client's submit timing against the predicted display time.
   - Measure real motion-to-photon with the optical latency stamp ([OPTICAL-LATENCY.md](OPTICAL-LATENCY.md)).
4. **Memory-clock noise.** Look for a way to pin or record it per cell; until then use more
   repetitions.
5. **`wired_video_connections` now defaults to 2** (owner's call, e325386, checked live with a
   session that lacks the key). Still to do: a sustained-play session, a cable unplug/replug, and a
   3-4 connection cell.
6. **In-headset quality suite with motion** (task 6), CDF 9/7, then the in-headset screenshots a
   release needs before publishing.

## October 6 (later): decoder mode 3, 160-180 fresh FPS at 207 Hz native

PR #13 (`claude/haar32`) makes the Haar 4:2:0 decoder default to multilevel Haar with quad-packed
coefficients and a packed luma plane (`debug.q3pw.haar32`, default 3; see
[HAAR32.md](HAAR32.md)). Live at 207 Hz, 2080x2208 per eye, 1000 Mbit/s: 160-180 fresh frames/s
against about 120 before, GPU decode p50 2.8-3.0 ms against 5.55 ms. 207 Hz is the highest rate
with the full native panel (240 Hz drops the panel to 1552x1664 per eye). Mode 3 needs 4:2:0;
4:4:4 falls back to the old decoder.

Next, in order (details in HAAR32.md "Next steps"): faster bit-plane decode in dequant (work in
progress, verified on CPU only), interleaved chroma output, the conversion pass, then queueing.
The client is still GPU-throughput bound (GPU ~96 %).

## October 6: 240 Hz streams, higher resolution and refresh measured

`.56` (`79e38af`, signed CI 37396414259, installed) reports a system-forced 240 Hz display to the
server, so SteamVR runs at 240 Hz; `tools/quest3/refresh_scaling.py` applies and restores the
override reliably. 240 Hz exists only as a 3104x1664 panel mode (1552x1664 per eye). Short screens:
229.7 fresh FPS at 1280x1376 and 222.7 at 1440x1536 per eye; 207 Hz reaches 196 at 1440x1536 but
only 117 at native size; above native size at 120 Hz the decoder sets the rate (89 at 2560x2720,
65 at 3072x3216). GPU level 7 helps 240 Hz slightly; LOW priority above 120 Hz, server phase lock
and PR #8's fused kernel (correct, no speedup) are not adopted. Details and limits:
[HIGH-REFRESH.md](HIGH-REFRESH.md), [REFRESH-RATES.md](REFRESH-RATES.md).

## Current engineering state

This section, "Read these first" and "Highest-value next experiment" describe the October 5 state
at 120 Hz. They are kept as a record. For the current state see
[Current goal and state](#current-goal-and-state-october-7) and the
[scorecard](WHOLE-STACK-SCORECARD.md).

PR #3 integrates diagnostics and safety work while keeping the experiments off.
See the [integration review and excluded Haar candidate](PR-3-REVIEW.md).
The owner's Codex hardware pause remains in effect; merging source does not
resume unattended tests. Installed-build statements below are historical records,
not fresh device readbacks. The [scorecard's October 5 summary](WHOLE-STACK-SCORECARD.md#earlier-scorecard-october-5-120-hz)
records the later `.50`/`.51` screens; the `.51` installation report is in its git history.

**Chip-level levers (October 5, draft PR #7, not run on hardware):** [ADRENO-740.md](ADRENO-740.md)
and [XR2-GEN2-SOC.md](XR2-GEN2-SOC.md) rank GPU, CPU, memory, DSP, USB and power levers. Built
behind default-off flags: `debug.q3pw.lpac`, `debug.q3pw.eye_invalidate` and
`debug.q3pw.thread_hints`. The always-on `[Q3PW_GPU_CAPS]` and `[Q3PW_SOC_CAPS]` logcat lines
say which of them this firmware exposes, so read them first in the next authorized session.

The `.48` prerecord worker is now active and measured, but **not promoted**.
All matching builds passed; actual native activation, preparation and direct-only
eye completions were verified. Same-session ABBA eye completion proxies stayed
116–117/s and p1 near 60; preparation worsened native completion tails to about
12.3 ms p99 versus 8.5–8.6 ms control. Keep sync default and prerecord off.
Estimated latency shifted about one runtime interval, not an optical gain.
See [measurements, hashes and limitations](PRODUCER-PRERECORD.md).

Installed reviewed `.48` is left with the mode off; temporary properties and
both sessions were restored, physical proximity restored, Virtual Desktop-only
driver registration and no VR/client/capture workers verified. Older pairs remain.
Native libraries EXACT `.47` reuse its small/native exact-pixel proofs including
partial-warm rejection. `.47` streaming windows remain excluded for inactive
allocation. Source fixture was native-sized; no supersampling quality claim.

Latest [LOW+release handoff](RELEASE-LOW.md) confirms exact native-size fenced
reuse and active GPU imports. CPU waiting drops ~1 ms and copy deferrals vanish,
but delivery/p1 do not improve. Keep release off; investigate producer resource
lifetime and presentation scheduling before another mechanism.

Latest [LOW+async comparison](ASYNC-LOW.md) verifies actual GPU-fence polling:
CPU eye-render time fell ~1.63→0.59 ms, but eye completions stayed ~117/s and
p1 near 60. Keep synchronous default; no sustained or optical acceptance.

| Item | Evidence / limitation |
| --- | --- |
| Public release | [alpha.7](https://github.com/JMS1717/Quest3-Pyrowave/releases/tag/v0.1.0-alpha.7), matching `.15` APK/server |
| Development pair | Reviewed `.42` (`ef1e8cc`), all matching CI jobs and exact default/three candidate GPU readbacks passed. Dedicated Haar kernels remain off by default after three short comparisons; see [findings](HAAR-PAIRS.md). `.41` static Surface orientation/lifecycle passed; normal video remains on GLES. Older matching pairs retained. Recheck actual installation before hardware work |
| User feedback | Positive manual playtest after overlay/OpenXR repairs; not sustained FPS, optical latency or broad game acceptance |
| Best short native screens | `.33` at 120 Hz / 1000 Mbps / 4:2:0: about 116–118 displayed target FPS (2.4–4.8 lost/s across sessions, `.34` unfiltered arm included) with the default wait and LOW decode priority. Without the priority it was 110–114, and without the wait 107–110 ([fresh-frame loss](FRESHNESS.md), [decode priority](DECODE-PRIORITY.md)). Stationary chart, not sustained gameplay |
| Baseline recommendation | Haar/Compute, full-frame native encode, USB/TCP, 120 Hz request, 1000 Mbps, 4:2:0; experimental fence paths off |
| 4:4:4 | Optional quality mode. Prior matched screens regressed performance; spare bandwidth does not make it free |
| 2000 Mbps | Short idle/Home comparison increased estimated latency about 11 ms versus 1000; not a controlled gameplay/optical measurement |
| High refresh | 144/207 requests accepted on tested OS. Short 144 Hz screens: about 134 displayed FPS at 1000 Mbps / 4:2:0, about 74 at 2000 Mbps / 4:4:4 (decode-bound). No sustained delivery claim. 240 rejected in tested configuration |

See [manual playtest](PLAYTEST-2026-10-02.md), [decode findings](DECODE-PIPELINE.md),
[chroma](CHROMA.md), [bitrate](BITRATE.md) and sanitized JSON under `results/`.
The current local session can differ from this historical baseline. Refresh live
state before testing; the private handoff includes a fresh disk snapshot.

## Read these first

1. [Fresh-frame loss](FRESHNESS.md): measured cause, `.31` default wait, interleaved A/B method.
   [Decode priority](DECODE-PRIORITY.md): `.33` eye copy preempts decode at ≤120 Hz / 4:2:0.
   [Compositor filtering](COMPOSITOR-FILTER.md): `.34` opt-in supersample/sharpen cost.
2. [Ready-fence experiment](READY-FENCE-EXPERIMENT.md): correct on GPU, rejected live; opt-in only.
3. [Nightfall synchronization review](NIGHTFALL-SYNC-REVIEW.md): ownership/lifetime audit.
4. [Independent render/encode resolution](RENDER-ENCODE-RESOLUTION.md): already implemented.
5. [Benchmarking](BENCHMARKING.md), [build](BUILD.md), [unattended safeguards](OVERNIGHT.md).
6. [OpenXR routing](OPENXR.md), [overlay](OVERLAY.md), [light foveation](LIGHT-FOVEATION.md).

## Highest-value next experiment

**207 Hz branch (October 5, no hardware run):** [PATH-TO-207.md](PATH-TO-207.md) adds
Balanced and Strong peripheral profiles (about 24% and 35% fewer encoded pixels,
candidates), an optical latency stamp and a no-decode cadence probe. Fused final
colour is #4's Quest-verified implementation ([FUSE-COLOR.md](FUSE-COLOR.md)): byte-exact,
no live 120 Hz gain, off. #8 adds an experimental fused dequant + level-0 Haar kernel behind
`debug.q3pw.dequant_haar` with a software exact-pixel gate; the client refuses it while fused
colour is active. The hardware plan starts with the Quest `dequant_haar_gate` and the 207 Hz
lobby cadence.

Use the [next overnight plan](NEXT-OVERNIGHT.md) and the
[October 5 scorecard](WHOLE-STACK-SCORECARD.md#earlier-scorecard-october-5-120-hz). The server already submits
about 120 frames/s. About 3 unique targets/s are still missed after the
half-frame wait, and that wait is already at its cap. `.49` passed review with
native libraries identical to `.48` and is not installed.

**October 5 measurement:** `.50` (`d0a77ef`) counted those expiries. In the
steady 25-second window every empty expiry was still decoding (56/56) and none
were idle. Unique targets stayed 117.7/s with 3.9 lost/s. GPU decode was about
5.9 ms and conversion about 0.8 ms, inside an 8.0 ms fence. Do not repeat
prerecord, async copy, release fences, or another wait sweep, and do not build
a presenter to recover these misses. Two concurrent GPU decodes remain unsafe
with current shared scratch/query/upload lifetimes. The open lever is shortening
that 5.9 ms GPU decode. An explicit 6 ms wait was measured on `.51` and is not
the default: unique targets did not move, and compositor stale counts roughly
doubled.

**Haar [3,2] live, October 5:** Reviewed `.52` (`f3a7184`) was measured, then
the branch removed that candidate. See [the format defect](PR-3-REVIEW.md).
Binding 2 is declared `r16f` while the final plane is `R8`. A 12 s off/on/off
at 120 Hz, 1000 Mbps, 4:2:0, 4160×2208 still ran: GPU decode went from 5.91 ms
to 6.37 ms p50, unique targets from about 118/s to 112/s, and lost targets from
about 2.3/s to 7.6/s. The flag was restored off. The standalone −50% result was
a different device, a locked 788 MHz clock, and 4:4:4 with no compositor. On
Quest the GPU sat at 640 MHz and about 87% busy, with roughly 700 preemptions
per second. Drop this port. A corrected shader needs an exact-pixel proof
before another live comparison. Adreno reports a 64-lane compute subgroup and
32 KB of shared memory; that does not explain the 5.9 ms, and retuning this
[3,2] port is not justified.

**Reported fused final color, October 5:** `.53` (`656a81b`) skips the final luma Haar
store when `debug.q3pw.fuse_color=1` and writes RGBA from that wavelet plus the
4:2:0 chroma planes in the existing fragment pass. A 12 s off/on/off at 120 Hz,
1000 Mbps, 4:2:0, 4160×2208: combined GPU decode+convert 6.62 ms to 5.40 ms
p50 (−18%), fence 7.94 ms to 6.89 ms (−13%), lost targets about 3.8/s to 1.7/s.
Unique targets moved only about 117/s to 118/s. These are preliminary reported
short-screen results, not sustained or exact-pixel acceptance. That implementation
is preserved on `experiment/fuse-color-review` and excluded from this integration;
`debug.q3pw.fuse_color` is not implemented by the integrated `.51` decoder. Require
exact-pixel and setting-lifetime checks before proposing it for integration.
Native 120 sustained/optical acceptance remains unmet. The historical evidence
below records earlier hypotheses; completed experiments are not pending work.


**Producer audit, October 5:** [bounded overlap](PRODUCER-OVERLAP.md) found an
existing Granite context-readiness method, but it has locking/recycling side
effects and is absent from the C API. Three AHBs can all be occupied by a lease,
pending output and active decode. Measure packet-arrival/recording/publication
overlap and free-slot availability before implementing a second submission.
A conditional pre-recorded successor with only one GPU submission is a smaller
proposal; no wait has been removed and no new native artifact was built.

**Latest payload screen, October5:** same-session native120/4:2:0/LOW comparison
of1000/800/600/800/1000Mbps used verified live directives and exact frame-byte
caps. Controls agree near118.1 eye completions/s;800 varied118.4→117.5,600 reached
118.7 once. GPU/completion times did not decrease consistently; p1 remains near60.
Keep1000 default and avoid an unchanged sweep. [Metrics and limitations](BITRATE.md#current-native120-payload-isolation-october5).
Next inspect presentation/completion scheduling. Earlier async-copy screens
predated the LOW decode-priority change; audit ownership and the actual prior
configuration before deciding whether that combination is a new useful test.

**Latest CPU scheduling screen, October 5:** `.44` replaced repeated50µs
selection sleeps with a bounded condition-variable notification, off by default.
All matching CI jobs passed, including40 Linux production decoder tests with
nine new race/FD cases. Its native libraries are byte-identical to GPU-verified
`.42`. The candidate executed223/194 real waits with zero fallback, but completed
115.82/116.03 eyes/s versus controls118.28/115.54. No consistent delivery gain;
keep it disabled and avoid an unchanged repeat. Private compositor images retain
correct orientation and eye mapping; sustained/optical acceptance remains unmet.
[Source, matching artifacts, replay tooling and sanitized findings](https://github.com/JMS1717/Quest3-Pyrowave/blob/experiment/publication-event/docs/PUBLICATION-EVENT.md).
Main retains `.42`; `.44` remains a documented experiment. Next isolate payload
versus fixed reconstruction/completion cost with the current native120 path,
or investigate removing the GLES bridge with exact image/pose ownership.

**Latest rejection, October 5:** the reviewed `.43` vector-dequant branch passed
all matching builds and six exact GPU checks (baseline/vector/combined at two
sizes). Stage-enabled medians suggested small headroom, but the diagnostic-off
repeat found no consistent GPU, completion or delivery gain. Keep it disabled;
do not repeat unchanged screens. [Implementation and measured findings](https://github.com/JMS1717/Quest3-Pyrowave/blob/experiment/dequant-vector/docs/DEQUANT-VECTOR.md).
Main retains `.42`; `.43` stays a documented experiment with matching artifacts.
The subsequent publication-notification screen above found no consistent gain;
notification itself does not establish GPU or optical completion.

**Latest kernel screen, October 5:** `.42` row-wise Haar lowered inverse-transform
stage averages to 2.8–3.0 ms versus roughly 3.1–3.6 ms, but delivery still overlaps
115–118 completion events/s with p1 near 60. Candidates ended at599MHz and controls
at640MHz. Diagnostic-off repeats suggest a small benefit, insufficient for default
promotion or sustained120 acceptance. Dequantization remains about2.6–3.0ms.
Read [source, matching build, exact GPU checks and three comparisons](HAAR-PAIRS.md).
Next examine dequant shader work or remove the GLES bridge with exact image/pose
ownership. Avoid repeating the same screens without a new hypothesis.

**Latest update, October 5:** `.40`'s tiny static Surface image
passed one-shot Vulkan enqueue, layer submission, both fences and orderly
STOPPING retirement on Quest 3. A private compositor screenshot exposed a
vertical flip. `.41` corrected rows only for that diagnostic in the existing
GLES-bound session. Matching builds, exact default GPU readbacks, screenshot
orientation and orderly shutdown passed; video/pose identity remains unverified.
Read [the result and remaining gates](SURFACE-CHART.md#quest-3-result-lifecycle-passed-orientation-rejected).
This screen found no speedup and does not establish live decoded-video identity,
color fidelity or pose/content pairing. The normal GLES path is retained.

Awake `.38` stage diagnostics showed dequantization (~2.6–2.8 ms) and inverse
transform (~3.2–3.6 ms) both contribute. Diagnostics remain off. Restarts yield
roughly 115–118 submission/completion events per second with p1 near 60; smooth
sustained native 120 remains unmet. Read
[the stage comparison](DECODE-STAGE-PROBE.md#awake-vr-comparison).
A Surface handle cannot enter the ordinary OpenXR eye acquire/release loop.
Establish exact decoded-image selection and matching pose before replacing that
loop. The following entries document earlier hypotheses.

**Update 2026-10-04 (evening):** removing the convert pass is not possible
this way: Quest 3 gralloc has no R8 AHardwareBuffer, so GLES cannot sample the
decoded planes ([details](DECODE-PIPELINE.md)). `.34` adds opt-in compositor
supersampling/sharpening. `supersample_hq+sharpen_hq` costs about 0.26 ms of
compositor GPU and no measurable FPS at 120 Hz ([details](COMPOSITOR-FILTER.md)).
It needs a headset-on visual comparison before it can become a default.
Larger lead: present through `XR_KHR_android_surface_swapchain` with Vulkan WSI
from the convert pass. That would remove the GLES eye copy (p50 about 1.55 ms
CPU), but pose/content pairing is the risk.

**Update 2026-10-04 (later):** with equal GPU priority, the GLES eye copy
queued behind the Vulkan decode (eye-copy CPU p90 about 7.5 ms). A LOW decode
queue fixes that at 120 Hz (+3–5 displayed FPS, half the lost frames) but costs
about 10 FPS at 144 Hz, where decode is throughput-bound. `.33` applies it only
at ≤120 Hz / 4:2:0. At 144 Hz / 1000 Mbps / 4:2:0 the GPU is close to full:
decode about 5.4 ms, convert 0.75 ms and copy about 0.65 ms in a 6.9 ms frame.
Next: remove the YCbCr→RGBA convert pass by sampling the decoded planes in the
eye copy. [Details](DECODE-PRIORITY.md).

**Update 2026-10-04:** the corrected ready-fence GPU probe passed on Quest 3.
Saved captures show lost frames come from publication landing 1–2 ms before the
post-`xrWaitFrame` selection, not from transfer or decode spikes. Interleaved
live screens on `.30` showed a bounded wait while a frame is decoding raises
displayed target FPS at 120 Hz / 1000 Mbps / 4:2:0 from about 107–108 to
113–116. Ready-FD early publication lowered it. `.31` enables the wait by default
(`debug.q3pw.frame_wait_us=0` disables). The saved 144 Hz / 2000 Mbps / 4:4:4
profile is decode-bound at about 74 displayed FPS. See
[fresh-frame loss](FRESHNESS.md).

Next: the remaining loss is late packet arrival, not decode. Check server send
pacing against the client's selection phase. Measure 144 Hz at 1000 Mbps /
4:2:0, where decode (about 7 ms) is near the 6.9 ms period. Then validate in
gameplay. The original `.29` notes follow.

`.29` adds default-OFF `debug.q3pw.ready_fd=1`: publish the AHardwareBuffer and
Vulkan SYNC_FD early so the renderer can enqueue a checked EGL GPU wait. It
retains one in-flight decode, producer completion checks before codec/resource
reuse, and synchronous completion of both eye copies. It may remove a CPU
handoff delay; it does not yet overlap multiple producer decodes.

The first small GPU probe was **inconclusive**: its post-decode
`pyroclient_is_ready` assertion confused packet readiness with GPU completion.
The first fenced read matched the reference, but that does not prove the full
lifecycle. Commit `e32596f` corrected the probe to use fence completion, GPU
queries and exact readback. Its diagnostic build succeeded, with three native
libraries matched byte-for-byte to the reviewed `.29` APK. **The corrected probe
has not run on-device; the ON/OFF live comparison remains pending.** Preserve
failed evidence instead of overwriting it.

Suggested sequence, adaptable by the next developer:

1. Audit source and corrected probe; establish installed binary hashes and a
   recoverable baseline before touching hardware.
2. Once hardware use is authorized and available, run corrected GPU correctness
   checks under a new output label. Stop on corruption, timeout or lifetime failure.
3. If correct, compare OFF/ON/OFF with identical geometry, chroma, source,
   thermal conditions and overlay state. Count native completions, actual eye
   copies, superseded frames, p1/gaps and estimated latency separately.
4. Keep optional unless a repeatable benefit appears. Do not simultaneously
   increase bitrate, enable 4:4:4 or change foveation.

Future multi-flight work must cover slot-owned commands/fences/queries, shared
YUV/codec/scratch resources, staging, descriptor lifetime, failures and teardown.
**Granite defaults to two frame contexts; the AHardwareBuffer ring has three
slots.** A third output slot does not extend staging lifetime. Do not delete a
completion wait without bounded ownership and cross-submission dependencies.
Nightfall is inspiration, not proof its model can be transplanted unchanged.

## Source layout and reproducibility

This publishable repo contains cumulative patches and canonical helpers. Local
ALVR/PyroWave trees are reconstructed inputs, not additional publishable repos.
[sources.lock.json](../sources.lock.json) pins upstream inputs.

`sh tools/ci/fetch_sources.sh <new-destination>` reconstructs ALVR, PyroWave and
Granite, applying research patches followed by Quest patches. Use a new
destination; never overwrite existing reconstructed trees.

| Change | Canonical location |
| --- | --- |
| Native decode / AHB bridge | `tools/pyroclient/pyroclient.cpp`, `.h`, fence helpers and GPU probes |
| ALVR client, server, settings | `patches/quest3-alvr.patch` |
| PyroWave integration changes | `patches/quest3-pyrowave.patch` |
| Ready-FD helpers | `tools/fences/native_ready.rs`, `ready_wait.rs`, `ready_frames.rs` |
| Light peripheral mapping | `tools/foveation/light.glsl` |
| Benchmark/control tools | `tools/quest3/`, `tests/` |

The fetch script copies the four canonical Rust/GLSL files into ALVR **after**
patching. Edit repo originals and mirror them locally as needed; exclude duplicate
copies when regenerating the cumulative patch.

Local reconstructed trees contain a staged research baseline and unstaged Quest
changes. Preserve both. Diff against the correct research baseline, include new
files deliberately, and use Git's binary patch output to avoid PowerShell
encoding changes. Validate reverse/forward application and fresh reconstruction
before publishing. A change absent from canonical patches/helpers disappears in CI.

Useful reconstructed paths: `alvr/client_core/src/video_decoder/`,
`alvr/graphics/src/{stream,direct_eye}.rs`, `alvr/client_openxr/src/`,
`alvr/server_openvr/cpp/platform/win32/{FrameRender,VideoEncoderPyroWave}.cpp`,
`alvr/server_core/`, `alvr/session/src/`, and PyroWave decoder/Granite resources.

## Settings and presentation traps

- `emulated_headset_view_resolution` sets SteamVR recommended per-eye source
  size (`target_eye_resolution_*`). `transcoding_view_resolution` sets stream
  size (`eye_resolution_*`). Both align up to 32: 3072 × 3216 becomes 3072 × 3232.
  Existing PC composition downsamples; separation needs no codec redesign.
- Global/per-app/game scaling and cached recommendations change actual game
  submission size. Verify textures; settings alone are insufficient.
- Direct-eye compatibility concerns decoded and Quest eye sizes, not equality
  between PC source and encode size.
- Optional `.2` sharpening uses neutral color controls; it cannot recover
  discarded detail and can produce halos.
- Forced `debug.q3pw.overlay_visible=1` overrides controller toggling. Clear for
  manual use. Click both thumbsticks and release both before rearming.
- SteamVR OpenXR fixed a VDXR form-factor failure with ALVR active. Preserve
  Virtual Desktop's service/driver and exact rollback.
- Light foveation stays optional: its screen reduced pixels 11.594% and GPU
  decode time but did not establish sustained/perceptual acceptance.

## Efficient validation and publication

Short screens are 10-12 seconds after a 3-5 second settle, per AGENTS.md and
[BENCHMARKING.md](BENCHMARKING.md). Promising results still need repeated sustained
thermal/gameplay validation. Improve test design rather than rerunning everything.

When nobody is playing VR, iterate with the [local fast build](LOCAL-BUILD.md)
(`python tools/local/fast_build.py build`): about 1-3 minutes per edit for a stable-signed
APK plus the Windows streamer, versus about 20 minutes per CI run. It refuses to start while
SteamVR runs. Releases are local builds of a clean, committed and pushed tree, checked in the
headset first; CI cross-checks every PR push.

Use [CI](../.github/workflows/ci.yml) for heavy builds while the PC may be used:

```text
python -m pytest -q tests
gh workflow run ci.yml --ref main -f tests_only=true
gh workflow run ci.yml --ref main
gh workflow run native-probes.yml --ref main
```

Choose checks appropriate to the change. At `.64` the suite had 183 Python tests,
and the Rust unit tests of the touched crates passed (client_core 73, server_core 13,
packets 7, adb 17, session 39). CI adds portable C++ ownership checks and software
GLES mapping/readback. `tests_only` produces no installable pair; native-probes
produces diagnostics, not a release. Native/protocol/shader edits need matching
reviewed builds; check packaged library/shader hashes, version, certificate and
provenance before deployment.

Start the stereo fixture after the client settles. Use normalized charts with
explicit source sizes; verify scene coverage, FOV, reference hashes, actual
submitted size and current process/session provenance. Under early publication,
generic ALVR decode/queue timings can reorder; use native timing and full-loop
estimates. Optical motion-to-photon remains unmeasured.

Overlay, eye timers, release-FD, frame wait/poll, scheduler, light-foveation and
geometry screens are already documented. Rerun to answer a new question or
resolve uncontrolled evidence, not to retrace history.

Commit/push only to `JMS1717/Quest3-Pyrowave` as JMS1717 with the configured
noreply email. Retain upstream credits/licenses and PayPal support links. Keep
raw captures, sessions, serials, keys and rollback material private. Passing CI
alone does not justify a release or a performance claim.


## October 5 `.55` integration completed

[PR #9](https://github.com/JMS1717/Quest3-Pyrowave/pull/9) merged after the signed
`2b289f8` install and 26 valid short comparison windows. The corrected 207 Hz
no-decode probe reached 206.9 FPS/15s; no 207 streaming or optical proof. Optional
profiles decode less but Strong completion 7.12 ms still misses 4.83 ms. Adaptive
filter tail pacing was weaker, so amended `9da8560` keeps Bilinear fresh/legacy
defaults and explicit Adaptive choices. All five signed CI jobs and matching-pair
review passed; all three native decoder libraries EXACT tested2b. [Report](PR-9-REVIEW.md).

Temporary hardware settings/proximity were restored and VD registration retained.
Tested2b pair remains installed, amended pair available but not deployed. Old
pairs and private evidence are preserved. Sustained native120/gameplay, menu
Apply/restart, perceptual quality and optical latency remain acceptance gates.
PR8's later fused-dequant follow-up533dfc4 remains separate/draft against main;
these integration results do not validate its new kernel.
