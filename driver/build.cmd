@echo off
rem Build driver_xrwired.dll with the VS 2022 Build Tools (x64).
rem Needs openvr_driver.h (from the OpenVR SDK) and nvEncodeAPI.h (NVENC SDK) in include\.
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
cd /d "%~dp0"
if not exist build mkdir build
cl /nologo /std:c++17 /O2 /EHsc /W3 /MD /LD /Iinclude /Isrc ^
   src\driver_xrwired.cpp src\encoder.cpp src\stream.cpp ^
   /Fo:build\ /Fe:pkg\xrwired\bin\win64\driver_xrwired.dll ^
   /link d3d11.lib dxgi.lib ws2_32.lib || exit /b 1
echo built %~dp0pkg\xrwired\bin\win64\driver_xrwired.dll
