param([Parameter(Mandatory, HelpMessage = 'headset IP address')][string]$HeadsetIp, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
Write-Output "=== PING ==="
ping -n 1 $HeadsetIp
Write-Output "=== CONTROL PORT 9943 ==="
Test-NetConnection $HeadsetIp -Port 9943 -InformationLevel Quiet
Write-Output "=== SESSION ==="
$session = Get-Content "$Workspace\ALVR\build\alvr_streamer_windows\session.json" -Raw | ConvertFrom-Json
$session.client_connections.'XXXX.client.local.' |
    Select-Object current_ip, manual_ips, trusted, connection_state | ConvertTo-Json -Depth 4
