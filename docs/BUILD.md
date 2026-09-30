# Reproducible builds

The fastest clean build is **Actions → Quest3-Pyrowave → Run workflow** in your own fork.
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
2022 C++ tools with Windows SDK and ATL. VS2019 can compile PyroWave and the transport probe;
the supported full-streamer recipe uses VS2022. The SDK, tools and project remain separate.

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
The old `tools/windows/install_toolchain.ps1` is inherited research tooling; use the explicit
versions below or the CI recipe instead.

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
own persistent release key. Keep both outside the repo. Main-branch CI builds use a stable repository signing key supplied by
`QUEST3_SIGNING_KEYSTORE_BASE64` and `QUEST3_SIGNING_KEYSTORE_PASSWORD` GitHub Actions secrets.
Forks can generate their own key and secrets; pull requests build with a temporary development
key and cannot access release signing secrets. APK certificate fingerprints accompany artifacts.

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
your headset, register this driver with SteamVR, and select **Quest 3 PyroWave 1000 Mbps**. Use
only one active ALVR SteamVR driver. This fork has its own protocol version; stock and Galaxy XR
clients cannot pair with it. Driver registration and firewall rules are through the ALVR UI.
Allow the streamer on your private LAN only; the API is local at port 8082 and PyroWave UDP at 9948.

Rollback: stop SteamVR, unregister this driver in the dashboard, then re-register your previous
driver. Uninstall only `io.github.jms1717.quest3pyrowave` if desired. No OS refresh properties,
network adapter settings, root access, or persistent GPU clock changes are part of setup.

For explicitly requested development sessions, `tools.quest3.awake` can temporarily bypass
proximity sleep with a timed rollback. It is not enabled by the APK or the normal installation.

## Validation

```powershell
python -m unittest discover -s tests -v
cd C:\q3pw\research\ALVR-20.13.0
cargo +1.97.1 test -p alvr_session --lib
```

Main-branch CI builds artifacts only. Pull requests run tool regression checks; tests can also be run manually. On-device correctness and timing
are separate gates: see [`BENCHMARKING.md`](BENCHMARKING.md).

## Switching between PyroWave and Virtual Desktop

Enable **ALVR** in SteamVR�s **Manage Add-ons** before starting Quest3-Pyrowave. A repair for a Virtual Desktop session may have disabled this add-on. When returning to Virtual Desktop, disable ALVR again so the drivers do not compete to claim the headset. Keep the Virtual Desktop installation.
