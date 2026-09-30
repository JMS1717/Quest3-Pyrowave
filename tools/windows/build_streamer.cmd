@echo off
rem Builds the full beta streamer (driver + dashboard + PyroWave) into
rem research\ALVR-20.13.0\build\alvr_streamer_windows, the folder testers unzip and run.
rem Build PyroWave first: build_pyrowave_pc.cmd interop
setlocal
set "WS=%~dp0..\..\.."
if not "%XRWIRED_INPUTS%"=="" set "WS=%XRWIRED_INPUTS%"
set "ALVR=%WS%\research\ALVR-20.13.0"
set "ALVR_PYROWAVE_DIR=%WS%\research\pyrowave"
if not "%XRWIRED_PYROWAVE%"=="" set "ALVR_PYROWAVE_DIR=%XRWIRED_PYROWAVE%"
set "PYRO_DLL=%ALVR_PYROWAVE_DIR%\build-interop\Release\libpyrowave-shared-0.dll"
if not exist "%PYRO_DLL%" ( echo build pyrowave interop first & exit /b 1 )
rem xtask's nested cargo calls inherit the pinned toolchain from here
set "RUSTUP_TOOLCHAIN=1.97.1"
cd /d "%ALVR%"
cargo xtask build-streamer --release || exit /b 1
copy /y "%PYRO_DLL%" build\alvr_streamer_windows\bin\win64\ >nul || exit /b 1
dir /b build\alvr_streamer_windows build\alvr_streamer_windows\bin\win64
echo BUILD_OK
