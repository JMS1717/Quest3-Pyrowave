# Light peripheral encoding

Added in development `.28`; **off by default** and opt-in through alpha.9 and `.64`.
Short Quest 3 mapping/timing screens passed; sustained performance and in-headset
quality acceptance remain pending. The owner's target (2080×2208 per eye at 207 Hz)
asks for no visible foveation, so this mode stays separate from it. At that target the
decoder is no longer the limit, and Strong foveation did not raise fresh FPS
([packed presentation](#packed-ycbcr-presentation-october-7)). This mode deliberately trades
a little peripheral detail for fewer encoded/decoded pixels; it does not reduce the game's
rendering workload.

## What changes

The fixed center occupies approximately **80% of each eye's width and height**,
with full sampling density there. A smooth ALVR spatial remapping compresses the
remaining outer bands with a gentle **1.5× edge ratio**. Neither eye follows gaze,
and both shifts are zero. The Quest reconstructs the original expanded view in
the existing direct-eye draw, preserving its color/range handling and buffer
ownership. There is no extra full-frame reconstruction pass in that direct path.

| Native120 / 4:2:0 comparison | Full frame | Light mode |
| --- | --- | --- |
| Expanded view per eye | 2080×2208 | 2080×2208 |
| Encoded/decoded pixels per eye | 2080×2208 | 1952×2080 |
| Raw stereo 8-bit 4:2:0 bytes/frame | 13,777,920 | 12,180,480 |
| 1000 Mbps / 120 Hz payload budget | 1,041,666.7 bytes/frame | 1,041,666.7 bytes/frame |

Light mode saves **11.59% encoded pixels** after alignment, not a promised
11.59% performance gain. Conversion, eye draws and runtime presentation still
cost time. The nominal center covers about 64% of image area, and alignment makes
each axis slightly less than 80%. It is a fixed image-center profile; lens frusta,
IPD, fit and looking toward peripheral text affect how noticeable it is. Human
in-headset acceptance remains necessary.

## Enable and compare

Use a matching `.28` or newer APK/Windows pair. Releases from alpha.8 on (alpha.9 is
the latest) include the setting; alpha.7 and older have no light-mode setting. In the
dashboard, open **Video → PyroWave → Light peripheral encoding (experimental)**. Restart
SteamVR after changing it. While a client streams, the server does this itself about 2 s
after the change ([SETTINGS-APPLY.md](SETTINGS-APPLY.md)).

```powershell
python -m tools.quest3.control foveation --mode light
# Restore full-frame encoding:
python -m tools.quest3.control foveation --mode off
# Inspect geometry and the unchanged payload target:
python -m tools.quest3.foveation --eye 2080 2208 --hz 120 --mbps 1000
```

The toggle verifies setting readback; it does not claim the running stream has
changed before restart. Captures verify the driver's fixed parameters and actual
3904×2080 stereo decode dimensions. Unsupported clients negotiate the full-frame
fallback. Old generic FFE/force-enable/gaze overrides cannot select a different
warp, and ordinary full-frame presets reset this explicit light switch.

Use short full/light/full screens with the same source resolution, scene,
refresh, chroma, wavelet, bitrate, overlay and clock/thermal evidence. Check fresh
submission and completed-copy rates, p1/gaps, decode/completion, estimated latency
and payload. Inspect separate LEFT/RIGHT labels, saturated edges, peripheral HUD
text and the stereo seam. Leave it optional if quality is noticeable or pacing
regresses. Runtime120 acceptance alone does not certify 120 fresh frames/s.

## First Quest 3 comparison (October 2)

Matching `.28` APK/Windows artifacts passed [CI and provenance checks](../results/LIGHT-FOVEATION-CI-2026-10-02.json).
Three short 15-second screens used the same 2544×2704 game-source chart,
expanded 2080×2208 view, runtime120, 4:2:0, Haar/compute, 1000 Mbps USB/TCP and
visible overlay. Restarts applied each geometry; individual source intervals and
reference-image hashes matched. All three clock snapshots in every cell were
690 MHz, battery temperature stayed 45°C and Android thermal status was zero.
Those sparse readings do not prove a continuously fixed clock or silicon temperature.

| Metric | Full | Light | Full restored |
| --- | ---: | ---: | ---: |
| Fresh submissions/s | 100.22 | 107.06 | 105.88 |
| Completed eye copies/s | 100.88 | 107.39 | 105.87 |
| GPU decode p50, ms | 4.49 | 3.91 | 4.46 |
| Decode completion p50, ms | 6.17 | 5.38 | 6.00 |
| CPU eye render p50, ms | 1.81 | 2.14 | 1.68 |
| Estimated pipeline latency p50, ms | 54.60 | 53.45 | 53.76 |
| Actual video payload p50, Mbps | 1001.72 | 1005.04 | 1002.58 |
| Timestamp gap p95, ms | 16.71 | 16.68 | 16.68 |

The actual decoder received **1952×2080 per eye** only in light mode, while the
reconstructed view stayed full size. GPU decode cost fell; CPU eye time rose,
and the fresh-FPS difference over the restored control was small. This is an
encouraging decode-budget result, not sustained 120 FPS or a proven latency win.
At the same constant bitrate, pixel reduction does **not** promise less payload.
The p1 nominal client rate remained about 60 FPS because some timestamps spanned
two 120-Hz slots; median nominal120 must not be substituted for fresh FPS.

Inspected ADB screenshots preserve LEFT/RIGHT labels, outward arrows, upright
colored HUD text and grid placement, with no gross warp or inversion seen.
They do not establish that peripheral loss is invisible in the lenses. The
[aggregate live record](../results/LIGHT-FOVEATION-LIVE-2026-10-02.json) retains
counter deltas, pacing, thermals and geometry. Full-frame mode was restored;
light remains an optional experiment pending sustained and human quality checks.

A follow-up with the GPU eye timer enabled in every mode stopped at the
conservative 46°C screening cutoff after its first control. It yielded no
comparative GPU reconstruction-cost result and is excluded from acceptance.
Temporary properties were restored with readback, and the proximity automation
was disabled; no thermal safeguards were changed.

## Stronger profile candidates

`video.pyrowave.foveation_profile` selects the fixed geometry used when peripheral
encoding is on. Light (80% center, 1.5x) is the default profile; encoding itself
remains off by default. Light, Balanced and Strong now have short Quest screens. Balanced (68% center, 1.75x) encodes 1824x1920 per eye from native
2080x2208, about 24% fewer pixels. Strong (60% center, 2x) encodes 1664x1792, about
35% fewer. Both were decode-budget candidates for 144/207 Hz, without perceptual or
sustained-performance acceptance; with the faster `.62` decoder they no longer raise fresh
FPS at 207 Hz (see below). Their constants were chosen so the client's f32
sizing and the server's mixed float/double sizing agree for every 8-pixel eye size
from 512 to 4096 (tests/test_foveation.py). `python -m tools.quest3.control foveation
--mode light --profile strong` selects one; see [PATH-TO-207.md](PATH-TO-207.md).

## October 5 profile screen

Signed `.55` negotiated Light 1952×2080, Balanced 1824×1920 and Strong 1664×1792
per eye, reconstructing 2080×2208. Fresh delivery stayed roughly 119 FPS. Strong
reduced median native completion to 7.12 ms, while eye reconstruction GPU cost
rose from about 0.66 ms without foveation to 1.19 ms. This still exceeds the
4.83 ms period at 207 Hz. Captured endpoints retain upright LEFT/RIGHT mapping;
peripheral detail changes need lens acceptance. Owner-interrupted windows were
excluded and repeated cleanly. [Window data and limits](PR-9-REVIEW.md).

## Packed YCbCr presentation (October 7)

Haar mode 5 hands the Quest's eye draw packed YCbCr: luma in 2x2 quads in one half of an RGBA8
buffer, 4:2:0 chroma in the other (see `present_ycbcr.glsl`). With foveation on, the packed draw
first read the buffer as RGBA, which showed a grey, doubled image. It now samples the decoded
eye size and maps each output pixel through the same inverse warp.

Filtering is where the cost lies. Every packed luma read is a texture fetch, so bilinear luma
costs four fetches per pixel against the hardware filter's one. The centre needs no filter: it
maps 1:1 onto decoded pixels (centre offsets are multiples of a quarter pixel, and rounding with a
0.375 bias never meets a tie). Only the compressed bands do. Run-time branches on the band did
not help, because the driver still fetched all four pixels. The eye draw therefore splits each
eye into up to nine scissored regions, and each region uses a program compiled for its filter:
none in the centre, x or y in the side bands, both in the corners. A unit test checks that the
regions tile both eyes exactly once, with right-eye and flipped-y mirroring.

Quest 3, 207 Hz, 2080x2208 per eye from 3072x3216, Haar 4:2:0, 1000 Mbps, Strong profile
(1664x1792 decoded per eye), static quality scene and a 60 deg/s pan, 12 s windows, headset
awake:

| Eye draw | Fresh FPS, static / pan | Eye-copy fence p50 |
|---|---:|---:|
| Foveation off | 193.0 | 4.50 ms |
| Bilinear luma everywhere | 172.8 / 170.7 | 5.17 ms |
| Run-time band branches | 176.8 / 175.0 | 5.09 ms |
| Scissored regions (current) | 188.3 / 188.9 | 4.78 ms |
| Nearest luma everywhere (diagnostic, blocky edges) | 194.6 / 190.4 | 3.35 ms |

Strong foveation still decodes about 0.8 ms faster (2.24 against 3.03 ms GPU decode), but at this
resolution the decoder is not the limit, so it does not raise fresh FPS. In-headset screenshots
of the same view with foveation off and with Strong show the same image, with no seams at region
edges. Perceptual acceptance of the softer periphery remains pending.

## Mapping safeguards and inspiration

The implementation adapts the existing MIT-licensed
[ALVR forward shader](https://github.com/alvr-org/ALVR/blob/v20.13.0/alvr/server_openvr/cpp/alvr_server/shader/CompressAxisAlignedPixelShader.hlsl)
and its CPU/inverse derivation. Right-eye mirroring is applied consistently on
both sides; encoded padding and per-eye half-texel bounds prevent seam sampling.
The server shader treats transition boundaries as part of the center rather than
leaving an uncovered equality case. Windows builds explicitly recompile that
shader so source edits and constant-buffer layout cannot silently use an old CSO.

CPU tests check dense forward/inverse round trips, transitions, monotonicity,
center density, both eyes, budgets and legacy/default settings. A software GLES
CI check evaluates the **same shared inverse helper** for 490 coordinates,
including corners and transition boundaries. It verifies mapping math, not Quest
interop, D3D sampling, image quality or headset performance. A matching live asymmetric-chart screen confirmed both-eye mapping and reduced
decode geometry. Gameplay/perceptual acceptance remains separate.

Virtual Desktop's developer publicly described spatial foveated streaming in
[the 1.8 announcement](https://www.reddit.com/r/OculusQuest/comments/dufzov/virtual_desktop_update_18_improved_image_quality/).
This is behavioral inspiration; its proprietary implementation and tuning are
not copied or claimed equivalent. [WiVRn's public compositor](https://github.com/WiVRn/WiVRn/blob/master/server/compositor/foveation.cpp)
also informed the review of per-view mapping and filtered reconstruction. No
WiVRn code or binaries are incorporated. ALVR supplies the implemented warp.

[![Support development](https://img.shields.io/badge/PayPal-Support%20development-0070BA?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/jasonselsley)
