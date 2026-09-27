@echo off
rem Build mvhevc_clip.exe with the VS 2022 Build Tools (x64). Copy nvEncodeAPI.h from the NVIDIA
rem Video Codec SDK into this folder first (it is not redistributed here).
rem Optional %1: output name (e.g. nvenc_probe) when mvhevc_clip.exe is in use.
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
cd /d "%~dp0"
set name=%~1
if "%name%"=="" set name=mvhevc_clip
cl /nologo /std:c++17 /O2 /EHsc /W3 /I. mvhevc_clip.cpp /Fe:%name%.exe || exit /b 1
echo built %~dp0%name%.exe
