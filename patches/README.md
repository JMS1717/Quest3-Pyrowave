# Patches

Local modifications to third-party clones, kept as patches so the clones themselves (2–4 GB each)
stay out of this history. Each one applies to a public upstream commit, so patch + base fully
reconstructs the tree.

Regenerate a patch with `git diff --binary --full-index --output=patches/<name>.patch` from the
clone (`--output` rather than a shell redirect, so PowerShell cannot re-encode the bytes). Check one still
matches its clone with `git apply --check -R patches/<name>.patch` — that check is the real
guarantee, because it passes only when the clone equals base + patch.

Two traps when regenerating, both of which have silently produced a patch that dropped work:

- **`git diff` omits files the patch *adds*.** They are untracked in the clone, so they simply do
  not appear. Run `git add -N <each new file>` first (intent-to-add), then `git reset -- <them>`
  afterwards to leave the clone's index as it was.
- **Do not build a pathspec list from the patch's `+++` lines.** Binary diffs (the prebuilt
  `.cso` shaders) have no `+++` line, so they get dropped from the list and then from the patch.
  Take paths from the `diff --git a/... b/<path>` lines, or pass no pathspec at all. Note also
  that zsh does not word-split an unquoted `$var`, so a space-joined file list passed as
  `git diff -- $FILES` becomes one nonexistent path and yields an empty diff.

