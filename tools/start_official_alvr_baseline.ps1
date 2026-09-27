param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = 'Stop'
$stock = "$Workspace\ALVR-official-v20.14.1"
$custom = "$Workspace\ALVR\build\alvr_streamer_windows"
$paths = "$env:LOCALAPPDATA\openvr\openvrpaths.vrpath"
$backup = "$Workspace\backups\official-alvr-baseline"
New-Item -ItemType Directory -Force $backup | Out-Null
Copy-Item $paths "$backup\openvrpaths-$(Get-Date -Format yyyyMMdd-HHmmss).vrpath"
Get-Process vrserver,vrcompositor,vrmonitor,vrdashboard,alvr_dashboard,ALVRDashboard,steamvr_tutorial -ErrorAction SilentlyContinue | Stop-Process -Force
$reg = 'C:\Program Files (x86)\Steam\steamapps\common\SteamVR\bin\win64\vrpathreg.exe'
& $reg removedriver $custom
& $reg adddriver $stock
$action = New-ScheduledTaskAction -Execute "$stock\ALVR Dashboard.exe" -WorkingDirectory $stock
Register-ScheduledTask -TaskName XRWiredOfficialALVR -Action $action -User $env:USERNAME -RunLevel Limited -Force | Out-Null
Start-ScheduledTask XRWiredOfficialALVR
Start-Sleep -Seconds 5
Get-ChildItem $stock -Filter session.json
Get-Process *alvr* -ErrorAction SilentlyContinue | Select-Object Name,Id
