@echo off
rem Build the D3D11 OpenXR probe (test\xrw_probe.cpp) with VS 2022 Build Tools (x64).
rem Mirrors runtime\build.cmd's compiler flags; links D3D11 + DXGI. Does NOT touch build.cmd.
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
cd /d "%~dp0.."
if not exist build mkdir build
cl /nologo /std:c++17 /O2 /EHsc /W3 /MD /Ithird_party\openxr\include ^
   test\xrw_probe.cpp /Fo:build\ /Fe:build\xrw_probe.exe ^
   /link d3d11.lib dxgi.lib || exit /b 1
echo built build\xrw_probe.exe
