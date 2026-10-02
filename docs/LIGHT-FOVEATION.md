# Light peripheral encoding

Development candidate `.28`; **off by default** until GPU, live pacing and
in-headset quality comparisons pass. The current no-foveation native120 goal
remains separate. This mode deliberately trades a little peripheral detail for
fewer encoded/decoded pixels; it does not reduce the game's rendering workload.

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

Use a matching `.28` or newer APK/Windows pair; the published alpha.7 pair has no
light-mode setting. In the development dashboard, open **Video → PyroWave → Light
peripheral encoding (experimental)**. Restart SteamVR after changing it.

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
interop, D3D sampling, image quality or headset performance. Matching live
asymmetric-chart and quality comparisons are required after the cloud build.

Virtual Desktop's developer publicly described spatial foveated streaming in
[the 1.8 announcement](https://www.reddit.com/r/OculusQuest/comments/dufzov/virtual_desktop_update_18_improved_image_quality/).
This is behavioral inspiration; its proprietary implementation and tuning are
not copied or claimed equivalent. [WiVRn's public compositor](https://github.com/WiVRn/WiVRn/blob/master/server/compositor/foveation.cpp)
also informed the review of per-view mapping and filtered reconstruction. No
WiVRn code or binaries are incorporated. ALVR supplies the implemented warp.

[![Support development](https://img.shields.io/badge/PayPal-Support%20development-0070BA?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/jasonselsley)
