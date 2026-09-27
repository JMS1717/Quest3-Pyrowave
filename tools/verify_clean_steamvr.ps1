$ErrorActionPreference = "SilentlyContinue"
$steam = "C:\Program Files (x86)\Steam"
$monitor = "$steam\steamapps\common\SteamVR\bin\win64\vrmonitor.exe"
$settings = "$steam\config\steamvr.vrsettings"
$openVrPath = "$env:LOCALAPPDATA\openvr\openvrpaths.vrpath"

Start-Process $monitor
Start-Sleep -Seconds 10

Write-Output "=== PROCESSES ==="
Get-Process vrmonitor, vrserver, vrcompositor -ErrorAction SilentlyContinue |
    Select-Object Name, Id, Path | ConvertTo-Json -Depth 3
Write-Output "=== SETTINGS ==="
if (Test-Path $settings) { Get-Content $settings } else { Write-Output "MISSING" }
Write-Output "=== EXTERNAL DRIVERS ==="
(Get-Content $openVrPath -Raw | ConvertFrom-Json).external_drivers | ConvertTo-Json
Write-Output "=== RECENT CUSTOM DRIVER LOG MATCHES ==="
$serverLog = "$steam\logs\vrserver.txt"
if (Test-Path $serverLog) {
    Get-Content $serverLog -Tail 250 | Select-String -Pattern "alvr|CustomHeadset|VirtualDesktop|Loaded server driver"
}
