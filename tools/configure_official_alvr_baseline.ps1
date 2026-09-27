param([Parameter(Mandatory, HelpMessage = 'IP address ALVR should dial the client at')][string]$ClientIp, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = 'Stop'
$path = "$Workspace\ALVR-official-v20.14.1\session.json"
Get-Process 'ALVR Dashboard',vrserver,vrcompositor,vrmonitor,steamvr_tutorial -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3
$s = Get-Content $path -Raw | ConvertFrom-Json
$s.client_connections | Add-Member -Force -NotePropertyName '0285.client' -NotePropertyValue ([pscustomobject]@{display_name='XR emulator stock baseline'; trusted=$true; manual_ips=@($ClientIp); current_ip=$null;connection_state='Disconnected'})
$s.session_settings.connection.stream_protocol.variant = 'Tcp'
$s.session_settings.video.preferred_fps = 72
$s.session_settings.video.preferred_codec.variant = 'H264'
$s.session_settings.video.foveated_encoding.enabled = $false
foreach ($resolution in @($s.session_settings.video.transcoding_view_resolution,$s.session_settings.video.emulated_headset_view_resolution)) {
    $resolution.variant = 'Absolute'
    $resolution.Absolute.width = 1440
    $resolution.Absolute.height.set = $true
    $resolution.Absolute.height.content = 1440
}
[IO.File]::WriteAllText($path, ($s | ConvertTo-Json -Depth 100), (New-Object Text.UTF8Encoding $false))
Start-ScheduledTask XRWiredOfficialALVR
Start-Sleep -Seconds 2
Stop-ScheduledTask XRWiredSteamVR -ErrorAction SilentlyContinue
Enable-ScheduledTask XRWiredSteamVR | Out-Null
Start-ScheduledTask XRWiredSteamVR
Start-Sleep -Seconds 5
Start-ScheduledTask XRWiredVRTest
