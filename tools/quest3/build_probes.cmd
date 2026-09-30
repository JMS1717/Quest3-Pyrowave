@echo off
setlocal
rem Run from a VS x64 Developer Prompt, or pass the vcvars64.bat path as Q3PW_VCVARS.
if not "%Q3PW_VCVARS%"=="" call "%Q3PW_VCVARS%" || exit /b 1
where cl >nul 2>nul || (echo Use an x64 Visual Studio Developer Prompt or set Q3PW_VCVARS & exit /b 1)
cd /d "%~dp0"
cl /nologo /EHsc /O2 /std:c++17 network_sender.cpp /Fe:network_sender.exe /link ws2_32.lib || exit /b 1
echo BUILD_OK
