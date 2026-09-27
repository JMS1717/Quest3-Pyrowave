@echo off
rem Build the D3D11 OpenXR hand-tracking visualizer (test\xrw_hands.cpp) with VS 2022 Build Tools (x64).
rem Mirrors build_probe.cmd's compiler flags; links D3D11 + DXGI + D3DCompiler. Does NOT touch build.cmd.
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
cd /d "%~dp0.."
if not exist build mkdir build
cl /nologo /std:c++17 /O2 /EHsc /W3 /MD /Ithird_party\openxr\include ^
   test\xrw_hands.cpp /Fo:build\ /Fe:build\xrw_hands.exe ^
   /link d3d11.lib dxgi.lib d3dcompiler.lib || exit /b 1
echo built build\xrw_hands.exe
