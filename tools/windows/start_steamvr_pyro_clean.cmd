@echo off
REM SteamVR for PyroWave runs, no tap, no dump: what the XRWiredSteamVRPyroClean task runs.
REM The codec and transport come from the session (video.preferred_codec, video.pyrowave); this
REM launcher only clears the research tap/dump variables and arms the runtime dump trigger.
REM Variables are set here, in-process, because a scheduled task inherits a cached copy of the user
REM environment (refreshed at logon), so a User-scope variable may never reach vrserver.
REM The runtime root is the workspace's bench folder: <workspace>\bench beside this repo.
for %%I in ("%~dp0..\..\..\bench") do set "BENCH=%%~fI"
set ALVR_BITSTREAM_TAP=
set ALVR_PYROWAVE_DUMP=
REM Experiment 5: runtime clip trigger directory (trigger.txt = "<path> <count>").
set "ALVR_PYROWAVE_DUMP_TRIGGER=%BENCH%\corpus\_trigger"
REM Per-segment overrides written by xrbench.sweep (dump path/count/start).
if exist "%BENCH%\pyro_env.cmd" call "%BENCH%\pyro_env.cmd"
start "" "C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrmonitor.exe"
