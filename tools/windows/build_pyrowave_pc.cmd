@echo off
rem Builds PyroWave for the PC from the workspace clone (research\pyrowave), with the flags the
rem PC has always used:
rem   build-interop : pyrowave-shared.dll/.lib (-DPYROWAVE_DEVEL=OFF), linked by the ALVR server
rem   build-pc      : pyrowave-encode.exe / pyrowave-decode.exe (-DPYROWAVE_DEVEL=ON), offline RD
rem   build-tools   : slangmosh.exe (Granite tools), only needed to regenerate shaders\slangmosh.hpp
rem Usage: build_pyrowave_pc.cmd [interop] [pc] [tools]   (no arguments = interop pc)
setlocal
set "WS=%~dp0..\..\.."
if not "%XRWIRED_INPUTS%"=="" set "WS=%XRWIRED_INPUTS%"
set "PW=%WS%\research\pyrowave"
if not "%XRWIRED_PYROWAVE%"=="" set "PW=%XRWIRED_PYROWAVE%"
if not exist "%PW%\pyrowave.h" ( echo no pyrowave.h under %PW% & exit /b 1 )
set "GEN=Visual Studio 17 2022"
if not "%Q3PW_CMAKE_GENERATOR%"=="" set "GEN=%Q3PW_CMAKE_GENERATOR%"
set "TARGETS=%*"
if "%TARGETS%"=="" set "TARGETS=interop pc"
cd /d "%PW%"
for %%T in (%TARGETS%) do call :%%T || exit /b 1
echo BUILD_OK
exit /b 0

:interop
cmake -S . -B build-interop -G "%GEN%" -DCMAKE_BUILD_TYPE=Release -DPYROWAVE_DEVEL=OFF -DSHADERC_ENABLE_SHARED_CRT=ON || exit /b 1
cmake --build build-interop --config Release --target pyrowave-shared -j 16 || exit /b 1
dir /b build-interop\Release\*pyrowave-shared*
exit /b 0

:pc
cmake -S . -B build-pc -G "%GEN%" -DCMAKE_BUILD_TYPE=Release -DPYROWAVE_DEVEL=ON -DSHADERC_ENABLE_SHARED_CRT=ON || exit /b 1
cmake --build build-pc --config Release --target pyrowave-encode pyrowave-decode -j 16 || exit /b 1
dir /b build-pc\Release\pyrowave-encode.exe build-pc\Release\pyrowave-decode.exe
exit /b 0

:tools
cmake -S Granite -B build-tools -G "%GEN%" -DCMAKE_BUILD_TYPE=Release -DGRANITE_TOOLS=ON -DGRANITE_RENDERER=OFF -DGRANITE_VULKAN_SPIRV_CROSS=ON -DGRANITE_VULKAN_SHADER_MANAGER_RUNTIME_COMPILER=ON -DGRANITE_VULKAN_SYSTEM_HANDLES=OFF -DSHADERC_ENABLE_SHARED_CRT=ON || exit /b 1
cmake --build build-tools --config Release --target slangmosh -j 16 || exit /b 1
dir /s /b build-tools\slangmosh.exe
exit /b 0
