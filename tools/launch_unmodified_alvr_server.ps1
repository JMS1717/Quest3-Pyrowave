param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = 'Stop'
$driver = "$Workspace\ALVR-unmodified-ca2deca\build\alvr_streamer_windows"
$dashboard = "$driver\ALVR Dashboard.exe"
if (!(Test-Path $dashboard)) { throw 'Unmodified build not ready' }
Get-Process vrserver,vrcompositor,vrmonitor,'ALVR Dashboard',steamvr_tutorial -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
Copy-Item "$Workspace\backups\alvr-emulator-session-pixel-probe.json" "$driver\session.json"
$reg = 'C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrpathreg.exe'
& $reg removedriver "$Workspace\ALVR-official-v20.14.1"
& $reg removedriver "$Workspace\ALVR\build\alvr_streamer_windows"
& $reg adddriver $driver
$action = New-ScheduledTaskAction -Execute $dashboard -WorkingDirectory $driver
Register-ScheduledTask -TaskName XRWiredUnmodifiedALVR -Action $action -User $env:USERNAME -RunLevel Limited -Force | Out-Null
Start-ScheduledTask XRWiredUnmodifiedALVR
Start-Sleep -Seconds 2
Stop-ScheduledTask XRWiredSteamVR -ErrorAction SilentlyContinue
Enable-ScheduledTask XRWiredSteamVR | Out-Null
Start-ScheduledTask XRWiredSteamVR
Start-Sleep -Seconds 4
Stop-ScheduledTask XRWiredVRTest -ErrorAction SilentlyContinue
Enable-ScheduledTask XRWiredVRTest | Out-Null
Start-ScheduledTask XRWiredVRTest
Get-Content "$env:LOCALAPPDATA\openvr\openvrpaths.vrpath"
