param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = "Stop"
$settingsPath = "C:\Program Files (x86)\Steam\config\steamvr.vrsettings"
$backupPath = "$Workspace\backups\steamvr-before-alvr-unblock-$(Get-Date -Format 'yyyyMMdd-HHmmss').vrsettings"

Get-Process vrmonitor, vrserver, vrcompositor -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2
Copy-Item $settingsPath $backupPath -Force
$settings = Get-Content $settingsPath -Raw | ConvertFrom-Json
if (-not $settings.driver_alvr_server) {
    $settings | Add-Member -NotePropertyName driver_alvr_server -NotePropertyValue ([pscustomobject]@{})
}
$settings.driver_alvr_server | Add-Member -NotePropertyName blocked_by_safe_mode -NotePropertyValue $false -Force
$settings | ConvertTo-Json -Depth 20 | Set-Content $settingsPath -Encoding UTF8
Write-Output "BACKUP=$backupPath"
Get-Content $settingsPath
