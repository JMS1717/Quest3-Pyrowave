param([Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$headers = @{ "X-ALVR" = "true" }
$body = @(
    "emulator.client.local.",
    @{ AddIfMissing = @{ trusted = $true; manual_ips = @("127.0.0.1") } }
) | ConvertTo-Json -Depth 7 -Compress

Invoke-WebRequest -UseBasicParsing -Method Post -Headers $headers -ContentType "application/json" `
    -Body $body -Uri "http://127.0.0.1:8082/api/session/client-connections" | Out-Null
Start-Sleep -Seconds 10
$session = Get-Content "$Workspace\ALVR\build\alvr_streamer_windows\session.json" -Raw | ConvertFrom-Json
$session.client_connections | ConvertTo-Json -Depth 7
