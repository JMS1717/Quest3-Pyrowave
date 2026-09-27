# Starts xrbench.sweep inside the logged-in (possibly disconnected) desktop session, where SteamVR
# runs, via the XRWiredBench scheduled task. Output goes to xrbench-runs\sweep-<time>.log.
# Example: .\start_xrbench_sweep.ps1 -SweepArgs '--client-host <alvr hostname> --client-ip <headset ip> --only W1-400'
param([Parameter(Mandatory)][string]$SweepArgs, [string]$Module = 'xrbench.sweep',
      [string]$Python = 'python.exe')
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$logDir = Join-Path $root 'xrbench-runs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "sweep-$(Get-Date -Format yyyyMMdd-HHmmss).log"
$cmd = "cd /d $root && set PYTHONUNBUFFERED=1 && `"$Python`" -m $Module $SweepArgs > `"$log`" 2>&1"
$action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c $cmd" -WorkingDirectory $root
# Starting a task that is still running is silently ignored, so wait for any previous run first.
$deadline = (Get-Date).AddSeconds(150)
while ((Get-ScheduledTask XRWiredBench -ErrorAction SilentlyContinue).State -eq 'Running') {
    if ((Get-Date) -gt $deadline) { throw 'XRWiredBench is still running after 150 s; not starting another run.' }
    Start-Sleep -Seconds 3
}
Register-ScheduledTask -TaskName XRWiredBench -Action $action -User $env:USERNAME -RunLevel Limited -Force | Out-Null
Start-ScheduledTask XRWiredBench
$deadline = (Get-Date).AddSeconds(15)
while (-not (Test-Path $log)) {
    if ((Get-Date) -gt $deadline) { throw "XRWiredBench did not start (no log at $log)" }
    Start-Sleep -Milliseconds 500
}
Write-Host "Started XRWiredBench. Log: $log"
