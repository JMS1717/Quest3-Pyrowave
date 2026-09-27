param([Parameter(Mandatory, HelpMessage = 'adb serial of the headset')][string]$Serial, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = 'Stop'
$adb = "$Workspace\android-tools\platform-tools\adb.exe"
$root = "$Workspace\ALVR-unmodified-ca2deca\build\alvr_streamer_windows"
Copy-Item "$root\session.json" "$Workspace\backups\before-direct-usb-$(Get-Date -Format yyyyMMdd-HHmmss).json"
$body = ConvertTo-Json -InputObject @('2386.client.local.', @{SetManualIps=@('127.0.0.1')}) -Depth 6 -Compress
Invoke-WebRequest -UseBasicParsing -Method Post -Headers @{'X-ALVR'='true'} -ContentType application/json -Body $body -Uri 'http://127.0.0.1:8082/api/session/client-connections' | Out-Null
& $adb -s $serial shell am force-stop alvr.client.dev
try {
    & $adb -s $serial shell svc wifi disable
    Get-Process vrserver,vrcompositor,vrmonitor,steamvr_tutorial -ErrorAction SilentlyContinue | Stop-Process -Force
    Stop-ScheduledTask XRWiredSteamVR -ErrorAction SilentlyContinue
    Start-ScheduledTask XRWiredSteamVR
    Start-Sleep -Seconds 3
    & $adb -s $serial shell monkey -p alvr.client.dev 1
    Start-Sleep -Seconds 5
    Stop-ScheduledTask XRWiredVRTest -ErrorAction SilentlyContinue
    Start-ScheduledTask XRWiredVRTest
    Start-Sleep -Seconds 3
    Write-Output 'WIFI_STATE:'
    & $adb -s $serial shell settings get global wifi_on
    & $adb -s $serial shell ip route
    & $adb forward --list
    $s = Get-Content "$root\session.json" -Raw | ConvertFrom-Json
    $s.client_connections | ConvertTo-Json -Depth 5
    $s.steamvr_hmd_init_config | ConvertTo-Json
    & $adb -s $serial shell screencap -p /sdcard/direct-usb-proof.png
    & $adb -s $serial pull /sdcard/direct-usb-proof.png "$Workspace\direct-usb-proof.png"
} finally {
    & $adb -s $serial shell svc wifi enable
}
