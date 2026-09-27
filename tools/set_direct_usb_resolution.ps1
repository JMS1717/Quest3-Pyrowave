param([ValidateSet(1440,1800,2048,2304,2560)][int]$Size=1800, [Parameter(Mandatory, HelpMessage = 'adb serial of the headset')][string]$Serial, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference='Stop'
$root="$Workspace\ALVR-unmodified-ca2deca\build\alvr_streamer_windows"
$adb="$Workspace\android-tools\platform-tools\adb.exe"
Copy-Item "$root\session.json" "$Workspace\backups\before-resolution-$Size-$(Get-Date -Format yyyyMMdd-HHmmss).json"
$changes=@()
foreach ($name in @('transcoding_view_resolution','emulated_headset_view_resolution')) {
    $changes += @{path=@(@{Name='session_settings'},@{Name='video'},@{Name=$name});value=@{variant='Absolute';Scale=1.0;Absolute=@{width=$Size;height=@{set=$true;content=$Size}}}}
}
$body=ConvertTo-Json -InputObject $changes -Depth 12 -Compress
Invoke-WebRequest -UseBasicParsing -Method Post -Headers @{'X-ALVR'='true'} -ContentType application/json -Body $body -Uri 'http://127.0.0.1:8082/api/session/values' | Out-Null
& $adb -s $Serial shell am force-stop alvr.client.dev
Get-Process vrserver,vrcompositor,vrmonitor,steamvr_tutorial -ErrorAction SilentlyContinue | Stop-Process -Force
Stop-ScheduledTask XRWiredSteamVR -ErrorAction SilentlyContinue
Start-ScheduledTask XRWiredSteamVR
Start-Sleep -Seconds 3
& $adb -s $Serial shell monkey -p alvr.client.dev 1
Start-Sleep -Seconds 7
Stop-ScheduledTask XRWiredVRTest -ErrorAction SilentlyContinue
Start-ScheduledTask XRWiredVRTest
Start-Sleep -Seconds 3
$s=Get-Content "$root\session.json" -Raw | ConvertFrom-Json
$s.steamvr_hmd_init_config | ConvertTo-Json
$s.client_connections | ConvertTo-Json -Depth 4
Write-Output "BITRATE_TARGET=$($s.session_settings.video.bitrate.mode.ConstantMbps)"
Get-Process vrserver,vrcompositor,steamvr_tutorial -ErrorAction SilentlyContinue | Select-Object Name,Id
