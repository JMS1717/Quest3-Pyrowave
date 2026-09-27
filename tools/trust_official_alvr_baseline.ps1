param([Parameter(Mandatory, HelpMessage = 'IP address ALVR should dial the client at')][string]$ClientIp, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = 'Stop'
$headers = @{'X-ALVR'='true'}
$base = 'http://127.0.0.1:8082/api'
$body = ConvertTo-Json -InputObject @('0285.client', @{AddIfMissing=@{trusted=$true;manual_ips=@($ClientIp)}}) -Depth 8 -Compress
Invoke-WebRequest -UseBasicParsing -Method Post -Headers $headers -ContentType application/json -Body $body -Uri "$base/session/client-connections" | Out-Null
$body = ConvertTo-Json -InputObject @(@{path=@(@{Name='session_settings'},@{Name='connection'},@{Name='stream_protocol'});value=@{variant='Tcp'}}) -Depth 10 -Compress
Invoke-WebRequest -UseBasicParsing -Method Post -Headers $headers -ContentType application/json -Body $body -Uri "$base/session/values" | Out-Null
Start-Sleep -Seconds 5
Get-Content "$Workspace\ALVR-official-v20.14.1\session.json" -Raw | ConvertFrom-Json | Select-Object -ExpandProperty client_connections | ConvertTo-Json -Depth 6
