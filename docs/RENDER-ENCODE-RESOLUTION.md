# Independent PC render and encoded resolution

The Windows pipeline already supports a larger SteamVR source with a smaller
PyroWave frame. This change exposes that separation through geometry-only
controls and paired benchmark profiles. The original geometry-only controls
work with the matching `.25` pair without a new APK. Development `.55` adds
separate dashboard/menu controls and an optional PC downsample shader; codec,
client decode size and foveation remain independently selectable.

## Verified semantics

Both settings are **per eye**. The names below refer to session settings, not
the differently named OpenVR configuration fields.

| Session setting | OpenVR configuration | Meaning |
| --- | --- | --- |
| `emulated_headset_view_resolution` | `target_eye_resolution_width/height` | SteamVR recommended game render size |
| `transcoding_view_resolution` | `eye_resolution_width/height` | Stream/composition size and negotiated Quest decode/output size |

Verified against pinned ALVR `7eda092` and our reconstructed patched tree:

- `alvr/session/src/settings.rs`, `VideoConfig`: upstream describes the first
  as the default game rendering size and the second as encoding/decoding size.
  [Pinned upstream settings](https://github.com/alvr-org/ALVR/blob/7eda092dbf0002281410a4222683ec228700cffb/alvr/session/src/settings.rs).
- `alvr/server_core/src/connection.rs`: resolves both independently; sends only
  stream resolution to the client and writes both separately to OpenVR config.
- `Settings.cpp` maps stream size to `m_renderWidth/Height` (stereo width),
  recommended size to `m_recommendedTargetWidth/Height`.
- `HMD.cpp::GetRecommendedRenderTargetSize` returns recommended per-eye size;
  output viewports remain at stream size.
- `FrameRender.cpp` allocates its composition texture and eye viewports at
  stream size, samples the submitted game textures using their normalized eye
  bounds with the existing anisotropic sampler, then converts at that size.
- `CEncoder.cpp::Initialize` passes `GetEncodingResolution` to PyroWave. With
  FFE off, this is stream stereo width/height. `client_openxr/src/stream.rs`
  derives client swapchains from negotiated stream size, subject to its existing
  client upscaling option. Leave upscaling disabled for this comparison.

The direct-eye requirement for equal encoded/output eye size refers to the
**Quest's decoded frame versus its output swapchain**, not the SteamVR source.
There is already a PC downsampling/composition pass, so no intermediate texture
is needed. Its filter was the weak point; see the next section.

## Game render above the stream (Virtual Desktop style)

Like Virtual Desktop's separate SteamVR resolution, the game render size and the
stream size are independent settings. Raising the game render size costs PC GPU
time only: the stream, the bitrate's per-frame byte budget and the Quest's decode
work all stay at the stream size.

| | Before | Now |
| --- | --- | --- |
| Composition filter | One bilinear tap per stream pixel (mip 0 only, so the anisotropic sampler acts as bilinear). At 3072→2080 it weights source pixels unevenly and lets detail above the stream's Nyquist fold back as moiré. | `tools/downsample/frame_downsample.hlsl`: a Catmull-Rom kernel widened to each pixel's source footprint (from UV derivatives, up to 3x per axis), clamped to the submitted eye bounds. Identity when both sizes match. |
| Dashboard | One "render scale" preset (50–100 %) set **both** fields to the same size, so there was no way to render above the panel. | "Stream resolution" (50–100 % of panel) sets only the stream; "Game render resolution" (100–200 % per axis) sets only the SteamVR recommendation. |
| Streaming profiles | Pinned both fields to the profile's size. | Pin the stream only; a chosen game render size survives a profile change. |

The filter setting is `video.pyrowave.render_downsample_filter` (`Bilinear`
default = the previous single tap, `Adaptive` = optional filtered downsampling) and applies to every
codec. It needs a SteamVR restart. If the runtime compile fails, the driver logs an
error and keeps the single tap; the Windows build also compiles it with `fxc` so a
shader error fails CI. The driver log prints the active sizes and filter:
`[FrameRender] game render WxH per eye -> stream WxH per eye (R x per axis), ... downsample`.

CPU model results (`tools/downsample/reference.py`, `tests/test_downsample.py`; one
axis, exact for the separable 2-D shader). Response is relative amplitude after
3072→2080; 1400 cycles cannot be shown at 2080 pixels and should be removed:

| Source frequency | Single tap | Adaptive |
| ---: | ---: | ---: |
| 400 cycles (kept) | 0.95 | 0.98 |
| 900 cycles (near stream Nyquist) | 0.76 | 0.65 |
| 1400 cycles (aliases to 680) | **0.59** | **0.15** |

Against a box-filtered ground truth of an edge-heavy test signal, RMS error was
0.137 rendering at 2080, 0.101 rendering at 3072 with the single tap, and 0.091
with the adaptive filter. Most of the gain is from rendering more pixels; the
filter mainly removes the moiré. Fetches per output pixel: up to 3x3 at 1:1 (all
zero-weight but one when aligned), 5x5 at 1.48x, 7x7 from 2x. PC GPU cost is
not isolated by the CPU model. The October 5 live filter screen below records
PC compositor/encoder timing and delivery; it withholds default promotion.

Keep SteamVR's own render resolution at a custom 100 %: SteamVR's automatic
setting multiplies the recommendation again. Footprints above 3x per axis are
filtered as 3x (under-filtered, never skipped).

| Change | Reason | Before | After | Headset result | Status |
| --- | --- | --- | --- | --- | --- |
| Adaptive downsample + split render/stream controls | Higher-than-native render should add detail without bandwidth or Quest decode cost | 1 tap, both sizes tied in the dashboard | Footprint-wide Catmull-Rom; independent controls | Eight short Quest windows at explicit 3072×3216 source / 2080×2208 decode: Bilinear 119.46 FPS, Adaptive 118.73; Adaptive tail pacing weaker. [Data](PR-9-REVIEW.md) | Adaptive optional; Bilinear default |

Our server rounds **both** axes up to multiples of 32. A request for
3072x3216 therefore becomes a **3072x3232 recommendation**. The encode request
2080x2208 is unchanged. This preserves the existing alignment semantics.
SteamVR automatic/global/per-app resolution and game settings may further alter
the actual submitted source size: a recommendation is not proof of game texture
dimensions. Record SteamVR's displayed per-eye size and the game's render size.

## Controls and paired profiles

Run from the repo root with the matching server running:

```powershell
python -m tools.quest3.control status
python -m tools.quest3.control resolution --profile native2080
python -m tools.quest3.control resolution --profile supersampled3072
```

Each command changes only the two resolution fields, verifies session readback,
prints requested and aligned sizes, and includes the previous resolution
settings for archival. It does **not** restart SteamVR automatically. Save its
JSON output beside the capture. Restart SteamVR after each geometry change,
wait for reconnection, and restart the game if it caches its render targets.
Do not run both profile commands consecutively without capturing between them.

For independent manual control, either argument may be omitted; omitted geometry
is preserved:

```powershell
python -m tools.quest3.control resolution --render-eye 3072 3216
python -m tools.quest3.control resolution --encode-eye 2080 2208
```

These commands leave the existing refresh, bitrate, wavelet, chroma, decoder,
transport and foveation settings alone. They do not enable 120 Hz implicitly.
Use your current best 4:2:0/noFFE/no-client-upscaling configuration for both.
The comparison manifest is `presets/resolution-comparison.json`; 1000 Mbps /120 Hz
remains an experimental target. The dashboard's stream and game render
resolution presets each set one field only, and streaming profiles set only the
stream. Existing defaults are preserved.

## Quick old/new benchmark

Use native2080 -> supersampled3072 -> native2080, with one restart/reconnect and
15-second capture per cell, then reverse the order in another repetition.
Keep scene, camera, source animation, SteamVR global/per-app percentages, refresh,
bitrate, wavelet, chroma, decoder properties, overlay and thermal state constant.
Ensure source-frame coverage; do not compare a live scene to idle SteamVR.

For the diagnostic chart, start the source **after** the client has reconnected
and settled. Querying OpenVR during reconnect can return an old recommended size
or the driver's temporary symmetric FOV. Pin the actual texture size and use
the normalized chart so text/panels occupy comparable projected space:

```powershell
# Native control cell (run separately after reconnect)
python -m tools.quest3.stereo_scene --quality --normalized-chart --source-eye 2080 2208 --seconds 120 --out results/local/render-native-source
# Larger-source cell (run separately after selecting the profile and reconnecting)
python -m tools.quest3.stereo_scene --quality --normalized-chart --source-eye 3072 3216 --seconds 120 --out results/local/render-super-source
```

Keep capture wholly inside the scene's ready/start/end interval. Archive its
`source_eye_size`, `steamvr_recommended_eye_size`, projections and chart metadata.
Verify both control cells have identical projections and reference-image hashes;
the larger source should keep the same projections but have different raster
dimensions. Reject a group with stale/default FOV or changed control content.
Normalized primitives are rasterized at each source size, rather than enlarging
a finished bitmap; glyph stroke rounding and aspect differences still limit a
pixel-perfect quality comparison. Existing pixel-sized charts remain the default.

```powershell
python -m tools.quest3.bench capture --hz 120 --seconds 15 --adb adb --out results/local/source-native-r1
# Select the larger-source profile and restart/reconnect before this capture.
python -m tools.quest3.bench capture --hz 120 --seconds 15 --adb adb --out results/local/source-super-r1
```

Capture reports now retain both configured fields and `resolution_evidence`:
requested aligned sizes, negotiated sizes, actual telemetry decode stereo size,
and the PC source pixel ratio. A mismatch marks the capture `resolution_mismatch`
(e.g. an unapplied restart or accidentally larger decoded frame). Missing
telemetry or scale/inferred-height settings remain `unknown`, not verified.
For both profiles expect decoded **4160x2208 stereo**. Reports cannot certify
actual game texture resolution or optical presentation.

The larger recommendation is **2.162x** the source pixels of native2080; encoding
stays 9,185,280 stereo pixels. At 4:2:0/120 Hz/1000 Mbps both retain 13,777,920
raw bytes/frame and a maximum budget of 1,041,666 encoded bytes/frame. Actual
payload and content-dependent codec cost can still change. Compare game/server
FPS, compositor/encoder time, delivered fresh FPS/p1, GPU decode/completion,
pipeline latency and thermals; visually compare text, HUD edges and fine detail.
Higher PC workload can erase a quality benefit by missing display deadlines.
No quality or performance improvement should be inferred from geometry alone.

## First controlled Quest 3 screen

The matching `.28` pair now has a completed 15-second native/larger-source/native
group using explicit **2080x2208 / 3072x3216 / 2080x2208 PC textures** and the
normalized chart. Client streaming settled before each source initialized;
projections matched, native control reference hashes matched, and all source
intervals covered capture. Decode stayed **2080x2208 per eye** in every cell,
with runtime 120 Hz, 4:2:0, no foveation and 1000 Mbps USB/TCP.
[Sanitized measurements and provenance](../results/RENDER-ENCODE-LIVE-2026-10-02.json).

| Metric | Native control | Larger source | Native return |
| --- | ---: | ---: | ---: |
| Delivered fresh FPS | 104.67 | 109.33 | 111.66 |
| Completed eye copies/s | 104.85 | 109.88 | 111.77 |
| Nominal FPS p1 | ~60 | ~60 | ~60 |
| Frame timestamp gap p95, ms | 16.69 | 16.66 | 16.65 |
| GPU decode p50, ms | 5.15 | 5.19 | 4.38 |
| Decode completion p50, ms | 6.59 | 6.67 | 5.91 |
| Encoder p50, ms | 2.65 | 1.56 | 1.49 |
| Estimated pipeline latency p50, ms | 56.11 | 54.68 | 43.56 |
| Actual video payload p50, Mbps | 1006.14 | 997.72 | 1001.21 |

The larger actual source has about 2.15x the input pixels; its aligned SteamVR
recommendation remains 3072x3232. Geometry separation works without adding
Quest decode pixels. This screen establishes **neither sustained 120 fresh FPS
nor a repeatable latency/quality win**. Battery samples were 40 / 40 / 41 C,
Android thermal status 0, and all nine sparse GPU clock samples 690 MHz; these
are not continuous clock/load or silicon-temperature measurements. The return
control changed substantially, so differences cannot be attributed solely to
render resolution. Screenshot review found upright matching eyes and comparable
chart placement; fine stripes changed with downsampling, without establishing
a noticeable in-headset quality uplift. Native source remains the default.

An earlier attempt is excluded: the source initialized during reconnection,
received stale recommended sizes/default symmetric FOV, and changed between
control cells. Those results are not performance or quality evidence. The new
fixed-source/normalized controls and CPU raster regressions address that fixture
problem; further reverse-order and sustained testing remains necessary.

To restore the existing native geometry use `--profile native2080`, restart and
reconnect. For an exact restoration of prior Scale/optional-height configuration,
use the saved `previous_resolution_settings` through ALVR settings, rather than
guessing it from the aligned negotiated dimensions.

## When the larger source still looks soft

Confirm the active game's render size, including its own resolution scale and
SteamVR per-application settings. A larger recommendation does not prove that
an already-running game recreated its render textures. Restart that game after
changing the recommendation if it caches render targets.

The final stream remains 2080x2208 per eye. Supersampling can reduce aliasing;
it cannot transmit the extra source pixels as additional decoded detail.
Downsampling, chroma subsampling and codec quantization can limit the visible
benefit. This profile is not a claim of Virtual Desktop Godlike equivalence.

For optional edge sharpening, the existing Windows color-correction pass can
use a modest `sharpening` value such as `0.2`, with brightness, contrast and
saturation all `0` and gamma `1`. These are neutral color values in this shader;
the existing default saturation `0.5` would increase saturation when enabled.
Enable color correction and restart SteamVR/stream initialization to apply it.
This adds a PC GPU pass after downsampling, without increasing Quest decode
resolution. It can accentuate compression or halos and is not recovered detail.
Keep this setting identical across resolution comparisons; it remains disabled
in the repository's baseline.
