param([Parameter(Mandatory, HelpMessage = 'adb serial of the headset')][string]$Serial, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = 'Stop'
$root = $Workspace
$adb = "$root\android-tools\platform-tools\adb.exe"
$package = 'alvr.client.stabletest'
$backup = "$root\backups\ci-d430eac-client"
New-Item -ItemType Directory -Path $backup -Force | Out-Null
& $adb -s $serial shell am force-stop $package
# Owner approved clearing this package's data after private-data backup was unavailable.
Copy-Item "$root\ALVR-stable-20.14.1\session.json" "$backup\session-before-ci.json"
& $adb -s $serial uninstall $package
if ($LASTEXITCODE -ne 0) { throw 'Uninstall failed.' }
& $adb -s $serial install "$root\Galaxy-XR-ALVR-ci-d430eac.apk"
if ($LASTEXITCODE -ne 0) { throw 'CI installation failed.' }
Stop-ScheduledTask -TaskName XRWiredStableALVR
Stop-ScheduledTask -TaskName XRWiredSteamVR
Get-Process ALVR*,vrmonitor,vrdashboard,vrcompositor,vrserver,vrwebhelper -ErrorAction SilentlyContinue | ForEach-Object { $_.Kill(); $_.WaitForExit(5000) | Out-Null }
Copy-Item "$root\before-10bit-session.json" "$root\ALVR-stable-20.14.1\session.json"
& $adb -s $serial forward tcp:9943 tcp:9943
& $adb -s $serial forward tcp:9944 tcp:9944
Start-ScheduledTask -TaskName XRWiredStableALVR
Start-Sleep -Seconds 5
& "$root\xrwired-configure-stable.ps1" -RefreshHz 72 -EyeSize 2560 -Mbps 375
$pairs = @()
foreach ($setting in @(@('use_10bit',$false),@('server_overrides_use_10bit',$true))) {
    $names = @('session_settings','video','encoder_config',$setting[0])
    $pairs += @{path=@($names | ForEach-Object { @{Name=$_} }); value=$setting[1]}
}
foreach ($setting in @(@('enable_hdr',$false),@('server_overrides_enable_hdr',$true))) {
    $names = @('session_settings','video','encoder_config','hdr',$setting[0])
    $pairs += @{path=@($names | ForEach-Object { @{Name=$_} }); value=$setting[1]}
}
$body = @{SetValues=$pairs} | ConvertTo-Json -Depth 12 -Compress
Invoke-RestMethod http://127.0.0.1:8082/api/dashboard-request -Method Post -Headers @{'X-ALVR'='true'} -ContentType 'application/json' -Body $body
Start-ScheduledTask -TaskName XRWiredSteamVR
Start-Sleep -Seconds 4
& $adb -s $serial shell monkey -p $package -c android.intent.category.LAUNCHER 1
Get-Content "$root\ALVR-stable-20.14.1\session.json" -Raw | ConvertFrom-Json | ForEach-Object { $_.session_settings.video } | Select-Object preferred_fps,preferred_codec,bitrate,transcoding_view_resolution,encoder_config | ConvertTo-Json -Depth 8
