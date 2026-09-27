param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = 'Stop'
$steamvr = 'C:\Program Files (x86)\Steam\steamapps\common\SteamVR'
$driver = "$Workspace\ALVR-unmodified-ca2deca\build\alvr_streamer_windows"
foreach ($file in @("$steamvr\bin\win64\vrmonitor.exe", "$driver\ALVR Dashboard.exe", "$steamvr\tools\steamvr_tutorial\win64\steamvr_tutorial.exe")) {
    if (!(Test-Path $file)) { throw "Required file missing: $file" }
}
Get-Content 'C:\Program Files (x86)\Steam\steamapps\appmanifest_250820.acf' | Select-String -Pattern 'buildid|BetaKey|StateFlags'
Get-Process vrserver,vrcompositor,vrmonitor,'ALVR Dashboard',steamvr_tutorial -ErrorAction SilentlyContinue | Stop-Process -Force
& "$steamvr\bin\win64\vrpathreg.exe" adddriver $driver
$tasks = @{
    XRWiredUnmodifiedALVR = "$driver\ALVR Dashboard.exe"
    XRWiredSteamVR = "$steamvr\bin\win64\vrmonitor.exe"
    XRWiredVRTest = "$steamvr\tools\steamvr_tutorial\win64\steamvr_tutorial.exe"
}
foreach ($name in $tasks.Keys) {
    $action = New-ScheduledTaskAction -Execute $tasks[$name] -WorkingDirectory (Split-Path $tasks[$name])
    Register-ScheduledTask -TaskName $name -Action $action -User $env:USERNAME -RunLevel Limited -Force | Out-Null
}
Start-ScheduledTask XRWiredUnmodifiedALVR
Start-Sleep -Seconds 2
Start-ScheduledTask XRWiredSteamVR
Start-Sleep -Seconds 4
Get-Process vrserver,vrcompositor,vrmonitor -ErrorAction SilentlyContinue | Select-Object Name,Id,SessionId
