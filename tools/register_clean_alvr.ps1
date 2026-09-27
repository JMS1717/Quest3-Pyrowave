param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = "Stop"
$steamVr = "C:\Program Files (x86)\Steam\steamapps\common\SteamVR"
$driver = "$Workspace\ALVR\build\alvr_streamer_windows"
$dashboard = "$driver\ALVR Dashboard.exe"
$vrpathreg = "$steamVr\bin\win64\vrpathreg.exe"

Get-Process vrmonitor, vrserver, vrcompositor, vrdashboard, alvr_dashboard -ErrorAction SilentlyContinue |
    Stop-Process -Force
Start-Sleep -Seconds 2

& $vrpathreg adddriver $driver
Start-Process $dashboard
Start-Sleep -Seconds 6

Write-Output "=== OPENVR PATHS ==="
Get-Content "$env:LOCALAPPDATA\openvr\openvrpaths.vrpath"
Write-Output "=== PROCESSES ==="
Get-Process alvr_dashboard, vrmonitor, vrserver -ErrorAction SilentlyContinue |
    Select-Object Name, Id, Path | ConvertTo-Json -Depth 3
