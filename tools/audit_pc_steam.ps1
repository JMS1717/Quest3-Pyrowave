param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = "SilentlyContinue"
$steam = "C:\Program Files (x86)\Steam"

Write-Output "=== PROCESSES ==="
Get-Process steam, steamwebhelper, vrserver, vrmonitor, alvr_dashboard -ErrorAction SilentlyContinue |
    Select-Object Name, Id, Path

Write-Output "=== LIBRARIES ==="
Get-Content "$steam\steamapps\libraryfolders.vdf"

Write-Output "=== STEAMVR SETTINGS ==="
Get-Content "$steam\config\steamvr.vrsettings"

Write-Output "=== OPENVR PATHS ==="
$openVrPath = "$env:LOCALAPPDATA\openvr\openvrpaths.vrpath"
Get-Content $openVrPath

Write-Output "=== STEAMVR DRIVERS ==="
Get-ChildItem "$steam\steamapps\common\SteamVR\drivers" -Directory |
    Select-Object Name, FullName, LastWriteTime | ConvertTo-Json -Depth 3

Write-Output "=== EXTERNAL DRIVER PATHS ==="
$openVr = Get-Content $openVrPath -Raw | ConvertFrom-Json
$openVr.external_drivers

Write-Output "=== ALVR LOCATIONS ==="
Get-ChildItem "$env:LOCALAPPDATA\ALVR", "$env:APPDATA\ALVR", $Workspace -ErrorAction SilentlyContinue |
    Select-Object FullName, Length, LastWriteTime | ConvertTo-Json -Depth 3

Write-Output "=== XRWIRED TASKS ==="
Get-ScheduledTask | Where-Object TaskName -Like "*XRWired*" |
    Select-Object TaskName, State,
        @{Name = "Execute"; Expression = { $_.Actions.Execute }},
        @{Name = "Arguments"; Expression = { $_.Actions.Arguments }} | ConvertTo-Json -Depth 4

Write-Output "=== STEAMVR MANIFEST ==="
Get-Item "$steam\steamapps\appmanifest_250820.acf" |
    Select-Object FullName, Length, LastWriteTime | ConvertTo-Json -Depth 3

Write-Output "=== PRESERVED DATA SIZES ==="
foreach ($path in @("$steam\steamapps", "$steam\userdata")) {
    $sum = (Get-ChildItem $path -File -Recurse | Measure-Object Length -Sum).Sum
    Write-Output "$path`t$([math]::Round($sum / 1GB, 2)) GB"
}