| Patch | Upstream | Base commit | Applies to |
|---|---|---|---|
| `alvr-20.13.0-galaxy-xr-client.patch` | `alvr-org/ALVR` | `7eda092` (v20.13.0) | older client-only subset, package `alvr.client.galaxy2013`; superseded by the cumulative patch below, do not stack them |
| `alvr-20.13.0-server-instrumentation.patch` | `alvr-org/ALVR` | `7eda092` (v20.13.0) | **cumulative: the whole 20.13.0 clone** (71 files: client, PyroWave decoder, streamer, frame ids, bitstream tap, PyroWave encoder, the beta's trimmed settings and streaming profiles, headset telemetry and the dashboard test report; version `20.13.0-pyro.1`); apply it alone |
| `alvr-20.14.1-galaxy-xr-client.patch` | `alvr-org/ALVR` | `a9f6542` (v20.14.1) | the version-comparison client, package `alvr.client.stabletest` |
| `alvr-ca2deca-XRWIRED.patch` | `alvr-org/ALVR` | `ca2decae968f2fd37b43b777cca4ba597808ba52` | abandoned direct-USB/stereo experiment, package `alvr.client.dev` |
| `pyrowave-galaxy-xr.patch` | `Themaister/pyrowave` | `d2997ac` | older subset: the standalone decode CLI on Galaxy XR, see below; superseded by the next row |
| `pyrowave-cdf53-haar-experiments2-3.patch` | `Themaister/pyrowave` | `d2997ac` | **cumulative: the whole pyrowave clone** (14 files incl. the regenerated `shaders/slangmosh.hpp`): Galaxy XR fixes, CDF 5/3 and Haar, precision, the Experiment 3 decoder API; apply it alone |
| `openxr-sdk-2b99fec-hello_xr.patch` | `KhronosGroup/OpenXR-SDK-Source` | `2b99fec` | the `hello_xr` distortion-grid / stereo decoder test app (9 files); the repo's `.gitattributes` makes checkouts CRLF, so compare ignoring line endings |
| `alvr-20.14.1-galaxy-xr-client.stale-20260919.patch` | — | — | superseded snapshot, kept for reference only |

CI builds pyrowave from `d2997ac` plus, in order, `pyrowave-cdf53-haar-experiments2-3.patch`,
`quest3-pyrowave.patch`, `pyrowave-prerecord-api.patch` and `pyrowave-fuse-color.patch`
(`tools/ci/fetch_sources.sh`). The last one adds only the per-decoder, default-off
`pyrowave_decoder_set_skip_final_luma_idwt()` option; see [FUSE-COLOR](../docs/FUSE-COLOR.md).

## Notes

`alvr-20.13.0-server-instrumentation.patch` began as a streamer-only patch beside the client patch.
Since the PyroWave work (shared `CodecType`, DecoderConfig, client decoder) it is **cumulative**:
base + this one patch reproduces the entire clone, and it no longer stacks on the client patch.
Verified by applying each current patch to a clean checkout of its base and comparing
every changed file with the working clone (ALVR 20.13.0, 20.14.1, ca2deca and pyrowave byte-exact).
Among other things it adds
`target_timestamp_ns` and `video_packet_bytes` to `GraphStatistics`
(`alvr/events/src/lib.rs`), populated at the emit site in
`alvr/server_core/src/statistics.rs`. Both values were already in scope there —
`video_packet_bytes` is what the existing `throughput_bps`/`bitrate_bps` are derived from — so
this is a struct-literal edit with no new locking, no call-path change and nothing on the encoder
thread. It resolves half of the upstream TODO above that literal (the nanosecond timestamp part;
the dashboard's graph-origin half is left alone).

Why it matters: stock `GraphStatistics` gives no way to tell which frame a sample describes, so a
capture can only be summarised, never joined to anything measured per frame. `per_frame_rows()` in
`tools/alvr_events_summary.py` consumes these fields and deliberately returns nothing for an
unpatched capture rather than unkeyed rows.

The same patch adds `BitstreamTap`, which writes the encoded stream to disk for offline analysis.
`<base>-<timestamp>.h264`/`.h265` is the access units concatenated with nothing added, so ffmpeg
decodes it directly; `<base>-<timestamp>.idx` says where each frame sits
(`pts_ns,offset,bytes,idr`) and ends with `# frames=N dropped=M`. Read it with
`tools/xrbench/bitstream.py`.

Three things it is careful about, each learned the hard way:

- **It never blocks the encoder.** `CEncoder::Run` gates the SteamVR presentation thread through
  `WaitForEncode()`, so file I/O inline in `Transmit()` would delay the next frame and inflate the
  latency being measured. `Submit()` only queues; a writer thread drains it, and a full queue
  drops frames rather than stalling.
- **Files open on the first frame, with a per-session name.** A sweep restarts SteamVR twice per
  segment and the last restart encodes nothing; opening eagerly with `"wb"` truncated the capture
  that had just been made.
- **Counters go in the index, not the log.** ALVR's `Info()` goes to its event stream, which the
  benchmark's telemetry capture filters down to statistics, so anything logged there is invisible.

Measured cost: about **2.3 GB per 33 s segment at 400 Mbps**, and the client reconnects when a
sweep ends so a tap left enabled keeps writing. `sweep.py --tap` therefore sets
`ALVR_BITSTREAM_TAP` around each segment's SteamVR restart and clears it in a `finally`.

**Building the patched streamer** needs an ALVR source tree on the PC, which the prebuilt
`<workspace>\ALVR-20.13.0` install is not. The working arrangement is
`ALVR-20.13.0-src` alongside it: `git archive HEAD` from this clone (~2.3 MB — `openvr/` is a
submodule and is excluded, so add `openvr/headers` and `openvr/lib/win64` by hand), with `deps`
junctioned from the ALVR master clone that is already set up there. Then
`cargo xtask build-streamer --release`.

## Gaze-driven foveation needs a *per-eye* centre shift

The two eyes have mirrored, asymmetric horizontal frusta, measured on this headset:

```
[VIEWS] fov[0] deg: left -54.5 right +39.9 up +52.6 down -52.6 (h 94.4 v 105.1) ipd 62.2 mm
[VIEWS] fov[1] deg: left -39.9 right +54.5 up +52.6 down -52.6 (h 94.4 v 105.1) ipd 62.2 mm
```

Vertical is symmetric and identical, so Y can share one value. Horizontal cannot.

`FoveatedRendering.hlsli` mirrors the right eye's UV in `TextureToEyeUV` and then applies the same
`centerShift` to both. One shift `s` therefore puts the left band centre at `0.5 + c0*s` and the
right at `0.5 - c0*s`. Solving what each eye actually needs for a gaze angle `theta`:

```
s_left  =  1.625*tan(theta) + 0.4596
s_right =  0.4596 - 1.625*tan(theta)
```

These agree **only at theta = 0**, and diverge by `3.25*tan(theta)` -- over half the total shift
range by 20 degrees off centre.

This is not an upstream bug. With a *static* centre, mirror-symmetric bands are exactly right for a
viewer looking straight ahead, which is what upstream assumes. It only breaks once the centre moves.

It also corrects an earlier decision recorded in this file: per-eye `center_shifts` were dismissed
because the runtime offers only a combined gaze ray. That conflated two different things. Per-eye
*gaze data* is indeed unavailable; per-eye *shift* is required anyway, because a single combined
ray still projects to different normalised positions in two asymmetric frusta.

The symptom is an asymmetry a user notices immediately -- gaze tracking that feels better to one
side than the other -- because each eye carries a different error that grows in opposite directions.

### Two bugs the per-eye change exposed, both worth remembering

**`FoveationVars` must stay a multiple of 16 bytes.** Adding the right-eye pair took the C++ struct
from 48 to 56 bytes. D3D11 rejects a constant buffer whose size is not a multiple of 16, so
`CreateBuffer` threw and the only visible symptom was:

```
[ERROR] Your GPU does not meet the requirements for video encoding.
Failed to initialize CEncoder:
```

-- i.e. no video at all, which looks nothing like a foveation problem and sends you hunting in the
wrong place. The HLSL cbuffer packs those float2s into four 16-byte registers, so the struct is
padded to 64 bytes with a `static_assert` to keep it that way.

**A shift of exactly +/-1 is a singularity.** `hi_bound_c = c0 * (s - 1) / c2 + 1` is exactly 1 at
`s = 1`, and `a_right`, `b_right` and `c_right` all divide by `1 - hi_bound_c`. At the limit that is
a division by zero: the coefficients become infinite and the inverse warp renders garbage in the
outer region of whichever eye clipped. `MAX_CENTER_SHIFT = 0.9` keeps the edge region a usable
width.

This is easy to hit, and the way it presents is a good diagnostic. The asymmetric frustum already
spends `s = 0.46` looking straight ahead, so the far side clips at only ~18 degrees of gaze:

```
s_left  = 1.625*tan(theta) + 0.4596   -> +1 at theta = +18.4 deg (looking right)
s_right = 0.4596 - 1.625*tan(theta)   -> +1 at theta = -18.4 deg (looking left)
```

So looking hard *left* corrupts the *right* eye and vice versa -- a crossed, single-eye artefact
that points straight at shift saturation rather than at the warp maths.

### Rebuilding the server's HLSL

`CompressAxisAlignedPixelShader.cso` and friends are **prebuilt and checked in**, pulled in by
`include_bytes!` in `server_openvr/src/graphics.rs`; the build does not compile HLSL. Changing a
shader means regenerating the `.cso` by hand. The original flags were recovered by bisecting until
the output matched the checked-in file **byte for byte**:

```
fxc /nologo /T ps_5_0 /E main /O3 /Qstrip_debug /Qstrip_reflect /Fo <out>.cso <in>.hlsl
```

(`/Qstrip_reflect` is the one that matters for size; without it the object is 2792 bytes against
the reference 1936.) fxc ships with the Windows SDK, at
`C:\Program Files (x86)\Windows Kits\10\bin\<ver>\x64\fxc.exe`.

## Passthrough on Galaxy XR needs ALPHA_BLEND, not XR_FB_passthrough

ALVR gets passthrough from an `XR_FB_passthrough` (or `XR_HTC_passthrough`) layer composited under
the stream layer, and `passthrough.rs`'s `PassthroughLayer::new` bails outright when neither is
present. Measured on this headset:

```
fb_passthrough: false
htc_passthrough: false
"XR_ANDROID_passthrough_camera_state"
"XR_ANDROID_composition_layer_passthrough_mesh"
```

So ALVR's passthrough **silently never worked here** -- the layer failed to construct, the option
appeared to do nothing, and nothing said why. The Galaxy XR does passthrough through Google's
Android extensions instead.

Implementing those is not necessary, because the runtime supports core OpenXR's alpha blending:

```
[BLEND] environment blend modes: Ok([OPAQUE, ALPHA_BLEND])
```

ALVR hardcoded `EnvironmentBlendMode::OPAQUE` at every `xr_frame_stream.end` call. The client now
selects `ALPHA_BLEND` when passthrough is configured *and* no FB/HTC layer exists *and* the runtime
offers it. The shader already produces the alpha; the compositor puts passthrough behind it.

**Use RGB Chroma Key, not Blend, for a screen surrounded by passthrough.** The two modes differ in
a way the setting names hide (`stream.wgsl:151`, `stream.rs:440-450`):

- **Blend** sets `alpha = 1 - threshold` **uniformly for every pixel**. The whole image becomes
  semi-transparent — a ghost overlay, the "AR glasses" effect its help text describes. It does
  *not* make dark areas transparent.
- **RGB / HSV Chroma Key** computes a per-pixel mask from distance to a key colour, so keying on
  **black (0, 0, 0)** makes the surround transparent while the screen stays solid. This is the one
  that gives "one giant screen, passthrough everywhere else".

Caveat to watch for on-device: near-black content *inside* the picture keys out too, so dark scenes
may show passthrough bleeding through. Keep `distance_threshold` small, and if it bites, HSV chroma
key can key on low value specifically rather than on proximity to black in RGB.

### `dim_surround`: one slider from passthrough to a dark theatre

Added to both `RgbChromaKeyConfig` and `HsvChromaKeyConfig`, flagged `real-time` so it can be
changed live. It is a **floor on the keyed-out area's alpha**, not a replacement, so kept pixels
stay fully opaque whatever it is set to:

```wgsl
alpha = max(mask, pc.blend_alpha);   // blend_alpha carries dim_surround in chroma-key mode
```

The compositor does `result = premultiplied_colour + passthrough * (1 - alpha)`, and the keyed-out
area has colour 0, so its alpha alone decides how much passthrough survives:

| `dim_surround` | surround appearance |
|---|---|
| 0.0 | passthrough cameras at full brightness |
| 0.5 | passthrough at half brightness |
| 1.0 | solid black, no passthrough |

So a single slider runs from a see-through room to a dark theatre around the screen, which is what
the 3D Giant Screen workload wants. `blend_alpha` was previously read only in Blend mode and unused
in chroma-key mode, so it was free to carry this.

This matters for the 3D Giant Screen workload, which is the expected way to play 99% of the time
-- one 16:9 screen with passthrough everywhere else, rather than a virtual theatre.

Note the wider consequence for that workload: if everything outside the screen is passthrough or
black, then the screen occupies about **27% of the frame area** (70% of FOV horizontally by 38%
vertically), and roughly **three quarters of every encoded frame carries nothing**. Standard FFE
cannot exploit that, because it has one centre and one edge ratio; three-tier screen-aware
foveation can, and that raises its value well above "later optimisation".

## Refresh rate is negotiated silently, so it must be verified not assumed

`client_openxr/src/lib.rs` builds the advertised rate list from the runtime when
`XR_FB_display_refresh_rate` is available, and otherwise falls back to a hardcoded
`vec![72.0, 90.0]` for Galaxy XR. The **server then picks the closest advertised rate to
`video.preferred_fps` and only `warn!`s** (`server_core/src/connection.rs:619-637`):

```rust
for rate in &streaming_caps.supported_refresh_rates {
    let diff = (*rate - initial_settings.video.preferred_fps).abs();
    ...
}
```

So asking for a rate that is not advertised does not fail — it quietly runs at a different one.
That matters because any encoded-resolution budget derived from the frame period is then wrong in
the unsafe direction, and the symptom (blown decode) looks like the resolution being too high
rather than the rate being wrong. The client therefore logs the list once at startup:

```
[RATES] enumerated=[60.000004, 72.00001, 90.0] from_runtime=true
```

**Measured on this headset: 60, 72 and 90 Hz are all available**, enumerated from the
runtime rather than from the fallback. The Android display agrees -- `dumpsys display` lists
7104x3840 (**3552x3840 per eye**, confirming the panel spec) at 90 / 72.00001 / 60.000004, with
`presentationDeadlineNanos` of 11111111 / 13888888 / 16666666. The 60 Hz figure is what the Giant
Screen and Seated Quality presets spend on resolution.

## Gaze staleness: measured, and why 2d needs no prediction

Gaze rides in the same `target_timestamp`-keyed `Tracking` packet that drives the rendered frame,
so the gaze steering a frame is exactly **one pipeline latency old** when that frame is displayed
(mean 83 ms, p95 97 ms at 72 Hz). The design question for 2d was whether that staleness demands
gaze prediction. It does not, and this is the measurement rather than an argument.

`gaze_arrival_diag` compares each frame's gaze angle against the one `GAZE_LAG_FRAMES` (6, ~83 ms)
earlier and accumulates the angular displacement. Measured over **5106 frame-pairs**, headset worn,
deliberate rapid saccades between far-apart targets:

| percentile | displacement over ~83 ms |
|---|---|
| p50 | 0.6 deg |
| p95 | 6.7 deg |
| max | 27.6 deg |

The FOV needed to interpret those numbers is recorded nowhere in a capture, so the patch also logs
it once per connection from `connection.rs`'s `ClientControlPacket::ViewsConfig` arm:

```
[VIEWS] fov[0] deg: left -54.5 right +39.9 up +52.6 down -52.6 (h 94.4 v 105.1) ipd 62.2 mm
```

Note the horizontal FOV is **asymmetric** (-54.5 / +39.9), as stereo overlap requires, so frame
centre is not straight ahead -- `center_shift` maths must not assume symmetry.

`center_size` is the fraction of the frame the full-resolution centre occupies (`_axis()` in
`tools/alvr_ffe_calc.py`: `edge_size = target - center_size * target`). At the default 0.45 x 0.40
the centre spans **42.5 x 42.0 deg**, a half-width of **+/-21.2 deg**. So:

- p95 staleness of 6.7 deg is **32%** of the centre half-width -- the fovea stays well inside full
  resolution 95% of the time with no prediction at all.
- The 27.6 deg worst case does exceed the half-width, but it is a saccade, and vision is
  suppressed during saccadic transit, so there is no resolvable detail to lose while it happens.
  Fixation completes on roughly the same timescale as the next gaze sample arriving.

**Decision: drive `center_shift` directly from gaze, no prediction.** `center_size` stays a cheap
tunable if margin is ever wanted; enlarging it costs encoded pixels, which the codec tables price.

## Server-side diagnostics must log at `error!` too, for a different reason

`alvr-20.13.0-server-instrumentation.patch` adds `gaze_arrival_diag()` to
`server_core/src/tracking/mod.rs`, called from `report_face_data` -- the per-frame tracking receive
path, right where the `target_timestamp`-keyed packet is handled.

It logs at `error!` because ALVR's server `Info()`/`info!` goes to its event stream, which the
benchmark's telemetry capture filters down to statistics; `error!` instead reaches
`ALVR-20.13.0/crash_log.txt`, which persists as a file and which `sweep.py` already copies out as
`failure_alvr_crash_log.txt`. This is the same "the log you need is the one being filtered" trap as
on the client, reached by a different route.

It reports **yaw/pitch in degrees, not the raw quaternion**, because that is the quantity 2d
consumes: gaze is already head-local at this point (`report_face_data` applies
`last_head_pose.inverse()`), so forward is -Z and the angles are the offset from straight ahead --
directly what `FoveationVars`' `center_shift` is driven from.

**Measured with W1-400 over Wi-Fi, headset worn, head deliberately held still while looking
left/right/up/down:**

| axis | min | max | range |
|---|---|---|---|
| yaw | -27.5 deg | +37.4 deg | 64.9 deg |
| pitch | -37.6 deg | +27.5 deg | 65.1 deg |

Both axes swing widely and symmetrically, at physiologically plausible magnitudes. The control in
the same run was 72.0 fps / 398.2 Mbps / 0 packets lost / 14.6 ms decode, so the instrumentation
costs nothing.

The contrast with a run where the headset was **not** being worn is what makes this proof rather
than assertion: there, pitch drifted +15.9 to -27.5 while yaw stayed pinned between +0.5 and +1.2,
i.e. the signal followed the headset being physically handled, not eyes. A single-axis swing is
therefore not evidence of gaze; both axes moving under a still head is.

Note `eye_gazes` arrives as `[Some(pose), None]` -- `left true right false` in the log -- which is
the combined ray from `XR_EXT_eye_gaze_interaction`, not per-eye data.

## Client-side diagnostics must log at `error!`

`alvr-20.13.0-galaxy-xr-client.patch` adds `gaze_diag()` in `interaction.rs`, and it logs at
**`error!`** on purpose. Anything lower is invisible exactly when it matters.

`client_core/src/logging_backend.rs` installs an `android_logger` whose format closure is
`if send_log(record) { writeln!(...) } else { Ok(()) }`. `send_log` returns `false` when the
record's level is below the server's client log level -- but only once `LOG_CHANNEL_SENDER` has
been set, which happens when the client connects. So:

- **Before** connection the channel is `None` and it "always print[s] everything", which is why
  startup lines like the OpenXR extension dump always reach `logcat`.
- **After** connection, a sub-threshold record is dropped from `logcat` *and* the event stream.

`get_eye_gazes` only runs inside `stream_input_loop`, i.e. exclusively while connected, so an
`info!` diagnostic there can never be observed. This cost a full debugging cycle: the gaze code was
working and the diagnostic proving it was being discarded, which read identically to gaze being
dead. The same trap applies to any future client-side instrumentation.

A second, smaller trap in the same module: `LOG_REPEAT_TIMEOUT` collapses identical messages
logged within 1 s. A steady-state diagnostic throttled to one line per second sits exactly on that
boundary, so `gaze_diag` includes the frame number to keep every line unique.

**What the Galaxy XR actually provides.** The working path is `XR_EXT_eye_gaze_interaction`
(a single *combined* gaze ray, so `get_eye_gazes` returns `[Some(pose), None]`), reached only
after moving `create_space` to after `attach_action_sets` -- the same ordering bug as
CheekyFoveatedDLSS#31. The hand-declared `XR_ANDROID_eye_tracking` tracker does **not** create on
this runtime, so `eye_tracker_android` is `None` and the code falls through. Because there are no
genuine per-eye poses, backporting master's per-eye `FoveatedEncodingParams.center_shifts` buys
nothing here and would bump the protocol to dev13, breaking comparability with the 20.13.0
baseline data.

`alvr-ca2deca-XRWIRED.patch` (19 files) makes MediaCodec decode straight into a
`SurfaceTexture`-backed `GL_TEXTURE_EXTERNAL_OES` texture instead of ALVR's
`ImageReader`/`AHardwareBuffer` path, adds Galaxy XR accommodations, and builds NVENC-only
(`ALVR_NVENC_ONLY`, no Intel VPL). It is **abandoned** — `alvr-20.13.0` solves the same decode
problem far more simply with the `xrw.decoder_name` named-codec override, and the two approaches
touch the same functions incompatibly. Two pieces in it are still worth lifting: `ProbeTexture()`
in `VideoEncoderNVENC.cpp` (a CPU-readable staging copy of the pre-encode texture — a real
pre-encode pixel tap) and its throttled per-frame telemetry across
`Present` → `CEncoder::Run` → `Transmit` → `ParseFrameNals`.

`alvr-20.14.1-galaxy-xr-client.stale-20260919.patch` no longer applies (fails at `lib.rs:255`);
the clone gained `[72.0, 90.0]` refresh rates, `encoder_10_bits` and `last_display_period` logging
after it was taken.

## 20.13.0 vs 20.14.1 comparability

`client_core/src/video_decoder/android.rs` is **byte-identical** between pristine v20.13.0 and
v20.14.1, so the `xrw.decoder_name` override ports between them unchanged. It is
applied to both, and the two patched files hash identically
(`5f1429e82d55cf724fe87b3cff79ea51e5f3c1c2bd7ed584880c0c11259f59c7`).

Neither release detects the Galaxy XR — `Platform::SamsungGalaxyXR` only exists upstream after
v20.14.1 — so both patches fall back to `alvr_system_info::model_name() == "SM-I610"`.

One asymmetry remains, and it is deliberate until someone decides otherwise. In the client
capabilities block the 20.13.0 patch overrides three fields with `galaxy_xr || …` while the
20.14.1 patch overrides only `encoder_10_bits`:

| field | 20.13.0 | 20.14.1 |
|---|---|---|
| `foveated_encoding` | `galaxy_xr \|\| platform != Unknown` | `platform != Unknown` |
| `encoder_high_profile` | `galaxy_xr \|\| platform != Unknown` | `platform != Unknown` |
| `encoder_10_bits` | `galaxy_xr \|\| platform != Unknown` | `galaxy_xr \|\| platform != Unknown` |

The pristine block is identical in both releases, so this is a gap in the older patch, not upstream
drift. Because the Galaxy XR resolves to `Platform::Unknown`, on 20.14.1 the client reports
**no** foveated-encoding and **no** H.264 high-profile support. Any 20.13-vs-20.14 comparison is
therefore also a with/without-high-profile comparison unless this is closed first.

The package id and label differ on purpose (`alvr.client.galaxy2013` vs `alvr.client.stabletest`)
so both APKs can be installed side by side.

## The PyroWave encoder in the streamer

`alvr-20.13.0-server-instrumentation.patch` now also carries `VideoEncoderPyroWave`, which
replaces NVENC when `ALVR_PYROWAVE=1` is set. It is off by default twice over: the code is only
compiled when `ALVR_PYROWAVE_DIR` points at a pyrowave checkout built with `-DPYROWAVE_DEVEL=OFF`,
and only selected at runtime by the env var.

**It does not take the composited RGB texture.** `FrameRender` renders three full-resolution
YCbCr planes into shared textures (`RgbToYuvPlanar.hlsl` via `RenderPipelinePlanar`, one pass,
three targets) and the encoder imports those directly as Vulkan images. No colour conversion and
no copy happen in the encoder.

Three decisions worth recording:

- **4:4:4, not 4:2:0.** Measured on the RTX 3090 at 3328x1472, 4:4:4 costs **1.51x decode**
  (277.5 us against 183.4) and 1.39x encode. Scaled onto the headset's measured 2.67 ms that is
  ~4 ms, against a 13.9 ms frame period and H.264's 14.8 ms. So chroma subsampling can simply be
  dropped as a loss term. *The Adreno figure is scaled from the desktop ratio, not measured --
  confirming it needs `pyrowave-bench` built for Android, which needs `PYROWAVE_DEVEL=ON` and a
  full Granite checkout.*
- **Three planes, not NV12.** Importing a 2-plane NV12 D3D11 texture misreads chroma (~26 dB
  against 60 dB); see `tools/pyrowave_d3d11/README.md`. Three single-component R8 images avoid it,
  and each view must report its own extent.
- **BT.709.** ALVR's existing `rgbtoyuv420.hlsl` only carries BT.2020 sets because that path is
  HDR-only. The composition texture is `_UNORM_SRGB` and not TYPELESS, so no non-sRGB view of it
  can be created and sampling returns linear light; the shader re-encodes sRGB before the matrix.
  Exact at 8-bit, since the intermediate is float32.

Output goes straight to `VideoSend()` rather than `ParseFrameNals()`, which exists only to pull
SPS/PPS out of an H.264 or HEVC stream. One packet per frame: PyroWave's packetisation is for loss
resilience, which ALVR's TCP transport does not need.

**Status: verified end to end.** Encoding live SteamVR frames at 3328x1472 4:4:4 on
ALVR's 600 Mbps budget, it scores **57.76 dB** against the encoder's own input -- matching
`pyrowave-encode` on the identical pixels (57.78). Measured by decoding the tapped bitstream with
`pyrowave-decode` and comparing against `ALVR_PYROWAVE_DUMP`, so no display, compositor, colour
conversion or homography is anywhere in the number.

**Two traps that cost hours, both worth knowing.**

*The tap must be armed.* `BitstreamTap::Submit()` silently discards everything unless
`BitstreamTap::Start()` was called first. Forgetting it produces a ground-truth dump and no
bitstream to compare it against, with no error anywhere.

*Declare the colour range.* A y4m without `XCOLORRANGE` reads as limited range to every tool,
while the `.wave` header carries its own flag -- and **ffmpeg's psnr filter silently inserts a
range conversion when its two inputs disagree**. That is worth about **29 dB** of luma: the same
bitstream scored 28.56 dB or 57.78 dB depending only on that one field. The artifact is
thoroughly convincing -- lifted blacks and banding across dark regions, exactly like a real
quantisation failure -- and it survived three plausible wrong explanations (render-target
compression needing a resolve, the queue-family ownership acquire, and a stray
`libpyrowave-shared-0.dll` in Steam's directory). What broke the deadlock was an in-process
control: encoding the *same bytes* through `pyrowave_encoder_encode_cpu_synchronous` also scored
28.56, which ruled out the GPU image read entirely and pointed at the measurement. Always check
both y4m headers before believing a PSNR number.

Build it with:

```
set ALVR_PYROWAVE_DIR=<workspace>\pyrowave-pc
cargo build -p alvr_server_openvr --release
```

**Deploying it needs two files, not one.** Linking against `pyrowave-shared.lib` makes
`driver_alvr_server.dll` depend on `libpyrowave-shared-0.dll` at load time, so that DLL must sit
beside it in `bin\win64\`. Without it SteamVR fails with

```
Unable to load driver alvr_server from ...\driver_alvr_server.dll (126)
Unable to load driver alvr_server because of error VRInitError_Init_FileNotFound(103). Skipping.
```

Error 126 is `ERROR_MOD_NOT_FOUND`: a *dependency* is missing, not the driver itself -- which is
sitting right there, making the message actively misleading. It presents as total silence: no ALVR
log, no frames, and a client stuck on "Stream will begin soon". Confirm a load with
`Select-String 'Loaded server driver alvr_server' "C:\Program Files (x86)\Steam\logs\vrserver.txt"`,
and if it is absent check `blocked_by_safe_mode` under `driver_alvr_server` in
`steamvr.vrsettings` -- SteamVR does block drivers after a crash, and this machine has scripts for
exactly that.

**Never edit `session.json` with PowerShell's `Set-Content -Encoding UTF8`.** Windows PowerShell
5.1 writes a UTF-8 BOM and ALVR rejects the file outright with
`Error on parsing session config: syntax error at line 1`. Use
`[System.IO.File]::WriteAllText($f, $json, (New-Object System.Text.UTF8Encoding($false)))`. Note
also that the dashboard owns that file and overwrites hand edits made while it is running.

Diagnostics log at `Error()` on purpose -- `Info()` goes to ALVR's event stream, which the
telemetry capture filters down to statistics, so `Info` never reaches `crash_log.txt`.

## A diagnostic that broke the thing it was measuring

The `[REPROJ]` probe in `client_openxr/src/lib.rs` called `xr_runtime_now()` **every frame** in
the render loop. Only its *logging* was throttled to every 72 frames; the runtime call was not.

That cost panel-native streaming outright. `B-15` (3552x3840/eye at 600 Mbps) failed every attempt
with **"session says Streaming but no frame statistics"** while lighter configurations were
unaffected — `Q-CONTROL` at 2560/eye ran clean throughout. The symptom names the session and the
statistics, so it reads as a server or network fault, and the hunt went there for most of a day.

Bisected by swapping one component at a time against a known-good pair:

| DLL | client | B-15 |
|---|---|---|
| PyroWave | probe on | FAIL x3 |
| known-good (20:32) | known-good (20:33) | ok |
| PyroWave | known-good | ok |
| PyroWave | probe gated off | **ok** |

The per-frame probe is now behind `ALVR_REPROJ_PROBE`, read once into a `OnceLock` so it costs
nothing when off, and off is the default.

**The general rule this earns:** a measurement build carries no diagnostic that can change what is
measured. Throttling the *log* is not enough — the work behind it has to be throttled too, or
gated off entirely. A per-frame runtime call is free until the frame budget is tight, and tight is
exactly the condition worth measuring.

## PyroWave on Galaxy XR

`pyrowave-galaxy-xr.patch` applies to `Themaister/pyrowave` at `d2997ac` and does two things:
it makes the standalone `pyrowave-decode` CLI usable on Adreno, and it adds three environment
knobs that isolate the Adreno faults found while measuring it.

Why it matters: PyroWave is an intra-only wavelet codec in pure Vulkan compute, and on this
headset it decodes in **2.67 ms** against the hardware H.264 decoder's **14.8–18.9 ms**. Encoding
on the PC and decoding on the headset reached **39.69 dB at 25 Mbps**, against ALVR's H.264 needing
~600 Mbps. Decode time, not bitrate, is what has blocked every quality preset in this project
(see the HEVC result — 82 ms at 2.06 Mpx), so a software decoder that is 6x faster than the
hardware one changes which questions are worth asking.

**The CLI fix.** `decode.cpp` only ever built the compute path, while `bench.cpp` honoured
`Decoder::device_prefers_fragment_path()` — which returns true for Qualcomm. So the CLI silently
took the path that is broken on Adreno. The patch calls the same selector, and because the
fragment path writes planes as colour attachments rather than storage images, it also adds
`VK_IMAGE_USAGE_COLOR_ATTACHMENT_BIT` to the plane images and splits both image barriers on
`g_fragment_path` (`ATTACHMENT_OPTIMAL` and `COLOR_ATTACHMENT_OUTPUT` instead of `GENERAL` and
`COMPUTE_SHADER`). Getting the usage flag or either barrier wrong produces a validation error
rather than a wrong image, which is the only reason this was quick to find.

**The three knobs**, all read with `getenv` so they need no rebuild:

| variable | effect |
|---|---|
| `PYROWAVE_FORCE_COMPUTE` / `PYROWAVE_FORCE_FRAGMENT` | override the vendor path selection |
| `PYROWAVE_FORCE_SSBO` | bypass the texel-buffer vendor allowlist, which excludes Adreno |
| `PYROWAVE_NO_LINEAR_TEX` | skip the linear-texture payload path, whose images have a hardcoded 1024 height |

**Two Adreno faults were found and both are encoder-side**, which is why they do not block the
intended use. The compute decode path blanks rows 512–1024, and encoding on the headset ceilings
around 28 dB. Since the PC encodes and the headset only decodes, neither is on our path — but
they are real upstream bugs and worth reporting rather than working around silently.

**The clone is not kept.** It lived in a session scratchpad, so this patch plus `d2997ac` is the
only record; `bash checkout_granite.sh` then a normal CMake build reconstructs it, and the
Android cross-build needs `-DCMAKE_TOOLCHAIN_FILE=$ANDROID_HOME/ndk/<ver>/build/cmake/android.toolchain.cmake`
with `-DANDROID_ABI=arm64-v8a`. On Windows, shaderc must be built with
`-DSHADERC_ENABLE_SHARED_CRT=ON` or it fails to link with LNK2038 (`/MT` against pyrowave's `/MD`).

## Rebuilding the server on the PC

The PyroWave encoder is compiled in only when `ALVR_PYROWAVE_DIR` is set at **build** time
(`alvr/server_openvr/build.rs`, `#ifdef ALVR_PYROWAVE` in `CEncoder.cpp`). A rebuild without it
succeeds and produces a driver that silently uses NVENC/H.264 whatever `ALVR_PYROWAVE=1` says at
run time — the client then gets an H.264 DecoderConfig. The recipe that works:

    cd <workspace>\ALVR-20.13.0-src
    set "ALVR_PYROWAVE_DIR=<workspace>\pyrowave-pc"
    cargo build --release -p alvr_server_openvr

Quote the `set`: in a one-line `set X=... && cargo ...` cmd keeps the trailing space in the
value and build.rs then reports `has no pyrowave.h` for a directory that plainly has one.
    copy target\release\alvr_server_openvr.dll ..\ALVR-20.13.0\bin\win64\driver_alvr_server.dll

Stop SteamVR first (the DLL is locked while loaded). Run-time: launch via the
`XRWiredSteamVRPyroClean` task (sets `ALVR_PYROWAVE=1` in-process; no tap, no dump).
