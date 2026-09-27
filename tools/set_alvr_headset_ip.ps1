param([Parameter(Mandatory, HelpMessage = 'headset IP address')][string]$HeadsetIp, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$headers = @{ "X-ALVR" = "true" }
$body = @(
    "XXXX.client.local.",
    @{ SetManualIps = @($HeadsetIp) }
) | ConvertTo-Json -Depth 5 -Compress

Invoke-WebRequest -UseBasicParsing -Method Post -Headers $headers -ContentType "application/json" `
    -Body $body -Uri "http://127.0.0.1:8082/api/session/client-connections" | Out-Null

Start-Sleep -Seconds 3
$session = Get-Content "$Workspace\ALVR\build\alvr_streamer_windows\session.json" -Raw | ConvertFrom-Json
$entry = $session.client_connections.'XXXX.client.local.'
$entry | Select-Object display_name, current_ip, manual_ips, trusted, connection_state | ConvertTo-Json -Depth 4
