# Steam Frame port

Source version: `20.13.0-frame.pyro.1`. Experimental Android ARM64/OpenXR client in Lepton,
with the existing **Windows gaming PC server**. This is a source port with offline validation;
no Steam Frame was available during implementation. It is not a native Windows headset client.
Do not pair this client with the published Quest `.136` server: the protocol version changed.

## Platform and defaults

Valve recommends Android or Linux ARM64 and documents OpenXR gaze access through
[`XR_EXT_eye_gaze_interaction`](https://partner.steamgames.com/doc/steamhardware/steamframe/engines/custom).
This port chooses Android to reuse the Vulkan decoder, AHardwareBuffer/GLES bridge, audio and
ALVR transport. Vulkan/AHardwareBuffer interoperability in Lepton still needs a device check.
Frame controllers use Valve's documented [Oculus Touch compatibility bindings](https://partner.steamgames.com/doc/steamhardware/steamframe/input).
Extra Frame buttons and capacitive finger data are not mapped yet.

The Android properties `Valve` / `Lepton`, explicit Steam Frame product names, and the OpenXR
system name identify Frame. The loader remains the generic Khronos Android loader.

Fresh installs use **Steam Frame 72 Hz (experimental)**:

| Setting | Frame default |
| --- | --- |
| Stream size | Runtime recommendation, aligned by ALVR; not the Quest panel size |
| PC game render | 3240 × 3240 per eye, 150% of Frame's physical panel |
| Refresh | Prefer 72 Hz; use the nearest advertised mode if that runtime does not offer 72 Hz |
| Codec / wavelet | PyroWave / CDF 5/3, compute decoding, 4:2:0 |
| Bitrate / network | 500 Mbps, UDP on Wi-Fi |
| Sharpening | Off; PC Lanczos downsampling retained |
| Colour | Rec. 709 where the runtime provides colour-space control |
| Quest panel / GPU overrides | Off |
| Eye-tracked encoding | Separate experimental profile; off in the startup profile |

The [Frame specification](https://store.steampowered.com/hardware/steamframe) gives 2160 × 2160
panels and 72–144 Hz, with 144 Hz experimental. Panel-relative resolution controls now use that
square panel. The refresh preset no longer enables Quest panel properties. SteamVR reports a
Frame virtual HMD; game-facing controllers retain Touch compatibility. Existing session values
are preserved: select the Frame profile explicitly after upgrading an old server installation.
The named Quest profiles remain available with their original stream dimensions and measurements.

The encoder's CSF panel-row reference now defaults to 2160. `PYROWAVE_CSF_PANEL_ROWS=2208`
restores the Quest reference for comparisons. This changes a model input, not a measured quality
win. The inherited angular/CSF model and peripheral image quality need Frame validation.

## Eye-tracked streaming

Select **Steam Frame eye-tracked 72 Hz (experimental)** on PC or in the headset menu. Both this
profile and the uniform Frame profile use 500 Mbps. Changing profiles restarts streaming/SteamVR
as required by the existing settings flow.

- The client advertises eye-tracked support only after the OpenXR system reports gaze support
  and the gaze action binding and action space succeed. Older clients advertise no such support.
- The server refuses the eye-tracked mode without that capability or without PyroWave. Quest's
  fixed peripheral profiles cannot silently enable gaze steering.
- A combined head-local gaze ray projects separately into the two actual eye frusta. Right-eye
  X and downward texture Y follow the existing encoder convention. The full-detail band covers
  half of each axis, with 2× peripheral compression. Shift is bounded at ±0.9.
- Both eyes' requested centers are published as one snapshot, avoiding a mixed sample during
  concurrent tracking and rendering. The applied centers travel with the encoded frame.
- The direct eye renderer updates its inverse warp **and filtering rectangles** per eye from the
  selected frame's metadata. The staging renderer uses the same parameters. Repeats retain the
  old layout. Lookup happens before any runtime timestamp clamp.
- Inactive/invalid gaze returns to head-forward. Server-arrival age is limited to 50 ms; the
  driver also checks on its 5 ms idle timeout if tracking uplink packets stop.
- Gaze is uploaded only for explicitly enabled streaming/face tracking. Normal diagnostics do
  not write gaze directions or maintain direction histories.

At an expanded 2176 × 2176 per eye, this geometry encodes 1632 × 1632: **43.75% fewer encoded
pixels**, independent of gaze position. That is allocation arithmetic, not a measured bitrate,
FPS or quality improvement. The full view is reconstructed at its original size; peripheral
spatial detail is reduced. Saccades, blinks, edge looks and both-eye alignment need worn checks.

## Runtime timing

Only advertised extensions are requested. An [upstream Frame report](https://github.com/alvr-org/ALVR/issues/3411)
identifies missing `XR_KHR_convert_timespec_time` as a Lepton startup failure in ordinary ALVR.
If conversion is available, this port continues using it. Otherwise it anchors an **estimated**
clock once per session from `xrWaitFrame`'s predicted display time minus one display period,
then advances it with `Instant`. The initial prediction offset is unknown; this is a compatibility
fallback, not an exact clock conversion or latency measurement. Input waits for calibration.
Frame submits composition at the runtime's predicted display time. Its handshake permits selecting
the nearest advertised refresh rate; Quest keeps the strict exact-rate check. The negotiated
rate and measured display period, rather than the profile name, determine the actual cadence. If no refresh-control
extension exists, it advertises only the cadence observed in the lobby.

An exact runtime clock API, or a validated offset measurement, is a follow-up before latency
claims. Do not infer optical latency from these timestamps or ALVR statistics.

## Build and offline evidence

Pinned sources and toolchains remain in [sources.lock.json](../sources.lock.json). The cumulative
ALVR patch is still [quest3-alvr.patch](../patches/quest3-alvr.patch); new source files and the
coherent-center helper are reconstructed by `tools/ci/fetch_sources.sh`.

On a Windows development PC, build the matching APK and server locally:

```powershell
python tools/local/fast_build.py setup
python tools/local/fast_build.py build
python tools/local/fast_build.py test
```

October 17 offline checks:

| Check | Result |
| --- | --- |
| Android ARM64 release APK | Built with Rust 1.97.1, NDK 27.2.12479018, Android 35 and the pinned loader; development signing |
| Native Android PyroWave and bridge | Built |
| Windows GNU target Rust check: `alvr_server_core`, `alvr_client_openxr` | Passed; not a Windows native server package |
| Rust: client core, server core, graphics, packets, session, system info | 165 passed |
| Dashboard | 12 passed, 1 existing ignored visual preview |
| Estimated clock unit test | 1 passed |
| Coherent-center C++ test | Passed, including concurrent publication and invalid input |
| Software GLES inverse mapping | 490 fixed + 7,840 moving-layout cases; worst moving UV error 1.11e-7 |
| Full native driver test | Not completed here: Linux build requires the separate FFmpeg development bundle; Windows C++/D3D11 package still needs its native build |

The Python suite and reconstruction check are recorded in [HANDOFF.md](HANDOFF.md). No Frame
runtime acceptance, decode budget, live streaming, sustained gameplay, visual quality, thermals
or optical latency result has been established. No Frame release was published.

## First device session

1. Build a matching Windows server/APK pair. Preserve the existing server installation and
   session before testing. Start with the uniform Frame profile.
2. Follow Valve's [Lepton setup](https://partner.steamgames.com/doc/steamhardware/steamframe/adb_lepton):
   enable developer mode, run Lepton Development and connect ADB to the Android container.
   Native Linux USB ADB is a different endpoint; start with Wi-Fi streaming. Do not assume the
   existing Quest wired auto-launch path selects the right Lepton endpoint.
3. Install the APK and launch **PyroWave Frame** from Lepton. Its compatibility package ID remains
   `io.github.jms1717.quest3pyrowave`. Check `[Q3PW_DEVICE]`, extension availability and effective
   display period before changing refresh rate.
4. Confirm decode/import, stereo orientation, controllers and audio. Check runtime gaze consent
   and calibration, then select the eye-tracked profile. Missing or inactive gaze must be visible
   in diagnostics and must leave a centered view.
5. Compare uniform / eye-tracked / eye-tracked / uniform. Settle 3–5 s and measure 10–12 s per
   window. Enable `debug.q3pw.fresh_probe=1`, `debug.q3pw.frame_trace=1` and, if needed,
   `debug.q3pw.loop_probe=1`. Snapshot and restore every property you change. Record distinct
   fresh frames, superseded/empty selections, decode/fence/eye times, clocks when exposed, and
   effective display period. Quest VrApi memory-clock collection does not apply to Frame.
6. Inspect static text and gradients, slow gaze motion, fast saccades, blinks, invalid tracking,
   reconnects and edge looks in both eyes. Then try 90/120 Hz and bitrate changes one at a time.
   Only promote settings after matched measurements and worn acceptance. Test sustained games
   separately. Leave the PC driver registration, SteamVR state and headset properties as found.
