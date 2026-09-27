param([Parameter(Mandatory, HelpMessage = 'adb serial of the headset')][string]$Serial, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference='Stop'
$root=$Workspace
for ($i=0; $i -lt 20; $i++) {
    try { Invoke-RestMethod http://127.0.0.1:8082/api/ping -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Seconds 1 }
}
& "$root\xrwired-configure-stable.ps1" -RefreshHz 72 -EyeSize 2560 -Mbps 375
$pairs=@()
foreach ($setting in @(@('encoder_config.use_10bit',$false),@('encoder_config.server_overrides_use_10bit',$true),@('encoder_config.hdr.enable_hdr',$false),@('encoder_config.hdr.server_overrides_enable_hdr',$true))) {
    $names=@('session_settings','video') + $setting[0].Split('.')
    $pairs+=@{path=@($names | ForEach-Object {@{Name=$_}});value=$setting[1]}
}
$body=@{SetValues=$pairs}|ConvertTo-Json -Depth 12 -Compress
Invoke-RestMethod http://127.0.0.1:8082/api/dashboard-request -Method Post -Headers @{'X-ALVR'='true'} -ContentType 'application/json' -Body $body
Start-ScheduledTask -TaskName XRWiredSteamVR
& "$root\android-tools\platform-tools\adb.exe" -s $Serial shell monkey -p alvr.client.stabletest -c android.intent.category.LAUNCHER 1
Get-Content "$root\ALVR-stable-20.14.1\session.json" -Raw|ConvertFrom-Json|ForEach-Object {$_.session_settings.video}|Select-Object preferred_fps,preferred_codec,bitrate,transcoding_view_resolution,encoder_config|ConvertTo-Json -Depth 8
