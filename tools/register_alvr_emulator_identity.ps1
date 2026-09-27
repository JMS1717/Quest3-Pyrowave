param(
    [string]$Hostname = "0830.client.local.",
    [Parameter(Mandatory, HelpMessage = 'IP address ALVR should dial the client at')][string]$ClientIp,
    [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace
)

$headers = @{ "X-ALVR" = "true" }
$endpoint = "http://127.0.0.1:8082/api/session/client-connections"

function Post-Action($hostname, $action) {
    $body = @($hostname, $action) | ConvertTo-Json -Depth 7 -Compress
    Invoke-WebRequest -UseBasicParsing -Method Post -Headers $headers -ContentType "application/json" `
        -Body $body -Uri $endpoint | Out-Null
}

Post-Action "emulator.client.local." "RemoveEntry"
Post-Action "XXXX.client.local." "RemoveEntry"
Post-Action $Hostname @{ AddIfMissing = @{ trusted = $true; manual_ips = @($ClientIp) } }
Post-Action $Hostname @{ SetDisplayName = "Android XR Emulator" }
Start-Sleep -Seconds 12

$session = Get-Content "$Workspace\ALVR\build\alvr_streamer_windows\session.json" -Raw | ConvertFrom-Json
$session.client_connections | ConvertTo-Json -Depth 7
