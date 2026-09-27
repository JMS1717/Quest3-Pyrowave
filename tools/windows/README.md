# Building on the PC (Windows)

Workspace layout (`<workspace>`): this repo in `ALVR_Custom_Galaxy_XR\`, the clones in
`research\`, the Android toolchain in `toolchain\`. `tools/lib/xrwired_env.sh` resolves all of it;
explicit `XRWIRED_*` / `JAVA_HOME` variables override.

| step | command | output |
|---|---|---|
| toolchain (once) | `powershell -File tools\windows\install_toolchain.ps1` | `toolchain\jdk-17`, `toolchain\android-sdk` |
| PyroWave PC | `tools\windows\build_pyrowave_pc.cmd` (`interop pc`, add `tools` for slangmosh) | `research\pyrowave\build-interop\Release\libpyrowave-shared-0.dll`, `build-pc\Release\pyrowave-{encode,decode}.exe` |
| ALVR server | `tools\windows\build_server.cmd` | `research\ALVR-20.13.0\target\release\alvr_server_openvr.dll` |
| beta streamer (driver, dashboard, PyroWave DLL) | `tools\windows\build_streamer.cmd` | `research\ALVR-20.13.0\build\alvr_streamer_windows\` |
| PyroWave Android | `sh tools/build_pyrowave_android.sh` | `research\pyrowave\build-android\libpyrowave-shared.so` |
| libpyroclient | `sh tools/pyroclient/build.sh` | `tools\pyroclient\libpyroclient.so` |
| client APK | `sh tools/build_alvr_2013.sh` | `research\ALVR-20.13.0\build\alvr_client_android\alvr_client_android.apk` |
| standalone decoder | `sh tools/pyrowave_android/build.sh` | `tools\pyrowave_android\pyrowave_android` |
| UDP receiver | `sh tools/udptest/build.sh` | `tools\udptest\udprecv-android` |

`sh` is Git for Windows' (`C:\Program Files\Git\bin\sh.exe`); the `bash` on PATH is WSL's.
The client APK is signed with `research\ALVR-20.13.0\build\alvr_client_android\debug.keystore`,
carried over from the Mac so new builds install over the headset's existing app; do not delete it.
Deploying the server DLL is still `deploy_server.cmd` on the C: runtime until the cutover.
