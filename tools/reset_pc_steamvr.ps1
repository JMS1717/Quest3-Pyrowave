param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = "Stop"
$steam = "C:\Program Files (x86)\Steam"
$steamVr = "$steam\steamapps\common\SteamVR"
$backupRoot = "$Workspace\backups"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$quarantine = "$backupRoot\steamvr-quarantine-$stamp"
New-Item -ItemType Directory -Path $quarantine -Force | Out-Null

Get-Process vrmonitor, vrserver, vrcompositor, vrdashboard, alvr_dashboard -ErrorAction SilentlyContinue |
    Stop-Process -Force

Get-ScheduledTask | Where-Object TaskName -Like "*XRWired*" | Disable-ScheduledTask | Out-Null

$settings = "$steam\config\steamvr.vrsettings"
if (Test-Path $settings) {
    Move-Item $settings "$quarantine\steamvr.vrsettings" -Force
}

$openVrPath = "$env:LOCALAPPDATA\openvr\openvrpaths.vrpath"
if (Test-Path $openVrPath) {
    Copy-Item $openVrPath "$quarantine\openvrpaths.vrpath" -Force
    $openVr = Get-Content $openVrPath -Raw | ConvertFrom-Json
    $openVr.external_drivers = @()
    $openVr | ConvertTo-Json -Depth 10 | Set-Content $openVrPath -Encoding UTF8
}

$customDriver = "$steamVr\drivers\CustomHeadsetOpenVR"
if (Test-Path $customDriver) {
    Move-Item $customDriver "$quarantine\CustomHeadsetOpenVR" -Force
}

Write-Output "QUARANTINE=$quarantine"
Write-Output "TASKS"
Get-ScheduledTask | Where-Object TaskName -Like "*XRWired*" |
    Select-Object TaskName, State | ConvertTo-Json
Write-Output "OPENVR"
Get-Content $openVrPath
Write-Output "CUSTOM_DRIVER_PRESENT=$(Test-Path $customDriver)"
Write-Output "SETTINGS_PRESENT=$(Test-Path $settings)"
