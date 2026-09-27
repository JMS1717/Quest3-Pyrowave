param([ValidateSet(30,60,100,150,200)][int]$Mbps=60, [Parameter(Mandatory, HelpMessage = 'Folder holding ALVR, android-tools and backups (this script predates the repo layout)')][string]$Workspace)
$ErrorActionPreference = 'Stop'
$root = "$Workspace\ALVR-unmodified-ca2deca\build\alvr_streamer_windows"
Copy-Item "$root\session.json" "$Workspace\backups\before-quality-$Mbps-$(Get-Date -Format yyyyMMdd-HHmmss).json"
$path = @(@{Name='session_settings'},@{Name='video'},@{Name='bitrate'},@{Name='mode'},@{Name='ConstantMbps'})
$body = ConvertTo-Json -InputObject @(@{path=$path;value=$Mbps}) -Depth 10 -Compress
Invoke-WebRequest -UseBasicParsing -Method Post -Headers @{'X-ALVR'='true'} -ContentType application/json -Body $body -Uri 'http://127.0.0.1:8082/api/session/values' | Out-Null
Start-Sleep -Seconds 3
$s = Get-Content "$root\session.json" -Raw | ConvertFrom-Json
Write-Output "BITRATE_TARGET_MBPS=$($s.session_settings.video.bitrate.mode.ConstantMbps)"
$s.steamvr_hmd_init_config | ConvertTo-Json
$s.client_connections | ConvertTo-Json -Depth 4
