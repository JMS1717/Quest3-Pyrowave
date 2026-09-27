@echo off
rem Register (or unregister) the XR Wired driver with SteamVR, and switch ALVR out of the way.
rem   install.cmd            register xrwired, disable the ALVR driver
rem   install.cmd remove     unregister xrwired, re-enable ALVR
setlocal
set VRPATHREG="C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrpathreg.exe"
set DRIVER=%~dp0pkg\xrwired
if /i "%~1"=="remove" (
    %VRPATHREG% removedriver "%DRIVER%"
    %VRPATHREG% show
    echo xrwired unregistered
    exit /b 0
)
if not exist "%DRIVER%\bin\win64\driver_xrwired.dll" (
    echo build the driver first: build.cmd
    exit /b 1
)
%VRPATHREG% adddriver "%DRIVER%"
%VRPATHREG% show
echo xrwired registered
