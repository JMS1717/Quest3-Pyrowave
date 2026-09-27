param([Parameter(Mandatory, HelpMessage = 'adb serial of the headset')][string]$Serial, [Parameter(Mandatory, HelpMessage = 'headset Wi-Fi IP address')][string]$HeadsetIp, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference='Stop'
$root=$Workspace
$adb="$root\android-tools\platform-tools\adb.exe"
Copy-Item "$root\ALVR-stable-20.14.1\session.json" "$root\backups\ci-d430eac-client\session-before-wifi.json"
& $adb -s $serial shell am force-stop alvr.client.stabletest
$body=@{UpdateClientList=@{hostname='galaxy-stable-usb';action=@{SetManualIps=@($HeadsetIp)}}}|ConvertTo-Json -Depth 8 -Compress
Invoke-RestMethod http://127.0.0.1:8082/api/dashboard-request -Method Post -Headers @{'X-ALVR'='true'} -ContentType 'application/json' -Body $body
# Retain TCP for a transport-controlled comparison; change only destination/routing.
& $adb -s $serial forward --remove tcp:9943
& $adb -s $serial forward --remove tcp:9944
& $adb -s $serial shell monkey -p alvr.client.stabletest -c android.intent.category.LAUNCHER 1
Start-Sleep -Seconds 5
$s=Get-Content "$root\ALVR-stable-20.14.1\session.json" -Raw|ConvertFrom-Json
$s.client_connections|ConvertTo-Json -Depth 6
$s.session_settings.video|Select-Object preferred_fps,preferred_codec,transcoding_view_resolution,bitrate|ConvertTo-Json -Depth 6
& $adb -s $serial forward --list
ping -n 2 $HeadsetIp
