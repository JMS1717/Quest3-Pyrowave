@echo off
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
cd /d "%~dp0"
cl /nologo /std:c++17 /O2 /EHsc /W3 /I. nvenc_caps.cpp /Fe:nvenc_caps.exe || exit /b 1
echo built
