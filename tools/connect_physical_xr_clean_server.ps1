param([Parameter(Mandatory, HelpMessage = 'headset IP address')][string]$HeadsetIp, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = 'Stop'
$root = "$Workspace\ALVR-unmodified-ca2deca\build\alvr_streamer_windows"
Copy-Item "$root\session.json" "$Workspace\backups\before-physical-xr-$(Get-Date -Format yyyyMMdd-HHmmss).json"
$headers = @{'X-ALVR'='true'}
$body = ConvertTo-Json -InputObject @('2386.client.local.', @{AddIfMissing=@{trusted=$true;manual_ips=@($HeadsetIp)}}) -Depth 8 -Compress
Invoke-WebRequest -UseBasicParsing -Method Post -Headers $headers -ContentType application/json -Body $body -Uri 'http://127.0.0.1:8082/api/session/client-connections' | Out-Null
Start-Sleep -Seconds 7
$s = Get-Content "$root\session.json" -Raw | ConvertFrom-Json
$s.client_connections | ConvertTo-Json -Depth 6
Get-Content "$root\session_log.txt" -Tail 15
