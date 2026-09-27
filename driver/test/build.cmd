@echo off
rem Build the standalone Stream round-trip test with the VS 2022 Build Tools (x64).
rem   driver\test\build.cmd   ->  driver\test\stream_test.exe
setlocal
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
cd /d "%~dp0"
cl /nologo /std:c++17 /O2 /EHsc /W4 /MT /I..\src stream_test.cpp ..\src\stream.cpp ^
   /Fe:stream_test.exe ws2_32.lib || exit /b 1
echo built %~dp0stream_test.exe
