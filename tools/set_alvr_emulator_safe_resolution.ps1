param([Parameter(Mandatory, HelpMessage = 'IP address ALVR should dial the client at')][string]$ClientIp, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = "Stop"
$file = "$Workspace\ALVR\build\alvr_streamer_windows\session.json"
Stop-Process -Name vrserver,vrcompositor,vrmonitor,"ALVR Dashboard" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
Copy-Item $file "$file.before-emulator-1440-$(Get-Date -Format yyyyMMdd-HHmmss).bak"
$s = Get-Content $file -Raw | ConvertFrom-Json
$s.client_connections = [pscustomobject]@{
    "0830.client.local." = [pscustomobject]@{ display_name = "Android XR Emulator"; current_ip = $null; manual_ips = @($ClientIp); trusted = $true; connection_state = "Disconnected" }
}
$s.session_settings.connection.stream_protocol.variant = "Tcp"
$s.session_settings.video.preferred_codec.variant = "H264"
$s.session_settings.video.foveated_encoding.enabled = $false
$s.session_settings.video.encoder_config.use_10bit.set = $true
$s.session_settings.video.encoder_config.use_10bit.content = $false
$s.session_settings.video.encoder_config.hdr.enable.set = $true
$s.session_settings.video.encoder_config.hdr.enable.content = $false
$s.session_settings.extra.logging.log_to_disk = $true
foreach ($resolution in @($s.session_settings.video.transcoding_view_resolution, $s.session_settings.video.emulated_headset_view_resolution)) {
    $resolution.variant = "Absolute"
    $resolution.Absolute.width = 1440
    $resolution.Absolute.height.set = $true
    $resolution.Absolute.height.content = 1440
}
[System.IO.File]::WriteAllText($file, ($s | ConvertTo-Json -Depth 100), (New-Object System.Text.UTF8Encoding($false)))
Enable-ScheduledTask -TaskName XRWiredSteamVR | Out-Null
Start-ScheduledTask -TaskName XRWiredSteamVR
Write-Output "Backed up settings and launched 1440x1440-per-eye unfoveated baseline."
