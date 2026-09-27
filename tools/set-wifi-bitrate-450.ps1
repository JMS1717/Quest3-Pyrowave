param([int]$Mbps=450, [Parameter(Mandatory, HelpMessage = 'headset Wi-Fi IP address')][string]$HeadsetIp, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference='Stop'
$root=$Workspace
$s=Get-Content "$root\ALVR-stable-20.14.1\session.json" -Raw|ConvertFrom-Json
if ($s.client_connections.'galaxy-stable-usb'.current_ip -ne $HeadsetIp) { throw 'Expected Wi-Fi connection not found.' }
$pair=@{path=@(@{Name='session_settings'},@{Name='video'},@{Name='bitrate'},@{Name='mode'},@{Name='ConstantMbps'});value=$Mbps}
$body=@{SetValues=@($pair)}|ConvertTo-Json -Depth 12 -Compress
Invoke-RestMethod http://127.0.0.1:8082/api/dashboard-request -Method Post -Headers @{'X-ALVR'='true'} -ContentType 'application/json' -Body $body
Start-Sleep -Seconds 2
$s=Get-Content "$root\ALVR-stable-20.14.1\session.json" -Raw|ConvertFrom-Json
[pscustomobject]@{target_mbps=$s.session_settings.video.bitrate.mode.ConstantMbps;mode=$s.session_settings.video.bitrate.mode.variant;hz=$s.session_settings.video.preferred_fps;codec=$s.session_settings.video.preferred_codec.variant;ip=$s.client_connections.'galaxy-stable-usb'.current_ip;state=$s.client_connections.'galaxy-stable-usb'.connection_state}|ConvertTo-Json
