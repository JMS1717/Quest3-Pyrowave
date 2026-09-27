param([ValidateSet('H264','Hevc')][string]$Codec='Hevc', [Parameter(Mandatory, HelpMessage = 'adb serial of the headset')][string]$Serial, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference='Stop'
$root="$Workspace\ALVR-unmodified-ca2deca\build\alvr_streamer_windows"
$adb="$Workspace\android-tools\platform-tools\adb.exe"
Copy-Item "$root\session.json" "$Workspace\backups\before-codec-$Codec-$(Get-Date -Format yyyyMMdd-HHmmss).json"
$body=ConvertTo-Json -InputObject @(@{path=@(@{Name='session_settings'},@{Name='video'},@{Name='preferred_codec'});value=@{variant=$Codec}}) -Depth 10 -Compress
Invoke-WebRequest -UseBasicParsing -Method Post -Headers @{'X-ALVR'='true'} -ContentType application/json -Body $body -Uri 'http://127.0.0.1:8082/api/session/values' | Out-Null
for ($attempt=0;$attempt -lt 2;$attempt++) {
    & $adb -s $Serial shell am force-stop alvr.client.dev
    Get-Process vrserver,vrcompositor,vrmonitor,steamvr_tutorial -ErrorAction SilentlyContinue | Stop-Process -Force
    Stop-ScheduledTask XRWiredSteamVR -ErrorAction SilentlyContinue
    Start-ScheduledTask XRWiredSteamVR
    Start-Sleep -Seconds 3
    & $adb -s $Serial shell monkey -p alvr.client.dev 1
    Start-Sleep -Seconds 7
    $s=Get-Content "$root\session.json" -Raw | ConvertFrom-Json
    if ($s.client_connections.'2386.client.local.'.connection_state -eq 'Streaming') {break}
}
if ($s.client_connections.'2386.client.local.'.connection_state -ne 'Streaming') {throw 'Codec test did not reconnect; rollback needed'}
Stop-ScheduledTask XRWiredVRTest -ErrorAction SilentlyContinue
Start-ScheduledTask XRWiredVRTest
$s.steamvr_hmd_init_config | ConvertTo-Json
$s.client_connections | ConvertTo-Json -Depth 4
Write-Output "CODEC=$($s.session_settings.video.preferred_codec.variant) BITRATE_TARGET=$($s.session_settings.video.bitrate.mode.ConstantMbps)"
