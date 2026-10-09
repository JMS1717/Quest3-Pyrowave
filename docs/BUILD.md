# Reproducible builds

**Build locally for everything, releases included.** On a Windows PC,
`python tools/local/fast_build.py build` builds the Quest APK and the Windows server with CI's own
scripts and pinned toolchain. It keeps a persistent source reconstruction and rebuilds only what
changed. Measured October 6, 2026: under 3 minutes cold and 30 seconds to 2 minutes after an edit,
against about 20 minutes per GitHub Actions run. The very first `setup` plus `build` from an empty
root took about 7 minutes. [LOCAL-BUILD.md](LOCAL-BUILD.md) has setup, timings, signing and the
differences from CI artifacts. The manual steps below are the same recipe, one script at a time.

```powershell
python tools/local/fast_build.py setup    # once
python tools/local/fast_build.py build    # APK + Windows server
python tools/local/fast_build.py test     # CI's Windows Rust tests + Python suite
```

`build` refuses to run while SteamVR is up; pass `--allow-while-vr` only when nobody is playing.
Outputs go to `C:\q3pw\fast\out\<commit>[-dirty]\` in CI's layout: the APK with
`SHA256SUMS.txt` and `APK-CERTIFICATE.txt`, the Windows server zip, and `PROVENANCE.json`.

**Release builds** are local builds of a committed and pushed tree, so the output folder has no
`-dirty` suffix. Tag the release at that commit, check the exact build in the headset, attach its
outputs, and record the commit and APK SHA-256 in the release notes.

**GitHub Actions** still runs on every pull-request push as a cross-check, and
**Actions → Quest3-Pyrowave → Run workflow** in your own fork builds without a Windows PC.
It produces separate Android and Windows artifacts, APK certificate information and SHA-256 sums.
No signing secrets are required for a development build. A CI development key changes between
clean builds; Android will refuse an update signed by a different key. Keep a persistent private
key for releases. Never commit a keystore or password. Do not uninstall another ALVR app to install
this one: the package is `io.github.jms1717.quest3pyrowave`.

Pinned inputs are recorded in [`sources.lock.json`](../sources.lock.json). Source reconstruction
uses ALVR v20.13.0 plus the complete research patch, then `patches/quest3-alvr.patch`. PyroWave and
Granite remain pinned to the tested research revisions. Cargo's lockfile comes from that tree.
Reproducible here means the same inputs and recipe, not identical signature timestamps or ZIP bytes.

## Local Windows build

Use a short external workspace such as `C:\q3pw` to avoid Windows native-tool path limits.
Install Git for Windows, Python 3.13, Rustup, CMake, Ninja, LLVM (libclang), and Visual Studio
2022 C++ tools with Windows SDK and ATL. VS2019 can compile PyroWave and the transport probe.
On October 6 it also built the full `.58` streamer (set `Q3PW_CMAKE_GENERATOR=Visual Studio 16 2019`).
CI and the supported recipe use VS2022; `fast_build.py` picks VS2022 when its C++ tools are
installed and falls back to VS2019, so its Windows binaries can differ from CI's. The SDK, tools
and project remain separate.

```powershell
$env:XRWIRED_INPUTS = 'C:\q3pw'
& 'C:\Program Files\Git\bin\sh.exe' tools/ci/fetch_sources.sh C:/q3pw/research
rustup toolchain install 1.97.1 --profile minimal
tools\windows\build_pyrowave_pc.cmd interop
tools\windows\build_streamer.cmd
```

The output is `C:\q3pw\research\ALVR-20.13.0\build\alvr_streamer_windows`. The PyroWave DLL
must be next to `driver_alvr_server.dll` in `bin\win64`. Run the dashboard from the output
directory. Select the GPU that SteamVR uses. On multiple-GPU systems the Vulkan and D3D11 LUIDs
must match; unsupported external-memory/fence interop is a startup error.

Source fetch is for an **empty destination**. Preserve modifications before fetching again;
use a new directory for an independent reconstruction. The pinned Granite submodules are large.

## Android APK

Use JDK 17, Android SDK platform 35 and build tools 35.0.0, NDK 27.2.12479018, CMake and Ninja.
Install Android packages using Android's SDK manager and accept its licenses interactively.
`python tools/local/fast_build.py setup` installs these pinned versions under its own root and
verifies each download. The old `tools/windows/install_toolchain.ps1` is inherited research
tooling; use `setup`, the explicit versions below or the CI recipe instead.

```powershell
$env:XRWIRED_INPUTS = 'C:\q3pw'
$env:XRWIRED_ANDROID_SDK = 'C:\q3pw\toolchain\android-sdk'
$env:XRWIRED_NDK = "$env:XRWIRED_ANDROID_SDK\ndk\27.2.12479018"
$env:JAVA_HOME = '<your JDK 17 directory>'
rustup target add aarch64-linux-android --toolchain 1.97.1
cargo +1.97.1 install --locked cargo-ndk@4.1.2 cbindgen@0.29.4
cargo +1.97.1 install --locked --git https://github.com/zarik5/cargo-apk --rev 0fd3126dad5aa1c5f0f26cdae3410f2e5af62c60 cargo-apk
```

Download the Khronos Android OpenXR loader 1.0.34 AAR (the URL and extraction path are in CI),
and copy `libopenxr_loader.so` to the ALVR clone's `deps/android_openxr/arm64-v8a/`.
Quest 3 uses ALVR's generic Khronos loader path, rather than the Quest 1 compatibility loader.

```powershell
& 'C:\Program Files\Git\bin\sh.exe' tools/build_pyrowave_android.sh
& 'C:\Program Files\Git\bin\sh.exe' tools/build_alvr_2013.sh
```

Despite its inherited script name, this builds the **patched Quest 3 APK**. The output is
`research\ALVR-20.13.0\build\alvr_client_android\alvr_client_android.apk` under the inputs root.
`cargo-apk` reads `CARGO_APK_RELEASE_KEYSTORE` and `CARGO_APK_RELEASE_KEYSTORE_PASSWORD` for your
own persistent release key. Keep both outside the repo. Main-branch and manually dispatched CI
builds use a stable repository signing key supplied by
`QUEST3_SIGNING_KEYSTORE_BASE64` and `QUEST3_SIGNING_KEYSTORE_PASSWORD` GitHub Actions secrets.
Forks can generate their own key and secrets; pull requests build with a temporary development
key and cannot access release signing secrets. APK certificate fingerprints accompany artifacts.

`fast_build.py` signs with the same stable key when it is in the private workspace beside the repo
(`workspace\keys\quest3-release`) or when `CARGO_APK_RELEASE_KEYSTORE` is set. Its certificate
matches CI's main-branch builds and releases, so `adb install -r` upgrades in either direction.
Without the key it warns and produces a debug-signed APK, which Android refuses as an update to a
stable-signed install. Compare `APK-CERTIFICATE.txt` before switching builds.

The first unsigned-development CI snapshots used disposable keys. Updating from one of those
to the stable signed build requires uninstalling **this app only** once; that removes its app
configuration, so trust the newly discovered client again. Subsequent main builds can install
with `adb install -r`. Never commit a keystore, signing password or raw device logs.

## Install and rollback

```powershell
adb devices -l
adb install -r Quest3-Pyrowave-dev.apk
adb shell am start -n io.github.jms1717.quest3pyrowave/android.app.NativeActivity
```

Allow the requested microphone permission if you use it. In the dashboard, explicitly trust
your headset and register this driver with SteamVR. A fresh install starts on
**Quest 3 Starter 72 Hz**, which streams on any connection. Use only one active ALVR SteamVR driver. This fork has its own protocol version; stock and Galaxy XR
clients cannot pair with it. Driver registration and firewall rules are through the ALVR UI.
Allow the streamer on your private LAN only. The dashboard API is local at port 8082; ALVR's control
and stream connections use TCP 9943 and 9944, and the optional PyroWave UDP transport uses 9948.
Over USB the server also forwards 9950-9953 for the parallel wired video connections
([USB guide](USB.md)).

Once it streams, pick a profile in **Settings → Presets → Streaming profile**, or in the headset
(hold both thumbsticks): **Quest 3 Quality 120 Hz** over USB, **Quest 3 Wi-Fi Quality 120 Hz** on
Wi-Fi 6E. The [profile guide](PROFILES.md) explains each one. The three **"(measured)"** reference
profiles (native 120 Hz, 207 Hz and 240 Hz scaled panel) reproduce 10-12 s screens, not sustained
play.

For standard Quest 3 controllers, choose **Settings → Headset → Controllers →
Emulation mode → Quest 3 Touch Plus**, then restart SteamVR. New sessions default
to this mode (since `.8`); older saved sessions can retain Quest 2 Touch. Games may choose their
own controller meshes independently of SteamVR's render-model property.

Rollback: stop SteamVR, unregister this driver in the dashboard, then re-register your previous
driver. Uninstall only `io.github.jms1717.quest3pyrowave` if desired. Setup itself changes no
network adapter settings and needs no root access.

Headset display and GPU properties change only over USB, and only when you ask for them. With
**Quest 3: set the panel refresh rate over USB** on and a rate above 120 Hz selected (the 240 Hz
profile and the 165/180/200/240 Hz refresh presets turn it on), or with **Quest 3: maximum GPU
clock** on (the 207 Hz and 240 Hz profiles turn it on), the server saves the current `debug.oculus.refreshRate`,
`debug.oculus.forceDisplayScaling` and `debug.oculus.gpuLevel` values, then sets the ones the
request needs through adb (display scaling only above 207 Hz). It puts the saved values back when the request is withdrawn (120 Hz or less selected, or the
option turned off) or SteamVR closes; until then other VR apps, Virtual
Desktop included, also run at that rate. Close SteamVR while the headset is still connected over
USB before switching back to another streamer.

For explicitly requested development sessions, `tools.quest3.awake` can temporarily bypass
proximity sleep with a timed rollback. It is not enabled by the APK or the normal installation.

## Validation

```powershell
python tools/local/fast_build.py test      # CI's Windows Rust tests + Python suite
python -m pytest -q tests                  # Python suite only (CI runs it with unittest)
cd C:\q3pw\research\ALVR-20.13.0
cargo +1.97.1 test -p alvr_session --lib   # one crate in a manual reconstruction
```

Default CI runs regression checks and builds matching APK/server artifacts.
On-device correctness and timing are separate gates: see
[`BENCHMARKING.md`](BENCHMARKING.md).

For tooling changes, dispatch the **Quest3-Pyrowave** workflow with `tests_only`
enabled to run Python, portable C++ and software-GLES regressions without
rebuilding APK/server artifacts. The default is a full matching build. With GitHub CLI:

```powershell
gh workflow run ci.yml --ref main -f tests_only=true
```

A tests-only run produces no installable pair; retain the last reviewed matching
APK/server and its source hash. Native source, shader or build changes still need
the full workflow.

## Switching between PyroWave and Virtual Desktop

Enable **ALVR** in SteamVR’s **Manage Add-ons** before starting Quest3-Pyrowave. A repair for a Virtual Desktop session may have disabled this add-on. When returning to Virtual Desktop, disable ALVR again so the drivers do not compete to claim the headset. Keep the Virtual Desktop installation.


## Regenerating embedded shaders

Normal builds consume the committed generated header. Both build helpers verify
its source/header hash manifest before compiling. After editing GLSL, dispatch
the `Regenerate pinned PyroWave shaders` GitHub workflow. Review the source hash
and download its generated header; replace `shaders/slangmosh.hpp` in the pinned
PyroWave tree, then run `python tools/ci/check_shader_manifest.py <tree> --write`.
Regenerate `patches/quest3-pyrowave.patch` against the staged research baseline,
including the manifest with `git add -N`. Reverse-check the patch before committing.
The workflow records the pinned compiler configuration and invocation.

## Cloud build caching

Actions reconstructs the pinned source trees on every run, then restores Cargo
registry/git dependencies and compiled target directories for the same platform
and pinned toolchain. Cargo still checks the changed sources and dependencies;
native PyroWave configuration/build, tests, signing, packaging and checksums run
again. Caches exclude signing keys, Cargo credentials, headset captures and session
configuration. Caches are saved only from main-branch pushes; pull-request and dispatched runs
restore them. The first run populates the cache; speed gains require a later run.
For a clean comparison, bump the workflow cache generation or remove its cache
steps and dispatch a new build. Artifacts remain tied to their exact CI commit.

## CI scope

Main-branch pushes containing only Markdown and `results/` findings skip the
automatic APK/server workflow. Pull-request checks retain their existing scope.
A newer push to the same non-main branch cancels the run in progress; main keeps every run.
Use **Actions → Quest3-Pyrowave → Run workflow** for an explicit full build,
or select `tests_only` for regression checks without native builds. Native input,
tooling, preset and workflow changes still trigger normal builds. Cancelling a
run also cancels its native build jobs. A skipped build produces no newly
validated matching artifacts; retain the recorded provenance of the tested pair.

---

[![Support Quest3-Pyrowave](https://img.shields.io/badge/Support_Quest3--Pyrowave-PayPal-0070BA?logo=paypal&logoColor=white)](https://www.paypal.com/paypalme/jasonselsley)
