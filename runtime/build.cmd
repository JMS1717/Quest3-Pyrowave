@echo off
rem Build xrwired_runtime.dll (+ mini-loader test) with VS 2022 Build Tools (x64).
rem Reuses ..\driver\src\encoder.cpp + stream.cpp; needs nvEncodeAPI.h in ..\driver\include.
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
cd /d "%~dp0"
if not exist build mkdir build
cl /nologo /std:c++17 /O2 /EHsc /W3 /MD /LD ^
   /Ithird_party\openxr\include /I..\driver\src /I..\driver\include ^
   src\xr_runtime.cpp src\log.cpp src\blitter.cpp ..\driver\src\encoder.cpp ..\driver\src\stream.cpp ^
   /Fo:build\ /Fe:xrwired_runtime.dll ^
   /link d3d11.lib dxgi.lib d3dcompiler.lib ws2_32.lib || exit /b 1
cl /nologo /std:c++17 /O2 /EHsc /W3 /MD /Ithird_party\openxr\include ^
   test\minixr_test.cpp /Fo:build\ /Fe:build\minixr_test.exe || exit /b 1
echo built xrwired_runtime.dll
