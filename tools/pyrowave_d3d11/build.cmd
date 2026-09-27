@echo off
REM Builds the D3D11 -> PyroWave encode harness on the PC.
REM Expects the pyrowave clone (<workspace>\research\pyrowave, or %XRWIRED_PYROWAVE%) with a
REM DEVEL=OFF build (which produces pyrowave-shared).
REM
REM NOTE: paths are used relative to this directory on purpose. "%~dp0" expands with a trailing
REM backslash, which escapes the closing quote and eats the rest of the command line.
setlocal
cd /d "%~dp0"
set "PW=..\..\..\research\pyrowave"
if not "%XRWIRED_PYROWAVE%"=="" set "PW=%XRWIRED_PYROWAVE%"
set VCVARS=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat
if not exist "%VCVARS%" ( echo VCVARS not found at "%VCVARS%" & exit /b 1 )
call "%VCVARS%" >nul
if errorlevel 1 ( echo vcvars failed & exit /b 1 )

cl /nologo /EHsc /O2 /MD /std:c++17 ^
   /I"%PW%" ^
   /I"%PW%\Granite\third_party\khronos\vulkan-headers\include" ^
   main.cpp ^
   /Fe:pyrowave_d3d11.exe ^
   /link "%PW%\build-interop\Release\pyrowave-shared.lib" d3d11.lib dxgi.lib
if errorlevel 1 ( echo BUILD_FAILED & exit /b 1 )

REM The import library needs its DLL beside the exe.
copy /Y "%PW%\build-interop\Release\libpyrowave-shared-0.dll" . >nul
echo BUILD_OK
