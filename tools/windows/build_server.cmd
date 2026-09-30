@echo off
rem Builds the ALVR 20.13.0 server driver (alvr_server_openvr.dll) with the PyroWave encoder, from
rem the workspace clone (research\ALVR-20.13.0) against research\pyrowave\build-interop.
rem Build PyroWave first: build_pyrowave_pc.cmd interop
setlocal
set "WS=%~dp0..\..\.."
if not "%XRWIRED_INPUTS%"=="" set "WS=%XRWIRED_INPUTS%"
set "ALVR=%WS%\research\ALVR-20.13.0"
set "ALVR_PYROWAVE_DIR=%WS%\research\pyrowave"
if not "%XRWIRED_PYROWAVE%"=="" set "ALVR_PYROWAVE_DIR=%XRWIRED_PYROWAVE%"
if not exist "%ALVR_PYROWAVE_DIR%\build-interop\Release\pyrowave-shared.lib" ( echo build pyrowave interop first & exit /b 1 )
cd /d "%ALVR%"
cargo +1.97.1 build --release -p alvr_server_openvr || exit /b 1
dir /b target\release\alvr_server_openvr.dll
echo BUILD_OK
