param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = "$Workspace\backups\steamvr-pre-reset-$stamp"
$steam = "C:\Program Files (x86)\Steam"

New-Item -ItemType Directory -Path $backup -Force | Out-Null

$files = @(
    "$steam\config\steamvr.vrsettings",
    "$steam\steamapps\libraryfolders.vdf",
    "$steam\steamapps\appmanifest_250820.acf",
    "$env:LOCALAPPDATA\openvr\openvrpaths.vrpath"
)
foreach ($file in $files) {
    if (Test-Path $file) {
        Copy-Item $file $backup -Force
    }
}

Get-ScheduledTask | Where-Object TaskName -Like "*XRWired*" | ForEach-Object {
    Export-ScheduledTask -TaskName $_.TaskName |
        Set-Content -Path "$backup\scheduled-task-$($_.TaskName).xml" -Encoding UTF8
}

$customDriver = "$steam\steamapps\common\SteamVR\drivers\CustomHeadsetOpenVR"
if (Test-Path $customDriver) {
    Copy-Item $customDriver "$backup\CustomHeadsetOpenVR" -Recurse -Force
}

$inventory = [ordered]@{
    Created = (Get-Date -Format o)
    SteamPath = $steam
    PreservedGameData = "$steam\steamapps"
    PreservedUserData = "$steam\userdata"
    ExternalDrivers = (Get-Content "$env:LOCALAPPDATA\openvr\openvrpaths.vrpath" -Raw | ConvertFrom-Json).external_drivers
    Notes = "Configuration-only backup. The 270+ GB steamapps payload remains in place and was not duplicated."
}
$inventory | ConvertTo-Json -Depth 5 | Set-Content "$backup\inventory.json" -Encoding UTF8

Write-Output $backup
Get-ChildItem $backup -Recurse | Select-Object FullName, Length | ConvertTo-Json -Depth 4
